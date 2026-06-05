"""Canonical persona-identity resolution for tournaments.

Tournaments key their field / session / eliminations on the stable
`personality_id` — the persona's *economic* identity — exactly as a cash-mode
seat stores `personality_id` (`cash_mode.tables.ai_slot`). This module is the
SINGLE place that turns one of those field ids into a human-readable display
name: the tournament analogue of cash's
`cash_mode.tables.personality_for_seat` → `PersonalityRepository.load_personality_by_id`.

Before this existed, each tournament surface resolved identity its own way — the
live-table builder and relocation reconcile had a copy of the lookup with
different `owner_name` fallbacks, the world-event ticker rendered the *archetype*
string as the winner's "name", and the completion standings emitted the raw
`personality_id` slug. Routing every surface through here gives one resolution
path and one fallback, so a persona shows the same name on the felt, the ticker,
and the final standings.

The `personality_repo` is duck-typed and injected (only `load_personality_by_id`
/ `display_names_by_ids` are used), so this stays Flask-free and unit-testable
with the field/session types it serves.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


def humanize_id(player_id: str) -> str:
    """Best-effort humanization of a bare field id when no persona is found.

    Turns a slug (`sun_tzu` → "Sun Tzu") or a synthetic seat id (`P07` → "P07")
    into something presentable. For an id that is already a display name (the
    single-table session keys `entries` by real name) this is a no-op.
    """
    return str(player_id).replace('_', ' ').title()


def resolve_display_name(
    player_id: str,
    *,
    is_human: bool = False,
    owner_name: Optional[str] = None,
    personality_repo=None,
    humanize_fallback: bool = True,
) -> str:
    """The human-readable name for one tournament field id.

    Mirrors the cash seat-resolution path: the human seat shows the owner's name
    (the frontend still renders the human's *own* seat as "You"); an AI seat
    resolves to its persona's real name via `load_personality_by_id`. Display is
    best-effort — repo failures never propagate.

    `humanize_fallback` controls the no-persona case. On the live felt (default
    True) an unresolved slug is humanized (`sun_tzu` → "Sun Tzu") for a friendlier
    label. The completion/standings path passes False so an unresolved id is left
    VERBATIM: a single-table session keys its field on the player's real display
    name (which misses a by-`personality_id` lookup), and `.title()`-humanizing it
    would mangle names like "McQueen" → "Mcqueen"; a synthetic `P07` seat is
    already legible as-is.
    """
    fallback = humanize_id if humanize_fallback else str
    if is_human:
        return owner_name or fallback(player_id)
    if personality_repo is not None:
        try:
            persona = personality_repo.load_personality_by_id(player_id)
            if persona and persona.get('name'):
                return persona['name']
        except Exception:  # noqa: BLE001 — display is best-effort; fall back below
            logger.debug("persona name lookup failed for %s", player_id, exc_info=True)
    return fallback(player_id)


def resolve_display_names(
    player_ids: Iterable[str],
    *,
    human_id: Optional[str] = None,
    owner_name: Optional[str] = None,
    personality_repo=None,
) -> dict[str, str]:
    """Batch `resolve_display_name` for a set of field ids (e.g. standings).

    Uses the repo's side-effect-free `display_names_by_ids` bulk lookup when
    available (one query for the whole field), then layers the human seat and the
    humanize fallback on top. Falls back to per-id resolution if the repo doesn't
    expose the bulk method.
    """
    ids = [pid for pid in dict.fromkeys(player_ids) if pid]
    names: dict[str, str] = {}

    bulk: dict[str, str] = {}
    if personality_repo is not None and hasattr(personality_repo, 'display_names_by_ids'):
        try:
            ai_ids = [pid for pid in ids if pid != human_id]
            bulk = personality_repo.display_names_by_ids(ai_ids) or {}
        except Exception:  # noqa: BLE001 — bulk display is best-effort
            logger.debug("bulk persona name lookup failed", exc_info=True)
            bulk = {}

    for pid in ids:
        if human_id is not None and pid == human_id:
            names[pid] = owner_name or humanize_id(pid)
        elif pid in bulk:
            names[pid] = bulk[pid]
        else:
            names[pid] = resolve_display_name(
                pid, is_human=False, personality_repo=personality_repo
            )
    return names
