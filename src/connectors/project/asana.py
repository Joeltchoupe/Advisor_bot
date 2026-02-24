# connectors/project/asana.py

import requests
from datetime import datetime
from typing import Optional
from models import Task, TaskStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

ASANA_BASE_URL = "https://app.asana.com/api/1.0"


class AsanaConnector(BaseConnector):
    """
    Credentials :
    {
        "access_token": str,
        "workspace_id": str
    }
    """

    def _get_source_name(self) -> str:
        return "asana"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}"
        }

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{ASANA_BASE_URL}/users/me",
                headers=self._get_headers(),
                timeout=10
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"Asana connexion : {e}")
            return False

    def fetch_tasks(self) -> list[Task]:
        raw_tasks = self._fetch_all_tasks()
        tasks = []
        for raw in raw_tasks:
            task = self._normalize_task(raw)
            if task:
                tasks.append(task)
        logger.info(f"Asana : {len(tasks)} tâches")
        return tasks

    def _fetch_all_tasks(self) -> list[dict]:
        """
        Asana : on fetch les tâches par workspace.
        On utilise la search API pour avoir les données enrichies.
        """
        all_tasks = []
        offset = None

        fields = ",".join([
            "name", "completed", "due_on",
            "assignee", "created_at", "completed_at",
            "modified_at", "notes"
        ])

        while True:
            try:
                params = {
                    "workspace": self.credentials["workspace_id"],
                    "opt_fields": fields,
                    "limit": 100
                }
                if offset:
                    params["offset"] = offset

                response = requests.get(
                    f"{ASANA_BASE_URL}/tasks",
                    headers=self._get_headers(),
                    params=params,
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                all_tasks.extend(data.get("data", []))

                next_page = data.get("next_page")
                if not next_page:
                    break
                offset = next_page.get("offset")

            except requests.RequestException as e:
                logger.error(f"Asana fetch_tasks : {e}")
                break

        return all_tasks

    def _normalize_task(self, raw: dict) -> Optional[Task]:
        try:
            raw_id = str(raw.get("gid", ""))
            if not raw_id:
                return None

            # Statut Asana
            completed = raw.get("completed", False)
            due_on = self._parse_asana_date(raw.get("due_on"))

            if completed:
                status = TaskStatus.DONE
            elif due_on and due_on < datetime.utcnow():
                status = TaskStatus.OVERDUE
            else:
                status = TaskStatus.TODO

            # Assignee
            assignee = raw.get("assignee") or {}
            assignee_id = str(assignee.get("gid", "")) if isinstance(assignee, dict) else ""
            assignee_name = assignee.get("name", "") if isinstance(assignee, dict) else ""

            # Cycle time
            created_at = self._parse_asana_datetime(raw.get("created_at"))
            completed_at = self._parse_asana_datetime(raw.get("completed_at"))
            cycle_time = None
            if created_at and completed_at:
                cycle_time = (completed_at - created_at).total_seconds() / 86400

            return Task(
                id=f"asana_{raw_id}",
                company_id=self.company_id,
                title=self._safe_str(raw.get("name"), "Sans titre"),
                assignee_id=assignee_id,
                assignee_name=assignee_name,
                status=status,
                created_at=created_at or datetime.utcnow(),
                due_at=due_on,
                completed_at=completed_at,
                cycle_time_days=cycle_time,
                connector_source="asana",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Asana normalize_task {raw.get('gid')} : {e}")
            return None

    def _parse_asana_date(self, value) -> Optional[datetime]:
        """Format Asana date : "2025-03-15" """
        if not value:
            return None
        try:
            return datetime.strptime(str(value), "%Y-%m-%d")
        except (ValueError, TypeError):
            return None

    def _parse_asana_datetime(self, value) -> Optional[datetime]:
        """Format Asana datetime : "2025-03-15T10:30:00.000Z" """
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
