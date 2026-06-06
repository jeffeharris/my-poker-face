"""Tests for the Phase-3 Presence authority engine (`cash_mode/presence_transitions.py`).

The engine reconciles `entity_presence` to a saved `CashTableState`'s seat map,
on a caller-supplied sqlite connection (as it will run inside
`CashTableRepository.save_table`'s transaction). These tests drive it directly
against a temp DB and assert the resulting presence rows + `cash_idle_metadata`,
covering the origin derivation (human→GO_OFFLINE, fish→RETURN_TO_POOL, AI→LEAVE)
and the SIT-precursor promotions that live shadowing surfaced (off-grid→END_OFFGRID,
seated-elsewhere→LEAVE, fresh fish→SEED).

Conventions per `tests/CLAUDE.md`: temp DB, no app, monkeypatch the flags.
"""

from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.integration

from cash_mode import economy_flags, presence_transitions as pt
from cash_mode.presence import Presence, PresenceEvent, ai_entity_id, player_entity_id
from cash_mode.tables import CashTableState, ai_slot, ai_slot_fish, human_slot, open_slot
from poker.repositories.schema_manager import SchemaManager

SANDBOX = "sb-test"
TID = "cash-table-2-001"
TID2 = "cash-table-2-002"


@pytest.fixture
def conn(tmp_path):
    db = str(tmp_path / "cash.db")
    SchemaManager(db).ensure_schema()
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row  # match BaseRepository._get_connection (prod parity)
    yield c
    c.close()


def _authority(monkeypatch):
    monkeypatch.setattr(economy_flags, "PRESENCE_AUTHORITY_ENABLED", True)
    monkeypatch.setattr(economy_flags, "PRESENCE_SHADOW_WRITE_ENABLED", False)


def _table(table_id, seats):
    full = list(seats) + [open_slot() for _ in range(6 - len(seats))]
    return CashTableState(table_id=table_id, stake_label="$2", seats=full)


def _state(conn, entity_id):
    row = conn.execute(
        "SELECT state, table_id, seat_index FROM entity_presence WHERE entity_id=? AND sandbox_id=?",
        (entity_id, SANDBOX),
    ).fetchone()
    return tuple(row) if row is not None else None  # (state, table_id, seat_index) or None


def _emit(conn, new_table, old_blob=None, idle_metadata=None):
    pt.emit_presence_transitions_for_save(
        conn,
        SANDBOX,
        old_blob,
        new_table,
        now_iso="2026-01-01T00:00:00",
        idle_metadata=idle_metadata,
    )


# --- flag off -------------------------------------------------------------


def test_flag_off_writes_nothing(conn, monkeypatch):
    monkeypatch.setattr(economy_flags, "PRESENCE_AUTHORITY_ENABLED", False)
    monkeypatch.setattr(economy_flags, "PRESENCE_SHADOW_WRITE_ENABLED", False)
    _emit(conn, _table(TID, [ai_slot("a", 100)]))
    assert conn.execute("SELECT COUNT(*) FROM entity_presence").fetchone()[0] == 0


# --- arrivals: AI + human + fish all seated -------------------------------


def test_seats_ai_human_and_fish(conn, monkeypatch):
    _authority(monkeypatch)
    t = _table(TID, [ai_slot("alice", 100), human_slot("jeff", 200), ai_slot_fish("nemo", 50)])
    _emit(conn, t)
    assert _state(conn, ai_entity_id("alice")) == ("seated", TID, 0)
    assert _state(conn, player_entity_id("jeff")) == ("seated", TID, 1)
    # fish promoted OFFLINE→SEED→POOL→SIT, ends SEATED
    assert _state(conn, ai_entity_id("nemo")) == ("seated", TID, 2)


def test_no_double_seat(conn, monkeypatch):
    _authority(monkeypatch)
    _emit(conn, _table(TID, [ai_slot("a", 1), ai_slot("b", 1), human_slot("h", 1)]))
    seated = conn.execute(
        "SELECT table_id, seat_index, COUNT(*) FROM entity_presence "
        "WHERE state='seated' GROUP BY table_id, seat_index HAVING COUNT(*)>1"
    ).fetchall()
    assert seated == []


# --- departures -----------------------------------------------------------


