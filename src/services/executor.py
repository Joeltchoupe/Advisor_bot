# services/executor.py

import time
import logging
from datetime import datetime
from typing import Any, Callable, Optional
from dataclasses import dataclass, field
from enum import Enum

from services.database import save, get_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# STRUCTURES
# ─────────────────────────────────────────

class ActionLevel(str, Enum):
    A = "A"    # autonome  : l'agent fait, l'humain voit après
    B = "B"    # supervisé : l'agent prépare, l'humain valide
    C = "C"    # assisté   : l'agent brief, l'humain agit


class ActionStatus(str, Enum):
    PENDING    = "pending"      # niveau B en attente de validation
    RUNNING    = "running"      # en cours d'exécution
    SUCCESS    = "success"      # terminée avec succès
    FAILED     = "failed"       # terminée en échec
    CANCELLED  = "cancelled"    # annulée par l'humain (niveau B refusé)


@dataclass
class Action:
    """
    Ce qu'un agent soumet à l'executor.
    """
    type: str                          # ex: "tag_deal_zombie", "send_email"
    level: ActionLevel                 # A, B, ou C
    company_id: str
    agent: str                         # quel agent a créé cette action
    payload: dict                      # les données nécessaires à l'exécution

    # Optionnel : description lisible pour le dashboard (niveau B)
    description: str = ""
    # Optionnel : données de preview pour l'humain (niveau B)
    preview: dict = field(default_factory=dict)


@dataclass
class ActionResult:
    """
    Ce que l'executor retourne après exécution.
    """
    action_type: str
    status: ActionStatus
    executed_at: datetime
    result: dict = field(default_factory=dict)
    error: str = ""
    attempts: int = 1


# ─────────────────────────────────────────
# EXECUTOR
# ─────────────────────────────────────────

