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
from dataclasses import dataclass
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
    from_seat: bool = True,
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

    `from_seat` (default True) is the chip-custody discriminator for this
    overloaded helper. When True, the credit is a real seat cash-out and
    (under `CHIP_CUSTODY_ENABLED`) records a `seat:ai → ai` transfer so the
    AI's at-table chips settle back into bankroll via the ledger. When False,
    the credit is a stake/carry payoff (no seat behind it) — the caller is
    responsible for recording the `stake_payoff` transfer for the funding
    source, so this helper records no seat transfer. Only matters when
    `chip_ledger_repo` is provided and custody is enabled.
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
                new_state,
                sandbox_id=sandbox_id,
                chip_ledger_repo=chip_ledger_repo,
            )
        except TypeError as e:
            if "sandbox_id" not in str(e):
                raise
            bankroll_repo.save_ai_bankroll(new_state)
        logger.info(
            "[CASH] AI cash-out (first-write) %r: +%d → %d",
            personality_id,
            effective_stack,
            effective_stack,
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
        from cash_mode import economy_flags
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
        # Chip-custody parity (AI side of Cut 2): settle the AI's table stack
        # back into bankroll as a `seat → ai` transfer. Only for real seat
        # cash-outs (`from_seat`); stake/carry payoffs record `stake_payoff` at
        # their call site instead. Conservation-neutral — the bankroll int
        # already rose by `effective_stack` above. No row on a bust
        # (effective_stack == 0), matching the human convention.
        if (
            from_seat
            and sandbox_id is not None
            and economy_flags.CHIP_CUSTODY_ENABLED
            and effective_stack > 0
        ):
            chip_ledger.record_ai_cash_out(
                chip_ledger_repo,
                personality_id=personality_id,
                sandbox_id=sandbox_id,
                amount=effective_stack,
                context=ctx,
            )
    logger.info(
        "[CASH] AI cash-out %r: +%d (projected=%d) → %d",
        personality_id,
        effective_stack,
        projected,
        new_chips,
    )
    return new_state


def settle_ai_bankroll_to_pool_on_delete(
    personality_id: str,
    *,
    bankroll_repo,
    chip_ledger_repo,
    now: Optional[datetime] = None,
) -> int:
    """Return a soon-to-be-deleted AI's bankroll chips (EVERY sandbox) to the
    bank pool, so persona deletion is conservation-safe — the chip-custody
    deletion-integrity hook (Phase 5).

    Deleting a personality row drops its `ai_bankroll_state` rows; if those
    held chips, the chips vanish from the ledger's view (drift) and from the
    closed economy (the recurring zombie-persona bug class). Instead, for each
    sandbox the AI has chips in, record a `casino_seat_return` (ai → bank pool —
    the established conservation-safe AI-removal mechanism) and zero the row, so
    the chips recycle back into the pool that funds future AIs.

    Gated on CHIP_CUSTODY_ENABLED + both repos present. Returns the total chips
    returned. Best-effort per sandbox — one bad row doesn't abort the rest.
    Caller should run this BEFORE `delete_personality`.
    """
    from cash_mode import economy_flags

    if (
        not economy_flags.CHIP_CUSTODY_ENABLED
        or bankroll_repo is None
        or chip_ledger_repo is None
        or not personality_id
    ):
        return 0
    if now is None:
        now = datetime.utcnow()
    from core.economy import ledger as chip_ledger

    pairs = [
        (pid, sb)
        for (pid, sb) in bankroll_repo.iter_personality_ids_with_bankrolls_by_sandbox()
        if pid == personality_id
    ]
    total = 0
    for pid, sandbox_id in pairs:
        try:
            state = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
        except TypeError:
            state = bankroll_repo.load_ai_bankroll(pid)
        chips = int(state.chips) if state is not None else 0
        if chips <= 0:
            continue
        row_id = chip_ledger.record_casino_seat_return(
            chip_ledger_repo,
            personality_id=pid,
            amount=chips,
            context={'site': 'persona_delete', 'sandbox_id': sandbox_id},
            sandbox_id=sandbox_id,
        )
        if row_id is None:
            logger.warning(
                "[CASH LIFECYCLE] persona-delete settle: ledger return REJECTED "
                "for %s sandbox=%s (%d chips) — NOT zeroing the row to avoid "
                "forfeiting them; needs operator attention",
                pid,
                sandbox_id,
                chips,
            )
            continue
        bankroll_repo.save_ai_bankroll(
            AIBankrollState(personality_id=pid, chips=0, last_regen_tick=now),
            sandbox_id=sandbox_id,
        )
        total += chips
        logger.info(
            "[CASH] persona-delete settle: returned %d chips to pool for %s "
            "sandbox=%s (conservation-safe deletion)",
            chips,
            pid,
            sandbox_id,
        )
    return total


def debit_bankroll_for_seat(
    bankroll_repo,
    personality_id: str,
    amount: int,
    *,
    sandbox_id: Optional[str] = None,
    chip_ledger_repo=None,
    now: Optional[datetime] = None,
) -> Optional[AIBankrollState]:
    """Atomic regen+debit: project bankroll forward, commit regen, debit `amount`.

    Pure transfer of `amount` chips from an AI's bankroll to a cash
    table seat. `cash_table_seats_ai` increases by `amount`;
    `ai_bankrolls_stored` decreases by `amount` minus any pending
    regen that gets committed here. The audit's `actual_outstanding`
    is preserved; the regen creation is the only ledger row.

    Called when:
      - Lobby seed fills a fresh AI seat at boot.
      - `refresh_table_roster`'s live-fill step seats an AI from the
        idle pool or eligible-never-seated pool.
      - `casino_provisioning` seats a fish after pool-funding its bankroll.
      - Player route sit-down.

    Three outcomes:
      - **Row missing**: return None (no bankroll to debit).
      - **Projected (stored + uncommitted regen) < amount**: refuse
        with None. Caller MUST unwind any pre-placed seat. This is
        the audit-safe replacement for the old clamp-to-zero that
        silently created `amount - stored.chips` phantom chips.
      - **Projected ≥ amount**: commit any pending regen via an
        `ai_regen` ledger entry (when `chip_ledger_repo` is provided),
        then write `chips = projected - amount, last_regen_tick = now`.
        `ai_regen` is the only ledger row; the debit itself is an
        internal transfer (bankroll → seat).

    When `chip_ledger_repo` is None the caller has opted out of regen
    instrumentation; the function falls back to comparing `stored`
    (not projected) against `amount` and skips the ledger row. This
    keeps tests and admin paths that don't have a ledger handle
    working without forcing them to construct one. Regen-eligible
    callers MUST pass the ledger repo so the audit balances.
    """
    if now is None:
        now = datetime.utcnow()
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

    # Two paths depending on whether the caller opted into regen
    # commit. Both refuse on insufficiency rather than clamping.
    if chip_ledger_repo is not None:
        knobs = bankroll_repo.load_personality_knobs(personality_id)
        projected = project_bankroll(
            stored,
            knobs.starting_bankroll,
            knobs.bankroll_rate,
            now,
        )
        if projected < amount:
            logger.warning(
                "[CASH] seat debit refused: pid=%s sandbox=%s "
                "stored=%d projected=%d amount=%d (shortfall=%d) — "
                "caller must unwind pre-placed seat",
                personality_id,
                sandbox_id,
                stored.chips,
                projected,
                amount,
                amount - projected,
            )
            return None
        # Commit pending regen as a ledger row so `ai_bankrolls_stored`
        # is allowed to grow from `stored.chips` to `projected` without
        # breaking conservation. No-op when projected == stored.
        from core.economy.ledger import record_ai_regen

        record_ai_regen(
            chip_ledger_repo,
            personality_id=personality_id,
            stored_chips=stored.chips,
            projected_chips=projected,
            context={'site': 'debit_bankroll_for_seat', 'sandbox_id': sandbox_id},
            sandbox_id=sandbox_id,
        )
        new_chips = projected - amount
    else:
        if stored.chips < amount:
            logger.warning(
                "[CASH] seat debit refused (no ledger): pid=%s "
                "sandbox=%s stored=%d amount=%d (shortfall=%d) — "
                "caller must unwind pre-placed seat OR pass "
                "chip_ledger_repo so pending regen can be committed",
                personality_id,
                sandbox_id,
                stored.chips,
                amount,
                amount - stored.chips,
            )
            return None
        new_chips = stored.chips - amount

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

    # Chip-custody parity (the AI side of Cut 2): record the buy-in as an
    # `ai → seat` transfer so the AI's at-table chips become a derivable ledger
    # balance, exactly as a human's are. Conservation-neutral — the bankroll int
    # already dropped by `amount` above; this just records WHERE it went. Gated
    # so the path is inert until the operator opts in, and needs the ledger repo
    # + a sandbox to key the seat account.
    from cash_mode import economy_flags

    if (
        chip_ledger_repo is not None
        and sandbox_id is not None
        and economy_flags.CHIP_CUSTODY_ENABLED
    ):
        from core.economy.ledger import record_ai_buy_in

        record_ai_buy_in(
            chip_ledger_repo,
            personality_id=personality_id,
            sandbox_id=sandbox_id,
            amount=amount,
            context={'site': 'debit_bankroll_for_seat', 'sandbox_id': sandbox_id},
        )
    return new_state
