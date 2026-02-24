# connectors/finance/xero.py

import requests
from datetime import datetime
from typing import Optional
from models import Invoice, Expense, InvoiceStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

XERO_BASE_URL = "https://api.xero.com/api.xro/2.0"


class XeroConnector(BaseConnector):
    """
    Credentials :
    {
        "access_token": str,
        "tenant_id": str     # Xero-Tenant-Id header obligatoire
    }
    """

    def _get_source_name(self) -> str:
        return "xero"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "Xero-Tenant-Id": self.credentials["tenant_id"],
            "Accept": "application/json"
        }

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{XERO_BASE_URL}/Organisation",
                headers=self._get_headers(),
                timeout=10
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"Xero connexion : {e}")
            return False

    def fetch_invoices(self) -> list[Invoice]:
        all_invoices = []
        page = 1

        while True:
            try:
                response = requests.get(
                    f"{XERO_BASE_URL}/Invoices",
                    headers=self._get_headers(),
                    params={
                        "Type": "ACCREC",    # Accounts Receivable = factures clients
                        "page": page,
                        "pageSize": 100
                    },
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                invoices = data.get("Invoices", [])
                for raw in invoices:
                    invoice = self._normalize_invoice(raw)
                    if invoice:
                        all_invoices.append(invoice)

                if len(invoices) < 100:
                    break
                page += 1

            except requests.RequestException as e:
                logger.error(f"Xero fetch_invoices : {e}")
                break

        logger.info(f"Xero : {len(all_invoices)} factures")
        return all_invoices

    def _normalize_invoice(self, raw: dict) -> Optional[Invoice]:
        try:
            raw_id = self._safe_str(raw.get("InvoiceID"))
            if not raw_id:
                return None

            # Statut Xero : DRAFT, SUBMITTED, AUTHORISED, PAID, VOIDED
            xero_status = raw.get("Status", "")
            if xero_status == "PAID":
                status = InvoiceStatus.PAID
            elif xero_status in ("AUTHORISED",):
                due_str = raw.get("DueDateString") or raw.get("DueDate")
                due = self._parse_xero_date(due_str)
                if due and due < datetime.utcnow():
                    status = InvoiceStatus.OVERDUE
                else:
                    status = InvoiceStatus.SENT
            else:
                status = InvoiceStatus.DRAFT

            total = self._safe_float(raw.get("Total"))
            amount_due = self._safe_float(raw.get("AmountDue"))
            amount_paid = total - amount_due

            # Date paiement réel
            paid_at = None
            payment_delay = None
            if status == InvoiceStatus.PAID:
                payments = raw.get("Payments", [])
                if payments:
                    last_payment = payments[-1]
                    paid_at = self._parse_xero_date(last_payment.get("Date"))
                due_at = self._parse_xero_date(raw.get("DueDateString") or raw.get("DueDate"))
                if paid_at and due_at:
                    payment_delay = (paid_at - due_at).days

            contact = raw.get("Contact") or {}

            return Invoice(
                id=f"xero_{raw_id}",
                company_id=self.company_id,
                amount=total,
                amount_paid=amount_paid,
                currency=self._safe_str(raw.get("CurrencyCode"), "EUR"),
                client_id=self._safe_str(contact.get("ContactID")),
                client_name=self._safe_str(contact.get("Name")),
                status=status,
                issued_at=self._parse_xero_date(raw.get("DateString") or raw.get("Date")) or datetime.utcnow(),
                due_at=self._parse_xero_date(raw.get("DueDateString") or raw.get("DueDate")),
                paid_at=paid_at,
                payment_delay_days=payment_delay,
                connector_source="xero",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Xero normalize_invoice {raw.get('InvoiceID')} : {e}")
            return None

    def fetch_expenses(self) -> list[Expense]:
        """
        Dans Xero : les dépenses sont des invoices de type ACCPAY
        (Accounts Payable = factures fournisseurs)
        """
        all_expenses = []
        page = 1

        while True:
            try:
                response = requests.get(
                    f"{XERO_BASE_URL}/Invoices",
                    headers=self._get_headers(),
                    params={
                        "Type": "ACCPAY",
                        "page": page,
                        "pageSize": 100
                    },
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                invoices = data.get("Invoices", [])
                for raw in invoices:
                    expense = self._normalize_expense(raw)
                    if expense:
                        all_expenses.append(expense)

                if len(invoices) < 100:
                    break
                page += 1

            except requests.RequestException as e:
                logger.error(f"Xero fetch_expenses : {e}")
                break

        return all_expenses

    def _normalize_expense(self, raw: dict) -> Optional[Expense]:
        try:
            raw_id = self._safe_str(raw.get("InvoiceID"))
            if not raw_id:
                return None

            contact = raw.get("Contact") or {}

            return Expense(
                id=f"xero_{raw_id}",
                company_id=self.company_id,
                amount=self._safe_float(raw.get("Total")),
                currency=self._safe_str(raw.get("CurrencyCode"), "EUR"),
                vendor=self._safe_str(contact.get("Name")),
                date=self._parse_xero_date(raw.get("DateString") or raw.get("Date")) or datetime.utcnow(),
                connector_source="xero",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Xero normalize_expense : {e}")
            return None

    def _parse_xero_date(self, value) -> Optional[datetime]:
        """
        Xero retourne parfois des dates au format bizarre :
        "/Date(1742123400000+0000)/" (timestamp ms en JSON)
        ou "2025-03-15" ou "2025-03-15T00:00:00"
        """
        if not value:
            return None
        try:
            s = str(value)
            # Format bizarre Xero
            if s.startswith("/Date("):
                ms = int(s.replace("/Date(", "").split("+")[0].split("-")[0])
                return datetime.utcfromtimestamp(ms / 1000)
            if "T" in s:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
