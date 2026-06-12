"""Shared shadow-write helper for the Presence-machine cutover (Phase 1).

The dual-write shadow phase (CASH_MODE_PRESENCE_MIGRATION.md §Sequencing
step 1) records every cash-mode presence change into the dormant
`entity_presence` table ALONGSIDE the existing authoritative writers
(`cash_tables` seat map, `cash_idle_pool`, `ai_side_hustle_state`,
`ai_vice_state`), so we can prove the machine tracks reality on live
traffic before flipping authority to it.

Every reroute call site funnels through `shadow_transition()` here rather
than touching `EntityPresenceRepository` directly, so the two safety
properties live in ONE place instead of being re-implemented (and
mis-implemented) at ~30 sites:

  1. **Gated.** No-op unless `economy_flags.PRESENCE_AUTHORITY_ENABLED`
     (permanent post-cutover; the old `PRESENCE_SHADOW_WRITE_ENABLED` kill
     switch was removed once authority became the sole driver).
  2. **Non-fatal.** The whole body is wrapped in try/except. A shadow-write
     failure (illegal transition, DB error, missing repo) is logged and
     swallowed — it must NEVER break the real seat write it shadows. During
     the shadow phase `cash_tables` et al. remain authoritative, so a missed
     shadow row is a divergence to investigate, not a chip bug.

The caller still owns atomicity: per design §6.1 the real write + this
shadow write should both happen inside the caller's
`get_sandbox_lock(sandbox_id)` critical section. This helper does not
acquire locks.

Usage (at a reroute site, AFTER the authoritative write):

    from cash_mode import presence_shadow
    from cash_mode.presence import PresenceEvent, player_entity_id

    presence_shadow.shadow_transition(
        entity_id=player_entity_id(owner_id),
        sandbox_id=sandbox_id,
        event=PresenceEvent.SIT,
        table_id=table_id,
        seat_index=seat_index,
    )

Once the flip lands, call sites switch from `shadow_transition` (best-effort
mirror) to authoritative `persist_transition` and the gate/try-except come
off. Keeping every site behind this one function makes that flip a
mechanical search-replace rather than 30 hand-edits.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """Whether presence writes (off-grid + the legacy call-site seat reconciles)
    are active. Gated on `PRESENCE_AUTHORITY_ENABLED` — read live so a runtime
    flip (or a test monkeypatch) takes effect without re-import.

    Authority drives this so the off-grid (side-hustle / vice) transitions keep
    mirroring after the seat machine flipped to authoritative. The seat machine
    itself is driven authoritatively by
    `presence_transitions.emit_presence_transitions_for_save` at the `save_table`
    chokepoint; the call-site `_shadow_reconcile_table` reconciles become harmless
    redundant no-ops once the chokepoint has already written presence (they read
    the now-correct state and skip)."""
    from cash_mode import economy_flags

    return bool(getattr(economy_flags, "PRESENCE_AUTHORITY_ENABLED", False))


def shadow_transition(
    *,
    entity_id: str,
    sandbox_id: str,
    event,  # cash_mode.presence.PresenceEvent
    table_id: Optional[str] = None,
    seat_index: Optional[int] = None,
    repo=None,
    updated_at: Optional[str] = None,
) -> None:
    """Best-effort mirror of one presence transition into `entity_presence`.

    No-op unless the kill switch is on. Never raises — any failure is logged
    at WARNING and swallowed so the authoritative write it shadows is never
    disturbed.

    `repo` defaults to `flask_app.extensions.entity_presence_repo`; callers in
    a sim / test context pass an explicit repo (the extensions singleton is
    None outside the Flask app).
    """
    if not is_enabled():
        return
    try:
        if repo is None:
            from flask_app import extensions

            repo = getattr(extensions, "entity_presence_repo", None)
        if repo is None:
            logger.debug("[PRESENCE-SHADOW] no entity_presence_repo; skipping")
            return
        repo.persist_transition(
            entity_id,
            sandbox_id,
            event,
            table_id=table_id,
            seat_index=seat_index,
            updated_at=updated_at,
        )
    except Exception as e:  # noqa: BLE001 — shadow must never break the real path
        logger.warning(
            "[PRESENCE-SHADOW] transition mirror failed (entity=%s sandbox=%s "
            "event=%s): %s — real write unaffected",
            entity_id,
            sandbox_id,
            getattr(event, "value", event),
            e,
        )
