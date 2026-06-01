"""Tests for `POST /api/cash/sit` — the lobby-v1.5 sit-down route.

Replaces `/api/cash/start` for lobby flows. Validates table existence,
seat openness, affordability + sponsor-eligibility branching, and
double-sit rejection. The roster used to build the game comes from
the persisted `cash_tables` row, not a fresh sample.
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
from cash_mode.tables import CashTableState, ai_slot, open_slot
from flask_app import create_app
from poker.personality_generator import PersonalityGenerator
from poker.poker_player import AIPokerPlayer
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]


PLAYER_OWNER_ID = "test-player-1"


def _mock_authorization_service(user, has_admin_permission=True):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


def _seat_napoleon():
    """Build a $10 table with napoleon + 5 open seats."""
    seats = [
        ai_slot("napoleon", 400),
        open_slot(),
        open_slot(),
        open_slot(),
        open_slot(),
        open_slot(),
    ]
    return CashTableState(
        table_id="cash-table-10-001",
        stake_label="$10",
        seats=seats,
    )


class _CashSitRouteBase(unittest.TestCase):
    """Shared tempdb across all tests in the file.

    Module-level repo binding in `flask_app.routes.game_routes` (the
    `prompt_preset_repo` import at module load) captures the FIRST
    create_app's tempdb path. Subsequent setUps creating fresh tempdbs
    leave the old one dangling, and any code that touches
    `prompt_preset_repo` crashes with "no such table".

    Workaround: use `setUpClass`/`tearDownClass` so all tests in this
    module share the same tempdb + app instance. Tests must clean up
    after themselves (reset bankroll, reset seats) to avoid cross-test
    pollution.
    """

    @classmethod
    def setUpClass(cls):
        cls.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        cls.test_db.close()

        repos = create_repos(cls.test_db.name)
        cls.repos = repos
        cls.bankroll_repo = repos['bankroll_repo']
        cls.personality_repo = repos['personality_repo']
        cls.cash_table_repo = repos['cash_table_repo']

        from tests._sandbox_test_helper import pin_sandbox_for

        pin_sandbox_for(PLAYER_OWNER_ID, repos['sandbox_repo'])

        cls.napoleon_id = cls.personality_repo.save_personality(
            'Napoleon',
            {
                'play_style': 'aggressive',
                'bankroll_knobs': {
                    'starting_bankroll': 5_000_000,
                    'bankroll_rate': 0,
                    'buy_in_multiplier': 1.0,
                    'stake_comfort_zone': '$10',
                },
            },
            circulating=True,
        )
        cls.bankroll_repo.save_ai_bankroll(
            AIBankrollState(
                personality_id=cls.napoleon_id,
                chips=4_000_000,
                last_regen_tick=datetime(2026, 5, 18, 12, 0, 0),
            ),
            sandbox_id="test-sandbox-1",
        )
        for i in range(30):
            pid = cls.personality_repo.save_personality(
                f'AI {i}',
                {
                    'bankroll_knobs': {
                        'starting_bankroll': 5_000_000,
                        'bankroll_rate': 0,
                        'buy_in_multiplier': 1.0,
                        'stake_comfort_zone': '$10',
                    },
                },
                circulating=True,
            )
            cls.bankroll_repo.save_ai_bankroll(
                AIBankrollState(
                    personality_id=pid,
                    chips=4_000_000,
                    last_regen_tick=datetime(2026, 5, 18, 12, 0, 0),
                ),
                sandbox_id="test-sandbox-1",
            )

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

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            cls.app = create_app()
        cls.app.testing = True
        cls.client = cls.app.test_client()

        # game_routes reads these repos live via `extensions.X`. For the
        # test harness — where multiple files create fresh tempdbs — we
        # explicitly pin the canonical extensions bindings to OUR repos so
        # the route doesn't query a closed connection.
        import flask_app.extensions as _ext

        for key in (
            'prompt_preset_repo',
            'game_repo',
            'user_repo',
            'guest_tracking_repo',
            'llm_repo',
            'tournament_repo',
            'hand_history_repo',
            'decision_analysis_repo',
            'capture_label_repo',
            'coach_repo',
            'relationship_repo',
            'persistence_db_path',
            'personality_repo',
        ):
            if key in repos:
                setattr(_ext, key, repos[key])
            elif key == 'persistence_db_path':
                setattr(_ext, key, repos['db_path'])

        from cash_mode.lobby import ensure_lobby_seeded

        ensure_lobby_seeded(
            cash_table_repo=cls.cash_table_repo,
            personality_repo=cls.personality_repo,
            bankroll_repo=cls.bankroll_repo,
            sandbox_id="test-sandbox-1",
        )

    @classmethod
    def tearDownClass(cls):
        # Restore the AIPokerPlayer class-level singleton so subsequent
        # tests don't see our tempdb-pointed instance. See setUp for
        # the rebind motivation.
        AIPokerPlayer._personality_generator = cls._prior_personality_generator
        try:
            os.unlink(cls.test_db.name)
        except FileNotFoundError:
            pass

    def setUp(self):
        # AIPokerPlayer._personality_generator is a class-level singleton
        # that auto-initializes to /app/data/poker_games.db. Without
        # explicit override, every controller built via _build_cash_game
        # → AIPokerPlayer(name) → _load_personality_config() →
        # get_personality(name) hits the **production** DB, and any
        # name not already present there gets auto-generated via LLM
        # and saved as a zombie row. Point the singleton at our tempdb
        # for the duration of this test class so "AI N" lookups resolve
        # against the test's own seeded personalities. Stored on the
        # class so tearDownClass can restore it.
        if not hasattr(type(self), '_prior_personality_generator'):
            type(self)._prior_personality_generator = AIPokerPlayer._personality_generator
        AIPokerPlayer._personality_generator = PersonalityGenerator(
            personality_repo=self.personality_repo,
        )

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

        # Reset game_state_service for a clean slate per test.
        from flask_app.services import game_state_service

        for gid in list(game_state_service.games.keys()):
            game_state_service.delete_game(gid)

    def tearDown(self):
        self._auth_patcher.stop()
        self._authz_patcher.stop()
        # Clear any cash sessions left over from this test.
        from flask_app.services import game_state_service

        for gid in list(game_state_service.games.keys()):
            game_state_service.delete_game(gid)
        # Re-seed the lobby to undo any seat mutations this test made.
        from cash_mode.lobby import ensure_lobby_seeded

        # Wipe and reseed: drop every table, then run the boot seeder.
        # Also drop persisted cash-* rows — `_build_cash_game` now writes
        # `llm_configs_json` at sit-down, and a leftover row would surface
        # via `_find_active_cash_game_id`'s DB fallback and trip the
        # "session already active" 409 on the next test's sit.
        with self.cash_table_repo._get_connection() as conn:
            conn.execute("DELETE FROM cash_tables")
            conn.execute("DELETE FROM cash_idle_pool")
            conn.execute("DELETE FROM games WHERE game_id LIKE 'cash-%'")
        ensure_lobby_seeded(
            cash_table_repo=self.cash_table_repo,
            personality_repo=self.personality_repo,
            bankroll_repo=self.bankroll_repo,
            sandbox_id="test-sandbox-1",
        )
        # Reset player bankroll.
        self.bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=PLAYER_OWNER_ID,
                chips=200,
                starting_bankroll=200,
            )
        )


class TestSitAll(_CashSitRouteBase):
    def test_missing_table_id_400(self):
        resp = self.client.post("/api/cash/sit", json={"seat_index": 1})
        assert resp.status_code == 400

    def test_unknown_table_id_404(self):
        resp = self.client.post(
            "/api/cash/sit",
            json={
                "table_id": "does-not-exist",
                "seat_index": 1,
            },
        )
        assert resp.status_code == 404

    def test_seat_out_of_range_400(self):
        # Use the lobby-seeded $2 table.
        resp = self.client.post(
            "/api/cash/sit",
            json={
                "table_id": "cash-table-2-001",
                "seat_index": 99,
            },
        )
        assert resp.status_code == 400

    def test_occupied_seat_falls_back_to_open(self):
        # Tapping a seat that filled in since the lobby snapshot no longer
        # 409s — the route falls back to another open seat on the table so
        # the stale-snapshot race doesn't read as a dead Sit button.
        table = self.cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        new_seats = list(table.seats)
        new_seats[0] = ai_slot(self.napoleon_id, 80)
        self.cash_table_repo.save_table(
            CashTableState(
                table_id=table.table_id,
                stake_label=table.stake_label,
                seats=new_seats,
            ),
            sandbox_id="test-sandbox-1",
        )
        resp = self.client.post(
            "/api/cash/sit",
            json={
                "table_id": "cash-table-2-001",
                "seat_index": 0,
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # Claimed a real open seat, not the taken one.
        assert data["seat_index"] != 0
        claimed = self.cash_table_repo.load_table(
            "cash-table-2-001", sandbox_id="test-sandbox-1"
        )
        assert claimed.seats[data["seat_index"]]["kind"] == "human"

    def test_full_table_409(self):
        # A genuinely full table (no open seat to fall back to) still 409s,
        # now with a "Table is full" message.
        table = self.cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        full_seats = [ai_slot(self.napoleon_id, 80) for _ in table.seats]
        self.cash_table_repo.save_table(
            CashTableState(
                table_id=table.table_id,
                stake_label=table.stake_label,
                seats=full_seats,
            ),
            sandbox_id="test-sandbox-1",
        )
        resp = self.client.post(
            "/api/cash/sit",
            json={
                "table_id": "cash-table-2-001",
                "seat_index": 0,
            },
        )
        assert resp.status_code == 409
        assert resp.get_json()["error"] == "Table is full"

    # --- Affordability tests (rolled into the same class to avoid
    # per-class setUpClass creating multiple tempdbs).

    def _set_bankroll(self, chips):
        self.bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=PLAYER_OWNER_ID,
                chips=chips,
                starting_bankroll=200,
            )
        )

    def test_unaffordable_at_lowest_tier_returns_sponsor_required(self):
        # Bankroll 0; sponsor-eligible at $2 (lowest tier).
        self._set_bankroll(0)
        # Find an open seat on $2 table.
        table = self.cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        open_idx = next(i for i, s in enumerate(table.seats) if s["kind"] == "open")
        resp = self.client.post(
            "/api/cash/sit",
            json={
                "table_id": "cash-table-2-001",
                "seat_index": open_idx,
            },
        )
        assert resp.status_code == 402
        data = resp.get_json()
        assert data.get("requires_sponsor") is True
        assert data.get("stake_label") == "$2"
        assert data.get("bankroll") == 0
        # The seat must now be held so the world ticker's live-fill can't
        # seat an AI in it while the SponsorModal is open (the "cut by the
        # AI" race). Response echoes the held seat for release/accept.
        assert data.get("table_id") == "cash-table-2-001"
        assert data.get("seat_index") == open_idx
        held = self.cash_table_repo.load_table(
            "cash-table-2-001", sandbox_id="test-sandbox-1"
        )
        assert held.seats[open_idx]["kind"] == "reserved"
        assert held.seats[open_idx]["personality_id"] == PLAYER_OWNER_ID

    def test_release_seat_frees_reservation_and_is_idempotent(self):
        # A 402 places a hold; releasing it returns the seat to "open"
        # and a second release is a harmless no-op.
        self._set_bankroll(0)
        table = self.cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        open_idx = next(i for i, s in enumerate(table.seats) if s["kind"] == "open")
        resp = self.client.post(
            "/api/cash/sit",
            json={"table_id": "cash-table-2-001", "seat_index": open_idx},
        )
        assert resp.status_code == 402
        held = self.cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        assert held.seats[open_idx]["kind"] == "reserved"

        rel = self.client.post(
            "/api/cash/release-seat",
            json={"table_id": "cash-table-2-001", "seat_index": open_idx},
        )
        assert rel.status_code == 200
        assert rel.get_json().get("released") is True
        freed = self.cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        assert freed.seats[open_idx]["kind"] == "open"

        # Idempotent: releasing an already-open seat is a no-op, not an error.
        rel2 = self.client.post(
            "/api/cash/release-seat",
            json={"table_id": "cash-table-2-001", "seat_index": open_idx},
        )
        assert rel2.status_code == 200
        assert rel2.get_json().get("released") is False

    def test_release_seat_leaves_other_players_seat_untouched(self):
        # release-seat only frees the caller's own hold. A reserved seat
        # owned by someone else (or an AI seat) is left alone.
        from cash_mode.tables import reserved_slot

        table = self.cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        open_idx = next(i for i, s in enumerate(table.seats) if s["kind"] == "open")
        other_hold = reserved_slot("someone-else", datetime.utcnow())
        self.cash_table_repo.save_table(
            table.with_seat(open_idx, other_hold), sandbox_id="test-sandbox-1"
        )
        rel = self.client.post(
            "/api/cash/release-seat",
            json={"table_id": "cash-table-2-001", "seat_index": open_idx},
        )
        assert rel.status_code == 200
        assert rel.get_json().get("released") is False
        after = self.cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        assert after.seats[open_idx]["kind"] == "reserved"
        assert after.seats[open_idx]["personality_id"] == "someone-else"

    def test_retap_other_seat_frees_prior_hold(self):
        # Tap seat A (402 → hold), then tap seat B: the prior hold on A
        # must be swept so the player never strands two seats.
        self._set_bankroll(0)
        table = self.cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        open_seats = [i for i, s in enumerate(table.seats) if s["kind"] == "open"]
        seat_a, seat_b = open_seats[0], open_seats[1]

        r_a = self.client.post(
            "/api/cash/sit",
            json={"table_id": "cash-table-2-001", "seat_index": seat_a},
        )
        assert r_a.status_code == 402
        r_b = self.client.post(
            "/api/cash/sit",
            json={"table_id": "cash-table-2-001", "seat_index": seat_b},
        )
        assert r_b.status_code == 402

        after = self.cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        assert after.seats[seat_a]["kind"] == "open", "prior hold on seat A should be freed"
        assert after.seats[seat_b]["kind"] == "reserved"
        assert after.seats[seat_b]["personality_id"] == PLAYER_OWNER_ID

    def test_unaffordable_at_high_tier_400(self):
        # Bankroll 0; $1000 table is locked (not sponsor-eligible).
        self._set_bankroll(0)
        table = self.cash_table_repo.load_table("cash-table-1000-001", sandbox_id="test-sandbox-1")
        open_idx = next(i for i, s in enumerate(table.seats) if s["kind"] == "open")
        resp = self.client.post(
            "/api/cash/sit",
            json={
                "table_id": "cash-table-1000-001",
                "seat_index": open_idx,
            },
        )
        assert resp.status_code == 400

    # --- Happy-path + double-sit combined into one method since
    # the tempdb is class-scoped and we want the second sit-attempt
    # to see the first sit's session still in game_state_service.

    def test_happy_path_and_double_sit(self):
        # Phase 1: happy-path sit.
        self.bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=PLAYER_OWNER_ID,
                chips=10_000,
                starting_bankroll=10_000,
            )
        )
        table = self.cash_table_repo.load_table("cash-table-10-001", sandbox_id="test-sandbox-1")
        open_idx = next(i for i, s in enumerate(table.seats) if s["kind"] == "open")
        resp = self.client.post(
            "/api/cash/sit",
            json={
                "table_id": "cash-table-10-001",
                "seat_index": open_idx,
                "buy_in": 400,
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data.get("table_id") == "cash-table-10-001"
        assert data.get("seat_index") == open_idx
        assert data.get("game_id", "").startswith("cash-")

        # Persisted table: human seat at that index.
        updated = self.cash_table_repo.load_table("cash-table-10-001", sandbox_id="test-sandbox-1")
        assert updated.seats[open_idx]["kind"] == "human"
        assert updated.seats[open_idx]["personality_id"] == PLAYER_OWNER_ID
        assert updated.seats[open_idx]["chips"] == 400

        # Player bankroll debited.
        bankroll = self.bankroll_repo.load_player_bankroll(PLAYER_OWNER_ID)
        assert bankroll.chips == 9600

        # Phase 2: double-sit attempt → 409.
        table2 = self.cash_table_repo.load_table("cash-table-50-001", sandbox_id="test-sandbox-1")
        open_idx2 = next(i for i, s in enumerate(table2.seats) if s["kind"] == "open")
        resp2 = self.client.post(
            "/api/cash/sit",
            json={
                "table_id": "cash-table-50-001",
                "seat_index": open_idx2,
                "buy_in": 2000,
            },
        )
        assert resp2.status_code == 409

    def test_orphaned_seat_is_freed_on_subsequent_sit(self):
        """Regression: a stale `human` slot on table A (game row gone but
        seat never reverted) must NOT survive when the player sits at
        table B. Reproduces the production case where a leave path
        cleaned the game row but left the seat occupied, double-seating
        the user across two tables.
        """
        from flask_app.extensions import game_repo

        self.bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=PLAYER_OWNER_ID,
                chips=10_000,
                starting_bankroll=10_000,
            )
        )

        # Phase 1: sit at $10 (happy path).
        table_a = self.cash_table_repo.load_table("cash-table-10-001", sandbox_id="test-sandbox-1")
        open_idx_a = next(i for i, s in enumerate(table_a.seats) if s["kind"] == "open")
        resp_a = self.client.post(
            "/api/cash/sit",
            json={
                "table_id": "cash-table-10-001",
                "seat_index": open_idx_a,
                "buy_in": 400,
            },
        )
        assert resp_a.status_code == 200, resp_a.get_data(as_text=True)
        sit_a_game_id = resp_a.get_json()["game_id"]

        # Phase 2: orphan the seat by deleting the game row directly,
        # leaving the human slot stranded on cash-table-10-001. Also
        # remove the in-memory game so `_find_active_cash_game_id` can't
        # find it and trip the 409 guard.
        from flask_app.services import game_state_service

        game_state_service.delete_game(sit_a_game_id)
        game_repo.delete_game(sit_a_game_id)
        stranded = self.cash_table_repo.load_table("cash-table-10-001", sandbox_id="test-sandbox-1")
        assert (
            stranded.seats[open_idx_a]["kind"] == "human"
        ), "Seat should still be human before the fix sweeps it"

        # Phase 3: sit at $50 — this must succeed AND free the orphan.
        table_b = self.cash_table_repo.load_table("cash-table-50-001", sandbox_id="test-sandbox-1")
        open_idx_b = next(i for i, s in enumerate(table_b.seats) if s["kind"] == "open")
        resp_b = self.client.post(
            "/api/cash/sit",
            json={
                "table_id": "cash-table-50-001",
                "seat_index": open_idx_b,
                "buy_in": 2000,
            },
        )
        assert resp_b.status_code == 200, resp_b.get_data(as_text=True)

        # Phase 4: the $10 seat must now be open, $50 seat human.
        after_a = self.cash_table_repo.load_table("cash-table-10-001", sandbox_id="test-sandbox-1")
        after_b = self.cash_table_repo.load_table("cash-table-50-001", sandbox_id="test-sandbox-1")
        assert after_a.seats[open_idx_a]["kind"] == "open", "Orphaned $10 seat was not freed"
        assert after_b.seats[open_idx_b]["kind"] == "human"
        assert after_b.seats[open_idx_b]["personality_id"] == PLAYER_OWNER_ID
        # Sanity: owner has no other human slot anywhere.
        humans_for_owner = [
            (t.table_id, i)
            for t in self.cash_table_repo.list_all_tables()
            for i, s in enumerate(t.seats)
            if s.get("kind") == "human" and s.get("personality_id") == PLAYER_OWNER_ID
        ]
        assert humans_for_owner == [("cash-table-50-001", open_idx_b)]

    def test_orphaned_seat_on_same_table_is_freed_on_subsequent_sit(self):
        """Regression: a stale `human` slot at index X on the SAME table
        the player is sitting back down at (different index Y) must NOT
        survive the new sit.

        Pre-fix, `sit_at_table` loaded the table once before calling
        `_free_ghost_human_seats`, then built `claimed_table` from that
        stale snapshot — the save resurrected the just-cleared orphan.
        This is the production scenario where the user resumed a
        session, was placed at a fresh seat, and ended up double-
        seated on the same table.
        """
        from flask_app.extensions import game_repo

        self.bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=PLAYER_OWNER_ID,
                chips=20_000,
                starting_bankroll=20_000,
            )
        )

        table_id = "cash-table-50-001"
        table = self.cash_table_repo.load_table(table_id, sandbox_id="test-sandbox-1")
        # Pick two distinct open indices on the same table.
        open_indices = [i for i, s in enumerate(table.seats) if s["kind"] == "open"]
        assert len(open_indices) >= 2, "need ≥2 open seats for this case"
        orphan_idx, new_idx = open_indices[0], open_indices[1]

        # Phase 1: sit at orphan_idx (legit), then strand it by deleting
        # the game row in both memory and DB. The cash_tables row keeps
        # the human slot — the ghost.
        resp_a = self.client.post(
            "/api/cash/sit",
            json={
                "table_id": table_id,
                "seat_index": orphan_idx,
                "buy_in": 2000,
            },
        )
        assert resp_a.status_code == 200, resp_a.get_data(as_text=True)
        sit_a_game_id = resp_a.get_json()["game_id"]

        from flask_app.services import game_state_service

        game_state_service.delete_game(sit_a_game_id)
        game_repo.delete_game(sit_a_game_id)
        stranded = self.cash_table_repo.load_table(table_id, sandbox_id="test-sandbox-1")
        assert stranded.seats[orphan_idx]["kind"] == "human"

        # Phase 2: sit at new_idx on the SAME table — must succeed and
        # leave only the new seat human.
        resp_b = self.client.post(
            "/api/cash/sit",
            json={
                "table_id": table_id,
                "seat_index": new_idx,
                "buy_in": 2000,
            },
        )
        assert resp_b.status_code == 200, resp_b.get_data(as_text=True)

        after = self.cash_table_repo.load_table(table_id, sandbox_id="test-sandbox-1")
        assert after.seats[new_idx]["kind"] == "human"
        assert after.seats[new_idx]["personality_id"] == PLAYER_OWNER_ID
        assert after.seats[orphan_idx]["kind"] == "open", (
            f"orphan at idx {orphan_idx} was resurrected by the stale-"
            f"snapshot save in sit_at_table — got "
            f"{after.seats[orphan_idx]!r}"
        )
        humans_for_owner = [
            (t.table_id, i)
            for t in self.cash_table_repo.list_all_tables()
            for i, s in enumerate(t.seats)
            if s.get("kind") == "human" and s.get("personality_id") == PLAYER_OWNER_ID
        ]
        assert humans_for_owner == [(table_id, new_idx)]
