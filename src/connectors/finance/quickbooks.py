# connectors/finance/quickbooks.py

import requests
from datetime import datetime
from typing import Optional
from models import Invoice, Expense
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)


class QuickBooksConnector(BaseConnector):
    """
    QuickBooks Online utilise OAuth2.
    Credentials :
    {
        "access_token": str,
        "realm_id": str,      # l'ID de la company QuickBooks
        "sandbox": bool       # True pour les tests
    }
    """

    def _get_source_name(self) -> str:
        return "quickbooks"

    def _base_url(self) -> str:
        if self.credentials.get("sandbox"):
            return f"https://sandbox-quickbooks.api.intuit.com/v3/company/{self.credentials['realm_id']}"
        return f"https://quickbooks.api.intuit.com/v3/company/{self.credentials['realm_id']}"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{self._base_url()}/companyinfo/{self.credentials['realm_id']}",
                headers=self._get_headers(),
                timeout=10
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"QuickBooks connexion : {e}")
            return False

    # ─────────────────────────────────────────
    # INVOICES
    # QuickBooks utilise son propre Query Language (IQL)
    # ─────────────────────────────────────────

    def fetch_invoices(self) -> list[Invoice]:
        raw_invoices = self._query_invoices()
        invoices = []
        for raw in raw_invoices:
            invoice = self._normalize_invoice(raw)
            if invoice:
                invoices.append(invoice)
        logger.info(f"QuickBooks : {len(invoices)} factures")
        return invoices

    def _query_invoices(self) -> list[dict]:
        """
        QuickBooks utilise une syntaxe SQL-like pour ses queries.
        """
        all_invoices = []
        start = 1
        max_results = 100

        while True:
            query = (
                f"SELECT * FROM Invoice "
                f"STARTPOSITION {start} MAXRESULTS {max_results}"
            )
            try:
                response = requests.get(
                    f"{self._base_url()}/query",
                    headers=self._get_headers(),
                    params={"query": query},
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                query_response = data.get("QueryResponse", {})
                invoices = query_response.get("Invoice", [])
                all_invoices.extend(invoices)

                total = query_response.get("totalCount", 0)
                if start + max_results > total:
                    break
                start += max_results

            except requests.RequestException as e:
                logger.error(f"QuickBooks query_invoices : {e}")
                break

        return all_invoices

    def _normalize_invoice(self, raw: dict) -> Optional[Invoice]:
        try:
            raw_id = str(raw.get("Id", ""))
            if not raw_id:
                return None

            # Statut QuickBooks
            balance = self._safe_float(raw.get("Balance"))
            total = self._safe_float(raw.get("TotalAmt"))

            if balance == 0 and total > 0:
                from models import InvoiceStatus
                status = InvoiceStatus.PAID
            else:
                due_date = self._parse_qb_date(raw.get("DueDate"))
                if due_date and due_date < datetime.utcnow():
                    from models import InvoiceStatus
                    status = InvoiceStatus.OVERDUE
                else:
                    from models import InvoiceStatus
                    status = InvoiceStatus.SENT

            # Client
            customer_ref = raw.get("CustomerRef") or {}

            # Dates de paiement réel
            paid_at = None
            payment_delay = None
            if status.value == "paid":
                # QuickBooks ne donne pas directement la date de paiement
                # On utilise la dernière modification comme proxy
                paid_at = self._parse_qb_date(raw.get("MetaData", {}).get("LastUpdatedTime"))
                due_date_obj = self._parse_qb_date(raw.get("DueDate"))
                if paid_at and due_date_obj:
                    payment_delay = (paid_at - due_date_obj).days

            return Invoice(
                id=f"quickbooks_{raw_id}",
                company_id=self.company_id,
                amount=total,
                amount_paid=total - balance,
                currency=self._safe_str(raw.get("CurrencyRef", {}).get("value"), "EUR"),
                client_id=self._safe_str(customer_ref.get("value")),
                client_name=self._safe_str(customer_ref.get("name")),
                status=status,
                issued_at=self._parse_qb_date(raw.get("TxnDate")) or datetime.utcnow(),
                due_at=self._parse_qb_date(raw.get("DueDate")),
                paid_at=paid_at,
                payment_delay_days=payment_delay,
                connector_source="quickbooks",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"QuickBooks normalize_invoice {raw.get('Id')} : {e}")
            return None

    # ─────────────────────────────────────────
    # EXPENSES
    # ─────────────────────────────────────────

    def fetch_expenses(self) -> list[Expense]:
        raw_expenses = self._query_expenses()
        expenses = []
        for raw in raw_expenses:
            expense = self._normalize_expense(raw)
            if expense:
                expenses.append(expense)
        logger.info(f"QuickBooks : {len(expenses)} dépenses")
        return expenses

    def _query_expenses(self) -> list[dict]:
        all_expenses = []
        start = 1

        while True:
            query = (
                f"SELECT * FROM Purchase "
                f"STARTPOSITION {start} MAXRESULTS 100"
            )
            try:
                response = requests.get(
                    f"{self._base_url()}/query",
                    headers=self._get_headers(),
                    params={"query": query},
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                query_response = data.get("QueryResponse", {})
                purchases = query_response.get("Purchase", [])
                all_expenses.extend(purchases)

                total = query_response.get("totalCount", 0)
                if start + 100 > total:
                    break
                start += 100

            except requests.RequestException as e:
                logger.error(f"QuickBooks query_expenses : {e}")
                break

        return all_expenses

    def _normalize_expense(self, raw: dict) -> Optional[Expense]:
        try:
            raw_id = str(raw.get("Id", ""))
            if not raw_id:
                return None

            # Vendor
            vendor_ref = raw.get("EntityRef") or {}
            vendor = vendor_ref.get("name", "") if isinstance(vendor_ref, dict) else ""

            # Catégorie depuis les lignes
            category = ""
            lines = raw.get("Line", [])
            if lines:
                first_line = lines[0]
                account_ref = first_line.get("AccountBasedExpenseLineDetail", {}).get("AccountRef", {})
                category = account_ref.get("name", "")

            return Expense(
                id=f"quickbooks_{raw_id}",
                company_id=self.company_id,
                amount=self._safe_float(raw.get("TotalAmt")),
                currency=self._safe_str(
                    raw.get("CurrencyRef", {}).get("value"), "EUR"),
                vendor=vendor,
                category=category,
                date=self._parse_qb_date(raw.get("TxnDate")) or datetime.utcnow(),
                connector_source="quickbooks",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"QuickBooks normalize_expense : {e}")
            return None

    def _parse_qb_date(self, value) -> Optional[datetime]:
        """
        QuickBooks retourne les dates en "YYYY-MM-DD"
        """
        if not value:
            return None
        try:
            if "T" in str(value):
                return datetime.fromisoformat(str(value))
            return datetime.strptime(str(value), "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
