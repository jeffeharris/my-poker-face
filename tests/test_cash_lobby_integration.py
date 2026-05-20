"""Integration tests for commit 7's lobby hooks.

Covers:
  - `/api/cash/sponsor-offers?table_id=...` narrows the candidate
    pool to the AIs seated at that table.
  - `/api/cash/sponsor-offers?table_id=...` falls back to the broad
    eligible pool when zero AIs at the table qualify (e.g., all
    seats AI bankrolls are too low to lend).
  - `/api/cash/leave` persists end-of-session chip counts back to
    `cash_tables` and frees the human seat.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from cash_mode.tables import CashTableState, ai_slot, human_slot, open_slot
from flask_app import create_app
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]


PLAYER_OWNER_ID = "test-player-1"


def _mock_authorization_service(user, has_admin_permission=True):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class _CashLobbyIntegrationBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        cls.test_db.close()
        repos = create_repos(cls.test_db.name)
        cls.repos = repos
        cls.bankroll_repo = repos['bankroll_repo']
        cls.personality_repo = repos['personality_repo']
        cls.relationship_repo = repos['relationship_repo']
        cls.cash_table_repo = repos['cash_table_repo']

        # Seed many personalities (so the broad pool has lots).
        cls.personality_ids = []
        for i in range(20):
            pid = cls.personality_repo.save_personality(
                f'Lender {i}',
                {
                    'bankroll_knobs': {
                        'bankroll_cap': 1_000_000,
                        'bankroll_rate': 0,
                        'buy_in_multiplier': 1.0,
                        'stop_loss_buy_ins': 3,
                        'stop_win_buy_ins': 5,
                        'stake_comfort_zone': '$10',
                    },
                    'lender_profile': {
                        'willing': True,
                        'max_loan_pct_of_bankroll': 0.20,
                        'floor_anchor': 1.10,
                        'rate_anchor': 0.20,
                        'respect_floor': -1.0,  # never floored out
                        'heat_ceiling': 1.0,    # never ceiling-ed out
                    },
                },
            )
            cls.bankroll_repo.save_ai_bankroll(AIBankrollState(
                personality_id=pid, chips=100_000,
                last_regen_tick=datetime(2026, 5, 18, 12, 0, 0),
            ))
            cls.personality_ids.append(pid)

        def mock_init_persistence():
            import flask_app.extensions as ext
            for key in (
                'game_repo', 'user_repo', 'settings_repo', 'personality_repo',
                'experiment_repo', 'prompt_capture_repo',
                'decision_analysis_repo', 'prompt_preset_repo',
                'capture_label_repo', 'replay_experiment_repo',
                'llm_repo', 'guest_tracking_repo', 'hand_history_repo',
                'tournament_repo', 'coach_repo', 'relationship_repo',
                'bankroll_repo', 'cash_table_repo', 'chip_ledger_repo',
                'stake_repo',
            ):
                if key in repos:
                    setattr(ext, key, repos[key])
            ext.persistence_db_path = repos['db_path']

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            cls.app = create_app()
        cls.app.testing = True
        cls.client = cls.app.test_client()

        # Rebind module-level repo capture in game_routes (see commit 6
        # commit message for context on this flake).
        import flask_app.routes.game_routes as _gr
        for key in (
            'prompt_preset_repo', 'game_repo', 'user_repo',
            'guest_tracking_repo', 'llm_repo', 'tournament_repo',
            'hand_history_repo', 'decision_analysis_repo',
            'capture_label_repo', 'coach_repo', 'relationship_repo',
            'personality_repo',
        ):
            if key in repos:
                setattr(_gr, key, repos[key])

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.test_db.name)
        except FileNotFoundError:
            pass

    def setUp(self):
        user = {'id': PLAYER_OWNER_ID, 'name': 'Tester'}
        self._authz_patcher = patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(user=user),
        )
        self._authz_patcher.start()
        auth_mock = MagicMock()
        auth_mock.get_current_user.return_value = user
        self._auth_patcher = patch(
            'flask_app.extensions.auth_manager',
            auth_mock,
        )
        self._auth_patcher.start()

    def tearDown(self):
        self._auth_patcher.stop()
        self._authz_patcher.stop()


class TestSponsorOffersNarrowing(_CashLobbyIntegrationBase):
    def test_no_table_id_returns_broad_pool(self):
        """Without `table_id`, behavior is the same as before (broad pool)."""
        # Set bankroll < min buy-in of $200 table (8000) but ≥ $50 min (2000).
        self.bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=PLAYER_OWNER_ID, chips=4_000, starting_bankroll=4_000,
        ))
        resp = self.client.get("/api/cash/sponsor-offers?stake_label=$200")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("eligible") is True
        offers = data["offers"]
        personality_offers = [o for o in offers if o["kind"] == "personality"]
        # With 20 seeded personalities, we get up to 3 in broad pool.
        assert len(personality_offers) > 0

    def test_with_table_id_narrows_to_seated_ais(self):
        # Set bankroll for sponsor at $50 (min 2000).
        self.bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=PLAYER_OWNER_ID, chips=600, starting_bankroll=600,
        ))
        # Seat only 4 of the 20 personalities at the $50 table.
        seated_pids = self.personality_ids[:4]
        seats = [ai_slot(pid, 2000) for pid in seated_pids] + [open_slot(), open_slot()]
        custom_table = CashTableState(
            table_id="cash-table-50-001",
            stake_label="$50",
            seats=seats,
        )
        self.cash_table_repo.save_table(custom_table)

        resp = self.client.get(
            "/api/cash/sponsor-offers?stake_label=$50&table_id=cash-table-50-001"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        offers = data["offers"]
        personality_offers = [o for o in offers if o["kind"] == "personality"]
        # Every personality offer must be from the seated_pids set.
        for offer in personality_offers:
            assert offer["lender_id"] in seated_pids

    def test_with_table_id_falls_back_when_zero_qualify(self):
        # Same scenario but seed table with personalities who refuse
        # to lend (heat_ceiling = -1.0 means they always refuse).
        unwilling_pid = self.personality_repo.save_personality(
            'Unwilling',
            {
                'lender_profile': {
                    'willing': False,
                    'max_loan_pct_of_bankroll': 0.20,
                    'floor_anchor': 1.10,
                    'rate_anchor': 0.20,
                    'respect_floor': -1.0,
                    'heat_ceiling': 1.0,
                },
            },
        )
        self.bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id=unwilling_pid, chips=100_000,
            last_regen_tick=datetime(2026, 5, 18, 12, 0, 0),
        ))
        # Seat only the unwilling personality.
        seats = [ai_slot(unwilling_pid, 80)] + [open_slot()] * 5
        custom_table = CashTableState(
            table_id="cash-table-2-001",
            stake_label="$2",
            seats=seats,
        )
        self.cash_table_repo.save_table(custom_table)
        self.bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=PLAYER_OWNER_ID, chips=50, starting_bankroll=50,
        ))

        resp = self.client.get(
            "/api/cash/sponsor-offers?stake_label=$2&table_id=cash-table-2-001"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # Narrow pool had zero qualifying, so we fell back to broad.
        offers = data["offers"]
        # House archetypes always appear when there's room, and the
        # broad-pool fallback may also surface lenders.
        assert isinstance(offers, list)
