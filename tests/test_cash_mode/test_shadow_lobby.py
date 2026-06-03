"""Tests for the Presence dual-write SHADOW wiring in `cash_mode/lobby.py`.

Phase 1 of the cash-mode Presence cutover mirrors each authoritative
`save_table` seat write into the dormant `entity_presence` table via
`presence_shadow` (see `docs/plans/CASH_MODE_PRESENCE_MIGRATION.md`
§Sequencing step 1). These tests exercise the real lobby seat paths
(`_shadow_reconcile_table` directly + an end-to-end `ensure_lobby_seeded`
seed) and assert that:

  - with the flag OFF, NO `entity_presence` rows are written;
  - with the flag ON, the shadow agrees with the authoritative seat map —
    every occupied seat has exactly one SEATED entity row pointing at that
    (table_id, seat_index), no `seated_and_idle` split-brain, an unchanged
    re-save is a no-op (no illegal `SEATED --sit--> SEATED`), and a move is
    a single relocated row (no double-seat).

Pattern mirrors `test_lobby_seeding.py`: a tempdb path + local seed/repo
helpers (no app, no real DB). The lobby's shadow path resolves its
`entity_presence` repo from `flask_app.extensions.entity_presence_repo`,
so each flag-on test wires the tempdb-backed repo there via monkeypatch.
Conventions per `tests/CLAUDE.md`: integration marker, `tmp_path`, no real DB.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

pytestmark = pytest.mark.integration

from cash_mode import economy_flags, lobby
from cash_mode.presence import Presence, ai_entity_id
from cash_mode.tables import CashTableState, ai_slot, open_slot
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.entity_presence_repository import EntityPresenceRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager

SANDBOX = "test-sandbox"


# --------------------------------------------------------------------------
# Local helpers (mirror test_lobby_seeding.py — tempdb, no shared fixtures)
# --------------------------------------------------------------------------


def _insert_personality(db_path: str, personality_id: str, *, name=None, knobs=None):
    config = {"bankroll_knobs": knobs} if knobs is not None else {}
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities "
            "(name, config_json, personality_id, visibility, circulating) "
            "VALUES (?, ?, ?, 'public', 1)",
            (name or f"Personality {personality_id}", json.dumps(config), personality_id),
        )
        conn.commit()


def _seed_personalities(db_path: str, count: int = 30) -> None:
    """Insert `count` cash-eligible personalities each with a funded
    `ai_bankroll_state` row in SANDBOX, mirroring test_lobby_seeding.py.

    The bankroll row matters: `ensure_lobby_seeded` debits each AI before
    committing its seat, so an unfunded AI is dropped from the seed.
    """
    for i in range(count):
        _insert_personality(
            db_path,
            f"p{i}",
            name=f"Personality {i}",
            knobs={
                "starting_bankroll": 100_000,
                "bankroll_rate": 0,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            },
        )
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO ai_bankroll_state "
                "(personality_id, sandbox_id, chips, last_regen_tick) "
                "VALUES (?, ?, ?, ?)",
                (f"p{i}", SANDBOX, 100_000, datetime.utcnow().isoformat()),
            )
            conn.commit()


def _make_repos(db_path: str):
    SchemaManager(db_path).ensure_schema()
    return (
        CashTableRepository(db_path),
        PersonalityRepository(db_path),
        BankrollRepository(db_path),
        EntityPresenceRepository(db_path),
    )


def _wire_shadow(monkeypatch, presence_repo, *, enabled: bool):
    """Flip the shadow flag and point the lobby's `_shadow_repo()` at our
    tempdb-backed presence repo (there's no Flask app under test)."""
    import flask_app.extensions as ext

    monkeypatch.setattr(economy_flags, "PRESENCE_SHADOW_WRITE_ENABLED", enabled)
    monkeypatch.setattr(ext, "entity_presence_repo", presence_repo, raising=False)


def _presence_rows(db_path: str):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT entity_id, state, table_id, seat_index "
            "FROM entity_presence WHERE sandbox_id = ?",
            (SANDBOX,),
        ).fetchall()


def _seated_rows(db_path: str):
    return [r for r in _presence_rows(db_path) if r[1] == Presence.SEATED.value]


def _make_table(table_id: str, ai_pids):
    """Build a 6-seat CashTableState seating each pid in `ai_pids`.

    Returns (table, {entity_id: (table_id, seat_index)}) with seat indices
    read back from the persisted table (the authoritative map the shadow
    must mirror).
    """
    seats = [open_slot() for _ in range(6)]
    for i, pid in enumerate(ai_pids):
        seats[i] = ai_slot(pid, 1000)
    table = CashTableState(table_id=table_id, stake_label="$2", seats=seats)
    expected = {
        ai_entity_id(slot["personality_id"]): (table.table_id, idx)
        for idx, slot in enumerate(table.seats)
        if slot.get("kind") == "ai"
    }
    return table, expected


# --------------------------------------------------------------------------
# Flag OFF — no shadow writes at all
# --------------------------------------------------------------------------


