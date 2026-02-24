# connectors/finance/freshbooks.py

import requests
from datetime import datetime
from typing import Optional
from models import Invoice, Expense, InvoiceStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

FRESHBOOKS_BASE_URL = "https://api.freshbooks.com"


class FreshBooksConnector(BaseConnector):
    """
    Credentials :
    {
        "access_token": str,
        "account_id": str     # business account_id FreshBooks
    }
    """

    def _get_source_name(self) -> str:
        return "freshbooks"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "Content-Type": "application/json"
        }

    def _account_url(self) -> str:
        return f"{FRESHBOOKS_BASE_URL}/accounting/account/{self.credentials['account_id']}"

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{FRESHBOOKS_BASE_URL}/auth/api/v1/users/me",
                headers=self._get_headers(),
                timeout=10
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"FreshBooks connexion : {e}")
            return False

    def fetch_invoices(self) -> list[Invoice]:
        all_invoices = []
        page = 1

        while True:
            try:
                response = requests.get(
                    f"{self._account_url()}/invoices/invoices",
                    headers=self._get_headers(),
                    params={"page": page, "per_page": 100},
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                invoices = data.get("response", {}).get("result", {}).get("invoices", [])
                for raw in invoices:
                    invoice = self._normalize_invoice(raw)
                    if invoice:
                        all_invoices.append(invoice)

                pages = data.get("response", {}).get("result", {}).get("pages", 1)
                if page >= pages:
                    break
                page += 1

            except requests.RequestException as e:
                logger.error(f"FreshBooks fetch_invoices : {e}")
                break

        logger.info(f"FreshBooks : {len(all_invoices)} factures")
        return all_invoices

    def _normalize_invoice(self, raw: dict) -> Optional[Invoice]:
        try:
            raw_id = str(raw.get("id", ""))
            if not raw_id:
                return None

            # Statut FreshBooks : 1=draft, 2=sent, 4=viewed, 5=outstanding, 6=paid
            fb_status = raw.get("v3_status", "")
            if fb_status == "paid":
                status = InvoiceStatus.PAID
            elif fb_status in ("outstanding", "overdue"):
                status = InvoiceStatus.OVERDUE
            elif fb_status in ("sent", "viewed"):
                status = InvoiceStatus.SENT
            else:
                status = InvoiceStatus.DRAFT

            total = self._safe_float(raw.get("amount", {}).get("amount"))
            outstanding = self._safe_float(raw.get("outstanding", {}).get("amount"))
            amount_paid = total - outstanding

            due_at = self._parse_fb_date(raw.get("due_date"))
            paid_at = self._parse_fb_date(raw.get("payment_date"))
            payment_delay = None
            if paid_at and due_at:
                payment_delay = (paid_at - due_at).days

            return Invoice(
                id=f"freshbooks_{raw_id}",
                company_id=self.company_id,
                amount=total,
                amount_paid=amount_paid,
                currency=self._safe_str(raw.get("currency_code"), "EUR"),
                client_id=str(raw.get("customerid", "")),
                client_name=self._safe_str(raw.get("organization") or raw.get("fname")),
                status=status,
                issued_at=self._parse_fb_date(raw.get("create_date")) or datetime.utcnow(),
                due_at=due_at,
                paid_at=paid_at,
                payment_delay_days=payment_delay,
                connector_source="freshbooks",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"FreshBooks normalize_invoice {raw.get('id')} : {e}")
            return None

    def _parse_fb_date(self, value) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.strptime(str(value), "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
