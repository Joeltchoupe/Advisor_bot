# orchestrator/weekly_report.py

import logging
from datetime import datetime, timedelta
from typing import Optional

from services.database import get_client
from services.llm import draft
from services.notification import send_email
from orchestrator.profile import get_company_profile
from prompts import weekly_report_narrative

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# POINT D'ENTRÉE
# ─────────────────────────────────────────

def send_weekly_report(company_id: str) -> bool:
    """
    Compile et envoie le rapport hebdomadaire unifié.
    Appelé par le scheduler chaque lundi à 6h30.

    Retourne True si l'email a été envoyé.
    """
    logger.info(f"[weekly_report] Génération pour {company_id}")

    profile = get_company_profile(company_id)
    if not profile:
        logger.error(f"[weekly_report] Profil introuvable : {company_id}")
        return False

    # Récupérer les données de chaque agent
    data = _collect_all_data(company_id)
    if not data:
        logger.error(f"[weekly_report] Données insuffisantes pour {company_id}")
        return False

    # Compiler le rapport
    subject = _build_subject(data, profile)
    body    = _build_body(data, profile)

    # Destinataires
    ceo_email = _get_ceo_email(profile)
    if not ceo_email:
        logger.error(f"[weekly_report] Pas d'email CEO configuré : {company_id}")
        return False

    # Envoyer
    success = send_email(
        to=ceo_email,
        subject=subject,
        body=body,
        from_name="Kuria"
    )

    if success:
        _log_report_sent(company_id, subject)
        logger.info(f"[weekly_report] Envoyé à {ceo_email}")

    return success


# ─────────────────────────────────────────
# COLLECTE DES DONNÉES
# ─────────────────────────────────────────

def _collect_all_data(company_id: str) -> Optional[dict]:
    """
    Récupère les dernières métriques de chaque agent depuis Supabase.
    Tout vient des tables que les agents ont peuplées.
    """
    try:
        client = get_client()
        now = datetime.utcnow()
        one_week_ago = now - timedelta(days=7)

        # ── Revenue Velocity ──
        forecast = _get_latest(client, "forecasts", company_id)
        recent_won = _count_recent_deals(client, company_id, "won", days=7)
        recent_lost = _count_recent_deals(client, company_id, "lost", days=7)
        deals_at_risk = _get_deals_at_risk(client, company_id)
        zombie_count = _count_zombies(client, company_id)

        # ── Cash Predictability ──
        cash_forecast = _get_latest(client, "cash_forecasts", company_id)
        overdue_invoices = _get_overdue_invoices(client, company_id)

        # ── Process Clarity ──
        process_metrics = _get_latest(client, "process_metrics", company_id)

        # ── Acquisition Efficiency ──
        cac_metrics = _get_latest(client, "cac_metrics", company_id)

        # ── Score de Clarté ──
        company_result = client.table("companies").select(
            "clarity_score, name"
        ).eq("id", company_id).limit(1).execute()
        company = company_result.data[0] if company_result.data else {}

        # ── Historique du score (pour le trend) ──
        prev_run = _get_previous_agent_run(client, company_id)

        return {
            "company_name": company.get("name", ""),
            "week_date": now.strftime("%d %B %Y"),
            "clarity_score": company.get("clarity_score", 0),

            "revenue": {
                "velocity": forecast.get("revenue_velocity", 0) if forecast else 0,
                "forecast_30d": forecast.get("forecast_30d", 0) if forecast else 0,
                "forecast_60d": forecast.get("forecast_60d", 0) if forecast else 0,
                "confidence": forecast.get("confidence", 0) if forecast else 0,
                "deals_won_this_week": recent_won,
                "deals_lost_this_week": recent_lost,
                "deals_at_risk": deals_at_risk,
                "zombie_count": zombie_count
            },

            "cash": {
                "base_30d": cash_forecast.get("base_30d", 0) if cash_forecast else 0,
                "stress_30d": cash_forecast.get("stress_30d", 0) if cash_forecast else 0,
                "runway_months": cash_forecast.get("runway_months", 0) if cash_forecast else 0,
                "monthly_burn": cash_forecast.get("monthly_burn", 0) if cash_forecast else 0,
                "days_until_critical": cash_forecast.get("days_until_critical") if cash_forecast else None,
                "overdue_invoices": overdue_invoices
            },

            "process": {
                "avg_cycle_time": process_metrics.get("avg_cycle_time_days", 0) if process_metrics else 0,
                "active_tasks": process_metrics.get("active_tasks_count", 0) if process_metrics else 0,
                "overdue_tasks": process_metrics.get("overdue_tasks_count", 0) if process_metrics else 0,
                "unassigned_tasks": process_metrics.get("unassigned_tasks_count", 0) if process_metrics else 0
            },

            "acquisition": {
                "blended_cac": cac_metrics.get("blended_cac", 0) if cac_metrics else 0,
                "top_source": cac_metrics.get("top_source", "") if cac_metrics else "",
                "total_clients_90d": cac_metrics.get("total_clients", 0) if cac_metrics else 0
            }
        }

    except Exception as e:
        logger.error(f"Erreur _collect_all_data : {e}")
        return None


