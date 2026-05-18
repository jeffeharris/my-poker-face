"""Tests for `cash_mode.lobby.ensure_lobby_seeded` and `kill_all_cash_sessions`
(commit 4).

`ensure_lobby_seeded` is idempotent: running it twice must not
seed extra tables or seat the same personality at two tables.
`kill_all_cash_sessions` is also idempotent: running on a clean state
is a no-op.

Both are tested against a tempdb to keep the suite hermetic.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration

from cash_mode.bankroll import AIBankrollState
from cash_mode.lobby import (
    _table_id_for_stake,
    ensure_lobby_seeded,
    kill_all_cash_sessions,
)
from cash_mode.stakes import STAKES_ORDER
from cash_mode.tables import CashTableState
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager


def _insert_personality(
    db_path: str,
    personality_id: str,
    *,
    name: str = None,
    bankroll_knobs: dict = None,
) -> None:
    config = {}
    if bankroll_knobs is not None:
        config["bankroll_knobs"] = bankroll_knobs
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id, visibility) "
            "VALUES (?, ?, ?, 'public')",
            (name or f"Personality {personality_id}", json.dumps(config), personality_id),
        )
        conn.commit()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "lobby.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def cash_table_repo(db_path):
    r = CashTableRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def bankroll_repo(db_path):
    r = BankrollRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def personality_repo(db_path):
    r = PersonalityRepository(db_path)
    yield r
    r.close()


# ============================================================
# Table id slug
# ============================================================


class TestTableIdSlug:
    def test_dollar_sign_stripped(self):
        assert _table_id_for_stake("$2") == "cash-table-2-001"
        assert _table_id_for_stake("$10") == "cash-table-10-001"
        assert _table_id_for_stake("$1000") == "cash-table-1000-001"


# ============================================================
# ensure_lobby_seeded
# ============================================================


def _seed_personalities(db_path: str, count: int = 30) -> None:
    """Insert `count` cash-eligible personalities."""
    for i in range(count):
        _insert_personality(
            db_path,
            f"p{i}",
            name=f"Personality {i}",
            bankroll_knobs={
                "bankroll_cap": 100_000,
                "bankroll_rate": 0,
                "buy_in_multiplier": 1.0,
                "stop_loss_buy_ins": 3,
                "stop_win_buy_ins": 5,
                "stake_comfort_zone": "$10",
            },
        )


class TestEnsureLobbySeeded:
    def test_creates_one_table_per_stake(
        self, cash_table_repo, personality_repo, bankroll_repo, db_path,
    ):
        _seed_personalities(db_path, count=30)
        ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
        )
        tables = cash_table_repo.list_all_tables()
        assert len(tables) == len(STAKES_ORDER)
        # One per stake.
        stake_labels = {t.stake_label for t in tables}
        assert stake_labels == set(STAKES_ORDER)

    def test_each_table_has_4_ai_2_open(
        self, cash_table_repo, personality_repo, bankroll_repo, db_path,
    ):
        _seed_personalities(db_path, count=30)
        ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
        )
        for t in cash_table_repo.list_all_tables():
            ai_count = sum(1 for s in t.seats if s["kind"] == "ai")
            open_count = sum(1 for s in t.seats if s["kind"] == "open")
            assert ai_count == 4
            assert open_count == 2

    def test_idempotent(
        self, cash_table_repo, personality_repo, bankroll_repo, db_path,
    ):
        _seed_personalities(db_path, count=30)
        first = ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
        )
        # Capture original seats.
        original_seats = {t.table_id: list(t.seats) for t in first}

        second = ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
        )
        # Still 5 tables.
        assert len(cash_table_repo.list_all_tables()) == len(STAKES_ORDER)
        # Seats unchanged.
        for t in second:
            assert list(t.seats) == original_seats[t.table_id]

    def test_one_personality_per_active_seat(
        self, cash_table_repo, personality_repo, bankroll_repo, db_path,
    ):
        """Hard invariant: a personality must appear at most one table."""
        _seed_personalities(db_path, count=30)
        ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
        )
        seen_pids = set()
        for t in cash_table_repo.list_all_tables():
            for slot in t.seats:
                if slot["kind"] != "ai":
                    continue
                pid = slot["personality_id"]
                assert pid not in seen_pids, (
                    f"Personality {pid!r} appears at multiple tables"
                )
                seen_pids.add(pid)

    def test_chips_match_buy_in(
        self, cash_table_repo, personality_repo, bankroll_repo, db_path,
    ):
        _seed_personalities(db_path, count=30)
        tables = ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
        )
        # For the $2 table, big_blind=2, min_buy_in=80; buy_in_multiplier=1.0
        # so chips should equal 80.
        two_dollar = next(t for t in tables if t.stake_label == "$2")
        ai_seats = [s for s in two_dollar.seats if s["kind"] == "ai"]
        assert all(s["chips"] == 80 for s in ai_seats)

    def test_partial_lobby_extended(
        self, cash_table_repo, personality_repo, bankroll_repo, db_path,
    ):
        """If a $2 table already exists, seeding leaves it untouched and
        adds the missing stakes."""
        _seed_personalities(db_path, count=30)
        # Pre-seed only $2.
        existing = CashTableState(
            table_id="cash-table-2-001",
            stake_label="$2",
            # All open — we'll verify it's preserved.
        )
        cash_table_repo.save_table(existing)

        ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
        )
        tables = cash_table_repo.list_all_tables()
        assert len(tables) == len(STAKES_ORDER)
        # $2 table preserved: all open.
        two = next(t for t in tables if t.stake_label == "$2")
        assert all(s["kind"] == "open" for s in two.seats)

    def test_personality_with_low_bankroll_skipped(
        self, cash_table_repo, personality_repo, bankroll_repo, db_path,
    ):
        # Two personalities. One has a huge bankroll, the other near-zero.
        _insert_personality(db_path, "rich", name="Rich", bankroll_knobs={
            "bankroll_cap": 100_000,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stop_loss_buy_ins": 3,
            "stop_win_buy_ins": 5,
            "stake_comfort_zone": "$10",
        })
        _insert_personality(db_path, "broke", name="Broke", bankroll_knobs={
            "bankroll_cap": 100_000,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stop_loss_buy_ins": 3,
            "stop_win_buy_ins": 5,
            "stake_comfort_zone": "$10",
        })
        # Force "broke" to have ai_bankroll_state row of 1 chip.
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id="broke", chips=1, last_regen_tick=datetime(2026, 5, 18),
        ))
        # "rich" has no row — defaults to bankroll_cap (rich enough).
        ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
        )
        # Find broke in any table — must not appear.
        for t in cash_table_repo.list_all_tables():
            for slot in t.seats:
                if slot["kind"] == "ai":
                    assert slot["personality_id"] != "broke"


# ============================================================
# kill_all_cash_sessions
# ============================================================


class _FakeGameStateService:
    """Minimal stand-in matching game_state_service's interface."""

    def __init__(self, initial: dict = None):
        self.games = dict(initial or {})
        self.deleted = []

    def delete_game(self, game_id: str) -> None:
        if game_id in self.games:
            del self.games[game_id]
            self.deleted.append(game_id)


