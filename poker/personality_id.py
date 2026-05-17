"""Stable identifier scheme for personalities.

Personality identity needs to persist across game sessions for the
relationship layer (heat / respect / likability axes) and cash mode
(per-personality AI bankrolls) to work. Display names are human-facing,
editable, and can collide — they're a brittle persistence key.

This module is the single source of truth for the slug-based ID scheme.
Both the DB schema migration (v85) and the JSON seed-source backfill
script import from here so the IDs stay aligned across the two paths.

Rules:
  - The id is derived from the display name at first assignment.
  - Once assigned, the id never changes — even if the name is edited.
  - Collisions resolve via `_v2`, `_v3`, ... suffix.
  - The id is a portable string (lowercase, ASCII, underscore-separated).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Set


def slugify_personality_name(name: str) -> str:
    """Map a display name to a stable slug ID.

    Rules:
      - Strip diacritics via NFKD normalization
      - ASCII only
      - Lowercase
      - Any non-alphanumeric run collapses to a single underscore
      - Strip leading/trailing underscores

    Examples:
      "Abraham Lincoln"         -> "abraham_lincoln"
      "Louis XIV"               -> "louis_xiv"
      "GTO-Lite"                -> "gto_lite"
      "Dr. Seuss"               -> "dr_seuss"
      "A guy who tells dad..."  -> "a_guy_who_tells_dad"
      "Renée"                   -> "renee"

    Pathological cases:
      "" or "---"               -> ""    (caller's responsibility)
    """
    n = unicodedata.normalize("NFKD", name)
    n = n.encode("ascii", "ignore").decode("ascii")
    n = n.lower()
    n = re.sub(r"[^a-z0-9]+", "_", n)
    n = n.strip("_")
    return n


def assign_unique_personality_id(base_slug: str, taken: Set[str]) -> str:
    """Pick the first unused id of the form `base_slug`, `base_slug_v2`,
    `base_slug_v3`, ...

    The suffix loop starts at 2 (no `_v1`) so the "first claimant gets the
    bare slug" rule holds — the entity that got there first keeps the
    unadorned identifier; later collisions get suffixes.

    Gaps in the suffix sequence are filled rather than skipped past:
        taken = {"abraham", "abraham_v3"}
        assign_unique_personality_id("abraham", taken) -> "abraham_v2"
    """
    if base_slug not in taken:
        return base_slug
    suffix = 2
    while f"{base_slug}_v{suffix}" in taken:
        suffix += 1
    return f"{base_slug}_v{suffix}"
