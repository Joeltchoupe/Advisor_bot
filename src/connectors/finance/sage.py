# connectors/finance/sage.py

import requests
from datetime import datetime
from typing import Optional
from models import Invoice, Expense, InvoiceStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

SAGE_BASE_URL = "https://api.accounting.sage.com/v3.1"


class SageConnector(BaseConnector):
    """
    Sage Business Cloud Accounting.
    Credentials :
    {
        "access_token": str
    }
    """

    def _get_source_name(self) -> str:
        return "sage"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "Content-Type": "application/json"
        }

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{SAGE_BASE_URL}/business",
                headers=self._get_headers(),
                timeout=10
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"Sage connexion : {e}")
            return False

    def fetch_invoices(self) -> list[Invoice]:
        all_invoices = []
        page = 1

        while True:
            try:
                response = requests.get(
                    f"{SAGE_BASE_URL}/sales_invoices",
                    headers=self._get_headers(),
                    params={"page": page, "items_per_page": 100},
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                items = data.get("$items", [])
                for raw in items:
                    invoice = self._normalize_invoice(raw)
                    if invoice:
                        all_invoices.append(invoice)

                if len(items) < 100:
                    break
                page += 1

            except requests.RequestException as e:
                logger.error(f"Sage fetch_invoices : {e}")
                break

        logger.info(f"Sage : {len(all_invoices)} factures")
        return all_invoices

    def _normalize_invoice(self, raw: dict) -> Optional[Invoice]:
        try:
            raw_id = self._safe_str(raw.get("id"))
            if not raw_id:
                return None

            sage_status = raw.get("status", {}).get("id", "")
            if sage_status == "PAID":
                status = InvoiceStatus.PAID
            elif sage_status == "OVERDUE":
                status = InvoiceStatus.OVERDUE
            else:
                status = InvoiceStatus.SENT

            total = self._safe_float(raw.get("total_amount"))
            outstanding = self._safe_float(raw.get("outstanding_amount"))

            due_at = self._parse_sage_date(raw.get("due_date"))
            paid_at = self._parse_sage_date(raw.get("last_paid"))
            payment_delay = None
            if paid_at and due_at:
                payment_delay = (paid_at - due_at).days

            contact = raw.get("contact") or {}

            return Invoice(
                id=f"sage_{raw_id}",
                company_id=self.company_id,
                amount=total,
                amount_paid=total - outstanding,
                currency=self._safe_str(
                    raw.get("currency", {}).get("id"), "EUR"),
                client_id=self._safe_str(contact.get("id")),
                client_name=self._safe_str(contact.get("displayed_as")),
                status=status,
                issued_at=self._parse_sage_date(raw.get("date")) or datetime.utcnow(),
                due_at=due_at,
                paid_at=paid_at,
                payment_delay_days=payment_delay,
                connector_source="sage",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Sage normalize_invoice {raw.get('id')} : {e}")
            return None

    def _parse_sage_date(self, value) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
