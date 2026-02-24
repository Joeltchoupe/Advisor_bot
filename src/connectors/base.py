# connectors/base.py

from abc import ABC, abstractmethod
from typing import Optional
from models import Deal, Contact, Invoice, Task, Expense
import logging

logger = logging.getLogger(__name__)


class BaseConnector(ABC):
    """
    Contrat que tous les connecteurs doivent respecter.

    Chaque connecteur reçoit company_id et credentials.
    Il retourne des listes de modèles Kuria normalisés.
    Il ne sait pas ce qui se passe après la normalisation.
    """

    def __init__(self, company_id: str, credentials: dict):
        """
        company_id  : l'identifiant du client dans Kuria
        credentials : les clés API / tokens pour ce connecteur
                      Format libre, chaque connecteur prend ce dont il a besoin
        """
        self.company_id = company_id
        self.credentials = credentials
        self.source_name = self._get_source_name()

    @abstractmethod
    def _get_source_name(self) -> str:
        """
        Retourne le nom du connecteur.
        Ex: "hubspot", "salesforce", "quickbooks"
        Utilisé dans connector_source sur chaque modèle.
        """
        pass

    @abstractmethod
    def connect(self) -> bool:
        """
        Vérifie que la connexion à l'API fonctionne.
        Retourne True si OK, False si KO.
        Ne lève pas d'exception : gère les erreurs en interne.
        """
        pass

    # ─────────────────────────────────────────
    # MÉTHODES DE FETCH
    # Chaque connecteur implémente celles qui sont pertinentes.
    # Un connecteur CRM n'implémente pas fetch_invoices.
    # Un connecteur finance n'implémente pas fetch_deals.
    # ─────────────────────────────────────────

    def fetch_deals(self) -> list[Deal]:
        """Récupère et normalise les deals."""
        return []

    def fetch_contacts(self) -> list[Contact]:
        """Récupère et normalise les contacts."""
        return []

    def fetch_invoices(self) -> list[Invoice]:
        """Récupère et normalise les factures."""
        return []

    def fetch_tasks(self) -> list[Task]:
        """Récupère et normalise les tâches."""
        return []

    def fetch_expenses(self) -> list[Expense]:
        """Récupère et normalise les dépenses."""
        return []

    # ─────────────────────────────────────────
    # MÉTHODES D'ÉCRITURE
    # Utilisées par l'Executor pour agir sur le monde.
    # ─────────────────────────────────────────

    def update_deal(self, raw_id: str, fields: dict) -> bool:
        """
        Met à jour un deal dans le système source.
        fields : dict des champs à modifier dans le format SOURCE
                 (pas le format Kuria)
        Retourne True si succès.
        """
        logger.warning(f"{self.source_name}.update_deal() non implémenté")
        return False

    def create_task(self, deal_raw_id: str, task_data: dict) -> bool:
        """
        Crée une tâche associée à un deal dans le système source.
        """
        logger.warning(f"{self.source_name}.create_task() non implémenté")
        return False

    def add_note(self, deal_raw_id: str, note: str) -> bool:
        """
        Ajoute une note à un deal dans le système source.
        """
        logger.warning(f"{self.source_name}.add_note() non implémenté")
        return False

    def update_contact(self, raw_id: str, fields: dict) -> bool:
        """
        Met à jour un contact dans le système source.
        """
        logger.warning(f"{self.source_name}.update_contact() non implémenté")
        return False

    # ─────────────────────────────────────────
    # UTILITAIRES COMMUNS
    # ─────────────────────────────────────────

    def _safe_float(self, value, default: float = 0.0) -> float:
        """Convertit en float sans lever d'exception."""
        try:
            return float(value) if value is not None else default
        except (ValueError, TypeError):
            return default

    def _safe_int(self, value, default: int = 0) -> int:
        """Convertit en int sans lever d'exception."""
        try:
            return int(value) if value is not None else default
        except (ValueError, TypeError):
            return default

    def _safe_str(self, value, default: str = "") -> str:
        """Convertit en str sans lever d'exception."""
        if value is None:
            return default
        return str(value).strip()
