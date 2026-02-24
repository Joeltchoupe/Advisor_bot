# connectors/project/notion.py

import requests
from datetime import datetime
from typing import Optional
from models import Task, TaskStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionConnector(BaseConnector):
    """
    Notion : on lit les databases comme des listes de tâches.

    Credentials :
    {
        "access_token": str,
        "database_id": str    # l'ID de la database tâches principale
    }

    Important : Notion nécessite que l'intégration soit
    invitée sur la database spécifique côté client.
    """

    def _get_source_name(self) -> str:
        return "notion"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json"
        }

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{NOTION_BASE_URL}/users/me",
                headers=self._get_headers(),
                timeout=10
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"Notion connexion : {e}")
            return False

    def fetch_tasks(self) -> list[Task]:
        database_id = self.credentials.get("database_id", "")
        if not database_id:
            logger.error("Notion : database_id manquant dans les credentials")
            return []

        raw_pages = self._fetch_database_pages(database_id)
        tasks = []
        for raw in raw_pages:
            task = self._normalize_page_as_task(raw)
            if task:
                tasks.append(task)

        logger.info(f"Notion : {len(tasks)} tâches")
        return tasks

    def _fetch_database_pages(self, database_id: str) -> list[dict]:
        all_pages = []
        has_more = True
        start_cursor = None

        while has_more:
            try:
                body = {"page_size": 100}
                if start_cursor:
                    body["start_cursor"] = start_cursor

                response = requests.post(
                    f"{NOTION_BASE_URL}/databases/{database_id}/query",
                    headers=self._get_headers(),
                    json=body,
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                all_pages.extend(data.get("results", []))
                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")

            except requests.RequestException as e:
                logger.error(f"Notion fetch_database : {e}")
                break

        return all_pages

    def _normalize_page_as_task(self, raw: dict) -> Optional[Task]:
        """
        Notion est flexible : chaque database a ses propres propriétés.
        On essaie de détecter les propriétés communes :
        - Nom/Title → title
        - Status/Statut → status
        - Assigné/Assigned → assignee
        - Date limite/Due → due_at
        """
        try:
            raw_id = raw.get("id", "")
            if not raw_id:
                return None

            props = raw.get("properties", {})

            # Titre : toujours une propriété "title" dans Notion
            title = self._extract_notion_title(props)
            if not title:
                return None

            # Status
            status = self._extract_notion_status(props)

            # Due date
            due_at = self._extract_notion_date(props, [
                "Date limite", "Due date", "Due", "Deadline",
                "Échéance", "echeance", "due_at"
            ])

            # Assignee
            assignee_name = self._extract_notion_assignee(props)

            # Dates Notion (metadata)
            created_at = self._parse_notion_date(raw.get("created_time"))
            last_edited = self._parse_notion_date(raw.get("last_edited_time"))

            # Cycle time si terminé
            completed_at = last_edited if status == TaskStatus.DONE else None
            cycle_time = None
            if created_at and completed_at:
                cycle_time = (completed_at - created_at).total_seconds() / 86400

            return Task(
                id=f"notion_{raw_id}",
                company_id=self.company_id,
                title=title,
                assignee_name=assignee_name,
                status=status,
                created_at=created_at or datetime.utcnow(),
                due_at=due_at,
                completed_at=completed_at,
                cycle_time_days=cycle_time,
                connector_source="notion",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Notion normalize_page {raw.get('id')} : {e}")
            return None

    def _extract_notion_title(self, props: dict) -> str:
        """Le titre dans Notion est toujours une propriété de type 'title'."""
        for key, val in props.items():
            if val.get("type") == "title":
                rich_text = val.get("title", [])
                if rich_text:
                    return rich_text[0].get("plain_text", "")
        return ""

    def _extract_notion_status(self, props: dict) -> TaskStatus:
        """
        Cherche une propriété de type 'status' ou 'select'
        avec des valeurs communes.
        """
        done_values = {"done", "completed", "terminé", "termine", "fini"}
        in_progress_values = {"in progress", "en cours", "doing", "wip"}

        for key, val in props.items():
            prop_type = val.get("type")
            if prop_type == "status":
                status_obj = val.get("status") or {}
                name = status_obj.get("name", "").lower()
            elif prop_type == "select":
                select_obj = val.get("select") or {}
                name = select_obj.get("name", "").lower()
            else:
                continue

            if name in done_values:
                return TaskStatus.DONE
            if name in in_progress_values:
                return TaskStatus.IN_PROGRESS

        return TaskStatus.TODO

    def _extract_notion_date(self, props: dict, keys: list) -> Optional[datetime]:
        """Cherche une date parmi plusieurs noms de propriétés possibles."""
        for key in keys:
            if key in props:
                date_prop = props[key]
                if date_prop.get("type") == "date":
                    date_obj = date_prop.get("date") or {}
                    return self._parse_notion_date(date_obj.get("start"))
        return None

    def _extract_notion_assignee(self, props: dict) -> str:
        """Cherche une propriété de type 'people'."""
        for key, val in props.items():
            if val.get("type") == "people":
                people = val.get("people", [])
                if people:
                    return people[0].get("name", "")
        return ""

    def _parse_notion_date(self, value) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