class _FakeGameRepo:
    """Minimal stand-in matching game_repo's list/delete interface."""

    def __init__(self, rows: list):
        self._rows = list(rows)
        self.deleted = []

    def list_games(self, owner_id=None, limit=10000, offset=0):
        return list(self._rows)

    def delete_game(self, game_id: str) -> None:
        self.deleted.append(game_id)
        self._rows = [r for r in self._rows if r.game_id != game_id]


def _row(game_id, owner_id="u1"):
    return SimpleNamespace(game_id=game_id, owner_id=owner_id)


class TestKillAllCashSessions:
    def test_drops_in_memory_cash_games(self):
        service = _FakeGameStateService(initial={
            "cash-abc": {"cash_mode": True, "owner_id": "u1"},
            "cash-def": {"cash_mode": True, "owner_id": "u2"},
            "tournament-1": {"cash_mode": False, "owner_id": "u1"},
        })
        repo = _FakeGameRepo([])
        dropped = kill_all_cash_sessions(
            game_state_service=service,
            game_repo=repo,
        )
        assert "cash-abc" in service.deleted
        assert "cash-def" in service.deleted
        # Tournament untouched.
        assert "tournament-1" in service.games
        assert dropped == 2

    def test_drops_persisted_cash_rows(self):
        service = _FakeGameStateService()
        repo = _FakeGameRepo([
            _row("cash-old-1"),
            _row("cash-old-2"),
            _row("tournament-3"),  # untouched
        ])
        dropped = kill_all_cash_sessions(
            game_state_service=service,
            game_repo=repo,
        )
        assert "cash-old-1" in repo.deleted
        assert "cash-old-2" in repo.deleted
        assert "tournament-3" not in repo.deleted
        assert dropped == 2

    def test_drops_both_in_memory_and_persisted(self):
        service = _FakeGameStateService(initial={
            "cash-abc": {"cash_mode": True, "owner_id": "u1"},
        })
        repo = _FakeGameRepo([
            _row("cash-old-1"),
            _row("tournament-1"),
        ])
        dropped = kill_all_cash_sessions(
            game_state_service=service,
            game_repo=repo,
        )
        # cash-abc dropped from memory; cash-old-1 from DB.
        assert dropped == 2

    def test_empty_state_returns_zero(self):
        service = _FakeGameStateService()
        repo = _FakeGameRepo([])
        assert kill_all_cash_sessions(
            game_state_service=service, game_repo=repo,
        ) == 0
