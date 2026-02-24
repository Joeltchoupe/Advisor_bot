# orchestrator/adapter.py

import logging
from datetime import datetime, timedelta
from typing import Optional

from services.database import get_client
from orchestrator.profile import update_agent_config, get_agent_config

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# ANALYSE DES PERFORMANCES
# ─────────────────────────────────────────

def analyze_agent_performance(company_id: str) -> dict:
    """
    Analyse les performances de chaque agent
    sur les 30 derniers jours.

    Retourne un rapport avec des recommandations
    d'ajustement que le consultant peut valider.

    Appelé lors du call mensuel de recalibration.
    """
    logger.info(f"[adapter] Analyse performance pour {company_id}")

    report = {
        "company_id": company_id,
        "analyzed_at": datetime.utcnow().isoformat(),
        "agents": {}
    }

    report["agents"]["revenue_velocity"] = _analyze_revenue_velocity(company_id)
    report["agents"]["cash_predictability"] = _analyze_cash_predictability(company_id)
    report["agents"]["process_clarity"] = _analyze_process_clarity(company_id)
    report["agents"]["acquisition_efficiency"] = _analyze_acquisition_efficiency(company_id)

    return report


def _analyze_revenue_velocity(company_id: str) -> dict:
    """
    Évalue la précision du lead scoring et du pipeline cleaning.

    Métriques clés :
    → Taux de conversion des leads HOT (devrait être > 30%)
    → Précision du forecast (écart forecast vs deals closés)
    → Taux de faux positifs zombies (deals taggés zombie qui ont closé)
    """
    try:
        client = get_client()
        now = datetime.utcnow()
        thirty_days_ago = now - timedelta(days=30)
        cutoff = thirty_days_ago.isoformat()

        # Deals closés ce mois
        won = client.table("deals").select("id, source").eq(
            "company_id", company_id
        ).eq("status", "won").gte("closed_at", cutoff).execute()

        lost = client.table("deals").select("id").eq(
            "company_id", company_id
        ).eq("status", "lost").gte("closed_at", cutoff).execute()

        total_won  = len(won.data or [])
        total_lost = len(lost.data or [])
        total_closed = total_won + total_lost

        win_rate = total_won / total_closed if total_closed > 0 else 0

        # Contacts scorés HOT et leur devenir
        hot_contacts = client.table("contacts").select("id, email").eq(
            "company_id", company_id
        ).eq("score_label", "hot").gte("created_at", cutoff).execute()

        hot_count = len(hot_contacts.data or [])

        # Recommandations
        recommendations = []
        current_config = get_agent_config(company_id, "revenue_velocity")

        if win_rate < 0.15 and total_closed >= 5:
            recommendations.append({
                "parameter": "stagnation_threshold_days",
                "current": current_config.get("stagnation_threshold_days", 21),
                "suggested": 14,
                "reason": f"Win rate faible ({win_rate:.0%}). "
                          f"Détecter la stagnation plus tôt."
            })

        return {
            "deals_won_30d": total_won,
            "deals_lost_30d": total_lost,
            "win_rate": round(win_rate, 3),
            "hot_leads_generated": hot_count,
            "recommendations": recommendations
        }

    except Exception as e:
        logger.error(f"Erreur analyze_revenue_velocity : {e}")
        return {"error": str(e), "recommendations": []}


def _analyze_cash_predictability(company_id: str) -> dict:
    """
    Évalue la précision du forecast cash.

    Métriques clés :
    → Écart forecast vs réalité (si historique disponible)
    → Taux de succès des relances factures
    → Évolution du DSO (Days Sales Outstanding)
    """
    try:
        client = get_client()
        thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()

        # Factures passées de overdue à paid ce mois
        # (indicateur de succès des relances)
        reminders_sent = client.table("invoice_reminders").select("id").eq(
            "company_id", company_id
        ).gte("sent_at", thirty_days_ago).execute()

        # Factures payées ce mois
        invoices_paid = client.table("invoices").select(
            "amount, payment_delay_days"
        ).eq("company_id", company_id).eq(
            "status", "paid"
        ).gte("paid_at", thirty_days_ago).execute()

        paid_data = invoices_paid.data or []
        delays = [
            i.get("payment_delay_days", 0)
            for i in paid_data
            if i.get("payment_delay_days") is not None
        ]

        avg_delay = sum(delays) / len(delays) if delays else 0
        recommendations = []

        if avg_delay > 30:
            recommendations.append({
                "parameter": "reminder_day_1",
                "current": 1,
                "suggested": 0,
                "reason": f"DSO élevé ({avg_delay:.0f}j). "
                          f"Envoyer le premier rappel le jour J."
            })

        return {
            "reminders_sent_30d": len(reminders_sent.data or []),
            "invoices_paid_30d": len(paid_data),
            "avg_payment_delay_days": round(avg_delay, 1),
            "recommendations": recommendations
        }

    except Exception as e:
        logger.error(f"Erreur analyze_cash_predictability : {e}")
        return {"error": str(e), "recommendations": []}


