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
# ─────────────────────────────────────────

def run_cash_predictability() -> None:
    """5h00 quotidien."""
    for company in get_all_active_companies():
        company_id = company["id"]
        try:
            from orchestrator.profile import get_agent_config, is_agent_enabled
            if not is_agent_enabled(company_id, "cash_predictability"):
                continue

            config = get_agent_config(company_id, "cash_predictability")
            from agents.cash_predictability import CashPredictabilityAgent
            result = CashPredictabilityAgent(company_id, config).run()

            logger.info(
                f"[cash] {company['name']} — "
                f"KPI: {result.kpi_value:.3f}"
            )
        except Exception as e:
            logger.error(f"[cash] {company['name']} : {e}")


def run_revenue_velocity() -> None:
    """6h00 quotidien."""
    for company in get_all_active_companies():
        company_id = company["id"]
        try:
            from orchestrator.profile import get_agent_config, is_agent_enabled
            if not is_agent_enabled(company_id, "revenue_velocity"):
                continue

            config = get_agent_config(company_id, "revenue_velocity")
            from agents.revenue_velocity import RevenueVelocityAgent
            result = RevenueVelocityAgent(company_id, config).run()

            logger.info(
                f"[rv] {company['name']} — "
                f"Velocity: {result.kpi_value:.0f}€/jour"
            )
        except Exception as e:
            logger.error(f"[rv] {company['name']} : {e}")


def run_router() -> None:
    """6h15 quotidien."""
    for company in get_all_active_companies():
        try:
            processed = process_events(company["id"])
            if processed > 0:
                logger.info(
                    f"[router] {company['name']} — {processed} events"
                )
        except Exception as e:
            logger.error(f"[router] {company['name']} : {e}")


def run_weekly_report() -> None:
    """6h30 — lundi uniquement."""
    for company in get_all_active_companies():
        try:
            success = send_weekly_report(company["id"])
            status  = "envoyé" if success else "échec"
            logger.info(f"[report] {company['name']} — {status}")
        except Exception as e:
            logger.error(f"[report] {company['name']} : {e}")


def run_process_clarity() -> None:
    """9h00 quotidien."""
    for company in get_all_active_companies():
        company_id = company["id"]
        try:
            from orchestrator.profile import get_agent_config, is_agent_enabled
            if not is_agent_enabled(company_id, "process_clarity"):
                continue

            config = get_agent_config(company_id, "process_clarity")
            from agents.process_clarity import ProcessClarityAgent
            result = ProcessClarityAgent(company_id, config).run()

            logger.info(
                f"[process] {company['name']} — "
                f"Cycle time: {result.kpi_value:.1f}j"
            )
        except Exception as e:
            logger.error(f"[process] {company['name']} : {e}")


def run_acquisition_efficiency() -> None:
    """1er du mois, 7h00."""
    for company in get_all_active_companies():
        company_id = company["id"]
        try:
            from orchestrator.profile import get_agent_config, is_agent_enabled
            if not is_agent_enabled(company_id, "acquisition_efficiency"):
                continue

            config = get_agent_config(company_id, "acquisition_efficiency")
            from agents.acquisition_efficiency import AcquisitionEfficiencyAgent
            result = AcquisitionEfficiencyAgent(company_id, config).run()

            logger.info(
                f"[acq] {company['name']} — CAC: {result.kpi_value:.0f}€"
            )
        except Exception as e:
            logger.error(f"[acq] {company['name']} : {e}")


def run_update_clarity_scores() -> None:
    """
    Dimanche 23h00 — avant le rapport du lundi.
    Recalcule le Score de Clarté de chaque client.

    Corrige le bug V1 :
    le score était calculé une fois lors du scan
    et jamais mis à jour même quand le client
    connectait de nouveaux outils.
    """
    from orchestrator.profile import update_clarity_score

    for company in get_all_active_companies():
        company_id = company["id"]
        try:
            new_score = _compute_clarity_score(company_id)
            update_clarity_score(company_id, new_score)
            logger.info(
                f"[clarity] {company['name']} — Score: {new_score}/100"
            )
        except Exception as e:
            logger.error(
                f"[clarity] {company['name']} : {e}"
            )


def _compute_clarity_score(company_id: str) -> int:
    """
    Calcule le Score de Clarté depuis les données Supabase.
    Copie de la logique dans api/routes/scan.py,
    centralisée ici pour être utilisée par le scheduler.

    On évite l'import circulaire en dupliquant la logique.
    Si la logique évolue, elle évolue dans scan.py
    et on met à jour ici.
    """
    from services.database import get_client, get

    try:
        client = get_client()
        score_a = 0
        score_b = 0

        # ── A. Lisibilité machine (50 pts) ──
        deals = get("deals", company_id)
        if deals:
            with_amount = sum(
                1 for d in deals
                if d.get("amount") and float(d.get("amount")) > 0
            )
            with_activity = sum(1 for d in deals if d.get("last_activity_at"))
            with_source   = sum(1 for d in deals if d.get("source"))
            n = len(deals)
            score_a += int((with_amount / n) * 10)
            score_a += int((with_activity / n) * 10)
            score_a += int((with_source / n) * 10)

        invoices = get("invoices", company_id)
        if invoices:
            with_dates = sum(
                1 for i in invoices
                if i.get("due_at") and i.get("issued_at")
            )
            score_a += int((with_dates / len(invoices)) * 10)

        tasks = get("tasks", company_id)
        if tasks:
            well_defined = sum(
                1 for t in tasks
                if t.get("assignee_id") and t.get("due_at")
            )
            score_a += int((well_defined / len(tasks)) * 10)

        # ── B. Compatibilité structurelle (50 pts) ──
        company_result = client.table("companies").select(
            "tools_connected"
        ).eq("id", company_id).limit(1).execute()

        tools = {}
        if company_result.data:
            tools = company_result.data[0].get("tools_connected") or {}

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
        logger.error(f"_compute_clarity_score {company_id} : {e}")
        return 0


