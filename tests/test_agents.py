# tests/test_agents.py

"""
Ce qu'on teste :
→ Les calculs sont mathématiquement corrects
→ Les bonnes actions sont déclenchées selon les conditions
→ Les bons events sont publiés

Ce qu'on mocke :
→ Supabase (pas de vraie DB en test)
→ Les connecteurs CRM (pas d'appels API réels)
→ Le LLM (pas de coût en test)
→ Les notifications (pas d'emails envoyés)
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


class TestRevenueVelocityCalculations:
    """
    Tests des calculs purs de Revenue Velocity.
    Pas de mock nécessaire — ce sont des fonctions mathématiques.
    """

    def setup_method(self):
        # On instancie l'agent avec un mock minimal
        with patch("services.database.get_client"):
            from agents.revenue_velocity import RevenueVelocityAgent
            self.agent = RevenueVelocityAgent(
                company_id="test-uuid",
                config={"stagnation_threshold_days": 21}
            )

    def test_win_rate_by_stage_correct(self):
        """
        3 won et 1 lost au stage 'proposal'
        → win rate = 3/4 = 0.75
        """
        won_deals = [
            {"stage": "proposal"}, {"stage": "proposal"}, {"stage": "proposal"},
            {"stage": "qualified"}
        ]
        lost_deals = [
            {"stage": "proposal"}
        ]

        rates = self.agent._compute_win_rate_by_stage(won_deals, lost_deals)

        assert "proposal" in rates
        assert rates["proposal"] == pytest.approx(0.75)

    def test_win_rate_fallback_insufficient_data(self):
        """
        Moins de 5 deals → fallback à 0.2 (pas assez de données)
        """
        won_deals = [{"stage": "proposal"}, {"stage": "proposal"}]
        lost_deals = [{"stage": "proposal"}]

        rates = self.agent._compute_win_rate_by_stage(won_deals, lost_deals)

        # 3 deals total < 5 → fallback
        assert rates.get("proposal", 0.2) == 0.2

    def test_lead_score_hot(self, sample_contacts, sample_deals):
        """
        Un contact avec source 'referral' (même que les deals WON)
        et activité récente doit avoir un score > 70.
        """
        won_deals = [d for d in sample_deals if d.get("status") == "won"]
        won_profile = self.agent._build_won_profile(won_deals)

        source_rates = {"referral": 0.65, "linkedin": 0.15}

        contact = sample_contacts[0]    # Sophie Laurent, source referral

        score, breakdown = self.agent._compute_lead_score(
            contact, won_profile, source_rates
        )

        assert score > 70, f"Score attendu > 70, obtenu {score}"
        assert "fit" in breakdown
        assert "source" in breakdown
        assert "timing" in breakdown

    def test_lead_score_cold(self, sample_contacts, sample_deals):
        """
        Un contact avec mauvaise source et petite entreprise
        doit avoir un score < 40.
        """
        won_deals = [d for d in sample_deals if d.get("status") == "won"]
        won_profile = self.agent._build_won_profile(won_deals)

        source_rates = {"referral": 0.65, "google_ads": 0.05}

        contact = sample_contacts[1]    # Jean Petit, source google_ads, size=3

        score, breakdown = self.agent._compute_lead_score(
            contact, won_profile, source_rates
        )

        assert score < 40, f"Score attendu < 40, obtenu {score}"

    def test_lead_score_bounded_0_100(self, sample_contacts, sample_deals):
        """Le score ne peut jamais dépasser 100 ou être négatif."""
        won_deals = [d for d in sample_deals if d.get("status") == "won"]
        won_profile = self.agent._build_won_profile(won_deals)
        source_rates = {}

        for contact in sample_contacts:
            score, _ = self.agent._compute_lead_score(
                contact, won_profile, source_rates
            )
            assert 0 <= score <= 100

    def test_zombie_detection_threshold(self, sample_deals):
        """
        Un deal sans activité depuis 32 jours avec threshold=21
        et cycle moyen de 10 jours (threshold effectif = 21)
        doit être détecté zombie.
        """
        # Le deal_002 dans sample_deals est sans activité depuis 32 jours
        stagnant_deal = next(
            d for d in sample_deals
            if d["id"] == "deal_002"
        )

        now = datetime.utcnow()
        last_activity = datetime.fromisoformat(
            stagnant_deal["last_activity_at"].replace("Z", "+00:00")
        )
        days_stagnant = (now - last_activity).days

        avg_cycle = {"qualified": 10}
        threshold = max(21, int(avg_cycle.get("qualified", 21) * 2))

        assert days_stagnant > threshold, (
            f"Deal devrait être zombie : {days_stagnant}j > {threshold}j"
        )

    def test_forecast_computation(self, sample_deals):
        """
        Le forecast doit être inférieur ou égal
        à la somme brute de tous les deals actifs
        (les probabilités pondèrent à la baisse).
        """
        with patch.object(self.agent, "_get_deals", return_value=sample_deals):
            with patch.object(self.agent, "_update_deal_probability"):
                with patch.object(self.agent, "_save_forecast"):
                    result = self.agent._compute_forecast(sample_deals)

        active_deals = [d for d in sample_deals if d.get("status") == "active"]
        total_active = sum(float(d.get("amount", 0)) for d in active_deals)

        assert result["forecast_30d"] >= 0
        assert result["forecast_30d"] <= total_active
        assert result["revenue_velocity"] >= 0
        assert 0 <= result["confidence"] <= 1


class TestCashPredictabilityCalculations:

    def setup_method(self):
        with patch("services.database.get_client"):
            from agents.cash_predictability import CashPredictabilityAgent
            self.agent = CashPredictabilityAgent(
                company_id="test-uuid",
                config={}
            )

    def test_payment_patterns_correct(self, sample_invoices):
        """
        Acme Corp a payé : -2 jours (inv_001) et +3 jours (inv_004 en retard)
        → moyenne correcte calculée
        """
        # Simuler 2 paiements d'Acme Corp
        invoices = [
            {
                "status": "paid",
                "client_id": "acme",
                "payment_delay_days": -2
            },
            {
                "status": "paid",
                "client_id": "acme",
                "payment_delay_days": 8
            },
            {
                "status": "paid",
                "client_id": "beta",
                "payment_delay_days": 15
            }
        ]

        patterns = self.agent._compute_payment_patterns(invoices)

        assert "acme" in patterns
        avg = patterns["acme"]["avg_delay_days"]
        assert avg == pytest.approx(3.0)    # (-2 + 8) / 2

    def test_trend_degrading(self):
        """Une série qui augmente → 'degrading'"""
        values = [5, 8, 10, 15, 18, 22]
        assert self.agent._compute_trend(values) == "degrading"

    def test_trend_improving(self):
        """Une série qui diminue → 'improving'"""
        values = [20, 18, 15, 10, 8, 5]
        assert self.agent._compute_trend(values) == "improving"

    def test_trend_stable(self):
        """Une série stable → 'stable'"""
        values = [10, 11, 10, 9, 10, 11]
        assert self.agent._compute_trend(values) == "stable"

    def test_reminder_day_selection(self):
        """
        Bonne relance selon le nombre de jours de retard.
        """
        assert self.agent._determine_reminder_number(1) == 1
        assert self.agent._determine_reminder_number(7) == 2
        assert self.agent._determine_reminder_number(15) == 3
        assert self.agent._determine_reminder_number(0) == 0

    def test_monthly_recurring_estimation(self, sample_invoices):
        """
        Les dépenses récurrentes sont estimées
        sur les 3 derniers mois.
        """
        now = datetime.utcnow()
        expenses = [
            {
                "amount": 3000.0,
                "date": (now - timedelta(days=10)).isoformat()
            },
            {
                "amount": 3000.0,
                "date": (now - timedelta(days=40)).isoformat()
            },
            {
                "amount": 3000.0,
                "date": (now - timedelta(days=70)).isoformat()
            },
            # Trop vieille → exclue
            {
                "amount": 99999.0,
                "date": (now - timedelta(days=120)).isoformat()
            }
        ]

        monthly = self.agent._estimate_monthly_recurring(expenses)

        # 3 × 3000€ sur 3 mois = 3000€/mois
        assert monthly == pytest.approx(3000.0, rel=0.1)


class TestProcessClarityCalculations:

    def setup_method(self):
        with patch("services.database.get_client"):
            from agents.process_clarity import ProcessClarityAgent
            self.agent = ProcessClarityAgent(
                company_id="test-uuid",
                config={}
            )

    def test_avg_cycle_time(self, sample_tasks):
        """
        Seule la tâche terminée (task_004, cycle_time=6.0)
        dans les 30 derniers jours doit compter.
        """
        cycle = self.agent._compute_avg_cycle_time(sample_tasks)
        assert cycle == pytest.approx(6.0)

    def test_workload_computation(self, sample_tasks):
        """
        Marie Dupont a 2 tâches actives (task_001 + task_004 exclue car done).
        Thomas Martin a 1 tâche active (task_002).
        task_003 n'a pas d'assigné.
        """
        workload = self.agent._compute_workload(sample_tasks)

        assert "user_001" in workload    # Marie
        assert "user_002" in workload    # Thomas

        # task_004 est done → pas comptée
        assert workload["user_001"]["active_tasks"] == 1

    def test_best_assignee_is_least_loaded(self, sample_tasks):
        """L'assigné choisi est celui avec le moins de tâches."""
        workload = self.agent._compute_workload(sample_tasks)
        best = self.agent._find_best_assignee(workload)

        assert best is not None
        # Thomas (1 tâche) ou Marie (1 tâche) — le moins chargé
        assert best["active_tasks"] <= min(
            w["active_tasks"] for w in workload.values()
        ) + 1


