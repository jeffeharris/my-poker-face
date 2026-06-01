"""Tests for `cash_mode.tables.CashTableState` and helpers (commit 1).

Covers the in-memory representation of a persisted cash table:
  - Default seat layout: 6 slots, all `"open"`.
  - Slot-kind validation in `__post_init__`.
  - JSON round-trip via `seats_to_json` / `seats_from_json`.
  - Read helpers: `open_seat_indices`, `ai_seat_indices`,
    `seated_personality_ids`, `human_seat_index`, `has_open_seat`.
  - `with_seat` functional update.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from cash_mode.tables import (
    BASELINE_AI_SEATS,
    OPEN_SEATS,
    SEAT_RESERVATION_TTL_SECONDS,
    TABLE_SEAT_COUNT,
    CashTableState,
    ai_slot,
    human_slot,
    is_reservation_expired,
    open_slot,
    reserved_slot,
    seats_from_json,
    seats_to_json,
)


class TestSlotConstructors:
    def test_open_slot(self):
        assert open_slot() == {"kind": "open"}

    def test_ai_slot(self):
        assert ai_slot("napoleon", 1240) == {
            "kind": "ai",
            "personality_id": "napoleon",
            "chips": 1240,
        }

    def test_human_slot(self):
        assert human_slot("user-123", 500) == {
            "kind": "human",
            "personality_id": "user-123",
            "chips": 500,
        }

    def test_reserved_slot(self):
        now = datetime(2026, 5, 29, 12, 0, 0)
        slot = reserved_slot("user-123", now)
        assert slot["kind"] == "reserved"
        assert slot["personality_id"] == "user-123"
        assert slot["reserved_at"] == now.isoformat()
        expected_expiry = now + timedelta(seconds=SEAT_RESERVATION_TTL_SECONDS)
        assert slot["expire_at"] == expected_expiry.isoformat()


class TestReservationExpiry:
    def test_fresh_hold_not_expired(self):
        now = datetime(2026, 5, 29, 12, 0, 0)
        slot = reserved_slot("u1", now)
        # A second later: still well inside the TTL.
        assert is_reservation_expired(slot, now + timedelta(seconds=1)) is False

    def test_hold_expired_past_ttl(self):
        now = datetime(2026, 5, 29, 12, 0, 0)
        slot = reserved_slot("u1", now)
        later = now + timedelta(seconds=SEAT_RESERVATION_TTL_SECONDS + 1)
        assert is_reservation_expired(slot, later) is True

    def test_non_reserved_slots_never_expire(self):
        now = datetime(2026, 5, 29, 12, 0, 0)
        assert is_reservation_expired(open_slot(), now) is False
        assert is_reservation_expired(ai_slot("napoleon", 100), now) is False
        assert is_reservation_expired(human_slot("u1", 100), now) is False

    def test_malformed_expire_at_treated_as_expired(self):
        # A garbled hold must never wedge a seat permanently — the sweep
        # frees it on the next refresh.
        now = datetime(2026, 5, 29, 12, 0, 0)
        assert is_reservation_expired({"kind": "reserved"}, now) is True
        assert is_reservation_expired({"kind": "reserved", "expire_at": "nonsense"}, now) is True

    def test_reserved_seat_index_for(self):
        now = datetime(2026, 5, 29, 12, 0, 0)
        seats = [
            ai_slot("napoleon", 400),
            reserved_slot("me", now),
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        t = CashTableState(table_id="t1", stake_label="$10", seats=seats)
        assert t.reserved_seat_index_for("me") == 1
        assert t.reserved_seat_index_for("someone-else") is None


class TestSeatCounts:
    def test_constants(self):
        assert TABLE_SEAT_COUNT == 6
        assert BASELINE_AI_SEATS == 4
        assert OPEN_SEATS == 2
        assert BASELINE_AI_SEATS + OPEN_SEATS == TABLE_SEAT_COUNT


class TestCashTableStateDefaults:
    def test_default_seats_all_open(self):
        t = CashTableState(table_id="t1", stake_label="$10")
        assert len(t.seats) == TABLE_SEAT_COUNT
        assert all(s["kind"] == "open" for s in t.seats)
        assert t.open_seat_indices() == list(range(TABLE_SEAT_COUNT))
        assert t.ai_seat_indices() == []
        assert t.human_seat_index() is None
        assert t.seated_personality_ids() == []
        assert t.has_open_seat() is True

    def test_mixed_seats(self):
        seats = [
            ai_slot("napoleon", 1240),
            ai_slot("zeus", 800),
            ai_slot("athena", 200),
            ai_slot("gatsby", 1600),
            open_slot(),
            open_slot(),
        ]
        t = CashTableState(table_id="t1", stake_label="$10", seats=seats)
        assert t.ai_seat_indices() == [0, 1, 2, 3]
        assert t.open_seat_indices() == [4, 5]
        assert t.seated_personality_ids() == ["napoleon", "zeus", "athena", "gatsby"]
        assert t.human_seat_index() is None
        assert t.has_open_seat() is True

    def test_with_human_seated(self):
        seats = [
            human_slot("user-1", 500),
            ai_slot("napoleon", 1240),
            ai_slot("zeus", 800),
            ai_slot("athena", 200),
            ai_slot("gatsby", 1600),
            open_slot(),
        ]
        t = CashTableState(table_id="t1", stake_label="$10", seats=seats)
        assert t.human_seat_index() == 0
        assert t.open_seat_indices() == [5]
        assert t.has_open_seat() is True


class TestValidation:
    def test_wrong_length_seats_rejected(self):
        with pytest.raises(ValueError, match="seats length"):
            CashTableState(
                table_id="t1",
                stake_label="$10",
                seats=[open_slot(), open_slot()],  # 2 slots
            )

    def test_unknown_kind_rejected(self):
        seats = [open_slot()] * 5 + [{"kind": "alien"}]
        with pytest.raises(ValueError, match="unknown kind"):
            CashTableState(table_id="t1", stake_label="$10", seats=seats)

    def test_malformed_slot_rejected(self):
        seats = [open_slot()] * 5 + ["not-a-dict"]
        with pytest.raises(ValueError, match="malformed"):
            CashTableState(table_id="t1", stake_label="$10", seats=seats)

    def test_reserved_kind_accepted(self):
        # A "reserved" hold must validate + survive a JSON roundtrip so it
        # persists across the SponsorModal window.
        seats = [reserved_slot("me", datetime(2026, 5, 29, 12, 0, 0))] + [open_slot()] * 5
        t = CashTableState(table_id="t1", stake_label="$10", seats=seats)
        assert t.seats[0]["kind"] == "reserved"
        restored = seats_from_json(seats_to_json(t.seats))
        assert restored[0]["kind"] == "reserved"
        assert restored[0]["personality_id"] == "me"


class TestWithSeat:
    def test_with_seat_replaces_target(self):
        t = CashTableState(table_id="t1", stake_label="$10")
        new_t = t.with_seat(3, ai_slot("napoleon", 1240))
        # Original is unchanged (per immutability invariant).
        assert t.seats[3]["kind"] == "open"
        # New copy has the AI in seat 3.
        assert new_t.seats[3]["kind"] == "ai"
        assert new_t.seats[3]["personality_id"] == "napoleon"
        # Other seats unaffected.
        for i in range(TABLE_SEAT_COUNT):
            if i != 3:
                assert new_t.seats[i]["kind"] == "open"

    def test_with_seat_out_of_range(self):
        t = CashTableState(table_id="t1", stake_label="$10")
        with pytest.raises(ValueError, match="out of range"):
            t.with_seat(99, open_slot())


class TestJsonRoundtrip:
    def test_roundtrip_all_kinds(self):
        seats = [
            ai_slot("napoleon", 1240),
            ai_slot("zeus", 800),
            human_slot("user-1", 500),
            ai_slot("athena", 200),
            open_slot(),
            open_slot(),
        ]
        blob = seats_to_json(seats)
        parsed = seats_from_json(blob)
        assert parsed == seats

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            seats_from_json("not a json list")

    def test_non_list_json_raises(self):
        with pytest.raises(ValueError, match="must decode to a list"):
            seats_from_json('{"not": "a list"}')
