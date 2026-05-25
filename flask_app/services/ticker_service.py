"""Realtime background world ticker for cash mode.

A single shared background task advances the unseated-table world for
every *active* sandbox (see `presence.py`) on a fixed cadence —
independent of whether the player is in the lobby or seated at a table.
This replaces the old read-driven model where the world only moved when
`GET /api/cash/lobby` was polled.

Design (see `docs/plans/CASH_MODE_REALTIME_TICKER.md`):

- **One thread, not one-per-session.** The GIL serializes the pure-Python
  sim anyway, and a single writer avoids SQLite write-lock contention.
- **Time-budgeted.** Each cycle spends at most `CYCLE_BUDGET_MS` across
  all active sandboxes, round-robining so none is starved. Under load the
  world slows gracefully instead of starving foreground request handling.
- **Cooperative yield** between sandboxes (`socketio.sleep(0)`).
- **Per-user pace** maps to the `hand_sim_prob` (and cadence) passed to
  `refresh_unseated_tables`.
- After each sandbox tick, pushes `lobby_tick` + new `world_event`s to the
  per-user lobby room so the client refreshes / shows signals without
  polling.

Seeding (`ensure_lobby_seeded`) stays a lobby-GET responsibility — the
ticker only advances tables that already exist, so a user who opened a
socket but never loaded the lobby costs nothing here.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# Cadence + budget. BASE_TICK is the wall-clock spacing between cycles;
# CYCLE_BUDGET caps how long one cycle spends running sims so the ticker
# can never monopolize the single worker's core.
BASE_TICK_SECONDS = 2.0
CYCLE_BUDGET_MS = 250.0
# Max new ticker events scanned/pushed per sandbox per tick (cosmetic).
WORLD_EVENT_LIMIT = 20

# pace -> (hand_sim_prob, run_every_n_cycles). `run_every` lets the
# quietest pace tick less often without a separate timer. With a 2s base
# tick, the per-table mean interval between hands is
# (run_every * BASE_TICK) / prob:
#   subtle   -> (3*2)/0.15 ≈ 40s   (ambient backdrop; world barely drifts)
#   lively   -> (1*2)/0.40 = 5s    (busy but followable; default)
#   bustling -> (1*2)/0.90 ≈ 2.2s  (Vegas-floor churn)
# Per table — the lobby's aggregate feed moves ~Ntables faster.
_PACE_PARAMS: Dict[str, Tuple[float, int]] = {
    "subtle": (0.15, 3),
    "lively": (0.40, 1),
    "bustling": (0.90, 1),
}
_DEFAULT_PACE = "lively"


def is_enabled() -> bool:
    """Whether the realtime ticker should run (default on).

    When off (`WORLD_TICKER_ENABLED=false`), `GET /api/cash/lobby` keeps
    its legacy read-driven `refresh_unseated_tables` call — the safety
    fallback so the world still moves without the ticker.
    """
    return os.environ.get("WORLD_TICKER_ENABLED", "true").lower() != "false"


_started = False
_start_lock = threading.RLock()
_stop = threading.Event()
# owner_id -> created_at of the newest world_event we've already pushed,
# so we only emit events generated since the last tick (no backlog spam).
_last_marker: Dict[str, str] = {}
_cycle = 0
_rr_offset = 0

# Net-worth snapshot cadence. The ticker records a holdings snapshot per
# active sandbox at most this often (wall-clock), driving the admin
# "Player Holdings" net-worth-over-time chart. Far slower than the base
# tick — net worth drifts on the order of minutes, and the table is just
# for admin analytics, so a fine cadence would only bloat the table.
SNAPSHOT_INTERVAL_SECONDS = 600.0
# sandbox_id -> monotonic time of its last recorded snapshot.
_last_snapshot_at: Dict[str, float] = {}


def start_world_ticker(socketio) -> None:
    """Start the shared ticker once. Idempotent across create_app() calls."""
    global _started
    if not is_enabled():
        logger.info("[TICKER] world ticker disabled via WORLD_TICKER_ENABLED")
        return
    with _start_lock:
        if _started:
            return
        _started = True
        _stop.clear()
        socketio.start_background_task(_run, socketio)
        logger.info(
            "[TICKER] world ticker started (tick=%.1fs budget=%.0fms)",
            BASE_TICK_SECONDS,
            CYCLE_BUDGET_MS,
        )


def stop_world_ticker() -> None:
    """Signal the ticker loop to exit. For tests / graceful shutdown."""
    global _started
    with _start_lock:
        _stop.set()
        _started = False


def _run(socketio) -> None:
    """The ticker loop. Runs until `stop_world_ticker()` is called."""
    global _cycle, _rr_offset
    while not _stop.is_set():
        socketio.sleep(BASE_TICK_SECONDS)
        if _stop.is_set():
            break
        _cycle += 1
        try:
            _run_cycle(socketio)
        except Exception:
            # One bad cycle must never kill the loop.
            logger.exception("[TICKER] cycle failed")


def _run_cycle(socketio) -> None:
    """Advance every active sandbox once, within the time budget."""
    global _rr_offset
    from flask_app.services import presence

    sessions = presence.active_sessions()
    if not sessions:
        # Prune markers for owners no longer active so the dict can't grow
        # unbounded over a long uptime.
        _last_marker.clear()
        return

    # Rotate the work list so a budget cutoff doesn't always starve the
    # same tail sandboxes across cycles.
    if sessions:
        _rr_offset = (_rr_offset + 1) % len(sessions)
        sessions = sessions[_rr_offset:] + sessions[:_rr_offset]

    active_owners = {s.owner_id for s in sessions}
    for stale in [o for o in _last_marker if o not in active_owners]:
        _last_marker.pop(stale, None)

    cycle_start = time.monotonic()
    for session in sessions:
        if (time.monotonic() - cycle_start) * 1000.0 > CYCLE_BUDGET_MS:
            break  # defer the rest to the next cycle
        try:
            _tick_sandbox(socketio, session.owner_id, session.sandbox_id)
        except Exception:
            logger.exception("[TICKER] tick failed for owner=%s", session.owner_id)
        socketio.sleep(0)  # cooperative yield between sandboxes


def _resolve_pace(owner_id: str) -> Tuple[float, int]:
    """Look up the owner's pace params, defaulting on any failure."""
    from flask_app import extensions

    repo = getattr(extensions, "user_prefs_repo", None)
    pace = _DEFAULT_PACE
    if repo is not None:
        try:
            pace = repo.get_world_pace(owner_id)
        except Exception:
            pace = _DEFAULT_PACE
    return _PACE_PARAMS.get(pace, _PACE_PARAMS[_DEFAULT_PACE])


