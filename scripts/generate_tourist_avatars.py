"""Batch-generate tourist avatar portraits.

For each (template, first_name) combo in the tourist factory's name
pools, create a namespaced DB personality (id `_tourist_<template>_<name>`)
that mirrors the template's anchors, then drive the existing
`character_images` pipeline to generate priority-emotion portraits.
Finally print a Python snippet to paste into `cash_mode/tourist_avatars.py`
to extend the `NAME_LEVEL` registry.

The leading underscore in the personality_id namespaces these out of
the cash-eligible pool — `ensure_ai_bankrolls_seeded` won't seed
bankrolls for them, and live-fill rejects any pid with bankroll<buy_in.
So no zombie-style leakage even though they live in `personalities`.

Idempotent: skips personalities that already exist AND already have a
confident-emotion avatar. Safe to re-run after a partial generation
fails (network, LLM rate-limit, etc.).

Usage:
  python3 scripts/generate_tourist_avatars.py                 # dry-run + report
  python3 scripts/generate_tourist_avatars.py --apply         # generate (LLM cost)
  python3 scripts/generate_tourist_avatars.py --apply --emotions confident,poker_face
  python3 scripts/generate_tourist_avatars.py --apply --template vacation_dad
  python3 scripts/generate_tourist_avatars.py --print-registry  # emit snippet only
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Dict, List, Optional

# Add repo root to path so we can import from cash_mode/, poker/, etc.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from cash_mode.tourist_factory import TOURIST_TEMPLATES, TouristTemplate
from poker.character_images import (
    generate_character_images,
    has_character_images,
)
from poker.repositories.personality_repository import PersonalityRepository


DB_PATH_DOCKER = "/app/data/poker_games.db"
DB_PATH_LOCAL = "data/poker_games.db"

# Generate only the priority emotions by default — the avatar handler
# falls back to these for any other emotion the UI requests. Full set
# (~12 emotions) is ~12x the API cost; usually overkill for tourists
# whose stay is short.
DEFAULT_EMOTIONS = ["confident", "poker_face"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _resolve_db_path() -> str:
    for p in (DB_PATH_DOCKER, DB_PATH_LOCAL):
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"DB not found at {DB_PATH_DOCKER} or {DB_PATH_LOCAL}")


def _slug(s: str) -> str:
    return s.lower().replace(" ", "_").replace("(", "").replace(")", "").strip("_")


def _tourist_personality_id(template_key: str, first_name: str) -> str:
    """Namespaced id that won't conflict with real personalities.
    Leading underscore signals 'system' / 'not for live-fill'."""
    return f"_tourist_{template_key}_{_slug(first_name)}"


def _tourist_personality_name(template: TouristTemplate, first_name: str) -> str:
    """Personality display name — used in `/api/avatar/<name>/...` URLs.
    Not shown to users; the seat carries its own `display_name`. We add
    a template tag so the avatar generator has visual context."""
    label_map = {
        "vacation_dad":          "Vacation Dad",
        "bachelorette":          "Bachelorette",
        "retired_know_it_all":   "Retired Know-It-All",
        "birthday_kid":          "Birthday Kid",
        "finance_bro":           "Finance Bro",
        "superstitious_grandma": "Grandmother",
        "slot_refugee":          "Slot Refugee",
        "golf_trip_dude":        "Golf Trip Dude",
    }
    tag = label_map.get(template.key, template.key.replace("_", " ").title())
    return f"{first_name} the {tag}"


def _build_personality_config(template: TouristTemplate, first_name: str) -> dict:
    """Mirror the template structure (anchors, tics, style) onto a
    first-name-specialized personality. The avatar generator uses
    `play_style` + `default_attitude` + `verbal_tics` to inform the
    visual prompt, so passing the template's flavor matters."""
    return {
        "archetype": "fish",
        "play_style": template.play_style,
        "default_confidence": template.default_confidence,
        "default_attitude": template.default_attitude,
        "anchors": dict(template.anchors),
        "verbal_tics": list(template.verbal_tics),
        "physical_tics": list(template.physical_tics),
        "nickname": first_name,
        "bankroll_knobs": {
            # Zero starting_bankroll — keeps these OUT of live-fill.
            # Live-fill picks personalities with bankroll>=buy_in.
            "starting_bankroll": 0,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$2",
        },
        "staker_profile": {"willing": False},
        "borrower_profile": {"willing": False},
        "rule_strategy": "fish",
        # Flag for any future filter that wants to identify tourist
        # template personalities specifically.
        "tourist_template_key": template.key,
        "_namespace": "tourist_avatar",
    }


