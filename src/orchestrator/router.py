# orchestrator/router.py

import logging
from datetime import datetime
from typing import Callable

from services.database import (
    get_unprocessed_events,
    mark_event_processed,
    get_client
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# TABLE DE ROUTAGE
#
# Structure :
# event_type → liste de handlers
# chaque handler est une fonction (company_id, payload) → None
#
# On déclare les routes ici.
# Les handlers sont définis plus bas.
# ─────────────────────────────────────────

def _build_routing_table() -> dict[str, list[Callable]]:
    return {

        # Revenue Velocity publie quand le forecast pipeline change
        "forecast_updated": [
            _handle_forecast_updated_for_cash,
        ],

        # Cash Predictability publie quand le cash forecast change
        "cash_forecast_updated": [
            _handle_cash_alert_for_revenue,
        ],

        # Acquisition Efficiency publie quand le CAC est recalculé
        "cac_updated": [
            _handle_cac_updated_for_scoring,
        ],
    }


# ─────────────────────────────────────────
# ROUTER PRINCIPAL
# ─────────────────────────────────────────

def process_events(company_id: str) -> int:
    """
    Lit tous les events non traités pour un client
    et déclenche les handlers correspondants.

    Retourne le nombre d'events traités.
    """
    events = get_unprocessed_events(company_id)

    if not events:
        return 0

    routing_table = _build_routing_table()
    processed = 0

    for event in events:
        event_type = event.get("event_type")
        payload    = event.get("payload") or {}
        event_id   = event.get("id")

        handlers = routing_table.get(event_type, [])

        if not handlers:
            # Event non géré → on le marque traité quand même
            # pour ne pas le retraiter indéfiniment
            logger.debug(f"Event non routé : {event_type}")
            mark_event_processed(event_id)
            processed += 1
            continue

        for handler in handlers:
            try:
                logger.info(
                    f"[router] {event_type} → {handler.__name__} "
                    f"pour company {company_id}"
                )
                handler(company_id, payload)
            except Exception as e:
                logger.error(
                    f"[router] Erreur handler {handler.__name__} "
                    f"sur {event_type} : {e}"
                )

        mark_event_processed(event_id)
        processed += 1

    if processed > 0:
        logger.info(
            f"[router] {processed} events traités pour {company_id}"
        )

    return processed


# ─────────────────────────────────────────
# HANDLERS
#
# Chaque handler reçoit (company_id, payload)
# et déclenche l'action appropriée sur l'agent cible.
#
# RÈGLE : un handler ne fait qu'une chose.
# Il ne recalcule pas tout — il déclenche
# uniquement ce qui a changé.
# ─────────────────────────────────────────

def _handle_forecast_updated_for_cash(
    company_id: str, payload: dict
) -> None:
    """
    Quand Revenue Velocity met à jour le forecast pipeline,
    Cash Predictability doit recalculer son forecast cash.

    On ne relance pas tout l'agent Cash.
    On met juste à jour le forecast dans Supabase
    pour que le prochain run de Cash utilise les bonnes données.

    En V1 : le forecast est stocké dans la table "forecasts".
    Cash le lira automatiquement à son prochain run.
    → Pas besoin de déclencher quoi que ce soit immédiatement.

    Si le forecast montre un problème urgent → alerte immédiate.
    """
    forecast_30d = payload.get("forecast_30d", 0)
    confidence   = payload.get("confidence", 0)

    logger.info(
        f"[router] forecast_updated reçu — "
        f"30d: {forecast_30d}€, confiance: {confidence}"
    )

    # Si la confiance est très basse → noter dans les métriques
    if confidence < 0.3:
        try:
            client = get_client()
            client.table("companies").update({
                "agent_configs": _append_to_config(
                    company_id,
                    "revenue_velocity",
                    {"last_low_confidence_alert": datetime.utcnow().isoformat()}
                )
            }).eq("id", company_id).execute()
        except Exception as e:
            logger.error(f"Erreur handler forecast_updated : {e}")


def _handle_cash_alert_for_revenue(
    company_id: str, payload: dict
) -> None:
    """
    Quand Cash Predictability détecte un risque de trésorerie,
    Revenue Velocity doit prioriser les deals à forte probabilité.

    Action : on marque dans le profil que les deals
    doivent être priorisés par valeur × probabilité.
    Au prochain run de Revenue Velocity, il en tiendra compte.
    """
    days_until_critical = payload.get("days_until_critical")

    if not days_until_critical:
        return

    logger.info(
        f"[router] cash_forecast_updated — "
        f"critique dans {days_until_critical}j"
    )

    if days_until_critical < 45:
        # Signaler à Revenue Velocity de prioriser les deals
        try:
            _update_agent_flag(
                company_id,
                "revenue_velocity",
                "cash_pressure_mode",
                True
            )
            logger.info(
                f"[router] cash_pressure_mode activé pour {company_id}"
            )
        except Exception as e:
            logger.error(f"Erreur handler cash_alert : {e}")


def _handle_cac_updated_for_scoring(
    company_id: str, payload: dict
) -> None:
    """
    Quand Acquisition Efficiency recalcule le CAC par source,
    Revenue Velocity doit mettre à jour les poids de son lead scoring.

    Action : stocker les taux de conversion par source
    dans le profil client pour que le prochain scoring les utilise.
    """
    cac_by_source = payload.get("cac_by_source", {})
    top_source    = payload.get("top_source", "")

    if not cac_by_source:
        return

    logger.info(
        f"[router] cac_updated — top source: {top_source}"
    )

    try:
        _update_agent_flag(
            company_id,
            "revenue_velocity",
            "cac_by_source",
            cac_by_source
        )
        _update_agent_flag(
            company_id,
            "revenue_velocity",
            "top_acquisition_source",
            top_source
        )
    except Exception as e:
        logger.error(f"Erreur handler cac_updated : {e}")


# ─────────────────────────────────────────
# UTILITAIRES INTERNES
# ─────────────────────────────────────────

def _update_agent_flag(
    company_id: str,
    agent_name: str,
    flag_key: str,
    flag_value
) -> None:
    """
    Ajoute ou met à jour un flag dans la config d'un agent.
    Utilisé par les handlers pour passer des signaux entre agents.
    """
    from orchestrator.profile import update_agent_config
    update_agent_config(company_id, agent_name, {flag_key: flag_value})


def _append_to_config(
    company_id: str, agent_name: str, updates: dict
) -> dict:
    """
    Retourne la config agent mise à jour.
    Utilisé pour les mises à jour partielles.
    """
    try:
        client = get_client()
        result = client.table("companies").select("agent_configs").eq(
            "id", company_id
        ).limit(1).execute()

        if result.data:
            configs = result.data[0].get("agent_configs") or {}
            agent_cfg = configs.get(agent_name, {})
            agent_cfg.update(updates)
            configs[agent_name] = agent_cfg
            return configs
    except Exception:
        pass
    return {}
