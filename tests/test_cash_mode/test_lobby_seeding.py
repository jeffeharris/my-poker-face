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
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration

from cash_mode.bankroll import AIBankrollState
from cash_mode.lobby import (
    _table_id_for_stake,
    ensure_lobby_seeded,
    kill_all_cash_sessions,
)
from cash_mode.lobby_config import LOBBY_TABLES
from cash_mode.stakes_ladder import STAKES_ORDER

# v111: total lobby table count across all tiers, derived from
# `cash_mode/lobby_config.py`. Tests pin to this rather than
# `len(STAKES_ORDER)` so adding/removing tables in lobby_config
# doesn't require touching every assertion here.
EXPECTED_LOBBY_TABLE_COUNT = sum(len(v) for v in LOBBY_TABLES.values())
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
                "starting_bankroll": 100_000,
                "bankroll_rate": 0,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            },
        )


class TestEnsureLobbySeeded:
    def test_creates_one_table_per_stake(
        self,
        cash_table_repo,
        personality_repo,
        bankroll_repo,
        db_path,
    ):
        _seed_personalities(db_path, count=60)
        ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
        )
        tables = cash_table_repo.list_all_tables()
        # v111: lobby_config drives the count, not STAKES_ORDER. Every
        # stake still appears, but with potentially multiple tables.
        assert len(tables) == EXPECTED_LOBBY_TABLE_COUNT
        stake_labels = {t.stake_label for t in tables}
        assert stake_labels == set(STAKES_ORDER)

    def test_each_table_has_4_ai_2_open(
        self,
        cash_table_repo,
        personality_repo,
        bankroll_repo,
        db_path,
    ):
        _seed_personalities(db_path, count=60)
        ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
        )
        for t in cash_table_repo.list_all_tables():
            ai_count = sum(1 for s in t.seats if s["kind"] == "ai")
            open_count = sum(1 for s in t.seats if s["kind"] == "open")
            assert ai_count == 4
            assert open_count == 2

    def test_idempotent(
        self,
        cash_table_repo,
        personality_repo,
        bankroll_repo,
        db_path,
    ):
        _seed_personalities(db_path, count=60)
        first = ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
        )
        # Capture original seats.
        original_seats = {t.table_id: list(t.seats) for t in first}

        second = ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
        )
        # Still the same N tables as the first call.
        assert len(cash_table_repo.list_all_tables()) == EXPECTED_LOBBY_TABLE_COUNT

        # Seat *assignments* unchanged. save_table stamps each AI seat with a
        # volatile `seated_at` timestamp (added 62f57b7a), so compare seats with
        # that field stripped — idempotency means the same personalities/chips
        # in the same seats, not an identical timestamp.
        def _assignments(seats):
            return [{k: v for k, v in s.items() if k != "seated_at"} for s in seats]

        for t in second:
            assert _assignments(t.seats) == _assignments(original_seats[t.table_id])

    def test_one_personality_per_active_seat(
        self,
        cash_table_repo,
        personality_repo,
        bankroll_repo,
        db_path,
    ):
        """Hard invariant: a personality must appear at most one table."""
        _seed_personalities(db_path, count=30)
        ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
        )
        seen_pids = set()
        for t in cash_table_repo.list_all_tables():
            for slot in t.seats:
                if slot["kind"] != "ai":
                    continue
                pid = slot["personality_id"]
                assert pid not in seen_pids, f"Personality {pid!r} appears at multiple tables"
                seen_pids.add(pid)

    def test_chips_match_buy_in(
        self,
        cash_table_repo,
        personality_repo,
        bankroll_repo,
        db_path,
    ):
        _seed_personalities(db_path, count=30)
        tables = ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
        )
        # For the $2 table, big_blind=2, min_buy_in=80; buy_in_multiplier=1.0
        # so chips should equal 80.
        two_dollar = next(t for t in tables if t.stake_label == "$2")
        ai_seats = [s for s in two_dollar.seats if s["kind"] == "ai"]
        assert all(s["chips"] == 80 for s in ai_seats)

    def test_partial_lobby_extended(
        self,
        cash_table_repo,
        personality_repo,
        bankroll_repo,
        db_path,
    ):
        """If the canonical $2 table already exists, seeding leaves it
        untouched and adds the rest of the lobby."""
        _seed_personalities(db_path, count=60)
        # Pre-seed only the canonical $2-001 table.
        existing = CashTableState(
            table_id="cash-table-2-001",
            stake_label="$2",
            # All open — we'll verify it's preserved.
        )
        cash_table_repo.save_table(existing, sandbox_id="test-sandbox-1")

        ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
        )
        tables = cash_table_repo.list_all_tables()
        assert len(tables) == EXPECTED_LOBBY_TABLE_COUNT
        # Pre-existing -001 table preserved: still all open.
        two_a = next(t for t in tables if t.table_id == "cash-table-2-001")
        assert all(s["kind"] == "open" for s in two_a.seats)

    def test_personality_with_low_bankroll_skipped(
        self,
        cash_table_repo,
        personality_repo,
        bankroll_repo,
        db_path,
    ):
        # Two personalities. One has a huge bankroll, the other near-zero.
        _insert_personality(
            db_path,
            "rich",
            name="Rich",
            bankroll_knobs={
                "starting_bankroll": 100_000,
                "bankroll_rate": 0,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            },
        )
        _insert_personality(
            db_path,
            "broke",
            name="Broke",
            bankroll_knobs={
                "starting_bankroll": 100_000,
                "bankroll_rate": 0,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            },
        )
        # Force "broke" to have ai_bankroll_state row of 1 chip.
        bankroll_repo.save_ai_bankroll(
            AIBankrollState(
                personality_id="broke",
                chips=1,
                last_regen_tick=datetime(2026, 5, 18),
            ),
            sandbox_id="test-sandbox-1",
        )
        # "rich" has no row — defaults to starting_bankroll (rich enough).
        ensure_lobby_seeded(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
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
        service = _FakeGameStateService(
            initial={
                "cash-abc": {"cash_mode": True, "owner_id": "u1"},
                "cash-def": {"cash_mode": True, "owner_id": "u2"},
                "tournament-1": {"cash_mode": False, "owner_id": "u1"},
            }
        )
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

    def test_preserves_persisted_cash_rows(self):
        """Persisted cash rows survive boot — they're the resume target."""
        service = _FakeGameStateService()
        repo = _FakeGameRepo(
            [
                _row("cash-old-1"),
                _row("cash-old-2"),
                _row("tournament-3"),
            ]
        )
        dropped = kill_all_cash_sessions(
            game_state_service=service,
            game_repo=repo,
        )
        assert repo.deleted == []
        # Only in-memory wipe counts; nothing to wipe here.
        assert dropped == 0

    def test_drops_in_memory_but_keeps_persisted(self):
        """In-memory cash games are still cleared (no-op in production
        boot since memory starts empty, but useful for callers that
        want a hard reset). Persisted rows untouched."""
        service = _FakeGameStateService(
            initial={
                "cash-abc": {"cash_mode": True, "owner_id": "u1"},
            }
        )
        repo = _FakeGameRepo(
            [
                _row("cash-old-1"),
                _row("tournament-1"),
            ]
        )
        dropped = kill_all_cash_sessions(
            game_state_service=service,
            game_repo=repo,
        )
        assert "cash-abc" in service.deleted
        assert repo.deleted == []
        assert dropped == 1

    def test_empty_state_returns_zero(self):
        service = _FakeGameStateService()
        repo = _FakeGameRepo([])
        assert (
            kill_all_cash_sessions(
                game_state_service=service,
                game_repo=repo,
            )
            == 0
        )


class TestKillAllCashSessionsHumanSeatReset:
    """Orphan-seat reconcile.

    A `"human"` seat is orphan when its owner has no surviving `cash-*`
    row. Those get reset + refunded. Seats backed by a real row stay
    intact so the player can resume on reconnect.
    """

    def test_resets_orphan_seat_and_refunds_chips(self, cash_table_repo, bankroll_repo):
        from cash_mode.bankroll import PlayerBankrollState
        from cash_mode.tables import CashTableState, human_slot, open_slot

        seats = [open_slot() for _ in range(6)]
        seats[4] = human_slot("guest_jeff", 150)
        cash_table_repo.save_table(
            CashTableState(
                table_id="cash-table-2-001",
                stake_label="$2",
                seats=seats,
            ),
            sandbox_id="test-sandbox-1",
        )
        bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id="guest_jeff",
                chips=148,
                starting_bankroll=200,
            )
        )

        # No cash row for guest_jeff → seat is orphan.
        kill_all_cash_sessions(
            game_state_service=_FakeGameStateService(),
            game_repo=_FakeGameRepo([]),
            cash_table_repo=cash_table_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
        )

        reloaded = cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        assert reloaded.seats[4] == open_slot()
        br = bankroll_repo.load_player_bankroll("guest_jeff")
        assert br.chips == 298  # 148 + 150

    def test_preserves_seat_when_cash_row_exists(self, cash_table_repo, bankroll_repo):
        """Backing row present → seat stays seated for resume."""
        from cash_mode.bankroll import PlayerBankrollState
        from cash_mode.tables import CashTableState, human_slot, open_slot

        seats = [open_slot() for _ in range(6)]
        seats[4] = human_slot("guest_jeff", 150)
        cash_table_repo.save_table(
            CashTableState(
                table_id="cash-table-2-001",
                stake_label="$2",
                seats=seats,
            ),
            sandbox_id="test-sandbox-1",
        )
        bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id="guest_jeff",
                chips=148,
                starting_bankroll=200,
            )
        )

        kill_all_cash_sessions(
            game_state_service=_FakeGameStateService(),
            game_repo=_FakeGameRepo([_row("cash-live-1", owner_id="guest_jeff")]),
            cash_table_repo=cash_table_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
        )

        reloaded = cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        assert reloaded.seats[4]["kind"] == "human"
        assert reloaded.seats[4]["chips"] == 150
        br = bankroll_repo.load_player_bankroll("guest_jeff")
        assert br.chips == 148  # not refunded — seat still claims it

    def test_skipped_when_repos_not_provided(self, cash_table_repo):
        """Older test harnesses don't pass the new repos — no-op for seats."""
        from cash_mode.tables import CashTableState, human_slot, open_slot

        seats = [open_slot() for _ in range(6)]
        seats[0] = human_slot("guest_jeff", 100)
        cash_table_repo.save_table(
            CashTableState(
                table_id="cash-table-2-001",
                stake_label="$2",
                seats=seats,
            ),
            sandbox_id="test-sandbox-1",
        )

        kill_all_cash_sessions(
            game_state_service=_FakeGameStateService(),
            game_repo=_FakeGameRepo([]),
        )

        reloaded = cash_table_repo.load_table("cash-table-2-001", sandbox_id="test-sandbox-1")
        assert reloaded.seats[0]["kind"] == "human"


