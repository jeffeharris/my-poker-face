#!/usr/bin/env python3
"""Backfill image_prompt_profile fields for existing personalities.

Usage:
  python3 bin/backfill_image_prompt_profiles.py            # dry-run
  python3 bin/backfill_image_prompt_profiles.py --apply    # write changes
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Tuple


DEFAULT_PROFILE = {
    "character_kind": "human",
    "age_band": "adult",
    "core_description": "a distinctive poker player with clear facial features and a memorable silhouette",
    "outfit_options": [
        "a tailored blazer over a clean shirt",
        "a signature jacket with subtle character flair",
    ],
    "accessory_options": [
        "a simple ring",
        "a tasteful watch",
    ],
    "iconic_association": "a confident table presence",
}

VALID_KINDS = {"human", "humanoid", "animal", "robot", "creature", "object"}
VALID_AGE_BANDS = {"young_adult", "adult", "middle_aged", "older"}


def get_default_db_path() -> str:
    if Path("/app/data").exists():
        return "/app/data/poker_games.db"
    return str(Path(__file__).resolve().parents[1] / "data" / "poker_games.db")


def infer_character_kind(name: str, core_description: str | None) -> str:
    text = f"{name} {(core_description or '')}".lower()
    if any(token in text for token in ("r2d2", "robot", "droid", "android", "cyborg")):
        return "robot"
    if any(token in text for token in ("badger", "dog", "cat", "wolf", "bear", "bird")):
        return "animal"
    if any(token in text for token in ("cthulhu", "eldritch", "tentacle", "monster", "demon", "alien")):
        return "creature"
    if any(token in text for token in ("statue", "object", "car", "ship")):
        return "object"
    return "human"


def normalize_profile(name: str, profile: Any) -> Dict[str, Any]:
    out = {
        "character_kind": DEFAULT_PROFILE["character_kind"],
        "age_band": DEFAULT_PROFILE["age_band"],
        "core_description": DEFAULT_PROFILE["core_description"],
        "outfit_options": list(DEFAULT_PROFILE["outfit_options"]),
        "accessory_options": list(DEFAULT_PROFILE["accessory_options"]),
        "iconic_association": DEFAULT_PROFILE["iconic_association"],
    }

    if isinstance(profile, dict):
        kind = profile.get("character_kind")
        if kind in VALID_KINDS:
            out["character_kind"] = kind

        age_band = profile.get("age_band")
        if age_band in VALID_AGE_BANDS:
            out["age_band"] = age_band

        core = profile.get("core_description")
        if isinstance(core, str) and core.strip():
            out["core_description"] = core.strip()

        outfits = profile.get("outfit_options")
        if isinstance(outfits, list):
            cleaned = [str(v).strip() for v in outfits if str(v).strip()]
            if cleaned:
                out["outfit_options"] = cleaned[:2]

        accessories = profile.get("accessory_options")
        if isinstance(accessories, list):
            cleaned = [str(v).strip() for v in accessories if str(v).strip()]
            if cleaned:
                out["accessory_options"] = cleaned[:2]

        iconic = profile.get("iconic_association")
        if isinstance(iconic, str) and iconic.strip():
            out["iconic_association"] = iconic.strip()

    while len(out["outfit_options"]) < 2:
        out["outfit_options"].append(DEFAULT_PROFILE["outfit_options"][len(out["outfit_options"]) % 2])
    while len(out["accessory_options"]) < 2:
        out["accessory_options"].append(DEFAULT_PROFILE["accessory_options"][len(out["accessory_options"]) % 2])

    if out["character_kind"] not in VALID_KINDS:
        out["character_kind"] = infer_character_kind(name, out.get("core_description"))

    return out


def backfill_config(name: str, config: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    result = dict(config)
    profile = normalize_profile(name, result.get("image_prompt_profile"))
    changed = result.get("image_prompt_profile") != profile
    result["image_prompt_profile"] = profile

    avatar_description = result.get("avatar_description")
    if not isinstance(avatar_description, str) or not avatar_description.strip():
        result["avatar_description"] = profile["core_description"]
        changed = True

    return result, changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill image_prompt_profile for personalities.")
    parser.add_argument("--db-path", default=get_default_db_path(), help="SQLite DB path")
    parser.add_argument("--apply", action="store_true", help="Persist updates")
    args = parser.parse_args()

    updated = 0
    scanned = 0
    with sqlite3.connect(args.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT name, config_json FROM personalities").fetchall()
        for row in rows:
            scanned += 1
            name = row["name"]
            config = json.loads(row["config_json"]) if row["config_json"] else {}
            new_config, changed = backfill_config(name, config)
            if not changed:
                continue
            updated += 1
            if args.apply:
                conn.execute(
                    "UPDATE personalities SET config_json = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                    (json.dumps(new_config), name),
                )
        if args.apply:
            conn.commit()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] db={args.db_path}")
    print(f"Scanned: {scanned}")
    print(f"Would update: {updated}" if not args.apply else f"Updated: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
