"""Tests for the Phase 2 Commit 3 additions to GET /api/cash/lobby.

Lobby response now carries a top-level `tier` (the player's tier at
their current playing stake — active session, else highest-affordable
stake) plus a per-table `tier` so the frontend can render a tier
indicator on each card.

Pattern mirrors test_cash_lobby_route.py + test_cash_default_route.py
but wires stake_repo into the patched extensions so tier logic
actually runs.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from cash_mode.stakes import (
    BORROWER_KIND_HUMAN,
    STAKE_STATUS_CARRY,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from cash_mode.staking_tier import (
    TIER_HOUSE_ONLY,
    TIER_PREMIUM,
    TIER_RESTRICTED,
    TIER_STANDARD,
)
from flask_app import create_app
from poker.repositories import create_repos


pytestmark = [pytest.mark.flask, pytest.mark.integration]


PLAYER_OWNER_ID = "test-player-1"
ANCHOR = datetime(2026, 5, 20, 12, 0, 0)


def _mock_authorization_service(user, has_admin_permission=True):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class _CashLobbyTierBase(unittest.TestCase):
    """Tempdb with stake_repo + chip_ledger_repo wired through."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

        repos = create_repos(self.test_db.name)
        self.bankroll_repo = repos['bankroll_repo']
        self.stake_repo = repos['stake_repo']
        self.personality_repo = repos['personality_repo']

        def mock_init_persistence():
            import flask_app.extensions as ext
            for key, value in repos.items():
                if key == 'db_path':
                    ext.persistence_db_path = value
                else:
                    setattr(ext, key, value)

        # Seed a personality so the lobby has something to populate
        # AI seats with.
        self.napoleon_id = self.personality_repo.save_personality(
            'Napoleon',
            {
                'play_style': 'aggressive',
                'bankroll_knobs': {
                    'starting_bankroll': 50_000, 'bankroll_rate': 0,
                    'buy_in_multiplier': 1.0,
                    'stake_comfort_zone': '$10',
                },
            },
        )
        self.bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id=self.napoleon_id, chips=10_000,
            last_regen_tick=ANCHOR,
        ), sandbox_id="test-sandbox-1")

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()

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
        self._authz_patcher.stop()
        self._auth_patcher.stop()
        try:
            os.unlink(self.test_db.name)
        except FileNotFoundError:
            pass

    def _seed_player_bankroll(self, chips: int):
        self.bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=PLAYER_OWNER_ID,
            chips=chips,
            starting_bankroll=chips,
        ))

    def _seed_carry(self, *, stake_id: str, carry_amount: int):
        self.stake_repo.create_stake(Stake(
            stake_id=stake_id,
            session_id=f"sess-{stake_id}",
            staker_id="napoleon",
            staker_kind=STAKER_KIND_PERSONALITY,
            borrower_id=PLAYER_OWNER_ID,
            borrower_kind=BORROWER_KIND_HUMAN,
            format="pure",
            principal=carry_amount,
            match_amount=0,
            origination_fee=0,
            cut=0.20,
            status=STAKE_STATUS_CARRY,
            carry_amount=carry_amount,
            stake_tier="$10",
            created_at=ANCHOR,
            settled_at=ANCHOR,
        ))


class TestTopLevelTier(_CashLobbyTierBase):
    def test_premium_for_player_with_no_carries(self):
        self._seed_player_bankroll(chips=5_000)
        response = self.client.get('/api/cash/lobby')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['tier'], TIER_PREMIUM)
        # tier_stake_label should be the highest stake their bankroll affords.
        # $5000 affords $10 ($400 min) but not $50 ($2000 min). Wait, $5000
        # DOES afford $50 ($2000 min) — so we expect $50.
        self.assertEqual(payload['tier_stake_label'], '$50')

    def test_standard_for_player_with_meaningful_carries(self):
        # Bankroll 300 affords $2 (min 80) but not $10 (min 400) →
        # tier_stake_label resolves to $2. $2 max_carry = 800; a 300
        # chip carry = 37.5% → standard.
        self._seed_player_bankroll(chips=300)
        self._seed_carry(stake_id='c1', carry_amount=300)
        response = self.client.get('/api/cash/lobby')
        payload = response.get_json()
        self.assertEqual(payload['tier_stake_label'], '$2')
        self.assertEqual(payload['tier'], TIER_STANDARD)

    def test_house_only_with_overflow_carry(self):
        # Bankroll 300 → tier_stake_label='$2'. $2 max_carry = 800.
        # 1000 chip carry → over cap → house_only.
        self._seed_player_bankroll(chips=300)
        self._seed_carry(stake_id='c1', carry_amount=1000)
        response = self.client.get('/api/cash/lobby')
        payload = response.get_json()
        self.assertEqual(payload['tier'], TIER_HOUSE_ONLY)


class TestPerTableTier(_CashLobbyTierBase):
    def test_each_table_carries_its_own_tier(self):
        # $2 max_carry = 800; $10 = 4000; $50 = 20000; $200 = 80000.
        # 1500 chip carry:
        #   $2:   1500/800 = 187%  → house_only
        #   $10:  1500/4000 = 37%  → standard
        #   $50:  1500/20000 = 7%  → premium
        self._seed_player_bankroll(chips=5_000)
        self._seed_carry(stake_id='c1', carry_amount=1500)

        response = self.client.get('/api/cash/lobby')
        payload = response.get_json()

        by_label = {t['stake_label']: t for t in payload['tables']}
        self.assertEqual(by_label['$2']['tier'], TIER_HOUSE_ONLY)
        self.assertEqual(by_label['$10']['tier'], TIER_STANDARD)
        self.assertEqual(by_label['$50']['tier'], TIER_PREMIUM)

    def test_premium_at_all_tables_with_no_carries(self):
        self._seed_player_bankroll(chips=5_000)
        response = self.client.get('/api/cash/lobby')
        for t in response.get_json()['tables']:
            self.assertEqual(t['tier'], TIER_PREMIUM)


class TestBackwardCompat(_CashLobbyTierBase):
    def test_existing_response_keys_preserved(self):
        # Phase 2 Commit 3 ADDS keys; pre-existing keys must remain
        # so the React frontend doesn't break before its update lands.
        self._seed_player_bankroll(chips=5_000)
        response = self.client.get('/api/cash/lobby')
        payload = response.get_json()
        for key in ('bankroll', 'tables', 'events'):
            self.assertIn(key, payload)
        for t in payload['tables']:
            for key in ('table_id', 'stake_label', 'big_blind',
                        'min_buy_in', 'max_buy_in', 'affordability',
                        'seats', 'dealer_index'):
                self.assertIn(key, t)
