# api/routes/agents.py

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from api.dependencies import verify_api_key, assert_company_access

router = APIRouter()
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# MODÈLES DE REQUÊTE
# ─────────────────────────────────────────

class RunAgentRequest(BaseModel):
    company_id: str
    agent_name: str     # "revenue_velocity" | "cash_predictability" |
                        # "process_clarity"  | "acquisition_efficiency"


class ApproveActionRequest(BaseModel):
    pending_action_id: str
    company_id: str  # on le garde (compat front), mais on ne lui fait pas confiance


class AdjustConfigRequest(BaseModel):
    company_id: str
    agent_name: str
    parameter: str
    new_value: str | int | float | bool
    reason: str = ""


# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────

@router.post("/run")
def run_agent(
    body: RunAgentRequest,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    """
    Déclenche un agent manuellement pour un client.
    Protégé par X-API-KEY (Option B : on check company_id body).
    """
    assert_company_access(body.company_id, auth_company_id)

    company_id = body.company_id
    agent_name = body.agent_name

    from orchestrator.profile import get_agent_config, is_agent_enabled

    if not is_agent_enabled(company_id, agent_name):
        raise HTTPException(
            status_code=400,
            detail=f"Agent {agent_name} désactivé pour ce client"
        )

    config = get_agent_config(company_id, agent_name)

    try:
        result = _run_agent(agent_name, company_id, config)
        return {
            "agent": result.agent,
            "kpi_name": result.kpi_name,
            "kpi_value": result.kpi_value,
            "actions_taken": len(result.actions_taken),
            "success": result.success,
            "duration_seconds": result.duration_seconds
        }
    except Exception as e:
        logger.error(f"Erreur run_agent {agent_name} : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{company_id}")
def get_agents_status(
    company_id: str,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    """
    Statut des agents pour un client.
    """
    assert_company_access(company_id, auth_company_id)

    from services.database import get_client

    try:
        client = get_client()

        runs = client.table("agent_runs").select("*").eq(
            "company_id", company_id
        ).order("started_at", desc=True).limit(20).execute()

        runs_by_agent: dict[str, dict] = {}
        for run in (runs.data or []):
            agent = run["agent"]
            if agent not in runs_by_agent:
                runs_by_agent[agent] = run

        pending = client.table("pending_actions").select("*").eq(
            "company_id", company_id
        ).eq("status", "pending").execute()

        return {
            "company_id": company_id,
            "agents": runs_by_agent,
            "pending_actions": pending.data or [],
            "pending_count": len(pending.data or [])
        }

    except Exception as e:
        logger.error(f"Erreur get_agents_status : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/actions/pending/{company_id}")
def get_pending_actions(
    company_id: str,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    """
    Actions niveau B en attente de validation.
    """
    assert_company_access(company_id, auth_company_id)

    from services.database import get_client

    try:
        client = get_client()
        result = client.table("pending_actions").select("*").eq(
            "company_id", company_id
        ).eq("status", "pending").order("created_at", desc=True).execute()

        return {"pending_actions": result.data or [], "count": len(result.data or [])}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/actions/approve")
def approve_action(
    body: ApproveActionRequest,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    """
    ✅ Approve une action niveau B.
    On ne fait PAS confiance à body.company_id :
    on lit l'action en DB et on vérifie qu'elle appartient à auth_company_id.
    """
    from services.database import get_client
    from services.executor import executor

    try:
        client = get_client()

        result = client.table("pending_actions").select("*").eq(
            "id", body.pending_action_id
        ).limit(1).execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Action introuvable")

        action_data = result.data[0]

        # Vérif ownership
        if str(action_data["company_id"]) != str(auth_company_id):
            raise HTTPException(status_code=403, detail="Forbidden")

        action_type = action_data["action_type"]
        payload = action_data.get("payload") or {}

        # Dispatcher (company_id = celui de l'action en DB)
        fn, args, kwargs = _resolve_action_fn(
            action_type, payload, action_data["company_id"]
        )

        if fn is None:
            raise HTTPException(
                status_code=400,
                detail=f"Action non dispatchable : {action_type}"
            )

        action_result = executor.approve(body.pending_action_id, fn, *args, **kwargs)

        return {
            "status": action_result.status.value,
            "action_type": action_type
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur approve_action : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/actions/reject/{pending_action_id}")
def reject_action(
    pending_action_id: str,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    """
    ❌ Reject une action niveau B.
    Vérifie que l'action appartient à la company de l'API key.
    """
    from services.database import get_client
    from services.executor import executor

    client = get_client()
    res = client.table("pending_actions").select("id, company_id").eq(
        "id", pending_action_id
    ).limit(1).execute()

    if not res.data:
        raise HTTPException(status_code=404, detail="Action introuvable")

    if str(res.data[0]["company_id"]) != str(auth_company_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    executor.reject(pending_action_id)
    return {"status": "cancelled"}


@router.post("/config/adjust")
def adjust_config(
    body: AdjustConfigRequest,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    """
    Ajuste un paramètre d'agent.
    """
    assert_company_access(body.company_id, auth_company_id)

    from orchestrator.adapter import apply_adjustment

    success = apply_adjustment(
        company_id=body.company_id,
        agent_name=body.agent_name,
        parameter=body.parameter,
        new_value=body.new_value,
        reason=body.reason
    )

    if not success:
        raise HTTPException(status_code=500, detail="Erreur lors de l'ajustement")

    return {
        "status": "adjusted",
        "agent": body.agent_name,
        "parameter": body.parameter,
        "new_value": body.new_value
    }


@router.get("/performance/{company_id}")
def get_performance_report(
    company_id: str,
    auth_company_id: str = Depends(verify_api_key),
) -> dict:
    """
    Rapport performance mensuel.
    """
    assert_company_access(company_id, auth_company_id)

    from orchestrator.adapter import analyze_agent_performance
    return analyze_agent_performance(company_id)


# ─────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────

def _run_agent(agent_name: str, company_id: str, config: dict):
    if agent_name == "revenue_velocity":
        from agents.revenue_velocity import RevenueVelocityAgent
        return RevenueVelocityAgent(company_id, config).run()

    elif agent_name == "cash_predictability":
        from agents.cash_predictability import CashPredictabilityAgent
        return CashPredictabilityAgent(company_id, config).run()

    elif agent_name == "process_clarity":
        from agents.process_clarity import ProcessClarityAgent
        return ProcessClarityAgent(company_id, config).run()

    elif agent_name == "acquisition_efficiency":
        from agents.acquisition_efficiency import AcquisitionEfficiencyAgent
        return AcquisitionEfficiencyAgent(company_id, config).run()

    raise ValueError(f"Agent inconnu : {agent_name}")


def _resolve_action_fn(action_type: str, payload: dict, company_id: str):
    if action_type == "send_invoice_reminder":
        from services.notification import send_email
        return (
            send_email,
            [],
            {
                "to": payload.get("client_email", ""),
                "subject": payload.get("subject", "Rappel de paiement"),
                "body": payload.get("email_body", "")
            }
        )

    elif action_type == "send_nurture_email":
        from services.notification import send_email
        return (
            send_email,
            [],
            {
                "to": payload.get("contact_email", ""),
                "subject": payload.get("subject", ""),
                "body": payload.get("body", "")
            }
        )

    return None, [], {}