def test_reconcile_flag_off_writes_nothing(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cash.db")
    _, _, _, presence_repo = _make_repos(db_path)
    _wire_shadow(monkeypatch, presence_repo, enabled=False)

    table, _ = _make_table("cash-table-2-001", ["alice", "bob"])
    lobby._shadow_reconcile_table(table, SANDBOX)
    assert _presence_rows(db_path) == [], "flag OFF must write no entity_presence rows"


def test_seed_flag_off_writes_nothing(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cash.db")
    cash_repo, personality_repo, bankroll_repo, presence_repo = _make_repos(db_path)
    _seed_personalities(db_path, count=60)
    _wire_shadow(monkeypatch, presence_repo, enabled=False)

    lobby.ensure_lobby_seeded(
        cash_table_repo=cash_repo,
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        sandbox_id=SANDBOX,
    )
    assert _presence_rows(db_path) == [], "flag OFF: lobby seed writes no shadow rows"


# --------------------------------------------------------------------------
# Flag ON — shadow mirrors the seat map
# --------------------------------------------------------------------------


def test_reconcile_records_seated_occupants(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cash.db")
    _, _, _, presence_repo = _make_repos(db_path)
    _wire_shadow(monkeypatch, presence_repo, enabled=True)

    table, expected = _make_table("cash-table-2-001", ["alice", "bob"])
    lobby._shadow_reconcile_table(table, SANDBOX)

    for entity_id, (table_id, seat_index) in expected.items():
        st = presence_repo.load(entity_id, SANDBOX)
        assert st.state is Presence.SEATED
        assert (st.table_id, st.seat_index) == (table_id, seat_index)
    assert len(_seated_rows(db_path)) == len(expected) == 2


def test_reconcile_matches_seat_map_no_split_brain(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cash.db")
    _, _, _, presence_repo = _make_repos(db_path)
    _wire_shadow(monkeypatch, presence_repo, enabled=True)

    table, expected = _make_table("cash-table-2-001", ["alice", "bob"])
    lobby._shadow_reconcile_table(table, SANDBOX)

    # Reverse direction: every SEATED shadow row points at a seat actually
    # held by that entity (the seated_and_idle / ghost-seat guard).
    seat_to_entity = {(t, s): e for e, (t, s) in expected.items()}
    for entity_id, _state, table_id, seat_index in _seated_rows(db_path):
        assert (table_id, seat_index) in seat_to_entity
        assert seat_to_entity[(table_id, seat_index)] == entity_id


def test_reconcile_unchanged_table_is_idempotent(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cash.db")
    _, _, _, presence_repo = _make_repos(db_path)
    _wire_shadow(monkeypatch, presence_repo, enabled=True)

    table, _ = _make_table("cash-table-2-001", ["alice"])
    lobby._shadow_reconcile_table(table, SANDBOX)
    before = _presence_rows(db_path)
    # Re-saving the same table must NOT raise illegal SEATED->SIT nor add rows.
    lobby._shadow_reconcile_table(table, SANDBOX)
    after = _presence_rows(db_path)
    assert before == after
    assert len(_seated_rows(db_path)) == 1


def test_reconcile_models_move_as_leave_then_sit(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cash.db")
    _, _, _, presence_repo = _make_repos(db_path)
    _wire_shadow(monkeypatch, presence_repo, enabled=True)

    # Alice seated at table A...
    table_a, _ = _make_table("cash-table-2-001", ["alice"])
    lobby._shadow_reconcile_table(table_a, SANDBOX)
    # ...then she shows up at table B (a rebalance/consolidate move).
    table_b, expected_b = _make_table("cash-table-2-002", ["alice"])
    lobby._shadow_reconcile_table(table_b, SANDBOX)

    alice_eid = ai_entity_id("alice")
    st = presence_repo.load(alice_eid, SANDBOX)
    assert st.state is Presence.SEATED
    assert (st.table_id, st.seat_index) == expected_b[alice_eid]
    assert st.table_id == "cash-table-2-002"
    # Compound PK guarantees one row per entity; assert no stale seat lingers.
    all_alice = [r for r in _presence_rows(db_path) if r[0] == alice_eid]
    assert len(all_alice) == 1
    assert all_alice[0][2] == "cash-table-2-002"


def test_seed_path_mirrors_full_lobby(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cash.db")
    cash_repo, personality_repo, bankroll_repo, presence_repo = _make_repos(db_path)
    _seed_personalities(db_path, count=60)
    _wire_shadow(monkeypatch, presence_repo, enabled=True)

    tables = lobby.ensure_lobby_seeded(
        cash_table_repo=cash_repo,
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        sandbox_id=SANDBOX,
    )

    # Authoritative seat map: every AI seat across the freshly-seeded lobby.
    expected = {
        ai_entity_id(slot["personality_id"]): (t.table_id, idx)
        for t in tables
        for idx, slot in enumerate(t.seats)
        if slot.get("kind") == "ai"
    }
    assert expected, "seed should have placed at least one AI"

    seated = _seated_rows(db_path)
    # One SEATED shadow row per seated AI, agreeing on (table, seat).
    assert len(seated) == len(expected)
    for entity_id, _state, table_id, seat_index in seated:
        assert entity_id in expected
        assert expected[entity_id] == (table_id, seat_index)