class Executor:
    """
    Point unique d'exécution de toute action qui touche le monde.

    Responsabilités :
    1. Retry avec backoff exponentiel
    2. Log de chaque action dans Supabase
    3. Gestion des actions niveau B (file d'attente + validation)
    4. Jamais de crash silencieux
    """

    MAX_ATTEMPTS = 3
    RETRY_DELAYS = [1, 3, 9]    # secondes entre les tentatives

    def run(
        self,
        action: Action,
        fn: Callable,
        *args,
        **kwargs
    ) -> ActionResult:
        """
        Point d'entrée principal.

        action : la description de l'action
        fn     : la fonction qui exécute réellement l'action
                 ex: hubspot_connector.update_deal
        args   : arguments positionnels pour fn
        kwargs : arguments nommés pour fn

        Usage :
            result = executor.run(
                action=Action(
                    type="tag_deal_zombie",
                    level=ActionLevel.A,
                    company_id="abc",
                    agent="revenue_velocity",
                    payload={"deal_id": "123", "tag": "zombie"}
                ),
                fn=hubspot.update_deal,
                raw_id="123",
                fields={"axio_status": "zombie"}
            )
        """

        # Les actions niveau B vont dans la file d'attente
        # Elles ne s'exécutent pas immédiatement
        if action.level == ActionLevel.B:
            return self._queue_for_approval(action)

        # Les actions niveau C sont juste loggées
        # L'humain agit lui-même
        if action.level == ActionLevel.C:
            return self._log_assisted_action(action)

        # Niveau A : on exécute
        return self._execute_with_retry(action, fn, *args, **kwargs)

    # ─────────────────────────────────────────
    # EXÉCUTION NIVEAU A
    # ─────────────────────────────────────────

    def _execute_with_retry(
        self,
        action: Action,
        fn: Callable,
        *args,
        **kwargs
    ) -> ActionResult:
        """
        Exécute fn avec retry et backoff exponentiel.
        Log le résultat dans tous les cas.
        """
        last_error = ""

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                logger.info(
                    f"[{action.agent}] {action.type} — tentative {attempt}/{self.MAX_ATTEMPTS}"
                )

                result = fn(*args, **kwargs)

                # Succès
                action_result = ActionResult(
                    action_type=action.type,
                    status=ActionStatus.SUCCESS,
                    executed_at=datetime.utcnow(),
                    result=result if isinstance(result, dict) else {"value": result},
                    attempts=attempt
                )
                self._log_action(action, action_result)
                return action_result

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"[{action.agent}] {action.type} — échec tentative {attempt}: {e}"
                )

                if attempt < self.MAX_ATTEMPTS:
                    delay = self.RETRY_DELAYS[attempt - 1]
                    logger.info(f"Retry dans {delay}s...")
                    time.sleep(delay)

        # Toutes les tentatives ont échoué
        action_result = ActionResult(
            action_type=action.type,
            status=ActionStatus.FAILED,
            executed_at=datetime.utcnow(),
            error=last_error,
            attempts=self.MAX_ATTEMPTS
        )
        self._log_action(action, action_result)

        logger.error(
            f"[{action.agent}] {action.type} — ÉCHEC définitif après "
            f"{self.MAX_ATTEMPTS} tentatives : {last_error}"
        )

        return action_result

    # ─────────────────────────────────────────
    # FILE D'ATTENTE NIVEAU B
    # ─────────────────────────────────────────

    def _queue_for_approval(self, action: Action) -> ActionResult:
        """
        Les actions niveau B ne s'exécutent pas.
        Elles sont sauvegardées en base avec status=PENDING.
        Le dashboard les affiche.
        L'humain clique ✅ ou ❌.
        """
        record = {
            "action_type": action.type,
            "level": action.level.value,
            "company_id": action.company_id,
            "agent": action.agent,
            "payload": action.payload,
            "description": action.description,
            "preview": action.preview,
            "status": ActionStatus.PENDING.value,
            "created_at": datetime.utcnow().isoformat()
        }

        try:
            client = get_client()
            result = client.table("pending_actions").insert(record).execute()
            action_id = result.data[0]["id"] if result.data else "unknown"

            logger.info(
                f"[{action.agent}] {action.type} — en attente de validation "
                f"(id: {action_id})"
            )

        except Exception as e:
            logger.error(f"Erreur queue pending_action : {e}")

        return ActionResult(
            action_type=action.type,
            status=ActionStatus.PENDING,
            executed_at=datetime.utcnow(),
            result={"queued": True, "description": action.description}
        )

    def approve(self, pending_action_id: str, fn: Callable, *args, **kwargs) -> ActionResult:
        """
        Appelé par l'API quand l'humain clique ✅ dans le dashboard.
        Transforme l'action pending en action A et l'exécute.
        """
        # Récupérer l'action en attente
        client = get_client()
        result = client.table("pending_actions").select("*").eq(
            "id", pending_action_id
        ).execute()

        if not result.data:
            logger.error(f"Pending action introuvable : {pending_action_id}")
            return ActionResult(
                action_type="unknown",
                status=ActionStatus.FAILED,
                executed_at=datetime.utcnow(),
                error="Action introuvable"
            )

        raw = result.data[0]

        # Reconstruire l'Action
        action = Action(
            type=raw["action_type"],
            level=ActionLevel.A,    # on l'exécute maintenant
            company_id=raw["company_id"],
            agent=raw["agent"],
            payload=raw["payload"],
            description=raw["description"]
        )

        # Marquer comme running
        client.table("pending_actions").update(
            {"status": ActionStatus.RUNNING.value}
        ).eq("id", pending_action_id).execute()

        # Exécuter
        action_result = self._execute_with_retry(action, fn, *args, **kwargs)

        # Mettre à jour le statut dans pending_actions
        client.table("pending_actions").update({
            "status": action_result.status.value,
            "executed_at": action_result.executed_at.isoformat(),
            "result": action_result.result
        }).eq("id", pending_action_id).execute()

        return action_result

    def reject(self, pending_action_id: str) -> None:
        """
        Appelé quand l'humain clique ❌.
        """
        client = get_client()
        client.table("pending_actions").update({
            "status": ActionStatus.CANCELLED.value,
            "executed_at": datetime.utcnow().isoformat()
        }).eq("id", pending_action_id).execute()

        logger.info(f"Action annulée par l'humain : {pending_action_id}")

    # ─────────────────────────────────────────
    # NIVEAU C
    # ─────────────────────────────────────────

    def _log_assisted_action(self, action: Action) -> ActionResult:
        """
        Niveau C : l'agent prépare un brief.
        On log le brief pour que l'humain le trouve dans le dashboard.
        """
        record = {
            "action_type": action.type,
            "level": action.level.value,
            "company_id": action.company_id,
            "agent": action.agent,
            "payload": action.payload,
            "description": action.description,
            "preview": action.preview,
            "status": ActionStatus.PENDING.value,
            "created_at": datetime.utcnow().isoformat()
        }

        try:
            client = get_client()
            client.table("pending_actions").insert(record).execute()
        except Exception as e:
            logger.error(f"Erreur log assisted action : {e}")

        return ActionResult(
            action_type=action.type,
            status=ActionStatus.PENDING,
            executed_at=datetime.utcnow(),
            result={"brief_ready": True}
        )

    # ─────────────────────────────────────────
    # LOGGING
    # ─────────────────────────────────────────

    def _log_action(self, action: Action, result: ActionResult) -> None:
        """
        Toute action exécutée est loggée dans Supabase.
        C'est ce qui permet de répondre à :
        "Qu'est-ce que Kuria a fait cette semaine ?"
        """
        record = {
            "action_type": action.type,
            "level": action.level.value,
            "company_id": action.company_id,
            "agent": action.agent,
            "payload": action.payload,
            "status": result.status.value,
            "result": result.result,
            "error": result.error,
            "attempts": result.attempts,
            "executed_at": result.executed_at.isoformat()
        }

        try:
            client = get_client()
            client.table("action_logs").insert(record).execute()
        except Exception as e:
            # Le log ne doit jamais crasher l'action principale
            logger.error(f"Erreur logging action : {e}")


# ─────────────────────────────────────────
# INSTANCE PARTAGÉE
# ─────────────────────────────────────────

# Un seul executor dans tout le système
executor = Executor()
