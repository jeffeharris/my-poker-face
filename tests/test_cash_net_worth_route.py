"""Tests for Phase 3 Commit 1: Net Worth API + voluntary payoff route
plus the lobby's per-AI-seat carry annotation.

The Net Worth route returns the player's bankroll + outstanding
carries + tier status + headroom. The voluntary payoff route clears a
carry by debiting the player's bankroll and crediting the staker's
bankroll. The lobby annotation surfaces per-seat carry amounts so
TableCard can render a corner pin on lenders the player owes.

Test pattern mirrors `test_cash_default_route.py` — per-test tempdb,
patched `init_persistence`, `create_app`, auth bypass.
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
from cash_mode.staking_tier import (
    TIER_HOUSE_ONLY,
    TIER_PREMIUM,
    TIER_RESTRICTED,
    TIER_STANDARD,
)
from cash_mode.tables import CashTableState, ai_slot, open_slot
from flask_app import create_app
from poker.repositories import create_repos
from tests._sandbox_test_helper import TEST_SANDBOX_ID, pin_sandbox_for

pytestmark = [pytest.mark.flask, pytest.mark.integration]


PLAYER_OWNER_ID = "test-player-1"
OTHER_PLAYER_ID = "other-player"
ANCHOR = datetime(2026, 5, 20, 12, 0, 0)


def _mock_authorization_service(user, has_admin_permission=True):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class _NetWorthRouteBase(unittest.TestCase):
    """Shared setup: tempdb + Napoleon personality + auth bypass."""

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

        # Pin the resolver so the route's sandbox lookup matches what
        # direct repo seeds write to. Required for credit_ai_cash_out
        # (the payoff route's staker credit) to find Napoleon's row.
        self.sandbox_id = pin_sandbox_for(PLAYER_OWNER_ID, self.sandbox_repo)

        # Seed Napoleon so the payables payload can resolve a display name.
        self.napoleon_id = self.personality_repo.save_personality(
            'Napoleon',
            {
                'play_style': 'aggressive',
                'bankroll_knobs': {
                    'starting_bankroll': 50_000,
                    'bankroll_rate': 0,
                    'buy_in_multiplier': 1.0,
                    'stake_comfort_zone': '$10',
                },
            },
            circulating=True,
        )
        # Napoleon needs a bankroll row for credit_ai_cash_out to land.
        self.bankroll_repo.save_ai_bankroll(
            AIBankrollState(
                personality_id=self.napoleon_id,
                chips=5_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=self.sandbox_id,
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

        # Seed a baseline bankroll for the player.
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
        stake_id: str = "stk-carry-1",
        borrower_id: str = PLAYER_OWNER_ID,
        staker_id=None,
        staker_kind: str = STAKER_KIND_PERSONALITY,
        format: str = STAKE_FORMAT_PURE,
        principal: int = 400,
        carry_amount: int = 250,
        stake_tier: str = "$10",
        status: str = STAKE_STATUS_CARRY,
    ) -> Stake:
        # staker_id defaults to napoleon_id because the route's name
        # resolution reads personality_repo, and we want a concrete
        # display_name in the response.
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
            stake_tier=stake_tier,
            created_at=ANCHOR,
            settled_at=ANCHOR if status != STAKE_STATUS_ACTIVE else None,
        )
        self.stake_repo.create_stake(stake)
        return stake


class TestNetWorthShape(_NetWorthRouteBase):
    def test_empty_state_has_all_keys(self):
        # No carries seeded; baseline bankroll = $5,000.
        response = self.client.get('/api/cash/net-worth')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        for key in (
            'bankroll',
            'tier_stake_label',
            'tier_status',
            'carry_cap',
            'payables',
            'receivables',
            'net_worth',
            'available',
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload['bankroll'], 5_000)
        self.assertEqual(payload['payables'], [])
        # Phase 5 stub: receivables structural slot is present but empty.
        self.assertEqual(payload['receivables'], [])

    def test_tier_status_is_premium_with_no_carries(self):
        # carry_load = 0 → premium tier at any stake label.
        response = self.client.get('/api/cash/net-worth')
        payload = response.get_json()
        self.assertEqual(payload['tier_status'], TIER_PREMIUM)

    def test_tier_stake_label_is_highest_affordable(self):
        # $5,000 bankroll covers up to $50 stakes ($50 min_buy_in = $2,000).
        response = self.client.get('/api/cash/net-worth')
        payload = response.get_json()
        self.assertEqual(payload['tier_stake_label'], '$50')

    def test_carry_cap_matches_tier_stake(self):
        # carry_cap = 10 × min_buy_in @ $50 = 10 × (50 × 40) = 20_000.
        response = self.client.get('/api/cash/net-worth')
        payload = response.get_json()
        self.assertEqual(payload['carry_cap'], 20_000)
        # No payables yet, so available headroom = carry_cap.
        self.assertEqual(payload['available'], 20_000)

    def test_net_worth_equals_bankroll_when_no_carries(self):
        response = self.client.get('/api/cash/net-worth')
        payload = response.get_json()
        self.assertEqual(payload['net_worth'], payload['bankroll'])


class TestNetWorthPayables(_NetWorthRouteBase):
    def test_single_carry_appears_in_payables(self):
        self._seed_carry(carry_amount=250, principal=400)

        response = self.client.get('/api/cash/net-worth')
        payload = response.get_json()

        self.assertEqual(len(payload['payables']), 1)
        p = payload['payables'][0]
        self.assertEqual(p['stake_id'], 'stk-carry-1')
        self.assertEqual(p['staker_id'], self.napoleon_id)
        self.assertEqual(p['staker_kind'], STAKER_KIND_PERSONALITY)
        self.assertEqual(p['staker_display_name'], 'Napoleon')
        self.assertEqual(p['carry_amount'], 250)
        self.assertEqual(p['principal'], 400)
        self.assertEqual(p['stake_tier'], '$10')
        self.assertIsNotNone(p['created_at'])

    def test_net_worth_subtracts_payables(self):
        self._seed_carry(carry_amount=300)
        self._seed_carry(stake_id='stk-carry-2', carry_amount=400)

        response = self.client.get('/api/cash/net-worth')
        payload = response.get_json()

        # bankroll 5000, payables sum 700, receivables 0.
        self.assertEqual(payload['bankroll'], 5_000)
        self.assertEqual(payload['net_worth'], 5_000 - 700)
        # available = carry_cap (20_000 @ $50) − payables_sum (700).
        self.assertEqual(payload['available'], 20_000 - 700)

    def test_house_carry_excluded_from_payables(self):
        # House carries shouldn't exist post-settlement; the route
        # defensively skips them rather than crashing on NULL staker_id.
        self._seed_carry(
            stake_id='stk-house',
            staker_kind=STAKER_KIND_HOUSE,
            format=STAKE_FORMAT_HOUSE,
            staker_id=None,
            carry_amount=100,
        )

        response = self.client.get('/api/cash/net-worth')
        payload = response.get_json()
        self.assertEqual(payload['payables'], [])

    def test_other_borrowers_carries_invisible(self):
        # A carry that belongs to another player must not leak into
        # this player's net worth.
        self._seed_carry(
            stake_id='stk-other',
            borrower_id=OTHER_PLAYER_ID,
            carry_amount=999,
        )

        response = self.client.get('/api/cash/net-worth')
        payload = response.get_json()
        self.assertEqual(payload['payables'], [])


class TestNetWorthTierDegradation(_NetWorthRouteBase):
    """Carry load drives the tier_status response."""

    def test_load_under_20pct_stays_premium(self):
        # carry_cap @ $50 = 20_000; 10% = 2_000. Under 20% threshold.
        self._seed_carry(carry_amount=2_000)
        payload = self.client.get('/api/cash/net-worth').get_json()
        self.assertEqual(payload['tier_status'], TIER_PREMIUM)

    def test_load_at_30pct_drops_to_standard(self):
        # 30% of 20_000 = 6_000. Above 20% threshold → standard.
        self._seed_carry(carry_amount=6_000)
        payload = self.client.get('/api/cash/net-worth').get_json()
        self.assertEqual(payload['tier_status'], TIER_STANDARD)

    def test_load_at_70pct_drops_to_restricted(self):
        self._seed_carry(carry_amount=14_000)
        payload = self.client.get('/api/cash/net-worth').get_json()
        self.assertEqual(payload['tier_status'], TIER_RESTRICTED)

    def test_load_at_100pct_drops_to_house_only(self):
        self._seed_carry(carry_amount=20_000)
        payload = self.client.get('/api/cash/net-worth').get_json()
        self.assertEqual(payload['tier_status'], TIER_HOUSE_ONLY)

    def test_available_clamps_to_zero_when_overdrawn(self):
        # Over-cap carry: available headroom can't go negative.
        self._seed_carry(carry_amount=25_000)
        payload = self.client.get('/api/cash/net-worth').get_json()
        self.assertEqual(payload['available'], 0)


class TestPayoffSuccess(_NetWorthRouteBase):
    def test_payoff_clears_carry_and_returns_settled(self):
        self._seed_carry(carry_amount=250)

        response = self.client.post('/api/cash/stakes/stk-carry-1/payoff')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['stake_id'], 'stk-carry-1')
        self.assertEqual(payload['status'], STAKE_STATUS_SETTLED)
        self.assertEqual(payload['paid'], 250)
        self.assertEqual(payload['bankroll'], 5_000 - 250)
        self.assertEqual(payload['staker_id'], self.napoleon_id)

        # Persistence checks.
        stake = self.stake_repo.load_stake('stk-carry-1')
        self.assertEqual(stake.status, STAKE_STATUS_SETTLED)
        self.assertEqual(stake.carry_amount, 0)
        self.assertIsNotNone(stake.settled_at)

    def test_payoff_debits_player_bankroll(self):
        self._seed_carry(carry_amount=750)

        self.client.post('/api/cash/stakes/stk-carry-1/payoff')

        bankroll = self.bankroll_repo.load_player_bankroll(PLAYER_OWNER_ID)
        self.assertEqual(bankroll.chips, 5_000 - 750)

    def test_payoff_credits_staker_bankroll(self):
        self._seed_carry(carry_amount=500)
        # Napoleon starts at 5,000 (seeded in setUp); cap is 50,000,
        # regen rate is 0. After payoff, projected stays at 5_000 and
        # the +500 credit lands → 5_500.
        before = self.bankroll_repo.load_ai_bankroll(
            self.napoleon_id,
            sandbox_id=self.sandbox_id,
        )
        self.assertEqual(before.chips, 5_000)

        self.client.post('/api/cash/stakes/stk-carry-1/payoff')

        after = self.bankroll_repo.load_ai_bankroll(
            self.napoleon_id,
            sandbox_id=self.sandbox_id,
        )
        self.assertEqual(after.chips, 5_500)

    def test_payoff_fires_stake_repaid_event(self):
        # STAKE_REPAID actor (staker's view of borrower) shifts:
        # heat=-0.05, respect=+0.15, likability=+0.10. So observer=
        # staker, opponent=borrower should move positively.
        self._seed_carry(carry_amount=250)

        self.client.post('/api/cash/stakes/stk-carry-1/payoff')

        state = self.relationship_repo.load_relationship_state(
            observer_id=self.napoleon_id,
            opponent_id=PLAYER_OWNER_ID,
        )
        self.assertIsNotNone(state)
        self.assertGreater(state.respect, 0.5)
        self.assertGreater(state.likability, 0.5)


class TestPayoffRejections(_NetWorthRouteBase):
    def test_unknown_stake_returns_404(self):
        response = self.client.post('/api/cash/stakes/does-not-exist/payoff')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], 'Stake not found')

    def test_other_borrowers_stake_returns_404(self):
        # Same 404 as missing — no enumeration leak.
        self._seed_carry(stake_id='stk-other', borrower_id=OTHER_PLAYER_ID)
        response = self.client.post('/api/cash/stakes/stk-other/payoff')
        self.assertEqual(response.status_code, 404)

    def test_active_stake_rejected_with_400(self):
        self._seed_carry(stake_id='stk-active', status=STAKE_STATUS_ACTIVE)
        response = self.client.post('/api/cash/stakes/stk-active/payoff')
        self.assertEqual(response.status_code, 400)
        self.assertIn("'active'", response.get_json()['error'])

    def test_settled_stake_rejected_with_400(self):
        self._seed_carry(stake_id='stk-settled', status=STAKE_STATUS_SETTLED)
        response = self.client.post('/api/cash/stakes/stk-settled/payoff')
        self.assertEqual(response.status_code, 400)

    def test_defaulted_stake_rejected_with_400(self):
        self._seed_carry(stake_id='stk-defaulted', status=STAKE_STATUS_DEFAULTED)
        response = self.client.post('/api/cash/stakes/stk-defaulted/payoff')
        self.assertEqual(response.status_code, 400)

    def test_house_carry_rejected_with_400(self):
        # House carries shouldn't reach this route; defensive guard.
        self._seed_carry(
            stake_id='stk-house',
            staker_id=None,
            staker_kind=STAKER_KIND_HOUSE,
            format=STAKE_FORMAT_HOUSE,
        )
        response = self.client.post('/api/cash/stakes/stk-house/payoff')
        self.assertEqual(response.status_code, 400)
        self.assertIn('House', response.get_json()['error'])

    def test_insufficient_bankroll_rejected_with_400(self):
        # Player has 5_000; carry is 6_000. Reject.
        self._seed_carry(carry_amount=6_000)

        response = self.client.post('/api/cash/stakes/stk-carry-1/payoff')

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn('Insufficient', payload['error'])
        self.assertEqual(payload['bankroll'], 5_000)
        self.assertEqual(payload['carry_amount'], 6_000)

    def test_missing_staker_bankroll_returns_503_without_mutation(self):
        # Stake exists with a staker_id that has no ai_bankroll_state
        # row (e.g. a personality that was deleted post-stake-creation).
        # The route must NOT debit the player and flip the stake to
        # settled in this case — without the pre-flight check, the
        # player would be charged for an evaporating credit.
        self._seed_carry(
            stake_id='stk-missing-staker',
            staker_id='ghost-personality',  # no bankroll row seeded
        )
        before_bankroll = self.bankroll_repo.load_player_bankroll(
            PLAYER_OWNER_ID,
        ).chips

        response = self.client.post(
            '/api/cash/stakes/stk-missing-staker/payoff',
        )

        self.assertEqual(response.status_code, 503)
        # Bankroll untouched.
        after_bankroll = self.bankroll_repo.load_player_bankroll(
            PLAYER_OWNER_ID,
        ).chips
        self.assertEqual(before_bankroll, after_bankroll)
        # Stake still in carry status.
        stake = self.stake_repo.load_stake('stk-missing-staker')
        self.assertEqual(stake.status, STAKE_STATUS_CARRY)
        self.assertEqual(stake.carry_amount, 250)

    def test_insufficient_bankroll_doesnt_mutate(self):
        # Reject path must leave stake + bankroll untouched.
        self._seed_carry(carry_amount=6_000)
        before_bankroll = self.bankroll_repo.load_player_bankroll(
            PLAYER_OWNER_ID,
        ).chips

        self.client.post('/api/cash/stakes/stk-carry-1/payoff')

        stake = self.stake_repo.load_stake('stk-carry-1')
        self.assertEqual(stake.status, STAKE_STATUS_CARRY)
        self.assertEqual(stake.carry_amount, 6_000)
        after_bankroll = self.bankroll_repo.load_player_bankroll(
            PLAYER_OWNER_ID,
        ).chips
        self.assertEqual(before_bankroll, after_bankroll)


class TestLobbyCarryAnnotation(unittest.TestCase):
    """Lobby AI seats gain a `carry_amount` field when the player has
    an outstanding carry to that personality."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

        repos = create_repos(self.test_db.name)
        self.bankroll_repo = repos['bankroll_repo']
        self.stake_repo = repos['stake_repo']
        self.personality_repo = repos['personality_repo']
        self.cash_table_repo = repos['cash_table_repo']
        self.sandbox_repo = repos['sandbox_repo']

        def mock_init_persistence():
            import flask_app.extensions as ext

            for key in (
                'game_repo',
                'user_repo',
                'settings_repo',
                'personality_repo',
                'experiment_repo',
                'prompt_capture_repo',
                'decision_analysis_repo',
                'prompt_preset_repo',
                'capture_label_repo',
                'replay_experiment_repo',
                'llm_repo',
                'guest_tracking_repo',
                'hand_history_repo',
                'tournament_repo',
                'coach_repo',
                'relationship_repo',
                'bankroll_repo',
                'cash_table_repo',
                'chip_ledger_repo',
                'stake_repo',
                'sandbox_repo',
            ):
                if key in repos:
                    setattr(ext, key, repos[key])
            ext.persistence_db_path = repos['db_path']

        self.sandbox_id = pin_sandbox_for(PLAYER_OWNER_ID, self.sandbox_repo)

        self.napoleon_id = self.personality_repo.save_personality(
            'Napoleon',
            {
                'play_style': 'aggressive',
                'bankroll_knobs': {
                    'starting_bankroll': 50_000,
                    'bankroll_rate': 0,
                    'buy_in_multiplier': 1.0,
                    'stake_comfort_zone': '$10',
                },
            },
            circulating=True,
        )
        self.bankroll_repo.save_ai_bankroll(
            AIBankrollState(
                personality_id=self.napoleon_id,
                chips=10_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=self.sandbox_id,
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

    def _seed_carry(self, *, carry_amount: int = 250) -> None:
        self.stake_repo.create_stake(
            Stake(
                stake_id='stk-carry-lobby',
                session_id='sess-lobby',
                staker_id=self.napoleon_id,
                staker_kind=STAKER_KIND_PERSONALITY,
                borrower_id=PLAYER_OWNER_ID,
                borrower_kind=BORROWER_KIND_HUMAN,
                format=STAKE_FORMAT_PURE,
                principal=400,
                match_amount=0,
                origination_fee=0,
                cut=0.20,
                status=STAKE_STATUS_CARRY,
                carry_amount=carry_amount,
                stake_tier='$10',
                created_at=ANCHOR,
                settled_at=ANCHOR,
            )
        )

    def _find_napoleon_seat(self, data):
        # The auto-seeded lobby places Napoleon somewhere — scan all tables.
        for t in data['tables']:
            for seat in t['seats']:
                if seat.get('kind') == 'ai' and seat.get('personality_id') == self.napoleon_id:
                    return seat
        return None

    def test_no_carry_means_no_carry_amount_field(self):
        response = self.client.get('/api/cash/lobby')
        data = response.get_json()
        seat = self._find_napoleon_seat(data)
        # If lobby seeding placed Napoleon, the field is absent (not
        # required when no carry exists).
        if seat is not None:
            self.assertNotIn('carry_amount', seat)

    def test_carry_surfaces_on_napoleons_seat(self):
        self._seed_carry(carry_amount=275)

        response = self.client.get('/api/cash/lobby')
        data = response.get_json()
        seat = self._find_napoleon_seat(data)
        self.assertIsNotNone(seat, "Napoleon should be seated in the seeded lobby")
        self.assertEqual(seat['carry_amount'], 275)

    def test_carries_aggregate_across_sessions(self):
        # Multiple carries to the same staker → annotation is the sum.
        self.stake_repo.create_stake(
            Stake(
                stake_id='stk-a',
                session_id='sess-a',
                staker_id=self.napoleon_id,
                staker_kind=STAKER_KIND_PERSONALITY,
                borrower_id=PLAYER_OWNER_ID,
                borrower_kind=BORROWER_KIND_HUMAN,
                format=STAKE_FORMAT_PURE,
                principal=400,
                match_amount=0,
                origination_fee=0,
                cut=0.20,
                status=STAKE_STATUS_CARRY,
                carry_amount=100,
                stake_tier='$10',
                created_at=ANCHOR,
                settled_at=ANCHOR,
            )
        )
        self.stake_repo.create_stake(
            Stake(
                stake_id='stk-b',
                session_id='sess-b',
                staker_id=self.napoleon_id,
                staker_kind=STAKER_KIND_PERSONALITY,
                borrower_id=PLAYER_OWNER_ID,
                borrower_kind=BORROWER_KIND_HUMAN,
                format=STAKE_FORMAT_PURE,
                principal=400,
                match_amount=0,
                origination_fee=0,
                cut=0.20,
                status=STAKE_STATUS_CARRY,
                carry_amount=200,
                stake_tier='$10',
                created_at=ANCHOR,
                settled_at=ANCHOR,
            )
        )

        response = self.client.get('/api/cash/lobby')
        data = response.get_json()
        seat = self._find_napoleon_seat(data)
        self.assertIsNotNone(seat)
        self.assertEqual(seat['carry_amount'], 300)


if __name__ == '__main__':
    unittest.main()
