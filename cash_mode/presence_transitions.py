"""Authoritative Presence transition engine for the `save_table` chokepoint.

This is the Phase-3 promotion of the Phase-1 shadow reconcile-diff
(`cash_mode/lobby.py:_shadow_reconcile_table`) into a writer that runs INSIDE
`CashTableRepository.save_table`'s own sqlite transaction, so `entity_presence`
commits atomically with the `cash_tables` seat write (no cross-connection desync
window). It is the single seat-presence writer for BOTH cutover modes:

  * `PRESENCE_AUTHORITY_ENABLED`  → authoritative: presence is the source of
    truth; a double-seat IntegrityError propagates (rolls back the whole
    `save_table`, rejecting the bad seat write — the structural guard doing its
    job).
  * else → no-op.

(The pre-cutover `PRESENCE_SHADOW_WRITE_ENABLED` best-effort-mirror mode was
removed once authority became permanent.)

Why a chokepoint and not call-site reconciles: nearly every seat write (AI
churn, casino, the human sit/leave routes) flows through `save_table`. Driving
presence here covers them all in one place — including paths the call-site
shadow missed (the human path, the casino whale spawn/wind-down).

The engine works by DIFFING the table's prior seat map (loaded by `save_table`)
against the new one and emitting the minimal legal transitions, deriving each
actor's destination from its slot:

  * a departed `player:` seat            → ``GO_OFFLINE`` (a human cashes OUT of
    the sandbox; IDLE is the AI idle-pool concept — design §5.1).
  * a departed `archetype='fish'` seat   → ``RETURN_TO_POOL``.
  * any other departed AI seat           → ``LEAVE`` (→ IDLE) + a
    ``cash_idle_metadata`` row (reason/target_stake from the caller).
  * a newly-occupied seat                 → ``SIT``, after promoting the actor
    through a legal precursor if needed (SEATED-elsewhere → ``LEAVE`` first;
    off-grid ``SIDE_HUSTLE``/``VICE`` → ``END_OFFGRID`` first; a fresh fish from
    ``OFFLINE`` → ``SEED`` → POOL first). These precursors are the gaps live
    shadowing surfaced (``SIT``-from-``SIDE_HUSTLE`` etc. were illegal-and-
    swallowed; here we sequence them legally).

All writes go through the pure machine (`cash_mode.presence.transition`) for
legality, then UPSERT/DELETE directly on the passed-in connection.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, Optional, Tuple

from cash_mode.presence import (
    IllegalPresenceTransition,
    Presence,
    PresenceEvent,
    PresenceState,
    ai_entity_id,
    offline,
    player_entity_id,
    transition,
)

logger = logging.getLogger(__name__)


# --- mode -----------------------------------------------------------------


def _mode() -> Optional[str]:
    """Read the authority flag live (so a runtime flip / test monkeypatch takes
    effect). Returns 'authority' or None."""
    from cash_mode import economy_flags

    if getattr(economy_flags, "PRESENCE_AUTHORITY_ENABLED", False):
        return "authority"
    return None


# --- slot → entity id -----------------------------------------------------


def _entity_id_for_slot(slot: Dict[str, Any]) -> Optional[str]:
    kind = slot.get("kind")
    if kind == "ai":
        pid = slot.get("personality_id")
        return ai_entity_id(pid) if pid else None
    if kind == "human":
        # `human_slot` stores the owner_id in `personality_id`; explicit keys win
        # if present (mirrors the lobby reader + the §A2 human-path fix).
        owner = (
            slot.get("owner_id")
            or slot.get("player_id")
            or slot.get("user_id")
            or slot.get("personality_id")
        )
        return player_entity_id(owner) if owner else None
    return None  # 'open' / 'reserved' carry no presence


def _seat_map(seats) -> Dict[str, Tuple[int, Dict[str, Any]]]:
    """entity_id -> (seat_index, slot) for occupied AI/human seats."""
    out: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    for idx, slot in enumerate(seats or []):
        eid = _entity_id_for_slot(slot)
        if eid:
            out[eid] = (idx, slot)
    return out


# --- raw-connection state read / write ------------------------------------


def _load_state(conn: sqlite3.Connection, entity_id: str, sandbox_id: str) -> PresenceState:
    row = conn.execute(
        "SELECT state, table_id, seat_index, updated_at FROM entity_presence "
        "WHERE entity_id = ? AND sandbox_id = ?",
        (entity_id, sandbox_id),
    ).fetchone()
    if row is None:
        return offline(entity_id, sandbox_id)
    return PresenceState(
        entity_id=entity_id,
        sandbox_id=sandbox_id,
        state=Presence(row[0]),
        table_id=row[1],
        seat_index=row[2],
        updated_at=row[3],
    )


def _write_state(conn: sqlite3.Connection, state: PresenceState) -> None:
    """UPSERT the row (OFFLINE = delete it — 'no row == offline'). Mirrors
    EntityPresenceRepository.save but on the caller's connection."""
    if state.state is Presence.OFFLINE:
        conn.execute(
            "DELETE FROM entity_presence WHERE entity_id = ? AND sandbox_id = ?",
            (state.entity_id, state.sandbox_id),
        )
        return
    conn.execute(
        """
        INSERT INTO entity_presence
            (entity_id, sandbox_id, state, table_id, seat_index, updated_at)
        VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
        ON CONFLICT(entity_id, sandbox_id) DO UPDATE SET
            state      = excluded.state,
            table_id   = excluded.table_id,
            seat_index = excluded.seat_index,
            updated_at = excluded.updated_at
        """,
        (
            state.entity_id,
            state.sandbox_id,
            state.state.value,
            state.table_id,
            state.seat_index,
            state.updated_at,
        ),
    )


