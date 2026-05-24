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
# Populated by `scripts/generate_tourist_avatars.py` which created a
# purpose-built `_tourist_<template>_<name>` personality per entry,
# each with its own LLM-generated portrait (priority emotions:
# confident + poker_face). Extend by re-running the script for new
# (template, name) combos — runtime lookup is O(1).
NAME_LEVEL: Dict[Tuple[str, str], str] = {
    # bachelorette
    ("bachelorette",          "Brenda"):    "_tourist_bachelorette_brenda",
    ("bachelorette",          "Tiffany"):   "_tourist_bachelorette_tiffany",
    ("bachelorette",          "Ashley"):    "_tourist_bachelorette_ashley",
    ("bachelorette",          "Brittany"):  "_tourist_bachelorette_brittany",
    ("bachelorette",          "Megan"):     "_tourist_bachelorette_megan",
    ("bachelorette",          "Courtney"):  "_tourist_bachelorette_courtney",
    ("bachelorette",          "Lauren"):    "_tourist_bachelorette_lauren",
    ("bachelorette",          "Stacy"):     "_tourist_bachelorette_stacy",
    ("bachelorette",          "Jenna"):     "_tourist_bachelorette_jenna",
    ("bachelorette",          "Caitlin"):   "_tourist_bachelorette_caitlin",
    # birthday_kid
    ("birthday_kid",          "Bobby"):     "_tourist_birthday_kid_bobby",
    ("birthday_kid",          "Tommy"):     "_tourist_birthday_kid_tommy",
    ("birthday_kid",          "Joey"):      "_tourist_birthday_kid_joey",
    ("birthday_kid",          "Kenny"):     "_tourist_birthday_kid_kenny",
    ("birthday_kid",          "Danny"):     "_tourist_birthday_kid_danny",
    ("birthday_kid",          "Ricky"):     "_tourist_birthday_kid_ricky",
    ("birthday_kid",          "Jimmy"):     "_tourist_birthday_kid_jimmy",
    ("birthday_kid",          "Mikey"):     "_tourist_birthday_kid_mikey",
    ("birthday_kid",          "Sammy"):     "_tourist_birthday_kid_sammy",
    # finance_bro
    ("finance_bro",           "Chad"):      "_tourist_finance_bro_chad",
    ("finance_bro",           "Trent"):     "_tourist_finance_bro_trent",
    ("finance_bro",           "Brett"):     "_tourist_finance_bro_brett",
    ("finance_bro",           "Connor"):    "_tourist_finance_bro_connor",
    ("finance_bro",           "Tyler"):     "_tourist_finance_bro_tyler",
    ("finance_bro",           "Hunter"):    "_tourist_finance_bro_hunter",
    ("finance_bro",           "Garrett"):   "_tourist_finance_bro_garrett",
    ("finance_bro",           "Brody"):     "_tourist_finance_bro_brody",
    # golf_trip_dude
    ("golf_trip_dude",        "Brad"):      "_tourist_golf_trip_dude_brad",
    ("golf_trip_dude",        "Doug"):      "_tourist_golf_trip_dude_doug",
    ("golf_trip_dude",        "Kevin"):     "_tourist_golf_trip_dude_kevin",
    ("golf_trip_dude",        "Scott"):     "_tourist_golf_trip_dude_scott",
    ("golf_trip_dude",        "Todd"):      "_tourist_golf_trip_dude_todd",
    ("golf_trip_dude",        "Greg"):      "_tourist_golf_trip_dude_greg",
    ("golf_trip_dude",        "Curt"):      "_tourist_golf_trip_dude_curt",
    ("golf_trip_dude",        "Jay"):       "_tourist_golf_trip_dude_jay",
    # retired_know_it_all
    ("retired_know_it_all",   "Carl"):      "_tourist_retired_know_it_all_carl",
    ("retired_know_it_all",   "Frank"):     "_tourist_retired_know_it_all_frank",
    ("retired_know_it_all",   "Stan"):      "_tourist_retired_know_it_all_stan",
    ("retired_know_it_all",   "Vince"):     "_tourist_retired_know_it_all_vince",
    ("retired_know_it_all",   "Norm"):      "_tourist_retired_know_it_all_norm",
    ("retired_know_it_all",   "Harold"):    "_tourist_retired_know_it_all_harold",
    ("retired_know_it_all",   "Ernie"):     "_tourist_retired_know_it_all_ernie",
    ("retired_know_it_all",   "Walt"):      "_tourist_retired_know_it_all_walt",
    ("retired_know_it_all",   "Lloyd"):     "_tourist_retired_know_it_all_lloyd",
    ("retired_know_it_all",   "Hank"):      "_tourist_retired_know_it_all_hank",
    # slot_refugee
    ("slot_refugee",          "Linda"):     "_tourist_slot_refugee_linda",
    ("slot_refugee",          "Karen"):     "_tourist_slot_refugee_karen",
    ("slot_refugee",          "Donna"):     "_tourist_slot_refugee_donna",
    ("slot_refugee",          "Cheryl"):    "_tourist_slot_refugee_cheryl",
    ("slot_refugee",          "Patty"):     "_tourist_slot_refugee_patty",
    ("slot_refugee",          "Sharon"):    "_tourist_slot_refugee_sharon",
    ("slot_refugee",          "Joyce"):     "_tourist_slot_refugee_joyce",
    ("slot_refugee",          "Marlene"):   "_tourist_slot_refugee_marlene",
    # superstitious_grandma
    ("superstitious_grandma", "Mona"):      "_tourist_superstitious_grandma_mona",
    ("superstitious_grandma", "Doris"):     "_tourist_superstitious_grandma_doris",
    ("superstitious_grandma", "Ethel"):     "_tourist_superstitious_grandma_ethel",
    ("superstitious_grandma", "Mildred"):   "_tourist_superstitious_grandma_mildred",
    ("superstitious_grandma", "Phyllis"):   "_tourist_superstitious_grandma_phyllis",
    ("superstitious_grandma", "Bernice"):   "_tourist_superstitious_grandma_bernice",
    ("superstitious_grandma", "Edna"):      "_tourist_superstitious_grandma_edna",
    ("superstitious_grandma", "Gertrude"):  "_tourist_superstitious_grandma_gertrude",
    # vacation_dad
    ("vacation_dad",          "Greg"):      "_tourist_vacation_dad_greg",
    ("vacation_dad",          "Dave"):      "_tourist_vacation_dad_dave",
    ("vacation_dad",          "Doug"):      "_tourist_vacation_dad_doug",
    ("vacation_dad",          "Rick"):      "_tourist_vacation_dad_rick",
    ("vacation_dad",          "Steve"):     "_tourist_vacation_dad_steve",
    ("vacation_dad",          "Mike"):      "_tourist_vacation_dad_mike",
    ("vacation_dad",          "Jeff"):      "_tourist_vacation_dad_jeff",
    ("vacation_dad",          "Brad"):      "_tourist_vacation_dad_brad",
    ("vacation_dad",          "Chad"):      "_tourist_vacation_dad_chad",
    ("vacation_dad",          "Wayne"):     "_tourist_vacation_dad_wayne",
    ("vacation_dad",          "Randy"):     "_tourist_vacation_dad_randy",
    ("vacation_dad",          "Kurt"):      "_tourist_vacation_dad_kurt",
}

# Per-template fallback: when no NAME_LEVEL match exists (e.g., a future
# name added to a template's pool before batch-gen catches up), share
# one of the template's existing portraits. None means "no fallback,
# render the letter circle." After batch-gen, every template has at
# least one portrait, so we always have a fallback to point at.
TEMPLATE_FALLBACK: Dict[str, Optional[str]] = {
    "vacation_dad":          "_tourist_vacation_dad_greg",
    "bachelorette":          "_tourist_bachelorette_brenda",
    "retired_know_it_all":   "_tourist_retired_know_it_all_carl",
    "birthday_kid":          "_tourist_birthday_kid_bobby",
    "finance_bro":           "_tourist_finance_bro_trent",
    "superstitious_grandma": "_tourist_superstitious_grandma_mona",
    "slot_refugee":          "_tourist_slot_refugee_linda",
    "golf_trip_dude":        "_tourist_golf_trip_dude_brad",
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
