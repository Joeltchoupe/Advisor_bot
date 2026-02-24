# connectors/payments/stripe.py

import stripe as stripe_lib
from datetime import datetime
from typing import Optional
from models import Invoice, InvoiceStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)


class StripeConnector(BaseConnector):
    """
    Stripe complète les données de facturation.
    Principalement utile pour :
    - Confirmer les paiements réels
    - Détecter les abonnements récurrents
    - Avoir les dates de paiement exactes

    Credentials :
    {
        "secret_key": str
    }
    """

    def _get_source_name(self) -> str:
        return "stripe"

    def connect(self) -> bool:
        try:
            stripe_lib.api_key = self.credentials["secret_key"]
            stripe_lib.Account.retrieve()
            return True
        except stripe_lib.error.AuthenticationError as e:
            logger.error(f"Stripe auth : {e}")
            return False
        except Exception as e:
            logger.error(f"Stripe connexion : {e}")
            return False

    def fetch_invoices(self) -> list[Invoice]:
        """
        Récupère les invoices Stripe (paiements réels confirmés).
        """
        stripe_lib.api_key = self.credentials["secret_key"]
        all_invoices = []

        try:
            # Stripe utilise une pagination par curseur
            invoices = stripe_lib.Invoice.list(limit=100)

            for raw in invoices.auto_paging_iter():
                invoice = self._normalize_invoice(raw)
                if invoice:
                    all_invoices.append(invoice)

        except Exception as e:
            logger.error(f"Stripe fetch_invoices : {e}")

        logger.info(f"Stripe : {len(all_invoices)} invoices")
        return all_invoices

    def _normalize_invoice(self, raw) -> Optional[Invoice]:
        try:
            raw_id = raw.get("id", "")
            if not raw_id:
                return None

            # Statut Stripe : draft, open, paid, void, uncollectible
            stripe_status = raw.get("status", "open")
            if stripe_status == "paid":
                status = InvoiceStatus.PAID
            elif stripe_status in ("open", "uncollectible"):
                due_ts = raw.get("due_date")
                if due_ts and due_ts < datetime.utcnow().timestamp():
                    status = InvoiceStatus.OVERDUE
                else:
                    status = InvoiceStatus.SENT
            else:
                status = InvoiceStatus.DRAFT

            total = raw.get("amount_paid", 0) / 100  # Stripe en centimes
            amount_due = raw.get("amount_due", 0) / 100
            amount_paid = raw.get("amount_paid", 0) / 100

            # Dates
            issued_at = datetime.utcfromtimestamp(raw["created"]) if raw.get("created") else datetime.utcnow()
            due_at = datetime.utcfromtimestamp(raw["due_date"]) if raw.get("due_date") else None
            paid_at = datetime.utcfromtimestamp(raw["status_transitions"]["paid_at"]) \
                if raw.get("status_transitions", {}).get("paid_at") else None

            payment_delay = None
            if paid_at and due_at:
                payment_delay = (paid_at - due_at).days

            # Client
            customer_name = ""
            if raw.get("customer_name"):
                customer_name = raw["customer_name"]
            elif raw.get("customer_email"):
                customer_name = raw["customer_email"]

            return Invoice(
                id=f"stripe_{raw_id}",
                company_id=self.company_id,
                amount=amount_due,
                amount_paid=amount_paid,
                currency=raw.get("currency", "eur").upper(),
                client_id=self._safe_str(raw.get("customer")),
                client_name=customer_name,
                status=status,
                issued_at=issued_at,
                due_at=due_at,
                paid_at=paid_at,
                payment_delay_days=payment_delay,
                connector_source="stripe",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Stripe normalize_invoice {raw.get('id')} : {e}")
            return None
