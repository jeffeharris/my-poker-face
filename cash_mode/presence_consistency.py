"""Read-only presence ⇔ seat-map consistency check (read-side completion R1).

The invariant the system now enforces by construction under
`PRESENCE_AUTHORITY_ENABLED`: every `entity_presence` SEATED row corresponds to an
occupied (`ai`/`human`) slot at the same `(table_id, seat_index)` in
`cash_tables`, and every occupied slot has a matching SEATED presence row. Ghost
seats (`seated_and_idle`, `double_seat`) are unrepresentable because the seat
write and the presence transition commit together in `save_table`; this checker
*documents and monitors* that invariant rather than repairing it.

Pure I/O + comparison, no writes, no schema change. Reused by tests. `reserved`
slots are a pre-sit sponsorship hold (the occupant has NOT sat) so they are
deliberately NOT expected to carry a SEATED presence row.

A non-empty result is either a real wiring bug or — on a live DB — a transient of
the ~2s world ticker (the seat write and presence commit are atomic, but a
snapshot taken mid-`save_table` across two connections can straddle them). Callers
auditing live state should double-read and keep only violations present in both.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List

from cash_mode.presence import ai_entity_id, player_entity_id

# Slot kinds that mean "an entity is seated here" (vs open / reserved-hold).
_OCCUPIED_KINDS = ("ai", "human")


def _slot_entity_id(slot: Dict[str, Any]) -> str | None:
    kind = slot.get("kind")
    if kind == "ai":
        pid = slot.get("personality_id")
        return ai_entity_id(pid) if pid else None
    if kind == "human":
        owner = (
            slot.get("owner_id")
            or slot.get("player_id")
            or slot.get("user_id")
            or slot.get("personality_id")
        )
        return player_entity_id(owner) if owner else None
    return None


def check_presence_seat_consistency(
    conn: sqlite3.Connection, sandbox_id: str
) -> List[Dict[str, Any]]:
    """Return a list of consistency violations for one sandbox (empty = consistent).

    Each violation is `{kind, entity_id, table_id, seat_index, detail}` where
    `kind` is one of:
      * `presence_seated_no_slot`  — presence says SEATED@(t,s) but the slot is
        open/reserved (no entity there).
      * `seat_entity_mismatch`     — presence SEATED@(t,s) but a *different*
        entity occupies that slot.
      * `slot_no_presence`         — an occupied slot has no matching SEATED
        presence row for its entity at that seat.
    """
    # 1. SEATED presence rows → {entity_id: (table_id, seat_index)} and the
    #    inverse {(table_id, seat_index): entity_id}.
    presence_by_entity: Dict[str, tuple] = {}
    presence_by_seat: Dict[tuple, str] = {}
    for entity_id, table_id, seat_index in conn.execute(
        "SELECT entity_id, table_id, seat_index FROM entity_presence "
        "WHERE sandbox_id = ? AND state = 'seated'",
        (sandbox_id,),
    ):
        presence_by_entity[entity_id] = (table_id, seat_index)
        presence_by_seat[(table_id, seat_index)] = entity_id

    # 2. Occupied cash_tables slots → {(table_id, seat_index): entity_id}.
    seat_occupants: Dict[tuple, str] = {}
    for table_id, seats_json in conn.execute(
        "SELECT table_id, seats_json FROM cash_tables WHERE sandbox_id = ?",
        (sandbox_id,),
    ):
        try:
            seats = json.loads(seats_json)
        except (ValueError, TypeError):
            continue
        for idx, slot in enumerate(seats):
            if slot.get("kind") in _OCCUPIED_KINDS:
                eid = _slot_entity_id(slot)
                if eid is not None:
                    seat_occupants[(table_id, idx)] = eid

    violations: List[Dict[str, Any]] = []

    # 3a. Every SEATED presence row must map to a matching occupied slot.
    for entity_id, (table_id, seat_index) in presence_by_entity.items():
        occupant = seat_occupants.get((table_id, seat_index))
        if occupant is None:
            violations.append(
                {
                    "kind": "presence_seated_no_slot",
                    "entity_id": entity_id,
                    "table_id": table_id,
                    "seat_index": seat_index,
                    "detail": "presence SEATED but slot is open/reserved",
                }
            )
        elif occupant != entity_id:
            violations.append(
                {
                    "kind": "seat_entity_mismatch",
                    "entity_id": entity_id,
                    "table_id": table_id,
                    "seat_index": seat_index,
                    "detail": f"presence SEATED here but slot holds {occupant!r}",
                }
            )

    # 3b. Every occupied slot must have a matching SEATED presence row.
    for (table_id, seat_index), entity_id in seat_occupants.items():
        seated = presence_by_seat.get((table_id, seat_index))
        if seated != entity_id:
            violations.append(
                {
                    "kind": "slot_no_presence",
                    "entity_id": entity_id,
                    "table_id": table_id,
                    "seat_index": seat_index,
                    "detail": (
                        "occupied slot with no matching SEATED presence row"
                        if seated is None
                        else f"slot holds {entity_id!r} but presence seats {seated!r} here"
                    ),
                }
            )

    return violations


def assert_presence_seat_consistency(conn: sqlite3.Connection, sandbox_id: str) -> None:
    """Raise AssertionError listing any presence ⇔ seat-map violations. For tests
    and assert-on-flip checks; production audits call `check_*` and report."""
    violations = check_presence_seat_consistency(conn, sandbox_id)
    if violations:
        raise AssertionError(
            f"presence/seat-map inconsistency in sandbox {sandbox_id!r}: "
            f"{len(violations)} violation(s): {violations}"
        )