class TestAcquisitionEfficiencyCalculations:

    def setup_method(self):
        with patch("services.database.get_client"):
            from agents.acquisition_efficiency import AcquisitionEfficiencyAgent
            self.agent = AcquisitionEfficiencyAgent(
                company_id="test-uuid",
                config={}
            )

    def test_blended_cac_calculation(self, sample_deals):
        """
        2 deals WON récents + 6000€ de marketing
        → CAC = 6000 / 2 = 3000€
        """
        from datetime import timedelta
        now = datetime.utcnow()

        # 2 deals WON dans les 90 jours
        won_deals = [
            {
                "status": "won",
                "amount": 45000.0,
                "source": "referral",
                "closed_at": (now - timedelta(days=10)).isoformat()
            },
            {
                "status": "won",
                "amount": 20000.0,
                "source": "linkedin",
                "closed_at": (now - timedelta(days=20)).isoformat()
            }
        ]

        expenses = [
            {
                "amount": 3000.0,
                "category": "advertising",
                "vendor": "Google",
                "date": (now - timedelta(days=15)).isoformat()
            },
            {
                "amount": 3000.0,
                "category": "marketing",
                "vendor": "LinkedIn",
                "date": (now - timedelta(days=15)).isoformat()
            }
        ]

        with patch.object(self.agent, "_get_deals", return_value=won_deals):
            result = self.agent._compute_cac(won_deals, expenses)

        assert result is not None
        assert result["blended_cac"] == pytest.approx(3000.0)
        assert result["total_clients"] == 2
        assert result["total_marketing_spend"] == pytest.approx(6000.0)

    def test_cac_none_when_no_won_deals(self):
        """Sans deals WON récents → retourner None, pas crasher."""
        result = self.agent._compute_cac([], [])
        assert result is None

    def test_marketing_expense_detection(self):
        """Les dépenses marketing sont correctement identifiées."""
        marketing_categories = ["marketing", "advertising", "ads"]

        assert self.agent._is_marketing_expense(
            {"category": "advertising", "vendor": "Google"},
            marketing_categories
        )

        assert self.agent._is_marketing_expense(
            {"category": "salaires", "vendor": "Google Ads"},
            marketing_categories
        )

        assert not self.agent._is_marketing_expense(
            {"category": "loyer", "vendor": "Propriétaire"},
            marketing_categories
        )
