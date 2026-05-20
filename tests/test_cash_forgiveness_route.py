"""Tests for Phase 3 Commit 3: POST /api/cash/stakes/<id>/request-forgiveness.

The borrower asks the staker to write off the carry. The staker
decides via a weighted relationship-axes score:

    score = likability * 0.5 + respect * 0.4 - heat * 0.3

`granted` when score > FORGIVENESS_THRESHOLD (0.55). Granted path
clears the carry and fires STAKE_FORGIVEN; refused path fires
STAKE_FORGIVENESS_REFUSED. Both paths stamp `forgiveness_last_asked`
to enforce the 24h rate-limit.

Test pattern mirrors test_cash_default_route.py — per-test tempdb,
patched init_persistence, create_app, auth bypass.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from cash_mode.stakes import (
    BORROWER_KIND_HUMAN,
    STAKE_FORMAT_HOUSE,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_HOUSE,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from flask_app import create_app
from poker.memory.opponent_model import RelationshipState
from poker.repositories import create_repos
from tests._sandbox_test_helper import pin_sandbox_for


# Mirror the route constants — keep them here so tests stay independent
# of the route module's import chain (which pulls in the flask limiter
# config). If these drift from the route's values, the integration
# assertions still catch it via the wire response's `threshold` field.
FORGIVENESS_THRESHOLD = 0.55


pytestmark = [pytest.mark.flask, pytest.mark.integration]


PLAYER_OWNER_ID = "test-player-1"
OTHER_PLAYER_ID = "other-player"
ANCHOR = datetime(2026, 5, 20, 12, 0, 0)


def _mock_authorization_service(user, has_admin_permission=True):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class _ForgivenessRouteBase(unittest.TestCase):
    """Shared setup: tempdb + Napoleon + auth bypass."""

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
            {
                'play_style': 'aggressive',
                'bankroll_knobs': {
                    'bankroll_cap': 50_000, 'bankroll_rate': 0,
                    'buy_in_multiplier': 1.0,
                    'stake_comfort_zone': '$10',
                },
            },
        )
        self.bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id=self.napoleon_id, chips=5_000,
            last_regen_tick=ANCHOR,
        ), sandbox_id=self.sandbox_id)

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

        self.bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=PLAYER_OWNER_ID,
            chips=5_000,
            starting_bankroll=5_000,
        ))

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
        stake_id: str = "stk-carry-1",
        borrower_id: str = PLAYER_OWNER_ID,
        staker_id=None,
        staker_kind: str = STAKER_KIND_PERSONALITY,
        format: str = STAKE_FORMAT_PURE,
        principal: int = 400,
        carry_amount: int = 250,
        status: str = STAKE_STATUS_CARRY,
        forgiveness_last_asked=None,
    ) -> Stake:
        if staker_id is None:
            staker_id = self.napoleon_id if staker_kind != STAKER_KIND_HOUSE else None
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
            stake_tier='$10',
            created_at=ANCHOR,
            settled_at=ANCHOR if status != STAKE_STATUS_ACTIVE else None,
            forgiveness_last_asked=forgiveness_last_asked,
        )
        self.stake_repo.create_stake(stake)
        # `create_stake` doesn't INSERT forgiveness_last_asked — stamp
        # it via the mark method for the rate-limit tests.
        if forgiveness_last_asked is not None:
            self.stake_repo.mark_forgiveness_asked(
                stake_id, forgiveness_last_asked,
            )
        return stake

    def _set_relationship(
        self, *, likability: float, respect: float, heat: float = 0.0,
    ) -> None:
        """Seed napoleon's view of the player at known axis values."""
        self.relationship_repo.save_relationship_state(
            observer_id=self.napoleon_id,
            opponent_id=PLAYER_OWNER_ID,
            state=RelationshipState(
                heat=heat, respect=respect, likability=likability,
                last_seen=ANCHOR, last_decay_tick=ANCHOR,
            ),
        )