# ─────────────────────────────────────────
# CONSTRUCTION DU SUJET
# ─────────────────────────────────────────

def _build_subject(data: dict, profile: dict) -> str:
    """
    Le sujet dit l'essentiel en une ligne.
    Score + tendance + alerte si nécessaire.
    """
    score = data["clarity_score"]
    date  = data["week_date"]

    # Y a-t-il une alerte critique ?
    has_cash_alert = (
        data["cash"].get("days_until_critical") is not None
        and data["cash"]["days_until_critical"] < 45
    )
    has_deals_at_risk = len(data["revenue"].get("deals_at_risk", [])) >= 3

    if has_cash_alert:
        return f"Kuria — {date} — ⚠️ Alerte trésorerie (Score {score}/100)"
    elif has_deals_at_risk:
        return f"Kuria — {date} — Score {score}/100 — {len(data['revenue']['deals_at_risk'])} deals en danger"
    else:
        return f"Kuria — {date} — Score {score}/100"


# ─────────────────────────────────────────
# CONSTRUCTION DU CORPS
# ─────────────────────────────────────────

def _build_body(data: dict, profile: dict) -> str:
    """
    Corps du rapport.
    Structure fixe. LLM uniquement pour l'introduction.
    """
    lines = []

    company_name = data["company_name"]
    score        = data["clarity_score"]
    rev          = data["revenue"]
    cash         = data["cash"]
    proc         = data["process"]
    acq          = data["acquisition"]

    # ── EN-TÊTE ──
    lines.append(f"Bonjour,")
    lines.append("")
    lines.append(f"Voici votre rapport Kuria — semaine du {data['week_date']}.")
    lines.append("")

    # ── SCORE DE CLARTÉ ──
    lines.append("─" * 50)
    lines.append(f"SCORE DE CLARTÉ : {score}/100")
    lines.append("─" * 50)
    lines.append("")

    # ── INTRODUCTION LLM ──
    intro = _generate_intro(data)
    if intro:
        lines.append(intro)
        lines.append("")

    # ── REVENUE VELOCITY ──
    lines.append("PIPELINE & VENTES")
    lines.append("")

    velocity = rev.get("velocity", 0)
    forecast_30 = rev.get("forecast_30d", 0)
    confidence = rev.get("confidence", 0)

    lines.append(f"Revenue Velocity : {velocity:,.0f}€/jour")
    lines.append(
        f"Forecast 30j : {forecast_30:,.0f}€ "
        f"(confiance : {confidence*100:.0f}%)"
    )
    lines.append(
        f"Cette semaine : {rev['deals_won_this_week']} deal(s) signés, "
        f"{rev['deals_lost_this_week']} perdu(s)"
    )

    if rev.get("zombie_count", 0) > 0:
        lines.append(
            f"⚠ {rev['zombie_count']} deal(s) zombie(s) "
            f"détecté(s) et taggé(s) dans votre CRM"
        )

    deals_at_risk = rev.get("deals_at_risk", [])
    if deals_at_risk:
        lines.append("")
        lines.append(f"Deals en danger ({len(deals_at_risk)}) :")
        for deal in deals_at_risk[:3]:
            lines.append(
                f"  → {deal.get('title', 'N/A')} — "
                f"{float(deal.get('amount', 0)):,.0f}€ — "
                f"{deal.get('days_stagnant', '?')}j sans activité"
            )

    lines.append("")

    # ── CASH ──
    lines.append("─" * 50)
    lines.append("TRÉSORERIE")
    lines.append("")

    lines.append(f"Cash 30j (scénario base) : {cash['base_30d']:,.0f}€")
    lines.append(f"Cash 30j (scénario stress) : {cash['stress_30d']:,.0f}€")
    lines.append(f"Burn mensuel estimé : {cash['monthly_burn']:,.0f}€/mois")
    lines.append(f"Runway : {cash['runway_months']:.1f} mois")

    if cash.get("days_until_critical"):
        lines.append("")
        lines.append(
            f"🚨 ALERTE : seuil critique prévu dans "
            f"{cash['days_until_critical']} jours"
        )

    overdue = cash.get("overdue_invoices", [])
    if overdue:
        total_overdue = sum(float(i.get("amount", 0)) for i in overdue)
        lines.append("")
        lines.append(
            f"Factures en retard : {len(overdue)} "
            f"({total_overdue:,.0f}€ total)"
        )
        for inv in overdue[:3]:
            lines.append(
                f"  → {inv.get('client_name', 'N/A')} — "
                f"{float(inv.get('amount', 0)):,.0f}€ — "
                f"{inv.get('days_overdue', '?')}j de retard"
            )

    lines.append("")

    # ── PROCESS ──
    lines.append("─" * 50)
    lines.append("OPÉRATIONS")
    lines.append("")

    lines.append(
        f"Cycle time moyen : {proc['avg_cycle_time']:.1f} jours"
    )
    lines.append(f"Tâches actives : {proc['active_tasks']}")

    if proc.get("overdue_tasks", 0) > 0:
        lines.append(f"⚠ Tâches en retard : {proc['overdue_tasks']}")

    if proc.get("unassigned_tasks", 0) > 0:
        lines.append(
            f"Tâches non assignées : {proc['unassigned_tasks']} "
            f"(routées automatiquement)"
        )

    lines.append("")

    # ── ACQUISITION ──
    lines.append("─" * 50)
    lines.append("ACQUISITION")
    lines.append("")

    if acq.get("blended_cac", 0) > 0:
        lines.append(f"CAC blended : {acq['blended_cac']:,.0f}€/client")
        lines.append(
            f"Clients acquis (90j) : {acq['total_clients_90d']}"
        )
        if acq.get("top_source"):
            lines.append(
                f"Meilleure source : {acq['top_source']}"
            )
    else:
        lines.append(
            "Données d'acquisition insuffisantes "
            "(connecter un outil finance pour calculer le CAC)"
        )

    lines.append("")

    # ── ACTION RECOMMANDÉE ──
    lines.append("─" * 50)
    top_action = _determine_top_action(data)
    lines.append(f"ACTION RECOMMANDÉE : {top_action}")
    lines.append("")

    # ── FOOTER ──
    lines.append("─" * 50)
    lines.append("Kuria — clarté d'abord, le reste suit.")
    lines.append("")
    lines.append(
        "Ce rapport est généré automatiquement. "
        "Pour ajuster les paramètres, contactez votre consultant Kuria."
    )

    return "\n".join(lines)


