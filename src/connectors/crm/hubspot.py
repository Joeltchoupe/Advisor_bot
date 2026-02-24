# connectors/crm/hubspot.py

import os
import requests
from datetime import datetime, timezone
from typing import Optional
from models import Deal, Contact, DealStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

HUBSPOT_BASE_URL = "https://api.hubapi.com"


class HubSpotConnector(BaseConnector):

    def _get_source_name(self) -> str:
        return "hubspot"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "Content-Type": "application/json"
        }

    # ─────────────────────────────────────────
    # CONNEXION
    # ─────────────────────────────────────────

    def connect(self) -> bool:
        """
        Vérifie la connexion en appelant l'endpoint account info.
        Si ça répond 200, on est connecté.
        """
        try:
            response = requests.get(
                f"{HUBSPOT_BASE_URL}/account-info/v3/details",
                headers=self._get_headers(),
                timeout=10
            )
            if response.status_code == 200:
                logger.info(f"HubSpot connecté pour company {self.company_id}")
                return True
            else:
                logger.error(f"HubSpot connexion échouée : {response.status_code}")
                return False
        except requests.RequestException as e:
            logger.error(f"HubSpot connexion exception : {e}")
            return False

    # ─────────────────────────────────────────
    # FETCH DEALS
    # ─────────────────────────────────────────

    def fetch_deals(self) -> list[Deal]:
        """
        Récupère tous les deals HubSpot et les normalise.
        Gère la pagination automatiquement.
        """
        raw_deals = self._fetch_all_deals()
        deals = []

        for raw in raw_deals:
            deal = self._normalize_deal(raw)
            if deal:
                deals.append(deal)

        logger.info(f"HubSpot : {len(deals)} deals récupérés pour {self.company_id}")
        return deals

    def _fetch_all_deals(self) -> list[dict]:
        """
        Récupère tous les deals via l'API HubSpot avec pagination.
        HubSpot retourne max 100 deals par page.
        """
        all_deals = []
        after = None

        # Les propriétés qu'on veut récupérer
        properties = [
            "dealname",
            "amount",
            "dealstage",
            "pipeline",
            "closedate",
            "createdate",
            "hs_lastmodifieddate",
            "hs_activity_timestamp",   # dernière activité
            "hubspot_owner_id",
            "hs_deal_stage_probability",
            "closed_lost_reason",
            "hs_is_closed_won",
            "hs_is_closed",
            "lead_source",             # source du lead
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
                response.raise_for_status()
                data = response.json()

                all_deals.extend(data.get("results", []))

                # Pagination
                paging = data.get("paging", {})
                next_page = paging.get("next", {})
                after = next_page.get("after")

                if not after:
                    break

            except requests.RequestException as e:
                logger.error(f"HubSpot fetch_deals erreur : {e}")
                break

        return all_deals

    def _normalize_deal(self, raw: dict) -> Optional[Deal]:
        """
        Traduit un deal HubSpot brut en Deal Kuria.
        Si une donnée critique manque, retourne None.
        """
        try:
            props = raw.get("properties", {})
            raw_id = raw.get("id", "")

            if not raw_id:
                return None

            # Statut
            is_won = props.get("hs_is_closed_won", "false") == "true"
            is_closed = props.get("hs_is_closed", "false") == "true"

            if is_won:
                status = DealStatus.WON
            elif is_closed and not is_won:
                status = DealStatus.LOST
            else:
                status = DealStatus.ACTIVE

            # Dates
            last_activity = self._parse_hs_date(
                props.get("hs_activity_timestamp") or
                props.get("hs_lastmodifieddate")
            )

            return Deal(
                id=f"hubspot_{raw_id}",
                company_id=self.company_id,
                title=self._safe_str(props.get("dealname"), "Sans titre"),
                amount=self._safe_float(props.get("amount")),
                stage=self._safe_str(props.get("dealstage")),
                stage_order=0,              # enrichi après via fetch_pipeline_stages
                probability=self._safe_float(props.get("hs_deal_stage_probability")),
                status=status,
                created_at=self._parse_hs_date(props.get("createdate")) or datetime.utcnow(),
                last_activity_at=last_activity,
                closed_at=self._parse_hs_date(props.get("closedate")),
                expected_close_date=self._parse_hs_date(props.get("closedate")),
                owner_id=self._safe_str(props.get("hubspot_owner_id")),
                source=self._safe_str(props.get("lead_source")),
                connector_source="hubspot",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"HubSpot normalize_deal erreur sur {raw.get('id')}: {e}")
            return None

    # ─────────────────────────────────────────
    # FETCH CONTACTS
    # ─────────────────────────────────────────

    def fetch_contacts(self) -> list[Contact]:
        """
        Récupère tous les contacts HubSpot.
        """
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
            "firstname", "lastname", "email",
            "company", "jobtitle",
            "num_employees",               # taille de l'entreprise
            "hs_lead_status",
            "hs_analytics_source",         # source first touch
            "createdate",
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
                response.raise_for_status()
                data = response.json()

                all_contacts.extend(data.get("results", []))

                paging = data.get("paging", {})
                after = paging.get("next", {}).get("after")
                if not after:
                    break

            except requests.RequestException as e:
                logger.error(f"HubSpot fetch_contacts erreur : {e}")
                break

        return all_contacts

    def _normalize_contact(self, raw: dict) -> Optional[Contact]:
        try:
            props = raw.get("properties", {})
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
                company_size=self._safe_int(props.get("num_employees")) or None,
                source=self._safe_str(props.get("hs_analytics_source")),
                created_at=self._parse_hs_date(props.get("createdate")) or datetime.utcnow(),
                last_activity_at=self._parse_hs_date(props.get("notes_last_activity")),
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
        """
        Met à jour un deal HubSpot.
        fields : propriétés HubSpot natives
                 ex: {"dealstage": "closedwon", "axio_status": "zombie"}
        """
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
        """
        Ajoute une note à un deal via l'API engagement.
        """
        try:
            # Créer la note
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

            # Associer la note au deal
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
        """
        Crée une tâche dans HubSpot associée à un deal.
        task_data : {
            "title": str,
            "due_date": datetime,
            "assigned_to": str (owner_id HubSpot)
        }
        """
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

            # Associer au deal
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

    # ─────────────────────────────────────────
    # UTILITAIRES HUBSPOT
    # ─────────────────────────────────────────

    def _parse_hs_date(self, value) -> Optional[datetime]:
        """
        HubSpot retourne les dates en plusieurs formats :
        - ISO string : "2025-03-15T10:30:00.000Z"
        - Timestamp ms : 1742123400000
        Retourne un datetime UTC ou None.
        """
        if not value:
            return None
        try:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
