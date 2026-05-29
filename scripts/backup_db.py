#!/usr/bin/env python3
"""WAL-safe SQLite backup with integrity check, retention, and off-box copy (PRH-29).

The old deploy-time backup did a plain `cp` of a live WAL database to the same
disk — that can capture a torn/inconsistent file (the `-wal` sidecar isn't
checkpointed) and a single-disk failure loses everything. This uses the SQLite
**online backup API** (`Connection.backup`), which produces a transactionally
consistent snapshot even while the app is writing, then verifies it with
`PRAGMA integrity_check`, prunes to a daily retention window, and (optionally)
ships the snapshot off-box.

Usage:
    python3 scripts/backup_db.py /opt/poker/data/poker_games.db
    python3 scripts/backup_db.py <db> --dest <dir> --keep 7 \
        --remote-cmd 'rclone copy {path} storagebox:poker-backups/'

Exit codes: 0 ok; 1 source missing / backup failed; 2 integrity check failed;
3 off-box copy failed (local backup still succeeded). Non-zero is alertable —
wire it to the PRH-28 webhook from cron.

Schedule (operator, on the prod box) — daily at 03:30, keep 7, ship off-box:
    30 3 * * * cd /opt/poker && python3 scripts/backup_db.py data/poker_games.db \
        --keep 7 --remote-cmd 'rclone copy {path} storagebox:poker-backups/' \
        >> /var/log/poker-backup.log 2>&1
"""

import argparse
import datetime
import os
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKUP_SUFFIX = ".backup_"  # poker_games.db.backup_YYYYmmdd-HHMMSS


def _log(msg: str) -> None:
    print(f"[backup_db] {msg}", flush=True)


def make_backup(src: Path, dest_dir: Path) -> Path:
    """Snapshot `src` into `dest_dir` via the SQLite online backup API.

    Returns the snapshot path. Raises on failure (caller maps to exit code).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{src.name}{BACKUP_SUFFIX}{stamp}"

    # The backup API copies a consistent snapshot even with concurrent WAL
    # writers — no need to stop the app or checkpoint first.
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    try:
        dst_conn = sqlite3.connect(str(dest), timeout=30)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    return dest


def integrity_ok(db: Path) -> bool:
    conn = sqlite3.connect(str(db), timeout=30)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"
    finally:
        conn.close()


def prune(dest_dir: Path, src_name: str, keep: int) -> int:
    """Keep the newest `keep` snapshots; delete the rest. Returns #deleted."""
    snaps = sorted(
        dest_dir.glob(f"{src_name}{BACKUP_SUFFIX}*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    deleted = 0
    for stale in snaps[keep:]:
        try:
            stale.unlink()
            deleted += 1
        except OSError as e:
            _log(f"WARN could not remove {stale}: {e}")
    return deleted


def ship_off_box(path: Path, remote_cmd_template: str) -> bool:
    """Run the off-box copy command with {path} substituted. True on success."""
    cmd = remote_cmd_template.format(path=str(path))
    _log(f"shipping off-box: {cmd}")
    result = subprocess.run(shlex.split(cmd), capture_output=True, text=True)
    if result.returncode != 0:
        _log(f"ERROR off-box copy failed (rc={result.returncode}): {result.stderr.strip()}")
        return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="WAL-safe SQLite backup (PRH-29)")
    parser.add_argument("src", help="path to the live SQLite database")
    parser.add_argument(
        "--dest",
        default=None,
        help="backup directory (default: <src_dir>/backups)",
    )
    parser.add_argument("--keep", type=int, default=7, help="daily snapshots to retain (default 7)")
    parser.add_argument(
        "--remote-cmd",
        default=os.environ.get("BACKUP_REMOTE_CMD"),
        help="off-box copy command template; {path} is substituted "
        "(or set BACKUP_REMOTE_CMD). Omit to keep backups on-box only.",
    )
    args = parser.parse_args(argv)

    src = Path(args.src)
    if not src.exists():
        _log(f"ERROR source DB not found: {src}")
        return 1
    dest_dir = Path(args.dest) if args.dest else src.parent / "backups"

    try:
        snapshot = make_backup(src, dest_dir)
    except sqlite3.Error as e:
        _log(f"ERROR backup failed: {e}")
        return 1
    _log(f"snapshot written: {snapshot} ({snapshot.stat().st_size} bytes)")

    if not integrity_ok(snapshot):
        _log(f"ERROR integrity_check FAILED on {snapshot} — removing corrupt snapshot")
        snapshot.unlink(missing_ok=True)
        return 2
    _log("integrity_check: ok")

    deleted = prune(dest_dir, src.name, args.keep)
    _log(f"retention: kept newest {args.keep}, deleted {deleted}")

    if args.remote_cmd:
        if not ship_off_box(snapshot, args.remote_cmd):
            return 3
        _log("off-box copy: ok")
    else:
        _log(
            "WARN no --remote-cmd / BACKUP_REMOTE_CMD set — backup is ON-BOX ONLY "
            "(a single-disk failure still loses everything; set a remote target)"
        )

    _log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
