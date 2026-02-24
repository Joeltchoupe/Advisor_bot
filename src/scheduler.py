# scheduler.py

import logging
import os
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from orchestrator.profile import get_all_active_companies
from orchestrator.router import process_events
from orchestrator.weekly_report import send_weekly_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s : %(message)s"
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# JOBS
# Un job = une fonction qui tourne pour TOUS les clients actifs.
# Chaque client a sa propre config.
# Le scheduler ne sait pas ce que font les agents.
# Il dit juste "maintenant, vous tournez".
# ─────────────────────────────────────────

def run_cash_predictability() -> None:
    """
    5h00 quotidien.
    En premier parce qu'il lit le forecast pipeline de la veille.
    """
    companies = get_all_active_companies()
    logger.info(f"[scheduler] Cash Predictability — {len(companies)} clients")

    for company in companies:
        company_id = company["id"]
        try:
            from orchestrator.profile import get_agent_config, is_agent_enabled

            if not is_agent_enabled(company_id, "cash_predictability"):
                continue

            config = get_agent_config(company_id, "cash_predictability")

            from agents.cash_predictability import CashPredictabilityAgent
            agent = CashPredictabilityAgent(company_id, config)
            result = agent.run()

            logger.info(
                f"[cash] {company['name']} — "
                f"KPI: {result.kpi_value:.3f} — "
                f"{len(result.actions_taken)} actions"
            )

        except Exception as e:
            logger.error(
                f"[cash] Erreur pour {company['name']} : {e}"
            )


def run_revenue_velocity() -> None:
    """
    6h00 quotidien.
    Produit le forecast pipeline du jour.
    Publie forecast_updated pour le router.
    """
    companies = get_all_active_companies()
    logger.info(f"[scheduler] Revenue Velocity — {len(companies)} clients")

    for company in companies:
        company_id = company["id"]
        try:
            from orchestrator.profile import get_agent_config, is_agent_enabled

            if not is_agent_enabled(company_id, "revenue_velocity"):
                continue

            config = get_agent_config(company_id, "revenue_velocity")

            from agents.revenue_velocity import RevenueVelocityAgent
            agent = RevenueVelocityAgent(company_id, config)
            result = agent.run()

            logger.info(
                f"[rv] {company['name']} — "
                f"Velocity: {result.kpi_value:.0f}€/jour — "
                f"{len(result.actions_taken)} actions"
            )

        except Exception as e:
            logger.error(
                f"[rv] Erreur pour {company['name']} : {e}"
            )


def run_router() -> None:
    """
    6h15 quotidien.
    Traite tous les events publiés par les agents.
    Fait circuler l'information entre les agents.
    """
    companies = get_all_active_companies()
    logger.info(f"[scheduler] Router — {len(companies)} clients")

    for company in companies:
        company_id = company["id"]
        try:
            processed = process_events(company_id)
            if processed > 0:
                logger.info(
                    f"[router] {company['name']} — "
                    f"{processed} events traités"
                )
        except Exception as e:
            logger.error(
                f"[router] Erreur pour {company['name']} : {e}"
            )


def run_weekly_report() -> None:
    """
    6h30 chaque lundi.
    Compile les données des 4 agents en 1 email.
    """
    companies = get_all_active_companies()
    logger.info(f"[scheduler] Weekly Report — {len(companies)} clients")

    for company in companies:
        company_id = company["id"]
        try:
            success = send_weekly_report(company_id)
            if success:
                logger.info(
                    f"[report] {company['name']} — rapport envoyé"
                )
            else:
                logger.warning(
                    f"[report] {company['name']} — échec envoi"
                )
        except Exception as e:
            logger.error(
                f"[report] Erreur pour {company['name']} : {e}"
            )


def run_process_clarity() -> None:
    """
    9h00 quotidien.
    Suivi des deadlines + routing des tâches.
    À 9h parce que les gens sont au travail.
    """
    companies = get_all_active_companies()
    logger.info(f"[scheduler] Process Clarity — {len(companies)} clients")

    for company in companies:
        company_id = company["id"]
        try:
            from orchestrator.profile import get_agent_config, is_agent_enabled

            if not is_agent_enabled(company_id, "process_clarity"):
                continue

            config = get_agent_config(company_id, "process_clarity")

            from agents.process_clarity import ProcessClarityAgent
            agent = ProcessClarityAgent(company_id, config)
            result = agent.run()

            logger.info(
                f"[process] {company['name']} — "
                f"Cycle time: {result.kpi_value:.1f}j — "
                f"{len(result.actions_taken)} actions"
            )

        except Exception as e:
            logger.error(
                f"[process] Erreur pour {company['name']} : {e}"
            )


