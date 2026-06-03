#!/usr/bin/env python3
"""Backfill the per-persona `skill` tier into personalities that lack one.

The skill spectrum (PLAYER_SKILL_SPECTRUM.md) was authored only into
`personalities.json`, so a DB re-seed gives the 62 curated celebrities their
`skill`. DB-native personas (the `ai_generated` corpus, tourists) are NOT in the
JSON, so the seeder skips them and they fall back to the `shark` ceiling — sharp,
but with no field variety.

This script closes that gap WITHOUT a re-seed: for every persona missing a
`skill` key, derive the tier from its own `anchors.adaptation_bias` (the exact
rule the roster used — see `skill_tier_for_adaptation_bias`) and merge it into
config_json. Only `config_json` is touched — `source`, ownership, visibility,
circulating, personality_id, and avatars are all left untouched. Idempotent
(personas that already have a `skill` are skipped unless `--overwrite`) and
conservation-safe (no rows added/removed).

Usage (inside the backend container, against the Flask DB):
    python3 scripts/backfill_skill_tiers.py --dry-run
    python3 scripts/backfill_skill_tiers.py            # apply
    python3 scripts/backfill_skill_tiers.py --overwrite  # re-derive every persona

Note: `scripts/` is gitignored; this file is force-added.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

# Allow running as a bare script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from poker.personality_generator import _is_reserved_persona_name  # noqa: E402
from poker.strategy.skill_tiers import (  # noqa: E402
    SKILL_TIERS,
    skill_tier_for_adaptation_bias,
)

# The Flask app's DB inside Docker (see CLAUDE.md — NOT the bare poker_games.db).
DEFAULT_DB_PATH = "/app/data/poker_games.db"

# `skill` is read ONLY by TieredBotController (the `sharp` bot). Personas played
# by other controllers never consume it, so the authored roster deliberately
# left them out ("50 non-fish/non-bot celebrities", PLAYER_SKILL_SPECTRUM.md).
# This backfill honours that: it skips fish/tourists (marked by an `archetype`
# key → fish/rule controller) and the named solver/rule reference bots below.
_NON_TIERED_BOT_NAMES = {"CaseBot", "GTO-Lite", "BaselineSolver"}


def _is_tiered_eligible(name: str, config: dict) -> bool:
    """True if this persona is played by the tiered (`sharp`) controller, i.e.
    `skill` is actually consumed. Fish (have an `archetype`) and the named rule
    bots are excluded — `skill` would be inert and semantically misleading."""
    if name in _NON_TIERED_BOT_NAMES:
        return False
    if "archetype" in config:  # fish / tourist deviation archetype
        return False
    return True


def backfill(db_path: str, dry_run: bool, overwrite: bool) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT name, config_json FROM personalities").fetchall()

    applied = Counter()       # tier -> count of personas updated
    skipped_reserved = []
    skipped_has_skill = 0
    skipped_non_tiered = []
    skipped_bad_config = []

    for r in rows:
        name = r["name"]
        try:
            config = json.loads(r["config_json"]) if r["config_json"] else {}
        except (TypeError, json.JSONDecodeError):
            skipped_bad_config.append(name)
            continue

        if _is_reserved_persona_name(name):
            skipped_reserved.append(name)
            continue

        if "skill" in config and not overwrite:
            skipped_has_skill += 1
            continue

        if not _is_tiered_eligible(name, config):
            skipped_non_tiered.append(name)
            continue

        adaptation_bias = (config.get("anchors") or {}).get("adaptation_bias")
        tier = skill_tier_for_adaptation_bias(adaptation_bias)
        assert tier in SKILL_TIERS, f"derived unknown tier {tier!r} for {name!r}"

        config["skill"] = tier
        applied[tier] += 1

        if not dry_run:
            # Surgical: only config_json. Never touches source/ownership/avatars.
            conn.execute(
                "UPDATE personalities SET config_json = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (json.dumps(config), name),
            )

    if not dry_run:
        conn.commit()
    conn.close()

    return {
        "total_rows": len(rows),
        "applied": dict(applied),
        "applied_total": sum(applied.values()),
        "skipped_has_skill": skipped_has_skill,
        "skipped_reserved": skipped_reserved,
        "skipped_non_tiered": skipped_non_tiered,
        "skipped_bad_config": skipped_bad_config,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH, help="SQLite DB path")
    ap.add_argument(
        "--dry-run", action="store_true", help="report what would change, write nothing"
    )
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="re-derive `skill` even for personas that already have one",
    )
    args = ap.parse_args()

    result = backfill(args.db_path, dry_run=args.dry_run, overwrite=args.overwrite)

    mode = "DRY-RUN (no writes)" if args.dry_run else "APPLIED"
    print(f"=== skill-tier backfill [{mode}] — {args.db_path} ===")
    print(f"personalities scanned:        {result['total_rows']}")
    print(f"skill assigned:               {result['applied_total']}")
    for tier in ("shark", "reg", "weak_reg", "rec"):
        if tier in result["applied"]:
            print(f"    {tier:9} {result['applied'][tier]}")
    print(f"skipped (already had skill):  {result['skipped_has_skill']}")
    print(f"skipped (fish/bot, non-tiered): {len(result['skipped_non_tiered'])}")
    if result["skipped_non_tiered"]:
        print("    " + ", ".join(sorted(result["skipped_non_tiered"])))
    print(f"skipped (reserved/junk name): {len(result['skipped_reserved'])}")
    if result["skipped_reserved"]:
        print("    " + ", ".join(sorted(result["skipped_reserved"])))
    if result["skipped_bad_config"]:
        print(f"skipped (unparseable config): {len(result['skipped_bad_config'])}")
        print("    " + ", ".join(sorted(result["skipped_bad_config"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
