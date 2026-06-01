"""D1 read-side occupancy projection (CashTableRepository).

Under PRESENCE_AUTHORITY_ENABLED, table reads render an `ai`/`human` slot that
presence does NOT confirm SEATED as `open` (occupancy-authority / payload-cache).
This makes a stale cache slot structurally invisible to every occupancy read —
the read-side half of retiring the ghost/zombie reconcilers. Writes are
unaffected (they diff against raw stored seats), and the projection is inert
when authority is off.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from cash_mode.tables import CashTableState, ai_slot, human_slot, open_slot
from poker.repositories import create_repos

SB = "proj-sb"
TID = "cash-proj-1"


@pytest.fixture
def repos(tmp_path):
    return create_repos(str(tmp_path / "proj.db"))


@pytest.fixture
def authority_on(monkeypatch):
    monkeypatch.setattr("cash_mode.economy_flags.PRESENCE_AUTHORITY_ENABLED", True)


def _seats(*slots):
    return list(slots) + [open_slot() for _ in range(6 - len(slots))]


def _inject_ghost(repos, table_id, idx, slot):
    """Write a raw cash_tables slot with NO presence row (a ghost)."""
    db = repos["db_path"]
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT seats_json FROM cash_tables WHERE table_id=? AND sandbox_id=?",
        (table_id, SB),
    ).fetchone()
    seats = json.loads(row[0])
    seats[idx] = slot
    conn.execute(
        "UPDATE cash_tables SET seats_json=? WHERE table_id=? AND sandbox_id=?",
        (json.dumps(seats), table_id, SB),
    )
    conn.commit()
    conn.close()


def test_presence_confirmed_seats_are_kept(repos, authority_on):
    repos["cash_table_repo"].save_table(
        CashTableState(table_id=TID, stake_label="$10",
                       seats=_seats(ai_slot("zeus", 1000), human_slot("guest_x", 500))),
        sandbox_id=SB,
    )
    t = repos["cash_table_repo"].load_table(TID, sandbox_id=SB)
    assert t.seats[0]["kind"] == "ai" and t.seats[0]["personality_id"] == "zeus"
    assert t.seats[1]["kind"] == "human"


def test_ghost_slot_projected_open(repos, authority_on):
    repos["cash_table_repo"].save_table(
        CashTableState(table_id=TID, stake_label="$10", seats=_seats(ai_slot("zeus", 1000))),
        sandbox_id=SB,
    )
    # A pid with no presence row, injected straight into the cache.
    _inject_ghost(repos, TID, 1, ai_slot("ghost_pid", 999))
    t = repos["cash_table_repo"].load_table(TID, sandbox_id=SB)
    assert t.seats[0]["kind"] == "ai"           # zeus confirmed → kept
    assert t.seats[1]["kind"] == "open"          # ghost → projected open
    # list_all_tables projects identically.
    lt = next(x for x in repos["cash_table_repo"].list_all_tables(sandbox_id=SB)
              if x.table_id == TID)
    assert lt.seats[1]["kind"] == "open"


def test_projection_inert_when_authority_off(repos):
    repos["cash_table_repo"].save_table(
        CashTableState(table_id=TID, stake_label="$10", seats=_seats(ai_slot("zeus", 1000))),
        sandbox_id=SB,
    )
    _inject_ghost(repos, TID, 1, ai_slot("ghost_pid", 999))
    # authority off (autouse default) → no projection → ghost visible.
    t = repos["cash_table_repo"].load_table(TID, sandbox_id=SB)
    assert t.seats[1]["kind"] == "ai"


def test_reserved_hold_not_projected(repos, authority_on):
    # A reserved hold is a pre-sit hold with no presence SEATED row; it must
    # NOT be projected away.
    repos["cash_table_repo"].save_table(
        CashTableState(table_id=TID, stake_label="$10", seats=_seats(ai_slot("zeus", 1000))),
        sandbox_id=SB,
    )
    _inject_ghost(repos, TID, 1, {"kind": "reserved", "personality_id": "guest_x"})
    t = repos["cash_table_repo"].load_table(TID, sandbox_id=SB)
    assert t.seats[1]["kind"] == "reserved"