def test_ai_departure_goes_idle_with_metadata(conn, monkeypatch):
    _authority(monkeypatch)
    old = _table(TID, [ai_slot("alice", 100)])
    _emit(conn, old)
    assert _state(conn, ai_entity_id("alice"))[0] == "seated"
    # alice leaves: new table has her seat open
    new = _table(TID, [open_slot()])
    import json

    meta = {"alice": {"reason": "take_break", "target_stake": "$5"}}
    _emit(conn, new, old_blob=json.dumps(old.seats), idle_metadata=meta)
    assert _state(conn, ai_entity_id("alice")) == ("idle", None, None)
    row = conn.execute(
        "SELECT reason, target_stake FROM cash_idle_metadata WHERE personality_id='alice' AND sandbox_id=?",
        (SANDBOX,),
    ).fetchone()
    assert tuple(row) == ("take_break", "$5")


def test_human_departure_goes_offline(conn, monkeypatch):
    _authority(monkeypatch)
    old = _table(TID, [human_slot("jeff", 200)])
    _emit(conn, old)
    assert _state(conn, player_entity_id("jeff"))[0] == "seated"
    import json

    _emit(conn, _table(TID, [open_slot()]), old_blob=json.dumps(old.seats))
    # OFFLINE == no row
    assert _state(conn, player_entity_id("jeff")) is None


def test_fish_departure_returns_to_pool(conn, monkeypatch):
    _authority(monkeypatch)
    old = _table(TID, [ai_slot_fish("nemo", 50)])
    _emit(conn, old)
    import json

    _emit(conn, _table(TID, [open_slot()]), old_blob=json.dumps(old.seats))
    assert _state(conn, ai_entity_id("nemo")) == ("pool", None, None)


# --- precursor promotions (the gaps live shadowing surfaced) --------------


def test_offgrid_ai_can_be_seated(conn, monkeypatch):
    _authority(monkeypatch)
    # put alice on a side hustle first (IDLE→START_HUSTLE)
    from cash_mode.presence import offline, transition

    s = transition(
        offline(ai_entity_id("alice"), SANDBOX), PresenceEvent.SIT, table_id=TID, seat_index=0
    )
    pt._write_state(conn, s)
    pt._apply(conn, SANDBOX, ai_entity_id("alice"), PresenceEvent.LEAVE)  # → idle
    pt._apply(conn, SANDBOX, ai_entity_id("alice"), PresenceEvent.START_HUSTLE)  # → side_hustle
    assert _state(conn, ai_entity_id("alice"))[0] == "side_hustle"
    # now seat her — engine must END_OFFGRID then SIT (no illegal swallow)
    _emit(conn, _table(TID, [ai_slot("alice", 100)]))
    assert _state(conn, ai_entity_id("alice")) == ("seated", TID, 0)


def test_move_between_tables_no_ghost(conn, monkeypatch):
    _authority(monkeypatch)
    _emit(conn, _table(TID, [ai_slot("alice", 100)]))  # seated at TID/0
    # alice now appears at TID2 seat 1 (a move) — TID2 save
    _emit(conn, _table(TID2, [open_slot(), ai_slot("alice", 100)]))
    assert _state(conn, ai_entity_id("alice")) == ("seated", TID2, 1)
    rows = conn.execute(
        "SELECT COUNT(*) FROM entity_presence WHERE entity_id=? AND sandbox_id=?",
        (ai_entity_id("alice"), SANDBOX),
    ).fetchone()[0]
    assert rows == 1  # compound PK — exactly one row, old seat not ghosted


def test_stale_occupant_cleared_lets_new_sit(conn, monkeypatch):
    _authority(monkeypatch)
    # bob is SEATED at TID/0 in presence but the new table seats alice there
    _emit(conn, _table(TID, [ai_slot("bob", 1)]))
    assert _state(conn, ai_entity_id("bob")) == ("seated", TID, 0)
    _emit(conn, _table(TID, [ai_slot("alice", 1)]))  # bob gone, alice arrives at seat 0
    assert _state(conn, ai_entity_id("alice")) == ("seated", TID, 0)
    assert _state(conn, ai_entity_id("bob")) == ("idle", None, None)  # cleared, not stranded


# --- authority vs shadow integrity semantics ------------------------------