def run_sync_connectors() -> None:
    """
    3h00 quotidien.
    Sync de tous les connecteurs → Supabase.
    Utilise la factory centralisée.
    """
    for company in get_all_active_companies():
        company_id = company["id"]
        try:
            _sync_company(company_id, company["name"])
        except Exception as e:
            logger.error(f"[sync] {company['name']} : {e}")


def _sync_company(company_id: str, company_name: str) -> None:
    """
    Synchronise tous les connecteurs d'un client.
    Utilise get_connector_from_db() — factory centralisée.
    """
    from connectors import get_connector_from_db, get_tool_category
    from orchestrator.profile import get_company_profile
    from services.database import save_many

    profile = get_company_profile(company_id)
    tools   = profile.get("tools_connected", {})
    total_synced = 0

    # Pour chaque catégorie d'outil configuré
    for category, tool_cfg in tools.items():
        tool_name = tool_cfg.get("name", "")
        if not tool_name or not tool_cfg.get("connected", False):
            continue

        connector = get_connector_from_db(tool_name, company_id)
        if not connector:
            continue

        if not connector.connect():
            logger.warning(
                f"[sync] {company_name} — "
                f"Connexion échouée pour {tool_name}"
            )
            continue

        # Fetch selon la catégorie
        if category == "crm":
            deals = connector.fetch_deals()
            contacts = connector.fetch_contacts()
            if deals:
                save_many("deals", deals)
                total_synced += len(deals)
            if contacts:
                save_many("contacts", contacts)
                total_synced += len(contacts)

        elif category in ("finance", "payments"):
            invoices = connector.fetch_invoices()
            expenses = connector.fetch_expenses()
            if invoices:
                save_many("invoices", invoices)
                total_synced += len(invoices)
            if expenses:
                save_many("expenses", expenses)
                total_synced += len(expenses)

        elif category == "project":
            tasks = connector.fetch_tasks()
            if tasks:
                save_many("tasks", tasks)
                total_synced += len(tasks)

    logger.info(
        f"[sync] {company_name} — {total_synced} objets synchronisés"
    )


# ─────────────────────────────────────────
# LISTENERS
# ─────────────────────────────────────────

def _on_job_executed(event) -> None:
    if event.exception:
        logger.error(f"[scheduler] Job {event.job_id} — exception levée")


# ─────────────────────────────────────────
# BUILD SCHEDULER
# ─────────────────────────────────────────

def build_scheduler() -> BlockingScheduler:
    timezone = os.environ.get("SCHEDULER_TIMEZONE", "Europe/Paris")
    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_listener(
        _on_job_executed,
        EVENT_JOB_ERROR | EVENT_JOB_EXECUTED
    )

    # 3h00 — Sync connecteurs
    scheduler.add_job(
        run_sync_connectors,
        trigger=CronTrigger(hour=3, minute=0),
        id="sync_connectors",
        name="Sync — Connecteurs",
        max_instances=1,
        coalesce=True
    )

    # 5h00 — Cash Predictability
    scheduler.add_job(
        run_cash_predictability,
        trigger=CronTrigger(hour=5, minute=0),
        id="cash_predictability",
        name="Agent — Cash Predictability",
        max_instances=1,
        coalesce=True
    )

    # 6h00 — Revenue Velocity
    scheduler.add_job(
        run_revenue_velocity,
        trigger=CronTrigger(hour=6, minute=0),
        id="revenue_velocity",
        name="Agent — Revenue Velocity",
        max_instances=1,
        coalesce=True
    )

    # 6h15 — Router
    scheduler.add_job(
        run_router,
        trigger=CronTrigger(hour=6, minute=15),
        id="router",
        name="Orchestrateur — Router",
        max_instances=1,
        coalesce=True
    )

    # 6h30 lundi — Weekly Report
    scheduler.add_job(
        run_weekly_report,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=30),
        id="weekly_report",
        name="Orchestrateur — Weekly Report",
        max_instances=1,
        coalesce=True
    )

    # 9h00 — Process Clarity
    scheduler.add_job(
        run_process_clarity,
        trigger=CronTrigger(hour=9, minute=0),
        id="process_clarity",
        name="Agent — Process Clarity",
        max_instances=1,
        coalesce=True
    )

    # 1er du mois 7h00 — Acquisition Efficiency
    scheduler.add_job(
        run_acquisition_efficiency,
        trigger=CronTrigger(day=1, hour=7, minute=0),
        id="acquisition_efficiency",
        name="Agent — Acquisition Efficiency",
        max_instances=1,
        coalesce=True
    )

    # Dimanche 23h00 — Recalcul Score de Clarté
    # NOUVEAU — corrige le bug du score figé
    scheduler.add_job(
        run_update_clarity_scores,
        trigger=CronTrigger(day_of_week="sun", hour=23, minute=0),
        id="update_clarity_scores",
        name="Maintenance — Score de Clarté",
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

    logger.info("Jobs configurés :")
    for job in scheduler.get_jobs():
        logger.info(f"  → {job.name}")

    logger.info("En attente...")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler arrêté proprement.")


if __name__ == "__main__":
    main()
