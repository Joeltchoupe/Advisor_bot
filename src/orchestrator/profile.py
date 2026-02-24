# orchestrator/profile.py

import logging
from datetime import datetime
from typing import Optional

from services.database import get_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# CONFIG PAR DÉFAUT
# Si un paramètre n'est pas dans le profil client,
# on utilise ces valeurs.
# ─────────────────────────────────────────

DEFAULT_AGENT_CONFIGS = {
    "revenue_velocity": {
        "enabled": True,
        "stagnation_threshold_days": 21,
        "lead_score_hot_threshold": 70,
        "lead_score_warm_threshold": 40,
        "head_of_sales_email": "",
        "alert_channel": "email",
        "report_frequency": "weekly"
    },
    "cash_predictability": {
        "enabled": True,
        "cash_critical_threshold": 0,       # calculé dynamiquement si 0
        "reminder_day_1": 1,
        "reminder_day_2": 7,
        "reminder_day_3": 15,
        "escalation_day": 30,
        "ceo_email": "",
        "alert_channel": "email"
    },
    "process_clarity": {
        "enabled": True,
        "deadline_warning_days": 2,
        "overdue_escalation_days": 3,
        "manager_email": "",
        "alert_channel": "email"
    },
    "acquisition_efficiency": {
        "enabled": True,
        "cac_anomaly_threshold": 0.30,      # +30% → alerte
        "marketing_expense_categories": [
            "marketing", "advertising", "publicite",
            "ads", "pub", "communication", "acquisition"
        ],
        "ceo_email": "",
        "period_days": 90
    }
}


# ─────────────────────────────────────────
# LECTURE DU PROFIL
# ─────────────────────────────────────────

def get_company_profile(company_id: str) -> dict:
    """
    Retourne le profil complet d'un client.
    Fusionne les configs stockées avec les defaults.

    Structure retournée :
    {
        "company": {...},           # données de base
        "tools_connected": {...},   # outils connectés
        "clarity_score": 0,
        "agent_configs": {
            "revenue_velocity": {...},
            "cash_predictability": {...},
            "process_clarity": {...},
            "acquisition_efficiency": {...}
        }
    }
    """
    try:
        client = get_client()
        result = client.table("companies").select("*").eq(
            "id", company_id
        ).limit(1).execute()

        if not result.data:
            logger.error(f"Company introuvable : {company_id}")
            return {}

        company = result.data[0]

        # Fusionner les configs stockées avec les defaults
        stored_configs = company.get("agent_configs") or {}
        merged_configs = {}

        for agent_name, default_config in DEFAULT_AGENT_CONFIGS.items():
            stored = stored_configs.get(agent_name, {})
            merged_configs[agent_name] = {**default_config, **stored}

        return {
            "company": {
                "id": company["id"],
                "name": company.get("name", ""),
                "sector": company.get("sector", ""),
                "size_employees": company.get("size_employees"),
                "size_revenue": company.get("size_revenue")
            },
            "tools_connected": company.get("tools_connected") or {},
            "clarity_score": company.get("clarity_score", 0),
            "agent_configs": merged_configs
        }

    except Exception as e:
        logger.error(f"Erreur get_company_profile {company_id} : {e}")
        return {}


def get_agent_config(company_id: str, agent_name: str) -> dict:
    """
    Raccourci : retourne uniquement la config d'un agent spécifique.
    C'est ce que les agents appellent au démarrage.
    """
    profile = get_company_profile(company_id)
    if not profile:
        return DEFAULT_AGENT_CONFIGS.get(agent_name, {})

    return profile.get("agent_configs", {}).get(
        agent_name,
        DEFAULT_AGENT_CONFIGS.get(agent_name, {})
    )


def is_agent_enabled(company_id: str, agent_name: str) -> bool:
    """
    Vérifie si un agent est activé pour ce client.
    Un agent peut être désactivé si les données sont insuffisantes.
    """
    config = get_agent_config(company_id, agent_name)
    return config.get("enabled", True)


def get_all_active_companies() -> list[dict]:
    """
    Retourne tous les clients avec au moins un agent activé.
    Utilisé par le scheduler pour savoir qui faire tourner.
    """
    try:
        client = get_client()
        result = client.table("companies").select(
            "id, name, agent_configs"
        ).execute()

        active = []
        for company in (result.data or []):
            configs = company.get("agent_configs") or {}
            any_enabled = any(
                configs.get(agent, {}).get("enabled", True)
                for agent in DEFAULT_AGENT_CONFIGS.keys()
            )
            if any_enabled:
                active.append({
                    "id": company["id"],
                    "name": company["name"]
                })

        return active

    except Exception as e:
        logger.error(f"Erreur get_all_active_companies : {e}")
        return []


# ─────────────────────────────────────────
# MISE À JOUR DU PROFIL
# ─────────────────────────────────────────

def update_agent_config(
    company_id: str,
    agent_name: str,
    updates: dict
) -> bool:
    """
    Met à jour la config d'un agent pour un client.
    Utilisé par l'adapter (recalibration manuelle en V1).

    updates : dict des paramètres à modifier
              ex: {"stagnation_threshold_days": 28}
    """
    try:
        client = get_client()

        # Récupérer la config actuelle
        result = client.table("companies").select("agent_configs").eq(
            "id", company_id
        ).limit(1).execute()

        if not result.data:
            return False

        current_configs = result.data[0].get("agent_configs") or {}
        agent_config = current_configs.get(agent_name, {})
        agent_config.update(updates)
        current_configs[agent_name] = agent_config

        # Sauvegarder
        client.table("companies").update({
            "agent_configs": current_configs,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", company_id).execute()

        logger.info(
            f"Config {agent_name} mise à jour pour {company_id} : {updates}"
        )
        return True

    except Exception as e:
        logger.error(f"Erreur update_agent_config : {e}")
        return False


def update_clarity_score(company_id: str, score: int) -> bool:
    """Met à jour le Score de Clarté d'un client."""
    try:
        client = get_client()
        client.table("companies").update({
            "clarity_score": score,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", company_id).execute()
        return True
    except Exception as e:
        logger.error(f"Erreur update_clarity_score : {e}")
        return False
