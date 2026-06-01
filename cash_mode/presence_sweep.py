"""Deletion-time presence/seat sweeps (read-side completion R3).

The reconcilers `_free_ghost_human_seats` / `_reclaim_zombie_casino_seats` exist
because a game-row or persona delete can leave a seat behind that presence never
saw freed. These sweeps close that at the SOURCE: on the delete, open the entity's
seat via `save_table` (which under `PRESENCE_AUTHORITY_ENABLED` atomically drives
the GO_OFFLINE / RETURN_TO_POOL transition), making the orphan unrepresentable
instead of swept later. That's what lets the reconcilers retire (R4).

Both are best-effort + gated on authority (a sweep failure must never block the
delete). They take the `repos` dict so they work from any layer without importing
flask extensions. The CHIP halves of these deletes already shipped (Phase-3 reaper
settle for games, Phase-5 `settle_ai_bankroll_to_pool_on_delete` for personas);
these are the PRESENCE/occupancy halves that compose beside them.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _authority_on() -> bool:
    from cash_mode import economy_flags

    return economy_flags.PRESENCE_AUTHORITY_ENABLED


def _open_seat(cash_table_repo, *, sandbox_id, table_id, seat_index, expect_id_field, expect_value):
    """Open one cash_tables slot via save_table (drives the presence transition
    under authority). Only opens if the slot still holds the expected occupant —
    avoids clobbering a seat that churned since the presence row was written.
    Returns True if a seat was opened."""
    from cash_mode.tables import open_slot

    table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
    if table is None or seat_index is None or seat_index >= len(table.seats):
        return False
    slot = table.seats[seat_index]
    if slot.get(expect_id_field) != expect_value:
        return False  # seat already changed hands; nothing to free here
    cash_table_repo.save_table(
        table.with_seat(seat_index, open_slot()), sandbox_id=sandbox_id
    )
    return True


def free_human_seat_on_delete(*, owner_id: str, sandbox_id: str, repos: Dict[str, Any]) -> int:
    """Open a human's persisted cash seat when their game row is deleted (R3a).

    Mirrors `_free_ghost_human_seats` but fires AT the deletion (reaper / purge)
    so the ghost seat never persists. Under authority `save_table` also drives the
    `GO_OFFLINE` presence transition, clearing the stale presence row. Best-effort;
    requires `sandbox_id` (callers have it). Returns the count of seats freed.
    """
    if not _authority_on() or not owner_id or not sandbox_id:
        return 0
    cash_table_repo = repos.get("cash_table_repo")
    if cash_table_repo is None:
        return 0
    freed = 0
    try:
        tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)
    except Exception as e:
        logger.warning("[CASH][SWEEP] free_human_seat list_all_tables failed: %s", e)
        return 0
    for table in tables:
        for idx, slot in enumerate(table.seats):
            if slot.get("kind") in ("human", "reserved") and slot.get("personality_id") == owner_id:
                try:
                    cash_table_repo.save_table(
                        table.with_seat(idx, _open_slot()), sandbox_id=sandbox_id
                    )
                    freed += 1
                    logger.info(
                        "[CASH][SWEEP] freed human seat on delete: table=%r seat=%d owner=%r",
                        table.table_id, idx, owner_id,
                    )
                except Exception as e:
                    logger.warning(
                        "[CASH][SWEEP] free_human_seat save_table failed %r:%d: %s",
                        table.table_id, idx, e,
                    )
    return freed


def sweep_presence_on_persona_delete(*, personality_id: str, repos: Dict[str, Any]) -> int:
    """Open an AI's casino seat(s) — every sandbox — when its persona is deleted
    (R3b), so deletion can't leave a zombie seat. Composes beside the Phase-5
    bankroll-to-pool settle. Uses presence SEATED rows (reliable for seated) to
    find the seats; opens via `save_table` (drives RETURN_TO_POOL/GO_OFFLINE), or
    clears a stale presence row directly if the cache seat already moved on.
    Best-effort + gated. Returns the count swept.
    """
    if not _authority_on() or not personality_id:
        return 0
    from cash_mode.presence import PresenceEvent, ai_entity_id

    presence_repo = repos.get("entity_presence_repo")
    cash_table_repo = repos.get("cash_table_repo")
    if presence_repo is None or cash_table_repo is None:
        return 0
    eid = ai_entity_id(personality_id)
    swept = 0
    try:
        seated = presence_repo.seated_rows_for_entity(eid)
    except Exception as e:
        logger.warning("[CASH][SWEEP] persona-delete seated lookup failed for %r: %s",
                       personality_id, e)
        return 0
    for st in seated:
        try:
            opened = _open_seat(
                cash_table_repo, sandbox_id=st.sandbox_id, table_id=st.table_id,
                seat_index=st.seat_index, expect_id_field="personality_id",
                expect_value=personality_id,
            )
            if not opened:
                # Cache seat already moved on; just clear the stale presence row.
                presence_repo.persist_transition(eid, st.sandbox_id, PresenceEvent.GO_OFFLINE)
            swept += 1
            logger.info(
                "[CASH][SWEEP] freed AI seat on persona delete: pid=%r sandbox=%r seat=%s",
                personality_id, st.sandbox_id, st.seat_index,
            )
        except Exception as e:
            logger.warning("[CASH][SWEEP] persona-delete sweep failed for %r: %s",
                           personality_id, e)
    return swept


def _open_slot():
    from cash_mode.tables import open_slot

    return open_slot()
