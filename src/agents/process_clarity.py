# agents/process_clarity.py

from datetime import datetime, timedelta
from typing import Optional
import logging
import statistics

from agents.base import BaseAgent, AgentRunResult
from services.executor import executor, Action, ActionLevel
from services.notification import notify_commercial, alert_ceo
from services.database import get_client

logger = logging.getLogger(__name__)


class ProcessClarityAgent(BaseAgent):

    def _get_name(self) -> str:
        return "process_clarity"

    def _run(self) -> AgentRunResult:
        started_at = datetime.utcnow()
        actions_taken = []
        errors = []

        tasks = self._get_tasks()

        if not tasks:
            return AgentRunResult(
                agent=self.name,
                company_id=self.company_id,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                kpi_name="avg_cycle_time_days",
                kpi_value=0.0
            )

        # ─────────────────────────────────────────
        # ACTION 2.4 — SUIVI DES DEADLINES
        # ─────────────────────────────────────────

        deadline_actions = self._process_deadlines(tasks)
        actions_taken.extend(deadline_actions)

        # ─────────────────────────────────────────
        # ACTION 2.3 — ROUTING INTELLIGENT
        # ─────────────────────────────────────────

        routing_actions = self._route_unassigned_tasks(tasks)
        actions_taken.extend(routing_actions)

        # ─────────────────────────────────────────
        # KPI — CYCLE TIME MOYEN
        # ─────────────────────────────────────────

        avg_cycle_time = self._compute_avg_cycle_time(tasks)

        # Sauvegarder les métriques
        self._save_process_metrics(avg_cycle_time, tasks)

        return AgentRunResult(
            agent=self.name,
            company_id=self.company_id,
            started_at=started_at,
            finished_at=datetime.utcnow(),
            actions_taken=actions_taken,
            kpi_value=round(avg_cycle_time, 2),
            kpi_name="avg_cycle_time_days",
            errors=errors
        )

    # ─────────────────────────────────────────
    # 2.4 — SUIVI DEADLINES
    # ─────────────────────────────────────────

    def _process_deadlines(self, tasks: list) -> list[dict]:
        """
        Envoie des notifications pour les tâches
        approchant leur deadline ou en retard.

        Logique :
        → 48h avant deadline → rappel à l'assigné
        → Deadline passée → alerte manager
        → 3j de retard → escalade
        """
        actions_taken = []
        now = datetime.utcnow()

        company = self._get_company()
        manager_email = company.get("agent_configs", {}).get(
            "process_clarity", {}
        ).get("manager_email", "")

        for task in tasks:
            if task.get("status") == "done":
                continue

            due_at = self._parse_date(task.get("due_at"))
            if not due_at:
                continue

            days_until_due = (due_at - now).days
            days_overdue = (now - due_at).days if due_at < now else -1

            assignee_email = self._get_assignee_email(
                task.get("assignee_id")
            )

            # 48h avant deadline
            if 0 <= days_until_due <= 2 and assignee_email:
                if not self._reminder_sent_recently(task["id"], "deadline_warning"):
                    self._send_task_notification(
                        to=assignee_email,
                        subject=f"Tâche due dans {days_until_due*24:.0f}h : {task.get('title')}",
                        message=(
                            f"La tâche '{task.get('title')}' "
                            f"est due {'demain' if days_until_due == 1 else \"aujourd'hui\"}.\n"
                            f"Statut actuel : {task.get('status')}"
                        ),
                        task_id=task["id"],
                        reminder_type="deadline_warning"
                    )
                    actions_taken.append({
                        "action": "deadline_warning",
                        "task_id": task.get("id"),
                        "task_title": task.get("title"),
                        "days_until_due": days_until_due
                    })

            # En retard (1-3 jours)
            elif 1 <= days_overdue <= 3:
                if not self._reminder_sent_recently(task["id"], "overdue_alert"):
                    if assignee_email:
                        self._send_task_notification(
                            to=assignee_email,
                            subject=f"Tâche en retard : {task.get('title')}",
                            message=(
                                f"La tâche '{task.get('title')}' "
                                f"est en retard de {days_overdue} jour(s).\n"
                                f"Merci de la mettre à jour ou de signaler un blocage."
                            ),
                            task_id=task["id"],
                            reminder_type="overdue_alert"
                        )

                    actions_taken.append({
                        "action": "overdue_alert",
                        "task_id": task.get("id"),
                        "task_title": task.get("title"),
                        "days_overdue": days_overdue
                    })

                    # Mettre à jour le statut dans Supabase
                    try:
                        client = get_client()
                        client.table("tasks").update({
                            "status": "overdue"
                        }).eq("id", task["id"]).execute()
                    except Exception as e:
                        logger.error(f"Erreur update task status : {e}")

            # Retard > 3 jours → escalade manager
            elif days_overdue > 3 and manager_email:
                if not self._reminder_sent_recently(
                    task["id"], "escalation", cooldown_days=3
                ):
                    self._send_task_notification(
                        to=manager_email,
                        subject=f"Escalade — Tâche en retard {days_overdue}j : {task.get('title')}",
                        message=(
                            f"La tâche '{task.get('title')}' est en retard "
                            f"de {days_overdue} jours.\n"
                            f"Assigné à : {task.get('assignee_name', 'N/A')}\n"
                            f"Impact potentiel sur le cycle time."
                        ),
                        task_id=task["id"],
                        reminder_type="escalation"
                    )
                    actions_taken.append({
                        "action": "task_escalation",
                        "task_id": task.get("id"),
                        "days_overdue": days_overdue
                    })

        return actions_taken

    # ─────────────────────────────────────────
    # 2.3 — ROUTING DES TÂCHES
    # ─────────────────────────────────────────

    def _route_unassigned_tasks(self, tasks: list) -> list[dict]:
        """
        Assigne les tâches sans assigné à la personne
        la moins chargée qui a l'expertise requise.
        """
        actions_taken = []

        unassigned = [
            t for t in tasks
            if not t.get("assignee_id")
            and t.get("status") not in ("done",)
        ]

        if not unassigned:
            return []

        # Calculer la charge actuelle par personne
        workload = self._compute_workload(tasks)

        for task in unassigned:
            # Trouver la personne la moins chargée
            best_assignee = self._find_best_assignee(workload)
            if not best_assignee:
                continue

            # Assigner dans Supabase
            try:
                client = get_client()
                client.table("tasks").update({
                    "assignee_id": best_assignee["id"],
                    "assignee_name": best_assignee["name"]
                }).eq("id", task["id"]).execute()

                # Mettre à jour la charge locale
                assignee_id = best_assignee["id"]
                if assignee_id in workload:
                    workload[assignee_id]["active_tasks"] += 1

                actions_taken.append({
                    "action": "task_assigned",
                    "task_id": task.get("id"),
                    "task_title": task.get("title"),
                    "assignee": best_assignee["name"]
                })

                # Notifier l'assigné
                assignee_email = best_assignee.get("email", "")
                if assignee_email:
                    notify_commercial(
                        name=best_assignee["name"],
                        email=assignee_email,
                        subject=f"Nouvelle tâche assignée : {task.get('title')}",
                        message=(
                            f"Kuria vous a assigné la tâche : "
                            f"'{task.get('title')}'\n"
                            f"Raison : vous avez la charge la plus légère "
                            f"de l'équipe ({workload[assignee_id]['active_tasks'] - 1} tâches actives)."
                        )
                    )

            except Exception as e:
                logger.error(f"Erreur routing task {task.get('id')} : {e}")

        return actions_taken

    def _compute_workload(self, tasks: list) -> dict:
        """
        Retourne {assignee_id: {name, active_tasks, email}}
        pour toutes les personnes avec des tâches actives.
        """
        workload: dict[str, dict] = {}

        active_tasks = [
            t for t in tasks
            if t.get("status") not in ("done",)
            and t.get("assignee_id")
        ]

        for task in active_tasks:
            assignee_id = task["assignee_id"]
            if assignee_id not in workload:
                workload[assignee_id] = {
                    "id": assignee_id,
                    "name": task.get("assignee_name", ""),
                    "email": self._get_assignee_email(assignee_id),
                    "active_tasks": 0
                }
            workload[assignee_id]["active_tasks"] += 1

        return workload

    def _find_best_assignee(self, workload: dict) -> Optional[dict]:
        """Retourne la personne avec le moins de tâches actives."""
        if not workload:
            return None
        return min(workload.values(), key=lambda w: w["active_tasks"])

    # ─────────────────────────────────────────
    # KPI — CYCLE TIME
    # ─────────────────────────────────────────

    def _compute_avg_cycle_time(self, tasks: list) -> float:
        """
        Cycle time moyen = moyenne des (completed_at - created_at)
        pour les tâches terminées dans les 30 derniers jours.
        """
        now = datetime.utcnow()
        thirty_days_ago = now - timedelta(days=30)

        completed_tasks = [
            t for t in tasks
            if t.get("status") == "done"
            and t.get("cycle_time_days") is not None
            and self._parse_date(t.get("completed_at")) >= thirty_days_ago
        ]

        if not completed_tasks:
            return 0.0

        cycle_times = [
            float(t["cycle_time_days"])
            for t in completed_tasks
        ]

        return statistics.mean(cycle_times)

    def _save_process_metrics(self, avg_cycle_time: float, tasks: list) -> None:
        """Sauvegarde les métriques process dans Supabase."""
        now = datetime.utcnow()
        active = [t for t in tasks if t.get("status") not in ("done",)]
        overdue = [t for t in tasks if t.get("status") == "overdue"]
        unassigned = [
            t for t in active if not t.get("assignee_id")
        ]

        try:
            client = get_client()
            client.table("process_metrics").upsert({
                "company_id": self.company_id,
                "computed_at": now.isoformat(),
                "avg_cycle_time_days": avg_cycle_time,
                "active_tasks_count": len(active),
                "overdue_tasks_count": len(overdue),
                "unassigned_tasks_count": len(unassigned)
            }, on_conflict="company_id").execute()
        except Exception as e:
            logger.error(f"Erreur save process metrics : {e}")

    # ─────────────────────────────────────────
    # UTILITAIRES
    # ─────────────────────────────────────────

    def _send_task_notification(
        self,
        to: str,
        subject: str,
        message: str,
        task_id: str,
        reminder_type: str
    ) -> None:
        from services.notification import send_email
        action = Action(
            type=f"task_notification_{reminder_type}",
            level=ActionLevel.A,
            company_id=self.company_id,
            agent=self.name,
            payload={"task_id": task_id, "reminder_type": reminder_type}
        )
        executor.run(action, send_email, to=to, subject=subject, body=message)

        # Logger le rappel
        try:
            client = get_client()
            client.table("task_reminders").insert({
                "company_id": self.company_id,
                "task_id": task_id,
                "reminder_type": reminder_type,
                "sent_at": datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            logger.error(f"Erreur log task reminder : {e}")

    def _reminder_sent_recently(
        self,
        task_id: str,
        reminder_type: str,
        cooldown_days: int = 1
    ) -> bool:
        """Vérifie si on a déjà envoyé ce type de rappel récemment."""
        try:
            client = get_client()
            cutoff = (
                datetime.utcnow() - timedelta(days=cooldown_days)
            ).isoformat()

            result = client.table("task_reminders").select("id").eq(
                "company_id", self.company_id
            ).eq("task_id", task_id).eq(
                "reminder_type", reminder_type
            ).gte("sent_at", cutoff).limit(1).execute()

            return bool(result.data)
        except Exception:
            return False

    def _get_assignee_email(self, assignee_id: str) -> Optional[str]:
        if not assignee_id:
            return None
        try:
            client = get_client()
            result = client.table("team_members").select("email").eq(
                "company_id", self.company_id
            ).eq("tool_user_id", assignee_id).limit(1).execute()
            return result.data[0].get("email") if result.data else None
        except Exception:
            return None

    def _parse_date(self, value) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
