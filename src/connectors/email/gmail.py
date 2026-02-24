# connectors/email/gmail.py

import requests
from datetime import datetime, timezone
from typing import Optional
from connectors.base import BaseConnector
import logging
import base64

logger = logging.getLogger(__name__)

GMAIL_BASE_URL = "https://gmail.googleapis.com/gmail/v1"


class GmailConnector(BaseConnector):
    """
    Gmail : on ne lit pas le contenu des emails.
    On lit les MÉTADONNÉES pour calculer les temps de réponse.

    Ce que ça donne aux agents :
    - Temps de réponse moyen (interne et externe)
    - Volume d'activité email par personne
    - Threads les plus longs (friction potentielle)

    Credentials :
    {
        "access_token": str
    }
    """

    def _get_source_name(self) -> str:
        return "gmail"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}"
        }

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{GMAIL_BASE_URL}/users/me/profile",
                headers=self._get_headers(),
                timeout=10
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"Gmail connexion : {e}")
            return False

    def fetch_email_metrics(self) -> dict:
        """
        Retourne les métriques email agrégées.
        Pas de modèle Kuria pour les emails — on retourne
        directement un dict de métriques.

        Pourquoi pas un modèle ? Les emails ne sont pas des
        entités métier qu'on stocke individuellement.
        Ce sont des signaux agrégés.
        """
        threads = self._fetch_recent_threads(max_results=100)

        if not threads:
            return {
                "total_threads": 0,
                "avg_response_time_hours": None,
                "threads_without_reply": 0,
                "after_hours_activity_pct": 0
            }

        response_times = []
        threads_without_reply = 0
        after_hours_count = 0
        total_messages = 0

        for thread_data in threads:
            messages = thread_data.get("messages", [])
            if len(messages) < 2:
                threads_without_reply += 1
                continue

            # Calculer le temps de réponse :
            # Date du 1er message → date du 2ème message
            first_ts = self._get_message_timestamp(messages[0])
            second_ts = self._get_message_timestamp(messages[1])

            if first_ts and second_ts and second_ts > first_ts:
                delta_hours = (second_ts - first_ts).total_seconds() / 3600
                response_times.append(delta_hours)

            # Activité after-hours (après 20h ou avant 7h)
            for msg in messages:
                ts = self._get_message_timestamp(msg)
                if ts:
                    total_messages += 1
                    hour = ts.hour
                    if hour >= 20 or hour < 7:
                        after_hours_count += 1

        avg_response = (
            sum(response_times) / len(response_times)
            if response_times else None
        )

        after_hours_pct = (
            after_hours_count / total_messages
            if total_messages > 0 else 0
        )

        return {
            "total_threads_analyzed": len(threads),
            "avg_response_time_hours": round(avg_response, 1) if avg_response else None,
            "threads_without_reply": threads_without_reply,
            "after_hours_activity_pct": round(after_hours_pct, 2)
        }

    def _fetch_recent_threads(self, max_results: int = 100) -> list[dict]:
        """
        Récupère les threads récents avec leurs messages.
        On prend seulement les métadonnées (pas le body).
        """
        try:
            # 1. Lister les threads
            response = requests.get(
                f"{GMAIL_BASE_URL}/users/me/threads",
                headers=self._get_headers(),
                params={
                    "maxResults": max_results,
                    "q": "newer_than:30d"    # 30 derniers jours
                },
                timeout=30
            )
            response.raise_for_status()
            thread_list = response.json().get("threads", [])

        except requests.RequestException as e:
            logger.error(f"Gmail fetch_threads liste : {e}")
            return []

        # 2. Pour chaque thread, récupérer les métadonnées
        threads_with_messages = []
        for thread_info in thread_list[:50]:  # Limiter à 50 pour la V1
            thread_id = thread_info.get("id")
            if not thread_id:
                continue

            try:
                response = requests.get(
                    f"{GMAIL_BASE_URL}/users/me/threads/{thread_id}",
                    headers=self._get_headers(),
                    params={"format": "METADATA",
                            "metadataHeaders": ["Date", "From", "To"]},
                    timeout=10
                )
                response.raise_for_status()
                threads_with_messages.append(response.json())

            except requests.RequestException:
                continue

        return threads_with_messages

    def _get_message_timestamp(self, message: dict) -> Optional[datetime]:
        """
        Extrait le timestamp d'un message Gmail.
        Gmail retourne internalDate en millisecondes.
        """
        ts = message.get("internalDate")
        if ts:
            try:
                return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
            except (ValueError, TypeError):
                return None
        return None