def _apply(
    conn: sqlite3.Connection,
    sandbox_id: str,
    entity_id: str,
    event: PresenceEvent,
    *,
    table_id: Optional[str] = None,
    seat_index: Optional[int] = None,
    now_iso: Optional[str] = None,
    raise_on_integrity: bool = False,
) -> PresenceState:
    """Load → pure transition → write, on `conn`. Returns the resulting state
    (or the unchanged current state if the edge was illegal / a swallowed
    collision)."""
    current = _load_state(conn, entity_id, sandbox_id)
    try:
        new_state = transition(
            current, event, table_id=table_id, seat_index=seat_index, updated_at=now_iso
        )
    except IllegalPresenceTransition as e:
        # Post-promotion this shouldn't happen. In AUTHORITY mode it's a real
        # consistency problem (the engine is the final guard) — propagate so the
        # whole save_table rolls back and the anomaly surfaces loudly, rather
        # than silently leaving the entity in an un-sittable state. When called
        # non-authoritatively (`raise_on_integrity=False`), log and skip.
        if raise_on_integrity:
            raise
        logger.warning(
            "[PRESENCE] illegal transition skipped (entity=%s sandbox=%s event=%s): %s",
            entity_id,
            sandbox_id,
            getattr(event, "value", event),
            e,
        )
        return current
    try:
        _write_state(conn, new_state)
    except sqlite3.IntegrityError as e:
        # A double-seat collision (partial-unique index). In AUTHORITY mode this
        # MUST surface — propagate to roll back the whole save_table and reject
        # the bad seat write. When called non-authoritatively, swallow.
        if raise_on_integrity:
            raise
        logger.warning(
            "[PRESENCE] integrity conflict swallowed (entity=%s sandbox=%s event=%s): %s",
            entity_id,
            sandbox_id,
            getattr(event, "value", event),
            e,
        )
        return current
    return new_state


# --- idle metadata satellite ----------------------------------------------


def _idle_meta_write(
    conn: sqlite3.Connection,
    entity_id: str,
    sandbox_id: str,
    idle_metadata: Optional[Dict[str, Any]],
    now_iso: Optional[str],
) -> None:
    """Record an AI's idle routing payload (reason/target_stake) when it lands in
    IDLE. `idle_metadata` is keyed by personality_id; unknown callers default the
    reason to 'forced_leave' (state is correct, reason imprecise — documented)."""
    if not entity_id.startswith("ai:"):
        return
    pid = entity_id[len("ai:") :]
    entry = (idle_metadata or {}).get(pid)
    reason = (
        getattr(entry, "reason", None)
        or (entry.get("reason") if isinstance(entry, dict) else None)
        or "forced_leave"
    )
    target = getattr(entry, "target_stake", None) or (
        entry.get("target_stake") if isinstance(entry, dict) else None
    )
    conn.execute(
        """
        INSERT INTO cash_idle_metadata (personality_id, sandbox_id, reason, target_stake, left_at)
        VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
        ON CONFLICT(personality_id, sandbox_id) DO UPDATE SET
            reason = excluded.reason,
            target_stake = excluded.target_stake,
            left_at = excluded.left_at
        """,
        (pid, sandbox_id, reason, target, now_iso),
    )


def _idle_meta_delete(conn: sqlite3.Connection, entity_id: str, sandbox_id: str) -> None:
    if not entity_id.startswith("ai:"):
        return
    pid = entity_id[len("ai:") :]
    conn.execute(
        "DELETE FROM cash_idle_metadata WHERE personality_id = ? AND sandbox_id = ?",
        (pid, sandbox_id),
    )