class TestGranted(_ForgivenessRouteBase):
    def test_high_axes_grant_forgiveness(self):
        self._seed_carry(carry_amount=250)
        # Score = 0.85*0.5 + 0.75*0.4 - 0.0*0.3 = 0.425 + 0.30 = 0.725.
        self._set_relationship(likability=0.85, respect=0.75)

        response = self.client.post(
            '/api/cash/stakes/stk-carry-1/request-forgiveness'
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['granted'])
        self.assertEqual(payload['status'], STAKE_STATUS_SETTLED)
        self.assertEqual(payload['staker_display_name'], 'Napoleon')
        self.assertGreater(payload['score'], FORGIVENESS_THRESHOLD)

    def test_granted_clears_carry(self):
        self._seed_carry(carry_amount=250)
        self._set_relationship(likability=0.85, respect=0.75)

        self.client.post('/api/cash/stakes/stk-carry-1/request-forgiveness')

        stake = self.stake_repo.load_stake('stk-carry-1')
        self.assertEqual(stake.status, STAKE_STATUS_SETTLED)
        self.assertEqual(stake.carry_amount, 0)

    def test_granted_fires_stake_forgiven(self):
        # STAKE_FORGIVEN mirror (borrower's view of staker) shifts:
        # heat=-0.10, respect=+0.10, likability=+0.15. The borrower's
        # view of Napoleon should move positively after a grant.
        self._seed_carry(carry_amount=250)
        self._set_relationship(likability=0.85, respect=0.75)

        self.client.post('/api/cash/stakes/stk-carry-1/request-forgiveness')

        state = self.relationship_repo.load_relationship_state(
            observer_id=PLAYER_OWNER_ID, opponent_id=self.napoleon_id,
        )
        self.assertIsNotNone(state)
        # Borrower side mirrors STAKE_FORGIVEN → likability +0.15.
        self.assertGreater(state.likability, 0.5)

    def test_granted_doesnt_move_bankroll(self):
        # Forgiveness doesn't move chips — it writes off the IOU.
        self._seed_carry(carry_amount=250)
        self._set_relationship(likability=0.85, respect=0.75)
        before = self.bankroll_repo.load_player_bankroll(PLAYER_OWNER_ID).chips

        self.client.post('/api/cash/stakes/stk-carry-1/request-forgiveness')

        after = self.bankroll_repo.load_player_bankroll(PLAYER_OWNER_ID).chips
        self.assertEqual(before, after)


class TestRefused(_ForgivenessRouteBase):
    def test_low_axes_refuse_forgiveness(self):
        self._seed_carry(carry_amount=250)
        # Hostile staker — neutral baseline scores 0.45 < 0.55.
        self._set_relationship(likability=0.4, respect=0.5, heat=0.2)

        response = self.client.post(
            '/api/cash/stakes/stk-carry-1/request-forgiveness'
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload['granted'])
        self.assertEqual(payload['status'], STAKE_STATUS_CARRY)
        self.assertLess(payload['score'], FORGIVENESS_THRESHOLD)

    def test_refused_preserves_carry(self):
        self._seed_carry(carry_amount=250)
        self._set_relationship(likability=0.4, respect=0.5)

        self.client.post('/api/cash/stakes/stk-carry-1/request-forgiveness')

        stake = self.stake_repo.load_stake('stk-carry-1')
        self.assertEqual(stake.status, STAKE_STATUS_CARRY)
        self.assertEqual(stake.carry_amount, 250)

    def test_refused_fires_relationship_event(self):
        # STAKE_FORGIVENESS_REFUSED actor (staker's POV of borrower):
        # heat=+0.02, likability=-0.05. The staker's view of the
        # borrower nudges negatively.
        self._seed_carry(carry_amount=250)
        self._set_relationship(likability=0.4, respect=0.5)

        self.client.post('/api/cash/stakes/stk-carry-1/request-forgiveness')

        state = self.relationship_repo.load_relationship_state(
            observer_id=self.napoleon_id, opponent_id=PLAYER_OWNER_ID,
        )
        self.assertIsNotNone(state)
        # Started at likability 0.4 — after event, should be ~0.35.
        self.assertLess(state.likability, 0.4)


