# api/routes/scan.py

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# MODÈLES
# ─────────────────────────────────────────

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


# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────

@router.post("/company")
def create_company(body: CreateCompanyRequest) -> dict:
    """
    Crée un nouveau client dans Supabase.
    Première étape de l'onboarding.
    """
    from services.database import get_client

    try:
        client = get_client()
        result = client.table("companies").insert({
            "name": body.name,
            "sector": body.sector,
            "size_employees": body.size_employees,
            "size_revenue": body.size_revenue,
            "clarity_score": 0,
            "tools_connected": {},
            "agent_configs": {}
        }).execute()

        if not result.data:
            raise HTTPException(
                status_code=500,
                detail="Erreur création company"
            )

        company = result.data[0]
        logger.info(f"Nouvelle company créée : {company['id']} — {body.name}")

        return {
            "company_id": company["id"],
            "name": company["name"]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur create_company : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/credentials")
def save_credentials(body: SaveCredentialsRequest) -> dict:
    """
    Sauvegarde les credentials d'un outil pour un client.
    Appelé après que le CEO a connecté un outil (OAuth).
    """
    from services.database import get_client

    try:
        client = get_client()

        # Upsert credentials
        client.table("credentials").upsert({
            "company_id": body.company_id,
            "tool": body.tool,
            "credentials": body.credentials
        }, on_conflict="company_id,tool").execute()

        # Mettre à jour tools_connected dans companies
        company_result = client.table("companies").select(
            "tools_connected"
        ).eq("id", body.company_id).limit(1).execute()

        tools = {}
        if company_result.data:
            tools = company_result.data[0].get("tools_connected") or {}

        # Déduire la catégorie de l'outil
        category = _get_tool_category(body.tool)
        tools[category] = {"name": body.tool, "connected": True}

        client.table("companies").update({
            "tools_connected": tools
        }).eq("id", body.company_id).execute()

        logger.info(
            f"Credentials sauvegardés : {body.tool} "
            f"pour company {body.company_id}"
        )

        return {
            "tool": body.tool,
            "category": category,
            "status": "connected"
        }

    except Exception as e:
        logger.error(f"Erreur save_credentials : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/init")
def init_scan(
    body: InitScanRequest,
    background_tasks: BackgroundTasks
) -> dict:
    """
    Lance le scan initial d'un nouveau client.
    → Sync de tous les connecteurs
    → Premier run de tous les agents
    → Calcul du Score de Clarté initial

    Tourne en arrière-plan (peut prendre 5-10 minutes).
    """
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
def get_scan_status(company_id: str) -> dict:
    """
    Vérifie si le scan initial est terminé.
    Pollé par le dashboard toutes les 30 secondes.
    """
    from services.database import get_client

    try:
        client = get_client()

        # Vérifier si des données existent
        deals = client.table("deals").select(
            "id"
        ).eq("company_id", company_id).limit(1).execute()

        invoices = client.table("invoices").select(
            "id"
        ).eq("company_id", company_id).limit(1).execute()

        runs = client.table("agent_runs").select(
            "agent, started_at"
        ).eq("company_id", company_id).execute()

        agents_run = list({r["agent"] for r in (runs.data or [])})

        company = client.table("companies").select(
            "clarity_score"
        ).eq("id", company_id).limit(1).execute()

        clarity = company.data[0].get("clarity_score", 0) if company.data else 0

        is_complete = (
            bool(deals.data) and
            len(agents_run) >= 2
        )

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
# SCAN INITIAL (tourne en arrière-plan)
# ─────────────────────────────────────────

def _run_initial_scan(company_id: str, full_sync: bool) -> None:
    """
    Orchestre le scan complet d'un nouveau client.
    Tourne en background task.
    """
    logger.info(f"[scan] Scan initial démarré pour {company_id}")

    try:
        # 1. Sync de tous les connecteurs
        if full_sync:
            from scheduler import _sync_company
            _sync_company(company_id, company_id)
            logger.info(f"[scan] Sync terminée pour {company_id}")

        # 2. Revenue Velocity (pipeline + leads)
        from orchestrator.profile import get_agent_config
        rv_config = get_agent_config(company_id, "revenue_velocity")
        from agents.revenue_velocity import RevenueVelocityAgent
        RevenueVelocityAgent(company_id, rv_config).run()
        logger.info(f"[scan] Revenue Velocity terminé")

        # 3. Cash Predictability
        cash_config = get_agent_config(company_id, "cash_predictability")
        from agents.cash_predictability import CashPredictabilityAgent
        CashPredictabilityAgent(company_id, cash_config).run()
        logger.info(f"[scan] Cash Predictability terminé")

        # 4. Process Clarity
        proc_config = get_agent_config(company_id, "process_clarity")
        from agents.process_clarity import ProcessClarityAgent
        ProcessClarityAgent(company_id, proc_config).run()
        logger.info(f"[scan] Process Clarity terminé")

        # 5. Acquisition Efficiency
        acq_config = get_agent_config(company_id, "acquisition_efficiency")
        from agents.acquisition_efficiency import AcquisitionEfficiencyAgent
        AcquisitionEfficiencyAgent(company_id, acq_config).run()
        logger.info(f"[scan] Acquisition Efficiency terminé")

        # 6. Calcul du Score de Clarté initial
        clarity_score = _compute_clarity_score(company_id)
        from orchestrator.profile import update_clarity_score
        update_clarity_score(company_id, clarity_score)
        logger.info(
            f"[scan] Score de Clarté calculé : {clarity_score}/100"
        )

        logger.info(f"[scan] Scan initial terminé pour {company_id}")

    except Exception as e:
        logger.error(f"[scan] Erreur scan initial {company_id} : {e}")


def _compute_clarity_score(company_id: str) -> int:
    """
    Calcule le Score de Clarté initial.

    2 composantes :
    A. Lisibilité machine (50%)
       → Qualité et complétude des données dans Supabase

    B. Compatibilité structurelle (50%)
       → Outils connectés, process mesurables

    Score 0-100.
    """
    from services.database import get_client, get

    try:
        client = get_client()

        score_a = 0    # Lisibilité machine
        score_b = 0    # Compatibilité structurelle

        # ── A. LISIBILITÉ MACHINE (50 points) ──

        # Deals avec montant (10 pts)
        deals = get("deals", company_id)
        if deals:
            with_amount = sum(
                1 for d in deals if d.get("amount") and float(d.get("amount")) > 0
            )
            score_a += int((with_amount / len(deals)) * 10)

        # Deals avec date d'activité (10 pts)
        if deals:
            with_activity = sum(
                1 for d in deals if d.get("last_activity_at")
            )
            score_a += int((with_activity / len(deals)) * 10)

        # Deals avec source trackée (10 pts)
        if deals:
            with_source = sum(
                1 for d in deals if d.get("source")
            )
            score_a += int((with_source / len(deals)) * 10)

        # Factures avec dates (10 pts)
        invoices = get("invoices", company_id)
        if invoices:
            with_dates = sum(
                1 for i in invoices
                if i.get("due_at") and i.get("issued_at")
            )
            score_a += int((with_dates / len(invoices)) * 10)

        # Tâches avec assigné et deadline (10 pts)
        tasks = get("tasks", company_id)
        if tasks:
            well_defined = sum(
                1 for t in tasks
                if t.get("assignee_id") and t.get("due_at")
            )
            score_a += int((well_defined / len(tasks)) * 10)

        # ── B. COMPATIBILITÉ STRUCTURELLE (50 points) ──

        company_result = client.table("companies").select(
            "tools_connected"
        ).eq("id", company_id).limit(1).execute()

        tools = {}
        if company_result.data:
            tools = company_result.data[0].get("tools_connected") or {}

        # CRM connecté (15 pts)
        if tools.get("crm", {}).get("connected"):
            score_b += 15

        # Finance connecté (15 pts)
        if tools.get("finance", {}).get("connected"):
            score_b += 15

        # Email connecté (10 pts)
        if tools.get("email", {}).get("connected"):
            score_b += 10

        # Project connecté (10 pts)
        if tools.get("project", {}).get("connected"):
            score_b += 10

        total = score_a + score_b
        return max(0, min(100, total))

    except Exception as e:
        logger.error(f"Erreur _compute_clarity_score : {e}")
        return 0


def _get_tool_category(tool_name: str) -> str:
    """Retourne la catégorie d'un outil."""
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
