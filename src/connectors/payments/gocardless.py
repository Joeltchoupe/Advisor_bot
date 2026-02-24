# connectors/payments/gocardless.py

import requests
from datetime import datetime
from typing import Optional
from models import Invoice, InvoiceStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

GOCARDLESS_BASE_URL = "https://api.gocardless.com"


class GoCardlessConnector(BaseConnector):
    """
    GoCardless : prélèvements automatiques.
    Utile pour tracker les paiements récurrents confirmés.

    Credentials :
    {
        "access_token": str,
        "environment": str    # "live" ou "sandbox"
    }
    """

    def _get_source_name(self) -> str:
        return "gocardless"

    def _get_headers(self) -> dict:
        env = self.credentials.get("environment", "live")
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "GoCardless-Version": "2015-07-06",
            "Content-Type": "application/json"
        }

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{GOCARDLESS_BASE_URL}/creditors",
                headers=self._get_headers(),
                timeout=10
            )
            return response.status_code == 200
        except requests.RequestException as e:
            logger.error(f"GoCardless connexion : {e}")
            return False

    def fetch_invoices(self) -> list[Invoice]:
        """
        GoCardless : les "payments" = prélèvements confirmés.
        On les traite comme des invoices payées.
        """
        all_invoices = []
        after = None

        while True:
            try:
                params = {"limit": 500}
                if after:
                    params["after"] = after

                response = requests.get(
                    f"{GOCARDLESS_BASE_URL}/payments",
                    headers=self._get_headers(),
                    params=params,
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                payments = data.get("payments", [])
                for raw in payments:
                    invoice = self._normalize_payment(raw)
                    if invoice:
                        all_invoices.append(invoice)

                meta = data.get("meta", {})
                cursors = meta.get("cursors", {})
                after = cursors.get("after")
                if not after:
                    break

            except requests.RequestException as e:
                logger.error(f"GoCardless fetch_invoices : {e}")
                break

        logger.info(f"GoCardless : {len(all_invoices)} paiements")
        return all_invoices

    def _normalize_payment(self, raw: dict) -> Optional[Invoice]:
        try:
            raw_id = self._safe_str(raw.get("id"))
            if not raw_id:
                return None

            gc_status = raw.get("status", "")
            if gc_status in ("confirmed", "paid_out"):
                status = InvoiceStatus.PAID
            elif gc_status in ("failed", "cancelled"):
                return None  # On ignore les paiements échoués
            else:
                status = InvoiceStatus.SENT

            amount = self._safe_float(raw.get("amount", 0)) / 100  # en centimes

            charge_date = self._parse_gc_date(raw.get("charge_date"))

            return Invoice(
                id=f"gocardless_{raw_id}",
                company_id=self.company_id,
                amount=amount,
                amount_paid=amount if status == InvoiceStatus.PAID else 0,
                currency=self._safe_str(raw.get("currency"), "EUR").upper(),
                status=status,
                issued_at=charge_date or datetime.utcnow(),
                due_at=charge_date,
                paid_at=charge_date if status == InvoiceStatus.PAID else None,
                payment_delay_days=0 if status == InvoiceStatus.PAID else None,
                connector_source="gocardless",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"GoCardless normalize_payment : {e}")
            return None

    def _parse_gc_date(self, value) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.strptime(str(value), "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
