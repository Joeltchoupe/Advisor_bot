# tests/conftest.py

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


# ─────────────────────────────────────────
# FIXTURES — DONNÉES RÉALISTES
# Des données qui ressemblent à ce qu'on
# reçoit vraiment des APIs.
# ─────────────────────────────────────────

@pytest.fixture
def company_id():
    return "test-company-uuid-123"


@pytest.fixture
def sample_deals():
    """
    10 deals réalistes.
    Mix de won, lost, active, stagnant.
    """
    now = datetime.utcnow()
    return [
        # Deal actif récent — fort
        {
            "id": "deal_001",
            "company_id": "test-company-uuid-123",
            "title": "Acme Corp — Projet Alpha",
            "amount": 45000.0,
            "stage": "proposal",
            "stage_order": 3,
            "probability": 0.6,
            "probability_real": None,
            "status": "active",
            "created_at": (now - timedelta(days=15)).isoformat(),
            "last_activity_at": (now - timedelta(days=2)).isoformat(),
            "closed_at": None,
            "expected_close_date": (now + timedelta(days=20)).isoformat(),
            "owner_id": "owner_001",
            "owner_name": "Marie Dupont",
            "source": "referral",
            "connector_source": "hubspot",
            "raw_id": "hs_001"
        },
        # Deal actif stagnant — zombie potentiel
        {
            "id": "deal_002",
            "company_id": "test-company-uuid-123",
            "title": "Beta Industries — Licence annuelle",
            "amount": 28000.0,
            "stage": "qualified",
            "stage_order": 2,
            "probability": 0.4,
            "probability_real": None,
            "status": "active",
            "created_at": (now - timedelta(days=45)).isoformat(),
            "last_activity_at": (now - timedelta(days=32)).isoformat(),
            "closed_at": None,
            "expected_close_date": (now + timedelta(days=10)).isoformat(),
            "owner_id": "owner_002",
            "owner_name": "Thomas Martin",
            "source": "linkedin",
            "connector_source": "hubspot",
            "raw_id": "hs_002"
        },
        # Deal WON récent
        {
            "id": "deal_003",
            "company_id": "test-company-uuid-123",
            "title": "Gamma SAS — Déploiement complet",
            "amount": 72000.0,
            "stage": "closed_won",
            "stage_order": 5,
            "probability": 1.0,
            "probability_real": 1.0,
            "status": "won",
            "created_at": (now - timedelta(days=60)).isoformat(),
            "last_activity_at": (now - timedelta(days=3)).isoformat(),
            "closed_at": (now - timedelta(days=3)).isoformat(),
            "expected_close_date": (now - timedelta(days=3)).isoformat(),
            "owner_id": "owner_001",
            "owner_name": "Marie Dupont",
            "source": "referral",
            "connector_source": "hubspot",
            "raw_id": "hs_003"
        },
        # Deal LOST récent
        {
            "id": "deal_004",
            "company_id": "test-company-uuid-123",
            "title": "Delta Corp — Pilote",
            "amount": 15000.0,
            "stage": "closed_lost",
            "stage_order": 5,
            "probability": 0.0,
            "probability_real": 0.0,
            "status": "lost",
            "created_at": (now - timedelta(days=90)).isoformat(),
            "last_activity_at": (now - timedelta(days=5)).isoformat(),
            "closed_at": (now - timedelta(days=5)).isoformat(),
            "expected_close_date": None,
            "owner_id": "owner_002",
            "owner_name": "Thomas Martin",
            "source": "cold_email",
            "connector_source": "hubspot",
            "raw_id": "hs_004"
        },
        # Deal WON ancien (référence historique)
        {
            "id": "deal_005",
            "company_id": "test-company-uuid-123",
            "title": "Epsilon Ltd — Support premium",
            "amount": 36000.0,
            "stage": "closed_won",
            "stage_order": 5,
            "probability": 1.0,
            "probability_real": 1.0,
            "status": "won",
            "created_at": (now - timedelta(days=120)).isoformat(),
            "last_activity_at": (now - timedelta(days=30)).isoformat(),
            "closed_at": (now - timedelta(days=30)).isoformat(),
            "expected_close_date": None,
            "owner_id": "owner_001",
            "owner_name": "Marie Dupont",
            "source": "referral",
            "connector_source": "hubspot",
            "raw_id": "hs_005"
        },
    ]


