"""Backfill stable `id` field on every personality in poker/personalities.json.

The `id` is a deterministic slug of the display name at first assignment.
Once assigned, the `id` never changes — even if the display name is
edited later. New personalities added to the file pick up an id on the
next run; collisions resolve via `_v2` / `_v3` suffix.

Why: personality identity must persist across game sessions for the
relationship layer (heat/respect/likability axes) and cash mode (AI
bankrolls) to work. Display names collide and can be edited. Slug IDs
are stable.

Idempotent: re-running this script on a file that already has IDs
makes no changes (it preserves existing IDs verbatim).

Usage:
    python3 scripts/backfill_personality_ids.py
    python3 scripts/backfill_personality_ids.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Dict, Set


PERSONALITIES_PATH = Path(__file__).resolve().parent.parent / "poker" / "personalities.json"


# NOTE: these helpers are intentionally duplicated from
# `poker/personality_id.py` so this script can run as a standalone
# command-line tool from the host (without paying for the heavy
# `poker/__init__.py` import chain that brings in openai etc.).
# `tests/test_personality_id_backfill.py` includes a consistency test
# that asserts both copies produce identical output across a range of
# inputs — so any future edit to the rule must update both places.


def slugify(name: str) -> str:
    """Map a display name to a stable slug ID.

    Mirrors `poker.personality_id.slugify_personality_name`. See that
    module's docstring for the canonical specification.
    """
    n = unicodedata.normalize("NFKD", name)
    n = n.encode("ascii", "ignore").decode("ascii")
    n = n.lower()
    n = re.sub(r"[^a-z0-9]+", "_", n)
    n = n.strip("_")
    return n


def assign_unique_id(base_slug: str, taken: Set[str]) -> str:
    """Pick the first unused id of the form `base_slug`, `base_slug_v2`,
    `base_slug_v3`, ...

    Mirrors `poker.personality_id.assign_unique_personality_id`.
    """
    if base_slug not in taken:
        return base_slug
    suffix = 2
    while f"{base_slug}_v{suffix}" in taken:
        suffix += 1
    return f"{base_slug}_v{suffix}"


def backfill(data: Dict, *, verbose: bool = True) -> tuple[Dict, int, int]:
    """Add `id` to each personality entry that lacks one. Returns
    (updated_data, n_assigned, n_already_had_id)."""
    personalities = data.get("personalities", {})
    taken: Set[str] = {
        entry["id"] for entry in personalities.values() if isinstance(entry, dict) and "id" in entry
    }

    assigned = 0
    skipped = 0

    for name, entry in personalities.items():
        if not isinstance(entry, dict):
            continue
        if "id" in entry:
            skipped += 1
            if verbose:
                print(f"  keep   {entry['id']:<40} ({name})")
            continue
        base_slug = slugify(name)
        if not base_slug:
            print(
                f"WARNING: {name!r} slugifies to empty string — skipping. "
                "Add an id manually.",
                file=sys.stderr,
            )
            continue
        new_id = assign_unique_id(base_slug, taken)
        entry["id"] = new_id
        taken.add(new_id)
        assigned += 1
        if verbose:
            print(f"  assign {new_id:<40} ({name})")

    return data, assigned, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing the file.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-entry assignment lines.",
    )
    args = parser.parse_args()

    if not PERSONALITIES_PATH.exists():
        print(f"ERROR: {PERSONALITIES_PATH} not found", file=sys.stderr)
        return 1

    with PERSONALITIES_PATH.open() as f:
        data = json.load(f)

    updated, assigned, skipped = backfill(data, verbose=not args.quiet)

    print()
    print(f"Assigned: {assigned}    Already had id: {skipped}")

    if assigned == 0:
        print("No changes needed.")
        return 0

    if args.dry_run:
        print("(dry-run — file not modified)")
        return 0

    with PERSONALITIES_PATH.open("w") as f:
        json.dump(updated, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Wrote {PERSONALITIES_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
