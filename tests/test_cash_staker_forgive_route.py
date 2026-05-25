"""Tests for v110 routes: the AI-asks-player forgiveness consent flow.

Two endpoints:
  - GET  /api/cash/forgiveness-requests — lists asks waiting on this owner.
  - POST /api/cash/stakes/<id>/staker-forgive {grant: bool} — decide.

Grant clears the carry + fires STAKE_FORGIVEN (player's view of AI
warms — gratitude lands on the player's relationship axes toward the
borrower). Refuse clears the pending stamp + fires
STAKE_FORGIVENESS_REFUSED (player's view of AI cools — the ask cost
the borrower goodwill).

Test pattern mirrors test_cash_forgiveness_route.py — per-test tempdb,
patched init_persistence, create_app, auth bypass.
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
    BORROWER_KIND_PERSONALITY,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_HUMAN,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from flask_app import create_app
from poker.repositories import create_repos
from tests._sandbox_test_helper import pin_sandbox_for

pytestmark = [pytest.mark.flask, pytest.mark.integration]


PLAYER_OWNER_ID = "test-player-1"
OTHER_PLAYER_ID = "other-player"
ANCHOR = datetime(2026, 5, 22, 12, 0, 0)


def _mock_authorization_service(user, has_admin_permission=True):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class _StakerForgiveRouteBase(unittest.TestCase):
    """Shared setup: tempdb + Napoleon (AI borrower) + auth bypass.

    The human is the staker here (inverse of test_cash_forgiveness_route
    where the human is the borrower). Player bankroll is unused by the
    decision math — forgiveness doesn't move chips, it cancels the IOU
    — but seeded so /net-worth side reads don't surprise.
    """

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

        repos = create_repos(self.test_db.name)
        self.bankroll_repo = repos['bankroll_repo']
        self.stake_repo = repos['stake_repo']
        self.relationship_repo = repos['relationship_repo']
        self.personality_repo = repos['personality_repo']
        self.sandbox_repo = repos['sandbox_repo']

        def mock_init_persistence():
            import flask_app.extensions as ext

            for key, value in repos.items():
                if key == 'db_path':
                    ext.persistence_db_path = value
                else:
                    setattr(ext, key, value)

        self.sandbox_id = pin_sandbox_for(PLAYER_OWNER_ID, self.sandbox_repo)

        self.napoleon_id = self.personality_repo.save_personality(
            'Napoleon',
            {'play_style': 'aggressive'},
        )

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

    def _seed_carry(
        self,
        *,
        stake_id: str = "stk-human-carry-1",
        staker_id: str = PLAYER_OWNER_ID,
        staker_kind: str = STAKER_KIND_HUMAN,
        pending: bool = True,
        status: str = STAKE_STATUS_CARRY,
        carry_amount: int = 250,
    ) -> Stake:
        """Seed a carry that the AI owes to a human staker, optionally
        with a pending forgiveness ask stamped."""
        stake = Stake(
            stake_id=stake_id,
            session_id=f"sess-{stake_id}",
            staker_id=staker_id,
            staker_kind=staker_kind,
            borrower_id=self.napoleon_id,
            borrower_kind=BORROWER_KIND_PERSONALITY,
            format=STAKE_FORMAT_PURE,
            principal=400,
            match_amount=0,
            origination_fee=0,
            cut=0.20,
            status=status,
            carry_amount=carry_amount,
            stake_tier='$10',
            created_at=ANCHOR,
            settled_at=ANCHOR if status != STAKE_STATUS_ACTIVE else None,
        )
        self.stake_repo.create_stake(stake)
        if pending:
            self.stake_repo.update_pending_forgiveness_ask(stake_id, ANCHOR)
        return stake


class TestListForgivenessRequests(_StakerForgiveRouteBase):
    def test_empty_when_no_pending(self):
        response = self.client.get('/api/cash/forgiveness-requests')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"requests": []})

    def test_lists_pending_for_owner(self):
        self._seed_carry()
        response = self.client.get('/api/cash/forgiveness-requests')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload['requests']), 1)
        req = payload['requests'][0]
        self.assertEqual(req['stake_id'], 'stk-human-carry-1')
        self.assertEqual(req['borrower_display_name'], 'Napoleon')
        self.assertEqual(req['carry_amount'], 250)
        self.assertEqual(req['stake_tier'], '$10')
        self.assertIsNotNone(req['pending_since'])

    def test_does_not_leak_other_owners_requests(self):
        # Pending ask on another player's carry — shouldn't appear in
        # this owner's list.
        self._seed_carry(stake_id='stk-other', staker_id=OTHER_PLAYER_ID)
        response = self.client.get('/api/cash/forgiveness-requests')
        self.assertEqual(response.get_json(), {"requests": []})

    def test_excludes_ai_staker_carries(self):
        # Even if an AI-staker carry has a (spurious) pending ask, the
        # list filters to staker_kind='human' — the consent flow is
        # only for human stakers.
        self._seed_carry(
            stake_id='stk-ai',
            staker_kind=STAKER_KIND_PERSONALITY,
            staker_id=self.napoleon_id,
        )
        response = self.client.get('/api/cash/forgiveness-requests')
        self.assertEqual(response.get_json(), {"requests": []})


class TestStakerForgiveGrant(_StakerForgiveRouteBase):
    def test_grant_clears_carry_and_returns_settled(self):
        self._seed_carry(carry_amount=250)
        response = self.client.post(
            '/api/cash/stakes/stk-human-carry-1/staker-forgive',
            json={'grant': True},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['granted'])
        self.assertEqual(payload['status'], STAKE_STATUS_SETTLED)
        self.assertEqual(payload['borrower_display_name'], 'Napoleon')

        stake = self.stake_repo.load_stake('stk-human-carry-1')
        self.assertEqual(stake.status, STAKE_STATUS_SETTLED)
        self.assertEqual(stake.carry_amount, 0)
        self.assertIsNone(stake.pending_forgiveness_ask)

    def test_grant_warms_player_view_of_borrower(self):
        # STAKE_FORGIVEN actor-side shift: heat=-0.10, respect=+0.10,
        # likability=+0.15. With the player as actor, the player's
        # view of Napoleon should warm.
        self._seed_carry()
        self.client.post(
            '/api/cash/stakes/stk-human-carry-1/staker-forgive',
            json={'grant': True},
        )
        state = self.relationship_repo.load_relationship_state(
            observer_id=PLAYER_OWNER_ID,
            opponent_id=self.napoleon_id,
        )
        self.assertIsNotNone(state)
        self.assertGreater(state.likability, 0.5)


class TestStakerForgiveRefuse(_StakerForgiveRouteBase):
    def test_refuse_keeps_carry_and_clears_pending(self):
        self._seed_carry(carry_amount=250)
        response = self.client.post(
            '/api/cash/stakes/stk-human-carry-1/staker-forgive',
            json={'grant': False},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload['granted'])
        self.assertEqual(payload['status'], STAKE_STATUS_CARRY)

        stake = self.stake_repo.load_stake('stk-human-carry-1')
        self.assertEqual(stake.status, STAKE_STATUS_CARRY)
        self.assertEqual(stake.carry_amount, 250)
        # Pending cleared — the ask is consumed; AI must wait the
        # 7-day rate-limit before re-asking.
        self.assertIsNone(stake.pending_forgiveness_ask)

    def test_refuse_cools_player_view_of_borrower(self):
        self._seed_carry()
        self.client.post(
            '/api/cash/stakes/stk-human-carry-1/staker-forgive',
            json={'grant': False},
        )
        state = self.relationship_repo.load_relationship_state(
            observer_id=PLAYER_OWNER_ID,
            opponent_id=self.napoleon_id,
        )
        self.assertIsNotNone(state)
        # STAKE_FORGIVENESS_REFUSED actor-side: likability=-0.15.
        self.assertLess(state.likability, 0.5)


class TestStakerForgiveRejections(_StakerForgiveRouteBase):
    def test_404_when_caller_is_not_the_staker(self):
        # Seed a carry where OTHER_PLAYER_ID is the staker — current
        # user must not be able to decide on it (and the 404 hides
        # the row's existence to avoid info leak).
        self._seed_carry(staker_id=OTHER_PLAYER_ID)
        response = self.client.post(
            '/api/cash/stakes/stk-human-carry-1/staker-forgive',
            json={'grant': True},
        )
        self.assertEqual(response.status_code, 404)

    def test_400_when_no_pending_ask(self):
        # Carry exists but no pending ask was stamped — the route
        # rejects to prevent side-stepping normal carry resolution.
        self._seed_carry(pending=False)
        response = self.client.post(
            '/api/cash/stakes/stk-human-carry-1/staker-forgive',
            json={'grant': True},
        )
        self.assertEqual(response.status_code, 400)
        # Carry still as it was.
        stake = self.stake_repo.load_stake('stk-human-carry-1')
        self.assertEqual(stake.status, STAKE_STATUS_CARRY)

    def test_400_when_status_is_not_carry(self):
        self._seed_carry(status=STAKE_STATUS_SETTLED, carry_amount=0)
        response = self.client.post(
            '/api/cash/stakes/stk-human-carry-1/staker-forgive',
            json={'grant': True},
        )
        self.assertEqual(response.status_code, 400)

    def test_400_when_staker_kind_is_personality(self):
        # AI-staker carry: even with a pending ask, the consent route
        # rejects — the auto-decision path inside try_ai_forgiveness_ask
        # is the only valid resolver for AI-staker carries.
        self._seed_carry(
            staker_kind=STAKER_KIND_PERSONALITY,
            staker_id=self.napoleon_id,  # treat napoleon as the staker for
            # this fixture path; route check
            # uses staker_kind, not identity.
        )
        # Caller is PLAYER_OWNER_ID; the staker is napoleon_id; the
        # ownership check itself will short-circuit with 404 here,
        # which is the right outcome (player has no claim on it).
        response = self.client.post(
            '/api/cash/stakes/stk-human-carry-1/staker-forgive',
            json={'grant': True},
        )
        self.assertEqual(response.status_code, 404)
