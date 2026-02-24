# connectors/crm/pipedrive.py

import requests
from datetime import datetime
from typing import Optional
from models import Deal, Contact, DealStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

PIPEDRIVE_BASE_URL = "https://api.pipedrive.com/v1"

# Pipedrive OAuth : tokens valides 1 heure
PIPEDRIVE_TOKEN_LIFETIME_SECONDS = 3600
PIPEDRIVE_TOKEN_URL = "https://oauth.pipedrive.com/oauth/token"


class PipedriveConnector(BaseConnector):

    def _get_source_name(self) -> str:
        return "pipedrive"

    def _base_params(self) -> dict:
        # Pipedrive supporte API key ET OAuth
        # Si access_token présent → OAuth
        # Sinon → api_token
        if self.credentials.get("access_token"):
            return {}    # Auth via header
        return {"api_token": self.credentials.get("api_token", "")}

    def _get_headers(self) -> dict:
        if self.credentials.get("access_token"):
            return {
                "Authorization": f"Bearer {self.credentials['access_token']}"
            }
        return {}

    # ─────────────────────────────────────────
    # TOKEN REFRESH (OAuth uniquement)
    # ─────────────────────────────────────────

    def refresh_access_token(self) -> bool:
        """
        Pipedrive OAuth2 refresh.
        Uniquement si on utilise OAuth (pas api_token).
        """
        refresh_token = self.credentials.get("refresh_token")
        client_id     = self.credentials.get("client_id")
        client_secret = self.credentials.get("client_secret")

        if not all([refresh_token, client_id, client_secret]):
            # Probablement en mode api_token → pas de refresh nécessaire
            return True

        try:
            import base64
            auth = base64.b64encode(
                f"{client_id}:{client_secret}".encode()
            ).decode()

            response = requests.post(
                PIPEDRIVE_TOKEN_URL,
                data={
                    "grant_type":    "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type":  "application/x-www-form-urlencoded"
                },
                timeout=15
            )
            response.raise_for_status()
            data = response.json()

            from datetime import timezone, timedelta
            expires_in = data.get(
                "expires_in", PIPEDRIVE_TOKEN_LIFETIME_SECONDS
            )
            expires_at = (
                datetime.now(tz=timezone.utc) +
                timedelta(seconds=expires_in)
            ).isoformat()

            new_credentials = {
                **self.credentials,
                "access_token":  data["access_token"],
                "refresh_token": data.get("refresh_token", refresh_token),
                "expires_at":    expires_at
            }

            self._save_refreshed_credentials(new_credentials)
            return True

        except requests.RequestException as e:
            logger.error(f"[pipedrive] Erreur refresh token : {e}")
            return False

    # ─────────────────────────────────────────
    # CONNEXION
    # ─────────────────────────────────────────

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{PIPEDRIVE_BASE_URL}/users/me",
                params=self._base_params(),
                headers=self._get_headers(),
                timeout=10
            )
            if response.status_code == 200:
                return True

            if response.status_code == 401:
                if self.refresh_access_token():
                    response2 = requests.get(
                        f"{PIPEDRIVE_BASE_URL}/users/me",
                        params=self._base_params(),
                        headers=self._get_headers(),
                        timeout=10
                    )
                    return response2.status_code == 200

            return False

        except requests.RequestException as e:
            logger.error(f"Pipedrive connexion : {e}")
            return False

    # ─────────────────────────────────────────
    # FETCH DEALS
    # ─────────────────────────────────────────

    def fetch_deals(self) -> list[Deal]:
        if not self.ensure_valid_token():
            return []

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
                    "start":  start,
                    "limit":  limit,
                    "status": "all_not_deleted"
                }
                response = requests.get(
                    f"{PIPEDRIVE_BASE_URL}/deals",
                    params=params,
                    headers=self._get_headers(),
                    timeout=30
                )

                if response.status_code == 401:
                    if self.refresh_access_token():
                        response = requests.get(
                            f"{PIPEDRIVE_BASE_URL}/deals",
                            params=params,
                            headers=self._get_headers(),
                            timeout=30
                        )
                    else:
                        break

                response.raise_for_status()
                data = response.json()

                results = data.get("data") or []
                all_deals.extend(results)

                pagination = data.get(
                    "additional_data", {}
                ).get("pagination", {})

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

            pd_status = raw.get("status", "open")
            if pd_status == "won":
                status = DealStatus.WON
            elif pd_status == "lost":
                status = DealStatus.LOST
            else:
                status = DealStatus.ACTIVE

            last_activity = self._parse_datetime(
                raw.get("last_activity_date") or
                raw.get("update_time")
            )

            owner      = raw.get("user_id") or {}
            owner_id   = str(owner.get("id", "")) if isinstance(owner, dict) else ""
            owner_name = owner.get("name", "") if isinstance(owner, dict) else ""
            source     = self._safe_str(raw.get("label"))

            return Deal(
                id=f"pipedrive_{raw_id}",
                company_id=self.company_id,
                title=self._safe_str(raw.get("title"), "Sans titre"),
                amount=self._safe_float(raw.get("value")),
                currency=self._safe_str(raw.get("currency"), "EUR"),
                stage=self._safe_str(raw.get("stage_id")),
                stage_order=self._safe_int(raw.get("stage_order_nr")),
                probability=self._safe_float(
                    raw.get("probability", 0)
                ) / 100,
                status=status,
                created_at=self._parse_datetime(
                    raw.get("add_time")
                ) or datetime.utcnow(),
                last_activity_at=last_activity,
                closed_at=self._parse_datetime(raw.get("close_time")),
                expected_close_date=self._parse_datetime(
                    raw.get("expected_close_date")
                ),
                owner_id=owner_id,
                owner_name=owner_name,
                source=source,
                connector_source="pipedrive",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(
                f"Pipedrive normalize_deal {raw.get('id')} : {e}"
            )
            return None

    def fetch_contacts(self) -> list[Contact]:
        if not self.ensure_valid_token():
            return []

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
                    headers=self._get_headers(),
                    timeout=30
                )

                if response.status_code == 401:
                    if self.refresh_access_token():
                        response = requests.get(
                            f"{PIPEDRIVE_BASE_URL}/persons",
                            params=params,
                            headers=self._get_headers(),
                            timeout=30
                        )
                    else:
                        break

                response.raise_for_status()
                data = response.json()

                for raw in (data.get("data") or []):
                    contact = self._normalize_contact(raw)
                    if contact:
                        all_contacts.append(contact)

                pagination = data.get(
                    "additional_data", {}
                ).get("pagination", {})
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

            emails = raw.get("email", [])
            email  = ""
            if isinstance(emails, list) and emails:
                email = emails[0].get("value", "")
            if not email:
                return None

            org          = raw.get("org_id") or {}
            company_name = org.get("name", "") if isinstance(org, dict) else ""

            return Contact(
                id=f"pipedrive_{raw_id}",
                company_id=self.company_id,
                email=email,
                first_name=self._safe_str(raw.get("first_name")),
                last_name=self._safe_str(raw.get("last_name")),
                company_name=company_name,
                created_at=self._parse_datetime(
                    raw.get("add_time")
                ) or datetime.utcnow(),
                connector_source="pipedrive",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Pipedrive normalize_contact : {e}")
            return None

    def update_deal(self, raw_id: str, fields: dict) -> bool:
        if not self.ensure_valid_token():
            return False
        try:
            response = requests.put(
                f"{PIPEDRIVE_BASE_URL}/deals/{raw_id}",
                params=self._base_params(),
                headers=self._get_headers(),
                json=fields,
                timeout=10
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"Pipedrive update_deal : {e}")
            return False

    def add_note(self, deal_raw_id: str, note: str) -> bool:
        if not self.ensure_valid_token():
            return False
        try:
            response = requests.post(
                f"{PIPEDRIVE_BASE_URL}/notes",
                params=self._base_params(),
                headers=self._get_headers(),
                json={"content": note, "deal_id": int(deal_raw_id)},
                timeout=10
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"Pipedrive add_note : {e}")
            return False
