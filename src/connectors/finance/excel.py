# connectors/finance/excel.py

import pandas as pd
from datetime import datetime
from typing import Optional
from models import Invoice, Expense, InvoiceStatus
from connectors.base import BaseConnector
import logging
import os

logger = logging.getLogger(__name__)


class ExcelConnector(BaseConnector):
    """
    Fallback universel pour les clients sans logiciel comptable.
    Lit des fichiers Excel/CSV déposés dans un dossier ou uploadés.

    Credentials :
    {
        "invoices_path": str,   # chemin vers le fichier factures
        "expenses_path": str    # chemin vers le fichier dépenses
    }

    Format attendu fichier factures (colonnes minimales) :
    id | client | amount | issued_date | due_date | paid_date | status

    Format attendu fichier dépenses :
    id | vendor | amount | date | category
    """

    def _get_source_name(self) -> str:
        return "excel"

    def connect(self) -> bool:
        """
        Vérifie que les fichiers existent et sont lisibles.
        """
        invoices_path = self.credentials.get("invoices_path", "")
        if invoices_path and not os.path.exists(invoices_path):
            logger.error(f"Excel : fichier introuvable : {invoices_path}")
            return False
        logger.info(f"Excel connecté pour company {self.company_id}")
        return True

    def fetch_invoices(self) -> list[Invoice]:
        path = self.credentials.get("invoices_path", "")
        if not path or not os.path.exists(path):
            return []

        try:
            # Lit Excel ou CSV selon l'extension
            if path.endswith(".csv"):
                df = pd.read_csv(path)
            else:
                df = pd.read_excel(path)

            # Normalisation des noms de colonnes
            # (tolérant aux variations orthographiques)
            df.columns = [self._clean_column_name(c) for c in df.columns]

            invoices = []
            for i, row in df.iterrows():
                invoice = self._normalize_invoice_row(row, str(i))
                if invoice:
                    invoices.append(invoice)

            logger.info(f"Excel : {len(invoices)} factures lues")
            return invoices

        except Exception as e:
            logger.error(f"Excel fetch_invoices : {e}")
            return []

    def _normalize_invoice_row(self, row, fallback_id: str) -> Optional[Invoice]:
        try:
            raw_id = self._safe_str(row.get("id") or row.get("numero") or fallback_id)
            amount = self._safe_float(row.get("amount") or row.get("montant") or row.get("total"))

            if amount == 0:
                return None

            # Statut
            paid_date = self._parse_excel_date(
                row.get("paid_date") or row.get("date_paiement"))

            due_date = self._parse_excel_date(
                row.get("due_date") or row.get("date_echeance") or row.get("echeance"))

            if paid_date:
                status = InvoiceStatus.PAID
            elif due_date and due_date < datetime.utcnow():
                status = InvoiceStatus.OVERDUE
            else:
                status = InvoiceStatus.SENT

            payment_delay = None
            if paid_date and due_date:
                payment_delay = (paid_date - due_date).days

            return Invoice(
                id=f"excel_{raw_id}",
                company_id=self.company_id,
                amount=amount,
                amount_paid=amount if paid_date else 0,
                client_name=self._safe_str(
                    row.get("client") or row.get("client_name") or row.get("nom_client")),
                status=status,
                issued_at=self._parse_excel_date(
                    row.get("issued_date") or row.get("date_emission") or row.get("date")) or datetime.utcnow(),
                due_at=due_date,
                paid_at=paid_date,
                payment_delay_days=payment_delay,
                connector_source="excel",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Excel normalize_invoice_row : {e}")
            return None

    def fetch_expenses(self) -> list[Expense]:
        path = self.credentials.get("expenses_path", "")
        if not path or not os.path.exists(path):
            return []

        try:
            if path.endswith(".csv"):
                df = pd.read_csv(path)
            else:
                df = pd.read_excel(path)

            df.columns = [self._clean_column_name(c) for c in df.columns]

            expenses = []
            for i, row in df.iterrows():
                expense = self._normalize_expense_row(row, str(i))
                if expense:
                    expenses.append(expense)

            return expenses

        except Exception as e:
            logger.error(f"Excel fetch_expenses : {e}")
            return []

    def _normalize_expense_row(self, row, fallback_id: str) -> Optional[Expense]:
        try:
            raw_id = self._safe_str(row.get("id") or fallback_id)
            amount = self._safe_float(
                row.get("amount") or row.get("montant") or row.get("total"))

            if amount == 0:
                return None

            return Expense(
                id=f"excel_{raw_id}",
                company_id=self.company_id,
                amount=amount,
                vendor=self._safe_str(
                    row.get("vendor") or row.get("fournisseur") or row.get("vendeur")),
                category=self._safe_str(
                    row.get("category") or row.get("categorie") or row.get("type")),
                date=self._parse_excel_date(
                    row.get("date") or row.get("date_depense")) or datetime.utcnow(),
                connector_source="excel",
                raw_id=raw_id
            )

        except Exception as e:
            logger.error(f"Excel normalize_expense_row : {e}")
            return None

    def _clean_column_name(self, col: str) -> str:
        """
        Normalise les noms de colonnes :
        "Date d'émission" → "date_d_emission"
        "Montant (€)" → "montant"
        """
        import re
        col = str(col).lower().strip()
        col = re.sub(r"[^a-z0-9_]", "_", col)
        col = re.sub(r"_+", "_", col)
        return col.strip("_")

    def _parse_excel_date(self, value) -> Optional[datetime]:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        try:
            if isinstance(value, datetime):
                return value
            if isinstance(value, pd.Timestamp):
                return value.to_pydatetime()
            # String date
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
                try:
                    return datetime.strptime(str(value)[:10], fmt)
                except ValueError:
                    continue
            return None
        except Exception:
            return None
