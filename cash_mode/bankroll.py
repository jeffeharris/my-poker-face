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

    `active_loan_amount`, `active_loan_floor`, `active_loan_rate`
    encode the session-scoped sponsor loan when one is active (v89).
    Reset to 0/0.0/0.0 on `/api/cash/leave` after settlement.

      - `active_loan_amount`: principal in chips (0 = no loan)
      - `active_loan_floor`: repayment multiplier on the principal
        (e.g., 1.30 = player must repay 130% of principal from
        chips-at-table before any split kicks in)
      - `active_loan_rate`: sponsor's cut of post-floor remaining
        chips (e.g., 0.40 = 40% to sponsor, 60% to player)
      - `active_loan_lender_id`: personality_id of the AI lender
        (v90, Path B). NULL = anonymous house loan (v1 sponsorship);
        non-NULL = named AI personality whose persistent bankroll
        receives sponsor_total at leave-time. Reset to NULL on
        `/api/cash/leave` alongside the other loan fields.

    See `docs/plans/CASH_MODE_SPONSORSHIP_HANDOFF.md` for the
    leave-time math and the sponsor archetype pool that produces
    these triples, and `docs/plans/CASH_MODE_PATH_B_HANDOFF.md` for
    the AI-lender extension.
    """

    player_id: str
    chips: int
    starting_bankroll: int
    active_loan_amount: int = 0
    active_loan_floor: float = 0.0
    active_loan_rate: float = 0.0
    active_loan_lender_id: Optional[str] = None


@dataclass
class BankrollKnobs:
    """Per-personality bankroll behavior knobs.

    Stored in columns on `personalities` (schema v88). Read at table
    sit-down to decide buy-in size, eligibility, and (in v2)
    stop-loss / stop-win cutoffs.

    `stop_loss_buy_ins` and `stop_win_buy_ins` are persisted in v1
    but unused — v1 ships bust-only AI session behavior (Part 2
    §"AI session behavior (v1)"). v2 reads these to add stop-loss /
    stop-win gates.

    `stake_comfort_zone` is the friendly stake label ("$10",
    "$50", ...) the AI prefers when multiple are affordable. v1 has
    no selection problem (one table), so this is also persisted-but-
    unused until v2's lobby lands.
    """

    bankroll_cap: int
    bankroll_rate: int
    buy_in_multiplier: float
    stop_loss_buy_ins: int
    stop_win_buy_ins: int
    stake_comfort_zone: str


# Defaults used when a personality has no per-row override yet.
# v1 ships uniform defaults; per-personality tuning is a follow-up
# (just populate the columns from personalities.json).
BANKROLL_KNOB_DEFAULTS = BankrollKnobs(
    bankroll_cap=10_000,
    bankroll_rate=500,
    buy_in_multiplier=1.0,
    stop_loss_buy_ins=3,
    stop_win_buy_ins=5,
    stake_comfort_zone="$10",
)


def project_bankroll(
    state: AIBankrollState,
    cap: int,
    rate: int,
    now: datetime,
) -> int:
    """Project bankroll chips through elapsed time.

    Pure function. Returns the value `chips` would currently have
    given the time elapsed since the last mutation, clamped to `cap`.
    Does not mutate `state`.

      projected = stored_chips + int(rate * elapsed_days)
      projected = min(cap, projected)

    Same pattern as `project_heat`. Persistence writes only on real
    events; reads always project. The fractional-day floor uses
    `int(...)` so projection is monotonic per day boundary — a
    half-second after `last_regen_tick` reads as the stored value,
    not a no-op `+0`.

    `last_regen_tick == None` is the no-event-yet state for freshly
    seeded AI personalities; the function returns stored `chips`
    verbatim so the seed value isn't immediately inflated.
    """
    if state.last_regen_tick is None:
        return state.chips
    elapsed_days = (now - state.last_regen_tick).total_seconds() / 86400.0
    projected = state.chips + int(rate * elapsed_days)
    return min(cap, projected)


def credit_ai_cash_out(
    bankroll_repo,
    personality_id: str,
    player_stack: int,
    *,
    now: Optional[datetime] = None,
    chip_ledger_repo=None,
    ledger_context: Optional[dict] = None,
) -> Optional[AIBankrollState]:
    """Credit `player_stack` chips back to an AI's persistent bankroll.

    Mirrors the leave-time accounting rule: project the stored
    bankroll forward through elapsed time (passive regen), then add
    the AI's current table stack, clamped to `bankroll_cap`. The cap
    is a hard ceiling — winnings above the cap evaporate. This is
    intentional: it prevents a single AI from accumulating a runaway
    bankroll relative to the rest of the cast.

    Skips (returns None) when:
      - the AI has no row in `ai_bankroll_state` yet (shouldn't
        happen for an AI that sat at a table — sit_down writes the
        row — but the seam is defensive)
      - `player_stack <= 0` (busted or near-zero stack; nothing to
        credit, and we avoid pointless writes)

    Writes a fresh `AIBankrollState` snapshot via `save_ai_bankroll`
    with `last_regen_tick = now`. Returns the persisted state so
    callers can log / inspect.

    `bankroll_repo` is the live `BankrollRepository` instance — taken
    as a parameter (rather than the module-level singleton) so tests
    can pass a tempdb-backed instance without monkey-patching the
    flask_app.extensions module.

    `chip_ledger_repo` (optional) opts the call into ledger
    instrumentation. When provided, the regen portion of the write
    fires an `ai_regen` entry and any overflow above `bankroll_cap`
    fires a `cap_clamp` entry. None disables instrumentation
    entirely so tests don't need the repo.
    """
    if player_stack <= 0:
        return None
    if now is None:
        now = datetime.utcnow()
    stored = bankroll_repo.load_ai_bankroll(personality_id)
    if stored is None:
        logger.warning(
            "[CASH] AI cash-out skipped — no bankroll row for %r",
            personality_id,
        )
        return None
    knobs = bankroll_repo.load_personality_knobs(personality_id)
    projected = project_bankroll(stored, knobs.bankroll_cap, knobs.bankroll_rate, now)
    new_chips = min(knobs.bankroll_cap, projected + player_stack)
    new_state = AIBankrollState(
        personality_id=personality_id,
        chips=new_chips,
        last_regen_tick=now,
    )
    bankroll_repo.save_ai_bankroll(new_state)
    if chip_ledger_repo is not None:
        from core.economy import ledger as chip_ledger
        ctx = {'site': 'credit_ai_cash_out'}
        if ledger_context:
            ctx.update(ledger_context)
        chip_ledger.record_ai_regen(
            chip_ledger_repo,
            personality_id=personality_id,
            stored_chips=stored.chips,
            projected_chips=projected,
            context=ctx,
        )
        # Cap clamp: chips that came off the table but couldn't fit
        # in the bankroll evaporate back to the bank. Pre-clamp
        # value = projected + player_stack; overflow = excess.
        overflow = max(0, (projected + player_stack) - knobs.bankroll_cap)
        clamp_ctx = dict(ctx)
        clamp_ctx['cap'] = knobs.bankroll_cap
        clamp_ctx['projected'] = projected
        clamp_ctx['player_stack'] = player_stack
        chip_ledger.record_cap_clamp(
            chip_ledger_repo,
            personality_id=personality_id,
            overflow=overflow,
            context=clamp_ctx,
        )
    logger.info(
        "[CASH] AI cash-out %r: +%d (projected=%d) → %d (cap %d)",
        personality_id, player_stack, projected, new_chips, knobs.bankroll_cap,
    )
    return new_state
