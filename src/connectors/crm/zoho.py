# connectors/crm/zoho.py

import requests
from datetime import datetime, timezone, timedelta
from typing import Optional
from models import Deal, Contact, DealStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

ZOHO_TOKEN_URL              = "https://accounts.zoho.com/oauth/v2/token"
ZOHO_TOKEN_LIFETIME_SECONDS = 3600  # 1 heure


class ZohoConnector(BaseConnector):

    def _get_source_name(self) -> str:
        return "zoho"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Zoho-oauthtoken {self.credentials['access_token']}"
        }

    def _base_url(self) -> str:
        domain = self.credentials.get(
            "api_domain", "https://www.zohoapis.com"
        )
        return f"{domain}/crm/v2"

    # ─────────────────────────────────────────
    # TOKEN REFRESH
    # ─────────────────────────────────────────

    def refresh_access_token(self) -> bool:
        """
        Zoho OAuth2 refresh.

        Credentials nécessaires :
        {
            "access_token":  str,
            "refresh_token": str,
            "client_id":     str,
            "client_secret": str,
            "api_domain":    str
        }
        """
        refresh_token = self.credentials.get("refresh_token")
        client_id     = self.credentials.get("client_id")
        client_secret = self.credentials.get("client_secret")

        if not all([refresh_token, client_id, client_secret]):
            logger.warning(
                "[zoho] Refresh impossible : credentials manquants"
            )
            return True

        try:
            response = requests.post(
                ZOHO_TOKEN_URL,
                params={
                    "grant_type":    "refresh_token",
                    "client_id":     client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
                timeout=15
            )
            response.raise_for_status()
            data = response.json()

            if "access_token" not in data:
                logger.error(f"[zoho] Refresh échoué : {data}")
                return False

            expires_at = (
                datetime.now(tz=timezone.utc) +
                timedelta(seconds=ZOHO_TOKEN_LIFETIME_SECONDS)
            ).isoformat()

            new_credentials = {
                **self.credentials,
                "access_token": data["access_token"],
                "expires_at":   expires_at
            }

            self._save_refreshed_credentials(new_credentials)
            return True

        except requests.RequestException as e:
            logger.error(f"[zoho] Erreur refresh token : {e}")
            return False

    # ─────────────────────────────────────────
    # CONNEXION
    # ─────────────────────────────────────────

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{self._base_url()}/org",
                headers=self._get_headers(),
                timeout=10
            )
            if response.status_code == 200:
                return True

            if response.status_code == 401:
                if self.refresh_access_token():
                    response2 = requests.get(
                        f"{self._base_url()}/org",
                        headers=self._get_headers(),
                        timeout=10
                    )
                    return response2.status_code == 200

            return False

        except requests.RequestException as e:
            logger.error(f"Zoho connexion : {e}")
            return False

    # ─────────────────────────────────────────
    # FETCH DEALS
    # ─────────────────────────────────────────

    def fetch_deals(self) -> list[Deal]:
        if not self.ensure_valid_token():
            return []

        all_deals = []
        page = 1
        fields = ",".join([
            "Deal_Name", "Amount", "Currency", "Stage",
            "Probability", "Closing_Date", "Created_Time",
            "Modified_Time", "Last_Activity_Time", "Owner",
            "Lead_Source",
        ])

        while True:
            try:
                response = requests.get(
                    f"{self._base_url()}/Deals",
                    headers=self._get_headers(),
                    params={"fields": fields, "page": page, "per_page": 200},
                    timeout=30
                )

                if response.status_code == 204:
                    break

                if response.status_code == 401:
                    if self.refresh_access_token():
                        response = requests.get(
                            f"{self._base_url()}/Deals",
                            headers=self._get_headers(),
                            params={
                                "fields": fields,
                                "page": page,
                                "per_page": 200
                            },
                            timeout=30
                        )
                    else:
                        break

                response.raise_for_status()
                data = response.json()

                for raw in data.get("data", []):
                    deal = self._normalize_deal(raw)
                    if deal:
                        all_deals.append(deal)

                if not data.get("info", {}).get("more_records"):
                    break
                page += 1

            except requests.RequestException as e:
                logger.error(f"Zoho fetch_deals : {e}")
                break

        logger.info(f"Zoho : {len(all_deals)} deals")
        return all_deals

    def _normalize_deal(self, raw: dict) -> Optional[Deal]:
        try:
            raw_id = str(raw.get("id", ""))
            if not raw_id:
                return None

            stage       = self._safe_str(raw.get("Stage"))
            stage_lower = stage.lower()

            if "closed won" in stage_lower or "won" in stage_lower:
                status = DealStatus.WON
            elif "closed lost" in stage_lower or "lost" in stage_lower:
                status = DealStatus.LOST
            else:
                status = DealStatus.ACTIVE

            owner = raw.get("Owner") or {}

            return Deal(
                id=f"zoho_{raw_id}",
                company_id=self.company_id,
                title=self._safe_str(raw.get("Deal_Name"), "Sans titre"),
                amount=self._safe_float(raw.get("Amount")),
                currency=self._safe_str(raw.get("Currency"), "EUR"),
                stage=stage,
                probability=self._safe_float(raw.get("Probability", 0)) / 100,
                status=status,
                created_at=self._parse_datetime(
                    raw.get("Created_Time")
                ) or datetime.utcnow(),
                last_activity_at=self._parse_datetime(
                    raw.get("Last_Activity_Time")
                ),
                closed_at=self._parse_datetime(raw.get("Closing_Date")),
                expected_close_date=self._parse_datetime(
                    raw.get("Closing_Date")
                ),
                owner_id=str(
                    owner.get("id", "")
                ) if isinstance(owner, dict) else "",
                owner_name=owner.get(
                    "name", ""
                ) if isinstance(owner, dict) else "",
                source=self._safe_str(raw.get("Lead_Source")),
                connector_source="zoho",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Zoho normalize_deal {raw.get('id')} : {e}")
            return None

    def fetch_contacts(self) -> list[Contact]:
        if not self.ensure_valid_token():
            return []

        all_contacts = []
        page = 1

        while True:
            try:
                response = requests.get(
                    f"{self._base_url()}/Contacts",
                    headers=self._get_headers(),
                    params={
                        "fields": "First_Name,Last_Name,Email,Account_Name,Created_Time",
                        "page":     page,
                        "per_page": 200
                    },
                    timeout=30
                )

                if response.status_code == 204:
                    break

                if response.status_code == 401:
                    if self.refresh_access_token():
                        continue
                    else:
                        break

                response.raise_for_status()
                data = response.json()

                for raw in data.get("data", []):
                    contact = self._normalize_contact(raw)
                    if contact:
                        all_contacts.append(contact)

                if not data.get("info", {}).get("more_records"):
                    break
                page += 1

            except requests.RequestException as e:
                logger.error(f"Zoho fetch_contacts : {e}")
                break

        return all_contacts

    def _normalize_contact(self, raw: dict) -> Optional[Contact]:
        try:
            raw_id = str(raw.get("id", ""))
            email  = self._safe_str(raw.get("Email"))
            if not raw_id or not email:
                return None

            return Contact(
                id=f"zoho_{raw_id}",
                company_id=self.company_id,
                email=email,
                first_name=self._safe_str(raw.get("First_Name")),
                last_name=self._safe_str(raw.get("Last_Name")),
                company_name=self._safe_str(raw.get("Account_Name")),
                created_at=self._parse_datetime(
                    raw.get("Created_Time")
                ) or datetime.utcnow(),
                connector_source="zoho",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Zoho normalize_contact : {e}")
            return None

    def update_deal(self, raw_id: str, fields: dict) -> bool:
        if not self.ensure_valid_token():
            return False
        try:
            response = requests.put(
                f"{self._base_url()}/Deals/{raw_id}",
                headers=self._get_headers(),
                json={"data": [fields]},
                timeout=10
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"Zoho update_deal : {e}")
            return False

    def add_note(self, deal_raw_id: str, note: str) -> bool:
        if not self.ensure_valid_token():
            return False
        try:
            response = requests.post(
                f"{self._base_url()}/Deals/{deal_raw_id}/Notes",
                headers=self._get_headers(),
                json={"data": [{"Note_Content": note}]},
                timeout=10
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"Zoho add_note : {e}")
            return False
