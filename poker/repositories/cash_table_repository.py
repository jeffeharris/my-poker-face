"""Repository for the v91 `cash_tables` persistence surface.

One row per persistent lobby table; the `seats_json` column carries
the 6 slot dicts (4 baseline AI + 2 open) that make the lobby's
multi-table view possible.

Distinct from `BankrollRepository` — table state is the lobby's
*identity* (who's seated, how many chips on the table) and crosses
sessions. Bankroll persistence handles the AI's off-table chips.

Spec: `docs/plans/CASH_MODE_LOBBY_HANDOFF.md` §"Persistent table state".
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from cash_mode.tables import (
    CashTableState,
    IdlePoolEntry,
    seats_from_json,
    seats_to_json,
)
from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


def _parse_timestamp(value) -> Optional[datetime]:
    """Coerce a SQLite TIMESTAMP value to a datetime, or None.

    SQLite returns timestamps as strings under the default sqlite3
    type detection; legacy rows may also surface datetimes. The
    parsing is duck-typed (string → fromisoformat; datetime → passthrough).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # SQLite default formats: "YYYY-MM-DD HH:MM:SS[.ffffff]"
        # `datetime.fromisoformat` handles both that and ISO 8601.
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            try:
                # Some legacy rows omit microseconds; the fromisoformat
                # call above already handles those. This branch is just
                # defense against truly broken values.
                return None
            except Exception:
                return None
    return None


def _has_column(conn, table_name: str, column_name: str) -> bool:
    return any(row[1] == column_name for row in conn.execute(f"PRAGMA table_info({table_name})"))


