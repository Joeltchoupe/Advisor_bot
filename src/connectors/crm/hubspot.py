# connectors/crm/hubspot.py

import requests
from datetime import datetime, timezone, timedelta
from typing import Optional
from models import Deal, Contact, DealStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

HUBSPOT_BASE_URL  = "https://api.hubapi.com"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"

# HubSpot : tokens valides 6 heures
HUBSPOT_TOKEN_LIFETIME_SECONDS = 21600


class HubSpotConnector(BaseConnector):

    def _get_source_name(self) -> str:
        return "hubspot"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "Content-Type": "application/json"
        }

    # ─────────────────────────────────────────
    # TOKEN REFRESH
    # ─────────────────────────────────────────

    def refresh_access_token(self) -> bool:
        """
        HubSpot OAuth2 token refresh.

        Credentials nécessaires :
        {
            "access_token": str,
            "refresh_token": str,
            "client_id": str,
            "client_secret": str,
            "expires_at": str (ISO)   ← optionnel
        }
        """
        refresh_token = self.credentials.get("refresh_token")
        client_id     = self.credentials.get("client_id")
        client_secret = self.credentials.get("client_secret")

        if not all([refresh_token, client_id, client_secret]):
            logger.warning(
                "[hubspot] Refresh impossible : "
                "refresh_token, client_id ou client_secret manquant"
            )
            return True  # Pas de refresh disponible → on tente avec le token actuel

        try:
            response = requests.post(
                HUBSPOT_TOKEN_URL,
                data={
                    "grant_type":    "refresh_token",
                    "client_id":     client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15
            )
            response.raise_for_status()
            data = response.json()

            # Calculer la date d'expiration
            expires_in = data.get(
                "expires_in", HUBSPOT_TOKEN_LIFETIME_SECONDS
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
            logger.error(f"[hubspot] Erreur refresh token : {e}")
            return False

    # ─────────────────────────────────────────
    # CONNEXION
    # ─────────────────────────────────────────

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{HUBSPOT_BASE_URL}/account-info/v3/details",
                headers=self._get_headers(),
                timeout=10
            )
            if response.status_code == 200:
                logger.info(
                    f"HubSpot connecté pour company {self.company_id}"
                )
                return True

            # 401 → token expiré → essayer de rafraîchir
            if response.status_code == 401:
                logger.warning("[hubspot] 401 sur connect() — tentative de refresh")
                if self.refresh_access_token():
                    # Réessayer avec le nouveau token
                    response2 = requests.get(
                        f"{HUBSPOT_BASE_URL}/account-info/v3/details",
                        headers=self._get_headers(),
                        timeout=10
                    )
                    return response2.status_code == 200

            logger.error(f"HubSpot connexion échouée : {response.status_code}")
            return False

        except requests.RequestException as e:
            logger.error(f"HubSpot connexion exception : {e}")
            return False

    # ─────────────────────────────────────────
    # FETCH DEALS
    # ─────────────────────────────────────────

    def fetch_deals(self) -> list[Deal]:
        # ensure_valid_token est appelé dans la classe parente
        if not self.ensure_valid_token():
            return []

        raw_deals = self._fetch_all_deals()
        deals = []

        for raw in raw_deals:
            deal = self._normalize_deal(raw)
            if deal:
                deals.append(deal)

        logger.info(
            f"HubSpot : {len(deals)} deals récupérés "
            f"pour {self.company_id}"
        )
        return deals

    def _fetch_all_deals(self) -> list[dict]:
        all_deals = []
        after = None

        properties = [
            "dealname", "amount", "dealstage", "pipeline",
            "closedate", "createdate", "hs_lastmodifieddate",
            "hs_activity_timestamp", "hubspot_owner_id",
            "hs_deal_stage_probability", "hs_is_closed_won",
            "hs_is_closed", "lead_source",
        ]

        while True:
            params = {
                "limit": 100,
                "properties": ",".join(properties),
                "associations": "contacts"
            }
            if after:
                params["after"] = after

            try:
                response = requests.get(
                    f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals",
                    headers=self._get_headers(),
                    params=params,
                    timeout=30
                )

                # Gestion du 401 en cours de pagination
                if response.status_code == 401:
                    logger.warning(
                        "[hubspot] 401 pendant fetch_deals — refresh et retry"
                    )
                    if self.refresh_access_token():
                        response = requests.get(
                            f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals",
                            headers=self._get_headers(),
                            params=params,
                            timeout=30
                        )
                    else:
                        break

                response.raise_for_status()
                data = response.json()

                all_deals.extend(data.get("results", []))

                paging = data.get("paging", {})
                after  = paging.get("next", {}).get("after")

                if not after:
                    break

            except requests.RequestException as e:
                logger.error(f"HubSpot fetch_deals erreur : {e}")
                break

        return all_deals

    def _normalize_deal(self, raw: dict) -> Optional[Deal]:
        try:
            props  = raw.get("properties", {})
            raw_id = raw.get("id", "")

            if not raw_id:
                return None

            is_won    = props.get("hs_is_closed_won", "false") == "true"
            is_closed = props.get("hs_is_closed", "false") == "true"

            if is_won:
                status = DealStatus.WON
            elif is_closed and not is_won:
                status = DealStatus.LOST
            else:
                status = DealStatus.ACTIVE

            last_activity = self._parse_datetime(
                props.get("hs_activity_timestamp") or
                props.get("hs_lastmodifieddate")
            )

            return Deal(
                id=f"hubspot_{raw_id}",
                company_id=self.company_id,
                title=self._safe_str(props.get("dealname"), "Sans titre"),
                amount=self._safe_float(props.get("amount")),
                stage=self._safe_str(props.get("dealstage")),
                stage_order=0,
                probability=self._safe_float(
                    props.get("hs_deal_stage_probability")
                ),
                status=status,
                created_at=self._parse_datetime(
                    props.get("createdate")
                ) or datetime.utcnow(),
                last_activity_at=last_activity,
                closed_at=self._parse_datetime(props.get("closedate")),
                expected_close_date=self._parse_datetime(props.get("closedate")),
                owner_id=self._safe_str(props.get("hubspot_owner_id")),
                source=self._safe_str(props.get("lead_source")),
                connector_source="hubspot",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(
                f"HubSpot normalize_deal erreur sur {raw.get('id')}: {e}"
            )
            return None

    # ─────────────────────────────────────────
    # FETCH CONTACTS
    # ─────────────────────────────────────────

    def fetch_contacts(self) -> list[Contact]:
        if not self.ensure_valid_token():
            return []

        raw_contacts = self._fetch_all_contacts()
        contacts = []

        for raw in raw_contacts:
            contact = self._normalize_contact(raw)
            if contact:
                contacts.append(contact)

        logger.info(f"HubSpot : {len(contacts)} contacts récupérés")
        return contacts

    def _fetch_all_contacts(self) -> list[dict]:
        all_contacts = []
        after = None

        properties = [
            "firstname", "lastname", "email", "company",
            "num_employees", "hs_lead_status",
            "hs_analytics_source", "createdate",
            "notes_last_activity",
        ]

        while True:
            params = {
                "limit": 100,
                "properties": ",".join(properties)
            }
            if after:
                params["after"] = after

            try:
                response = requests.get(
                    f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts",
                    headers=self._get_headers(),
                    params=params,
                    timeout=30
                )

                if response.status_code == 401:
                    if self.refresh_access_token():
                        response = requests.get(
                            f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts",
                            headers=self._get_headers(),
                            params=params,
                            timeout=30
                        )
                    else:
                        break

                response.raise_for_status()
                data = response.json()

                all_contacts.extend(data.get("results", []))

                paging = data.get("paging", {})
                after  = paging.get("next", {}).get("after")
                if not after:
                    break

            except requests.RequestException as e:
                logger.error(f"HubSpot fetch_contacts erreur : {e}")
                break

        return all_contacts

    def _normalize_contact(self, raw: dict) -> Optional[Contact]:
        try:
            props  = raw.get("properties", {})
            raw_id = raw.get("id", "")

            if not raw_id:
                return None

            email = self._safe_str(props.get("email"))
            if not email:
                return None

            return Contact(
                id=f"hubspot_{raw_id}",
                company_id=self.company_id,
                email=email,
                first_name=self._safe_str(props.get("firstname")),
                last_name=self._safe_str(props.get("lastname")),
                company_name=self._safe_str(props.get("company")),
                company_size=self._safe_int(
                    props.get("num_employees")
                ) or None,
                source=self._safe_str(props.get("hs_analytics_source")),
                created_at=self._parse_datetime(
                    props.get("createdate")
                ) or datetime.utcnow(),
                last_activity_at=self._parse_datetime(
                    props.get("notes_last_activity")
                ),
                connector_source="hubspot",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"HubSpot normalize_contact erreur : {e}")
            return None

    # ─────────────────────────────────────────
    # ÉCRITURE
    # ─────────────────────────────────────────

    def update_deal(self, raw_id: str, fields: dict) -> bool:
        if not self.ensure_valid_token():
            return False
        try:
            response = requests.patch(
                f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals/{raw_id}",
                headers=self._get_headers(),
                json={"properties": fields},
                timeout=10
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"HubSpot update_deal erreur : {e}")
            return False

    def add_note(self, deal_raw_id: str, note: str) -> bool:
        if not self.ensure_valid_token():
            return False
        try:
            note_response = requests.post(
                f"{HUBSPOT_BASE_URL}/crm/v3/objects/notes",
                headers=self._get_headers(),
                json={
                    "properties": {
                        "hs_note_body": note,
                        "hs_timestamp": datetime.utcnow().isoformat()
                    }
                },
                timeout=10
            )
            note_response.raise_for_status()
            note_id = note_response.json()["id"]

            assoc_response = requests.put(
                f"{HUBSPOT_BASE_URL}/crm/v3/objects/notes/{note_id}"
                f"/associations/deals/{deal_raw_id}/note_to_deal",
                headers=self._get_headers(),
                timeout=10
            )
            assoc_response.raise_for_status()
            return True

        except requests.RequestException as e:
            logger.error(f"HubSpot add_note erreur : {e}")
            return False

    def create_task(self, deal_raw_id: str, task_data: dict) -> bool:
        if not self.ensure_valid_token():
            return False
        try:
            due_ms = int(task_data["due_date"].timestamp() * 1000)

            task_response = requests.post(
                f"{HUBSPOT_BASE_URL}/crm/v3/objects/tasks",
                headers=self._get_headers(),
                json={
                    "properties": {
                        "hs_task_subject": task_data["title"],
                        "hs_task_status": "NOT_STARTED",
                        "hs_timestamp": due_ms,
                        "hubspot_owner_id": task_data.get("assigned_to", "")
                    }
                },
                timeout=10
            )
            task_response.raise_for_status()
            task_id = task_response.json()["id"]

            requests.put(
                f"{HUBSPOT_BASE_URL}/crm/v3/objects/tasks/{task_id}"
                f"/associations/deals/{deal_raw_id}/task_to_deal",
                headers=self._get_headers(),
                timeout=10
            )
            return True

        except requests.RequestException as e:
            logger.error(f"HubSpot create_task erreur : {e}")
            return False
