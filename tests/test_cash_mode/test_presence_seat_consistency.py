"""R1 — presence ⇔ seat-map consistency checker tests."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cash_mode.presence_consistency import (
    assert_presence_seat_consistency,
    check_presence_seat_consistency,
)
from poker.repositories.schema_manager import SchemaManager

SB = "consistency-sb"
TID = "cash-tbl-1"


@pytest.fixture
def conn(tmp_path):
    p = str(tmp_path / "consistency.db")
    SchemaManager(p).ensure_schema()
    c = sqlite3.connect(p)
    yield c
    c.close()


def _seat(conn, seats):
    conn.execute(
        "INSERT INTO cash_tables (table_id, sandbox_id, stake_label, seats_json) "
        "VALUES (?, ?, ?, ?)",
        (TID, SB, "$10", json.dumps(seats)),
    )
    conn.commit()


def _presence(conn, entity_id, state, table_id=None, seat_index=None):
    conn.execute(
        "INSERT INTO entity_presence (entity_id, sandbox_id, state, table_id, seat_index) "
        "VALUES (?, ?, ?, ?, ?)",
        (entity_id, SB, state, table_id, seat_index),
    )
    conn.commit()


def test_consistent_when_presence_and_slots_agree(conn):
    _seat(conn, [
        {"kind": "ai", "personality_id": "zeus", "chips": 1000},
        {"kind": "human", "owner_id": "guest_x", "chips": 500},
        {"kind": "open"},
    ])
    _presence(conn, "ai:zeus", "seated", TID, 0)
    _presence(conn, "player:guest_x", "seated", TID, 1)
    assert check_presence_seat_consistency(conn, SB) == []
    assert_presence_seat_consistency(conn, SB)  # does not raise


def test_reserved_slot_needs_no_presence(conn):
    # A reserved hold is pre-sit — no SEATED presence expected.
    _seat(conn, [{"kind": "reserved", "owner_id": "guest_x"}, {"kind": "open"}])
    assert check_presence_seat_consistency(conn, SB) == []


def test_presence_seated_but_slot_open(conn):
    _seat(conn, [{"kind": "open"}, {"kind": "open"}])
    _presence(conn, "ai:zeus", "seated", TID, 0)
    v = check_presence_seat_consistency(conn, SB)
    assert len(v) == 1 and v[0]["kind"] == "presence_seated_no_slot"
    assert v[0]["entity_id"] == "ai:zeus"


def test_occupied_slot_no_presence(conn):
    _seat(conn, [{"kind": "ai", "personality_id": "zeus", "chips": 1000}])
    # no presence row for zeus
    v = check_presence_seat_consistency(conn, SB)
    assert len(v) == 1 and v[0]["kind"] == "slot_no_presence"


def test_seat_entity_mismatch(conn):
    _seat(conn, [{"kind": "ai", "personality_id": "hades", "chips": 1000}])
    _presence(conn, "ai:zeus", "seated", TID, 0)  # presence says zeus, slot says hades
    v = check_presence_seat_consistency(conn, SB)
    kinds = {x["kind"] for x in v}
    # Both directions flag it: zeus has no real slot, hades' slot has no presence.
    assert "seat_entity_mismatch" in kinds
    assert_raises = False
    try:
        assert_presence_seat_consistency(conn, SB)
    except AssertionError:
        assert_raises = True
    assert assert_raises
