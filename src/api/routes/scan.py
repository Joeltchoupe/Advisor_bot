# api/routes/scan.py

import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel

from api.dependencies import verify_api_key, assert_company_access

router = APIRouter()
logger = logging.getLogger(__name__)


class InitScanRequest(BaseModel):
    company_id: str
    trigger_full_sync: bool = True


class CreateCompanyRequest(BaseModel):
    name: str
    sector: str = ""
    size_employees: int = 0
    size_revenue: float = 0.0


class SaveCredentialsRequest(BaseModel):
    company_id: str
    tool: str
    credentials: dict


@router.post("/company")
def create_company(body: CreateCompanyRequest) -> dict:
    """
    Signup V1 : crée une company + génère api_key.
    Endpoint PUBLIC (pas d'API key requise).
    """
    from services.database import get_client

    try:
        client = get_client()

        api_key = "kuria_" + secrets.token_urlsafe(32)

        result = client.table("companies").insert({
            "name": body.name,
            "sector": body.sector,
            "size_employees": body.size_employees,
            "size_revenue": body.size_revenue,
            "clarity_score": 0,
            "tools_connected": {},
            "agent_configs": {},
            "api_key": api_key
        }).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Erreur création company")

        company = result.data[0]
        logger.info(f"Nouvelle company créée : {company['id']} — {body.name}")

        return {
            "company_id": company["id"],
            "name": company["name"],
            "api_key": api_key,  # ← indispensable pour Lovable
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur create_company : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/credentials")
def save_credentials(
    body: SaveCredentialsRequest,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    """
    Protégé : il faut X-API-KEY
    """
    assert_company_access(body.company_id, auth_company_id)

    from services.database import get_client

    try:
        client = get_client()

        client.table("credentials").upsert({
            "company_id": body.company_id,
            "tool": body.tool,
            "credentials": body.credentials
        }, on_conflict="company_id,tool").execute()

        company_result = client.table("companies").select(
            "tools_connected"
        ).eq("id", body.company_id).limit(1).execute()

        tools = company_result.data[0].get("tools_connected") if company_result.data else {}
        tools = tools or {}

        category = _get_tool_category(body.tool)
        tools[category] = {"name": body.tool, "connected": True}

        client.table("companies").update({
            "tools_connected": tools
        }).eq("id", body.company_id).execute()

        return {"tool": body.tool, "category": category, "status": "connected"}

    except Exception as e:
        logger.error(f"Erreur save_credentials : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/init")
def init_scan(
    body: InitScanRequest,
    background_tasks: BackgroundTasks,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    """
    Protégé : il faut X-API-KEY
    """
    assert_company_access(body.company_id, auth_company_id)

    background_tasks.add_task(
        _run_initial_scan,
        body.company_id,
        body.trigger_full_sync
    )

    return {
        "status": "scan_initiated",
        "company_id": body.company_id,
        "message": "Scan en cours. Résultats disponibles dans 5-10 minutes."
    }


@router.get("/status/{company_id}")
def get_scan_status(
    company_id: str,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    """
    Protégé : il faut X-API-KEY
    """
    assert_company_access(company_id, auth_company_id)

    from services.database import get_client

    try:
        client = get_client()

        deals = client.table("deals").select("id").eq("company_id", company_id).limit(1).execute()
        invoices = client.table("invoices").select("id").eq("company_id", company_id).limit(1).execute()

        runs = client.table("agent_runs").select("agent, started_at").eq(
            "company_id", company_id
        ).execute()

        agents_run = list({r["agent"] for r in (runs.data or [])})

        company = client.table("companies").select("clarity_score").eq(
            "id", company_id
        ).limit(1).execute()

        clarity = company.data[0].get("clarity_score", 0) if company.data else 0

        is_complete = bool(deals.data) and len(agents_run) >= 2

        return {
            "company_id": company_id,
            "scan_complete": is_complete,
            "has_deals": bool(deals.data),
            "has_invoices": bool(invoices.data),
            "agents_completed": agents_run,
            "clarity_score": clarity
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
# Scan initial : inchangé
# ─────────────────────────────────────────

def _run_initial_scan(company_id: str, full_sync: bool) -> None:
    logger.info(f"[scan] Scan initial démarré pour {company_id}")
    try:
        if full_sync:
            from scheduler import _sync_company
            _sync_company(company_id, company_id)

        from orchestrator.profile import get_agent_config

        rv_config = get_agent_config(company_id, "revenue_velocity")
        from agents.revenue_velocity import RevenueVelocityAgent
        RevenueVelocityAgent(company_id, rv_config).run()

        cash_config = get_agent_config(company_id, "cash_predictability")
        from agents.cash_predictability import CashPredictabilityAgent
        CashPredictabilityAgent(company_id, cash_config).run()

        proc_config = get_agent_config(company_id, "process_clarity")
        from agents.process_clarity import ProcessClarityAgent
        ProcessClarityAgent(company_id, proc_config).run()

        acq_config = get_agent_config(company_id, "acquisition_efficiency")
        from agents.acquisition_efficiency import AcquisitionEfficiencyAgent
        AcquisitionEfficiencyAgent(company_id, acq_config).run()

        clarity_score = _compute_clarity_score(company_id)
        from orchestrator.profile import update_clarity_score
        update_clarity_score(company_id, clarity_score)

        logger.info(f"[scan] Scan initial terminé pour {company_id}")

    except Exception as e:
        logger.error(f"[scan] Erreur scan initial {company_id} : {e}")


def _compute_clarity_score(company_id: str) -> int:
    from services.database import get_client, get
    try:
        client = get_client()
        score_a = 0
        score_b = 0

        deals = get("deals", company_id)
        if deals:
            with_amount = sum(1 for d in deals if d.get("amount") and float(d.get("amount")) > 0)
            with_activity = sum(1 for d in deals if d.get("last_activity_at"))
            with_source = sum(1 for d in deals if d.get("source"))
            score_a += int((with_amount / len(deals)) * 10)
            score_a += int((with_activity / len(deals)) * 10)
            score_a += int((with_source / len(deals)) * 10)

        invoices = get("invoices", company_id)
        if invoices:
            with_dates = sum(1 for i in invoices if i.get("due_at") and i.get("issued_at"))
            score_a += int((with_dates / len(invoices)) * 10)

        tasks = get("tasks", company_id)
        if tasks:
            well_defined = sum(1 for t in tasks if t.get("assignee_id") and t.get("due_at"))
            score_a += int((well_defined / len(tasks)) * 10)

        company_result = client.table("companies").select("tools_connected").eq("id", company_id).limit(1).execute()
        tools = company_result.data[0].get("tools_connected") if company_result.data else {}
        tools = tools or {}

        if tools.get("crm", {}).get("connected"):
            score_b += 15
        if tools.get("finance", {}).get("connected"):
            score_b += 15
        if tools.get("email", {}).get("connected"):
            score_b += 10
        if tools.get("project", {}).get("connected"):
            score_b += 10

        return max(0, min(100, score_a + score_b))

    except Exception as e:
        logger.error(f"Erreur _compute_clarity_score : {e}")
        return 0


def _get_tool_category(tool_name: str) -> str:
    categories = {
        "hubspot": "crm", "salesforce": "crm",
        "pipedrive": "crm", "zoho": "crm",
        "quickbooks": "finance", "xero": "finance",
        "freshbooks": "finance", "sage": "finance",
        "excel": "finance",
        "stripe": "payments", "gocardless": "payments",
        "gmail": "email", "outlook": "email",
        "asana": "project", "notion": "project",
        "trello": "project"
    }
    return categories.get(tool_name, "other")