def _departure_event(entity_id: str, old_slot: Optional[Dict[str, Any]]) -> PresenceEvent:
    """Where does a vacated seat's occupant go? Derived from the slot it left."""
    if entity_id.startswith("player:"):
        return PresenceEvent.GO_OFFLINE  # human cashes out of the sandbox
    if old_slot is not None and old_slot.get("archetype") == "fish":
        return PresenceEvent.RETURN_TO_POOL  # pool-funded fish
    return PresenceEvent.LEAVE  # cash AI → idle pool


# --- the engine -----------------------------------------------------------


def emit_presence_transitions_for_save(
    conn: sqlite3.Connection,
    sandbox_id: Optional[str],
    old_seats_blob: Optional[str],
    new_table,  # cash_mode.tables.CashTableState
    now_iso: Optional[str] = None,
    *,
    idle_metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Reconcile `entity_presence` to a table's NEW seat map, on `conn`.

    Called inside `CashTableRepository.save_table`'s transaction. No-op unless a
    cutover flag is set. `old_seats_blob` is the prior `cash_tables.seats_json`
    (str) so departures can be detected and their origin (fish/human/AI) read.
    """
    mode = _mode()
    if mode is None or sandbox_id is None or new_table is None:
        return
    # Authority is the only live mode now (the pre-cutover shadow best-effort
    # mode was removed), and `mode is None` returned above — so presence is the
    # source of truth here: a structural-guard IntegrityError (or any write
    # failure) propagates out of `save_table`'s transaction, rolling back the bad
    # seat write rather than being swallowed.
    table_id = new_table.table_id
    desired = _seat_map(new_table.seats)

    old_slots: Dict[str, Dict[str, Any]] = {}
    if old_seats_blob:
        try:
            for slot in json.loads(old_seats_blob):
                eid = _entity_id_for_slot(slot)
                if eid:
                    old_slots[eid] = slot
        except (ValueError, TypeError):
            pass

    # (1) Departures: anyone presence has SEATED at THIS table who is no
    # longer in the new map at the same seat. Clearing them first frees the
    # seat in the partial-unique index so arrivals below can't collide.
    seated_here = conn.execute(
        "SELECT entity_id, seat_index FROM entity_presence "
        "WHERE sandbox_id = ? AND table_id = ? AND state = 'seated'",
        (sandbox_id, table_id),
    ).fetchall()
    for eid, seat in seated_here:
        d = desired.get(eid)
        if d is not None and d[0] == seat:
            continue  # still correctly seated here
        event = _departure_event(eid, old_slots.get(eid))
        result = _apply(conn, sandbox_id, eid, event, now_iso=now_iso, raise_on_integrity=True)
        if result.state is Presence.IDLE:
            _idle_meta_write(conn, eid, sandbox_id, idle_metadata, now_iso)

    # (2) Arrivals: seat each desired occupant, promoting through a legal
    # precursor when its current state can't SIT directly.
    for eid, (seat, slot) in desired.items():
        cur = _load_state(conn, eid, sandbox_id)
        if cur.state is Presence.SEATED and cur.table_id == table_id and cur.seat_index == seat:
            continue  # already correctly seated — no-op

        if cur.state is Presence.SEATED:
            # Seated elsewhere (a move): LEAVE the old seat first.
            _apply(
                conn,
                sandbox_id,
                eid,
                PresenceEvent.LEAVE,
                now_iso=now_iso,
                raise_on_integrity=True,
            )
        elif cur.state in (Presence.SIDE_HUSTLE, Presence.VICE):
            # Returning off-grid AI: END_OFFGRID → IDLE first.
            _apply(
                conn,
                sandbox_id,
                eid,
                PresenceEvent.END_OFFGRID,
                now_iso=now_iso,
                raise_on_integrity=True,
            )

        cur = _load_state(conn, eid, sandbox_id)
        if cur.state is Presence.OFFLINE and slot.get("archetype") == "fish":
            # Fresh pool-funded fish: SEED → POOL before SIT.
            _apply(
                conn,
                sandbox_id,
                eid,
                PresenceEvent.SEED,
                now_iso=now_iso,
                raise_on_integrity=True,
            )

        _apply(
            conn,
            sandbox_id,
            eid,
            PresenceEvent.SIT,
            table_id=table_id,
            seat_index=seat,
            now_iso=now_iso,
            raise_on_integrity=True,
        )
        _idle_meta_delete(conn, eid, sandbox_id)  # no longer idle
