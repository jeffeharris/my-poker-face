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
    only eligible candidate, then force live-fill (`live_fill_prob=1.0`; one
    burst hand runs but it's a no-op on the empty table). Without the param
    the world seats him (the corruption precondition); with the param it must
    not."""

    SANDBOX = "sb-live-seated"

    def _setup(self, db_path, cash_table_repo):
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
            # >0 so one (no-op, empty-table) hand burst runs — live-fill
            # only fires inside the burst loop. live_fill_prob=1.0 forces
            # the open seat to fill.
            hand_sim_prob=1.0,
            live_fill_prob=1.0,
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
        self._setup(db_path, cash_table_repo)
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
        self._setup(db_path, cash_table_repo)
        self._run(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            chip_ledger_repo=chip_ledger_repo,
            live={"blackbeard"},
        )
        assert "blackbeard" not in self._seated_pids(cash_table_repo)


if __name__ == "__main__":
    import unittest

    unittest.main()