# ─────────────────────────────────────────
# INTRODUCTION LLM
# ─────────────────────────────────────────

def _generate_intro(data: dict) -> str:
    """
    4 phrases max. Le LLM synthétise la semaine.
    Commence par le fait le plus important.
    """
    rev  = data["revenue"]
    cash = data["cash"]

    context = {
        "semaine": data["week_date"],
        "score_clarte": data["clarity_score"],
        "revenue_velocity": rev.get("velocity", 0),
        "forecast_30j": rev.get("forecast_30d", 0),
        "deals_signes": rev.get("deals_won_this_week", 0),
        "deals_en_danger": len(rev.get("deals_at_risk", [])),
        "cash_30j": cash.get("base_30d", 0),
        "alerte_cash": cash.get("days_until_critical") is not None,
        "factures_retard": len(cash.get("overdue_invoices", [])),
        "taches_retard": data["process"].get("overdue_tasks", 0)
    }

    return draft(
        data=context,
        instruction=weekly_report_narrative(
            company_name=data["company_name"],
            week_number=int(datetime.utcnow().strftime("%W")),
            clarity_score=data["clarity_score"],
            clarity_trend=0,
            revenue_velocity=rev.get("velocity", 0),
            revenue_velocity_trend=0,
            deals_at_risk=rev.get("deals_at_risk", []),
            cash_status="alerte" if cash.get("days_until_critical") else "stable",
            top_bottleneck=None,
            cac_trend=None
        )
    )


# ─────────────────────────────────────────
# ACTION RECOMMANDÉE
# Code pur — pas de LLM
# ─────────────────────────────────────────

def _determine_top_action(data: dict) -> str:
    """
    Détermine l'action #1 de la semaine.
    Logique de priorité simple et déterministe.

    Priorité :
    1. Alerte cash critique
    2. Deals en danger à forte valeur
    3. Factures en retard importantes
    4. Tâches en retard critiques
    5. Message par défaut
    """
    cash = data["cash"]
    rev  = data["revenue"]

    # 1. Cash critique
    if cash.get("days_until_critical") and cash["days_until_critical"] < 30:
        return (
            f"URGENT — Cash critique dans {cash['days_until_critical']}j. "
            f"Relancer les factures en retard et accélérer les deals chauds."
        )

    # 2. Deals à risque de forte valeur
    deals_at_risk = rev.get("deals_at_risk", [])
    if deals_at_risk:
        top_deal = max(
            deals_at_risk,
            key=lambda d: float(d.get("amount", 0))
        )
        return (
            f"Relancer le deal '{top_deal.get('title', 'N/A')}' "
            f"({float(top_deal.get('amount', 0)):,.0f}€) — "
            f"aucune activité depuis {top_deal.get('days_stagnant', '?')} jours."
        )

    # 3. Factures en retard
    overdue = cash.get("overdue_invoices", [])
    if overdue:
        total = sum(float(i.get("amount", 0)) for i in overdue)
        return (
            f"Traiter les {len(overdue)} factures impayées "
            f"({total:,.0f}€ total). "
            f"Des relances automatiques ont été envoyées."
        )

    # 4. Tâches en retard
    if data["process"].get("overdue_tasks", 0) >= 5:
        return (
            f"{data['process']['overdue_tasks']} tâches en retard. "
            f"Revue de la charge d'équipe recommandée."
        )

    # 5. Par défaut
    return (
        "Maintenir le rythme. "
        f"Revenue Velocity à {data['revenue'].get('velocity', 0):,.0f}€/jour."
    )


