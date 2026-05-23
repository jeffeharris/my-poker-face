"""Bankroll state dataclasses and projection-on-read regen.

Two persisted bankroll surfaces — one per AI personality, one per
human player — plus the pure `project_bankroll` function used to
compute live values on read without a background timer. Same pattern
as `project_heat` on the relationship layer:

  Stored value = "bankroll as of last_regen_tick"
  Read value   = stored + (elapsed * rate), clamped to cap

Persistence writes only happen on real events (sit-down, win, loss);
reads always project through elapsed wall-clock time.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2
  §"Bankroll regen (pure projection on read)" and
  §"Bankroll knob storage".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AIBankrollState:
    """Per-personality persistent bankroll.

    Keyed by `personality_id` (the stable v85 slug, not display name).
    Survives sessions, games, and personality renames.

    `last_regen_tick` is the wall-clock anchor for projection. On
    every write (sit-down, win, loss), the caller projects the live
    value, snaps `chips` to that projected value, and resets
    `last_regen_tick = now`. Subsequent reads project from `now`
    again.

    `last_regen_tick == None` means no event has ever been recorded;
    `project_bankroll` returns the stored `chips` verbatim in that
    case (typically the starting grant for a freshly seeded AI).
    """

    personality_id: str
    chips: int
    last_regen_tick: Optional[datetime] = None


@dataclass
class PlayerBankrollState:
    """Per-player persistent bankroll.

    Player bankrolls do **not** regen in v1 — the player picks a
    sponsor at `/cash` entry when bankroll falls below the cheapest
    table's min buy-in, and no passive refill happens. The dataclass
    shape matches `AIBankrollState` for symmetry but the projection
    function isn't called for players.

    `starting_bankroll` is the seed grant for first-time entry. Kept
    per-row so future staking / character progression can alter it
    without a schema migration.

    Active stakes live in the `stakes` table (`StakeRepository`), not
    on this dataclass — the legacy `active_loan_*` columns were
    dropped in Cleanup B of the backing-system handoff after the
    stakes-table cutover finished. See
    `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` for the stake
    model that replaced them.
    """

    player_id: str
    chips: int
    starting_bankroll: int


@dataclass
class BankrollKnobs:
    """Per-personality bankroll behavior knobs.

    Stored in `personalities.config_json.bankroll_knobs`. Read at
    table sit-down to decide buy-in size and eligibility.

    `stake_comfort_zone` is the friendly stake label ("$10",
    "$50", ...) the AI prefers when multiple are affordable. v1 has
    no selection problem (one table), so it's persisted-but-unused
    until the v2 multi-table lobby lands.
    """

    starting_bankroll: int
    bankroll_rate: int
    buy_in_multiplier: float
    stake_comfort_zone: str


# Defaults used when a personality has no per-row override yet.
# v1 ships uniform defaults; per-personality tuning is a follow-up
# (just populate the columns from personalities.json).
BANKROLL_KNOB_DEFAULTS = BankrollKnobs(
    starting_bankroll=10_000,
    bankroll_rate=500,
    buy_in_multiplier=1.0,
    stake_comfort_zone="$10",
)


def project_bankroll(
    state: AIBankrollState,
    starting_bankroll: int,
    rate: int,
    now: datetime,
) -> int:
    """Project bankroll chips through elapsed time.

    Pure function. Returns the value `chips` would currently have
    given the time elapsed since the last mutation. Does not mutate
    `state`.

    `starting_bankroll` is the regen *target*, not a cap:
      - When chips are below it, passive regen accrues at `rate/day`,
        not overshooting the target.
      - When chips are at or above it (the AI has won their way past
        their character's "natural wealth"), regen is dormant and
        chips are returned verbatim. Winnings above starting_bankroll
        are kept — there is no ceiling.

    Same pattern as `project_heat` for the below-target case.
    Persistence writes only on real events; reads always project.
    The fractional-day floor uses `int(...)` so projection is
    monotonic per day boundary — a half-second after `last_regen_tick`
    reads as the stored value, not a no-op `+0`.

    `last_regen_tick == None` is the no-event-yet state for freshly
    seeded AI personalities; the function returns stored `chips`
    verbatim so the seed value isn't immediately inflated.

    When `economy_flags.REGEN_ENABLED` is False, the passive faucet
    is off — returns stored chips unchanged.
    """
    from cash_mode import economy_flags
    if state.last_regen_tick is None:
        return state.chips
    if state.chips >= starting_bankroll:
        return state.chips
    if not economy_flags.REGEN_ENABLED:
        return state.chips
    elapsed_days = (now - state.last_regen_tick).total_seconds() / 86400.0
    projected = state.chips + int(rate * elapsed_days)
    return min(starting_bankroll, projected)


def credit_ai_cash_out(
    bankroll_repo,
    personality_id: str,
    player_stack: int,
    *,
    sandbox_id: Optional[str] = None,
    now: Optional[datetime] = None,
    chip_ledger_repo=None,
    ledger_context: Optional[dict] = None,
) -> Optional[AIBankrollState]:
    """Credit `player_stack` chips back to an AI's persistent bankroll.

    Leave-time accounting: project the stored bankroll forward through
    elapsed time (passive regen toward `starting_bankroll` when chips
    are below target, dormant when at or above), then add the AI's
    current table stack as winnings. **No cap on the upside** —
    `starting_bankroll` is the regen *target*, not a ceiling, so an
    AI who wins above their character's natural wealth keeps the
    winnings and can buy into higher stakes next session.

    Always writes a fresh row when one doesn't exist (defensive seam:
    if the seed/debit chain skipped the AI, the credit path creates
    the row so the regen clock starts). A row with `chips=0,
    last_regen_tick=now` is preferable to no row — the latter strands
    the AI forever because every lookup returns None.

    For an existing row, writes `chips=projected + max(0,
    player_stack), last_regen_tick=now`. `player_stack <= 0`
    no longer short-circuits: committing regen on a bust-out leave
    advances the tick so passive regen starts accruing from `now`,
    not from the prior sit-down. Without this, AIs that lose
    everything sit at 0 forever.

    `bankroll_repo` is the live `BankrollRepository` instance — taken
    as a parameter (rather than the module-level singleton) so tests
    can pass a tempdb-backed instance without monkey-patching the
    flask_app.extensions module.

    `chip_ledger_repo` (optional) opts the call into ledger
    instrumentation. When provided, the regen portion of the write
    fires an `ai_regen` entry. (The legacy `cap_clamp` destruction
    path was removed when `starting_bankroll` semantics shifted from
    ceiling to target — winnings are kept, not evaporated.)
    """
    if now is None:
        now = datetime.utcnow()
    effective_stack = max(0, player_stack)
    try:
        stored = bankroll_repo.load_ai_bankroll(
            personality_id,
            sandbox_id=sandbox_id,
        )
    except TypeError as e:
        if "sandbox_id" not in str(e):
            raise
        stored = bankroll_repo.load_ai_bankroll(personality_id)
    knobs = bankroll_repo.load_personality_knobs(personality_id)
    if stored is None:
        # First-write seam — write a row so the regen clock can begin.
        # `save_ai_bankroll`'s first-write hook emits an `ai_seed`
        # ledger entry for the chips landing here, keeping the audit
        # balanced. No cap on the first-write either; the AI gets
        # exactly what they brought back from the seat.
        new_state = AIBankrollState(
            personality_id=personality_id,
            chips=effective_stack,
            last_regen_tick=now,
        )
        try:
            bankroll_repo.save_ai_bankroll(
                new_state, sandbox_id=sandbox_id,
                chip_ledger_repo=chip_ledger_repo,
            )
        except TypeError as e:
            if "sandbox_id" not in str(e):
                raise
            bankroll_repo.save_ai_bankroll(new_state)
        logger.info(
            "[CASH] AI cash-out (first-write) %r: +%d → %d",
            personality_id, effective_stack, effective_stack,
        )
        return new_state
    projected = project_bankroll(stored, knobs.starting_bankroll, knobs.bankroll_rate, now)
    new_chips = projected + effective_stack
    new_state = AIBankrollState(
        personality_id=personality_id,
        chips=new_chips,
        last_regen_tick=now,
    )
    try:
        bankroll_repo.save_ai_bankroll(new_state, sandbox_id=sandbox_id)
    except TypeError as e:
        if "sandbox_id" not in str(e):
            raise
        bankroll_repo.save_ai_bankroll(new_state)
    if chip_ledger_repo is not None:
        from core.economy import ledger as chip_ledger
        ctx = {'site': 'credit_ai_cash_out', 'sandbox_id': sandbox_id}
        if ledger_context:
            ctx.update(ledger_context)
        chip_ledger.record_ai_regen(
            chip_ledger_repo,
            personality_id=personality_id,
            stored_chips=stored.chips,
            projected_chips=projected,
            context=ctx,
            sandbox_id=sandbox_id,
        )
    logger.info(
        "[CASH] AI cash-out %r: +%d (projected=%d) → %d",
        personality_id, effective_stack, projected, new_chips,
    )
    return new_state


def debit_bankroll_for_seat(
    bankroll_repo,
    personality_id: str,
    amount: int,
    *,
    sandbox_id: Optional[str] = None,
) -> Optional[AIBankrollState]:
    """Pure transfer: move chips from an AI's bankroll to a cash table seat.

    No ledger entry — `ai_bankrolls_stored` decreases and
    `cash_table_seats_ai` increases by the same amount, so the audit's
    `actual_outstanding` is preserved. Symmetric pair to the
    seat → bankroll credit path which goes through `credit_ai_cash_out`
    (which DOES write ledger entries, because it commits regen and may
    cap-clamp).

    Preserves `last_regen_tick` — doesn't commit any pending regen at
    debit time. Uncommitted regen catches up at the next credit-side
    write or read.

    Called when:
      - Lobby seed fills a fresh AI seat at boot.
      - `refresh_table_roster`'s live-fill step seats an AI from the
        idle pool or eligible-never-seated pool.

    Defensively clamps the new stored chip count at 0 — bankroll
    eligibility checks (`bankroll_lookup` callbacks in
    refresh_table_roster) should keep this from ever firing, but a
    negative bankroll would silently break the audit invariant.
    Returns the persisted state or None if no row exists.

    KNOWN LEAK (audit drift): when `stored.chips < amount`, the
    clamp creates `amount - stored.chips` phantom chips at the seat
    without debiting them from any bankroll — a primary source of
    audit drift. Logs a WARNING so the leak is observable. Proper
    fix is one of:
      (a) Commit pending regen before debit (threads chip_ledger_repo
          through every caller — drives projected to stored), or
      (b) Refuse the debit AND have callers unwind the pre-placed
          seat on None.
    Tracked as a follow-up; the warning is the diagnostic seam.
    """
    try:
        stored = bankroll_repo.load_ai_bankroll(
            personality_id,
            sandbox_id=sandbox_id,
        )
    except TypeError as e:
        if "sandbox_id" not in str(e):
            raise
        stored = bankroll_repo.load_ai_bankroll(personality_id)
    if stored is None:
        logger.warning(
            "[CASH] seat debit skipped — no bankroll row for %r",
            personality_id,
        )
        return None
    # Diagnostic seam: when stored < amount, the clamp below creates
    # `amount - stored.chips` phantom chips at the seat (caller has
    # already placed the seat with `amount` chips before calling).
    # Logging here makes the leak observable in run logs. Upstream
    # eligibility checks should make this branch unreachable.
    if stored.chips < amount:
        logger.warning(
            "[CASH][AUDIT] debit clamp leak: pid=%s sandbox=%s "
            "stored=%d amount=%d leak=+%d — bankroll_lookup let "
            "this through; investigate",
            personality_id, sandbox_id, stored.chips, amount,
            amount - stored.chips,
        )
    new_chips = max(0, stored.chips - amount)
    new_state = AIBankrollState(
        personality_id=personality_id,
        chips=new_chips,
        last_regen_tick=stored.last_regen_tick,
    )
    try:
        bankroll_repo.save_ai_bankroll(new_state, sandbox_id=sandbox_id)
    except TypeError as e:
        if "sandbox_id" not in str(e):
            raise
        bankroll_repo.save_ai_bankroll(new_state)
    return new_state
