"""Tests for the Presence dual-write SHADOW wiring of the HUMAN cash path.

Phase 1 of the cutover shadow-wired only the AI seat writers (lobby
seed/fill/burst, casino, off-grid). The HUMAN seat path
(`flask_app/routes/cash_routes.py` sit / sponsor-and-sit / leave) was left
entirely unshadowed — and the shadow's seat-map reader (`_shadow_seat_state`)
didn't even recognise a human slot (`human_slot` stores the owner in
`personality_id`, which the reader didn't check), so a seated human got NO
`entity_presence` row at all.

These tests model exactly what the route handlers now do:

  * SIT  = `save_table(claimed_table)` then
           `lobby._shadow_reconcile_table(claimed_table, sandbox)`
  * LEAVE = `save_table(freed_table)` then
           `presence_shadow.shadow_transition(player:<owner>, GO_OFFLINE)`

and assert the human's presence stays correct across the lifecycle — including
the two edge cases that matter: a stale occupant of the target seat must not
strand the human's SIT (the §C cascade, now fixed), and a human cash-out lands
OFFLINE (row deleted), not in the AI-only IDLE pool.

Conventions per `tests/CLAUDE.md` + `test_shadow_lobby.py`: tempdb, no app,
monkeypatch the flag + `flask_app.extensions.entity_presence_repo`.
"""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.integration

from cash_mode import economy_flags, lobby, presence_shadow
from cash_mode.presence import (
    Presence,
    PresenceEvent,
    ai_entity_id,
    player_entity_id,
)
from cash_mode.tables import CashTableState, ai_slot, human_slot, open_slot
from poker.repositories.entity_presence_repository import EntityPresenceRepository
from poker.repositories.schema_manager import SchemaManager

SANDBOX = "test-sandbox"
OWNER = "player-jeff"


def _presence_repo(tmp_path):
    db_path = str(tmp_path / "cash.db")
    SchemaManager(db_path).ensure_schema()
    return db_path, EntityPresenceRepository(db_path)


def _wire(monkeypatch, presence_repo, *, enabled: bool):
    """Flip the flag + point the shadow's repo resolver at our tempdb repo
    (the route resolves it from `flask_app.extensions.entity_presence_repo`)."""
    import flask_app.extensions as ext

    monkeypatch.setattr(economy_flags, "PRESENCE_SHADOW_WRITE_ENABLED", enabled)
    monkeypatch.setattr(ext, "entity_presence_repo", presence_repo, raising=False)


def _table_with_human(seat_index: int, *, ai_pids=(), buy_in: int = 2000):
    """A 6-seat table with `ai_pids` seated from seat 0 and a human at
    `seat_index`, mirroring what the sit route persists."""
    seats = [open_slot() for _ in range(6)]
    for i, pid in enumerate(ai_pids):
        seats[i] = ai_slot(pid, 1000)
    seats[seat_index] = human_slot(OWNER, buy_in)
    return CashTableState(table_id="cash-table-2-001", stake_label="$2", seats=seats)


def _sit(table: CashTableState):
    """Mirror the route's SIT shadow step (reconcile-diff over the saved table)."""
    lobby._shadow_reconcile_table(table, SANDBOX)


def _leave(owner: str = OWNER):
    """Mirror the route's LEAVE shadow step (explicit GO_OFFLINE for the human)."""
    presence_shadow.shadow_transition(
        entity_id=player_entity_id(owner),
        sandbox_id=SANDBOX,
        event=PresenceEvent.GO_OFFLINE,
    )


# --------------------------------------------------------------------------
# Flag OFF — inert
# --------------------------------------------------------------------------

def test_human_sit_flag_off_writes_nothing(tmp_path, monkeypatch):
    db_path, repo = _presence_repo(tmp_path)
    _wire(monkeypatch, repo, enabled=False)
    _sit(_table_with_human(2, ai_pids=["alice"]))
    assert repo.list_for_sandbox(SANDBOX) == []


# --------------------------------------------------------------------------
# Human SIT — the bug the merged reader had (human slot was skipped entirely)
# --------------------------------------------------------------------------

def test_human_sit_records_seated_row(tmp_path, monkeypatch):
    db_path, repo = _presence_repo(tmp_path)
    _wire(monkeypatch, repo, enabled=True)

    _sit(_table_with_human(2, ai_pids=["alice", "bob"]))

    st = repo.load(player_entity_id(OWNER), SANDBOX)
    assert st.state is Presence.SEATED
    assert (st.table_id, st.seat_index) == ("cash-table-2-001", 2)
    # AIs at the same table are recorded too.
    for i, pid in enumerate(["alice", "bob"]):
        ai = repo.load(ai_entity_id(pid), SANDBOX)
        assert ai.state is Presence.SEATED and ai.seat_index == i


