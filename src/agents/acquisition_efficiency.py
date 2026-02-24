# agents/acquisition_efficiency.py

from datetime import datetime, timedelta
from typing import Optional
import logging
import statistics

from agents.base import BaseAgent, AgentRunResult
from services.database import get_client
from services.notification import alert_ceo

logger = logging.getLogger(__name__)


class AcquisitionEfficiencyAgent(BaseAgent):

    def _get_name(self) -> str:
        return "acquisition_efficiency"

    def _run(self) -> AgentRunResult:
        started_at = datetime.utcnow()
        actions_taken = []
        errors = []

        deals   = self._get_deals()
        expenses = self._get_expenses()

        # ─────────────────────────────────────────
        # ACTION 4.2 — CAC RÉEL
        # ─────────────────────────────────────────

        cac_result = self._compute_cac(deals, expenses)

        if cac_result:
            self._save_cac_metrics(cac_result)
            actions_taken.append({
                "action": "compute_cac",
                "blended_cac": cac_result.get("blended_cac"),
                "clients_acquired": cac_result.get("total_clients"),
                "marketing_spend": cac_result.get("total_marketing_spend")
            })

            # Publier l'event pour Revenue Velocity
            # (le scoring des leads intègre le CAC par source)
            self._publish("cac_updated", {
                "blended_cac": cac_result.get("blended_cac"),
                "cac_by_source": cac_result.get("cac_by_source"),
                "top_source": cac_result.get("top_source")
            })

            # Alerte si le CAC a fortement augmenté
            self._check_cac_anomaly(cac_result)

        return AgentRunResult(
            agent=self.name,
            company_id=self.company_id,
            started_at=started_at,
            finished_at=datetime.utcnow(),
            actions_taken=actions_taken,
            kpi_value=round(cac_result.get("blended_cac", 0), 2) if cac_result else 0.0,
            kpi_name="blended_cac_eur",
            errors=errors
        )

    # ─────────────────────────────────────────
    # 4.2 — CALCUL DU CAC
    # ─────────────────────────────────────────

    def _compute_cac(
        self, deals: list, expenses: list
    ) -> Optional[dict]:
        """
        Blended CAC = dépenses marketing totales / nouveaux clients
        CAC par source = si la source est trackée dans les deals WON

        Période : les 90 derniers jours (assez de données, pas trop vieux)
        """
        now = datetime.utcnow()
        ninety_days_ago = now - timedelta(days=90)

        # Deals gagnés dans les 90 derniers jours
        won_recent = [
            d for d in deals
            if d.get("status") == "won"
            and self._parse_date(d.get("closed_at")) >= ninety_days_ago
        ]

        if not won_recent:
            logger.info(
                f"[acquisition_efficiency] Pas de deals WON récents "
                f"pour {self.company_id}"
            )
            return None

        # Dépenses marketing sur la même période
        marketing_categories = self._cfg(
            "marketing_expense_categories",
            ["marketing", "advertising", "publicite", "ads",
             "pub", "communication", "acquisition"]
        )

        marketing_expenses = [
            float(e.get("amount") or 0)
            for e in expenses
            if self._is_marketing_expense(e, marketing_categories)
            and self._parse_date(e.get("date")) >= ninety_days_ago
        ]

        total_marketing_spend = sum(marketing_expenses)
        total_clients = len(won_recent)

        # Blended CAC
        blended_cac = (
            total_marketing_spend / total_clients
            if total_clients > 0 else 0
        )

        # CAC par source
        clients_by_source: dict[str, int] = {}
        revenue_by_source: dict[str, float] = {}

        for deal in won_recent:
            source = deal.get("source") or "unknown"
            clients_by_source[source] = clients_by_source.get(source, 0) + 1
            revenue_by_source[source] = (
                revenue_by_source.get(source, 0) +
                float(deal.get("amount") or 0)
            )

        # CAC par source (si on a les dépenses ventilées par source)
        # En V1 : on distribue le spend au prorata des clients acquis
        cac_by_source = {}
        for source, count in clients_by_source.items():
            proportion = count / total_clients
            source_spend = total_marketing_spend * proportion
            cac_by_source[source] = round(
                source_spend / count if count > 0 else 0, 2
            )

        # Top source (meilleur ROI = plus de clients, moins de spend)
        top_source = min(
            cac_by_source.keys(),
            key=lambda s: cac_by_source[s]
        ) if cac_by_source else "unknown"

        return {
            "blended_cac": round(blended_cac, 2),
            "total_clients": total_clients,
            "total_marketing_spend": round(total_marketing_spend, 2),
            "cac_by_source": cac_by_source,
            "clients_by_source": clients_by_source,
            "revenue_by_source": {
                k: round(v, 2) for k, v in revenue_by_source.items()
            },
            "top_source": top_source,
            "period_days": 90,
            "computed_at": now.isoformat()
        }

    def _is_marketing_expense(
        self, expense: dict, marketing_categories: list
    ) -> bool:
        """
        Détermine si une dépense est du marketing
        par sa catégorie ou son vendor.
        """
        category = (expense.get("category") or "").lower()
        vendor = (expense.get("vendor") or "").lower()

        return any(
            kw in category or kw in vendor
            for kw in marketing_categories
        )

    def _save_cac_metrics(self, cac_result: dict) -> None:
        try:
            client = get_client()
            client.table("cac_metrics").upsert({
                "company_id": self.company_id,
                **cac_result
            }, on_conflict="company_id").execute()
        except Exception as e:
            logger.error(f"Erreur save cac metrics : {e}")

    def _check_cac_anomaly(self, cac_result: dict) -> None:
        """
        Compare le CAC actuel au CAC précédent.
        Si hausse > 30% → alerte CEO.
        """
        try:
            client = get_client()
            result = client.table("cac_metrics").select(
                "blended_cac, computed_at"
            ).eq(
                "company_id", self.company_id
            ).order("computed_at", desc=True).limit(2).execute()

            records = result.data or []
            if len(records) < 2:
                return

            current_cac  = records[0].get("blended_cac", 0)
            previous_cac = records[1].get("blended_cac", 0)

            if previous_cac == 0:
                return

            change_pct = (current_cac - previous_cac) / previous_cac

            if change_pct > 0.30:    # hausse de plus de 30%
                company = self._get_company()
                ceo_email = company.get("agent_configs", {}).get(
                    "acquisition_efficiency", {}
                ).get("ceo_email", "")

                if ceo_email:
                    alert_ceo(
                        company_id=self.company_id,
                        subject="⚠️ CAC en hausse de 30%+",
                        message=(
                            f"Votre CAC a augmenté de {change_pct*100:.1f}%.\n\n"
                            f"CAC précédent : {previous_cac:,.0f}€\n"
                            f"CAC actuel : {current_cac:,.0f}€\n\n"
                            f"Top source actuelle : "
                            f"{cac_result.get('top_source', 'N/A')}\n"
                            f"Vérifiez vos dépenses marketing."
                        ),
                        ceo_email=ceo_email,
                        urgency="normal"
                    )

        except Exception as e:
            logger.error(f"Erreur check cac anomaly : {e}")

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