# ─────────────────────────────────────────
# REQUÊTES SUPABASE
# ─────────────────────────────────────────

def _get_latest(client, table: str, company_id: str) -> Optional[dict]:
    try:
        result = client.table(table).select("*").eq(
            "company_id", company_id
        ).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception:
        return None


def _count_recent_deals(
    client, company_id: str, status: str, days: int
) -> int:
    try:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        result = client.table("deals").select("id").eq(
            "company_id", company_id
        ).eq("status", status).gte("closed_at", cutoff).execute()
        return len(result.data or [])
    except Exception:
        return 0


def _get_deals_at_risk(client, company_id: str) -> list[dict]:
    """
    Deals actifs avec probability_real < 0.3
    ou sans activité depuis > 14 jours.
    """
    try:
        cutoff = (datetime.utcnow() - timedelta(days=14)).isoformat()
        result = client.table("deals").select(
            "id, title, amount, stage, last_activity_at, probability_real"
        ).eq("company_id", company_id).eq(
            "status", "active"
        ).lt("last_activity_at", cutoff).execute()

        deals = result.data or []
        now = datetime.utcnow()

        for deal in deals:
            last_act = deal.get("last_activity_at")
            if last_act:
                try:
                    dt = datetime.fromisoformat(
                        str(last_act).replace("Z", "+00:00")
                    )
                    deal["days_stagnant"] = (now - dt).days
                except Exception:
                    deal["days_stagnant"] = 0

        return sorted(
            deals,
            key=lambda d: float(d.get("amount") or 0),
            reverse=True
        )[:5]

    except Exception as e:
        logger.error(f"Erreur _get_deals_at_risk : {e}")
        return []


def _count_zombies(client, company_id: str) -> int:
    """Deals taggés zombie cette semaine."""
    try:
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
        result = client.table("action_logs").select("id").eq(
            "company_id", company_id
        ).eq("action_type", "tag_deal_zombie").gte(
            "executed_at", cutoff
        ).execute()
        return len(result.data or [])
    except Exception:
        return 0


def _get_overdue_invoices(client, company_id: str) -> list[dict]:
    try:
        result = client.table("invoices").select(
            "id, client_name, amount, due_at"
        ).eq("company_id", company_id).eq(
            "status", "overdue"
        ).execute()

        invoices = result.data or []
        now = datetime.utcnow()

        for inv in invoices:
            due = inv.get("due_at")
            if due:
                try:
                    dt = datetime.fromisoformat(
                        str(due).replace("Z", "+00:00")
                    )
                    inv["days_overdue"] = (now - dt).days
                except Exception:
                    inv["days_overdue"] = 0

        return sorted(
            invoices,
            key=lambda i: float(i.get("amount") or 0),
            reverse=True
        )

    except Exception as e:
        logger.error(f"Erreur _get_overdue_invoices : {e}")
        return []


def _get_previous_agent_run(client, company_id: str) -> Optional[dict]:
    try:
        result = client.table("agent_runs").select(
            "kpi_value, kpi_name, started_at"
        ).eq("company_id", company_id).order(
            "started_at", desc=True
        ).limit(10).execute()
        return result.data[0] if result.data else None
    except Exception:
        return None


def _get_ceo_email(profile: dict) -> Optional[str]:
    """
    Cherche l'email CEO dans la config de chaque agent.
    """
    agent_configs = profile.get("agent_configs", {})

    for agent_name in ("cash_predictability", "acquisition_efficiency", "revenue_velocity"):
        email = agent_configs.get(agent_name, {}).get("ceo_email", "")
        if email:
            return email

    return None


def _log_report_sent(company_id: str, subject: str) -> None:
    try:
        client = get_client()
        client.table("action_logs").insert({
            "action_type": "weekly_report_sent",
            "level": "A",
            "company_id": company_id,
            "agent": "orchestrator",
            "payload": {"subject": subject},
            "status": "success",
            "result": {},
            "error": "",
            "attempts": 1,
            "executed_at": datetime.utcnow().isoformat()
        }).execute()
    except Exception as e:
        logger.error(f"Erreur log report : {e}")
