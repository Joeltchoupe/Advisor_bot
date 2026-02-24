# agents/cash_predictability.py

from datetime import datetime, timedelta
from typing import Optional
import logging
import statistics

from agents.base import BaseAgent, AgentRunResult
from services.executor import executor, Action, ActionLevel
from services.llm import draft
from services.notification import alert_ceo
from services.database import get_client, get
from prompts import invoice_reminder_email

logger = logging.getLogger(__name__)


class CashPredictabilityAgent(BaseAgent):

    def _get_name(self) -> str:
        return "cash_predictability"

    def _run(self) -> AgentRunResult:
        started_at = datetime.utcnow()
        actions_taken = []
        errors = []

        invoices = self._get_invoices()
        expenses = self._get_expenses()

        if not invoices and not expenses:
            return AgentRunResult(
                agent=self.name,
                company_id=self.company_id,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                kpi_name="cash_forecast_accuracy_pct",
                kpi_value=0.0
            )

        # ─────────────────────────────────────────
        # ACTION 3.2 — PATTERNS DE PAIEMENT
        # En premier : utilisés par le forecast
        # ─────────────────────────────────────────

        payment_patterns = self._compute_payment_patterns(invoices)
        actions_taken.append({
            "action": "compute_payment_patterns",
            "clients_analyzed": len(payment_patterns)
        })

        # ─────────────────────────────────────────
        # ACTION 3.6 — FORECAST CASH CONTINU
        # ─────────────────────────────────────────

        forecast_result = self._compute_cash_forecast(
            invoices, expenses, payment_patterns
        )
        self._save_cash_forecast(forecast_result)

        actions_taken.append({
            "action": "compute_cash_forecast",
            "cash_30d_base": forecast_result["base_30d"],
            "cash_30d_stress": forecast_result["stress_30d"],
            "runway_months": forecast_result["runway_months"]
        })

        # Publier l'event pour les autres agents
        self._publish("cash_forecast_updated", {
            "base_30d": forecast_result["base_30d"],
            "stress_30d": forecast_result["stress_30d"],
            "runway_months": forecast_result["runway_months"],
            "days_until_critical": forecast_result.get("days_until_critical")
        })

        # ─────────────────────────────────────────
        # ACTION 3.1 — RELANCES FACTURES
        # ─────────────────────────────────────────

        reminder_actions = self._process_overdue_invoices(invoices)
        actions_taken.extend(reminder_actions)

        # ─────────────────────────────────────────
        # ALERTE CEO si nécessaire
        # ─────────────────────────────────────────

        days_critical = forecast_result.get("days_until_critical")
        if days_critical and days_critical < 60:
            self._send_cash_alert(forecast_result)
            actions_taken.append({
                "action": "cash_alert_sent",
                "days_until_critical": days_critical
            })

        # Précision du forecast (KPI)
        accuracy = self._compute_forecast_accuracy()

        return AgentRunResult(
            agent=self.name,
            company_id=self.company_id,
            started_at=started_at,
            finished_at=datetime.utcnow(),
            actions_taken=actions_taken,
            kpi_value=round(accuracy, 3),
            kpi_name="cash_forecast_accuracy_pct",
            errors=errors
        )

    # ─────────────────────────────────────────
    # 3.2 — PATTERNS DE PAIEMENT
    # ─────────────────────────────────────────

    def _compute_payment_patterns(self, invoices: list) -> dict:
        """
        Pour chaque client, calcule son délai de paiement moyen.
        Retourne {client_id: avg_delay_days}
        """
        paid_invoices = [
            i for i in invoices
            if i.get("status") == "paid"
            and i.get("payment_delay_days") is not None
        ]

        delays_by_client: dict[str, list] = {}
        for invoice in paid_invoices:
            client_id = invoice.get("client_id", "unknown")
            delay = invoice.get("payment_delay_days", 0)
            if client_id not in delays_by_client:
                delays_by_client[client_id] = []
            delays_by_client[client_id].append(delay)

        patterns = {}
        for client_id, delays in delays_by_client.items():
            if len(delays) >= 2:
                patterns[client_id] = {
                    "avg_delay_days": round(statistics.mean(delays), 1),
                    "trend": self._compute_trend(delays),
                    "sample_size": len(delays)
                }

        return patterns

    def _compute_trend(self, values: list) -> str:
        """
        Calcule si les valeurs augmentent, diminuent ou sont stables.
        Utilisé pour détecter les clients qui dégradent leur paiement.
        """
        if len(values) < 3:
            return "stable"

        recent = statistics.mean(values[-3:])
        older  = statistics.mean(values[:-3]) if len(values) > 3 else values[0]

        if recent > older * 1.2:
            return "degrading"     # paie de plus en plus tard
        elif recent < older * 0.8:
            return "improving"
        return "stable"

    # ─────────────────────────────────────────
    # 3.6 — FORECAST CASH
    # ─────────────────────────────────────────

    def _compute_cash_forecast(
        self,
        invoices: list,
        expenses: list,
        payment_patterns: dict
    ) -> dict:
        """
        Forecast 30/60/90 jours avec 3 scénarios.

        BASE   : encaissements au timing réel (patterns)
        STRESS : encaissements retardés de 15j + pipeline × 0.5
        UPSIDE : encaissements accélérés + pipeline × 1.2
        """
        now = datetime.utcnow()

        # Récupérer le forecast pipeline (de Revenue Velocity)
        pipeline_forecast = self._get_pipeline_forecast()

        # Cash actuel (approximation : factures payées - dépenses)
        total_received = sum(
            float(i.get("amount_paid") or 0)
            for i in invoices
            if i.get("status") == "paid"
        )
        total_expenses = sum(
            float(e.get("amount") or 0)
            for e in expenses
        )

        # Factures en attente (envoyées, pas encore payées)
        pending_invoices = [
            i for i in invoices
            if i.get("status") in ("sent", "overdue")
        ]

        # Dépenses récurrentes mensuelles estimées
        monthly_recurring = self._estimate_monthly_recurring(expenses)

        # ── SCÉNARIO BASE ──
        base_30d = self._forecast_scenario(
            pending_invoices, payment_patterns,
            pipeline_forecast.get("forecast_30d", 0),
            monthly_recurring, horizon_days=30,
            pipeline_factor=1.0, delay_factor=0
        )

        base_60d = self._forecast_scenario(
            pending_invoices, payment_patterns,
            pipeline_forecast.get("forecast_60d", 0),
            monthly_recurring, horizon_days=60,
            pipeline_factor=1.0, delay_factor=0
        )

        base_90d = self._forecast_scenario(
            pending_invoices, payment_patterns,
            pipeline_forecast.get("forecast_90d", 0),
            monthly_recurring, horizon_days=90,
            pipeline_factor=1.0, delay_factor=0
        )

        # ── SCÉNARIO STRESS ──
        stress_30d = self._forecast_scenario(
            pending_invoices, payment_patterns,
            pipeline_forecast.get("forecast_30d", 0),
            monthly_recurring, horizon_days=30,
            pipeline_factor=0.5, delay_factor=15
        )

        # ── SCÉNARIO UPSIDE ──
        upside_30d = self._forecast_scenario(
            pending_invoices, payment_patterns,
            pipeline_forecast.get("forecast_30d", 0),
            monthly_recurring, horizon_days=30,
            pipeline_factor=1.2, delay_factor=-5
        )

        # Seuil critique (depuis la config ou 30 jours de dépenses)
        critical_threshold = self._cfg(
            "cash_critical_threshold",
            monthly_recurring * 1.5
        )

        # Jours avant le seuil critique (scénario stress)
        days_until_critical = None
        if stress_30d < critical_threshold:
            days_until_critical = 30
        elif base_60d < critical_threshold:
            days_until_critical = 45

        # Runway = mois restants au rythme actuel
        monthly_burn = monthly_recurring
        runway_months = (base_30d / monthly_burn) if monthly_burn > 0 else 99

        return {
            "base_30d": round(base_30d, 2),
            "base_60d": round(base_60d, 2),
            "base_90d": round(base_90d, 2),
            "stress_30d": round(stress_30d, 2),
            "upside_30d": round(upside_30d, 2),
            "monthly_burn": round(monthly_recurring, 2),
            "runway_months": round(runway_months, 1),
            "days_until_critical": days_until_critical,
            "critical_threshold": round(critical_threshold, 2),
            "computed_at": now.isoformat()
        }

    def _forecast_scenario(
        self,
        pending_invoices: list,
        payment_patterns: dict,
        pipeline_amount: float,
        monthly_recurring: float,
        horizon_days: int,
        pipeline_factor: float,
        delay_factor: int
    ) -> float:
        """
        Calcule le cash position à horizon_days jours
        pour un scénario donné.
        """
        now = datetime.utcnow()
        horizon = now + timedelta(days=horizon_days)

        # Encaissements attendus des factures en cours
        expected_receipts = 0.0
        for invoice in pending_invoices:
            client_id = invoice.get("client_id", "unknown")
            due_date = self._parse_date(invoice.get("due_at"))
            amount = float(invoice.get("amount") or 0)

            if not due_date:
                continue

            # Ajustement selon le pattern de paiement du client
            pattern = payment_patterns.get(client_id, {})
            avg_delay = pattern.get("avg_delay_days", 0) + delay_factor
            expected_payment_date = due_date + timedelta(days=int(avg_delay))

            if expected_payment_date <= horizon:
                expected_receipts += amount

        # Dépenses prévues
        expected_expenses = monthly_recurring * (horizon_days / 30)

        # Pipeline (deals qui devraient closer)
        pipeline_contribution = pipeline_amount * pipeline_factor

        return expected_receipts + pipeline_contribution - expected_expenses

    def _estimate_monthly_recurring(self, expenses: list) -> float:
        """
        Estime les dépenses récurrentes mensuelles
        depuis l'historique des 3 derniers mois.
        """
        now = datetime.utcnow()
        three_months_ago = now - timedelta(days=90)

        recent_expenses = [
            float(e.get("amount") or 0)
            for e in expenses
            if self._parse_date(e.get("date") or "") and
            self._parse_date(e.get("date")) >= three_months_ago
        ]

        if not recent_expenses:
            return 0.0

        total_3_months = sum(recent_expenses)
        return total_3_months / 3    # moyenne mensuelle

    def _get_pipeline_forecast(self) -> dict:
        """Récupère le forecast pipeline depuis Supabase."""
        try:
            client = get_client()
            result = client.table("forecasts").select("*").eq(
                "company_id", self.company_id
            ).eq("agent", "revenue_velocity").order(
                "computed_at", desc=True
            ).limit(1).execute()

            if result.data:
                return result.data[0]
        except Exception as e:
            logger.error(f"Erreur get pipeline forecast : {e}")

        return {"forecast_30d": 0, "forecast_60d": 0, "forecast_90d": 0}

    def _save_cash_forecast(self, forecast: dict) -> None:
        try:
            client = get_client()
            client.table("cash_forecasts").upsert({
                "company_id": self.company_id,
                **forecast
            }, on_conflict="company_id").execute()
        except Exception as e:
            logger.error(f"Erreur save cash forecast : {e}")

    # ─────────────────────────────────────────
    # 3.1 — RELANCES FACTURES
    # ─────────────────────────────────────────

    def _process_overdue_invoices(self, invoices: list) -> list[dict]:
        """
        Pour chaque facture en retard, décide quelle relance envoyer.

        J+1  → reminder 1 (doux)     [A]
        J+7  → reminder 2 (ferme)    [A]
        J+15 → reminder 3 + alerte   [A] + brief [C]
        J+30 → escalade CEO          [A]
        """
        actions_taken = []
        now = datetime.utcnow()

        overdue = [
            i for i in invoices
            if i.get("status") == "overdue"
        ]

        for invoice in overdue:
            due_at = self._parse_date(invoice.get("due_at"))
            if not due_at:
                continue

            days_overdue = (now - due_at).days

            # Vérifier si on a déjà envoyé une relance récemment
            last_reminder = self._get_last_reminder(invoice.get("id"))
            if last_reminder and (now - last_reminder).days < 7:
                continue    # On ne relance pas 2 fois en moins de 7 jours

            reminder_number = self._determine_reminder_number(days_overdue)
            if reminder_number == 0:
                continue

            # LLM rédige l'email
            email_text = draft(
                data={
                    "client_name": invoice.get("client_name"),
                    "invoice_number": invoice.get("raw_id"),
                    "amount": invoice.get("amount"),
                    "currency": invoice.get("currency", "EUR"),
                    "days_overdue": days_overdue,
                    "due_date": str(due_at.date()) if due_at else ""
                },
                instruction=invoice_reminder_email(
                    client_name=invoice.get("client_name", ""),
                    invoice_number=invoice.get("raw_id", ""),
                    amount=float(invoice.get("amount") or 0),
                    currency=invoice.get("currency", "EUR"),
                    due_date=str(due_at.date()) if due_at else "",
                    days_overdue=days_overdue,
                    payment_history="",
                    reminder_number=reminder_number
                )
            )

            if not email_text:
                continue

            # Récupérer l'email du contact client
            client_email = self._get_client_email(invoice.get("client_id"))
            if not client_email:
                continue

            # Envoyer l'email [A]
            from services.notification import send_email
            action = Action(
                type="send_invoice_reminder",
                level=ActionLevel.A,
                company_id=self.company_id,
                agent=self.name,
                payload={
                    "invoice_id": invoice.get("id"),
                    "client_email": client_email,
                    "days_overdue": days_overdue,
                    "reminder_number": reminder_number
                }
            )

            executor.run(
                action,
                send_email,
                to=client_email,
                subject=f"Facture {invoice.get('raw_id')} — Rappel de paiement",
                body=email_text
            )

            # Logger la relance
            self._log_reminder_sent(invoice.get("id"), reminder_number)

            actions_taken.append({
                "action": "invoice_reminder_sent",
                "invoice_id": invoice.get("id"),
                "client": invoice.get("client_name"),
                "amount": invoice.get("amount"),
                "days_overdue": days_overdue,
                "reminder_number": reminder_number
            })

            # J+30 → escalade CEO
            if days_overdue >= 30:
                company = self._get_company()
                ceo_email = company.get("agent_configs", {}).get(
                    "cash_predictability", {}
                ).get("ceo_email", "")

                if ceo_email:
                    alert_ceo(
                        company_id=self.company_id,
                        subject=f"🚨 Facture impayée 30j+ : {invoice.get('client_name')}",
                        message=(
                            f"La facture {invoice.get('raw_id')} de "
                            f"{invoice.get('amount')}€ est impayée "
                            f"depuis {days_overdue} jours.\n\n"
                            f"Client : {invoice.get('client_name')}\n"
                            f"Échéance : {str(due_at.date()) if due_at else 'N/A'}"
                        ),
                        ceo_email=ceo_email,
                        urgency="urgent"
                    )

        return actions_taken

    def _determine_reminder_number(self, days_overdue: int) -> int:
        if days_overdue >= 15:
            return 3
        elif days_overdue >= 7:
            return 2
        elif days_overdue >= 1:
            return 1
        return 0

    def _get_last_reminder(self, invoice_id: str) -> Optional[datetime]:
        try:
            client = get_client()
            result = client.table("action_logs").select("executed_at").eq(
                "company_id", self.company_id
            ).eq("action_type", "send_invoice_reminder").contains(
                "payload", {"invoice_id": invoice_id}
            ).order("executed_at", desc=True).limit(1).execute()

            if result.data:
                return self._parse_date(result.data[0]["executed_at"])
        except Exception:
            pass
        return None

    def _log_reminder_sent(self, invoice_id: str, reminder_number: int) -> None:
        try:
            client = get_client()
            client.table("invoice_reminders").insert({
                "company_id": self.company_id,
                "invoice_id": invoice_id,
                "reminder_number": reminder_number,
                "sent_at": datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            logger.error(f"Erreur log reminder : {e}")

    def _get_client_email(self, client_id: str) -> Optional[str]:
        if not client_id:
            return None
        try:
            client = get_client()
            result = client.table("contacts").select("email").eq(
                "company_id", self.company_id
            ).eq("client_id", client_id).limit(1).execute()
            return result.data[0].get("email") if result.data else None
        except Exception:
            return None

    # ─────────────────────────────────────────
    # ALERTE CASH
    # ─────────────────────────────────────────

    def _send_cash_alert(self, forecast: dict) -> None:
        company = self._get_company()
        ceo_email = company.get("agent_configs", {}).get(
            "cash_predictability", {}
        ).get("ceo_email", "")

        if not ceo_email:
            return

        days = forecast.get("days_until_critical", "?")
        message = (
            f"Alerte trésorerie : seuil critique prévu dans {days} jours.\n\n"
            f"Position cash estimée à 30j (scénario base) : "
            f"{forecast['base_30d']:,.0f}€\n"
            f"Position cash estimée à 30j (scénario stress) : "
            f"{forecast['stress_30d']:,.0f}€\n"
            f"Seuil critique : {forecast['critical_threshold']:,.0f}€\n\n"
            f"Actions recommandées :\n"
            f"→ Relancer les factures en retard\n"
            f"→ Accélérer la closing des deals chauds\n"
            f"→ Vérifier les dépenses reportables"
        )

        alert_ceo(
            company_id=self.company_id,
            subject="⚠️ Alerte trésorerie — Action requise",
            message=message,
            ceo_email=ceo_email,
            urgency="urgent" if days and days < 30 else "normal"
        )

    # ─────────────────────────────────────────
    # PRÉCISION DU FORECAST (KPI)
    # ─────────────────────────────────────────

    def _compute_forecast_accuracy(self) -> float:
        """
        Compare les prédictions passées aux réalités observées.
        Retourne la précision moyenne (0-1).

        En V1 : si pas d'historique, retourne 0 (pas encore mesurable).
        """
        try:
            client = get_client()
            result = client.table("cash_forecasts").select("*").eq(
                "company_id", self.company_id
            ).order("computed_at", desc=True).limit(10).execute()

            forecasts = result.data or []
            if len(forecasts) < 2:
                return 0.0

            # Comparer le forecast d'il y a 30j à la réalité actuelle
            # En V1 simplifié : on retourne 0 tant qu'on n'a pas 30j d'historique
            return 0.0

        except Exception:
            return 0.0

    # ─────────────────────────────────────────
    # UTILITAIRES
    # ─────────────────────────────────────────

    def _parse_date(self, value) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
          