def test_apply_integrity_raises_in_authority_swallows_in_shadow(conn):
    # seed bob SEATED at TID/0 directly
    from cash_mode.presence import offline, transition

    pt._write_state(
        conn,
        transition(
            offline(ai_entity_id("bob"), SANDBOX), PresenceEvent.SIT, table_id=TID, seat_index=0
        ),
    )
    # alice (currently idle) tries to SIT the SAME occupied seat
    pt._apply(
        conn, SANDBOX, ai_entity_id("alice"), PresenceEvent.SIT, table_id=TID2, seat_index=0
    )  # park elsewhere first? no — give her a state
    # force alice OFFLINE→SIT to TID/0 with raise → IntegrityError
    conn.execute("DELETE FROM entity_presence WHERE entity_id=?", (ai_entity_id("alice"),))
    with pytest.raises(sqlite3.IntegrityError):
        pt._apply(
            conn,
            SANDBOX,
            ai_entity_id("alice"),
            PresenceEvent.SIT,
            table_id=TID,
            seat_index=0,
            raise_on_integrity=True,
        )
    # same with raise_on_integrity=False → swallowed, alice not seated there
    conn.execute("DELETE FROM entity_presence WHERE entity_id=?", (ai_entity_id("alice"),))
    pt._apply(
        conn,
        SANDBOX,
        ai_entity_id("alice"),
        PresenceEvent.SIT,
        table_id=TID,
        seat_index=0,
        raise_on_integrity=False,
    )
    assert _state(conn, ai_entity_id("alice")) is None  # swallowed


def test_shadow_mode_never_raises(conn, monkeypatch):
    monkeypatch.setattr(economy_flags, "PRESENCE_AUTHORITY_ENABLED", False)
    monkeypatch.setattr(economy_flags, "PRESENCE_SHADOW_WRITE_ENABLED", True)
    # even a degenerate table shouldn't raise in shadow mode
    _emit(conn, _table(TID, [ai_slot("a", 1)]))
    assert _state(conn, ai_entity_id("a")) == ("seated", TID, 0)


def test_apply_illegal_transition_raises_under_authority(conn):
    """Review finding 1: an illegal edge must PROPAGATE when raise_on_integrity
    (authority) is set — the engine is the final guard — and be swallowed
    otherwise."""
    from cash_mode.presence import IllegalPresenceTransition

    # OFFLINE --end_offgrid--> is not a legal edge.
    with pytest.raises(IllegalPresenceTransition):
        pt._apply(
            conn, SANDBOX, ai_entity_id("ghost"), PresenceEvent.END_OFFGRID, raise_on_integrity=True
        )
    # swallowed when not authoritative — entity stays OFFLINE (no row)
    pt._apply(
        conn, SANDBOX, ai_entity_id("ghost"), PresenceEvent.END_OFFGRID, raise_on_integrity=False
    )
    assert _state(conn, ai_entity_id("ghost")) is None


def test_coldload_binding_prefers_presence_over_stale_cash_session(tmp_path, monkeypatch):
    """Read-side migration: under authority, _restore_cash_table_binding recovers
    the seat from the authoritative entity_presence row, overriding a STALE
    cash_sessions binding (e.g. the player moved seats after sit)."""
    from unittest.mock import MagicMock

    import flask_app.extensions as ext
    import flask_app.services.game_state_service as gss
    from flask_app.handlers import game_handler
    from poker.repositories.entity_presence_repository import EntityPresenceRepository

    db = str(tmp_path / "cl.db")
    SchemaManager(db).ensure_schema()
    epr = EntityPresenceRepository(db)
    # Authoritative presence: jeff is SEATED at the LIVE seat.
    epr.persist_transition(
        player_entity_id("jeff"),
        SANDBOX,
        PresenceEvent.SIT,
        table_id="cash-table-2-009",
        seat_index=4,
    )
    # cash_sessions has a STALE binding (sit-time seat, since moved).
    cs = MagicMock(
        owner_id="jeff", sandbox_id=SANDBOX, cash_table_id="cash-table-2-001", cash_seat_index=0
    )
    fake_session_repo = MagicMock()
    fake_session_repo.load.return_value = cs

    monkeypatch.setattr(economy_flags, "PRESENCE_AUTHORITY_ENABLED", True)
    monkeypatch.setattr(ext, "entity_presence_repo", epr, raising=False)
    monkeypatch.setattr(ext, "cash_session_repo", fake_session_repo, raising=False)
    monkeypatch.setattr(gss, "set_game", lambda *a, **k: None)

    gd: dict = {}
    resolved = game_handler._restore_cash_table_binding("cash-jeff-1", gd)
    assert resolved == "cash-table-2-009"  # presence (authority) wins
    assert gd["cash_table_id"] == "cash-table-2-009"
    assert gd["cash_seat_index"] == 4

    # authority OFF → falls back to the cash_sessions binding (unchanged path)
    monkeypatch.setattr(economy_flags, "PRESENCE_AUTHORITY_ENABLED", False)
    gd2: dict = {}
    assert game_handler._restore_cash_table_binding("cash-jeff-1", gd2) == "cash-table-2-001"


