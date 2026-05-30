"""Atomicity of `offer_stake_to_ai` (Window C — human-facing stake route).

Phase 4 of `docs/plans/CASH_SEAT_INVARIANT_HARDENING.md` (§1.2 Window C, §3).

`POST /api/cash/stakes/offer` lets a human stake an AI. The historical
ordering committed the player debit BEFORE the seat write/verify under
the per-sandbox lock, and wrote the backing `stakes` row AFTER the seat
write with no rollback. Two partial-commit windows:

  - **Race**: the chosen open seat is taken by a concurrent ticker
    live-fill between selection and the locked write. The route
    re-verifies the seat under the lock and 409s — but the player was
    already debited above the lock, so chips are gone with no seat.
  - **Orphan**: `create_stake` (last step) raises AFTER `save_table`
    succeeds. The AI is seated with the player's principal on the seat
    but NO backing stake row → on leave, settlement credits the AI the
    full amount and the player gets nothing.

Each test first asserts the bug REPRODUCES on the failure path (player
debited / seat orphaned), then — after the fix — asserts the correct
failure mode (no debit on race; refund + seat-revert on orphan). A
happy-path test pins that a successful offer is unchanged.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from cash_mode.tables import CashTableState, ai_slot, open_slot
from flask_app import create_app
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]


OWNER_ID = "offer-atomicity-player"
TARGET_PID = "napoleon_offer_test"
TARGET_NAME = "Napoleon"
TABLE_ID = "offer-atomicity-50-table"
STAKE_LABEL = "$50"
# $50 tier: bb=50, min_buy_in=2000, max_buy_in=5000. Pure stake principal
# must land in [2000, 5000] and equals the seat chips.
PRINCIPAL = 2000
PLAYER_START_CHIPS = 10_000
AI_START_CHIPS = 10_000
ANCHOR = datetime(2026, 5, 21, 12, 0, 0)


def _ai_config():
    """Personality config: willing borrower with comfort one tier below
    the target ($10 → +1 = $50), friendly axes so the willingness math
    clears."""
    return {
        "anchors": {
            "baseline_aggression": 0.5,
            "baseline_looseness": 0.3,
            "ego": 0.5,
            "poise": 0.7,
        },
        "bankroll_knobs": {
            "starting_bankroll": AI_START_CHIPS,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$10",
        },
        "borrower_profile": {"willing": True, "willingness_threshold": 0.30},
    }


class _TestBase:
    """Shared app + repo wiring (mirrors test_leave_race.py)."""

    @classmethod
    def setup_class(cls):
        cls.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        cls.test_db.close()
        cls.repos = create_repos(cls.test_db.name)

        def mock_init_persistence():
            import flask_app.extensions as ext

            for key, repo in cls.repos.items():
                if key == 'db_path':
                    ext.persistence_db_path = repo
                    continue
                setattr(ext, key, repo)

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            cls.app = create_app()
        cls.app.testing = True

    @classmethod
    def teardown_class(cls):
        try:
            os.unlink(cls.test_db.name)
        except FileNotFoundError:
            pass

    def setup_method(self):
        self.client = self.app.test_client()

        user = {'id': OWNER_ID, 'name': 'OfferTester'}
        authz = MagicMock()
        authz.auth_manager.get_current_user.return_value = user
        authz.has_permission.return_value = True
        self._authz_patcher = patch('poker.authorization.authorization_service', authz)
        self._authz_patcher.start()
        auth_mock = MagicMock()
        auth_mock.get_current_user.return_value = user
        self._auth_patcher = patch('flask_app.extensions.auth_manager', auth_mock)
        self._auth_patcher.start()

        # ensure_lobby_seeded would seed/auto-fill the whole lobby (and
        # could seat our target AI elsewhere). We pre-create exactly one
        # $50 table with one open seat, so stub it to a no-op. The route
        # imports it lazily as `from cash_mode.lobby import
        # ensure_lobby_seeded`, so patch at the source module.
        self._seed_patcher = patch('cash_mode.lobby.ensure_lobby_seeded', lambda **kw: None)
        self._seed_patcher.start()

        # Resolve the sandbox the route will use, then seed into it.
        from flask_app.services.sandbox_resolver import resolve_default_sandbox_for

        self.sandbox_id = resolve_default_sandbox_for(
            OWNER_ID, sandbox_repo=self.repos['sandbox_repo']
        )

        self._seed_world()

    def teardown_method(self):
        self._seed_patcher.stop()
        self._auth_patcher.stop()
        self._authz_patcher.stop()
        # Wipe rows so each test starts fresh.
        with sqlite3.connect(self.test_db.name) as conn:
            for tbl in (
                'personalities',
                'cash_tables',
                'stakes',
                'relationships',
                'player_bankrolls',
                'ai_bankrolls',
            ):
                try:
                    conn.execute(f"DELETE FROM {tbl}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    # --- seeding ----------------------------------------------------------

    def _seed_world(self):
        repos = self.repos
        # Player bankroll (above the 1.5 * 2000 = 3000 floor).
        repos['bankroll_repo'].save_player_bankroll(
            PlayerBankrollState(
                player_id=OWNER_ID,
                chips=PLAYER_START_CHIPS,
                starting_bankroll=PLAYER_START_CHIPS,
            )
        )
        # Target personality + bankroll.
        with sqlite3.connect(self.test_db.name) as conn:
            conn.execute(
                "INSERT INTO personalities (name, personality_id, config_json, visibility) "
                "VALUES (?, ?, ?, 'public')",
                (TARGET_NAME, TARGET_PID, json.dumps(_ai_config())),
            )
            conn.commit()
        repos['bankroll_repo'].save_ai_bankroll(
            AIBankrollState(personality_id=TARGET_PID, chips=AI_START_CHIPS, last_regen_tick=ANCHOR),
            sandbox_id=self.sandbox_id,
        )
        # Met-before relationship with friendly axes so all gates clear.
        from poker.memory.opponent_model import RelationshipState

        repos['relationship_repo'].save_relationship_state(
            TARGET_PID,
            OWNER_ID,
            RelationshipState(
                likability=0.7,
                respect=0.8,
                heat=0.0,
                last_seen=ANCHOR,
                last_decay_tick=ANCHOR,
            ),
        )
        # One $50 table with a single open seat (index 0); the rest are
        # filled by throwaway AIs so the route's random seat pick is
        # deterministic (only seat 0 is open).
        seats = [open_slot()] + [
            ai_slot(f"filler_{i}", PRINCIPAL) for i in range(1, 6)
        ]
        repos['cash_table_repo'].save_table(
            CashTableState(table_id=TABLE_ID, stake_label=STAKE_LABEL, seats=seats),
            sandbox_id=self.sandbox_id,
            now=ANCHOR,
        )

    # --- helpers ----------------------------------------------------------

    def _offer_body(self):
        return {
            "target_pid": TARGET_PID,
            "stake_label": STAKE_LABEL,
            "principal": PRINCIPAL,
            "cut": 0.30,
            "format": "pure",
        }

    def _player_chips(self):
        return self.repos['bankroll_repo'].load_player_bankroll(OWNER_ID).chips

    def _seat0_kind(self):
        t = self.repos['cash_table_repo'].load_table(TABLE_ID, sandbox_id=self.sandbox_id)
        return t.seats[0].get('kind')

    def _stake_count(self):
        with sqlite3.connect(self.test_db.name) as conn:
            return conn.execute("SELECT COUNT(*) FROM stakes").fetchone()[0]


class TestOfferStakeHappyPath(_TestBase):
    def test_successful_offer_end_state(self):
        """Pin the success path: player debited principal, AI seated with
        principal chips at the open seat, exactly one stake row."""
        resp = self.client.post('/api/cash/stakes/offer', json=self._offer_body())
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data.get('accepted') is True, data

        assert self._player_chips() == PLAYER_START_CHIPS - PRINCIPAL
        assert self._seat0_kind() == 'ai'
        t = self.repos['cash_table_repo'].load_table(TABLE_ID, sandbox_id=self.sandbox_id)
        assert t.seats[0].get('personality_id') == TARGET_PID
        assert t.seats[0].get('chips') == PRINCIPAL
        assert self._stake_count() == 1


class TestOfferStakeRaceWindow(_TestBase):
    def test_seat_raced_does_not_strand_player_chips(self):
        """Race: the chosen seat is taken between selection and the locked
        write. Pre-fix the player was already debited above the lock, so
        the 409 stranded their chips. Post-fix: clean 409, NO debit.

        We force the race by patching `load_table` (called *inside* the
        lock to re-read the table) to return a table whose seat 0 is no
        longer open — exactly what a concurrent live-fill would produce.
        """
        real_load = self.repos['cash_table_repo'].load_table

        def racing_load(table_id, *args, **kwargs):
            t = real_load(table_id, *args, **kwargs)
            if t is not None and table_id == TABLE_ID:
                # Simulate a ticker having filled seat 0 in the race window.
                return t.with_seat(0, ai_slot('race_winner', PRINCIPAL))
            return t

        with patch.object(self.repos['cash_table_repo'], 'load_table', side_effect=racing_load):
            resp = self.client.post('/api/cash/stakes/offer', json=self._offer_body())

        assert resp.status_code == 409, resp.get_data(as_text=True)
        # Correct failure mode: the player must NOT be permanently debited.
        assert self._player_chips() == PLAYER_START_CHIPS, (
            "Window C race: player chips were debited despite the 409 — "
            "chips stranded with no seat."
        )
        # No backing stake row should have been written.
        assert self._stake_count() == 0


class TestOfferStakeOrphanWindow(_TestBase):
    def test_create_stake_failure_rolls_back(self):
        """Orphan: `create_stake` raises AFTER the seat write. Pre-fix the
        AI was left seated with the player's principal but no backing
        stake row (a settlement-time chip-loss for the player). Post-fix:
        player refunded AND seat reverted to open.
        """
        def boom(*args, **kwargs):  # noqa: ANN001
            raise RuntimeError("simulated create_stake failure")

        with patch.object(self.repos['stake_repo'], 'create_stake', side_effect=boom):
            resp = self.client.post('/api/cash/stakes/offer', json=self._offer_body())

        # Either a 5xx (fix returns an error) — the key assertions are on
        # the resulting chip/seat state.
        assert resp.status_code >= 500, resp.get_data(as_text=True)

        # Correct failure mode: player fully refunded, seat reverted, no
        # orphaned stake.
        assert self._player_chips() == PLAYER_START_CHIPS, (
            "Window C orphan: player not refunded after create_stake failure — "
            "AI seated with principal but no backing stake row."
        )
        assert self._seat0_kind() == 'open', (
            "Window C orphan: seat left occupied by an AI with no backing "
            "stake row."
        )
        assert self._stake_count() == 0


if __name__ == '__main__':
    import unittest

    unittest.main()