def test_human_sit_no_double_seat(tmp_path, monkeypatch):
    db_path, repo = _presence_repo(tmp_path)
    _wire(monkeypatch, repo, enabled=True)
    _sit(_table_with_human(0))
    seated = [s for s in repo.list_for_sandbox(SANDBOX) if s.is_seated]
    occupied = {(s.table_id, s.seat_index) for s in seated}
    assert len(occupied) == len(seated), "no two entities may share a seat"


# --------------------------------------------------------------------------
# §C protection: a stale occupant must not strand the human's SIT
# --------------------------------------------------------------------------

def test_human_sit_clears_stale_seat_occupant(tmp_path, monkeypatch):
    db_path, repo = _presence_repo(tmp_path)
    _wire(monkeypatch, repo, enabled=True)

    # Simulate an AI that previously sat in seat 2 but left WITHOUT a shadowed
    # LEAVE — a stale SEATED row holding the seat in the partial-unique index.
    repo.persist_transition(
        ai_entity_id("ghost"), SANDBOX, PresenceEvent.SIT,
        table_id="cash-table-2-001", seat_index=2,
    )

    # Human now legitimately takes seat 2. Without the §C reconcile clear, the
    # human's SIT would collide (IntegrityError, swallowed) and strand them.
    _sit(_table_with_human(2, ai_pids=["alice"]))

    human = repo.load(player_entity_id(OWNER), SANDBOX)
    assert human.state is Presence.SEATED
    assert (human.table_id, human.seat_index) == ("cash-table-2-001", 2)
    ghost = repo.load(ai_entity_id("ghost"), SANDBOX)
    assert ghost.state is not Presence.SEATED, "stale occupant must be cleared"


# --------------------------------------------------------------------------
# Human LEAVE — GO_OFFLINE, not the AI idle pool
# --------------------------------------------------------------------------

def test_human_leave_goes_offline(tmp_path, monkeypatch):
    db_path, repo = _presence_repo(tmp_path)
    _wire(monkeypatch, repo, enabled=True)

    _sit(_table_with_human(1, ai_pids=["alice"]))
    assert repo.load(player_entity_id(OWNER), SANDBOX).state is Presence.SEATED

    _leave()
    after = repo.load(player_entity_id(OWNER), SANDBOX)
    # OFFLINE is represented as the absence of a row (no idle-pool residue).
    assert after.state is Presence.OFFLINE
    assert after.table_id is None and after.seat_index is None


def test_human_coldload_leave_no_presence_row_is_safe(tmp_path, monkeypatch):
    """A cold-loaded session never shadowed its SIT, so the human has no row.
    LEAVE must be a swallowed no-op (GO_OFFLINE is illegal from OFFLINE) — never
    a crash, never a stranded row."""
    db_path, repo = _presence_repo(tmp_path)
    _wire(monkeypatch, repo, enabled=True)

    _leave()  # no prior SIT — must not raise
    assert repo.load(player_entity_id(OWNER), SANDBOX).state is Presence.OFFLINE


# --------------------------------------------------------------------------
# Full lifecycle — sit → leave → re-sit → leave stays consistent
# --------------------------------------------------------------------------

def test_human_full_lifecycle_no_ghost(tmp_path, monkeypatch):
    db_path, repo = _presence_repo(tmp_path)
    _wire(monkeypatch, repo, enabled=True)

    _sit(_table_with_human(3, ai_pids=["alice", "bob"]))
    assert repo.load(player_entity_id(OWNER), SANDBOX).seat_index == 3

    _leave()
    assert repo.load(player_entity_id(OWNER), SANDBOX).state is Presence.OFFLINE

    # Re-sit at a DIFFERENT seat — must not leave a ghost at the old seat.
    _sit(_table_with_human(5, ai_pids=["alice", "bob"]))
    st = repo.load(player_entity_id(OWNER), SANDBOX)
    assert st.state is Presence.SEATED and st.seat_index == 5
    human_rows = [
        s for s in repo.list_for_sandbox(SANDBOX)
        if s.entity_id == player_entity_id(OWNER)
    ]
    assert len(human_rows) == 1, "exactly one presence row per entity (compound PK)"

    _leave()
    assert repo.load(player_entity_id(OWNER), SANDBOX).state is Presence.OFFLINE
