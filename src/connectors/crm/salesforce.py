# connectors/crm/salesforce.py

import requests
from datetime import datetime
from typing import Optional
from models import Deal, Contact, DealStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)


class SalesforceConnector(BaseConnector):
    """
    Salesforce utilise OAuth2 avec un instance_url dynamique.
    Credentials nécessaires :
    {
        "access_token": str,
        "instance_url": str,   # ex: https://mycompany.salesforce.com
        "api_version": str     # ex: "v59.0"
    }
    """

    def _get_source_name(self) -> str:
        return "salesforce"

    def _base_url(self) -> str:
        version = self.credentials.get("api_version", "v59.0")
        return f"{self.credentials['instance_url']}/services/data/{version}"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "Content-Type": "application/json"
        }

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{self.credentials['instance_url']}/services/data/",
                headers=self._get_headers(),
                timeout=10
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"Salesforce connexion : {e}")
            return False

    # ─────────────────────────────────────────
    # DEALS (= Opportunities dans Salesforce)
    # ─────────────────────────────────────────

    def fetch_deals(self) -> list[Deal]:
        """
        Salesforce utilise SOQL pour les requêtes.
        On requête la table Opportunity.
        """
        raw_deals = self._query_opportunities()
        deals = []
        for raw in raw_deals:
            deal = self._normalize_deal(raw)
            if deal:
                deals.append(deal)
        logger.info(f"Salesforce : {len(deals)} opportunities")
        return deals

    def _query_opportunities(self) -> list[dict]:
        """
        SOQL : Structured Query Language de Salesforce.
        On récupère tous les champs nécessaires en une requête.
        """
        soql = """
            SELECT Id, Name, Amount, CurrencyIsoCode,
                   StageName, Probability, IsClosed, IsWon,
                   CreatedDate, LastActivityDate, CloseDate,
                   OwnerId, Owner.Name, LeadSource,
                   LastModifiedDate
            FROM Opportunity
            WHERE IsDeleted = false
            ORDER BY LastModifiedDate DESC
        """

        all_records = []
        url = f"{self._base_url()}/query"
        params = {"q": soql}

        while True:
            try:
                response = requests.get(
                    url,
                    headers=self._get_headers(),
                    params=params,
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                all_records.extend(data.get("records", []))

                # Pagination Salesforce via nextRecordsUrl
                if data.get("done"):
                    break

                next_url = data.get("nextRecordsUrl")
                if not next_url:
                    break

                # Pour les pages suivantes, l'URL est différente
                url = f"{self.credentials['instance_url']}{next_url}"
                params = {}

            except requests.RequestException as e:
                logger.error(f"Salesforce query_opportunities : {e}")
                break

        return all_records

    def _normalize_deal(self, raw: dict) -> Optional[Deal]:
        try:
            raw_id = raw.get("Id", "")
            if not raw_id:
                return None

            is_won = raw.get("IsWon", False)
            is_closed = raw.get("IsClosed", False)

            if is_won:
                status = DealStatus.WON
            elif is_closed and not is_won:
                status = DealStatus.LOST
            else:
                status = DealStatus.ACTIVE

            # Owner : Salesforce retourne un sous-objet
            owner = raw.get("Owner") or {}

            return Deal(
                id=f"salesforce_{raw_id}",
                company_id=self.company_id,
                title=self._safe_str(raw.get("Name"), "Sans titre"),
                amount=self._safe_float(raw.get("Amount")),
                currency=self._safe_str(raw.get("CurrencyIsoCode"), "EUR"),
                stage=self._safe_str(raw.get("StageName")),
                probability=self._safe_float(raw.get("Probability", 0)) / 100,
                status=status,
                created_at=self._parse_sf_date(raw.get("CreatedDate")) or datetime.utcnow(),
                last_activity_at=self._parse_sf_date(raw.get("LastActivityDate")),
                closed_at=self._parse_sf_date(raw.get("CloseDate")),
                expected_close_date=self._parse_sf_date(raw.get("CloseDate")),
                owner_id=self._safe_str(raw.get("OwnerId")),
                owner_name=owner.get("Name", "") if isinstance(owner, dict) else "",
                source=self._safe_str(raw.get("LeadSource")),
                connector_source="salesforce",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Salesforce normalize_deal {raw.get('Id')} : {e}")
            return None

    # ─────────────────────────────────────────
    # CONTACTS
    # ─────────────────────────────────────────

    def fetch_contacts(self) -> list[Contact]:
        raw_contacts = self._query_contacts()
        contacts = []
        for raw in raw_contacts:
            contact = self._normalize_contact(raw)
            if contact:
                contacts.append(contact)
        return contacts

    def _query_contacts(self) -> list[dict]:
        soql = """
            SELECT Id, FirstName, LastName, Email,
                   Account.Name, Account.NumberOfEmployees,
                   Account.AnnualRevenue, Account.Industry,
                   LeadSource, CreatedDate, LastActivityDate
            FROM Contact
            WHERE IsDeleted = false
            AND Email != null
        """
        all_records = []
        url = f"{self._base_url()}/query"
        params = {"q": soql}

        while True:
            try:
                response = requests.get(
                    url,
                    headers=self._get_headers(),
                    params=params,
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()
                all_records.extend(data.get("records", []))

                if data.get("done"):
                    break
                next_url = data.get("nextRecordsUrl")
                if not next_url:
                    break
                url = f"{self.credentials['instance_url']}{next_url}"
                params = {}

            except requests.RequestException as e:
                logger.error(f"Salesforce query_contacts : {e}")
                break

        return all_records

    def _normalize_contact(self, raw: dict) -> Optional[Contact]:
        try:
            raw_id = raw.get("Id", "")
            email = self._safe_str(raw.get("Email"))
            if not raw_id or not email:
                return None

            account = raw.get("Account") or {}

            return Contact(
                id=f"salesforce_{raw_id}",
                company_id=self.company_id,
                email=email,
                first_name=self._safe_str(raw.get("FirstName")),
                last_name=self._safe_str(raw.get("LastName")),
                company_name=account.get("Name", "") if isinstance(account, dict) else "",
                company_size=self._safe_int(
                    account.get("NumberOfEmployees")) or None,
                company_revenue=self._safe_float(
                    account.get("AnnualRevenue")) or None,
                sector=self._safe_str(account.get("Industry")),
                source=self._safe_str(raw.get("LeadSource")),
                created_at=self._parse_sf_date(raw.get("CreatedDate")) or datetime.utcnow(),
                last_activity_at=self._parse_sf_date(raw.get("LastActivityDate")),
                connector_source="salesforce",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Salesforce normalize_contact : {e}")
            return None

    # ─────────────────────────────────────────
    # ÉCRITURE
    # ─────────────────────────────────────────

    def update_deal(self, raw_id: str, fields: dict) -> bool:
        """
        Salesforce : PATCH sur /sobjects/Opportunity/{id}
        """
        try:
            response = requests.patch(
                f"{self._base_url()}/sobjects/Opportunity/{raw_id}",
                headers=self._get_headers(),
                json=fields,
                timeout=10
            )
            # Salesforce retourne 204 No Content sur succès
            return response.status_code in (200, 204)
        except requests.RequestException as e:
            logger.error(f"Salesforce update_deal : {e}")
            return False

    def add_note(self, deal_raw_id: str, note: str) -> bool:
        """
        Salesforce : créer un Task de type "Note"
        associé à l'Opportunity.
        """
        try:
            response = requests.post(
                f"{self._base_url()}/sobjects/Task",
                headers=self._get_headers(),
                json={
                    "WhatId": deal_raw_id,
                    "Subject": "Note Kuria",
                    "Description": note,
                    "Status": "Completed",
                    "ActivityDate": datetime.utcnow().strftime("%Y-%m-%d")
                },
                timeout=10
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"Salesforce add_note : {e}")
            return False

    # ─────────────────────────────────────────
    # UTILITAIRES SALESFORCE
    # ─────────────────────────────────────────

    def _parse_sf_date(self, value) -> Optional[datetime]:
        """
        Salesforce retourne les dates en ISO 8601 :
        "2025-03-15T10:30:00.000+0000"
        """
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("+0000", "+00:00"))
        except (ValueError, TypeError):
            return None
