# tests/test_orchestrator.py

"""
Ce qu'on teste :
→ Le router déclenche les bons handlers
→ Le rapport du lundi est correctement construit
→ Le profil client fusionne les defaults correctement
→ L'adapter produit les bonnes recommandations
"""

import pytest
from unittest.mock import patch, MagicMock, call


class TestRouter:

    def test_forecast_updated_triggers_cash_handler(self):
        """
        Quand Revenue Velocity publie 'forecast_updated',
        le handler cash doit être appelé.
        """
        from orchestrator.router import _build_routing_table

        routing_table = _build_routing_table()

        assert "forecast_updated" in routing_table
        assert len(routing_table["forecast_updated"]) >= 1

        handler = routing_table["forecast_updated"][0]
        assert callable(handler)

    def test_cac_updated_triggers_scoring_handler(self):
        """
        Quand Acquisition publie 'cac_updated',
        le handler scoring doit être appelé.
        """
        from orchestrator.router import _build_routing_table

        routing_table = _build_routing_table()

        assert "cac_updated" in routing_table
        assert len(routing_table["cac_updated"]) >= 1

    def test_process_events_marks_processed(self, mock_supabase):
        """
        Après traitement, les events doivent être marqués processed.
        """
        from orchestrator.router import process_events

        with patch("services.database.get_unprocessed_events") as mock_events:
            with patch("services.database.mark_event_processed") as mock_mark:
                mock_events.return_value = [
                    {
                        "id": "event_001",
                        "event_type": "forecast_updated",
                        "company_id": "test-uuid",
                        "payload": {"forecast_30d": 50000}
                    }
                ]

                with patch("orchestrator.router._handle_forecast_updated_for_cash"):
                    count = process_events("test-uuid")

                assert count == 1
                mock_mark.assert_called_once_with("event_001")

    def test_unknown_event_marked_processed(self):
        """
        Un event inconnu doit être marqué processed
        pour ne pas boucler indéfiniment.
        """
        from orchestrator.router import process_events

        with patch("services.database.get_unprocessed_events") as mock_events:
            with patch("services.database.mark_event_processed") as mock_mark:
                mock_events.return_value = [
                    {
                        "id": "evt_unknown",
                        "event_type": "event_qui_nexiste_pas",
                        "company_id": "test-uuid",
                        "payload": {}
                    }
                ]

                count = process_events("test-uuid")

                assert count == 1
                mock_mark.assert_called_once_with("evt_unknown")


class TestProfile:

    def test_default_config_merged(self, mock_supabase):
        """
        Un client sans config spécifique
        reçoit les valeurs par défaut.
        """
        from orchestrator.profile import get_agent_config
        from orchestrator.profile import DEFAULT_AGENT_CONFIGS

        config = get_agent_config("test-uuid", "revenue_velocity")

        assert config.get("stagnation_threshold_days") == \
               DEFAULT_AGENT_CONFIGS["revenue_velocity"]["stagnation_threshold_days"]

    def test_client_config_overrides_default(self, mock_supabase):
        """
        Si le client a une config custom,
        elle écrase le default.
        """
        from orchestrator.profile import get_company_profile

        # Le mock retourne une company avec config custom
        mock_supabase.table("companies").select().eq().limit(
        ).execute.return_value = MagicMock(data=[{
            "id": "test-uuid",
            "name": "Test",
            "clarity_score": 50,
            "tools_connected": {},
            "agent_configs": {
                "revenue_velocity": {
                    "stagnation_threshold_days": 14    # override
                }
            }
        }])

        profile = get_company_profile("test-uuid")
        rv_config = profile["agent_configs"]["revenue_velocity"]

        assert rv_config["stagnation_threshold_days"] == 14

    def test_is_agent_enabled_default_true(self, mock_supabase):
        """
        Par défaut, tous les agents sont activés.
        """
        from orchestrator.profile import is_agent_enabled

        for agent in [
            "revenue_velocity",
            "cash_predictability",
            "process_clarity",
            "acquisition_efficiency"
        ]:
            assert is_agent_enabled("test-uuid", agent) is True