# ============================================================
# Boot-time stale-orphan sweep (T2.2)
# ============================================================


class _FakeCashSessionRepo:
    """Minimal cash_session_repo for the boot-sweep tests."""

    def __init__(self, sessions: dict):
        # game_id -> SimpleNamespace(session_id, ended_at, sandbox_id, ...)
        self._sessions = sessions
        self.finalised: list = []

    def load(self, session_id: str):
        return self._sessions.get(session_id)

    def finalise(self, session_id, *, ended_at, closed_status, **_ignored):
        self.finalised.append((session_id, closed_status))
        s = self._sessions.get(session_id)
        if s is not None:
            s.ended_at = ended_at
        return True


def _session(session_id, ended_at=None):
    return SimpleNamespace(
        session_id=session_id,
        ended_at=ended_at,
        sandbox_id="sb",
        hands_played=0,
        hands_won=0,
        biggest_pot_won=0,
    )


def _row_aged(game_id, age_seconds, *, now, owner_id="u1"):
    return SimpleNamespace(
        game_id=game_id,
        owner_id=owner_id,
        updated_at=now - timedelta(seconds=age_seconds),
    )


class TestKillAllCashSessionsBootSweep:
    """T2.2: abandoned cash-* rows (untouched past the TTL) are swept;
    fresh rows are preserved so resume-on-reboot keeps working."""

    def test_sweeps_stale_orphan_row(self):
        now = datetime(2026, 5, 28, 12, 0, 0)
        repo = _FakeGameRepo([_row_aged("cash-stale-1", 7200, now=now)])  # 2h old
        sessions = _FakeCashSessionRepo({"cash-stale-1": _session("cash-stale-1")})

        kill_all_cash_sessions(
            game_state_service=_FakeGameStateService(),
            game_repo=repo,
            cash_session_repo=sessions,
            stale_ttl_seconds=1800,
            now=now,
        )

        assert "cash-stale-1" in repo.deleted, "stale orphan games row not deleted"
        assert ("cash-stale-1", "boot_swept") in sessions.finalised

    def test_preserves_fresh_orphan_row(self):
        now = datetime(2026, 5, 28, 12, 0, 0)
        repo = _FakeGameRepo([_row_aged("cash-fresh-1", 60, now=now)])  # 1 min old
        sessions = _FakeCashSessionRepo({"cash-fresh-1": _session("cash-fresh-1")})

        kill_all_cash_sessions(
            game_state_service=_FakeGameStateService(),
            game_repo=repo,
            cash_session_repo=sessions,
            stale_ttl_seconds=1800,
            now=now,
        )

        assert repo.deleted == [], "fresh resumable row was swept — resume-on-reboot broken"
        assert sessions.finalised == []

    def test_does_not_touch_tournament_rows(self):
        now = datetime(2026, 5, 28, 12, 0, 0)
        repo = _FakeGameRepo([_row_aged("tournament-old", 99999, now=now)])
        sessions = _FakeCashSessionRepo({})

        kill_all_cash_sessions(
            game_state_service=_FakeGameStateService(),
            game_repo=repo,
            cash_session_repo=sessions,
            stale_ttl_seconds=1800,
            now=now,
        )

        assert repo.deleted == []

    def test_sweep_skipped_without_cash_session_repo(self):
        """Back-compat: callers that don't pass cash_session_repo get
        the legacy behavior (no row sweep)."""
        now = datetime(2026, 5, 28, 12, 0, 0)
        repo = _FakeGameRepo([_row_aged("cash-stale-2", 7200, now=now)])

        kill_all_cash_sessions(
            game_state_service=_FakeGameStateService(),
            game_repo=repo,
        )

        assert repo.deleted == [], "row swept without an explicit cash_session_repo"