@pytest.fixture
def sample_invoices():
    """
    6 factures : mix payées, en retard, en cours.
    """
    now = datetime.utcnow()
    return [
        # Payée à temps
        {
            "id": "inv_001",
            "company_id": "test-company-uuid-123",
            "amount": 12000.0,
            "amount_paid": 12000.0,
            "currency": "EUR",
            "client_id": "client_001",
            "client_name": "Acme Corp",
            "status": "paid",
            "issued_at": (now - timedelta(days=60)).isoformat(),
            "due_at": (now - timedelta(days=30)).isoformat(),
            "paid_at": (now - timedelta(days=28)).isoformat(),
            "payment_delay_days": -2,
            "connector_source": "quickbooks",
            "raw_id": "qb_001"
        },
        # Payée en retard
        {
            "id": "inv_002",
            "company_id": "test-company-uuid-123",
            "amount": 8500.0,
            "amount_paid": 8500.0,
            "currency": "EUR",
            "client_id": "client_002",
            "client_name": "Beta Industries",
            "status": "paid",
            "issued_at": (now - timedelta(days=75)).isoformat(),
            "due_at": (now - timedelta(days=45)).isoformat(),
            "paid_at": (now - timedelta(days=30)).isoformat(),
            "payment_delay_days": 15,
            "connector_source": "quickbooks",
            "raw_id": "qb_002"
        },
        # En retard — 12 jours
        {
            "id": "inv_003",
            "company_id": "test-company-uuid-123",
            "amount": 24000.0,
            "amount_paid": 0.0,
            "currency": "EUR",
            "client_id": "client_003",
            "client_name": "Gamma SAS",
            "status": "overdue",
            "issued_at": (now - timedelta(days=42)).isoformat(),
            "due_at": (now - timedelta(days=12)).isoformat(),
            "paid_at": None,
            "payment_delay_days": None,
            "connector_source": "quickbooks",
            "raw_id": "qb_003"
        },
        # En retard — 3 jours
        {
            "id": "inv_004",
            "company_id": "test-company-uuid-123",
            "amount": 6000.0,
            "amount_paid": 0.0,
            "currency": "EUR",
            "client_id": "client_001",
            "client_name": "Acme Corp",
            "status": "overdue",
            "issued_at": (now - timedelta(days=33)).isoformat(),
            "due_at": (now - timedelta(days=3)).isoformat(),
            "paid_at": None,
            "payment_delay_days": None,
            "connector_source": "quickbooks",
            "raw_id": "qb_004"
        },
        # En cours — pas encore due
        {
            "id": "inv_005",
            "company_id": "test-company-uuid-123",
            "amount": 18000.0,
            "amount_paid": 0.0,
            "currency": "EUR",
            "client_id": "client_004",
            "client_name": "Delta Corp",
            "status": "sent",
            "issued_at": (now - timedelta(days=10)).isoformat(),
            "due_at": (now + timedelta(days=20)).isoformat(),
            "paid_at": None,
            "payment_delay_days": None,
            "connector_source": "quickbooks",
            "raw_id": "qb_005"
        },
    ]


@pytest.fixture
def sample_tasks():
    """
    8 tâches : mix de statuts et assignés.
    """
    now = datetime.utcnow()
    return [
        # À temps
        {
            "id": "task_001",
            "company_id": "test-company-uuid-123",
            "title": "Préparer la démo Acme",
            "description": "",
            "assignee_id": "user_001",
            "assignee_name": "Marie Dupont",
            "status": "in_progress",
            "created_at": (now - timedelta(days=3)).isoformat(),
            "due_at": (now + timedelta(days=2)).isoformat(),
            "completed_at": None,
            "cycle_time_days": None,
            "connector_source": "asana",
            "raw_id": "asana_001"
        },
        # En retard
        {
            "id": "task_002",
            "company_id": "test-company-uuid-123",
            "title": "Relire le contrat Beta",
            "description": "",
            "assignee_id": "user_002",
            "assignee_name": "Thomas Martin",
            "status": "overdue",
            "created_at": (now - timedelta(days=10)).isoformat(),
            "due_at": (now - timedelta(days=3)).isoformat(),
            "completed_at": None,
            "cycle_time_days": None,
            "connector_source": "asana",
            "raw_id": "asana_002"
        },
        # Non assignée
        {
            "id": "task_003",
            "company_id": "test-company-uuid-123",
            "title": "Mettre à jour les slides produit",
            "description": "",
            "assignee_id": None,
            "assignee_name": None,
            "status": "todo",
            "created_at": (now - timedelta(days=2)).isoformat(),
            "due_at": (now + timedelta(days=5)).isoformat(),
            "completed_at": None,
            "cycle_time_days": None,
            "connector_source": "asana",
            "raw_id": "asana_003"
        },
        # Terminée avec cycle time
        {
            "id": "task_004",
            "company_id": "test-company-uuid-123",
            "title": "Onboarding client Gamma",
            "description": "",
            "assignee_id": "user_001",
            "assignee_name": "Marie Dupont",
            "status": "done",
            "created_at": (now - timedelta(days=12)).isoformat(),
            "due_at": (now - timedelta(days=5)).isoformat(),
            "completed_at": (now - timedelta(days=6)).isoformat(),
            "cycle_time_days": 6.0,
            "connector_source": "asana",
            "raw_id": "asana_004"
        },
    ]