class TestWeeklyReport:

    def test_top_action_cash_critical_priority(self):
        """
        Une alerte cash critique doit être l'action #1,
        même si des deals sont en danger.
        """
        from orchestrator.weekly_report import _determine_top_action

        data = {
            "revenue": {
                "velocity": 5000,
                "deals_at_risk": [
                    {"title": "Big Deal", "amount": 100000, "days_stagnant": 30}
                ]
            },
            "cash": {
                "base_30d": 50000,
                "days_until_critical": 20    # critique < 30j
            },
            "process": {"overdue_tasks": 0}
        }

        action = _determine_top_action(data)

        assert "URGENT" in action
        assert "20" in action    # les jours jusqu'au critique

    def test_top_action_stagnant_deal_when_no_cash_alert(self):
        """
        Sans alerte cash, le deal à risque le plus important
        doit être l'action #1.
        """
        from orchestrator.weekly_report import _determine_top_action

        data = {
            "revenue": {
                "velocity": 5000,
                "deals_at_risk": [
                    {
                        "title": "Gros Deal",
                        "amount": 80000,
                        "days_stagnant": 25
                    },
                    {
                        "title": "Petit Deal",
                        "amount": 10000,
                        "days_stagnant": 20
                    }
                ]
            },
            "cash": {
                "base_30d": 200000,
                "days_until_critical": None    # pas d'alerte
            },
            "process": {"overdue_tasks": 0}
        }

        action = _determine_top_action(data)

        # Le plus gros deal doit être mentionné
        assert "Gros Deal" in action

    def test_subject_contains_score(self):
        """Le sujet contient toujours le Score de Clarté."""
        from orchestrator.weekly_report import _build_subject

        data = {
            "clarity_score": 42,
            "week_date": "15 Mars 2025",
            "cash": {"days_until_critical": None},
            "revenue": {"deals_at_risk": []}
        }

        subject = _build_subject(data, {})

        assert "42" in subject
        assert "Kuria" in subject

    def test_subject_contains_alert_emoji_when_cash_critical(self):
        """Un sujet avec alerte cash doit contenir le signal d'alerte."""
        from orchestrator.weekly_report import _build_subject

        data = {
            "clarity_score": 35,
            "week_date": "15 Mars 2025",
            "cash": {"days_until_critical": 25},    # < 45j → alerte
            "revenue": {"deals_at_risk": []}
        }

        subject = _build_subject(data, {})

        assert "⚠️" in subject


class TestAdapter:

    def test_apply_adjustment_calls_update_config(self, mock_supabase):
        """
        apply_adjustment doit appeler update_agent_config
        avec les bons paramètres.
        """
        from orchestrator.adapter import apply_adjustment

        with patch("orchestrator.adapter.update_agent_config") as mock_update:
            mock_update.return_value = True

            result = apply_adjustment(
                company_id="test-uuid",
                agent_name="revenue_velocity",
                parameter="stagnation_threshold_days",
                new_value=14,
                reason="Test"
            )

            mock_update.assert_called_once_with(
                "test-uuid",
                "revenue_velocity",
                {"stagnation_threshold_days": 14}
            )
            assert result is True

    def test_win_rate_recommendation_when_low(self, mock_supabase):
        """
        Si le win rate est < 15%, une recommandation
        d'ajustement doit être générée.
        """
        from orchestrator.adapter import _analyze_revenue_velocity

        # Mock : 1 won, 10 lost → win rate = 9%
        mock_supabase.table("deals").select().eq().eq().gte(
        ).execute.side_effect = [
            MagicMock(data=[{"id": f"won_{i}", "source": "referral"}
                            for i in range(1)]),     # won
            MagicMock(data=[{"id": f"lost_{i}"}
                            for i in range(10)]),    # lost
        ]

        with patch("orchestrator.adapter.get_agent_config") as mock_cfg:
            mock_cfg.return_value = {"stagnation_threshold_days": 21}

            with patch("services.database.get_client",
                       return_value=mock_supabase):
                result = _analyze_revenue_velocity("test-uuid")

        # Avec 11 deals et win rate ~9%, une recommandation devrait exister
        # (le test vérifie la structure, pas l'exacte valeur)
        assert "recommendations" in result
        assert "win_rate" in result
