# connectors/base.py

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional
from models import Deal, Contact, Invoice, Task, Expense
import logging
import requests

logger = logging.getLogger(__name__)


class BaseConnector(ABC):
    """
    Contrat que tous les connecteurs respectent.

    Nouveautés vs V1 :
    → refresh_access_token() : hook pour renouveler le token avant chaque fetch
    → _is_token_expired()    : vérifie si le token est encore valide
    → Les credentials incluent refresh_token et expires_at
    """

    def __init__(self, company_id: str, credentials: dict):
        self.company_id   = company_id
        self.credentials  = credentials
        self.source_name  = self._get_source_name()

    @abstractmethod
    def _get_source_name(self) -> str:
        pass

    @abstractmethod
    def connect(self) -> bool:
        pass

    # ─────────────────────────────────────────
    # TOKEN REFRESH
    # ─────────────────────────────────────────

    def ensure_valid_token(self) -> bool:
        """
        Appelé avant chaque fetch.
        Si le token est expiré ou proche de l'expiration,
        on le rafraîchit avant de continuer.

        Retourne True si le token est valide (ou rafraîchi avec succès).
        Retourne False si on ne peut pas obtenir un token valide.
        """
        if not self._needs_refresh():
            return True

        logger.info(
            f"[{self.source_name}] Token expiré ou proche — "
            f"rafraîchissement en cours"
        )

        success = self.refresh_access_token()

        if success:
            logger.info(f"[{self.source_name}] Token rafraîchi avec succès")
        else:
            logger.error(f"[{self.source_name}] Échec du rafraîchissement du token")

        return success

    def _needs_refresh(self) -> bool:
        """
        Vérifie si le token a besoin d'être rafraîchi.

        Logique :
        → Pas d'expires_at → on ne sait pas → on rafraîchit par précaution
        → expires_at dans moins de 5 minutes → on rafraîchit
        → expires_at dans plus de 5 minutes → on ne rafraîchit pas
        """
        expires_at = self.credentials.get("expires_at")

        if not expires_at:
            # Pas d'info d'expiration
            # Si on a un refresh_token, on peut essayer
            # Sinon, on suppose que le token est valide
            return bool(self.credentials.get("refresh_token"))

        try:
            if isinstance(expires_at, str):
                expiry = datetime.fromisoformat(
                    expires_at.replace("Z", "+00:00")
                )
            elif isinstance(expires_at, (int, float)):
                expiry = datetime.fromtimestamp(expires_at, tz=timezone.utc)
            else:
                return False

            now = datetime.now(tz=timezone.utc)
            margin = 300    # 5 minutes de marge

            return (expiry - now).total_seconds() < margin

        except (ValueError, TypeError):
            return False

    def refresh_access_token(self) -> bool:
        """
        Rafraîchit le token OAuth.
        Chaque connecteur qui utilise OAuth doit implémenter cette méthode.

        Doit :
        1. Appeler l'endpoint de refresh du provider
        2. Mettre à jour self.credentials avec le nouveau token
        3. Sauvegarder les nouveaux credentials dans Supabase
        4. Retourner True si succès, False si échec

        Par défaut : retourne True (connecteurs sans OAuth n'ont pas besoin)
        """
        return True

    def _save_refreshed_credentials(self, new_credentials: dict) -> None:
        """
        Sauvegarde les credentials rafraîchis dans Supabase.
        Appelé par les connecteurs après un refresh réussi.
        """
        try:
            from services.database import get_client
            client = get_client()

            client.table("credentials").update({
                "credentials": new_credentials
            }).eq("company_id", self.company_id).eq(
                "tool", self.source_name
            ).execute()

            # Mettre à jour les credentials en mémoire
            self.credentials = new_credentials

            logger.info(
                f"[{self.source_name}] Credentials sauvegardés "
                f"pour company {self.company_id}"
            )

        except Exception as e:
            logger.error(
                f"[{self.source_name}] Erreur sauvegarde credentials : {e}"
            )

    # ─────────────────────────────────────────
    # FETCH (avec ensure_valid_token automatique)
    # ─────────────────────────────────────────

    def fetch_deals(self) -> list[Deal]:
        if not self.ensure_valid_token():
            logger.error(f"[{self.source_name}] Token invalide — fetch_deals annulé")
            return []
        return []

    def fetch_contacts(self) -> list[Contact]:
        if not self.ensure_valid_token():
            logger.error(f"[{self.source_name}] Token invalide — fetch_contacts annulé")
            return []
        return []

    def fetch_invoices(self) -> list[Invoice]:
        if not self.ensure_valid_token():
            logger.error(f"[{self.source_name}] Token invalide — fetch_invoices annulé")
            return []
        return []

    def fetch_tasks(self) -> list[Task]:
        if not self.ensure_valid_token():
            logger.error(f"[{self.source_name}] Token invalide — fetch_tasks annulé")
            return []
        return []

    def fetch_expenses(self) -> list[Expense]:
        if not self.ensure_valid_token():
            logger.error(f"[{self.source_name}] Token invalide — fetch_expenses annulé")
            return []
        return []

    # ─────────────────────────────────────────
    # ÉCRITURE
    # ─────────────────────────────────────────

    def update_deal(self, raw_id: str, fields: dict) -> bool:
        logger.warning(f"{self.source_name}.update_deal() non implémenté")
        return False

    def create_task(self, deal_raw_id: str, task_data: dict) -> bool:
        logger.warning(f"{self.source_name}.create_task() non implémenté")
        return False

    def add_note(self, deal_raw_id: str, note: str) -> bool:
        logger.warning(f"{self.source_name}.add_note() non implémenté")
        return False

    def update_contact(self, raw_id: str, fields: dict) -> bool:
        logger.warning(f"{self.source_name}.update_contact() non implémenté")
        return False

    # ─────────────────────────────────────────
    # UTILITAIRES COMMUNS
    # ─────────────────────────────────────────

    def _safe_float(self, value, default: float = 0.0) -> float:
        try:
            return float(value) if value is not None else default
        except (ValueError, TypeError):
            return default

    def _safe_int(self, value, default: int = 0) -> int:
        try:
            return int(value) if value is not None else default
        except (ValueError, TypeError):
            return default

    def _safe_str(self, value, default: str = "") -> str:
        if value is None:
            return default
        return str(value).strip()

    def _parse_datetime(self, value) -> Optional[datetime]:
        """
        Parse universelle des dates.
        Gère ISO, timestamp ms, formats communs.
        Centralisé ici pour tous les connecteurs.
        """
        if not value:
            return None

        try:
            if isinstance(value, datetime):
                return value

            if isinstance(value, (int, float)):
                # Timestamp millisecondes (HubSpot)
                if value > 1e10:
                    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
                # Timestamp secondes
                return datetime.fromtimestamp(value, tz=timezone.utc)

            s = str(value).strip()

            # Format bizarre Xero : /Date(1742123400000+0000)/
            if s.startswith("/Date("):
                ms = int(s.replace("/Date(", "").split("+")[0].split("-")[0])
                return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

            # ISO 8601 avec Z
            s = s.replace("Z", "+00:00")

            # ISO complet
            if "T" in s:
                return datetime.fromisoformat(s)

            # Date seule : YYYY-MM-DD
            if len(s) == 10 and s[4] == "-":
                return datetime.strptime(s, "%Y-%m-%d")

            # Date Pipedrive : YYYY-MM-DD HH:MM:SS
            if len(s) == 19 and s[4] == "-":
                return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")

            return None

        except (ValueError, TypeError, OSError):
            return None
