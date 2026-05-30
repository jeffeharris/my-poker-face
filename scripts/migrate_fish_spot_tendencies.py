#!/usr/bin/env python3
"""Backfill live casino-fish personas with their fixture spot_tendencies.

Context (docs/plans/VARIETY_VALIDATION_AND_DEPLOY_HANDOFF.md §C): the
`fish → tiered calling_station` migration gives every fish a recognizable tell
via a `spot_tendencies` reshape (sticky / over_bluff) derived from its legacy
`fish_leak`. The FIXTURE (poker/personalities.json) carries both `fish_leak`
and the explicit `spot_tendencies` for each fish. But the LIVE DB rows are
*bare* — `rule_strategy=='fish'` with `fish_leak=None` and
`spot_tendencies=None` — so at $10/$50 (where the profile isn't stake-forced)
the live fish are all identical bland calling-stations. ($2 is unaffected:
`weak_fish` is stake-forced in code.)

This copies, BY NAME, each fixture fish's `fish_leak` + `spot_tendencies` into
the matching live persona's `config_json`. A fish whose fixture entry has no
spot_tendencies (limps_every_hand / bets_strong_transparently — no clean
spot-tendency analogue) gets its `fish_leak` set but no tendencies, which is
correct: the bare calling_station already plays that shape.

Safety:
  - DRY RUN by default. Pass --apply to write.
  - Backs up the DB via the sqlite *backup API* (a plain cp of a live WAL DB is
    malformed — see memory reference_sqlite_wal_backup) before any write.
  - IDEMPOTENT: only rows whose fish_leak/spot_tendencies actually differ from
    the fixture are updated; re-running is a no-op.
  - Conservation-safe: touches only behavioral fields in config_json
    (fish_leak, spot_tendencies) — never chips, bankroll, or ledger rows.
  - Names with no fixture match are skipped and reported (so a prod fish with
    a novel name surfaces rather than being silently ignored).

Usage (inside the backend container, against the Flask app DB):
  docker compose exec -T backend python scripts/migrate_fish_spot_tendencies.py            # dry run
  docker compose exec -T backend python scripts/migrate_fish_spot_tendencies.py --apply    # write
"""

import argparse
import json
import os
import sqlite3
import sys

DEFAULT_DB = '/app/data/poker_games.db'
DEFAULT_FIXTURE = '/app/poker/personalities.json'


def load_fixture_fish(fixture_path):
    """Return {name: {'fish_leak': str|None, 'spot_tendencies': list|None}}
    for every fixture persona with rule_strategy == 'fish'."""
    raw = json.load(open(fixture_path))
    personas = raw.get('personalities', raw)
    out = {}
    for name, v in personas.items():
        if v.get('rule_strategy') == 'fish':
            out[name] = {
                'fish_leak': v.get('fish_leak'),
                'spot_tendencies': v.get('spot_tendencies'),
            }
    return out


def backup_db(db_path, backup_dir):
    """WAL-safe backup via the sqlite backup API. Returns the backup path.
    A fixed (non-timestamped) name keeps re-runs from littering — but we refuse
    to clobber an existing backup so a prior good copy is never lost."""
    os.makedirs(backup_dir, exist_ok=True)
    base = os.path.basename(db_path)
    backup_path = os.path.join(backup_dir, f'{base}.pre_fish_tendencies.bak')
    if os.path.exists(backup_path):
        print(f"  [backup] reusing existing pre-migration backup: {backup_path}")
        return backup_path
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    print(f"  [backup] wrote WAL-safe backup → {backup_path}")
    return backup_path


def plan_updates(db_path, fixture_fish):
    """Return (updates, skips). updates: list of (id, name, new_config_dict,
    change_summary). skips: list of (name, reason)."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    updates, skips = [], []
    rows = con.execute(
        "SELECT id, name, config_json FROM personalities"
    ).fetchall()
    for r in rows:
        try:
            cfg = json.loads(r['config_json']) if r['config_json'] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if cfg.get('rule_strategy') != 'fish':
            continue
        fx = fixture_fish.get(r['name'])
        if fx is None:
            skips.append((r['name'], 'no fixture match by name'))
            continue
        target_leak = fx['fish_leak']
        target_tend = fx['spot_tendencies']
        cur_leak = cfg.get('fish_leak')
        cur_tend = cfg.get('spot_tendencies')
        if cur_leak == target_leak and cur_tend == target_tend:
            skips.append((r['name'], 'already matches fixture (idempotent no-op)'))
            continue
        new_cfg = dict(cfg)
        new_cfg['fish_leak'] = target_leak
        if target_tend is not None:
            new_cfg['spot_tendencies'] = target_tend
        else:
            # No analogue: ensure no stale tendencies linger; the bare
            # calling_station covers limps/transparent-sizing fish.
            new_cfg.pop('spot_tendencies', None)
        summary = f"fish_leak {cur_leak!r}→{target_leak!r}, spot_tendencies {cur_tend}→{target_tend}"
        updates.append((r['id'], r['name'], new_cfg, summary))
    con.close()
    return updates, skips


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', default=DEFAULT_DB)
    p.add_argument('--fixture', default=DEFAULT_FIXTURE)
    p.add_argument('--backup-dir', default=None,
                   help='where to write the WAL-safe backup (default: alongside the DB)')
    p.add_argument('--apply', action='store_true', help='write changes (default: dry run)')
    args = p.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.fixture):
        print(f"fixture not found: {args.fixture}", file=sys.stderr)
        sys.exit(1)

    fixture_fish = load_fixture_fish(args.fixture)
    print(f"Fixture fish ({len(fixture_fish)}): {', '.join(sorted(fixture_fish))}")
    updates, skips = plan_updates(args.db, fixture_fish)

    print(f"\n=== PLAN ({'APPLY' if args.apply else 'DRY RUN'}) — DB {args.db} ===")
    print(f"\n{len(updates)} row(s) to update:")
    for _id, name, _cfg, summary in updates:
        print(f"  • {name} (id={_id}): {summary}")
    print(f"\n{len(skips)} skipped:")
    for name, reason in skips:
        print(f"  - {name}: {reason}")

    if not args.apply:
        print("\nDRY RUN — no changes written. Re-run with --apply to commit.")
        return
    if not updates:
        print("\nNothing to update.")
        return

    backup_dir = args.backup_dir or os.path.dirname(os.path.abspath(args.db))
    backup_db(args.db, backup_dir)

    con = sqlite3.connect(args.db)
    with con:
        for _id, name, cfg, _summary in updates:
            con.execute(
                "UPDATE personalities SET config_json = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (json.dumps(cfg), _id),
            )
    con.close()
    print(f"\nApplied {len(updates)} update(s). ✅")


if __name__ == '__main__':
    main()
