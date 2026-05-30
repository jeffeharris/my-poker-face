"""Regression: a persona the human is playing live must not be seated by
the world sim.

Field bug ("Blackbeard busted at the $1000 table" mid-hand while the human
was playing Blackbeard at $200): the world sim's occupancy view
(`cash_mode/lobby.py:_global_seated_set`) is built only from the persisted
`cash_tables` snapshot. A human's live hand lives in the in-memory game
registry (`game_state_service`), and its `cash_tables` row can lag or be
absent (legacy `/api/cash/start` path, a mid-session table/stake move that
frees the old row, or the hand-boundary refresh's early-return when
`cash_table_id` is unset). When that happens the live opponent stays
visible to the world ticker, which seats — and busts — it at another table:
the double-booked-persona corruption.

The fix makes the live opponents an authoritative occupancy source:
`game_handler.live_cash_seated_pids` reads them straight from the registry,
and `refresh_unseated_tables(live_seated_pids=...)` treats them as occupied
regardless of snapshot staleness.

`TestLiveCashSeatedPids` pins the registry reader; `TestRefreshHonorsLiveSeated`
proves the world sim won't seat a live opponent even when its `cash_tables`
row is absent (an open seat it would otherwise fill).
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime

import pytest

from cash_mode.lobby import refresh_unseated_tables
from cash_mode.tables import CashTableState, open_slot
from flask_app.handlers import game_handler
from flask_app.services import game_state_service
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager

# ============================================================
# live_cash_seated_pids — the registry reader
# ============================================================


def _cash_game(sandbox_id, name_to_pid):
    return {"sandbox_id": sandbox_id, "cash_personality_ids": dict(name_to_pid)}


@pytest.fixture
def fake_registry(monkeypatch):
    """Swap the in-memory game registry for a controllable dict.

    `list_game_ids()` derives from the same module-level `games` dict, so
    setting it here drives the reader without touching real games.
    """
    games: dict = {}
    monkeypatch.setattr(game_state_service, "games", games)
    return games


class TestLiveCashSeatedPids:
    def test_collects_opponents_for_matching_sandbox(self, fake_registry):
        fake_registry["cash-abc"] = _cash_game("sb-1", {"Blackbeard": "blackbeard", "Zeus": "zeus"})
        assert game_handler.live_cash_seated_pids("sb-1") == {"blackbeard", "zeus"}

    def test_filters_by_sandbox(self, fake_registry):
        fake_registry["cash-abc"] = _cash_game("sb-1", {"Blackbeard": "blackbeard"})
        fake_registry["cash-def"] = _cash_game("sb-2", {"Zeus": "zeus"})
        assert game_handler.live_cash_seated_pids("sb-1") == {"blackbeard"}

    def test_ignores_non_cash_games(self, fake_registry):
        # Same payload shape, but a tournament game_id — not a cash game.
        fake_registry["tournament-xyz"] = _cash_game("sb-1", {"Blackbeard": "blackbeard"})
        assert game_handler.live_cash_seated_pids("sb-1") == set()

    def test_unions_multiple_live_games_same_sandbox(self, fake_registry):
        fake_registry["cash-1"] = _cash_game("sb-1", {"Blackbeard": "blackbeard"})
        fake_registry["cash-2"] = _cash_game("sb-1", {"Zeus": "zeus"})
        assert game_handler.live_cash_seated_pids("sb-1") == {"blackbeard", "zeus"}

    def test_none_sandbox_returns_empty(self, fake_registry):
        fake_registry["cash-abc"] = _cash_game("sb-1", {"Blackbeard": "blackbeard"})
        assert game_handler.live_cash_seated_pids(None) == set()

    def test_fail_soft_on_registry_error(self, monkeypatch):
        def _boom():
            raise RuntimeError("registry exploded")

        monkeypatch.setattr(game_state_service, "list_game_ids", _boom)
        # An error reading the registry must never block the world from
        # advancing — it degrades to "nobody is live" (empty set).
        assert game_handler.live_cash_seated_pids("sb-1") == set()


# ============================================================
# refresh_unseated_tables honors live_seated_pids
# ============================================================


def _insert_personality(db_path: str, personality_id: str, *, name: str, bankroll_knobs: dict):
    config = {"bankroll_knobs": bankroll_knobs}
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id, visibility) "
            "VALUES (?, ?, ?, 'public')",
            (name, json.dumps(config), personality_id),
        )
        conn.commit()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "live_seated.db")
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


@pytest.fixture
def chip_ledger_repo(db_path):
    r = ChipLedgerRepository(db_path)
    yield r
    r.close()


@pytest.mark.integration
class TestRefreshHonorsLiveSeated:
    """Set up one $2 world table with all seats open and `blackbeard` as the
    only eligible candidate, then force the global fill (`seek_rate=1.0`; one
    burst hand runs but it's a no-op on the empty table). Without the
    live_seated param the world seats him (the corruption precondition); with
    it he's treated as occupied and must not be seated."""

    SANDBOX = "sb-live-seated"

    def _setup(self, db_path, cash_table_repo, bankroll_repo):
        _insert_personality(
            db_path,
            "blackbeard",
            name="Blackbeard",
            bankroll_knobs={
                "starting_bankroll": 100_000,
                "bankroll_rate": 0,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$2",
            },
        )
        # Seed the bankroll row production writes at boot
        # (ensure_ai_bankrolls_seeded). Without it the global greedy fill
        # correctly refuses to seat — it can't fund the buy-in from a
        # missing row (the inversion never mints chips onto an open seat,
        # unlike the old per-seat fill which seated regardless).
        from cash_mode.bankroll import AIBankrollState

        bankroll_repo.save_ai_bankroll(
            AIBankrollState(personality_id="blackbeard", chips=100_000, last_regen_tick=None),
            sandbox_id=self.SANDBOX,
        )
        table = CashTableState(
            table_id="cash-table-2-001",
            stake_label="$2",
            seats=[open_slot() for _ in range(6)],
            name="The Back Room",
        )
        cash_table_repo.save_table(table, sandbox_id=self.SANDBOX)

    def _run(self, *, cash_table_repo, personality_repo, bankroll_repo, chip_ledger_repo, live):
        refresh_unseated_tables(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id=self.SANDBOX,
            now=datetime.utcnow(),
            rng=random.Random(0),
            # >0 so one (no-op, empty-table) hand burst runs, then the
            # global greedy fill seats the candidate. seek_rate=1.0 forces
            # every eligible AI to go room-hunting (the inversion replaced
            # the per-seat live_fill_prob with a per-refresh seek-rate).
            hand_sim_prob=1.0,
            seek_rate=1.0,
            chip_ledger_repo=chip_ledger_repo,
            live_seated_pids=live,
        )

    def _seated_pids(self, cash_table_repo):
        pids: set = set()
        for t in cash_table_repo.list_all_tables(sandbox_id=self.SANDBOX):
            pids |= {s["personality_id"] for s in t.seats if s["kind"] == "ai"}
        return pids

    def test_control_seats_blackbeard_without_param(
        self, db_path, cash_table_repo, personality_repo, bankroll_repo, chip_ledger_repo
    ):
        # Establishes the precondition: with the only eligible candidate and
        # a forced fill, the world DOES seat blackbeard. If this regresses,
        # the protection test below would pass vacuously.
        self._setup(db_path, cash_table_repo, bankroll_repo)
        self._run(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            chip_ledger_repo=chip_ledger_repo,
            live=None,
        )
        assert "blackbeard" in self._seated_pids(cash_table_repo)

    def test_live_seated_blackbeard_is_not_seated(
        self, db_path, cash_table_repo, personality_repo, bankroll_repo, chip_ledger_repo
    ):
        self._setup(db_path, cash_table_repo, bankroll_repo)
        self._run(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            chip_ledger_repo=chip_ledger_repo,
            live={"blackbeard"},
        )
        assert "blackbeard" not in self._seated_pids(cash_table_repo)


# ============================================================
# _restore_cash_table_binding — cold-load seat-orphan self-heal
# ============================================================


class _FakeSession:
    def __init__(self, cash_table_id, cash_seat_index):
        self.cash_table_id = cash_table_id
        self.cash_seat_index = cash_seat_index


class _FakeSessionRepo:
    def __init__(self, session):
        self._session = session
        self.loaded_with = None

    def load(self, game_id):
        self.loaded_with = game_id
        return self._session


class TestRestoreCashTableBinding:
    """Regression: a cold-loaded cash game loses `cash_table_id` (it's
    memory-only, not in `game_state_json`). When the hand-boundary refresh
    early-returns on the missing id, the human seat is never re-stamped into
    the `cash_tables` row, `refresh_unseated_tables` then treats the table as
    empty and refills the human's seat with AIs — the "my seat got taken,
    Resume shows different players" split-brain. `_restore_cash_table_binding`
    re-attaches the binding from the durable `cash_sessions` row so the
    refresh keeps the seat protected across cold-loads.
    """

    def _patch_repo(self, monkeypatch, session):
        repo = _FakeSessionRepo(session)
        import flask_app.extensions as ext

        monkeypatch.setattr(ext, "cash_session_repo", repo, raising=False)
        saved: dict = {}
        monkeypatch.setattr(
            game_state_service,
            "set_game",
            lambda gid, data: saved.update({"gid": gid, "data": data}),
        )
        return repo, saved

    def test_present_binding_is_returned_untouched(self, monkeypatch):
        repo, saved = self._patch_repo(monkeypatch, _FakeSession("cash-table-2-001", 4))
        game_data = {"cash_table_id": "already-here", "cash_seat_index": 2}
        out = game_handler._restore_cash_table_binding("cash-x", game_data)
        assert out == "already-here"
        # No session lookup, no write-back when the binding is already present.
        assert repo.loaded_with is None
        assert saved == {}

    def test_recovers_binding_from_session_and_writes_back(self, monkeypatch):
        repo, saved = self._patch_repo(monkeypatch, _FakeSession("cash-table-2-001", 4))
        game_data = {}
        out = game_handler._restore_cash_table_binding("cash-P3lh", game_data)
        assert out == "cash-table-2-001"
        assert repo.loaded_with == "cash-P3lh"
        # Re-stamped onto game_data so subsequent refreshes + leave see it.
        assert game_data["cash_table_id"] == "cash-table-2-001"
        assert game_data["cash_seat_index"] == 4
        assert saved["gid"] == "cash-P3lh"

    def test_no_session_row_returns_none(self, monkeypatch):
        _repo, saved = self._patch_repo(monkeypatch, None)
        game_data = {}
        out = game_handler._restore_cash_table_binding("cash-legacy", game_data)
        # Legacy /api/cash/start games never had a binding — nothing to heal.
        assert out is None
        assert "cash_table_id" not in game_data
        assert saved == {}

    def test_session_without_table_id_returns_none(self, monkeypatch):
        _repo, _saved = self._patch_repo(monkeypatch, _FakeSession(None, None))
        game_data = {}
        out = game_handler._restore_cash_table_binding("cash-coldstart", game_data)
        assert out is None
        assert "cash_table_id" not in game_data


# ============================================================
# _ensure_cash_mode — cold-load cash-metadata rehydration
# ============================================================


class _FakePlayer:
    def __init__(self, name, is_human=False):
        self.name = name
        self.is_human = is_human


class _FakeGameState:
    def __init__(self, players, current_ante=2):
        self.players = players
        self.current_ante = current_ante


class _FakeStateMachine:
    def __init__(self, game_state):
        self.game_state = game_state


class _FakePersonalityRepo:
    def __init__(self, mapping):
        self._m = mapping

    def resolve_name_to_personality_id(self, name):
        return self._m.get(name)


class _FakeMemoryManager:
    def __init__(self):
        self.cap = None

    def set_table_max_buy_in(self, value):
        self.cap = value


class TestEnsureCashMode:
    """Regression for the cold-load ghost-seat split-brain: the cash-mode
    memory fields (`cash_mode`, `cash_table_id`, `cash_personality_ids`, …)
    aren't in `game_state_json`, so a *background* hand advance that
    cold-loads a game (world ticker / socket, bypassing the /api/game-state
    restore block) comes back with `cash_mode` falsy. The hand-end cash flow
    — refill, bust-detect, lobby refresh + the binding self-heal inside it —
    is all gated on that flag, so it silently skips and the human seat
    orphans. `_ensure_cash_mode` rebuilds the dropped fields from the durable
    `cash_sessions` row + live players so the flow runs.
    """

    def _patch(self, monkeypatch, session, name_to_pid):
        import flask_app.extensions as ext

        monkeypatch.setattr(ext, "cash_session_repo", _FakeSessionRepo(session), raising=False)
        monkeypatch.setattr(
            ext, "personality_repo", _FakePersonalityRepo(name_to_pid), raising=False
        )
        saved: dict = {}
        monkeypatch.setattr(
            game_state_service,
            "set_game",
            lambda gid, data: saved.update({"gid": gid, "data": data}),
        )
        return saved

    def test_warm_game_is_noop(self, monkeypatch):
        saved = self._patch(monkeypatch, _FakeSession("cash-table-2-001", 1), {})
        game_data = {"cash_mode": True}
        assert game_handler._ensure_cash_mode("cash-x", game_data) is True
        # Already hydrated — no rebuild, no write-back.
        assert saved == {}

    def test_non_cash_game_returns_false(self, monkeypatch):
        saved = self._patch(monkeypatch, _FakeSession("cash-table-2-001", 1), {})
        game_data = {}
        assert game_handler._ensure_cash_mode("regular-game-id", game_data) is False
        assert "cash_mode" not in game_data
        assert saved == {}

    def test_cold_loaded_cash_game_rehydrates(self, monkeypatch):
        saved = self._patch(
            monkeypatch,
            _FakeSession("cash-casino-2-001", 1),
            {"Sherlock Holmes": "sherlock_holmes", "Lizzo": "lizzo"},
        )
        players = [
            _FakePlayer("Jeff", is_human=True),
            _FakePlayer("Sherlock Holmes"),
            _FakePlayer("Lizzo"),
        ]
        mm = _FakeMemoryManager()
        game_data = {
            "state_machine": _FakeStateMachine(_FakeGameState(players, current_ante=2)),
            "memory_manager": mm,
            "owner_id": "guest_jeff",
        }

        assert game_handler._ensure_cash_mode("cash-GNu7", game_data) is True

        # Flag + binding recovered from the durable session row.
        assert game_data["cash_mode"] is True
        assert game_data["cash_table_id"] == "cash-casino-2-001"
        assert game_data["cash_seat_index"] == 1
        # Stake label + table cap resolved from the big blind ($2 → bb 2).
        assert game_data["cash_stake_label"] == "$2"
        assert mm.cap is not None and mm.cap > 0
        # Personality ids rebuilt from the live opponents only (human excluded).
        assert game_data["cash_personality_ids"] == {
            "Sherlock Holmes": "sherlock_holmes",
            "Lizzo": "lizzo",
        }
        # Persisted so the in-loop refresh + downstream cash steps see it.
        assert saved["gid"] == "cash-GNu7"

    def test_legacy_cash_game_without_binding_still_enables_flow(self, monkeypatch):
        # /api/cash/start games never had a cash_sessions binding. We still
        # want cash_mode on (so refill/bust-detect run); the lobby refresh
        # self-heal will just early-return on the absent table id.
        self._patch(monkeypatch, None, {})
        players = [_FakePlayer("Jeff", is_human=True), _FakePlayer("Buddha")]
        game_data = {
            "state_machine": _FakeStateMachine(_FakeGameState(players, current_ante=10)),
        }
        assert game_handler._ensure_cash_mode("cash-legacy", game_data) is True
        assert game_data["cash_mode"] is True
        assert game_data.get("cash_table_id") is None


if __name__ == "__main__":
    import unittest

    unittest.main()