def run_acquisition_efficiency() -> None:
    """
    1er du mois à 7h00.
    Le CAC nécessite un mois de données pour être significatif.
    """
    companies = get_all_active_companies()
    logger.info(f"[scheduler] Acquisition Efficiency — {len(companies)} clients")

    for company in companies:
        company_id = company["id"]
        try:
            from orchestrator.profile import get_agent_config, is_agent_enabled

            if not is_agent_enabled(company_id, "acquisition_efficiency"):
                continue

            config = get_agent_config(company_id, "acquisition_efficiency")

            from agents.acquisition_efficiency import AcquisitionEfficiencyAgent
            agent = AcquisitionEfficiencyAgent(company_id, config)
            result = agent.run()

            logger.info(
                f"[acq] {company['name']} — "
                f"CAC: {result.kpi_value:.0f}€ — "
                f"{len(result.actions_taken)} actions"
            )

        except Exception as e:
            logger.error(
                f"[acq] Erreur pour {company['name']} : {e}"
            )


def run_sync_connectors() -> None:
    """
    3h00 quotidien.
    Synchronise les données de tous les connecteurs vers Supabase.
    Tourne AVANT les agents pour qu'ils aient des données fraîches.

    Ordre :
    1. CRM      → deals + contacts
    2. Finance  → invoices + expenses
    3. Payments → complète la finance
    4. Project  → tasks
    """
    companies = get_all_active_companies()
    logger.info(f"[scheduler] Sync Connectors — {len(companies)} clients")

    for company in companies:
        company_id = company["id"]
        try:
            _sync_company(company_id, company["name"])
        except Exception as e:
            logger.error(
                f"[sync] Erreur pour {company['name']} : {e}"
            )


def _sync_company(company_id: str, company_name: str) -> None:
    """
    Synchronise tous les connecteurs d'un client.
    """
    from orchestrator.profile import get_company_profile
    from services.database import save_many

    profile = get_company_profile(company_id)
    tools = profile.get("tools_connected", {})

    total_synced = 0

    # ── CRM ──
    crm_tool = tools.get("crm", {}).get("name", "")
    if crm_tool:
        connector = _get_connector(crm_tool, company_id)
        if connector and connector.connect():
            deals = connector.fetch_deals()
            contacts = connector.fetch_contacts()

            if deals:
                save_many("deals", deals)
                total_synced += len(deals)

            if contacts:
                save_many("contacts", contacts)
                total_synced += len(contacts)

    # ── FINANCE ──
    finance_tool = tools.get("finance", {}).get("name", "")
    if finance_tool:
        connector = _get_connector(finance_tool, company_id)
        if connector and connector.connect():
            invoices = connector.fetch_invoices()
            expenses = connector.fetch_expenses()

            if invoices:
                save_many("invoices", invoices)
                total_synced += len(invoices)

            if expenses:
                save_many("expenses", expenses)
                total_synced += len(expenses)

    # ── PAYMENTS ──
    payment_tool = tools.get("payments", {}).get("name", "")
    if payment_tool:
        connector = _get_connector(payment_tool, company_id)
        if connector and connector.connect():
            payments = connector.fetch_invoices()
            if payments:
                save_many("invoices", payments)
                total_synced += len(payments)

    # ── PROJECT ──
    project_tool = tools.get("project", {}).get("name", "")
    if project_tool:
        connector = _get_connector(project_tool, company_id)
        if connector and connector.connect():
            tasks = connector.fetch_tasks()
            if tasks:
                save_many("tasks", tasks)
                total_synced += len(tasks)

    logger.info(
        f"[sync] {company_name} — "
        f"{total_synced} objets synchronisés"
    )


def _get_connector(tool_name: str, company_id: str):
    """
    Instancie le bon connecteur selon le nom de l'outil.
    Récupère les credentials depuis Supabase.
    """
    from services.database import get_client as db_client

    try:
        client = db_client()
        result = client.table("credentials").select("credentials").eq(
            "company_id", company_id
        ).eq("tool", tool_name).limit(1).execute()

        if not result.data:
            logger.warning(
                f"[sync] Credentials manquants pour {tool_name} "
                f"/ company {company_id}"
            )
            return None

        credentials = result.data[0]["credentials"]

    except Exception as e:
        logger.error(f"[sync] Erreur get credentials {tool_name} : {e}")
        return None

    # CRM
    if tool_name == "hubspot":
        from connectors.crm.hubspot import HubSpotConnector
        return HubSpotConnector(company_id, credentials)
    elif tool_name == "salesforce":
        from connectors.crm.salesforce import SalesforceConnector
        return SalesforceConnector(company_id, credentials)
    elif tool_name == "pipedrive":
        from connectors.crm.pipedrive import PipedriveConnector
        return PipedriveConnector(company_id, credentials)
    elif tool_name == "zoho":
        from connectors.crm.zoho import ZohoConnector
        return ZohoConnector(company_id, credentials)

    # Finance
    elif tool_name == "quickbooks":
        from connectors.finance.quickbooks import QuickBooksConnector
        return QuickBooksConnector(company_id, credentials)
    elif tool_name == "xero":
        from connectors.finance.xero import XeroConnector
        return XeroConnector(company_id, credentials)
    elif tool_name == "freshbooks":
        from connectors.finance.freshbooks import FreshBooksConnector
        return FreshBooksConnector(company_id, credentials)
    elif tool_name == "sage":
        from connectors.finance.sage import SageConnector
        return SageConnector(company_id, credentials)
    elif tool_name == "excel":
        from connectors.finance.excel import ExcelConnector
        return ExcelConnector(company_id, credentials)

    # Payments
    elif tool_name == "stripe":
        from connectors.payments.stripe import StripeConnector
        return StripeConnector(company_id, credentials)
    elif tool_name == "gocardless":
        from connectors.payments.gocardless import GoCardlessConnector
        return GoCardlessConnector(company_id, credentials)

    # Email
    elif tool_name == "gmail":
        from connectors.email.gmail import GmailConnector
        return GmailConnector(company_id, credentials)
    elif tool_name == "outlook":
        from connectors.email.outlook import OutlookConnector
        return OutlookConnector(company_id, credentials)

    # Project
    elif tool_name == "asana":
        from connectors.project.asana import AsanaConnector
        return AsanaConnector(company_id, credentials)
    elif tool_name == "notion":
        from connectors.project.notion import NotionConnector
        return NotionConnector(company_id, credentials)
    elif tool_name == "trello":
        from connectors.project.trello import TrelloConnector
        return TrelloConnector(company_id, credentials)

    logger.warning(f"[sync] Connecteur inconnu : {tool_name}")
    return None