@pytest.fixture
def sample_contacts():
    """3 contacts avec des profils variés."""
    now = datetime.utcnow()
    return [
        # Profil fort — ressemble aux deals WON
        {
            "id": "contact_001",
            "company_id": "test-company-uuid-123",
            "email": "ceo@newprospect.com",
            "first_name": "Sophie",
            "last_name": "Laurent",
            "company_name": "NewProspect SAS",
            "company_size": 45,
            "company_revenue": None,
            "sector": "consulting",
            "source": "referral",
            "source_detail": "",
            "score": None,
            "score_label": None,
            "score_reason": "",
            "created_at": (now - timedelta(hours=2)).isoformat(),
            "last_activity_at": (now - timedelta(hours=1)).isoformat(),
            "connector_source": "hubspot",
            "raw_id": "hs_contact_001"
        },
        # Profil faible
        {
            "id": "contact_002",
            "company_id": "test-company-uuid-123",
            "email": "info@smallbiz.fr",
            "first_name": "Jean",
            "last_name": "Petit",
            "company_name": "Small Biz",
            "company_size": 3,
            "company_revenue": None,
            "sector": "retail",
            "source": "google_ads",
            "source_detail": "",
            "score": None,
            "score_label": None,
            "score_reason": "",
            "created_at": (now - timedelta(days=1)).isoformat(),
            "last_activity_at": (now - timedelta(days=1)).isoformat(),
            "connector_source": "hubspot",
            "raw_id": "hs_contact_002"
        },
    ]


@pytest.fixture
def mock_supabase(company_id, sample_deals, sample_invoices,
                  sample_tasks, sample_contacts):
    """
    Mock Supabase complet.
    Toutes les requêtes retournent des données de test.
    On ne touche jamais la vraie base pendant les tests.
    """
    with patch("services.database.get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        def table_mock(table_name):
            table = MagicMock()

            data_map = {
                "deals": sample_deals,
                "invoices": sample_invoices,
                "tasks": sample_tasks,
                "contacts": sample_contacts,
                "companies": [{
                    "id": company_id,
                    "name": "Test Company",
                    "clarity_score": 42,
                    "tools_connected": {
                        "crm": {"name": "hubspot", "connected": True},
                        "finance": {"name": "quickbooks", "connected": True}
                    },
                    "agent_configs": {}
                }],
                "forecasts": [],
                "cash_forecasts": [],
                "agent_runs": [],
                "action_logs": [],
                "pending_actions": [],
                "events": [],
                "win_loss_analyses": [],
                "process_metrics": [],
                "cac_metrics": [],
                "invoice_reminders": [],
                "task_reminders": [],
                "team_members": [],
                "credentials": []
            }

            # Chaîne de méthodes fluide
            query = MagicMock()
            query.select.return_value = query
            query.eq.return_value = query
            query.neq.return_value = query
            query.lt.return_value = query
            query.lte.return_value = query
            query.gte.return_value = query
            query.gt.return_value = query
            query.order.return_value = query
            query.limit.return_value = query
            query.not_.return_value = query
            query.is_.return_value = query
            query.contains.return_value = query
            query.upsert.return_value = query
            query.insert.return_value = query
            query.update.return_value = query

            # Execute retourne les données correspondant à la table
            query.execute.return_value = MagicMock(
                data=data_map.get(table_name, [])
            )

            table.select.return_value = query
            table.insert.return_value = query
            table.update.return_value = query
            table.upsert.return_value = query

            return table

        mock_client.table.side_effect = table_mock
        yield mock_client
