# api/routes/dashboard.py

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends

from api.dependencies import verify_api_key, assert_company_access

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/overview/{company_id}")
def get_overview(
    company_id: str,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    assert_company_access(company_id, auth_company_id)

    from services.database import get_client
    from orchestrator.profile import get_company_profile

    try:
        client = get_client()
        profile = get_company_profile(company_id)

        if not profile:
            raise HTTPException(status_code=404, detail="Client introuvable")

        clarity_score = profile.get("clarity_score", 0)
        kpis = _get_latest_kpis(client, company_id)
        alerts = _get_active_alerts(client, company_id)

        pending = client.table("pending_actions").select(
            "id, action_type, description, agent, created_at"
        ).eq("company_id", company_id).eq(
            "status", "pending"
        ).order("created_at", desc=True).execute()

        recent_runs = client.table("agent_runs").select(
            "agent, kpi_name, kpi_value, started_at, success"
        ).eq("company_id", company_id).order(
            "started_at", desc=True
        ).limit(8).execute()

        return {
            "company": profile.get("company", {}),
            "clarity_score": clarity_score,
            "kpis": kpis,
            "alerts": alerts,
            "pending_actions": pending.data or [],
            "recent_runs": recent_runs.data or []
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur get_overview {company_id} : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pipeline/{company_id}")
def get_pipeline(
    company_id: str,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    assert_company_access(company_id, auth_company_id)

    from services.database import get_client, get

    try:
        client = get_client()
        active_deals = get("deals", company_id, {"status": "active"})

        forecast = client.table("forecasts").select("*").eq(
            "company_id", company_id
        ).limit(1).execute()

        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
        recent_won = client.table("deals").select(
            "id, title, amount, closed_at"
        ).eq("company_id", company_id).eq(
            "status", "won"
        ).gte("closed_at", cutoff).execute()

        recent_lost = client.table("deals").select(
            "id, title, amount, closed_at"
        ).eq("company_id", company_id).eq(
            "status", "lost"
        ).gte("closed_at", cutoff).execute()

        win_loss = client.table("win_loss_analyses").select(
            "deal_title, outcome, total_days, analysis, analyzed_at"
        ).eq("company_id", company_id).order(
            "analyzed_at", desc=True
        ).limit(5).execute()

        stage_distribution = _compute_stage_distribution(active_deals)

        return {
            "forecast": forecast.data[0] if forecast.data else {},
            "active_deals": active_deals,
            "stage_distribution": stage_distribution,
            "recent_won": recent_won.data or [],
            "recent_lost": recent_lost.data or [],
            "win_loss_analyses": win_loss.data or []
        }

    except Exception as e:
        logger.error(f"Erreur get_pipeline {company_id} : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cash/{company_id}")
def get_cash(
    company_id: str,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    assert_company_access(company_id, auth_company_id)

    from services.database import get_client

    try:
        client = get_client()

        cash_forecast = client.table("cash_forecasts").select("*").eq(
            "company_id", company_id
        ).limit(1).execute()

        overdue = client.table("invoices").select("*").eq(
            "company_id", company_id
        ).eq("status", "overdue").order("due_at").execute()

        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
        recently_paid = client.table("invoices").select(
            "id, client_name, amount, paid_at, payment_delay_days"
        ).eq("company_id", company_id).eq(
            "status", "paid"
        ).gte("paid_at", cutoff).execute()

        recent_expenses = client.table("expenses").select(
            "id, vendor, category, amount, date"
        ).eq("company_id", company_id).gte(
            "date", cutoff
        ).order("date", desc=True).limit(20).execute()

        reminders = client.table("invoice_reminders").select(
            "invoice_id, reminder_number, sent_at"
        ).eq("company_id", company_id).order(
            "sent_at", desc=True
        ).limit(20).execute()

        paid_data = recently_paid.data or []
        delays = [
            i.get("payment_delay_days", 0)
            for i in paid_data
            if i.get("payment_delay_days") is not None
        ]
        avg_dso = sum(delays) / len(delays) if delays else 0

        return {
            "cash_forecast": cash_forecast.data[0] if cash_forecast.data else {},
            "overdue_invoices": overdue.data or [],
            "overdue_total": sum(float(i.get("amount", 0)) for i in (overdue.data or [])),
            "recently_paid": paid_data,
            "avg_dso_days": round(avg_dso, 1),
            "recent_expenses": recent_expenses.data or [],
            "reminders_sent": reminders.data or []
        }

    except Exception as e:
        logger.error(f"Erreur get_cash {company_id} : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/process/{company_id}")
def get_process(
    company_id: str,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    assert_company_access(company_id, auth_company_id)

    from services.database import get_client, get

    try:
        client = get_client()

        metrics = client.table("process_metrics").select("*").eq(
            "company_id", company_id
        ).limit(1).execute()

        active_tasks = get("tasks", company_id)
        overdue_tasks = [t for t in active_tasks if t.get("status") == "overdue"]
        unassigned_tasks = [
            t for t in active_tasks
            if not t.get("assignee_id") and t.get("status") != "done"
        ]

        workload = _compute_workload_distribution(active_tasks)

        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
        completed = client.table("tasks").select(
            "id, title, assignee_name, cycle_time_days, completed_at"
        ).eq("company_id", company_id).eq(
            "status", "done"
        ).gte("completed_at", cutoff).execute()

        adjustments = client.table("adapter_adjustments").select("*").eq(
            "company_id", company_id
        ).eq("agent_name", "process_clarity").order(
            "applied_at", desc=True
        ).limit(5).execute()

        return {
            "metrics": metrics.data[0] if metrics.data else {},
            "overdue_tasks": overdue_tasks,
            "unassigned_tasks": unassigned_tasks,
            "workload_distribution": workload,
            "completed_tasks_30d": completed.data or [],
            "recent_adjustments": adjustments.data or []
        }

    except Exception as e:
        logger.error(f"Erreur get_process {company_id} : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/acquisition/{company_id}")
def get_acquisition(
    company_id: str,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    assert_company_access(company_id, auth_company_id)

    from services.database import get_client

    try:
        client = get_client()

        cac = client.table("cac_metrics").select("*").eq(
            "company_id", company_id
        ).limit(1).execute()

        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
        recent_leads = client.table("contacts").select(
            "id, email, company_name, source, score, score_label, score_reason, created_at"
        ).eq("company_id", company_id).gte(
            "created_at", cutoff
        ).order("score", desc=True).limit(20).execute()

        all_scored = client.table("contacts").select(
            "score_label"
        ).eq("company_id", company_id).not_.is_("score_label", "null").execute()

        score_dist = {"hot": 0, "warm": 0, "cold": 0}
        for c in (all_scored.data or []):
            label = c.get("score_label", "cold")
            if label in score_dist:
                score_dist[label] += 1

        return {
            "cac_metrics": cac.data[0] if cac.data else {},
            "recent_leads": recent_leads.data or [],
            "score_distribution": score_dist,
            "total_scored": sum(score_dist.values())
        }

    except Exception as e:
        logger.error(f"Erreur get_acquisition {company_id} : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs/{company_id}")
def get_action_logs(
    company_id: str,
    agent: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    assert_company_access(company_id, auth_company_id)

    from services.database import get_client

    try:
        client = get_client()
        query = client.table("action_logs").select("*").eq(
            "company_id", company_id
        ).order("executed_at", desc=True).limit(limit)

        if agent:
            query = query.eq("agent", agent)

        result = query.execute()

        return {"logs": result.data or [], "count": len(result.data or [])}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
# UTILITAIRES (inchangés)
# ─────────────────────────────────────────

def _get_latest_kpis(client, company_id: str) -> dict:
    kpis = {}
    agents = ["revenue_velocity", "cash_predictability", "process_clarity", "acquisition_efficiency"]

    for agent in agents:
        result = client.table("agent_runs").select(
            "kpi_name, kpi_value, started_at"
        ).eq("company_id", company_id).eq(
            "agent", agent
        ).order("started_at", desc=True).limit(1).execute()

        if result.data:
            kpis[agent] = result.data[0]
    return kpis


def _get_active_alerts(client, company_id: str) -> list[dict]:
    alerts = []

    cash = client.table("cash_forecasts").select(
        "days_until_critical"
    ).eq("company_id", company_id).limit(1).execute()

    if cash.data:
        days = cash.data[0].get("days_until_critical")
        if days and days < 60:
            alerts.append({
                "type": "cash_critical",
                "severity": "high" if days < 30 else "medium",
                "message": f"Seuil de trésorerie critique dans {days} jours",
                "agent": "cash_predictability"
            })

    cutoff = (datetime.utcnow() - timedelta(days=14)).isoformat()
    stagnant = client.table("deals").select("id").eq(
        "company_id", company_id
    ).eq("status", "active").lt(
        "last_activity_at", cutoff
    ).execute()

    if stagnant.data:
        alerts.append({
            "type": "deals_stagnant",
            "severity": "medium",
            "message": f"{len(stagnant.data)} deal(s) sans activité depuis 14+ jours",
            "agent": "revenue_velocity"
        })

    return alerts


def _compute_stage_distribution(deals: list) -> list[dict]:
    stages: dict[str, dict] = {}
    for deal in deals:
        stage = deal.get("stage", "unknown")
        amount = float(deal.get("amount") or 0)

        if stage not in stages:
            stages[stage] = {"stage": stage, "count": 0, "total_amount": 0}
        stages[stage]["count"] += 1
        stages[stage]["total_amount"] += amount

    return sorted(stages.values(), key=lambda s: s.get("stage_order", 0))


def _compute_workload_distribution(tasks: list) -> list[dict]:
    workload: dict[str, dict] = {}
    for task in tasks:
        if task.get("status") == "done":
            continue

        name = task.get("assignee_name") or "Non assigné"
        if name not in workload:
            workload[name] = {"assignee": name, "total": 0, "overdue": 0, "in_progress": 0}

        workload[name]["total"] += 1
        if task.get("status") == "overdue":
            workload[name]["overdue"] += 1
        elif task.get("status") == "in_progress":
            workload[name]["in_progress"] += 1

    return sorted(workload.values(), key=lambda w: w["total"], reverse=True)
