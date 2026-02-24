# agents/base.py

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field

from services.database import get, get_client
from services.executor import executor, Action, ActionLevel
from services.notification import alert_ceo, notify_commercial

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# RÉSULTAT D'UN RUN
# ─────────────────────────────────────────

@dataclass
class AgentRunResult:
    agent:        str
    company_id:   str
    started_at:   datetime
    finished_at:  datetime
    actions_taken: list[dict] = field(default_factory=list)
    kpi_value:    float = 0.0
    kpi_name:     str   = ""
    errors:       list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


# ─────────────────────────────────────────
# BASE AGENT
# ─────────────────────────────────────────

class BaseAgent(ABC):

    def __init__(self, company_id: str, config: dict):
        self.company_id = company_id
        self.config     = config
        self.name       = self._get_name()

    @abstractmethod
    def _get_name(self) -> str:
        pass

    @abstractmethod
    def _run(self) -> AgentRunResult:
        pass

    def run(self) -> AgentRunResult:
        started_at = datetime.utcnow()
        logger.info(
            f"[{self.name}] Démarrage pour company {self.company_id}"
        )

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

    def _get_action_logs(
        self, agent: str = None, limit: int = 100
    ) -> list[dict]:
        client = get_client()
        query  = client.table("action_logs").select("*").eq(
            "company_id", self.company_id
        ).order("executed_at", desc=True).limit(limit)

        if agent:
            query = query.eq("agent", agent)

        result = query.execute()
        return result.data or []

    # ─────────────────────────────────────────
    # CONNECTEUR — FACTORY CENTRALISÉE
    # Plus de _get_crm_connector() dans chaque agent
    # ─────────────────────────────────────────

    def _get_connector(self, category: str = "crm"):
        """
        Retourne le connecteur actif pour une catégorie donnée.

        category : "crm" | "finance" | "payments" | "email" | "project"

        Utilise la factory de connectors/__init__.py.
        Charge automatiquement les credentials depuis Supabase.
        """
        from connectors import get_connector_from_db

        company  = self._get_company()
        tools    = company.get("tools_connected") or {}
        tool_cfg = tools.get(category, {})
        tool_name = tool_cfg.get("name", "")

        if not tool_name:
            logger.debug(
                f"[{self.name}] Pas d'outil {category} configuré "
                f"pour {self.company_id}"
            )
            return None

        connector = get_connector_from_db(tool_name, self.company_id)

        if not connector:
            logger.warning(
                f"[{self.name}] Impossible d'instancier {tool_name}"
            )
            return None

        return connector

    # ─────────────────────────────────────────
    # PUBLICATION D'EVENTS
    # ─────────────────────────────────────────

    def _publish(self, event_type: str, payload: dict) -> None:
        from services.database import publish_event
        publish_event(
            event_type=event_type,
            company_id=self.company_id,
            payload=payload
        )

    # ─────────────────────────────────────────
    # CONFIG
    # ─────────────────────────────────────────

    def _cfg(self, key: str, default=None):
        return self.config.get(key, default)

    # ─────────────────────────────────────────
    # PARSE DATE — CENTRALISÉ ICI
    # N'existe plus dans chaque agent séparément
    # ─────────────────────────────────────────

    def _parse_date(self, value) -> Optional[datetime]:
        """
        Parse universelle des dates pour les agents.
        Centralisé dans base.py — une seule version dans tout le système.

        Gère :
        → datetime natif Python
        → ISO 8601 avec ou sans timezone
        → Timestamps millisecondes
        → Strings "YYYY-MM-DD"
        """
        if not value:
            return None

        if isinstance(value, datetime):
            # Normaliser vers UTC naive pour la cohérence
            if value.tzinfo is not None:
                return value.replace(tzinfo=None)
            return value

        try:
            s = str(value).strip()

            # Timestamp millisecondes (HubSpot)
            if s.isdigit() and len(s) == 13:
                return datetime.utcfromtimestamp(int(s) / 1000)

            # Timestamp secondes
            if s.isdigit() and len(s) == 10:
                return datetime.utcfromtimestamp(int(s))

            # Normaliser le Z
            s = s.replace("Z", "+00:00")

            if "T" in s:
                dt = datetime.fromisoformat(s)
                # Convertir en UTC naive
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt

            # Date seule
            if len(s) >= 10:
                return datetime.strptime(s[:10], "%Y-%m-%d")

            return None

        except (ValueError, TypeError, OSError):
            return None

    def _is_recent(self, date_str, days: int = 7) -> bool:
        """Vérifie si une date est dans les N derniers jours."""
        if not date_str:
            return False
        dt = self._parse_date(date_str)
        if not dt:
            return False
        return (datetime.utcnow() - dt).days <= days

    # ─────────────────────────────────────────
    # LOGGING
    # ─────────────────────────────────────────

    def _log_run(self, result: AgentRunResult) -> None:
        try:
            client = get_client()
            client.table("agent_runs").insert({
                "agent":           result.agent,
                "company_id":      result.company_id,
                "started_at":      result.started_at.isoformat(),
                "finished_at":     result.finished_at.isoformat(),
                "duration_seconds": result.duration_seconds,
                "kpi_name":        result.kpi_name,
                "kpi_value":       result.kpi_value,
                "actions_count":   len(result.actions_taken),
                "errors":          result.errors,
                "success":         result.success
            }).execute()
        except Exception as e:
            logger.error(f"Erreur log run : {e}")
