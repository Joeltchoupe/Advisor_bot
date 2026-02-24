# connectors/crm/pipedrive.py

import requests
from datetime import datetime
from typing import Optional
from models import Deal, Contact, DealStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

PIPEDRIVE_BASE_URL = "https://api.pipedrive.com/v1"


class PipedriveConnector(BaseConnector):

    def _get_source_name(self) -> str:
        return "pipedrive"

    def _base_params(self) -> dict:
        # Pipedrive utilise une API key en query param
        return {"api_token": self.credentials["api_token"]}

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{PIPEDRIVE_BASE_URL}/users/me",
                params=self._base_params(),
                timeout=10
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"Pipedrive connexion : {e}")
            return False

    # ─────────────────────────────────────────
    # DEALS
    # ─────────────────────────────────────────

    def fetch_deals(self) -> list[Deal]:
        raw_deals = self._fetch_all_deals()
        deals = []
        for raw in raw_deals:
            deal = self._normalize_deal(raw)
            if deal:
                deals.append(deal)
        logger.info(f"Pipedrive : {len(deals)} deals")
        return deals

    def _fetch_all_deals(self) -> list[dict]:
        all_deals = []
        start = 0
        limit = 100

        while True:
            try:
                params = {
                    **self._base_params(),
                    "start": start,
                    "limit": limit,
                    "status": "all_not_deleted"
                }
                response = requests.get(
                    f"{PIPEDRIVE_BASE_URL}/deals",
                    params=params,
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                results = data.get("data") or []
                all_deals.extend(results)

                # Pagination Pipedrive
                pagination = data.get("additional_data", {}).get("pagination", {})
                if not pagination.get("more_items_in_collection"):
                    break
                start += limit

            except requests.RequestException as e:
                logger.error(f"Pipedrive fetch_deals : {e}")
                break

        return all_deals

    def _normalize_deal(self, raw: dict) -> Optional[Deal]:
        try:
            raw_id = str(raw.get("id", ""))
            if not raw_id:
                return None

            # Statut Pipedrive : "open", "won", "lost"
            pd_status = raw.get("status", "open")
            if pd_status == "won":
                status = DealStatus.WON
            elif pd_status == "lost":
                status = DealStatus.LOST
            else:
                status = DealStatus.ACTIVE

            # Dernière activité
            last_activity = self._parse_pd_date(
                raw.get("last_activity_date") or
                raw.get("update_time")
            )

            # Owner : Pipedrive retourne un objet
            owner = raw.get("user_id") or {}
            owner_id = str(owner.get("id", "")) if isinstance(owner, dict) else ""
            owner_name = owner.get("name", "") if isinstance(owner, dict) else ""

            # Source : via le channel custom ou le champ label
            source = self._safe_str(raw.get("label"))

            return Deal(
                id=f"pipedrive_{raw_id}",
                company_id=self.company_id,
                title=self._safe_str(raw.get("title"), "Sans titre"),
                amount=self._safe_float(raw.get("value")),
                currency=self._safe_str(raw.get("currency"), "EUR"),
                stage=self._safe_str(raw.get("stage_id")),
                stage_order=self._safe_int(raw.get("stage_order_nr")),
                probability=self._safe_float(raw.get("probability", 0)) / 100,
                status=status,
                created_at=self._parse_pd_date(raw.get("add_time")) or datetime.utcnow(),
                last_activity_at=last_activity,
                closed_at=self._parse_pd_date(raw.get("close_time")),
                expected_close_date=self._parse_pd_date(raw.get("expected_close_date")),
                owner_id=owner_id,
                owner_name=owner_name,
                source=source,
                connector_source="pipedrive",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Pipedrive normalize_deal {raw.get('id')} : {e}")
            return None

    # ─────────────────────────────────────────
    # CONTACTS
    # ─────────────────────────────────────────

    def fetch_contacts(self) -> list[Contact]:
        raw_contacts = self._fetch_all_contacts()
        contacts = []
        for raw in raw_contacts:
            contact = self._normalize_contact(raw)
            if contact:
                contacts.append(contact)
        return contacts

    def _fetch_all_contacts(self) -> list[dict]:
        all_contacts = []
        start = 0

        while True:
            try:
                params = {
                    **self._base_params(),
                    "start": start,
                    "limit": 100
                }
                response = requests.get(
                    f"{PIPEDRIVE_BASE_URL}/persons",
                    params=params,
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                results = data.get("data") or []
                all_contacts.extend(results)

                pagination = data.get("additional_data", {}).get("pagination", {})
                if not pagination.get("more_items_in_collection"):
                    break
                start += 100

            except requests.RequestException as e:
                logger.error(f"Pipedrive fetch_contacts : {e}")
                break

        return all_contacts

    def _normalize_contact(self, raw: dict) -> Optional[Contact]:
        try:
            raw_id = str(raw.get("id", ""))
            if not raw_id:
                return None

            # Email : Pipedrive retourne une liste
            emails = raw.get("email", [])
            email = ""
            if isinstance(emails, list) and emails:
                email = emails[0].get("value", "")
            if not email:
                return None

            # Organisation
            org = raw.get("org_id") or {}
            company_name = org.get("name", "") if isinstance(org, dict) else ""

            return Contact(
                id=f"pipedrive_{raw_id}",
                company_id=self.company_id,
                email=email,
                first_name=self._safe_str(raw.get("first_name")),
                last_name=self._safe_str(raw.get("last_name")),
                company_name=company_name,
                created_at=self._parse_pd_date(raw.get("add_time")) or datetime.utcnow(),
                connector_source="pipedrive",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Pipedrive normalize_contact : {e}")
            return None

    # ─────────────────────────────────────────
    # ÉCRITURE
    # ─────────────────────────────────────────

    def update_deal(self, raw_id: str, fields: dict) -> bool:
        try:
            response = requests.put(
                f"{PIPEDRIVE_BASE_URL}/deals/{raw_id}",
                params=self._base_params(),
                json=fields,
                timeout=10
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"Pipedrive update_deal : {e}")
            return False

    def add_note(self, deal_raw_id: str, note: str) -> bool:
        try:
            response = requests.post(
                f"{PIPEDRIVE_BASE_URL}/notes",
                params=self._base_params(),
                json={
                    "content": note,
                    "deal_id": int(deal_raw_id)
                },
                timeout=10
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"Pipedrive add_note : {e}")
            return False

    # ─────────────────────────────────────────
    # UTILITAIRES PIPEDRIVE
    # ─────────────────────────────────────────

    def _parse_pd_date(self, value) -> Optional[datetime]:
        """
        Pipedrive retourne les dates en :
        - "2025-03-15 10:30:00" (datetime string)
        - "2025-03-15" (date string)
        """
        if not value:
            return None
        try:
            if len(str(value)) == 10:
                return datetime.strptime(str(value), "%Y-%m-%d")
            return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return None
