"""One-shot: settle and clean up an orphan cash session via the production leave path.

Use case
--------
When a `cash-*` row sits in the DB (typically after a backend restart
mid-session) and `_find_active_cash_game_id` keeps 409-ing every new
sit attempt with "A cash session is already active. Leave first." —
but the user can't easily reach the in-game "Leave table" button
(e.g., the cold-load is flaky, or they want a clean exit without
playing through to a hand boundary).

What it does
------------
1. Snapshots the 4 affected rows (`games`, `cash_sessions`, `stakes`,
   the `cash_tables` seat) plus the player + sponsor bankrolls into a
   timestamped rollback JSON under `data/`.
2. Replicates the minimum cold-load: rebuilds the in-memory game_data
   the production leave path expects, including `cash_personality_ids`
   so AI cash-out credits the right bankrolls.
3. Calls `_leave_table_locked` from `flask_app/routes/cash_routes.py`
   — same code path as `POST /api/cash/leave`. Settles the stake via
   `settle_stake_on_leave` (normal underwater/over math), finalizes
   the `cash_sessions` row with `closed_status='left'`, credits AI
   bankrolls, deletes the `games` row, purges any other cash rows for
   this owner.
4. Works around the nested-if bug at `cash_routes.py:4256`: the
   `_free_ghost_human_seats` cross-table sweep is currently scoped
   inside `if cash_table_id is not None:`, so sessions whose
   `cash_sessions.cash_table_id` is NULL (sponsor-flow gap at
   `cash_routes.py:1976`) leave their lobby seat behind. We call the
   sweep ourselves after the leave path returns.

Run
---
    docker compose exec backend python3 /app/scripts/cleanup_orphan_cash_session.py \
        --game-id cash--7j9cUI_JR_WA4BUhc-Avw \
        --owner-id guest_jeff

Add `--dry-run` to see what would happen without writing.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("cleanup_orphan")

DB_PATH = "/app/data/poker_games.db"


def snapshot_rollback(conn: sqlite3.Connection, game_id: str, owner_id: str) -> Dict[str, Any]:
    """Read every row that the cleanup might touch into a JSON-safe dict.

    Saved alongside the cleanup so a manual SQL replay can put the
    universe back if the settlement turns out wrong. Bankrolls are
    snapshotted both for the human owner and for any non-house staker
    so we can spot a math drift.
    """
    conn.row_factory = sqlite3.Row
    snap: Dict[str, Any] = {
        "snapshot_at": datetime.utcnow().isoformat(),
        "game_id": game_id,
        "owner_id": owner_id,
    }
    snap["games"] = [dict(r) for r in conn.execute("SELECT * FROM games WHERE game_id = ?", (game_id,))]
    snap["cash_sessions"] = [
        dict(r) for r in conn.execute("SELECT * FROM cash_sessions WHERE session_id = ?", (game_id,))
    ]
    snap["stakes"] = [
        dict(r) for r in conn.execute("SELECT * FROM stakes WHERE session_id = ?", (game_id,))
    ]
    snap["cash_tables_with_owner_seat"] = []
    for row in conn.execute("SELECT * FROM cash_tables WHERE seats_json LIKE ?", (f"%{owner_id}%",)):
        snap["cash_tables_with_owner_seat"].append(dict(row))

    # Bankrolls: human + every staker the stakes table mentions.
    staker_ids = {r["staker_id"] for r in snap["stakes"] if r["staker_id"]}
    bk_keys = {owner_id} | staker_ids
    snap["bankrolls_human"] = []
    for pid in bk_keys:
        rows = list(
            conn.execute(
                "SELECT * FROM player_bankroll_state WHERE player_id = ?",
                (pid,),
            )
        )
        snap["bankrolls_human"].append({"player_id": pid, "rows": [dict(r) for r in rows]})

    # AI bankrolls for everyone seated at the game (Blackbeard etc.) —
    # `_leave_table_locked` credits each AI's bankroll with their
    # current Player.stack, so capture pre-state.
    game_row = snap["games"][0] if snap["games"] else None
    if game_row:
        gs = json.loads(game_row["game_state_json"])
        ai_pids = []
        for p in gs.get("players", []):
            if p.get("is_human"):
                continue
            # Name → personality_id resolution requires the repo;
            # capture by name + we'll do a separate lookup at apply time.
            ai_pids.append(p.get("name"))
        snap["seated_ai_names"] = ai_pids
        # `ai_bankroll_state` is keyed on `personality_id` only; we
        # captured names above (the in-game label, e.g. "Blackbeard")
        # so this best-effort lookup snapshots the canonical-name row
        # if `personality_id == lowercased(name)` (the common case for
        # curated personalities) plus a fuzzy LIKE for snake-case ids.
        ai_bankrolls = []
        for name in ai_pids:
            pid_guess = name.lower().replace(" ", "_")
            for r in conn.execute(
                "SELECT * FROM ai_bankroll_state WHERE personality_id = ? OR personality_id LIKE ?",
                (pid_guess, f"%{pid_guess}%"),
            ):
                ai_bankrolls.append(dict(r))
        snap["bankrolls_ai_pre"] = ai_bankrolls

    return snap


def write_rollback(snapshot: Dict[str, Any]) -> Path:
    """Pin the rollback JSON under /app/data with a UTC-stamped name.

    Convention matches earlier cleanups in this repo
    (`data/orphan_cleanup_rollback_*.json`,
    `data/stale_session_close_rollback_*.json`).
    """
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H%M%S%f")
    path = Path(f"/app/data/orphan_cleanup_rollback_{ts}.json")
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
    logger.info("rollback snapshot written to %s (%d bytes)", path, path.stat().st_size)
    return path


def warm_game_into_memory(app, game_id: str) -> bool:
    """Replay the minimum cold-load that `_leave_table_locked` needs.

    We don't run the FULL cold-load (tournament tracker, hand-history
    restore, opponent-model wiring, etc.) — none of it is required for
    leave-time settlement. We do need:

    * `state_machine` — read for human stack and AI stacks
    * `owner_id` / `owner_name` — for logging + permission echo
    * `sandbox_id` — controls which cash_tables get swept
    * `cash_mode=True` — gates the cash branches inside leave
    * `cash_personality_ids` — drives `credit_ai_cash_out` for each AI
    * `cash_table_id` / `cash_seat_index` — pulled from `cash_sessions`
      (NULL in our case, leave path tolerates that)

    Returns True if the game was warmed into game_state_service.
    """
    from flask_app.extensions import game_repo, personality_repo, cash_session_repo
    from flask_app.routes.cash_routes import STAKES_LADDER
    from flask_app.services import game_state_service
    from flask_app.game_adapter import StateMachineAdapter

    if game_state_service.get_game(game_id) is not None:
        logger.info("game %s already in memory — using existing entry", game_id)
        return True

    base_state_machine = game_repo.load_game(game_id)
    if not base_state_machine:
        logger.error("game_repo.load_game(%s) returned None — nothing to clean", game_id)
        return False

    state_machine = StateMachineAdapter(base_state_machine)
    big_blind = state_machine.game_state.current_ante or 100
    stake_label = next(
        (label for label, cfg in STAKES_LADDER.items() if cfg["big_blind"] == big_blind),
        None,
    )

    cash_personality_ids: Dict[str, str] = {}
    for player in state_machine.game_state.players:
        if player.is_human:
            continue
        try:
            pid = personality_repo.resolve_name_to_personality_id(player.name)
        except Exception:
            pid = None
        if pid:
            cash_personality_ids[player.name] = pid
        else:
            logger.warning("could not resolve personality_id for AI %r — its bankroll won't be credited", player.name)

    owner_info = game_repo.get_game_owner_info(game_id) or {}
    cs = cash_session_repo.load(game_id) if cash_session_repo else None

    game_data: Dict[str, Any] = {
        "state_machine": state_machine,
        "owner_id": owner_info.get("owner_id"),
        "owner_name": owner_info.get("owner_name"),
        "cash_mode": True,
        "cash_stake_label": stake_label,
        "cash_personality_ids": cash_personality_ids,
        "messages": [],
        "ai_controllers": {},
        "sandbox_id": cs.sandbox_id if cs else None,
        "cash_buy_in": cs.total_buy_in if cs else 0,
        "cash_table_id": cs.cash_table_id if cs else None,
        "cash_seat_index": cs.cash_seat_index if cs else None,
        "game_started": True,
        "last_announced_phase": None,
        "hand_start_stacks": {p.name: p.stack for p in state_machine.game_state.players},
        "short_stack_players": set(),
    }
    game_state_service.set_game(game_id, game_data)
    logger.info(
        "warmed game %s | players=%s | stake=%s | sandbox=%s | cash_table_id=%s",
        game_id,
        [(p.name, p.is_human, p.stack) for p in state_machine.game_state.players],
        stake_label,
        game_data["sandbox_id"],
        game_data["cash_table_id"],
    )
    return True


def run_leave(app, game_id: str, owner_id: str) -> dict:
    """Invoke `_leave_table_locked` exactly as the route would.

    The route normally wraps the call in `with game_state_service.get_game_lock(game_id):`
    — since we're single-threaded here we mirror that for paranoia.
    The route returns a Flask `Response`; we extract its JSON body so
    the caller can log + verify.
    """
    from flask_app.routes.cash_routes import _leave_table_locked
    from flask_app.services import game_state_service

    lock = game_state_service.get_game_lock(game_id)
    with lock:
        with app.test_request_context():  # leave path uses jsonify
            response = _leave_table_locked(owner_id, game_id)
    body = response.get_json() if hasattr(response, "get_json") else json.loads(response.data)
    return body


def sweep_ghost_seats(owner_id: str, sandbox_id: str) -> int:
    """Workaround for the nested-if bug at `cash_routes.py:4256`.

    `_leave_table_locked` only calls `_free_ghost_human_seats` inside
    `if cash_table_id is not None:`. Sessions whose `cash_sessions.cash_table_id`
    is NULL (sponsor-and-sit gap at `cash_routes.py:1976`) skip that
    sweep entirely, leaving a "human" slot on `cash_tables` pinned to
    the owner. We run the same helper explicitly here.
    """
    from flask_app.routes.cash_routes import _free_ghost_human_seats

    freed = _free_ghost_human_seats(owner_id, sandbox_id=sandbox_id)
    logger.info("explicit ghost-seat sweep freed %d seat(s) for owner=%s", freed, owner_id)
    return freed


def post_state(conn: sqlite3.Connection, game_id: str, owner_id: str) -> Dict[str, Any]:
    conn.row_factory = sqlite3.Row
    out: Dict[str, Any] = {}
    out["games_row"] = list(conn.execute("SELECT game_id FROM games WHERE game_id = ?", (game_id,)))
    out["cash_session_closed_status"] = list(
        conn.execute("SELECT session_id, closed_status, ended_at FROM cash_sessions WHERE session_id = ?", (game_id,))
    )
    out["stake_status"] = list(
        conn.execute("SELECT stake_id, status, carry_amount FROM stakes WHERE session_id = ?", (game_id,))
    )
    out["remaining_cash_rows"] = list(
        conn.execute("SELECT game_id, updated_at FROM games WHERE game_id LIKE 'cash-%' AND owner_id = ?", (owner_id,))
    )
    out["remaining_human_seats"] = list(
        conn.execute("SELECT table_id FROM cash_tables WHERE seats_json LIKE ?", (f"%{owner_id}%",))
    )
    out["player_bankroll"] = list(
        conn.execute("SELECT player_id, chips FROM player_bankroll_state WHERE player_id = ?", (owner_id,))
    )
    return {k: [dict(r) for r in v] for k, v in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Clean up an orphan cash session via the production leave path.")
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--owner-id", required=True)
    ap.add_argument("--dry-run", action="store_true", help="Snapshot only — don't run the leave path or sweep.")
    args = ap.parse_args()

    sys.path.insert(0, "/app")
    from flask_app import create_app

    conn = sqlite3.connect(DB_PATH)
    snapshot = snapshot_rollback(conn, args.game_id, args.owner_id)
    logger.info("pre-cleanup snapshot: games=%d cash_sessions=%d stakes=%d seated_tables=%d",
                len(snapshot["games"]),
                len(snapshot["cash_sessions"]),
                len(snapshot["stakes"]),
                len(snapshot["cash_tables_with_owner_seat"]))
    if not snapshot["games"]:
        logger.error("no games row for %s — nothing to clean", args.game_id)
        return 1
    rollback_path = write_rollback(snapshot)

    if args.dry_run:
        logger.info("--dry-run: skipping mutate steps. rollback at %s", rollback_path)
        return 0

    app = create_app()
    sandbox_id = None
    with app.app_context():
        if not warm_game_into_memory(app, args.game_id):
            logger.error("warm-into-memory failed; aborting")
            return 2

        from flask_app.services import game_state_service
        gd = game_state_service.get_game(args.game_id)
        sandbox_id = gd.get("sandbox_id") if gd else None

        result = run_leave(app, args.game_id, args.owner_id)
        logger.info("leave returned: %s", json.dumps(result, indent=2, sort_keys=True))

        if sandbox_id:
            sweep_ghost_seats(args.owner_id, sandbox_id)
        else:
            logger.warning("no sandbox_id resolved — skipping explicit ghost-seat sweep")

    conn = sqlite3.connect(DB_PATH)
    after = post_state(conn, args.game_id, args.owner_id)
    logger.info("post-cleanup state: %s", json.dumps(after, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
