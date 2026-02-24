# tests/test_connectors.py

"""
Ce qu'on teste :
→ normalize() retourne le bon format Kuria
→ Les données manquantes ne font pas crasher
→ Les dates sont correctement parsées
→ Les montants sont des floats

Ce qu'on ne teste PAS :
→ Les vraies APIs (pas de tokens en test)
→ La pagination (couvre trop de cas)
"""

import pytest
from datetime import datetime
from models import Deal, Contact, Invoice, Task, DealStatus, InvoiceStatus


class TestHubSpotNormalize:
    """
    On teste uniquement la normalisation.
    On bypasse fetch_deals() — on appelle _normalize_deal() directement.
    """

    def setup_method(self):
        from connectors.crm.hubspot import HubSpotConnector
        self.connector = HubSpotConnector(
            company_id="test-uuid",
            credentials={"access_token": "fake-token"}
        )

    def test_normalize_active_deal(self):
        raw = {
            "id": "123456",
            "properties": {
                "dealname": "Test Deal",
                "amount": "45000",
                "dealstage": "proposal",
                "hs_deal_stage_probability": "0.6",
                "hs_is_closed_won": "false",
                "hs_is_closed": "false",
                "createdate": "2025-01-15T10:00:00.000Z",
                "hs_lastmodifieddate": "2025-03-10T14:00:00.000Z",
                "hubspot_owner_id": "owner_001",
                "lead_source": "referral"
            }
        }

        deal = self.connector._normalize_deal(raw)

        assert deal is not None
        assert isinstance(deal, Deal)
        assert deal.id == "hubspot_123456"
        assert deal.title == "Test Deal"
        assert deal.amount == 45000.0
        assert deal.stage == "proposal"
        assert deal.probability == 0.6
        assert deal.status == DealStatus.ACTIVE
        assert deal.source == "referral"
        assert deal.connector_source == "hubspot"
        assert deal.raw_id == "123456"

    def test_normalize_won_deal(self):
        raw = {
            "id": "789",
            "properties": {
                "dealname": "Won Deal",
                "amount": "20000",
                "dealstage": "closedwon",
                "hs_deal_stage_probability": "1.0",
                "hs_is_closed_won": "true",
                "hs_is_closed": "true",
                "createdate": "2025-01-01T00:00:00.000Z",
                "hs_lastmodifieddate": "2025-02-01T00:00:00.000Z",
            }
        }

        deal = self.connector._normalize_deal(raw)

        assert deal is not None
        assert deal.status == DealStatus.WON

    def test_normalize_lost_deal(self):
        raw = {
            "id": "456",
            "properties": {
                "dealname": "Lost Deal",
                "amount": "10000",
                "dealstage": "closedlost",
                "hs_is_closed_won": "false",
                "hs_is_closed": "true",
                "createdate": "2025-01-01T00:00:00.000Z",
                "hs_lastmodifieddate": "2025-02-01T00:00:00.000Z",
            }
        }

        deal = self.connector._normalize_deal(raw)

        assert deal is not None
        assert deal.status == DealStatus.LOST

    def test_normalize_missing_amount(self):
        """Un deal sans montant doit retourner amount=0, pas crasher."""
        raw = {
            "id": "999",
            "properties": {
                "dealname": "No Amount Deal",
                "amount": None,
                "dealstage": "proposal",
                "hs_is_closed_won": "false",
                "hs_is_closed": "false",
                "createdate": "2025-01-01T00:00:00.000Z",
                "hs_lastmodifieddate": "2025-01-01T00:00:00.000Z",
            }
        }

        deal = self.connector._normalize_deal(raw)

        assert deal is not None
        assert deal.amount == 0.0

    def test_normalize_missing_id_returns_none(self):
        """Sans ID, on ne peut pas identifier le deal — retourner None."""
        raw = {
            "id": "",
            "properties": {"dealname": "Ghost Deal"}
        }

        deal = self.connector._normalize_deal(raw)
        assert deal is None

    def test_normalize_date_parsing(self):
        """Les dates HubSpot (ISO + Z) doivent être parsées correctement."""
        raw = {
            "id": "date_test",
            "properties": {
                "dealname": "Date Test",
                "amount": "1000",
                "dealstage": "proposal",
                "hs_is_closed_won": "false",
                "hs_is_closed": "false",
                "createdate": "2025-03-15T10:30:00.000Z",
                "hs_lastmodifieddate": "2025-03-15T10:30:00.000Z",
            }
        }

        deal = self.connector._normalize_deal(raw)

        assert deal is not None
        assert isinstance(deal.created_at, datetime)
        assert deal.created_at.year == 2025
        assert deal.created_at.month == 3
        assert deal.created_at.day == 15