def _prior_seated_stamps(seats_json: Optional[str]) -> dict:
    """Map ``personality_id -> seated_at`` from a persisted seats blob.

    Best-effort: a malformed or absent blob yields ``{}``. Only AI slots
    that already carry a ``seated_at`` contribute. Used by ``save_table``
    to carry an AI's sit-down timestamp forward across re-saves.
    """
    if not seats_json:
        return {}
    try:
        seats = json.loads(seats_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(seats, list):
        return {}
    stamps: dict = {}
    for slot in seats:
        if (
            isinstance(slot, dict)
            and slot.get("kind") == "ai"
            and slot.get("seated_at")
            and slot.get("personality_id")
        ):
            stamps[slot["personality_id"]] = slot["seated_at"]
    return stamps


def _stamp_seated_at(seats: List[dict], prior_stamps: dict, now_iso: str) -> List[dict]:
    """Return a copy of ``seats`` where every AI slot carries ``seated_at``.

    ``seated_at`` marks when that AI sat at THIS table. A pid already
    seated here (present in ``prior_stamps``, read from the table's
    previously-persisted seats) keeps its original stamp — so the clock
    survives per-hand chip updates and same-seat rebuys, and only the chip
    count changes. A pid not previously at this table is a fresh sit-down,
    stamped ``now``; the timer therefore resets naturally on a table
    change. Non-AI slots pass through untouched.
    """
    out: List[dict] = []
    for slot in seats:
        if isinstance(slot, dict) and slot.get("kind") == "ai" and slot.get("personality_id"):
            stamp = prior_stamps.get(slot["personality_id"]) or now_iso
            out.append({**slot, "seated_at": stamp})
        else:
            out.append(slot)
    return out


def _seated_presence_map(conn, sandbox_id: Optional[str]):
    """`{(table_id, seat_index): entity_id}` for SEATED `entity_presence` rows in
    the sandbox — the occupancy authority used to project the cached seat map.

    `entity_presence` is the permanent occupancy authority (the Presence cutover
    is complete). Returns None (→ no projection) only when no sandbox is given
    (cross-sandbox admin reads stay raw) or the table is absent (legacy schema).
    """
    if not sandbox_id:
        return None
    try:
        rows = conn.execute(
            "SELECT entity_id, table_id, seat_index FROM entity_presence "
            "WHERE sandbox_id = ? AND state = 'seated'",
            (sandbox_id,),
        ).fetchall()
    except Exception:
        return None
    return {(r["table_id"], r["seat_index"]): r["entity_id"] for r in rows}


def _project_table_occupancy(state: CashTableState, presence_map):
    """Render any `ai`/`human` slot NOT confirmed SEATED by presence as `open`
    (occupancy-authority / payload-cache — the D1 read-side projection).

    A stale cache slot (left by a deleted game row / persona, before the
    cascade reaches it) becomes structurally invisible to every occupancy read,
    which is what lets the ghost-seat / zombie-seat reconcilers retire. `open`
    and `reserved` (a pre-sit hold, never a presence SEATED row) pass through.
    Read-only: writes diff against the RAW stored seats, so a write that re-saves
    a projected table simply persists the (correct) opened ghost — self-healing.
    """
    if presence_map is None:
        return state
    from dataclasses import replace

    from cash_mode.presence import ai_entity_id, player_entity_id
    from cash_mode.tables import open_slot

    new_seats: List[dict] = []
    changed = False
    for idx, slot in enumerate(state.seats):
        kind = slot.get("kind") if isinstance(slot, dict) else None
        if kind == "ai":
            pid = slot.get("personality_id")
            eid = ai_entity_id(pid) if pid else None
        elif kind == "human":
            owner = (
                slot.get("owner_id")
                or slot.get("player_id")
                or slot.get("user_id")
                or slot.get("personality_id")
            )
            eid = player_entity_id(owner) if owner else None
        else:
            new_seats.append(slot)
            continue
        if eid is not None and presence_map.get((state.table_id, idx)) == eid:
            new_seats.append(slot)  # presence-confirmed → keep payload
        else:
            new_seats.append(open_slot())
            changed = True
    return replace(state, seats=new_seats) if changed else state


class CashTableRepository(BaseRepository):
    """CRUD for `cash_tables`.

    Two reads (`load_table`, `list_all_tables`) and one write
    (`save_table`). No delete in v1.5 — tables are seeded once and
    never removed.

    Like other repositories, the schema is created by
    `SchemaManager.ensure_schema()`; this class only touches data.
    """

    def save_table(
        self,
        state: CashTableState,
        *,
        sandbox_id: Optional[str] = None,
        now: Optional[datetime] = None,
        idle_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Upsert a cash table row.

        Bumps `last_activity_at` to `now` (default `datetime.utcnow()`)
        on every write — the refresh hook calls save_table after any
        movement decision so admin views can sort tables by recent
        activity.

        `created_at` is preserved on re-saves: if `state.created_at` is
        non-None we honor it, else SQL DEFAULT applies on first insert
        and existing rows keep their original timestamp via the COALESCE.
        """
        if now is None:
            now = datetime.utcnow()
        created_iso = state.created_at.isoformat() if state.created_at else None
        with self._get_connection() as conn:
            scoped = _has_column(conn, "cash_tables", "sandbox_id")
            named = _has_column(conn, "cash_tables", "name")
            typed = _has_column(conn, "cash_tables", "table_type")
            has_closing = _has_column(conn, "cash_tables", "closing_hand_countdown")
            if scoped and not sandbox_id:
                raise ValueError("sandbox_id is required for cash_tables writes")
            # Preserve created_at on upsert if a row exists; otherwise use
            # the provided value or fall back to SQL DEFAULT.
            if scoped:
                existing = conn.execute(
                    """
                    SELECT created_at, seats_json FROM cash_tables
                    WHERE table_id = ? AND sandbox_id = ?
                    """,
                    (state.table_id, sandbox_id),
                ).fetchone()
            else:
                existing = conn.execute(
                    "SELECT created_at, seats_json FROM cash_tables WHERE table_id = ?",
                    (state.table_id,),
                ).fetchone()
            # Stamp each AI seat with when it sat at THIS table. Carrying
            # forward the prior row's stamps preserves the clock across
            # per-hand chip updates and rebuys; a pid new to the table is
            # stamped `now` (so the timer resets on a table change). Lives
            # in the seats_json blob — no schema column.
            prior_stamps = _prior_seated_stamps(existing["seats_json"] if existing else None)
            seats_blob = seats_to_json(_stamp_seated_at(state.seats, prior_stamps, now.isoformat()))
            # Build column-list dynamically so this repo stays compatible
            # with pre-v111 schemas (used by some legacy fixtures).
            extra_set_cols = []
            extra_set_vals: list = []
            extra_ins_cols = []
            extra_ins_vals: list = []
            if named:
                extra_set_cols.append("name = ?")
                extra_set_vals.append(state.name)
                extra_ins_cols.append("name")
                extra_ins_vals.append(state.name)
            if typed:
                extra_set_cols.append("table_type = ?")
                extra_set_vals.append(state.table_type)
                extra_ins_cols.append("table_type")
                extra_ins_vals.append(state.table_type)
            if has_closing:
                # Round-trip the closing countdown verbatim. NULL means
                # "active" (lobby tables and active casinos); integer
                # means "closing with N hands remaining."
                extra_set_cols.append("closing_hand_countdown = ?")
                extra_set_vals.append(state.closing_hand_countdown)
                extra_ins_cols.append("closing_hand_countdown")
                extra_ins_vals.append(state.closing_hand_countdown)
            extra_set_clause = (", " + ", ".join(extra_set_cols)) if extra_set_cols else ""
            extra_ins_col_clause = (", " + ", ".join(extra_ins_cols)) if extra_ins_cols else ""
            extra_ins_qmark_clause = (
                (", " + ", ".join("?" * len(extra_ins_cols))) if extra_ins_cols else ""
            )

            if existing:
                if scoped:
                    conn.execute(
                        f"""
                        UPDATE cash_tables
                        SET stake_label = ?, seats_json = ?, dealer_idx = ?,
                            last_activity_at = ?{extra_set_clause}
                        WHERE table_id = ? AND sandbox_id = ?
                        """,
                        (
                            state.stake_label,
                            seats_blob,
                            int(state.dealer_idx),
                            now.isoformat(),
                            *extra_set_vals,
                            state.table_id,
                            sandbox_id,
                        ),
                    )
                else:
                    conn.execute(
                        f"""
                        UPDATE cash_tables
                        SET stake_label = ?, seats_json = ?, dealer_idx = ?,
                            last_activity_at = ?{extra_set_clause}
                        WHERE table_id = ?
                        """,
                        (
                            state.stake_label,
                            seats_blob,
                            int(state.dealer_idx),
                            now.isoformat(),
                            *extra_set_vals,
                            state.table_id,
                        ),
                    )
            else:
                if created_iso is None:
                    if scoped:
                        conn.execute(
                            f"""
                            INSERT INTO cash_tables
                                (table_id, sandbox_id, stake_label, seats_json,
                                 dealer_idx, last_activity_at{extra_ins_col_clause})
                            VALUES (?, ?, ?, ?, ?, ?{extra_ins_qmark_clause})
                            """,
                            (
                                state.table_id,
                                sandbox_id,
                                state.stake_label,
                                seats_blob,
                                int(state.dealer_idx),
                                now.isoformat(),
                                *extra_ins_vals,
                            ),
                        )
                    else:
                        conn.execute(
                            f"""
                            INSERT INTO cash_tables
                                (table_id, stake_label, seats_json, dealer_idx,
                                 last_activity_at{extra_ins_col_clause})
                            VALUES (?, ?, ?, ?, ?{extra_ins_qmark_clause})
                            """,
                            (
                                state.table_id,
                                state.stake_label,
                                seats_blob,
                                int(state.dealer_idx),
                                now.isoformat(),
                                *extra_ins_vals,
                            ),
                        )
                else:
                    if scoped:
                        conn.execute(
                            f"""
                            INSERT INTO cash_tables
                                (table_id, sandbox_id, stake_label, seats_json,
                                 dealer_idx, created_at, last_activity_at{extra_ins_col_clause})
                            VALUES (?, ?, ?, ?, ?, ?, ?{extra_ins_qmark_clause})
                            """,
                            (
                                state.table_id,
                                sandbox_id,
                                state.stake_label,
                                seats_blob,
                                int(state.dealer_idx),
                                created_iso,
                                now.isoformat(),
                                *extra_ins_vals,
                            ),
                        )
                    else:
                        conn.execute(
                            f"""
                            INSERT INTO cash_tables
                                (table_id, stake_label, seats_json, dealer_idx,
                                 created_at, last_activity_at{extra_ins_col_clause})
                            VALUES (?, ?, ?, ?, ?, ?{extra_ins_qmark_clause})
                            """,
                            (
                                state.table_id,
                                state.stake_label,
                                seats_blob,
                                int(state.dealer_idx),
                                created_iso,
                                now.isoformat(),
                                *extra_ins_vals,
                            ),
                        )

            # Presence is the authoritative record of actor location: drive
            # `entity_presence` from this seat write, INSIDE this same
            # transaction so presence + the cash_tables seat map commit together
            # (no cross-connection desync). A double-seat IntegrityError
            # propagates and rolls back this whole save_table, rejecting the bad
            # write. This chokepoint also enforces the seated⇒not-idle invariant
            # structurally (a SIT clears the actor's IDLE presence + metadata),
            # which used to require a separate `cash_idle_pool` clear here. See
            # cash_mode/presence_transitions.py.
            from cash_mode.presence_transitions import emit_presence_transitions_for_save

            emit_presence_transitions_for_save(
                conn,
                sandbox_id,
                existing["seats_json"] if existing else None,
                state,
                now.isoformat(),
                idle_metadata=idle_metadata,
            )

    def load_table(
        self,
        table_id: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> Optional[CashTableState]:
        """Load a single cash table by id, or None if it doesn't exist."""
        with self._get_connection() as conn:
            if _has_column(conn, "cash_tables", "sandbox_id"):
                if not sandbox_id:
                    raise ValueError("sandbox_id is required for cash_tables reads")
                row = conn.execute(
                    """
                    SELECT *
                    FROM cash_tables
                    WHERE table_id = ? AND sandbox_id = ?
                    """,
                    (table_id, sandbox_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT *
                    FROM cash_tables
                    WHERE table_id = ?
                    """,
                    (table_id,),
                ).fetchone()
            if not row:
                return None
            state = _row_to_state(row)
            # D1 read-side projection: occupancy from presence, payload from cache.
            return _project_table_occupancy(state, _seated_presence_map(conn, sandbox_id))

    def set_closing_countdown(
        self,
        table_id: str,
        *,
        sandbox_id: Optional[str] = None,
        countdown: Optional[int],
    ) -> bool:
        """Set the `closing_hand_countdown` column for a casino table.

        `countdown=None` resets to NULL (active). `countdown=int` puts
        the table in 'closing' state with N hands remaining (smooth
        shutdown from `casino_provisioning`).

        Returns True if a row was updated. Returns False (and logs at
        debug) if the column isn't present (pre-v113 schema) so legacy
        fixtures don't error — the closing state just doesn't persist.
        """
        with self._get_connection() as conn:
            if not _has_column(conn, "cash_tables", "closing_hand_countdown"):
                logger.debug(
                    "cash_tables has no closing_hand_countdown column "
                    "(pre-v113 schema); set_closing_countdown is a no-op"
                )
                return False
            scoped = _has_column(conn, "cash_tables", "sandbox_id")
            if scoped:
                if not sandbox_id:
                    raise ValueError("sandbox_id is required for cash_tables writes")
                cursor = conn.execute(
                    "UPDATE cash_tables SET closing_hand_countdown = ? "
                    "WHERE table_id = ? AND sandbox_id = ?",
                    (countdown, table_id, sandbox_id),
                )
            else:
                cursor = conn.execute(
                    "UPDATE cash_tables SET closing_hand_countdown = ? " "WHERE table_id = ?",
                    (countdown, table_id),
                )
            return cursor.rowcount > 0

    def get_closing_countdown(
        self,
        table_id: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> Optional[int]:
        """Read the `closing_hand_countdown` column. None when active,
        when the row doesn't exist, or when the column isn't present."""
        with self._get_connection() as conn:
            if not _has_column(conn, "cash_tables", "closing_hand_countdown"):
                return None
            scoped = _has_column(conn, "cash_tables", "sandbox_id")
            if scoped:
                if not sandbox_id:
                    return None
                row = conn.execute(
                    "SELECT closing_hand_countdown FROM cash_tables "
                    "WHERE table_id = ? AND sandbox_id = ?",
                    (table_id, sandbox_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT closing_hand_countdown FROM cash_tables " "WHERE table_id = ?",
                    (table_id,),
                ).fetchone()
            if not row:
                return None
            raw = row["closing_hand_countdown"]
            return int(raw) if raw is not None else None

    def delete_table(
        self,
        table_id: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> bool:
        """Delete a cash table row. Returns True if a row was removed.

        Used by ephemeral table types (currently `casino` — see
        `cash_mode/casino_provisioning.py`) that spawn and tear down
        based on bank-pool depth. Lobby tables (`table_type='lobby'`)
        aren't deleted in normal flows — they're seeded once and
        persist.

        No-op (returns False) when the row doesn't exist. Callers
        treat that as success in idempotent flows.
        """
        with self._get_connection() as conn:
            scoped = _has_column(conn, "cash_tables", "sandbox_id")
            if scoped:
                if not sandbox_id:
                    raise ValueError("sandbox_id is required for cash_tables deletes")
                cursor = conn.execute(
                    "DELETE FROM cash_tables WHERE table_id = ? AND sandbox_id = ?",
                    (table_id, sandbox_id),
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM cash_tables WHERE table_id = ?",
                    (table_id,),
                )
            return cursor.rowcount > 0

    def list_all_tables(
        self,
        *,
        sandbox_id: Optional[str] = None,
    ) -> List[CashTableState]:
        """Return every persisted cash table, ordered by stake (ascending).

        Ordering keys off `cash_mode.stakes_ladder.STAKES_ORDER` — the single
        source of truth for the stakes ladder. Adding a new stake (or
        reordering) only requires editing that list; this repo picks
        up the change with no edits here. Tables whose stake_label
        isn't in STAKES_ORDER (shouldn't happen — pinned by schema —
        but defensive) land at the end in insertion order.

        Secondary sort by `table_id` keeps order deterministic when
        multiple tables share a stake (v2 / Path C territory; v1.5
        invariant is one table per stake).
        """
        from cash_mode.stakes_ladder import STAKES_ORDER

        with self._get_connection() as conn:
            if sandbox_id is None:
                # Admin / audit cross-sandbox aggregation.
                rows = conn.execute(
                    """
                    SELECT *
                    FROM cash_tables
                    """,
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM cash_tables
                    WHERE sandbox_id = ?
                    """,
                    (sandbox_id,),
                ).fetchall()
            # D1 read-side projection (one presence query for all tables).
            presence_map = _seated_presence_map(conn, sandbox_id)

        # Python-side stable sort against STAKES_ORDER. SQL CASE would
        # hardcode the ladder a second time; this version stays in
        # lockstep with the canonical list.
        rank = {label: i for i, label in enumerate(STAKES_ORDER)}
        unknown = len(STAKES_ORDER)
        states = [_project_table_occupancy(_row_to_state(r), presence_map) for r in rows]
        states.sort(key=lambda s: (rank.get(s.stake_label, unknown), s.table_id))
        return states

    # --- Idle pool ---

    def list_idle(
        self,
        *,
        sandbox_id: Optional[str] = None,
    ) -> List[IdlePoolEntry]:
        """Return the genuinely-idle AIs, ordered by `left_at` ASC (oldest first,
        so the re-entry tick naturally walks the longest-waiting AI back up).

        The idle set is derived from `entity_presence` (state='idle') — the
        permanent occupancy authority — joined with the `cash_idle_metadata`
        satellite for the reason/target_stake routing payload. Deriving from
        presence yields exactly the available-to-seat set: an AI off-grid on a
        hustle is SIDE_HUSTLE in presence (correctly excluded), not idle.
        `sandbox_id=None` is the cross-sandbox admin read (every sandbox).
        """
        return self._list_idle_from_presence(sandbox_id)

    def _list_idle_from_presence(self, sandbox_id: Optional[str]) -> List[IdlePoolEntry]:
        """Derive the idle pool from `entity_presence` (the authority) joined
        with the `cash_idle_metadata` satellite for reason/target_stake/left_at.
        Only `ai:` entities; ordered oldest-idle-first. `sandbox_id=None` spans
        every sandbox (admin)."""
        where = "p.state = 'idle' AND p.entity_id LIKE 'ai:%'"
        params: tuple = ()
        if sandbox_id is not None:
            where = "p.sandbox_id = ? AND " + where
            params = (sandbox_id,)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT REPLACE(p.entity_id, 'ai:', '') AS personality_id,
                       COALESCE(m.left_at, p.updated_at) AS left_at,
                       COALESCE(m.reason, 'forced_leave') AS reason,
                       m.target_stake AS target_stake
                FROM entity_presence p
                LEFT JOIN cash_idle_metadata m
                    ON m.personality_id = REPLACE(p.entity_id, 'ai:', '')
                    AND m.sandbox_id = p.sandbox_id
                WHERE {where}
                ORDER BY left_at ASC
                """,
                params,
            ).fetchall()
        out: List[IdlePoolEntry] = []
        for r in rows:
            la = _parse_timestamp(r["left_at"])
            if la is None:
                # Defensive: skip a malformed timestamp rather than raise — a
                # bad metadata row must not break the whole re-seat tick.
                continue
            out.append(
                IdlePoolEntry(
                    personality_id=r["personality_id"],
                    left_at=la,
                    reason=r["reason"],
                    target_stake=r["target_stake"],
                )
            )
        return out

    def delete_idle(
        self,
        personality_id: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> bool:
        """Mark an AI as no longer idle: clear its IDLE `entity_presence` row and
        its `cash_idle_metadata` satellite. Returns True if an IDLE row was cleared.

        Called when an AI leaves the idle pool WITHOUT a re-seat (a re-seat
        already moved it to SEATED via the save_table chokepoint, which clears the
        IDLE row + metadata there). Only touches a row presence still shows as
        IDLE, so it can't stomp an AI a concurrent save just seated.
        """
        if not sandbox_id:
            raise ValueError("sandbox_id is required for idle deletes")
        cleared = False
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT state FROM entity_presence WHERE entity_id = ? AND sandbox_id = ?",
                (f"ai:{personality_id}", sandbox_id),
            ).fetchone()
            if row is not None and row["state"] == "idle":
                # Left the pool without a re-seat → OFFLINE (row delete).
                conn.execute(
                    "DELETE FROM entity_presence WHERE entity_id = ? AND sandbox_id = ?",
                    (f"ai:{personality_id}", sandbox_id),
                )
                cleared = True
            conn.execute(
                "DELETE FROM cash_idle_metadata WHERE personality_id = ? AND sandbox_id = ?",
                (personality_id, sandbox_id),
            )
        return cleared


def _row_to_state(row) -> CashTableState:
    """Build a `CashTableState` from a `cash_tables` row."""
    seats = seats_from_json(row["seats_json"])
    # `dealer_idx` was added in schema v96. The migration has DEFAULT 0
    # so existing rows backfill cleanly; this guard handles any path
    # where the row predates the migration in tests or partial restores.
    try:
        dealer_idx = int(row["dealer_idx"] or 0)
    except (KeyError, IndexError):
        dealer_idx = 0
    # v111 columns. sqlite3.Row raises IndexError for missing columns
    # under SELECT *; treat absence as the default value.
    try:
        name = row["name"]
    except (KeyError, IndexError):
        name = None
    try:
        table_type = row["table_type"] or 'lobby'
    except (KeyError, IndexError):
        table_type = 'lobby'
    # v113 column. Same KeyError/IndexError treatment for older schemas.
    try:
        raw = row["closing_hand_countdown"]
        closing_hand_countdown = int(raw) if raw is not None else None
    except (KeyError, IndexError):
        closing_hand_countdown = None
    return CashTableState(
        table_id=row["table_id"],
        stake_label=row["stake_label"],
        seats=seats,
        created_at=_parse_timestamp(row["created_at"]),
        last_activity_at=_parse_timestamp(row["last_activity_at"]),
        dealer_idx=dealer_idx,
        name=name,
        table_type=table_type,
        closing_hand_countdown=closing_hand_countdown,
    )