def _tick_sandbox(socketio, owner_id: str, sandbox_id: str) -> None:
    """Run one world-advancing refresh for a sandbox + push the deltas."""
    from cash_mode.activity import recent_events, serialize_event
    from cash_mode.lobby import refresh_unseated_tables
    from flask_app import extensions
    from flask_app.services import presence

    hand_sim_prob, run_every = _resolve_pace(owner_id)
    if run_every > 1 and (_cycle % run_every) != 0:
        return  # quiet pace: skip this cycle

    # Baseline the event marker on first sight so we don't replay the
    # ring-buffer backlog the moment a user becomes active.
    if owner_id not in _last_marker:
        existing = recent_events(limit=1, sandbox_id=sandbox_id)
        _last_marker[owner_id] = existing[0].created_at if existing else ""

    refresh_unseated_tables(
        cash_table_repo=extensions.cash_table_repo,
        personality_repo=extensions.personality_repo,
        bankroll_repo=extensions.bankroll_repo,
        user_id=owner_id,
        sandbox_id=sandbox_id,
        hand_sim_prob=hand_sim_prob,
        chip_ledger_repo=extensions.chip_ledger_repo,
        relationship_repo=extensions.relationship_repo,
        stake_repo=extensions.stake_repo,
        vice_repo=extensions.vice_state_repo,
        side_hustle_repo=extensions.side_hustle_state_repo,
    )

    _maybe_record_holdings_snapshot(sandbox_id)

    room = presence.lobby_room_name(owner_id)
    # Push new ticker events (newest-first from the buffer; emit oldest
    # first so the client appends in chronological order).
    prev_marker = _last_marker.get(owner_id, "")
    fresh = [
        e
        for e in recent_events(limit=WORLD_EVENT_LIMIT, sandbox_id=sandbox_id)
        if e.created_at > prev_marker
    ]
    if fresh:
        _last_marker[owner_id] = fresh[0].created_at
        for event in reversed(fresh):
            socketio.emit("world_event", serialize_event(event), to=room)

    # Lightweight nudge so a mounted lobby refetches the snapshot.
    socketio.emit("lobby_tick", {"sandbox_id": sandbox_id, "ts": time.time()}, to=room)


def _maybe_record_holdings_snapshot(sandbox_id: str) -> None:
    """Record a net-worth snapshot for this sandbox, rate-limited.

    Captures at most once per `SNAPSHOT_INTERVAL_SECONDS` per sandbox so
    the admin "Player Holdings" chart has real net-worth-over-time points.
    Best-effort: any failure is logged and swallowed — snapshotting must
    never delay or break the world tick.
    """
    now = time.monotonic()
    last = _last_snapshot_at.get(sandbox_id)
    if last is not None and (now - last) < SNAPSHOT_INTERVAL_SECONDS:
        return
    try:
        from flask_app import extensions
        from flask_app.services.holdings_view import record_holdings_snapshot

        repo = getattr(extensions, "holdings_snapshots_repo", None)
        if repo is None:
            return
        # Stamp the attempt BEFORE recording: a persistently failing snapshot
        # (DB lock, schema mismatch) must back off to the normal cadence, not
        # retry the full N+1 computation on every 2s tick.
        _last_snapshot_at[sandbox_id] = now
        record_holdings_snapshot(
            snapshots_repo=repo,
            bankroll_repo=extensions.bankroll_repo,
            personality_repo=extensions.personality_repo,
            user_repo=extensions.user_repo,
            stake_repo=extensions.stake_repo,
            db_path=extensions.persistence_db_path,
            sandbox_id=sandbox_id,
        )
    except Exception:
        logger.exception("[TICKER] holdings snapshot failed for sandbox=%s", sandbox_id)
