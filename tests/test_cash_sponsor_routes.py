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
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from cash_mode.tables import CashTableState, ai_slot, human_slot, open_slot, reserved_slot
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

        # Pin the cash-mode resolver so route + setUp seeds agree on
        # sandbox_id. Direct-seed calls use "test-sandbox-1" via the
        # repo; pinned cache makes the route's
        # `resolve_default_sandbox_for(PLAYER_OWNER_ID)` return the
        # same id.
        from tests._sandbox_test_helper import TEST_SANDBOX_ID, pin_sandbox_for

        pin_sandbox_for(PLAYER_OWNER_ID, repos['sandbox_repo'])
        self.test_sandbox_id = TEST_SANDBOX_ID

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
                'cash_session_repo',
                'chip_ledger_repo',
                'stake_repo',
                'vice_state_repo',
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

        # Seed lender personalities. staker_profile in config_json is the
        # surface load_staker_profile reads.
        self.napoleon_id = self.personality_repo.save_personality(
            'Napoleon',
            {
                'play_style': 'aggressive',
                'bankroll_knobs': {
                    'starting_bankroll': 50_000,
                    'bankroll_rate': 500,
                    'buy_in_multiplier': 1.0,
                    'stake_comfort_zone': '$10',
                },
                'staker_profile': {
                    'willing': True,
                    'max_loan_pct_of_bankroll': 0.08,
                    'floor_anchor': 1.4,
                    'rate_anchor': 0.45,
                    'respect_floor': -0.9,
                    'heat_ceiling': 0.95,
                },
            },
            source='test_seed',
            circulating=True,
        )
        self.buddha_id = self.personality_repo.save_personality(
            'Buddha',
            {
                'play_style': 'tight',
                'bankroll_knobs': {
                    'starting_bankroll': 50_000,
                    'bankroll_rate': 500,
                    'buy_in_multiplier': 1.0,
                    'stake_comfort_zone': '$10',
                },
                'staker_profile': {
                    'willing': True,
                    'max_loan_pct_of_bankroll': 0.15,
                    'floor_anchor': 1.0,
                    'rate_anchor': 0.15,
                    'respect_floor': -0.7,
                    'heat_ceiling': 0.85,
                },
            },
            source='test_seed',
            circulating=True,
        )
        self.mime_id = self.personality_repo.save_personality(
            'A Mime',
            {
                'play_style': 'silent',
                'staker_profile': {'willing': False},
            },
            source='test_seed',
            circulating=True,
        )

        # Healthy AI bankrolls for the willing lenders.
        now = datetime.utcnow()
        self.bankroll_repo.save_ai_bankroll(
            AIBankrollState(
                personality_id=self.napoleon_id,
                chips=20_000,
                last_regen_tick=now,
            ),
            sandbox_id="test-sandbox-1",
        )
        self.bankroll_repo.save_ai_bankroll(
            AIBankrollState(
                personality_id=self.buddha_id,
                chips=20_000,
                last_regen_tick=now,
            ),
            sandbox_id="test-sandbox-1",
        )
        self.bankroll_repo.save_ai_bankroll(
            AIBankrollState(
                personality_id=self.mime_id,
                chips=20_000,
                last_regen_tick=now,
            ),
            sandbox_id="test-sandbox-1",
        )

        # Seed player bankroll below the $10 tier min (= 400) so sponsor-
        # eligible at $10 stake (no prior tier — $2 — so eligibility logic
        # accepts).
        self.bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=PLAYER_OWNER_ID,
                chips=80,  # < $2 min (80) wait — that's exactly. Use 60 to be sure.
                starting_bankroll=200,
            )
        )
        # 60 is below $2 min (80) AND below $10 min (400) → only $2 eligible.
        # For mixed-pool tests we want $10 eligibility, so set bankroll
        # between $2 min and $10 min.
        self.bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=PLAYER_OWNER_ID,
                chips=200,  # ≥ $2 min (80), < $10 min (400) → $10 sponsor-eligible
                starting_bankroll=200,
            )
        )

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
        self.bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=PLAYER_OWNER_ID,
                chips=0,
                starting_bankroll=0,
            )
        )
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
            self.bankroll_repo.save_ai_bankroll(
                AIBankrollState(
                    personality_id=pid,
                    chips=0,
                    last_regen_tick=now,
                ),
                sandbox_id="test-sandbox-1",
            )
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

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "KNOWN GAP (2026-06-01 audit): a personality stake from a NON-SEATED "
            "lender mints the principal — the player's table stack grows by the "
            "principal with no offsetting debit from the lender's bankroll "
            "(_build_cash_game only debits SEATED AIs, and the lender isn't seated). "
            "Gate for the Sal mentor-stake: the fix must explicitly debit the "
            "lender's bankroll by the principal. When fixed, this xfail flips to "
            "xpass (strict) — remove the marker."
        ),
    )
    def test_personality_stake_principal_comes_from_lender_not_minted(self):
        """CONSERVATION AUDIT (the gate for wiring Sal as the home-court backer).

        A personality stake's principal must come OUT of the lender's bankroll —
        never minted — even when the lender is NOT seated at the table (the mentor-
        stake / Sal case: auto-sit, no table_id). Runs _build_cash_game UNMOCKED so
        the real funding path executes, and checks the chip-ledger audit drift
        (mint detector) + the lender's bankroll delta.
        """
        import flask_app.extensions as ext
        from flask_app.services import game_state_service
        from flask_app.services.chip_ledger_audit import compute_audit

        def _drift():
            return compute_audit(
                ledger_repo=ext.chip_ledger_repo,
                bankroll_repo=self.bankroll_repo,
                cash_table_repo=ext.cash_table_repo,
                stake_repo=self.stake_repo,
                db_path=ext.persistence_db_path,
                list_game_ids_fn=game_state_service.list_game_ids,
                get_game_fn=game_state_service.get_game,
            )['drift']

        def _nap():
            return self.bankroll_repo.load_ai_bankroll(
                self.napoleon_id, sandbox_id=self.test_sandbox_id
            ).chips

        nap_before = _nap()
        drift_before = _drift()

        # NOTE: deliberately NOT patching _build_cash_game — we want the real
        # funding/debit path to run.
        response = self.client.post(
            '/api/cash/sponsor-and-sit',
            json={'stake_label': '$10', 'lender_id': self.napoleon_id, 'opponents': 2},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        stake = self.stake_repo.load_active_for_session(data['game_id'])
        principal = stake.principal

        drift_after = _drift()
        nap_after = _nap()

        # No minting: the total chip universe didn't grow by the principal.
        self.assertEqual(
            drift_after, drift_before,
            f"chip drift changed {drift_before} -> {drift_after}: principal {principal} minted?",
        )
        # The principal came OUT of the lender's bankroll (it went down).
        self.assertLess(
            nap_after, nap_before,
            f"lender bankroll didn't fund the principal (before={nap_before}, after={nap_after})",
        )

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
            observer_id=self.napoleon_id,
            opponent_id=PLAYER_OWNER_ID,
        )
        self.assertIsNotNone(state_lender_pov)
        self.assertGreater(state_lender_pov.respect, 0.5)
        self.assertGreater(state_lender_pov.likability, 0.5)

        # Player's view of the lender: mirror shifts also positive.
        state_player_pov = self.relationship_repo.load_relationship_state(
            observer_id=PLAYER_OWNER_ID,
            opponent_id=self.napoleon_id,
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
            observer_id=self.napoleon_id,
            opponent_id=PLAYER_OWNER_ID,
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

    def test_table_aware_path_uses_persisted_roster(self):
        """Regression: when the sponsor flow originates from a lobby
        seat tap (table_id + seat_index sent), the resulting game must
        be populated with the AIs the lobby card showed at that table
        — not a freshly-sampled lineup.

        Pre-fix, `sponsor_and_sit` ignored any table context and
        called `_build_cash_game` with no `preselected_ai`, so the
        legacy fresh-sample path picked random eligible personalities.
        Users tapped a $200 table showing AIs X/Y/Z and got seated
        with A/B/C/D/E.
        """
        # _CashSponsorRouteBase doesn't stash repos as self.repos;
        # pull cash_table_repo via the extension binding instead.
        from flask_app import extensions

        cash_table_repo = extensions.cash_table_repo

        # Build a $10 table with napoleon + buddha seated, open seats
        # elsewhere. The roster is unambiguous — only two AIs and they
        # are seeded in setUp with stake_comfort_zone='$10', so they
        # are also legacy-sample candidates. We assert by personality_id
        # which the legacy path would not deterministically hit.
        seats = [
            ai_slot(self.napoleon_id, 400),
            open_slot(),
            ai_slot(self.buddha_id, 400),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        cash_table_repo.save_table(
            CashTableState(
                table_id='cash-table-10-001',
                stake_label='$10',
                seats=seats,
            ),
            sandbox_id=self.test_sandbox_id,
        )

        # Stub `_build_cash_game` to capture its preselected args
        # rather than running the full game build (heavy + irrelevant
        # to this regression).
        captured: dict = {}

        def _spy(**kwargs):
            captured.update(kwargs)
            return 'cash-test-spy-id', None

        with patch(
            'flask_app.routes.cash_routes._build_cash_game',
            side_effect=_spy,
        ):
            response = self.client.post(
                '/api/cash/sponsor-and-sit',
                json={
                    'stake_label': '$10',
                    'archetype_id': 'friendly_boost',
                    'table_id': 'cash-table-10-001',
                    'seat_index': 1,
                    'opponents': 2,
                },
            )

        self.assertEqual(
            response.status_code,
            200,
            response.get_data(as_text=True),
        )

        preselected_ai = captured.get('preselected_ai')
        self.assertIsNotNone(
            preselected_ai,
            'sponsor_and_sit did not pass preselected_ai — game would '
            'fall through to the legacy fresh-sample path and the '
            'in-game lineup would differ from the lobby card',
        )
        roster_pids = sorted(entry['personality_id'] for entry in preselected_ai)
        self.assertEqual(
            roster_pids,
            sorted([self.napoleon_id, self.buddha_id]),
            'game roster did not come from the persisted cash_tables '
            'row — sponsor_and_sit ignored the table_id',
        )

        preselected_chips = captured.get('preselected_ai_chips') or {}
        self.assertEqual(preselected_chips.get(self.napoleon_id), 400)
        self.assertEqual(preselected_chips.get(self.buddha_id), 400)

        # Human seat must now be claimed in cash_tables, otherwise
        # the lobby will keep showing seat 1 as open and another
        # player could double-book it.
        after = cash_table_repo.load_table(
            'cash-table-10-001',
            sandbox_id=self.test_sandbox_id,
        )
        self.assertEqual(after.seats[1]['kind'], 'human')
        self.assertEqual(after.seats[1]['personality_id'], PLAYER_OWNER_ID)

    def test_accepts_own_reserved_seat(self):
        """The /sit 402 path holds the tapped seat as `"reserved"` while
        the SponsorModal is open. On accept, sponsor-and-sit must claim
        that hold (not 409 "Seat is not open") and convert it to human.
        """
        from flask_app import extensions

        cash_table_repo = extensions.cash_table_repo

        # Seat the player's own reservation at seat 1 (what the 402 path
        # leaves behind), with two AIs elsewhere for a deterministic roster.
        seats = [
            ai_slot(self.napoleon_id, 400),
            reserved_slot(PLAYER_OWNER_ID, datetime.utcnow()),
            ai_slot(self.buddha_id, 400),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        cash_table_repo.save_table(
            CashTableState(
                table_id='cash-table-10-001',
                stake_label='$10',
                seats=seats,
            ),
            sandbox_id=self.test_sandbox_id,
        )

        def _spy(**kwargs):
            return 'cash-test-spy-id', None

        with patch(
            'flask_app.routes.cash_routes._build_cash_game',
            side_effect=_spy,
        ):
            response = self.client.post(
                '/api/cash/sponsor-and-sit',
                json={
                    'stake_label': '$10',
                    'archetype_id': 'friendly_boost',
                    'table_id': 'cash-table-10-001',
                    'seat_index': 1,
                    'opponents': 2,
                },
            )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        after = cash_table_repo.load_table(
            'cash-table-10-001',
            sandbox_id=self.test_sandbox_id,
        )
        self.assertEqual(after.seats[1]['kind'], 'human')
        self.assertEqual(after.seats[1]['personality_id'], PLAYER_OWNER_ID)

    def test_does_not_steal_seat_reserved_by_another_player(self):
        """A hold owned by a DIFFERENT player must never be stolen. The
        sponsor accept now falls back to another open seat instead of
        409-ing, but the other player's reserved seat stays theirs."""
        from flask_app import extensions

        cash_table_repo = extensions.cash_table_repo

        seats = [
            ai_slot(self.napoleon_id, 400),
            reserved_slot("a-different-player", datetime.utcnow()),
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        cash_table_repo.save_table(
            CashTableState(
                table_id='cash-table-10-001',
                stake_label='$10',
                seats=seats,
            ),
            sandbox_id=self.test_sandbox_id,
        )
        with patch(
            'flask_app.routes.cash_routes._build_cash_game',
            side_effect=lambda **kwargs: ('cash-test-reserved-fallback', None),
        ):
            response = self.client.post(
                '/api/cash/sponsor-and-sit',
                json={
                    'stake_label': '$10',
                    'archetype_id': 'friendly_boost',
                    'table_id': 'cash-table-10-001',
                    'seat_index': 1,
                    'opponents': 2,
                },
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        after = cash_table_repo.load_table('cash-table-10-001', sandbox_id=self.test_sandbox_id)
        # The other player's hold is untouched...
        self.assertEqual(after.seats[1]['kind'], 'reserved')
        self.assertEqual(after.seats[1]['personality_id'], 'a-different-player')
        # ...and we sat in a different (previously open) seat.
        human_seats = [
            i for i, s in enumerate(after.seats)
            if s['kind'] == 'human' and s.get('personality_id') == PLAYER_OWNER_ID
        ]
        self.assertEqual(len(human_seats), 1)
        self.assertNotEqual(human_seats[0], 1)

    def test_table_aware_path_persists_cash_table_id(self):
        """Regression: the seat-tapped sponsor flow must persist
        cash_table_id + cash_seat_index onto the cash_sessions row.

        Pre-fix, `sponsor_and_sit` called `_record_cash_session_start`
        without these two fields even though both were in scope, so
        every sponsor session wrote cash_sessions.cash_table_id=NULL.
        That NULL then defeated the leave-time ghost-seat sweep (see
        test_leave_clears_orphan_seats) and broke per-table analytics.
        """
        from flask_app import extensions

        cash_table_repo = extensions.cash_table_repo
        cash_session_repo = extensions.cash_session_repo

        seats = [
            ai_slot(self.napoleon_id, 400),
            open_slot(),
            ai_slot(self.buddha_id, 400),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        cash_table_repo.save_table(
            CashTableState(
                table_id='cash-table-10-001',
                stake_label='$10',
                seats=seats,
            ),
            sandbox_id=self.test_sandbox_id,
        )

        # Spy out the heavy game build; the route still calls
        # _record_cash_session_start afterward with our spy game_id.
        def _spy(**kwargs):
            return 'cash-test-cti-id', None

        with patch(
            'flask_app.routes.cash_routes._build_cash_game',
            side_effect=_spy,
        ):
            response = self.client.post(
                '/api/cash/sponsor-and-sit',
                json={
                    'stake_label': '$10',
                    'archetype_id': 'friendly_boost',
                    'table_id': 'cash-table-10-001',
                    'seat_index': 1,
                    'opponents': 2,
                },
            )
        self.assertEqual(
            response.status_code,
            200,
            response.get_data(as_text=True),
        )

        session = cash_session_repo.load('cash-test-cti-id')
        self.assertIsNotNone(
            session,
            'no cash_sessions row was written for the sponsor session',
        )
        self.assertEqual(
            session.cash_table_id,
            'cash-table-10-001',
            'sponsor session did not persist cash_table_id — the '
            'leave-time ghost-seat sweep would be unable to locate '
            'the seat',
        )
        self.assertEqual(session.cash_seat_index, 1)

    def test_table_aware_taken_seat_falls_back_to_open(self):
        """Sponsor flow with table_id pointing at a seat that filled in
        (live-fill race) falls back to another open seat on the SAME
        table — same roster, not a fresh sample — rather than 409-ing.
        """
        from flask_app import extensions

        cash_table_repo = extensions.cash_table_repo
        seats = [
            ai_slot(self.napoleon_id, 400),
            ai_slot(self.buddha_id, 400),
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        cash_table_repo.save_table(
            CashTableState(
                table_id='cash-table-10-001',
                stake_label='$10',
                seats=seats,
            ),
            sandbox_id=self.test_sandbox_id,
        )
        with patch(
            'flask_app.routes.cash_routes._build_cash_game',
            side_effect=lambda **kwargs: ('cash-test-taken-fallback', None),
        ):
            response = self.client.post(
                '/api/cash/sponsor-and-sit',
                json={
                    'stake_label': '$10',
                    'archetype_id': 'friendly_boost',
                    'table_id': 'cash-table-10-001',
                    'seat_index': 0,  # napoleon's seat (taken)
                    'opponents': 2,
                },
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        after = cash_table_repo.load_table('cash-table-10-001', sandbox_id=self.test_sandbox_id)
        # Napoleon's seat is untouched; we landed in a previously-open seat.
        self.assertEqual(after.seats[0]['kind'], 'ai')
        human_seats = [
            i for i, s in enumerate(after.seats)
            if s['kind'] == 'human' and s.get('personality_id') == PLAYER_OWNER_ID
        ]
        self.assertEqual(len(human_seats), 1)
        self.assertGreaterEqual(human_seats[0], 2)

    def test_full_table_still_409s(self):
        """A genuinely full table (no open seat to fall back to) still
        409s with a 'Table is full' message."""
        from flask_app import extensions

        cash_table_repo = extensions.cash_table_repo
        seats = [ai_slot(self.napoleon_id, 400) for _ in range(6)]
        cash_table_repo.save_table(
            CashTableState(
                table_id='cash-table-10-001',
                stake_label='$10',
                seats=seats,
            ),
            sandbox_id=self.test_sandbox_id,
        )
        response = self.client.post(
            '/api/cash/sponsor-and-sit',
            json={
                'stake_label': '$10',
                'archetype_id': 'friendly_boost',
                'table_id': 'cash-table-10-001',
                'seat_index': 0,
                'opponents': 2,
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error'], 'Table is full')

    def test_table_aware_requires_both_table_id_and_seat_index(self):
        """Sending one without the other is ambiguous — reject with 400."""
        response = self.client.post(
            '/api/cash/sponsor-and-sit',
            json={
                'stake_label': '$10',
                'archetype_id': 'friendly_boost',
                'table_id': 'cash-table-10-001',
                # seat_index intentionally omitted
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('seat_index', response.get_json()['error'])


class TestSponsorRoutesViceFilter(_CashSponsorRouteBase):
    """Vice spending: route refuses to stake an AI who's off-grid, and
    the offer list excludes vicing AIs entirely.
    """

    def _put_napoleon_on_vice(self):
        """Insert a vice state row for Napoleon."""
        from datetime import timedelta

        from poker.repositories.vice_state_repository import ViceState

        now = datetime.utcnow()
        self.vice_repo = self.app.extensions  # not used; reach via test_db
        from poker.repositories.vice_state_repository import ViceStateRepository

        vice_repo = ViceStateRepository(self.test_db.name)
        vice_repo.insert_vice_state(
            ViceState(
                personality_id=self.napoleon_id,
                sandbox_id=self.test_sandbox_id,
                started_at=now,
                ends_at=now + timedelta(hours=2),
                amount=2500,
                duration_bucket='long',
                narration='Napoleon commissioned a bronze bust',
            )
        )

    def test_sponsor_offers_excludes_vicing_ai(self):
        """A vicing AI should not appear in the personality offers list."""
        self._put_napoleon_on_vice()
        response = self.client.get('/api/cash/sponsor-offers?stake_label=$10')
        self.assertEqual(response.status_code, 200)
        offers = response.get_json()['offers']
        lender_ids = [o.get('lender_id') for o in offers if o['kind'] == 'personality']
        self.assertNotIn(self.napoleon_id, lender_ids)

    def test_sponsor_and_sit_refuses_vicing_lender(self):
        """Stake-create against a vicing AI returns 409 with a clear payload."""
        self._put_napoleon_on_vice()
        response = self.client.post(
            '/api/cash/sponsor-and-sit',
            json={
                'stake_label': '$10',
                'lender_id': self.napoleon_id,
                'opponents': 2,
            },
        )
        self.assertEqual(response.status_code, 409)
        data = response.get_json()
        self.assertIn('away', data['error'])
        self.assertEqual(data['lender_id'], self.napoleon_id)
        self.assertIn('vice_ends_at', data)
        self.assertIn('vice_narration', data)
