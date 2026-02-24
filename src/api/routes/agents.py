# api/routes/agents.py

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

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
    company_id: str


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
def run_agent(body: RunAgentRequest) -> dict:
    """
    Déclenche un agent manuellement pour un client.
    Utilisé depuis le dashboard pour forcer un run.
    """
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
def get_agents_status(company_id: str) -> dict:
    """
    Retourne le statut de chaque agent pour un client :
    → Dernier run (quand, KPI, succès)
    → Actions en attente (niveau B)
    → Prochains runs planifiés
    """
    from services.database import get_client

    try:
        client = get_client()

        # Dernier run par agent
        runs = client.table("agent_runs").select("*").eq(
            "company_id", company_id
        ).order("started_at", desc=True).limit(20).execute()

        runs_by_agent: dict[str, dict] = {}
        for run in (runs.data or []):
            agent = run["agent"]
            if agent not in runs_by_agent:
                runs_by_agent[agent] = run

        # Actions en attente
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
def get_pending_actions(company_id: str) -> dict:
    """
    Retourne les actions niveau B en attente de validation.
    Affichées dans le dashboard pour que le CEO valide.
    """
    from services.database import get_client

    try:
        client = get_client()
        result = client.table("pending_actions").select("*").eq(
            "company_id", company_id
        ).eq("status", "pending").order(
            "created_at", desc=True
        ).execute()

        return {
            "pending_actions": result.data or [],
            "count": len(result.data or [])
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/actions/approve")
def approve_action(body: ApproveActionRequest) -> dict:
    """
    L'humain clique ✅ sur une action niveau B.
    L'executor l'exécute immédiatement.

    Note V1 : l'exécution réelle dépend du type d'action.
    On dispatch selon action_type.
    """
    from services.database import get_client
    from services.executor import executor

    try:
        client = get_client()

        # Récupérer l'action
        result = client.table("pending_actions").select("*").eq(
            "id", body.pending_action_id
        ).limit(1).execute()

        if not result.data:
            raise HTTPException(
                status_code=404,
                detail="Action introuvable"
            )

        action_data = result.data[0]
        action_type = action_data["action_type"]
        payload     = action_data["payload"]

        # Dispatcher selon le type
        fn, args, kwargs = _resolve_action_fn(
            action_type, payload, body.company_id
        )

        if fn is None:
            raise HTTPException(
                status_code=400,
                detail=f"Action non dispatchable : {action_type}"
            )

        from services.executor import Action, ActionLevel
        action = Action(
            type=action_type,
            level=ActionLevel.A,
            company_id=body.company_id,
            agent=action_data["agent"],
            payload=payload
        )

        action_result = executor.approve(
            body.pending_action_id, fn, *args, **kwargs
        )

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
def reject_action(pending_action_id: str) -> dict:
    """L'humain clique ❌."""
    from services.executor import executor
    executor.reject(pending_action_id)
    return {"status": "cancelled"}


@router.post("/config/adjust")
def adjust_config(body: AdjustConfigRequest) -> dict:
    """
    Ajuste un paramètre de configuration d'un agent.
    Appelé depuis l'interface de l'adapter (call mensuel).
    """
    from orchestrator.adapter import apply_adjustment

    success = apply_adjustment(
        company_id=body.company_id,
        agent_name=body.agent_name,
        parameter=body.parameter,
        new_value=body.new_value,
        reason=body.reason
    )

    if not success:
        raise HTTPException(
            status_code=500,
            detail="Erreur lors de l'ajustement"
        )

    return {
        "status": "adjusted",
        "agent": body.agent_name,
        "parameter": body.parameter,
        "new_value": body.new_value
    }


@router.get("/performance/{company_id}")
def get_performance_report(company_id: str) -> dict:
    """
    Rapport de performance des agents (pour le call mensuel).
    """
    from orchestrator.adapter import analyze_agent_performance
    return analyze_agent_performance(company_id)


# ─────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────

def _run_agent(agent_name: str, company_id: str, config: dict):
    """Instancie et lance le bon agent."""
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


def _resolve_action_fn(
    action_type: str, payload: dict, company_id: str
):
    """
    Pour une action en attente (niveau B),
    retourne la fonction à appeler + ses arguments.

    C'est la table de dispatch des actions niveau B.
    """
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

    # Pas de fonction connue pour ce type
    return None, [], {}
