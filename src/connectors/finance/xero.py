# connectors/finance/xero.py

import requests
from datetime import datetime, timezone, timedelta
from typing import Optional
from models import Invoice, Expense, InvoiceStatus
from connectors.base import BaseConnector
import logging

logger = logging.getLogger(__name__)

XERO_BASE_URL              = "https://api.xero.com/api.xro/2.0"
XERO_TOKEN_URL             = "https://identity.xero.com/connect/token"
XERO_TOKEN_LIFETIME_SECONDS = 1800  # 30 minutes — le plus court


class XeroConnector(BaseConnector):

    def _get_source_name(self) -> str:
        return "xero"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials['access_token']}",
            "Xero-Tenant-Id": self.credentials["tenant_id"],
            "Accept":         "application/json"
        }

    # ─────────────────────────────────────────
    # TOKEN REFRESH
    # Xero expire en 30 minutes — critique
    # ─────────────────────────────────────────

    def refresh_access_token(self) -> bool:
        """
        Xero OAuth2 refresh.
        Tokens valides 30 minutes → refresh très fréquent.

        Credentials nécessaires :
        {
            "access_token":  str,
            "refresh_token": str,
            "client_id":     str,
            "client_secret": str,
            "tenant_id":     str
        }
        """
        refresh_token = self.credentials.get("refresh_token")
        client_id     = self.credentials.get("client_id")
        client_secret = self.credentials.get("client_secret")

        if not all([refresh_token, client_id, client_secret]):
            logger.warning("[xero] Refresh impossible : credentials manquants")
            return True

        try:
            import base64
            auth = base64.b64encode(
                f"{client_id}:{client_secret}".encode()
            ).decode()

            response = requests.post(
                XERO_TOKEN_URL,
                data={
                    "grant_type":    "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type":  "application/x-www-form-urlencoded"
                },
                timeout=15
            )
            response.raise_for_status()
            data = response.json()

            expires_at = (
                datetime.now(tz=timezone.utc) +
                timedelta(seconds=data.get(
                    "expires_in", XERO_TOKEN_LIFETIME_SECONDS
                ))
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
            logger.error(f"[xero] Erreur refresh token : {e}")
            return False

    # ─────────────────────────────────────────
    # CONNEXION
    # ─────────────────────────────────────────

    def connect(self) -> bool:
        try:
            response = requests.get(
                f"{XERO_BASE_URL}/Organisation",
                headers=self._get_headers(),
                timeout=10
            )
            if response.status_code == 200:
                return True

            if response.status_code == 401:
                if self.refresh_access_token():
                    response2 = requests.get(
                        f"{XERO_BASE_URL}/Organisation",
                        headers=self._get_headers(),
                        timeout=10
                    )
                    return response2.status_code == 200

            return False

        except requests.RequestException as e:
            logger.error(f"Xero connexion : {e}")
            return False

    # ─────────────────────────────────────────
    # FETCH INVOICES
    # ─────────────────────────────────────────

    def fetch_invoices(self) -> list[Invoice]:
        if not self.ensure_valid_token():
            return []

        all_invoices = []
        page = 1

        while True:
            try:
                response = requests.get(
                    f"{XERO_BASE_URL}/Invoices",
                    headers=self._get_headers(),
                    params={
                        "Type":     "ACCREC",
                        "page":     page,
                        "pageSize": 100
                    },
                    timeout=30
                )

                if response.status_code == 401:
                    if self.refresh_access_token():
                        response = requests.get(
                            f"{XERO_BASE_URL}/Invoices",
                            headers=self._get_headers(),
                            params={
                                "Type": "ACCREC",
                                "page": page,
                                "pageSize": 100
                            },
                            timeout=30
                        )
                    else:
                        break

                response.raise_for_status()
                data     = response.json()
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

            xero_status = raw.get("Status", "")
            if xero_status == "PAID":
                status = InvoiceStatus.PAID
            elif xero_status == "AUTHORISED":
                due_str = raw.get("DueDateString") or raw.get("DueDate")
                due     = self._parse_datetime(due_str)
                if due and due < datetime.utcnow():
                    status = InvoiceStatus.OVERDUE
                else:
                    status = InvoiceStatus.SENT
            else:
                status = InvoiceStatus.DRAFT

            total      = self._safe_float(raw.get("Total"))
            amount_due = self._safe_float(raw.get("AmountDue"))
            amount_paid = total - amount_due

            paid_at         = None
            payment_delay   = None
            due_at          = self._parse_datetime(
                raw.get("DueDateString") or raw.get("DueDate")
            )

            if status == InvoiceStatus.PAID:
                payments = raw.get("Payments", [])
                if payments:
                    paid_at = self._parse_datetime(payments[-1].get("Date"))
                if paid_at and due_at:
                    payment_delay = (paid_at - due_at.replace(
                        tzinfo=None
                    )).days if paid_at.tzinfo else (paid_at - due_at).days

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
                issued_at=self._parse_datetime(
                    raw.get("DateString") or raw.get("Date")
                ) or datetime.utcnow(),
                due_at=due_at,
                paid_at=paid_at,
                payment_delay_days=payment_delay,
                connector_source="xero",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(
                f"Xero normalize_invoice {raw.get('InvoiceID')} : {e}"
            )
            return None

    def fetch_expenses(self) -> list[Expense]:
        if not self.ensure_valid_token():
            return []

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

                if response.status_code == 401:
                    if self.refresh_access_token():
                        continue
                    else:
                        break

                response.raise_for_status()
                data     = response.json()
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
                date=self._parse_datetime(
                    raw.get("DateString") or raw.get("Date")
                ) or datetime.utcnow(),
                connector_source="xero",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Xero normalize_expense : {e}")
            return None
