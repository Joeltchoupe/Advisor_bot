# connectors/__init__.py

"""
Factory centralisée pour tous les connecteurs.

Utilisation :
    from connectors import get_connector

    connector = get_connector("hubspot", company_id, credentials)
    if connector and connector.connect():
        deals = connector.fetch_deals()

Plus jamais d'import direct dispersé dans le code.
Un seul endroit à modifier si un connecteur change de nom.
"""

import logging
from typing import Optional
from connectors.base import BaseConnector

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# MAP : nom outil → classe connecteur
# Ajouter un connecteur = ajouter une ligne ici
# ─────────────────────────────────────────

_CONNECTOR_MAP = {
    # CRM
    "hubspot":    ("connectors.crm.hubspot",    "HubSpotConnector"),
    "salesforce": ("connectors.crm.salesforce", "SalesforceConnector"),
    "pipedrive":  ("connectors.crm.pipedrive",  "PipedriveConnector"),
    "zoho":       ("connectors.crm.zoho",       "ZohoConnector"),

    # Finance
    "quickbooks": ("connectors.finance.quickbooks", "QuickBooksConnector"),
    "xero":       ("connectors.finance.xero",       "XeroConnector"),
    "freshbooks": ("connectors.finance.freshbooks", "FreshBooksConnector"),
    "sage":       ("connectors.finance.sage",       "SageConnector"),
    "excel":      ("connectors.finance.excel",      "ExcelConnector"),

    # Paiements
    "stripe":     ("connectors.payments.stripe",     "StripeConnector"),
    "gocardless": ("connectors.payments.gocardless", "GoCardlessConnector"),

    # Email
    "gmail":   ("connectors.email.gmail",   "GmailConnector"),
    "outlook": ("connectors.email.outlook", "OutlookConnector"),

    # Projet
    "asana":  ("connectors.project.asana",  "AsanaConnector"),
    "notion": ("connectors.project.notion", "NotionConnector"),
    "trello": ("connectors.project.trello", "TrelloConnector"),
}

# Catégories d'outils
# Utilisé pour savoir quel type de données fetcher
TOOL_CATEGORIES = {
    "hubspot":    "crm",
    "salesforce": "crm",
    "pipedrive":  "crm",
    "zoho":       "crm",
    "quickbooks": "finance",
    "xero":       "finance",
    "freshbooks": "finance",
    "sage":       "finance",
    "excel":      "finance",
    "stripe":     "payments",
    "gocardless": "payments",
    "gmail":      "email",
    "outlook":    "email",
    "asana":      "project",
    "notion":     "project",
    "trello":     "project",
}


def get_connector(
    tool_name: str,
    company_id: str,
    credentials: dict
) -> Optional[BaseConnector]:
    """
    Instancie et retourne le bon connecteur.

    tool_name   : nom de l'outil ("hubspot", "quickbooks", etc.)
    company_id  : UUID du client dans Kuria
    credentials : dict des credentials pour ce connecteur

    Retourne None si l'outil est inconnu.
    Ne lance jamais d'exception — log et retourne None.
    """
    entry = _CONNECTOR_MAP.get(tool_name.lower())

    if not entry:
        logger.warning(f"Connecteur inconnu : {tool_name}")
        return None

    module_path, class_name = entry

    try:
        import importlib
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        return cls(company_id, credentials)

    except (ImportError, AttributeError) as e:
        logger.error(
            f"Erreur instanciation connecteur {tool_name} : {e}"
        )
        return None


def get_connector_from_db(
    tool_name: str,
    company_id: str
) -> Optional[BaseConnector]:
    """
    Variante qui charge automatiquement les credentials depuis Supabase.
    C'est la version qu'on utilise dans le scheduler et les agents.

    Plus besoin de passer les credentials manuellement.
    """
    credentials = _load_credentials(tool_name, company_id)

    if credentials is None:
        logger.warning(
            f"Credentials introuvables pour {tool_name} "
            f"/ company {company_id}"
        )
        return None

    return get_connector(tool_name, company_id, credentials)


def get_tool_category(tool_name: str) -> str:
    """Retourne la catégorie d'un outil."""
    return TOOL_CATEGORIES.get(tool_name.lower(), "other")


def list_supported_tools() -> list[str]:
    """Retourne la liste de tous les outils supportés."""
    return list(_CONNECTOR_MAP.keys())


# ─────────────────────────────────────────
# UTILITAIRE INTERNE
# ─────────────────────────────────────────

def _load_credentials(tool_name: str, company_id: str) -> Optional[dict]:
    """Charge les credentials depuis Supabase."""
    try:
        from services.database import get_client
        client = get_client()

        result = client.table("credentials").select(
            "credentials"
        ).eq("company_id", company_id).eq(
            "tool", tool_name
        ).limit(1).execute()

        if result.data:
            return result.data[0]["credentials"]

        return None

    except Exception as e:
        logger.error(
            f"Erreur chargement credentials {tool_name} "
            f"/ {company_id} : {e}"
        )
        return None