class TestQuickBooksNormalize:

    def setup_method(self):
        from connectors.finance.quickbooks import QuickBooksConnector
        self.connector = QuickBooksConnector(
            company_id="test-uuid",
            credentials={"access_token": "fake", "realm_id": "123"}
        )

    def test_normalize_paid_invoice(self):
        raw = {
            "Id": "QB_001",
            "TotalAmt": "12000.00",
            "Balance": "0.00",
            "DueDate": "2025-02-15",
            "TxnDate": "2025-01-15",
            "CustomerRef": {"value": "cust_001", "name": "Acme Corp"},
            "CurrencyRef": {"value": "EUR"},
            "MetaData": {"LastUpdatedTime": "2025-02-13T10:00:00+00:00"}
        }

        invoice = self.connector._normalize_invoice(raw)

        assert invoice is not None
        assert isinstance(invoice, Invoice)
        assert invoice.amount == 12000.0
        assert invoice.amount_paid == 12000.0
        assert invoice.status == InvoiceStatus.PAID
        assert invoice.client_name == "Acme Corp"
        assert invoice.connector_source == "quickbooks"
        assert invoice.raw_id == "QB_001"

    def test_normalize_overdue_invoice(self):
        from datetime import timedelta
        past_date = (datetime.utcnow() - timedelta(days=10)).strftime("%Y-%m-%d")

        raw = {
            "Id": "QB_002",
            "TotalAmt": "8000.00",
            "Balance": "8000.00",
            "DueDate": past_date,
            "TxnDate": "2025-01-01",
            "CustomerRef": {"value": "cust_002", "name": "Beta Corp"},
            "CurrencyRef": {"value": "EUR"}
        }

        invoice = self.connector._normalize_invoice(raw)

        assert invoice is not None
        assert invoice.status == InvoiceStatus.OVERDUE
        assert invoice.amount_paid == 0.0

    def test_normalize_missing_id_returns_none(self):
        raw = {"Id": "", "TotalAmt": "1000"}
        invoice = self.connector._normalize_invoice(raw)
        assert invoice is None


class TestExcelConnector:
    """
    Excel est notre fallback universel.
    On teste la tolérance aux variations de colonnes.
    """

    def setup_method(self):
        from connectors.finance.excel import ExcelConnector
        self.connector = ExcelConnector(
            company_id="test-uuid",
            credentials={}
        )

    def test_clean_column_name(self):
        """Les noms de colonnes sont normalisés."""
        assert self.connector._clean_column_name("Date d'émission") == "date_d_mission"
        assert self.connector._clean_column_name("Montant (€)") == "montant"
        assert self.connector._clean_column_name("CLIENT NAME") == "client_name"
        assert self.connector._clean_column_name("  amount  ") == "amount"

    def test_normalize_invoice_french_columns(self):
        """Les colonnes en français sont reconnues."""
        row = {
            "montant": 5000.0,
            "client": "Client Test",
            "date_emission": "2025-01-15",
            "echeance": "2025-02-15"
        }

        invoice = self.connector._normalize_invoice_row(row, "row_0")

        assert invoice is not None
        assert invoice.amount == 5000.0
        assert invoice.client_name == "Client Test"

    def test_normalize_invoice_zero_amount_skipped(self):
        """Une ligne avec montant=0 est ignorée."""
        row = {"montant": 0, "client": "Test"}
        invoice = self.connector._normalize_invoice_row(row, "row_0")
        assert invoice is None


class TestPipedriveNormalize:

    def setup_method(self):
        from connectors.crm.pipedrive import PipedriveConnector
        self.connector = PipedriveConnector(
            company_id="test-uuid",
            credentials={"api_token": "fake-token"}
        )

    def test_normalize_open_deal(self):
        raw = {
            "id": 42,
            "title": "Pipedrive Deal",
            "value": 30000,
            "currency": "EUR",
            "status": "open",
            "stage_id": "stage_1",
            "stage_order_nr": 2,
            "probability": 50,
            "add_time": "2025-01-10 09:00:00",
            "update_time": "2025-03-10 14:00:00",
            "user_id": {"id": 1, "name": "Agent Smith"},
            "label": "inbound"
        }

        deal = self.connector._normalize_deal(raw)

        assert deal is not None
        assert deal.id == "pipedrive_42"
        assert deal.amount == 30000.0
        assert deal.status == DealStatus.ACTIVE
        assert deal.probability == 0.5    # 50/100
        assert deal.owner_name == "Agent Smith"

    def test_email_extraction_from_list(self):
        """Pipedrive retourne les emails sous forme de liste."""
        raw = {
            "id": 10,
            "email": [
                {"value": "contact@test.com", "primary": True}
            ],
            "first_name": "Jean",
            "last_name": "Test",
            "add_time": "2025-01-01 00:00:00"
        }

        contact = self.connector._normalize_contact(raw)

        assert contact is not None
        assert contact.email == "contact@test.com"
