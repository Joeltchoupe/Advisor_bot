# agents/revenue_velocity.py

from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
import logging
import statistics

from agents.base import BaseAgent, AgentRunResult
from services.executor import executor, Action, ActionLevel
from services.llm import explain, generate
from services.notification import notify_commercial, alert_ceo
from services.database import get_client, publish_event
from prompts import (
    deal_zombie_note,
    lead_score_explanation,
    win_loss_analysis
)

logger = logging.getLogger(__name__)


class RevenueVelocityAgent(BaseAgent):

    def _get_name(self) -> str:
        return "revenue_velocity"

    def _run(self) -> AgentRunResult:
        started_at = datetime.utcnow()
        actions_taken = []
        errors = []

        # ─────────────────────────────────────────
        # DONNÉES D'ENTRÉE
        # Tout depuis Supabase — pas d'appel API direct
        # ─────────────────────────────────────────

        deals = self._get_deals()
        contacts = self._get_contacts()

        if not deals:
            logger.info(f"[revenue_velocity] Aucun deal pour {self.company_id}")
            return AgentRunResult(
                agent=self.name,
                company_id=self.company_id,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                kpi_name="revenue_velocity_eur_per_day",
                kpi_value=0.0
            )

        # ─────────────────────────────────────────
        # ACTION 1.2 — FORECAST AUTOMATIQUE
        # En premier : c'est le KPI
        # ─────────────────────────────────────────

        forecast_result = self._compute_forecast(deals)
        actions_taken.append({
            "action": "compute_forecast",
            "forecast_30d": forecast_result["forecast_30d"],
            "revenue_velocity": forecast_result["revenue_velocity"],
            "deals_analyzed": forecast_result["deals_analyzed"]
        })

        # Sauvegarder le forecast dans Supabase
        self._save_forecast(forecast_result)

        # Publier l'event pour Cash Predictability
        self._publish("forecast_updated", {
            "forecast_30d": forecast_result["forecast_30d"],
            "forecast_60d": forecast_result["forecast_60d"],
            "forecast_90d": forecast_result["forecast_90d"],
            "confidence": forecast_result["confidence"]
        })

        # ─────────────────────────────────────────
        # ACTION 1.1 — NETTOYAGE PIPELINE
        # ─────────────────────────────────────────

        zombie_actions = self._clean_pipeline(
            deals, forecast_result["avg_cycle_days_by_stage"]
        )
        actions_taken.extend(zombie_actions)

        # ─────────────────────────────────────────
        # ACTION 1.4 — SCORING LEADS
        # Seulement les contacts récents (< 7 jours)
        # ─────────────────────────────────────────

        won_deals = [d for d in deals if d.get("status") == "won"]
        recent_contacts = [
            c for c in contacts
            if self._is_recent(c.get("created_at"), days=7)
            and not c.get("score")    # pas encore scoré
        ]

        scoring_actions = self._score_leads(recent_contacts, won_deals)
        actions_taken.extend(scoring_actions)

        # ─────────────────────────────────────────
        # ACTION 1.9 — ANALYSE WIN/LOSS
        # Deals fermés dans les dernières 48h
        # ─────────────────────────────────────────

        recently_closed = [
            d for d in deals
            if d.get("status") in ("won", "lost")
            and self._is_recent(d.get("closed_at"), days=2)
        ]

        winloss_actions = self._analyze_win_loss(recently_closed, deals)
        actions_taken.extend(winloss_actions)

        return AgentRunResult(
            agent=self.name,
            company_id=self.company_id,
            started_at=started_at,
            finished_at=datetime.utcnow(),
            actions_taken=actions_taken,
            kpi_value=round(forecast_result["revenue_velocity"], 2),
            kpi_name="revenue_velocity_eur_per_day",
            errors=errors
        )

    # ─────────────────────────────────────────
    # 1.2 — FORECAST
    # ─────────────────────────────────────────

    def _compute_forecast(self, deals: list[dict]) -> dict:
        """
        Calcule la probabilité réelle de chaque deal actif
        et construit le forecast 30/60/90 jours.

        Probabilité réelle = f(
            jours_sans_activité,
            win_rate_historique_du_stage,
            concordance_expected_close_date
        )
        """
        active_deals = [d for d in deals if d.get("status") == "active"]
        won_deals    = [d for d in deals if d.get("status") == "won"]
        lost_deals   = [d for d in deals if d.get("status") == "lost"]

        # Win rate par stage (sur les deals historiques)
        win_rate_by_stage = self._compute_win_rate_by_stage(
            won_deals, lost_deals
        )

        # Cycle moyen par stage (sur les deals WON)
        avg_cycle_by_stage = self._compute_avg_cycle_by_stage(won_deals)

        # Forecast
        forecast_30d = 0.0
        forecast_60d = 0.0
        forecast_90d = 0.0
        total_weighted = 0.0
        deals_analyzed = 0
        confidence_scores = []

        now = datetime.utcnow()

        for deal in active_deals:
            stage = deal.get("stage", "")
            amount = float(deal.get("amount") or 0)

            if amount == 0:
                continue

            # Jours sans activité
            last_activity = self._parse_date(deal.get("last_activity_at"))
            days_stagnant = (now - last_activity).days if last_activity else 999

            # Win rate du stage
            stage_win_rate = win_rate_by_stage.get(stage, 0.2)

            # Facteur de stagnation
            # Plus le deal stagne vs la moyenne, plus la proba baisse
            avg_cycle = avg_cycle_by_stage.get(stage, 30)
            stagnation_factor = max(0.1, 1 - (days_stagnant / (avg_cycle * 2)))

            # Probabilité réelle
            real_probability = stage_win_rate * stagnation_factor

            # Pondération par date de clôture attendue
            expected_close = self._parse_date(deal.get("expected_close_date"))
            if expected_close:
                days_to_close = (expected_close - now).days
                if 0 <= days_to_close <= 30:
                    forecast_30d += amount * real_probability
                elif 30 < days_to_close <= 60:
                    forecast_60d += amount * real_probability
                elif 60 < days_to_close <= 90:
                    forecast_90d += amount * real_probability
            else:
                # Pas de date → on distribue selon la probabilité
                forecast_30d += amount * real_probability * 0.3
                forecast_60d += amount * real_probability * 0.4
                forecast_90d += amount * real_probability * 0.3

            total_weighted += amount * real_probability
            confidence_scores.append(real_probability)
            deals_analyzed += 1

            # Mettre à jour la probabilité réelle dans Supabase
            self._update_deal_probability(
                deal["id"], real_probability, days_stagnant
            )

        # Revenue Velocity = forecast 30j / 30 jours
        revenue_velocity = forecast_30d / 30 if forecast_30d > 0 else 0

        # Confiance globale du forecast
        avg_confidence = (
            statistics.mean(confidence_scores)
            if confidence_scores else 0
        )

        return {
            "forecast_30d": round(forecast_30d, 2),
            "forecast_60d": round(forecast_60d, 2),
            "forecast_90d": round(forecast_90d, 2),
            "revenue_velocity": round(revenue_velocity, 2),
            "deals_analyzed": deals_analyzed,
            "confidence": round(avg_confidence, 3),
            "avg_cycle_days_by_stage": avg_cycle_by_stage,
            "win_rate_by_stage": win_rate_by_stage
        }

    def _compute_win_rate_by_stage(
        self, won_deals: list, lost_deals: list
    ) -> dict:
        """
        Win rate par stage = deals WON depuis ce stage
                           / (WON + LOST depuis ce stage)

        Si pas assez de données (<5 deals) → fallback 0.2
        """
        won_by_stage: dict[str, int] = {}
        lost_by_stage: dict[str, int] = {}

        for deal in won_deals:
            stage = deal.get("stage", "unknown")
            won_by_stage[stage] = won_by_stage.get(stage, 0) + 1

        for deal in lost_deals:
            stage = deal.get("stage", "unknown")
            lost_by_stage[stage] = lost_by_stage.get(stage, 0) + 1

        all_stages = set(list(won_by_stage.keys()) + list(lost_by_stage.keys()))
        win_rates = {}

        for stage in all_stages:
            won = won_by_stage.get(stage, 0)
            lost = lost_by_stage.get(stage, 0)
            total = won + lost
            if total >= 5:
                win_rates[stage] = won / total
            else:
                win_rates[stage] = 0.2    # fallback si pas assez de données

        return win_rates

    def _compute_avg_cycle_by_stage(self, won_deals: list) -> dict:
        """
        Temps moyen passé dans chaque stage pour les deals WON.
        On utilise le stage actuel et la durée totale comme proxy.
        """
        durations_by_stage: dict[str, list] = {}

        for deal in won_deals:
            stage = deal.get("stage", "unknown")
            created = self._parse_date(deal.get("created_at"))
            closed = self._parse_date(deal.get("closed_at"))

            if created and closed:
                total_days = (closed - created).days
                if stage not in durations_by_stage:
                    durations_by_stage[stage] = []
                durations_by_stage[stage].append(total_days)

        avg_by_stage = {}
        for stage, durations in durations_by_stage.items():
            avg_by_stage[stage] = statistics.mean(durations)

        return avg_by_stage

    def _update_deal_probability(
        self, deal_id: str, probability: float, days_stagnant: int
    ) -> None:
        """Met à jour probability_real dans Supabase."""
        try:
            client = get_client()
            client.table("deals").update({
                "probability_real": probability
            }).eq("id", deal_id).execute()
        except Exception as e:
            logger.error(f"Erreur update probability : {e}")

    def _save_forecast(self, forecast: dict) -> None:
        """Sauvegarde le forecast dans une table dédiée."""
        try:
            client = get_client()
            client.table("forecasts").upsert({
                "company_id": self.company_id,
                "agent": self.name,
                "computed_at": datetime.utcnow().isoformat(),
                "forecast_30d": forecast["forecast_30d"],
                "forecast_60d": forecast["forecast_60d"],
                "forecast_90d": forecast["forecast_90d"],
                "revenue_velocity": forecast["revenue_velocity"],
                "confidence": forecast["confidence"]
            }, on_conflict="company_id,agent").execute()
        except Exception as e:
            logger.error(f"Erreur save forecast : {e}")

    # ─────────────────────────────────────────
    # 1.1 — NETTOYAGE PIPELINE
    # ─────────────────────────────────────────

    def _clean_pipeline(
        self, deals: list[dict], avg_cycle_by_stage: dict
    ) -> list[dict]:
        """
        Identifie les deals zombies et les tague dans le CRM.

        Zombie = deal actif sans activité
                 > (2 × cycle moyen du stage)
        """
        actions_taken = []
        now = datetime.utcnow()

        stagnation_threshold = self._cfg("stagnation_threshold_days", 21)
        active_deals = [d for d in deals if d.get("status") == "active"]

        for deal in active_deals:
            stage = deal.get("stage", "")
            last_activity = self._parse_date(deal.get("last_activity_at"))

            if not last_activity:
                continue

            days_stagnant = (now - last_activity).days
            avg_cycle = avg_cycle_by_stage.get(stage, stagnation_threshold)
            threshold = max(stagnation_threshold, int(avg_cycle * 2))

            if days_stagnant < threshold:
                continue

            # Ce deal est zombie
            win_rate = 0.15    # probabilité générique faible
            amount = float(deal.get("amount") or 0)

            # LLM rédige la note
            note_text = explain(
                data={
                    "deal_title": deal.get("title"),
                    "days_stagnant": days_stagnant,
                    "stage": stage,
                    "avg_cycle_days_at_stage": avg_cycle,
                    "win_rate_at_stage": win_rate,
                    "amount": amount
                },
                instruction=deal_zombie_note(
                    deal_title=deal.get("title", ""),
                    days_stagnant=days_stagnant,
                    stage=stage,
                    avg_cycle_days_at_stage=avg_cycle,
                    win_rate_at_stage=win_rate,
                    amount=amount
                )
            )

            # Tagger dans le CRM via connector
            connector = self._get_crm_connector()
            if connector:
                action = Action(
                    type="tag_deal_zombie",
                    level=ActionLevel.A,
                    company_id=self.company_id,
                    agent=self.name,
                    payload={
                        "deal_id": deal.get("raw_id"),
                        "days_stagnant": days_stagnant,
                        "note": note_text
                    },
                    description=f"Deal '{deal.get('title')}' taggé zombie"
                )

                executor.run(
                    action,
                    connector.update_deal,
                    raw_id=deal.get("raw_id"),
                    fields={"axio_status": "zombie"}
                )

                if note_text:
                    executor.run(
                        Action(
                            type="add_note_zombie",
                            level=ActionLevel.A,
                            company_id=self.company_id,
                            agent=self.name,
                            payload={"deal_id": deal.get("raw_id")}
                        ),
                        connector.add_note,
                        deal_raw_id=deal.get("raw_id"),
                        note=note_text
                    )

                actions_taken.append({
                    "action": "tag_zombie",
                    "deal_id": deal.get("id"),
                    "deal_title": deal.get("title"),
                    "days_stagnant": days_stagnant
                })

                # Notifier le commercial
                owner_email = self._get_owner_email(deal.get("owner_id"))
                if owner_email:
                    notify_commercial(
                        name=deal.get("owner_name", ""),
                        email=owner_email,
                        subject=f"⚠️ Deal en stagnation : {deal.get('title')}",
                        message=note_text
                    )

        logger.info(
            f"[revenue_velocity] Pipeline nettoyé — "
            f"{len(actions_taken)} zombies détectés"
        )
        return actions_taken

    # ─────────────────────────────────────────
    # 1.4 — SCORING LEADS
    # ─────────────────────────────────────────

    def _score_leads(
        self, contacts: list[dict], won_deals: list[dict]
    ) -> list[dict]:
        """
        Score chaque nouveau contact de 0 à 100.

        Score = fit_score (50%) + source_score (30%) + timing_score (20%)

        fit_score   = similarité profil vs deals WON historiques
        source_score = taux de conversion historique de la source
        timing_score = récence de la dernière activité
        """
        if not contacts:
            return []

        actions_taken = []

        # Profil moyen des deals WON (pour le fit score)
        won_profile = self._build_won_profile(won_deals)

        # Taux de conversion par source
        source_rates = self._compute_source_conversion_rates(won_deals)

        for contact in contacts:
            score, breakdown = self._compute_lead_score(
                contact, won_profile, source_rates
            )

            # Label
            from models import LeadScore
            if score >= 70:
                label = LeadScore.HOT.value
            elif score >= 40:
                label = LeadScore.WARM.value
            else:
                label = LeadScore.COLD.value

            # Explication LLM
            explanation = explain(
                data={
                    "score": score,
                    "label": label,
                    "contact": {
                        "company": contact.get("company_name"),
                        "sector": contact.get("sector"),
                        "size": contact.get("company_size"),
                        "source": contact.get("source")
                    },
                    "breakdown": breakdown,
                    "similar_won_deals": won_profile.get("count", 0)
                },
                instruction=lead_score_explanation(
                    score=score,
                    score_label=label,
                    fit_score=breakdown.get("fit", 0),
                    source=contact.get("source", ""),
                    source_conversion_rate=source_rates.get(
                        contact.get("source", ""), 0.2
                    ),
                    company_size=contact.get("company_size"),
                    sector=contact.get("sector", ""),
                    similar_won_deals=won_profile.get("count", 0)
                )
            )

            # Sauvegarder le score dans Supabase
            try:
                client = get_client()
                client.table("contacts").update({
                    "score": score,
                    "score_label": label,
                    "score_reason": explanation
                }).eq("id", contact["id"]).execute()
            except Exception as e:
                logger.error(f"Erreur save lead score : {e}")

            actions_taken.append({
                "action": "score_lead",
                "contact_id": contact.get("id"),
                "contact_email": contact.get("email"),
                "score": score,
                "label": label
            })

            # Notifier si HOT
            if label == LeadScore.HOT.value:
                owner_email = self._cfg("head_of_sales_email", "")
                if owner_email:
                    notify_commercial(
                        name="Head of Sales",
                        email=owner_email,
                        subject=f"🔥 Lead chaud : {contact.get('email')}",
                        message=(
                            f"Nouveau lead HOT (score {score}/100)\n\n"
                            f"{explanation}\n\n"
                            f"Entreprise : {contact.get('company_name', 'N/A')}\n"
                            f"Source : {contact.get('source', 'N/A')}"
                        )
                    )

        logger.info(
            f"[revenue_velocity] {len(actions_taken)} leads scorés"
        )
        return actions_taken

    def _build_won_profile(self, won_deals: list) -> dict:
        """
        Profil moyen des deals gagnés :
        taille moyenne, montant moyen, sources communes.
        """
        if not won_deals:
            return {"count": 0, "avg_amount": 0, "common_sources": []}

        amounts = [float(d.get("amount") or 0) for d in won_deals if d.get("amount")]
        sources = [d.get("source") for d in won_deals if d.get("source")]

        # Sources les plus fréquentes
        source_counts: dict[str, int] = {}
        for s in sources:
            source_counts[s] = source_counts.get(s, 0) + 1
        common_sources = sorted(
            source_counts.keys(),
            key=lambda x: source_counts[x],
            reverse=True
        )[:3]

        return {
            "count": len(won_deals),
            "avg_amount": statistics.mean(amounts) if amounts else 0,
            "common_sources": common_sources
        }

    def _compute_source_conversion_rates(self, won_deals: list) -> dict:
        """
        Taux de conversion par source =
        deals WON avec cette source / total deals avec cette source.
        """
        all_deals = self._get_deals()
        won_by_source: dict[str, int] = {}
        total_by_source: dict[str, int] = {}

        for deal in all_deals:
            source = deal.get("source", "unknown")
            total_by_source[source] = total_by_source.get(source, 0) + 1

        for deal in won_deals:
            source = deal.get("source", "unknown")
            won_by_source[source] = won_by_source.get(source, 0) + 1

        rates = {}
        for source, total in total_by_source.items():
            won = won_by_source.get(source, 0)
            rates[source] = won / total if total >= 3 else 0.2

        return rates

    def _compute_lead_score(
        self,
        contact: dict,
        won_profile: dict,
        source_rates: dict
    ) -> tuple[int, dict]:
        """
        Score = fit (50%) + source (30%) + timing (20%)
        Chaque composante est normalisée sur 100.
        """
        # FIT SCORE (50%)
        # Basé sur la source et le montant similaire des deals WON
        source = contact.get("source", "")
        is_common_source = source in won_profile.get("common_sources", [])
        fit_raw = 0.7 if is_common_source else 0.4
        # Bonus si la taille d'entreprise est connue
        if contact.get("company_size"):
            fit_raw = min(1.0, fit_raw + 0.2)
        fit_score = fit_raw * 100

        # SOURCE SCORE (30%)
        source_rate = source_rates.get(source, 0.2)
        source_score = min(100, source_rate * 200)   # 50% conv rate → 100

        # TIMING SCORE (20%)
        last_activity = self._parse_date(contact.get("last_activity_at"))
        if last_activity:
            hours_since = (datetime.utcnow() - last_activity).total_seconds() / 3600
            timing_score = max(0, 100 - (hours_since * 2))   # -2pts/heure
        else:
            timing_score = 50.0

        # Score final pondéré
        final_score = int(
            fit_score * 0.5 +
            source_score * 0.3 +
            timing_score * 0.2
        )
        final_score = max(0, min(100, final_score))

        breakdown = {
            "fit": round(fit_score, 1),
            "source": round(source_score, 1),
            "timing": round(timing_score, 1)
        }

        return final_score, breakdown

    # ─────────────────────────────────────────
    # 1.9 — ANALYSE WIN/LOSS
    # ─────────────────────────────────────────

    def _analyze_win_loss(
        self, closed_deals: list, all_deals: list
    ) -> list[dict]:
        """
        Pour chaque deal récemment fermé :
        - Calcule les métriques du deal
        - Les compare aux deals WON historiques
        - Génère une analyse avec le LLM
        - Sauvegarde pour améliorer le scoring futur
        """
        if not closed_deals:
            return []

        actions_taken = []
        won_deals = [d for d in all_deals if d.get("status") == "won"]

        # Métriques moyennes des deals WON (référence)
        avg_won_days = self._compute_avg_total_days(won_deals)
        avg_won_activities = 8   # valeur par défaut, sera affinée

        for deal in closed_deals:
            outcome = deal.get("status")    # "won" ou "lost"

            created = self._parse_date(deal.get("created_at"))
            closed  = self._parse_date(deal.get("closed_at"))
            total_days = (closed - created).days if created and closed else 0

            # Analyse LLM
            analysis = generate(
                data={
                    "deal": {
                        "title": deal.get("title"),
                        "amount": deal.get("amount"),
                        "stage": deal.get("stage"),
                        "owner": deal.get("owner_name")
                    },
                    "outcome": outcome,
                    "total_days": total_days,
                    "avg_won_days": round(avg_won_days, 1),
                    "days_vs_avg": round(total_days - avg_won_days, 1)
                },
                instruction=win_loss_analysis(
                    deal_title=deal.get("title", ""),
                    outcome=outcome,
                    total_days=total_days,
                    avg_won_days=avg_won_days,
                    stage_durations={},
                    bottleneck_stage=deal.get("stage", ""),
                    bottleneck_days=0,
                    avg_activities=avg_won_activities,
                    actual_activities=0
                )
            )

            # Sauvegarder l'analyse
            try:
                client = get_client()
                client.table("win_loss_analyses").insert({
                    "company_id": self.company_id,
                    "deal_id": deal.get("id"),
                    "deal_title": deal.get("title"),
                    "outcome": outcome,
                    "total_days": total_days,
                    "avg_won_days": avg_won_days,
                    "analysis": analysis,
                    "analyzed_at": datetime.utcnow().isoformat()
                }).execute()
            except Exception as e:
                logger.error(f"Erreur save win_loss : {e}")

            actions_taken.append({
                "action": "win_loss_analysis",
                "deal_id": deal.get("id"),
                "outcome": outcome,
                "total_days": total_days
            })

        return actions_taken

    def _compute_avg_total_days(self, won_deals: list) -> float:
        durations = []
        for deal in won_deals:
            created = self._parse_date(deal.get("created_at"))
            closed  = self._parse_date(deal.get("closed_at"))
            if created and closed:
                durations.append((closed - created).days)
        return statistics.mean(durations) if durations else 30.0

    # ─────────────────────────────────────────
    # UTILITAIRES
    # ─────────────────────────────────────────

    def _get_crm_connector(self):
        """
        Retourne le bon connecteur CRM selon les outils du client.
        """
        company = self._get_company()
        tools = company.get("tools_connected", {})
        crm_tool = tools.get("crm", {}).get("name", "")

        credentials = self._get_credentials(crm_tool)
        if not credentials:
            return None

        if crm_tool == "hubspot":
            from connectors.crm.hubspot import HubSpotConnector
            return HubSpotConnector(self.company_id, credentials)
        elif crm_tool == "salesforce":
            from connectors.crm.salesforce import SalesforceConnector
            return SalesforceConnector(self.company_id, credentials)
        elif crm_tool == "pipedrive":
            from connectors.crm.pipedrive import PipedriveConnector
            return PipedriveConnector(self.company_id, credentials)
        elif crm_tool == "zoho":
            from connectors.crm.zoho import ZohoConnector
            return ZohoConnector(self.company_id, credentials)

        return None

    def _get_credentials(self, tool: str) -> Optional[dict]:
        """Récupère les credentials d'un outil depuis Supabase."""
        try:
            client = get_client()
            result = client.table("credentials").select("*").eq(
                "company_id", self.company_id
            ).eq("tool", tool).limit(1).execute()
            return result.data[0].get("credentials") if result.data else None
        except Exception:
            return None

    def _get_owner_email(self, owner_id: str) -> Optional[str]:
        """Récupère l'email d'un owner via la table team_members."""
        if not owner_id:
            return None
        try:
            client = get_client()
            result = client.table("team_members").select("email").eq(
                "company_id", self.company_id
            ).eq("crm_owner_id", owner_id).limit(1).execute()
            return result.data[0].get("email") if result.data else None
        except Exception:
            return None

    def _is_recent(self, date_str: Optional[str], days: int = 7) -> bool:
        if not date_str:
            return False
        dt = self._parse_date(date_str)
        if not dt:
            return False
        return (datetime.utcnow() - dt).days <= days

    def _parse_date(self, value) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

        
      











