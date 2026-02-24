# connectors/email/outlook.py

import requests
from datetime import datetime, timezone
from typing import Optional
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class OutlookConnector(BaseConnector):
    """
    Microsoft Outlook via Microsoft Graph API.
    Mêmes métriques que Gmail — métadonnées uniquement.

    Credentials :
    {
        "access_token": str
    }
    """

    def _get_source_name(self) -> str:
        return "outlook"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "Content-Type": "application/json"
        }

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{GRAPH_BASE_URL}/me",
                headers=self._get_headers(),
                timeout=10
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"Outlook connexion : {e}")
            return False

    def fetch_email_metrics(self) -> dict:
        """
        Même interface que GmailConnector.fetch_email_metrics().
        Le reste du système ne fait pas la différence.
        """
        messages = self._fetch_recent_messages()

        if not messages:
            return {
                "total_threads": 0,
                "avg_response_time_hours": None,
                "threads_without_reply": 0,
                "after_hours_activity_pct": 0
            }

        # Grouper par conversationId (= thread)
        threads: dict[str, list] = {}
        for msg in messages:
            conv_id = msg.get("conversationId", msg.get("id"))
            if conv_id not in threads:
                threads[conv_id] = []
            threads[conv_id].append(msg)

        response_times = []
        threads_without_reply = 0
        after_hours_count = 0
        total_messages = len(messages)

        for conv_id, msgs in threads.items():
            # Trier par date
            msgs_sorted = sorted(msgs, key=lambda m: m.get("receivedDateTime", ""))

            if len(msgs_sorted) < 2:
                threads_without_reply += 1
            else:
                t1 = self._parse_graph_date(msgs_sorted[0].get("receivedDateTime"))
                t2 = self._parse_graph_date(msgs_sorted[1].get("receivedDateTime"))
                if t1 and t2 and t2 > t1:
                    delta_hours = (t2 - t1).total_seconds() / 3600
                    response_times.append(delta_hours)

            for msg in msgs_sorted:
                ts = self._parse_graph_date(msg.get("receivedDateTime"))
                if ts and (ts.hour >= 20 or ts.hour < 7):
                    after_hours_count += 1

        avg_response = (
            sum(response_times) / len(response_times)
            if response_times else None
        )

        return {
            "total_threads_analyzed": len(threads),
            "avg_response_time_hours": round(avg_response, 1) if avg_response else None,
            "threads_without_reply": threads_without_reply,
            "after_hours_activity_pct": round(after_hours_count / total_messages, 2)
            if total_messages > 0 else 0
        }

    def _fetch_recent_messages(self) -> list[dict]:
        all_messages = []

        try:
            response = requests.get(
                f"{GRAPH_BASE_URL}/me/messages",
                headers=self._get_headers(),
                params={
                    "$top": 100,
                    "$select": "id,conversationId,receivedDateTime,from,toRecipients",
                    "$filter": "receivedDateTime ge " + self._thirty_days_ago()
                },
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            all_messages = data.get("value", [])

        except requests.RequestException as e:
            logger.error(f"Outlook fetch_messages : {e}")

        return all_messages

    def _thirty_days_ago(self) -> str:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=30)
        return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _parse_graph_date(self, value) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