def _analyze_process_clarity(company_id: str) -> dict:
    """
    Évalue l'efficacité du suivi des tâches.

    Métriques clés :
    → Taux de livraison à temps (trend)
    → Tâches routées automatiquement vs assignées manuellement
    → Cycle time trend (s'améliore-t-il ?)
    """
    try:
        client = get_client()
        thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()

        tasks_completed = client.table("tasks").select(
            "id, cycle_time_days, completed_at, due_at"
        ).eq("company_id", company_id).eq(
            "status", "done"
        ).gte("completed_at", thirty_days_ago).execute()

        tasks = tasks_completed.data or []

        on_time = sum(
            1 for t in tasks
            if t.get("due_at") and t.get("completed_at")
            and t["completed_at"] <= t["due_at"]
        )

        delivery_rate = on_time / len(tasks) if tasks else 0

        cycle_times = [
            float(t["cycle_time_days"])
            for t in tasks
            if t.get("cycle_time_days")
        ]

        avg_cycle = sum(cycle_times) / len(cycle_times) if cycle_times else 0
        recommendations = []

        if delivery_rate < 0.6 and len(tasks) >= 5:
            recommendations.append({
                "parameter": "deadline_warning_days",
                "current": get_agent_config(
                    company_id, "process_clarity"
                ).get("deadline_warning_days", 2),
                "suggested": 3,
                "reason": f"Taux de livraison faible ({delivery_rate:.0%}). "
                          f"Prévenir plus tôt."
            })

        return {
            "tasks_completed_30d": len(tasks),
            "on_time_delivery_rate": round(delivery_rate, 3),
            "avg_cycle_time_days": round(avg_cycle, 1),
            "recommendations": recommendations
        }

    except Exception as e:
        logger.error(f"Erreur analyze_process_clarity : {e}")
        return {"error": str(e), "recommendations": []}


def _analyze_acquisition_efficiency(company_id: str) -> dict:
    """
    Évalue les tendances du CAC.
    """
    try:
        client = get_client()

        cac_history = client.table("cac_metrics").select(
            "blended_cac, computed_at, top_source"
        ).eq("company_id", company_id).order(
            "computed_at", desc=True
        ).limit(3).execute()

        records = cac_history.data or []
        recommendations = []

        if len(records) >= 2:
            current  = records[0].get("blended_cac", 0)
            previous = records[1].get("blended_cac", 0)

            if previous > 0:
                trend = (current - previous) / previous
                if trend > 0.20:
                    recommendations.append({
                        "parameter": "cac_anomaly_threshold",
                        "current": 0.30,
                        "suggested": 0.20,
                        "reason": f"CAC en hausse régulière ({trend:.0%}). "
                                  f"Abaisser le seuil d'alerte."
                    })

        return {
            "cac_records_available": len(records),
            "latest_blended_cac": records[0].get("blended_cac", 0) if records else 0,
            "top_source": records[0].get("top_source", "") if records else "",
            "recommendations": recommendations
        }

    except Exception as e:
        logger.error(f"Erreur analyze_acquisition_efficiency : {e}")
        return {"error": str(e), "recommendations": []}


# ─────────────────────────────────────────
# APPLICATION DES AJUSTEMENTS
# ─────────────────────────────────────────

def apply_adjustment(
    company_id: str,
    agent_name: str,
    parameter: str,
    new_value,
    reason: str = ""
) -> bool:
    """
    Applique un ajustement de paramètre pour un agent.
    Appelé manuellement par le consultant après validation.

    Usage :
        apply_adjustment(
            company_id="uuid",
            agent_name="revenue_velocity",
            parameter="stagnation_threshold_days",
            new_value=14,
            reason="Win rate faible — détecter stagnation plus tôt"
        )
    """
    success = update_agent_config(
        company_id, agent_name, {parameter: new_value}
    )

    if success:
        _log_adjustment(company_id, agent_name, parameter, new_value, reason)
        logger.info(
            f"[adapter] Ajustement appliqué : "
            f"{agent_name}.{parameter} = {new_value} "
            f"({reason})"
        )

    return success


def get_adjustment_history(
    company_id: str, limit: int = 20
) -> list[dict]:
    """
    Retourne l'historique des ajustements pour un client.
    Visible dans le dashboard.
    """
    try:
        client = get_client()
        result = client.table("adapter_adjustments").select("*").eq(
            "company_id", company_id
        ).order("applied_at", desc=True).limit(limit).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Erreur get_adjustment_history : {e}")
        return []


def _log_adjustment(
    company_id: str,
    agent_name: str,
    parameter: str,
    new_value,
    reason: str
) -> None:
    try:
        client = get_client()
        client.table("adapter_adjustments").insert({
            "company_id": company_id,
            "agent_name": agent_name,
            "parameter": parameter,
            "new_value": str(new_value),
            "reason": reason,
            "applied_at": datetime.utcnow().isoformat(),
            "applied_by": "consultant"
        }).execute()
    except Exception as e:
        logger.error(f"Erreur log adjustment : {e}")