class TestRateLimit(_ForgivenessRouteBase):
    def test_second_ask_with_fresh_timestamp_returns_429(self):
        # Use real "now" so the rate-limit window is current.
        now = datetime.utcnow()
        self._seed_carry(
            carry_amount=250,
            forgiveness_last_asked=now - timedelta(hours=2),
        )
        self._set_relationship(likability=0.85, respect=0.75)

        response = self.client.post(
            '/api/cash/stakes/stk-carry-1/request-forgiveness'
        )

        self.assertEqual(response.status_code, 429)
        payload = response.get_json()
        self.assertIn('Forgiveness already requested', payload['error'])
        # ~22 hours left in the window.
        self.assertGreater(payload['retry_after_seconds'], 21 * 3600)
        self.assertLess(payload['retry_after_seconds'], 23 * 3600)

    def test_ask_outside_24h_window_proceeds(self):
        now = datetime.utcnow()
        self._seed_carry(
            carry_amount=250,
            forgiveness_last_asked=now - timedelta(hours=25),
        )
        self._set_relationship(likability=0.85, respect=0.75)

        response = self.client.post(
            '/api/cash/stakes/stk-carry-1/request-forgiveness'
        )

        # Should be 200 (not 429) — outside the window.
        self.assertEqual(response.status_code, 200)

    def test_grant_path_stamps_rate_limit(self):
        # Even successful asks stamp the timestamp — so a granted ask
        # immediately followed by another (e.g., on a re-created carry
        # if Phase 4 ever produces one) gets rate-limited.
        self._seed_carry(carry_amount=250)
        self._set_relationship(likability=0.85, respect=0.75)

        self.client.post('/api/cash/stakes/stk-carry-1/request-forgiveness')

        stake = self.stake_repo.load_stake('stk-carry-1')
        self.assertIsNotNone(stake.forgiveness_last_asked)

    def test_refused_path_stamps_rate_limit(self):
        self._seed_carry(carry_amount=250)
        self._set_relationship(likability=0.4, respect=0.5)

        self.client.post('/api/cash/stakes/stk-carry-1/request-forgiveness')

        stake = self.stake_repo.load_stake('stk-carry-1')
        self.assertIsNotNone(stake.forgiveness_last_asked)


class TestRejections(_ForgivenessRouteBase):
    def test_unknown_stake_returns_404(self):
        response = self.client.post(
            '/api/cash/stakes/does-not-exist/request-forgiveness'
        )
        self.assertEqual(response.status_code, 404)

    def test_other_borrowers_stake_returns_404(self):
        self._seed_carry(stake_id='stk-other', borrower_id=OTHER_PLAYER_ID)
        response = self.client.post(
            '/api/cash/stakes/stk-other/request-forgiveness'
        )
        self.assertEqual(response.status_code, 404)

    def test_active_stake_rejected_with_400(self):
        self._seed_carry(stake_id='stk-active', status=STAKE_STATUS_ACTIVE)
        response = self.client.post(
            '/api/cash/stakes/stk-active/request-forgiveness'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("'active'", response.get_json()['error'])

    def test_settled_stake_rejected_with_400(self):
        self._seed_carry(stake_id='stk-settled', status=STAKE_STATUS_SETTLED)
        response = self.client.post(
            '/api/cash/stakes/stk-settled/request-forgiveness'
        )
        self.assertEqual(response.status_code, 400)

    def test_house_carry_rejected_with_400(self):
        self._seed_carry(
            stake_id='stk-house', staker_id=None,
            staker_kind=STAKER_KIND_HOUSE, format=STAKE_FORMAT_HOUSE,
        )
        response = self.client.post(
            '/api/cash/stakes/stk-house/request-forgiveness'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('House', response.get_json()['error'])


class TestNoPriorRelationship(_ForgivenessRouteBase):
    def test_no_prior_relationship_falls_below_threshold(self):
        # No save_relationship_state — load returns None → neutral
        # defaults (0.5/0.5/0.0) → score 0.45 → below 0.55.
        self._seed_carry(carry_amount=250)

        response = self.client.post(
            '/api/cash/stakes/stk-carry-1/request-forgiveness'
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload['granted'])
        self.assertAlmostEqual(payload['score'], 0.45)


if __name__ == '__main__':
    unittest.main()
