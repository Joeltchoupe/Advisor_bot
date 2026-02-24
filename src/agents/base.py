# agents/base.py

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from dataclasses import dataclass, field

from services.database import get, get_client
from services.executor import executor, Action, ActionLevel
from services.notification import alert_ceo, notify_commercial

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# RÉSULTAT D'UN RUN D'AGENT
# ─────────────────────────────────────────

@dataclass
class AgentRunResult:
    agent: str
    company_id: str
    started_at: datetime
    finished_at: datetime
    actions_taken: list[dict] = field(default_factory=list)
    kpi_value: float = 0.0
    kpi_name: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


# ─────────────────────────────────────────
# AGENT DE BASE
# ─────────────────────────────────────────

class BaseAgent(ABC):
    """
    Tout agent hérite de cette classe.

    Elle fournit :
    → Le cycle run() standard
    → L'accès aux données via _get_data()
    → La publication d'events via _publish()
    → Le logging du résultat
    """

    def __init__(self, company_id: str, config: dict):
        """
        company_id : le client pour lequel tourne l'agent
        config     : la configuration spécifique à ce client
                     (seuils, fréquences, channels de notification)
                     vient du profil client dans Supabase
        """
        self.company_id = company_id
        self.config = config
        self.name = self._get_name()
        self._run_result: AgentRunResult = None

    @abstractmethod
    def _get_name(self) -> str:
        """Nom de l'agent. Ex: 'revenue_velocity'"""
        pass

    @abstractmethod
    def _run(self) -> AgentRunResult:
        """
        La logique de l'agent.
        Appelée par run().
        Doit retourner un AgentRunResult.
        """
        pass

    def run(self) -> AgentRunResult:
        """
        Point d'entrée public.
        Encapsule _run() avec logging et gestion d'erreur globale.
        """
        started_at = datetime.utcnow()
        logger.info(f"[{self.name}] Démarrage pour company {self.company_id}")

        try:
            result = self._run()
        except Exception as e:
            logger.error(f"[{self.name}] Erreur critique : {e}")
            result = AgentRunResult(
                agent=self.name,
                company_id=self.company_id,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                errors=[str(e)]
            )

        # Log du run dans Supabase
        self._log_run(result)

        logger.info(
            f"[{self.name}] Terminé en {result.duration_seconds:.1f}s — "
            f"KPI: {result.kpi_value} {result.kpi_name} — "
            f"{len(result.actions_taken)} actions"
        )

        return result

    # ─────────────────────────────────────────
    # ACCÈS AUX DONNÉES
    # ─────────────────────────────────────────

    def _get_deals(self, filters: dict = None) -> list[dict]:
        return get("deals", self.company_id, filters)

    def _get_invoices(self, filters: dict = None) -> list[dict]:
        return get("invoices", self.company_id, filters)

    def _get_tasks(self, filters: dict = None) -> list[dict]:
        return get("tasks", self.company_id, filters)

    def _get_expenses(self, filters: dict = None) -> list[dict]:
        return get("expenses", self.company_id, filters)

    def _get_contacts(self, filters: dict = None) -> list[dict]:
        return get("contacts", self.company_id, filters)

    def _get_company(self) -> dict:
        client = get_client()
        result = client.table("companies").select("*").eq(
            "id", self.company_id
        ).limit(1).execute()
        return result.data[0] if result.data else {}

    def _get_action_logs(self, agent: str = None, limit: int = 100) -> list[dict]:
        """Historique des actions pour évaluation et apprentissage."""
        client = get_client()
        query = client.table("action_logs").select("*").eq(
            "company_id", self.company_id
        ).order("executed_at", desc=True).limit(limit)

        if agent:
            query = query.eq("agent", agent)

        result = query.execute()
        return result.data or []

    # ─────────────────────────────────────────
    # PUBLICATION D'EVENTS
    # ─────────────────────────────────────────

    def _publish(self, event_type: str, payload: dict) -> None:
        """
        Publie un event que le router peut distribuer aux autres agents.
        """
        from services.database import publish_event
        publish_event(
            event_type=event_type,
            company_id=self.company_id,
            payload=payload
        )

    # ─────────────────────────────────────────
    # CONFIG HELPERS
    # ─────────────────────────────────────────

    def _cfg(self, key: str, default=None):
        """Raccourci pour lire la config de l'agent."""
        return self.config.get(key, default)

    # ─────────────────────────────────────────
    # LOGGING
    # ─────────────────────────────────────────

    def _log_run(self, result: AgentRunResult) -> None:
        try:
            client = get_client()
            client.table("agent_runs").insert({
                "agent": result.agent,
                "company_id": result.company_id,
                "started_at": result.started_at.isoformat(),
                "finished_at": result.finished_at.isoformat(),
                "duration_seconds": result.duration_seconds,
                "kpi_name": result.kpi_name,
                "kpi_value": result.kpi_value,
                "actions_count": len(result.actions_taken),
                "errors": result.errors,
                "success": result.success
            }).execute()
        except Exception as e:
            logger.error(f"Erreur log run : {e}")
