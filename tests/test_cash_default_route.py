"""Tests for POST /api/cash/stakes/<stake_id>/default (Phase 2 Commit 2).

The explicit-default endpoint mutates a carry's status + zeros the
carry_amount + fires STAKE_DEFAULTED. **No bankroll movement**: the
reputation hit IS the cost (locked decision #12).

Pattern mirrors test_cash_sponsor_routes.py: tempdb + patched
init_persistence + create_app + auth bypass. Auth ensures a player
can only default their own carries.
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

from cash_mode.bankroll import PlayerBankrollState
from cash_mode.stakes import (
    BORROWER_KIND_HUMAN,
    STAKE_FORMAT_HOUSE,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_DEFAULTED,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_HOUSE,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from flask_app import create_app
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]


PLAYER_OWNER_ID = "test-player-1"
OTHER_PLAYER_ID = "other-player"
ANCHOR = datetime(2026, 5, 20, 12, 0, 0)


def _mock_authorization_service(user, has_admin_permission=True):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class _DefaultRouteBase(unittest.TestCase):
    """Shared setup: tempdb with stake_repo wired through."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

        repos = create_repos(self.test_db.name)
        self.bankroll_repo = repos['bankroll_repo']
        self.stake_repo = repos['stake_repo']
        self.relationship_repo = repos['relationship_repo']

        def mock_init_persistence():
            import flask_app.extensions as ext

            for key, value in repos.items():
                if key == 'db_path':
                    ext.persistence_db_path = value
                else:
                    setattr(ext, key, value)

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

        # Seed a baseline player bankroll so we can verify it stays
        # unchanged across default operations.
        self.bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=PLAYER_OWNER_ID,
                chips=5_000,
                starting_bankroll=5_000,
            )
        )

    def tearDown(self):
        self._authz_patcher.stop()
        self._auth_patcher.stop()
        try:
            os.unlink(self.test_db.name)
        except FileNotFoundError:
            pass

    def _seed_stake(
        self,
        *,
        stake_id: str = "stk-carry-1",
        borrower_id: str = PLAYER_OWNER_ID,
        staker_id="napoleon",
        staker_kind: str = STAKER_KIND_PERSONALITY,
        format: str = STAKE_FORMAT_PURE,
        principal: int = 400,
        carry_amount: int = 250,
        status: str = STAKE_STATUS_CARRY,
    ) -> Stake:
        stake = Stake(
            stake_id=stake_id,
            session_id=f"sess-{stake_id}",
            staker_id=staker_id,
            staker_kind=staker_kind,
            borrower_id=borrower_id,
            borrower_kind=BORROWER_KIND_HUMAN,
            format=format,
            principal=principal,
            match_amount=0,
            origination_fee=0,
            cut=0.20,
            status=status,
            carry_amount=carry_amount,
            stake_tier="$10",
            created_at=ANCHOR,
            settled_at=ANCHOR if status != STAKE_STATUS_ACTIVE else None,
        )
        self.stake_repo.create_stake(stake)
        return stake


class TestSuccessfulDefault(_DefaultRouteBase):
    def test_carry_defaults_cleanly(self):
        self._seed_stake(carry_amount=250)

        response = self.client.post('/api/cash/stakes/stk-carry-1/default')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['stake_id'], 'stk-carry-1')
        self.assertEqual(payload['status'], STAKE_STATUS_DEFAULTED)
        self.assertEqual(payload['former_carry_amount'], 250)
        self.assertEqual(payload['staker_id'], 'napoleon')

        # Persistence check.
        stake = self.stake_repo.load_stake('stk-carry-1')
        self.assertEqual(stake.status, STAKE_STATUS_DEFAULTED)
        self.assertEqual(stake.carry_amount, 0)

    def test_bankroll_unchanged_after_default(self):
        # Locked decision #12: no bankroll movement on explicit default.
        self._seed_stake(carry_amount=250)
        before = self.bankroll_repo.load_player_bankroll(PLAYER_OWNER_ID).chips

        self.client.post('/api/cash/stakes/stk-carry-1/default')

        after = self.bankroll_repo.load_player_bankroll(PLAYER_OWNER_ID).chips
        self.assertEqual(before, after)

    def test_fires_stake_defaulted_relationship_event(self):
        # The sharpest negative event in the dispatch table — confirmed
        # by checking that the lender's relationship axes moved
        # negatively after the default.
        self._seed_stake(carry_amount=250, staker_id='napoleon')

        self.client.post('/api/cash/stakes/stk-carry-1/default')

        state_lender_pov = self.relationship_repo.load_relationship_state(
            observer_id='napoleon',
            opponent_id=PLAYER_OWNER_ID,
        )
        self.assertIsNotNone(state_lender_pov)
        # STAKE_DEFAULTED actor shifts: heat=+0.30, respect=-0.30, likability=-0.20.
        self.assertGreater(state_lender_pov.heat, 0.0)
        self.assertLess(state_lender_pov.respect, 0.5)
        self.assertLess(state_lender_pov.likability, 0.5)


class TestRejections(_DefaultRouteBase):
    def test_unknown_stake_id_returns_404(self):
        response = self.client.post('/api/cash/stakes/does-not-exist/default')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], 'Stake not found')

    def test_other_borrowers_stake_returns_404(self):
        # Same 404 as missing, by design — avoids leaking other players' ids.
        self._seed_stake(
            stake_id='stk-other',
            borrower_id=OTHER_PLAYER_ID,
        )

        response = self.client.post('/api/cash/stakes/stk-other/default')
        self.assertEqual(response.status_code, 404)

    def test_cannot_default_active_stake(self):
        self._seed_stake(stake_id='stk-active', status=STAKE_STATUS_ACTIVE)
        response = self.client.post('/api/cash/stakes/stk-active/default')
        self.assertEqual(response.status_code, 400)
        self.assertIn("'active'", response.get_json()['error'])

    def test_cannot_default_settled_stake(self):
        self._seed_stake(stake_id='stk-settled', status=STAKE_STATUS_SETTLED)
        response = self.client.post('/api/cash/stakes/stk-settled/default')
        self.assertEqual(response.status_code, 400)
        self.assertIn("'settled'", response.get_json()['error'])

    def test_cannot_default_already_defaulted_stake(self):
        self._seed_stake(stake_id='stk-already', status=STAKE_STATUS_DEFAULTED)
        response = self.client.post('/api/cash/stakes/stk-already/default')
        self.assertEqual(response.status_code, 400)

    def test_cannot_default_house_carry(self):
        # House carries shouldn't exist (settle_stake_on_leave overrides
        # them to 'settled'), but if one somehow does, the route refuses.
        self._seed_stake(
            stake_id='stk-house',
            staker_id=None,
            staker_kind=STAKER_KIND_HOUSE,
            format=STAKE_FORMAT_HOUSE,
        )
        response = self.client.post('/api/cash/stakes/stk-house/default')
        self.assertEqual(response.status_code, 400)
        self.assertIn('House', response.get_json()['error'])


class TestIdempotency(_DefaultRouteBase):
    def test_second_default_returns_400_not_500(self):
        # First default succeeds; second hits the "not in carry" guard
        # rather than re-mutating or crashing.
        self._seed_stake(carry_amount=250)

        first = self.client.post('/api/cash/stakes/stk-carry-1/default')
        self.assertEqual(first.status_code, 200)

        second = self.client.post('/api/cash/stakes/stk-carry-1/default')
        self.assertEqual(second.status_code, 400)