def test_list_idle_derives_from_presence_excludes_hustlers(tmp_path, monkeypatch):
    """list_idle returns the genuinely-idle set from entity_presence (+ the
    cash_idle_metadata satellite), and an off-grid AI (SIDE_HUSTLE) is correctly
    EXCLUDED. The cutover is complete: idle is always derived from presence (the
    legacy cash_idle_pool cache was dropped in v152)."""
    from poker.repositories.cash_table_repository import CashTableRepository
    from poker.repositories.entity_presence_repository import EntityPresenceRepository

    db = str(tmp_path / "i.db")
    SchemaManager(db).ensure_schema()
    ctr = CashTableRepository(db)
    epr = EntityPresenceRepository(db)
    # alice → IDLE (sit then leave) with routing metadata
    epr.persist_transition(
        ai_entity_id("alice"), SANDBOX, PresenceEvent.SIT, table_id="t", seat_index=0
    )
    epr.persist_transition(ai_entity_id("alice"), SANDBOX, PresenceEvent.LEAVE)
    sqlite3.connect(db).execute(
        "INSERT INTO cash_idle_metadata (personality_id,sandbox_id,reason,target_stake,left_at) "
        "VALUES ('alice',?,'stake_up_queued','$5','2026-01-01T00:00:00')",
        (SANDBOX,),
    ).connection.commit()
    # bob → SIDE_HUSTLE (idle then start) — must NOT be offered as idle
    epr.persist_transition(
        ai_entity_id("bob"), SANDBOX, PresenceEvent.SIT, table_id="t", seat_index=1
    )
    epr.persist_transition(ai_entity_id("bob"), SANDBOX, PresenceEvent.LEAVE)
    epr.persist_transition(ai_entity_id("bob"), SANDBOX, PresenceEvent.START_HUSTLE)

    monkeypatch.setattr(economy_flags, "PRESENCE_AUTHORITY_ENABLED", True)
    out = ctr.list_idle(sandbox_id=SANDBOX)
    pids = {e.personality_id for e in out}
    assert "alice" in pids
    assert "bob" not in pids, "an off-grid hustler must not be in the idle re-seat set"
    alice = next(e for e in out if e.personality_id == "alice")
    assert alice.reason == "stake_up_queued" and alice.target_stake == "$5"


def test_delete_idle_clears_stale_presence_but_not_seated(tmp_path, monkeypatch):
    """Under authority, delete_idle clears a STALE presence IDLE row (reaped from
    the pool without a re-seat → OFFLINE), but must NOT touch a SEATED row (a
    re-seat already moved it via save_table)."""
    from poker.repositories.cash_table_repository import CashTableRepository
    from poker.repositories.entity_presence_repository import EntityPresenceRepository

    db = str(tmp_path / "d.db")
    SchemaManager(db).ensure_schema()
    ctr = CashTableRepository(db)
    epr = EntityPresenceRepository(db)
    monkeypatch.setattr(economy_flags, "PRESENCE_AUTHORITY_ENABLED", True)

    epr.persist_transition(
        ai_entity_id("alice"), SANDBOX, PresenceEvent.SIT, table_id="t", seat_index=0
    )
    epr.persist_transition(ai_entity_id("alice"), SANDBOX, PresenceEvent.LEAVE)  # IDLE
    ctr.delete_idle("alice", sandbox_id=SANDBOX)
    assert epr.load(ai_entity_id("alice"), SANDBOX).state is Presence.OFFLINE  # stale cleared

    epr.persist_transition(
        ai_entity_id("carol"), SANDBOX, PresenceEvent.SIT, table_id="t", seat_index=2
    )
    ctr.delete_idle("carol", sandbox_id=SANDBOX)  # re-seat cleanup must not offline her
    assert epr.load(ai_entity_id("carol"), SANDBOX).state is Presence.SEATED
