"""Tourist avatar registry — map ephemeral tourists to existing
personality avatars without triggering zombie creation.

Background
----------
Going through `get_avatar_url_with_fallback(display_name, ...)` for
tourist seats would hit `personality_generator.get_personality(name)`,
which auto-creates a DB personality row when the name isn't found.
Those zombies become eligible for live-fill at any cash stake — the
exact leak that put "Brad" / "Karen" / "Lauren" at $200 tables.

This module solves that by mapping each tourist to an EXISTING
personality whose avatar we can serve. Two resolution layers:

  1. **Direct name match** — when the tourist's first name overlaps
     with an existing JSON personality (Greg → Vacation Greg, Brenda
     → Bachelorette Brenda, etc.), serve that personality's avatar.
     Most evocative because the costume + name align.

  2. **Template fallback** — when no direct match, fall back to the
     template's "anchor" personality (the JSON fish the template was
     modeled after). All non-Greg vacation_dads (Dave, Doug, Rick…)
     share Vacation Greg's avatar.

When neither resolves, returns None and the frontend renders the
initial-letter circle (`lobby-table-card__seat-initial`).

Extending the registry
----------------------
The intended growth path is a batch-generation script that produces
30+ unique tourist portraits via the existing `character_images`
pipeline. Each generated image gets stored under a controlled
personality id (`_tourist_<template>_<name>`) so it's reachable via
the standard `/api/avatar/<name>/<emotion>` endpoint. Wire new
mappings into `NAME_LEVEL` (or the per-template list) and they're
served immediately. The registry intentionally has room for 80+
entries (8 templates × ~10 names each).

For now, only the 4 anchor matches are hard-wired. The other 4
templates (finance_bro, superstitious_grandma, slot_refugee,
golf_trip_dude) have no JSON counterpart, so their tourists fall
back to the letter circle until a batch-gen run adds avatars.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# --- Registry --------------------------------------------------------

# (template_key, first_name) → personality_id whose avatar to serve.
# Pre-populated with the 4 anchor matches: tourists whose first name
# matches an existing JSON fish personality's first name get their
# avatar verbatim. Extend by appending — runtime lookup is O(1).
NAME_LEVEL: Dict[Tuple[str, str], str] = {
    ("vacation_dad",          "Greg"):   "vacation_greg",
    ("bachelorette",          "Brenda"): "bachelorette_brenda",
    ("retired_know_it_all",   "Carl"):   "cruise_carl",
    ("birthday_kid",          "Bobby"):  "birthday_bobby",
    # --- batch-gen avatars register here over time ---
}

# Per-template fallback: when no NAME_LEVEL match exists, share the
# template's anchor avatar with every tourist of that template. None
# means "no template anchor" — falls through to letter circle.
TEMPLATE_FALLBACK: Dict[str, Optional[str]] = {
    "vacation_dad":          "vacation_greg",
    "bachelorette":          "bachelorette_brenda",
    "retired_know_it_all":   "cruise_carl",
    "birthday_kid":          "birthday_bobby",
    # No JSON counterpart yet — letter fallback until batch-gen lands.
    "finance_bro":           None,
    "superstitious_grandma": None,
    "slot_refugee":          None,
    "golf_trip_dude":        None,
}


# --- Resolver --------------------------------------------------------


def resolve_tourist_avatar_personality_id(
    template_key: str,
    first_name: str,
) -> Optional[str]:
    """Return the personality_id whose avatar to serve for this tourist.

    Lookup order:
      1. (template_key, first_name) direct match in `NAME_LEVEL`.
      2. `TEMPLATE_FALLBACK[template_key]` — template's anchor.
      3. None → frontend renders the initial-letter circle.

    Tourist seats should call this BEFORE
    `get_avatar_url_with_fallback`; if it returns non-None, look up the
    personality's display name and pass THAT to the fallback (which is
    safe because the personality exists in the DB — no zombie creation).
    """
    by_name = NAME_LEVEL.get((template_key, first_name))
    if by_name:
        return by_name
    return TEMPLATE_FALLBACK.get(template_key)


def register_tourist_avatar(
    template_key: str,
    first_name: str,
    personality_id: str,
) -> None:
    """Wire a new (template, name) → personality avatar mapping.

    Called by batch-generation scripts after they've populated the
    `character_images` table with new tourist portraits. The
    personality_id must exist in the `personalities` table — otherwise
    `get_avatar_url_with_fallback` will fall back to its own zombie-
    creating path.
    """
    NAME_LEVEL[(template_key, first_name)] = personality_id
    logger.info(
        "[TOURIST_AVATARS] registered (%r, %r) → %r",
        template_key, first_name, personality_id,
    )
