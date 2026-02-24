# connectors/project/trello.py

import requests
from datetime import datetime
from typing import Optional
from models import Task, TaskStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

TRELLO_BASE_URL = "https://api.trello.com/1"


class TrelloConnector(BaseConnector):
    """
    Trello : boards → lists → cards (= tâches).

    Credentials :
    {
        "api_key": str,
        "token": str,
        "board_id": str    # le board principal à scanner
    }
    """

    def _get_source_name(self) -> str:
        return "trello"

    def _auth_params(self) -> dict:
        return {
            "key": self.credentials["api_key"],
            "token": self.credentials["token"]
        }

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{TRELLO_BASE_URL}/members/me",
                params=self._auth_params(),
                timeout=10
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"Trello connexion : {e}")
            return False

    def fetch_tasks(self) -> list[Task]:
        board_id = self.credentials.get("board_id", "")
        if not board_id:
            logger.error("Trello : board_id manquant")
            return []

        # Récupérer les lists du board pour connaître les statuts
        lists_map = self._fetch_board_lists(board_id)
        raw_cards = self._fetch_board_cards(board_id)

        tasks = []
        for raw in raw_cards:
            task = self._normalize_card(raw, lists_map)
            if task:
                tasks.append(task)

        logger.info(f"Trello : {len(tasks)} cartes")
        return tasks

    def _fetch_board_lists(self, board_id: str) -> dict:
        """Retourne un dict {list_id: list_name}"""
        try:
            response = requests.get(
                f"{TRELLO_BASE_URL}/boards/{board_id}/lists",
                params=self._auth_params(),
                timeout=10
            )
            response.raise_for_status()
            lists = response.json()
            return {l["id"]: l["name"] for l in lists}
        except requests.RequestException as e:
            logger.error(f"Trello fetch_lists : {e}")
            return {}

    def _fetch_board_cards(self, board_id: str) -> list[dict]:
        try:
            response = requests.get(
                f"{TRELLO_BASE_URL}/boards/{board_id}/cards",
                params={
                    **self._auth_params(),
                    "fields": "name,idList,due,dueComplete,dateLastActivity,idMembers,closed",
                    "members": "true",
                    "member_fields": "fullName"
                },
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Trello fetch_cards : {e}")
            return []

    def _normalize_card(self, raw: dict, lists_map: dict) -> Optional[Task]:
        try:
            raw_id = raw.get("id", "")
            if not raw_id:
                return None

            # Statut basé sur le nom de la liste
            list_id = raw.get("idList", "")
            list_name = lists_map.get(list_id, "").lower()

            done_keywords = ["done", "terminé", "completed", "fini", "archive"]
            in_progress_keywords = ["doing", "en cours", "in progress", "wip"]

            closed = raw.get("closed", False)
            due_complete = raw.get("dueComplete", False)

            if closed or due_complete or any(k in list_name for k in done_keywords):
                status = TaskStatus.DONE
            elif any(k in list_name for k in in_progress_keywords):
                status = TaskStatus.IN_PROGRESS
            else:
                status = TaskStatus.TODO

            due_at = self._parse_trello_date(raw.get("due"))

            if status != TaskStatus.DONE and due_at and due_at < datetime.utcnow():
                status = TaskStatus.OVERDUE

            # Assignee (premier membre)
            members = raw.get("members", [])
            assignee_name = members[0].get("fullName", "") if members else ""

            # Dates
            created_at = self._extract_creation_date_from_id(raw_id)
            last_activity = self._parse_trello_date(raw.get("dateLastActivity"))
            completed_at = last_activity if status == TaskStatus.DONE else None

            cycle_time = None
            if created_at and completed_at:
                cycle_time = (completed_at - created_at).total_seconds() / 86400

            return Task(
                id=f"trello_{raw_id}",
                company_id=self.company_id,
                title=self._safe_str(raw.get("name"), "Sans titre"),
                assignee_name=assignee_name,
                status=status,
                created_at=created_at or datetime.utcnow(),
                due_at=due_at,
                completed_at=completed_at,
                cycle_time_days=cycle_time,
                connector_source="trello",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Trello normalize_card {raw.get('id')} : {e}")
            return None

    def _extract_creation_date_from_id(self, card_id: str) -> Optional[datetime]:
        """
        L'ID Trello encode la date de création dans ses 8 premiers caractères.
        C'est du base16 → timestamp Unix.
        """
        try:
            timestamp = int(card_id[:8], 16)
            return datetime.utcfromtimestamp(timestamp)
        except (ValueError, TypeError):
            return None

    def _parse_trello_date(self, value) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