# ─────────────────────────────────────────
# LISTENERS
# Log les succès et erreurs de chaque job
# ─────────────────────────────────────────

def _on_job_executed(event) -> None:
    if event.exception:
        logger.error(
            f"[scheduler] Job {event.job_id} a levé une exception"
        )
    else:
        logger.debug(
            f"[scheduler] Job {event.job_id} terminé"
        )


# ─────────────────────────────────────────
# CONFIGURATION DU SCHEDULER
# ─────────────────────────────────────────

def build_scheduler() -> BlockingScheduler:
    """
    Configure et retourne le scheduler.
    Appelé une fois au démarrage.
    """
    timezone = os.environ.get("SCHEDULER_TIMEZONE", "Europe/Paris")

    scheduler = BlockingScheduler(timezone=timezone)

    # Listener pour le logging
    scheduler.add_listener(
        _on_job_executed,
        EVENT_JOB_ERROR | EVENT_JOB_EXECUTED
    )

    # ── SYNC CONNECTEURS : 3h00 quotidien ──
    # En premier : les agents lisent des données fraîches
    scheduler.add_job(
        run_sync_connectors,
        trigger=CronTrigger(hour=3, minute=0),
        id="sync_connectors",
        name="Sync — Tous les connecteurs",
        max_instances=1,        # pas de double run
        coalesce=True           # si un run a été raté, on en fait 1 seul
    )

    # ── CASH PREDICTABILITY : 5h00 quotidien ──
    scheduler.add_job(
        run_cash_predictability,
        trigger=CronTrigger(hour=5, minute=0),
        id="cash_predictability",
        name="Agent — Cash Predictability",
        max_instances=1,
        coalesce=True
    )

    # ── REVENUE VELOCITY : 6h00 quotidien ──
    scheduler.add_job(
        run_revenue_velocity,
        trigger=CronTrigger(hour=6, minute=0),
        id="revenue_velocity",
        name="Agent — Revenue Velocity",
        max_instances=1,
        coalesce=True
    )

    # ── ROUTER : 6h15 quotidien ──
    scheduler.add_job(
        run_router,
        trigger=CronTrigger(hour=6, minute=15),
        id="router",
        name="Orchestrateur — Router",
        max_instances=1,
        coalesce=True
    )

    # ── WEEKLY REPORT : lundi 6h30 ──
    scheduler.add_job(
        run_weekly_report,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=30),
        id="weekly_report",
        name="Orchestrateur — Weekly Report",
        max_instances=1,
        coalesce=True
    )

    # ── PROCESS CLARITY : 9h00 quotidien ──
    scheduler.add_job(
        run_process_clarity,
        trigger=CronTrigger(hour=9, minute=0),
        id="process_clarity",
        name="Agent — Process Clarity",
        max_instances=1,
        coalesce=True
    )

    # ── ACQUISITION EFFICIENCY : 1er du mois, 7h00 ──
    scheduler.add_job(
        run_acquisition_efficiency,
        trigger=CronTrigger(day=1, hour=7, minute=0),
        id="acquisition_efficiency",
        name="Agent — Acquisition Efficiency",
        max_instances=1,
        coalesce=True
    )

    return scheduler


# ─────────────────────────────────────────
# POINT D'ENTRÉE
# ─────────────────────────────────────────

def main() -> None:
    logger.info("=" * 50)
    logger.info("Kuria Scheduler — Démarrage")
    logger.info("=" * 50)

    scheduler = build_scheduler()

    # Afficher le calendrier au démarrage
    logger.info("Jobs configurés :")
    for job in scheduler.get_jobs():
        logger.info(f"  → {job.name} ({job.trigger})")

    logger.info("Scheduler démarré. En attente des jobs...")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler arrêté proprement.")


if __name__ == "__main__":
    main()
