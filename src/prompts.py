# prompts.py


# ─────────────────────────────────────────
# AGENT REVENUE VELOCITY
# ─────────────────────────────────────────

def deal_zombie_note(
    deal_title: str,
    days_stagnant: int,
    stage: str,
    avg_cycle_days_at_stage: float,
    win_rate_at_stage: float,
    amount: float
) -> str:
    """
    Action 1.1 — Note CRM pour un deal zombie.
    Doit être courte (≤ 3 phrases), factuelle, sans bullshit.
    Utilisée avec llm.explain()
    """
    return (
        f"Rédige une note CRM courte (3 phrases maximum) pour informer "
        f"le commercial que ce deal est en stagnation. "
        f"Sois factuel et direct. Donne une recommandation claire : "
        f"relancer, archiver, ou escalader. "
        f"Ne commence pas par 'Je' ou 'Voici'. "
        f"Format : observation → données → recommandation."
    )


def lead_score_explanation(
    score: int,
    score_label: str,
    fit_score: float,
    source: str,
    source_conversion_rate: float,
    company_size: Optional[int],
    sector: str,
    similar_won_deals: int
) -> str:
    """
    Action 1.4 — Explication du score d'un lead.
    2 phrases max. Pourquoi ce score, quelle action.
    Utilisée avec llm.explain()
    """
    return (
        f"Explique en 2 phrases pourquoi ce lead a ce score. "
        f"Termine par l'action recommandée (appeler immédiatement / "
        f"nurture / archiver). "
        f"Sois direct. Pas d'introduction."
    )


def win_loss_analysis(
    deal_title: str,
    outcome: str,
    total_days: int,
    avg_won_days: float,
    stage_durations: dict,
    bottleneck_stage: str,
    bottleneck_days: float,
    avg_activities: int,
    actual_activities: int
) -> str:
    """
    Action 1.9 — Analyse win/loss d'un deal fermé.
    Paragraphe court. Cause probable + recommandation.
    Utilisée avec llm.generate() (Sonnet)
    """
    return (
        f"Analyse pourquoi ce deal a été {outcome}. "
        f"Identifie la cause principale en te basant sur les données. "
        f"Structure ta réponse en 3 parties : "
        f"1) Ce qui s'est passé (factuel), "
        f"2) La cause probable, "
        f"3) Une recommandation concrète pour les prochains deals similaires. "
        f"Maximum 150 mots. Pas de bullet points. Pas d'introduction."
    )


# ─────────────────────────────────────────
# AGENT CASH PREDICTABILITY
# ─────────────────────────────────────────

def invoice_reminder_email(
    client_name: str,
    invoice_number: str,
    amount: float,
    currency: str,
    due_date: str,
    days_overdue: int,
    payment_history: str,
    reminder_number: int
) -> str:
    """
    Action 3.1 — Email de relance de facture impayée.
    Ton adapté selon le numéro de relance et l'historique.
    Utilisée avec llm.draft()
    """

    tone_instruction = {
        1: "Ton très courtois. Simple rappel. On suppose une erreur d'oubli.",
        2: "Ton professionnel mais ferme. On demande une date de règlement précise.",
        3: "Ton sérieux. On informe que la situation nécessite une résolution urgente."
    }.get(reminder_number, "Ton professionnel et ferme.")

    return (
        f"Rédige un email de relance de facture impayée. "
        f"{tone_instruction} "
        f"L'email doit être court (5-7 lignes maximum). "
        f"Inclure : mention de la facture, montant, retard en jours, "
        f"demande d'action claire. "
        f"Ne pas inclure de signature (elle sera ajoutée automatiquement). "
        f"Ne pas commencer par 'Je me permets'. "
        f"Commencer directement par le sujet."
    )


# ─────────────────────────────────────────
# ORCHESTRATEUR
# ─────────────────────────────────────────

def weekly_report_narrative(
    company_name: str,
    week_number: int,
    clarity_score: int,
    clarity_trend: int,
    revenue_velocity: float,
    revenue_velocity_trend: float,
    deals_at_risk: list,
    cash_status: str,
    top_bottleneck: Optional[str],
    cac_trend: Optional[float]
) -> str:
    """
    Rapport unifié du lundi.
    Génère le narratif principal (≤ 5 lignes).
    Le reste du rapport est structuré par le code.
    Utilisée avec llm.draft()
    """
    return (
        f"Rédige l'introduction du rapport hebdomadaire Kuria. "
        f"Maximum 4 phrases. "
        f"Synthétise la semaine : ce qui va bien, ce qui nécessite attention. "
        f"Sois direct et factuel. "
        f"Ne commence pas par 'Cette semaine' ou 'Bonjour'. "
        f"Commence par le fait le plus important de la semaine."
    )


# ─────────────────────────────────────────
# TYPE HINT IMPORT
# ─────────────────────────────────────────
from typing import Optional