def plan_entries(
    template_filter: Optional[str] = None,
) -> List[tuple]:
    """Return [(template, first_name, pid, display_name)] for every
    (template, name) combo to process."""
    out = []
    for template in TOURIST_TEMPLATES:
        if template_filter and template.key != template_filter:
            continue
        for first_name in template.name_pool:
            pid = _tourist_personality_id(template.key, first_name)
            name = _tourist_personality_name(template, first_name)
            out.append((template, first_name, pid, name))
    return out


def process_one(
    template: TouristTemplate,
    first_name: str,
    pid: str,
    display_name: str,
    *,
    repo: PersonalityRepository,
    emotions: List[str],
    apply: bool,
) -> str:
    """Return one of {'skipped', 'created_personality',
    'created_personality+avatars', 'avatars_only', 'avatars_only_failed'}.
    """
    # Skip if personality + at least one emotion avatar already there.
    existing_pers = repo.load_personality_by_id(pid)
    has_avatar = has_character_images(display_name) if existing_pers else False
    if existing_pers and has_avatar:
        logger.info(
            "  skip: %s (%s) — personality + avatar already exist", pid, display_name,
        )
        return "skipped"

    actions = []
    if not existing_pers:
        actions.append("create personality")
    if not has_avatar:
        actions.append(f"generate avatars: {emotions}")
    logger.info(
        "  %s: %s — %s",
        "WOULD" if not apply else "DO",
        display_name,
        " + ".join(actions),
    )

    if not apply:
        return "preview"

    # Create personality if missing.
    if not existing_pers:
        config = _build_personality_config(template, first_name)
        repo.save_personality(
            display_name, config, personality_id=pid, source="tourist_avatar_seed",
        )

    # Generate avatars.
    try:
        result = generate_character_images(display_name, emotions=emotions)
        n = result.get("generated", 0)
        errs = result.get("errors", [])
        if errs:
            logger.warning("    avatar errors: %s", errs)
        if n > 0:
            return ("created_personality+avatars" if not existing_pers
                    else "avatars_only")
        return "avatars_only_failed"
    except Exception as exc:
        logger.warning("    avatar generation raised: %s", exc)
        return "avatars_only_failed"


def emit_registry_snippet(entries: List[tuple]) -> None:
    """Print a Python snippet for cash_mode/tourist_avatars.py NAME_LEVEL."""
    print()
    print("=" * 70)
    print("REGISTRY SNIPPET — paste into cash_mode/tourist_avatars.py NAME_LEVEL")
    print("=" * 70)
    # Group by template for readability
    by_template: Dict[str, List[tuple]] = {}
    for template, first_name, pid, _name in entries:
        by_template.setdefault(template.key, []).append((first_name, pid))
    for template_key in sorted(by_template):
        print(f"    # {template_key}")
        for first_name, pid in by_template[template_key]:
            print(f'    ({template_key!r:>26}, {first_name!r:>15}): {pid!r},')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually create personalities + call avatar pipeline (LLM cost)")
    parser.add_argument("--db", default=None)
    parser.add_argument("--emotions", default=",".join(DEFAULT_EMOTIONS),
                        help="Comma-separated emotion list (default: confident,poker_face)")
    parser.add_argument("--template", default=None,
                        help="Limit to one template_key (debugging)")
    parser.add_argument("--print-registry", action="store_true",
                        help="Print the NAME_LEVEL snippet without generating anything")
    args = parser.parse_args()

    db_path = args.db or _resolve_db_path()
    emotions = [e.strip() for e in args.emotions.split(",") if e.strip()]

    entries = plan_entries(template_filter=args.template)
    logger.info("Planned %d (template, name) entries.", len(entries))

    if args.print_registry:
        emit_registry_snippet(entries)
        return 0

    repo = PersonalityRepository(db_path)

    counts: Dict[str, int] = {}
    for template, first_name, pid, display_name in entries:
        result = process_one(
            template, first_name, pid, display_name,
            repo=repo, emotions=emotions, apply=args.apply,
        )
        counts[result] = counts.get(result, 0) + 1

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")

    if args.apply:
        emit_registry_snippet(entries)
    else:
        print()
        print("(dry run — re-run with --apply to create personalities + generate avatars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
