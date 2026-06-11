"""Intake world warm-up — pre-warm a new player's hidden world.

A brand-new sandbox's lobby is seeded but **cold**: the world ticker plays only
~1 hand/table per couple seconds, so at Scene-0 graduation the hidden cardrooms
have almost no history (fresh relationships, an unmoved economy, AIs still in
their seed seats). This fires a short, bounded, **deterministic, no-LLM** sim
burst across those hidden tables when intake completes, so by the time the player
graduates the world feels lived-in — AI↔AI relationships have texture, chips have
moved, and AIs have redistributed across rooms.

It reuses the exact economy path the ticker + offline sim use
(`refresh_unseated_tables`), so it's chip-conserving by construction (no mint).
The pinned Scene-0 table is `table_type='scripted'` and excluded from refresh, so
the burst never touches the player's tutorial. AI→human regard does NOT move (the
human isn't in these hands) — consistent with the social-accrual vouch model.

Design + decisions: `docs/plans/CASH_MODE_INTAKE_WORLD_WARMUP.md`.
Flag: `economy_flags.INTAKE_WORLD_WARMUP_ENABLED` (default ON; inert unless
`CAREER_PROGRESSION_ENABLED`, since nothing reaches intake without it).
One-shot per sandbox via `CareerProgress.world_warmed`.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# "Moderate — reads form." Each iteration runs one `refresh_unseated_tables`
# pass, which plays ~1 hand per unseated table (at hand_sim_prob=1.0). ~30
# iterations over ~10 hidden tables ≈ ~30 hands/table ≈ enough for opponent
# models to populate and the economy to shift, without overshooting "a little
# action". A tunable starting point — adjust from playtest.
WARMUP_ITERATIONS = 30
# Wall-clock backstop so a slow box can never let the burst run long. At ~4ms a
# hand the budget is generous; this is a safety cap, not the usual exit.
WARMUP_MAX_SECONDS = 5.0


def warm_up_world(
    sandbox_id: str,
    *,
    iterations: int = WARMUP_ITERATIONS,
    max_seconds: float = WARMUP_MAX_SECONDS,
    sleep: Optional[Callable[[float], None]] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
) -> dict:
    """Run the bounded warm-up burst for one sandbox (synchronous core).

    Loops `refresh_unseated_tables` up to `iterations` times (or until
    `max_seconds` elapses), each pass under the per-sandbox seat lock so it
    serializes cleanly with route-side seat claims and the live ticker — then
    yields between passes (`sleep`) so it never monopolizes the worker. No LLM
    (vice/hustle narration is templated; no async narration scheduler).

    Best-effort: any per-pass error is logged and skipped; the burst must never
    break the request that scheduled it. `sleep`/`now_fn` are injectable so tests
    can drive it without socketio / real time. Returns a small result summary.
    """
    from cash_mode import economy_flags
    from cash_mode.lobby import refresh_unseated_tables
    from flask_app import extensions
    from flask_app.services import game_state_service

    cash_table_repo = getattr(extensions, "cash_table_repo", None)
    personality_repo = getattr(extensions, "personality_repo", None)
    bankroll_repo = getattr(extensions, "bankroll_repo", None)
    if cash_table_repo is None or personality_repo is None or bankroll_repo is None:
        return {"iterations_run": 0, "elapsed_s": 0.0, "skipped": "repos_unavailable"}

    now_fn = now_fn or datetime.utcnow
    started = time.monotonic()
    ran = 0
    for i in range(max(0, iterations)):
        if time.monotonic() - started >= max_seconds:
            break
        try:
            with game_state_service.get_sandbox_lock(sandbox_id):
                refresh_unseated_tables(
                    cash_table_repo=cash_table_repo,
                    personality_repo=personality_repo,
                    bankroll_repo=bankroll_repo,
                    sandbox_id=sandbox_id,
                    now=now_fn(),
                    hand_sim_prob=1.0,
                    chip_ledger_repo=getattr(extensions, "chip_ledger_repo", None),
                    relationship_repo=getattr(extensions, "relationship_repo", None),
                    stake_repo=getattr(extensions, "stake_repo", None),
                    vice_repo=getattr(extensions, "vice_state_repo", None),
                    side_hustle_repo=getattr(extensions, "side_hustle_state_repo", None),
                    prestige_snapshots_repo=getattr(extensions, "prestige_snapshots_repo", None),
                    human_headroom=economy_flags.LIVE_FILL_HUMAN_HEADROOM,
                    # No LLM: vice/hustle narrate via the templated narrator and
                    # there's no async narration scheduler (the ticker's LLM path).
                    vice_use_llm_narration=False,
                    hustle_use_llm_narration=False,
                )
            ran += 1
        except Exception:
            logger.debug("[WARMUP] refresh pass %d failed (non-fatal)", i, exc_info=True)
        if sleep is not None:
            sleep(0)

    elapsed = time.monotonic() - started
    logger.info(
        "[WARMUP] sandbox=%s warmed: %d/%d passes in %.2fs",
        sandbox_id,
        ran,
        iterations,
        elapsed,
    )
    return {"iterations_run": ran, "elapsed_s": elapsed}


def schedule_warm_up(sandbox_id: str, owner_id: str) -> bool:
    """Fire the one-shot warm-up burst for a sandbox as a background task.

    The route-facing entry. Checks the flags + the `world_warmed` one-shot guard,
    stamps the guard (so a reconnect / lobby reload never re-fires), and spawns
    `warm_up_world` on a socketio background greenlet. Returns True if scheduled.
    Best-effort — never raises into the caller (intake must not fail on this).
    """
    from cash_mode import economy_flags
    from flask_app import extensions

    if not (economy_flags.CAREER_PROGRESSION_ENABLED and economy_flags.INTAKE_WORLD_WARMUP_ENABLED):
        return False
    repo = getattr(extensions, "career_progress_repo", None)
    if repo is None:
        return False
    try:
        progress = repo.load(sandbox_id, owner_id)
        if progress.world_warmed:
            return False
        # Stamp BEFORE spawning so a concurrent load can't double-schedule; a
        # crashed burst simply doesn't retry (best-effort warm-up, not critical).
        progress.world_warmed = True
        repo.save(progress)
        extensions.socketio.start_background_task(
            warm_up_world, sandbox_id, sleep=extensions.socketio.sleep
        )
        return True
    except Exception:
        logger.warning("[WARMUP] schedule failed for sandbox=%s", sandbox_id, exc_info=True)
        return False
