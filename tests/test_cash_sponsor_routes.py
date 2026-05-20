"""Tests for Path B's sponsor route changes.

`/api/cash/sponsor-offers` now returns mixed offers (personality
first, anonymous house fallback). `/api/cash/sponsor-and-sit`
accepts either `archetype_id` (house) or `lender_id` (personality)
and routes accordingly.

Pattern mirrors `test_personality_routes_bankroll_knobs.py`: tempdb +
patched `init_persistence` + `create_app` + auth bypass. The cash
routes' late imports of `bankroll_repo` / `personality_repo` /
`relationship_repo` all see the patched extensions through the
app-create path.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from flask_app import create_app
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]


PLAYER_OWNER_ID = "test-player-1"


def _mock_authorization_service(user, has_admin_permission=True):
    """Build a fake global authorization service for require_permission()."""
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class _CashSponsorRouteBase(unittest.TestCase):
    """Shared setup: tempdb, app, auth patches, seeded personalities + bankrolls.

    Seeds three personalities representing different lender vibes:
      - Napoleon: predatory profile, healthy bankroll, no relationship row
        → qualifies with anchor terms.
      - Buddha: generous profile, healthy bankroll → qualifies, softer terms.
      - Mime: unwilling, healthy bankroll → excluded by gate 1.
    """

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

        repos = create_repos(self.test_db.name)
        self.bankroll_repo = repos['bankroll_repo']
        self.personality_repo = repos['personality_repo']
        self.relationship_repo = repos['relationship_repo']
        self.stake_repo = repos['stake_repo']

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
            self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()

        user = {'id': PLAYER_OWNER_ID, 'name': 'Tester'}
        self._authz_patcher = patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(user=user),
        )
        self._authz_patcher.start()

        # cash_routes resolves user via `auth_manager` (lazy import inside the
        # helper). Patch the bound `auth_manager` on the extensions module so
        # both `_resolve_owner_id` and `_resolve_player_name` see our user.
        auth_mock = MagicMock()
        auth_mock.get_current_user.return_value = user
        self._auth_patcher = patch(
            'flask_app.extensions.auth_manager',
            auth_mock,
        )
        self._auth_patcher.start()

        # Seed lender personalities. lender_profile in config_json is the
        # surface load_lender_profile reads.
        self.napoleon_id = self.personality_repo.save_personality(
            'Napoleon',
            {
                'play_style': 'aggressive',
                'bankroll_knobs': {
                    'bankroll_cap': 50_000, 'bankroll_rate': 500,
                    'buy_in_multiplier': 1.0,
                    'stop_loss_buy_ins': 3, 'stop_win_buy_ins': 5,
                    'stake_comfort_zone': '$10',
                },
                'lender_profile': {
                    'willing': True,
                    'max_loan_pct_of_bankroll': 0.08,
                    'floor_anchor': 1.4,
                    'rate_anchor': 0.45,
                    'respect_floor': -0.9,
                    'heat_ceiling': 0.95,
                },
            },
            source='test_seed',
        )
        self.buddha_id = self.personality_repo.save_personality(
            'Buddha',
            {
                'play_style': 'tight',
                'bankroll_knobs': {
                    'bankroll_cap': 50_000, 'bankroll_rate': 500,
                    'buy_in_multiplier': 1.0,
                    'stop_loss_buy_ins': 3, 'stop_win_buy_ins': 5,
                    'stake_comfort_zone': '$10',
                },
                'lender_profile': {
                    'willing': True,
                    'max_loan_pct_of_bankroll': 0.15,
                    'floor_anchor': 1.0,
                    'rate_anchor': 0.15,
                    'respect_floor': -0.7,
                    'heat_ceiling': 0.85,
                },
            },
            source='test_seed',
        )
        self.mime_id = self.personality_repo.save_personality(
            'A Mime',
            {
                'play_style': 'silent',
                'lender_profile': {'willing': False},
            },
            source='test_seed',
        )

        # Healthy AI bankrolls for the willing lenders.
        now = datetime.utcnow()
        self.bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id=self.napoleon_id, chips=20_000, last_regen_tick=now,
        ))
        self.bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id=self.buddha_id, chips=20_000, last_regen_tick=now,
        ))
        self.bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id=self.mime_id, chips=20_000, last_regen_tick=now,
        ))

        # Seed player bankroll below the $10 tier min (= 400) so sponsor-
        # eligible at $10 stake (no prior tier — $2 — so eligibility logic
        # accepts).
        self.bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=PLAYER_OWNER_ID,
            chips=80,  # < $2 min (80) wait — that's exactly. Use 60 to be sure.
            starting_bankroll=200,
        ))
        # 60 is below $2 min (80) AND below $10 min (400) → only $2 eligible.
        # For mixed-pool tests we want $10 eligibility, so set bankroll
        # between $2 min and $10 min.
        self.bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=PLAYER_OWNER_ID,
            chips=200,  # ≥ $2 min (80), < $10 min (400) → $10 sponsor-eligible
            starting_bankroll=200,
        ))

    def tearDown(self):
        self._auth_patcher.stop()
        self._authz_patcher.stop()
        os.unlink(self.test_db.name)


class TestSponsorOffersRoute(_CashSponsorRouteBase):
    """GET /api/cash/sponsor-offers returns mixed offers."""

    def test_returns_personality_offers_first(self):
        response = self.client.get('/api/cash/sponsor-offers?stake_label=$10')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        self.assertTrue(data['eligible'])
        self.assertEqual(data['stake_label'], '$10')
        offers = data['offers']
        self.assertEqual(len(offers), 3)
        # First-N (≥ 1) of the offers should be personality kind; the rest
        # fill with house. Both willing AIs qualify → both personalities
        # show, then 1 house.
        kinds = [o['kind'] for o in offers]
        self.assertEqual(kinds.count('personality'), 2)
        self.assertEqual(kinds.count('house'), 1)

    def test_personality_offers_carry_lender_id(self):
        response = self.client.get('/api/cash/sponsor-offers?stake_label=$10')
        data = response.get_json()
        personality_offers = [o for o in data['offers'] if o['kind'] == 'personality']
        for po in personality_offers:
            self.assertIn(po['lender_id'], (self.napoleon_id, self.buddha_id))
            self.assertIn('name', po)
            self.assertIn('amount', po)
            self.assertIn('floor', po)
            self.assertIn('rate', po)
            self.assertIn('relationship_hint', po)
            self.assertIn('flavor', po)

    def test_house_offers_carry_archetype_id(self):
        response = self.client.get('/api/cash/sponsor-offers?stake_label=$10')
        data = response.get_json()
        house_offers = [o for o in data['offers'] if o['kind'] == 'house']
        for ho in house_offers:
            self.assertIn('archetype_id', ho)
            self.assertIn('name', ho)
            self.assertIn('amount', ho)

    def test_unwilling_personality_excluded(self):
        response = self.client.get('/api/cash/sponsor-offers?stake_label=$10')
        data = response.get_json()
        personality_offers = [o for o in data['offers'] if o['kind'] == 'personality']
        lender_ids = [po['lender_id'] for po in personality_offers]
        self.assertNotIn(self.mime_id, lender_ids)

    def test_ineligible_stake_returns_locked(self):
        # Drop player bankroll below $2 min → no $2 prev tier → wait,
        # at $10 stake we need bankroll between $2 min (80) and $10 min
        # (400). Setting to 0 → bankroll < $10 min but < $2 min too → not
        # sponsor-eligible at $10 (would require bankroll ≥ $2 min).
        self.bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=PLAYER_OWNER_ID, chips=0, starting_bankroll=0,
        ))
        response = self.client.get('/api/cash/sponsor-offers?stake_label=$10')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data['eligible'])
        self.assertEqual(data['reason'], 'tier_locked')

    def test_house_only_when_no_personality_qualifies(self):
        # Crank all lender heat ceilings to 0.0 AND set relationship heat
        # > 0.0 — easier path: kill personalities by making them poor.
        # Set bankroll to 0 for all AIs → load_ai_bankroll_current returns
        # the snapshot 0 → capacity = 0% which is below min_buy_in.
        now = datetime.utcnow()
        for pid in (self.napoleon_id, self.buddha_id, self.mime_id):
            self.bankroll_repo.save_ai_bankroll(AIBankrollState(
                personality_id=pid, chips=0, last_regen_tick=now,
            ))
        response = self.client.get('/api/cash/sponsor-offers?stake_label=$10')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        offers = data['offers']
        kinds = [o['kind'] for o in offers]
        self.assertTrue(all(k == 'house' for k in kinds))
        self.assertEqual(len(offers), 3)

    def test_invalid_stake_label_returns_400(self):
        response = self.client.get('/api/cash/sponsor-offers?stake_label=$nope')
        self.assertEqual(response.status_code, 400)


class TestSponsorAndSitRoute(_CashSponsorRouteBase):
    """POST /api/cash/sponsor-and-sit accepts archetype_id OR lender_id."""

    def test_requires_one_of_archetype_or_lender(self):
        response = self.client.post(
            '/api/cash/sponsor-and-sit',
            json={'stake_label': '$10'},
        )
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIn('archetype_id or lender_id', data['error'])

    def test_rejects_both_archetype_and_lender(self):
        response = self.client.post(
            '/api/cash/sponsor-and-sit',
            json={
                'stake_label': '$10',
                'archetype_id': 'friendly_boost',
                'lender_id': self.napoleon_id,
            },
        )
        self.assertEqual(response.status_code, 400)

    def _patch_build_cash_game(self):
        """Stub `_build_cash_game` so tests don't bootstrap the full state machine.

        The route's load-bearing logic for Path B is:
          - resolve the offer (house archetype vs personality)
          - call `_build_cash_game` to make the table
          - create a `stakes` row with the lender / principal / cut

        The full game build pulls in the state machine, controllers,
        memory manager, AI selection, etc. — none of which we're
        testing. Stubbing it lets the route's stake-write logic run in
        isolation. The cash_personality_ids mapping the leave-time
        cash-out loop reads is verified separately in commit 5's tests.
        """
        return patch(
            'flask_app.routes.cash_routes._build_cash_game',
            return_value=("cash-test-stub-id", None),
        )

    def test_house_path_writes_house_stake_row(self):
        # House archetype path — creates a stake row with staker_id=NULL.
        with self._patch_build_cash_game():
            response = self.client.post(
                '/api/cash/sponsor-and-sit',
                json={
                    'stake_label': '$10',
                    'archetype_id': 'friendly_boost',
                    'opponents': 2,
                },
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        self.assertIn('game_id', data)
        self.assertEqual(data['offer']['kind'], 'house')
        self.assertEqual(data['offer']['archetype_id'], 'friendly_boost')

        stake = self.stake_repo.load_active_for_session(data['game_id'])
        self.assertIsNotNone(stake)
        self.assertIsNone(stake.staker_id)
        self.assertEqual(stake.staker_kind, 'house')
        self.assertGreater(stake.principal, 0)

    def test_personality_path_writes_personality_stake_row(self):
        with self._patch_build_cash_game():
            response = self.client.post(
                '/api/cash/sponsor-and-sit',
                json={
                    'stake_label': '$10',
                    'lender_id': self.napoleon_id,
                    'opponents': 2,
                },
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        self.assertEqual(data['offer']['kind'], 'personality')
        self.assertEqual(data['offer']['lender_id'], self.napoleon_id)

        stake = self.stake_repo.load_active_for_session(data['game_id'])
        self.assertIsNotNone(stake)
        self.assertEqual(stake.staker_id, self.napoleon_id)
        self.assertEqual(stake.staker_kind, 'personality')
        self.assertGreater(stake.principal, 0)

    def test_personality_path_emits_sponsorship_offered_event(self):
        # The route fires STAKE_OFFERED via the relationship_repo
        # when an AI staker extends a loan. The repo's projection-on-read
        # surface (load_relationship_state) reveals the bilateral update.
        with self._patch_build_cash_game():
            response = self.client.post(
                '/api/cash/sponsor-and-sit',
                json={
                    'stake_label': '$10',
                    'lender_id': self.napoleon_id,
                    'opponents': 2,
                },
            )
        self.assertEqual(response.status_code, 200)

        # Lender's view of the player: small positive shifts on
        # respect + likability per the actor table.
        state_lender_pov = self.relationship_repo.load_relationship_state(
            observer_id=self.napoleon_id, opponent_id=PLAYER_OWNER_ID,
        )
        self.assertIsNotNone(state_lender_pov)
        self.assertGreater(state_lender_pov.respect, 0.5)
        self.assertGreater(state_lender_pov.likability, 0.5)

        # Player's view of the lender: mirror shifts also positive.
        state_player_pov = self.relationship_repo.load_relationship_state(
            observer_id=PLAYER_OWNER_ID, opponent_id=self.napoleon_id,
        )
        self.assertIsNotNone(state_player_pov)
        self.assertGreater(state_player_pov.respect, 0.5)
        self.assertGreater(state_player_pov.likability, 0.5)

    def test_house_path_does_not_emit_event(self):
        # House-archetype stakes have no actor to fire STAKE_OFFERED.
        # No relationship row should land.
        with self._patch_build_cash_game():
            response = self.client.post(
                '/api/cash/sponsor-and-sit',
                json={
                    'stake_label': '$10',
                    'archetype_id': 'friendly_boost',
                    'opponents': 2,
                },
            )
        self.assertEqual(response.status_code, 200)

        # No relationship_state row keyed on player vs napoleon (the
        # only AI in the seeded pool we'd otherwise see).
        state = self.relationship_repo.load_relationship_state(
            observer_id=self.napoleon_id, opponent_id=PLAYER_OWNER_ID,
        )
        self.assertIsNone(state)

    def test_personality_path_rejects_unwilling_lender(self):
        # The Mime won't lend → eligibility filters them out → route 400.
        response = self.client.post(
            '/api/cash/sponsor-and-sit',
            json={
                'stake_label': '$10',
                'lender_id': self.mime_id,
                'opponents': 2,
            },
        )
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIn("doesn't qualify", data['error'])

    def test_personality_path_rejects_unknown_lender(self):
        response = self.client.post(
            '/api/cash/sponsor-and-sit',
            json={
                'stake_label': '$10',
                'lender_id': 'not_a_real_personality',
                'opponents': 2,
            },
        )
        self.assertEqual(response.status_code, 400)
