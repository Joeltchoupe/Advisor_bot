# services/llm.py

import os
import logging
from typing import Optional
from anthropic import Anthropic

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# MODÈLES
# Haiku  → rapide, pas cher, pour les tâches simples
#           (expliquer un chiffre, rédiger une note)
# Sonnet → plus puissant, pour les tâches complexes
#           (générer une proposition, analyser un win/loss)
# ─────────────────────────────────────────

HAIKU  = "claude-haiku-4-5"
SONNET = "claude-sonnet-4-5"

# Seuils de coût :
# Haiku  ≈ 0.001€ par appel
# Sonnet ≈ 0.01-0.05€ par appel

_client: Optional[Anthropic] = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


# ─────────────────────────────────────────
# FONCTION PRINCIPALE
# ─────────────────────────────────────────

def ask(
    prompt: str,
    context: dict,
    model: str = HAIKU,
    max_tokens: int = 500,
    temperature: float = 0.3
) -> str:
    """
    Interface unique pour tous les appels LLM de Kuria.

    prompt      : l'instruction (ce qu'on veut que le LLM fasse)
    context     : les données à analyser (dict → converti en texte structuré)
    model       : HAIKU par défaut, SONNET pour les tâches complexes
    max_tokens  : limiter la longueur de la réponse
    temperature : 0.3 pour des réponses cohérentes et factuelles
                  (pas de créativité inutile)

    Retourne : le texte généré, ou "" en cas d'erreur
    """
    client = _get_client()

    # Construire le message système
    system = (
        "Tu es Kuria, un système d'analyse business. "
        "Tu analyses des données réelles d'entreprise. "
        "Tes réponses sont factuelles, directes et actionnables. "
        "Tu ne fais jamais de suppositions. "
        "Si les données sont insuffisantes, tu le dis clairement. "
        "Tu réponds en français sauf instruction contraire."
    )

    # Construire le message utilisateur
    context_text = _format_context(context)
    user_message = f"{context_text}\n\n{prompt}"

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[
                {"role": "user", "content": user_message}
            ]
        )

        result = response.content[0].text

        # Log du coût (tokens utilisés)
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        logger.info(
            f"LLM [{model}] — {input_tokens}in/{output_tokens}out tokens"
        )

        return result

    except Exception as e:
        logger.error(f"LLM erreur : {e}")
        return ""


# ─────────────────────────────────────────
# FONCTIONS SPÉCIALISÉES
# Elles appellent ask() avec les bons paramètres
# ─────────────────────────────────────────

def explain(data: dict, instruction: str) -> str:
    """
    Expliquer un chiffre ou une situation en 2-3 phrases.
    → Haiku suffit
    → Max 150 tokens
    """
    return ask(
        prompt=instruction,
        context=data,
        model=HAIKU,
        max_tokens=150,
        temperature=0.2
    )


def draft(data: dict, instruction: str) -> str:
    """
    Rédiger un message (email, note CRM, alerte).
    → Haiku suffit pour les courts
    → Max 300 tokens
    """
    return ask(
        prompt=instruction,
        context=data,
        model=HAIKU,
        max_tokens=300,
        temperature=0.3
    )


def generate(data: dict, instruction: str) -> str:
    """
    Générer du contenu long (proposition, analyse, SOP).
    → Sonnet nécessaire
    → Max 1500 tokens
    """
    return ask(
        prompt=instruction,
        context=data,
        model=SONNET,
        max_tokens=1500,
        temperature=0.4
    )


# ─────────────────────────────────────────
# UTILITAIRE INTERNE
# ─────────────────────────────────────────

def _format_context(context: dict) -> str:
    """
    Convertit un dict de contexte en texte structuré lisible par le LLM.

    Exemple :
    {
        "deal": {"title": "Acme Corp", "amount": 45000},
        "days_stagnant": 23
    }
    →
    "DONNÉES :
     deal:
       title: Acme Corp
       amount: 45000
     days_stagnant: 23"
    """
    if not context:
        return ""

    lines = ["DONNÉES :"]
    lines.extend(_dict_to_lines(context, indent=0))
    return "\n".join(lines)


def _dict_to_lines(obj, indent: int) -> list[str]:
    """Récursif. Convertit un dict/list en lignes indentées."""
    lines = []
    prefix = "  " * indent

    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_dict_to_lines(value, indent + 1))
            elif value is not None and value != "":
                lines.append(f"{prefix}{key}: {value}")

    elif isinstance(obj, list):
        for i, item in enumerate(obj[:10]):   # max 10 items pour éviter des contextes trop longs
            if isinstance(item, dict):
                lines.append(f"{prefix}- ")
                lines.extend(_dict_to_lines(item, indent + 1))
            else:
                lines.append(f"{prefix}- {item}")

    return lines
