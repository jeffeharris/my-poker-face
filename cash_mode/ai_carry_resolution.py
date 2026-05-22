"""AI-initiated carry resolution (Phase 4.5 Commits 3-5).

Phase 4 leaves a one-way credit market: AIs accept stakes when they
bust (`take_stake` interception) but never resolve the resulting
carries. Without this module, AI dossiers accumulate "they owe" rows
indefinitely; the cast looks like an accumulating debt graph rather
than a churning credit market.

Three behaviors mirror player-side carry-clearing paths (Phase 3 +
Phase 2 commit 2), but with AI-driven triggers:

  - `try_ai_voluntary_payoff` (Commit 3) — when flush, an AI clears
    the oldest carry from bankroll. Mirrors POST /payoff.
  - `try_ai_forgiveness_ask` (Commit 4) — when carrying debt and
    bankroll-poor, an AI asks the staker (likability-first) for
    forgiveness. Mirrors POST /request-forgiveness. Rate-limited at
    7 days (looser than the 24h human rate-limit because the rolls
    are auto-fired, not user-initiated).
  - `try_ai_explicit_default` (Commit 5) — under sustained pressure
    (low energy, high carry-load, low respect), an AI walks away
    from a specific carry, eating the STAKE_DEFAULTED reputation
    hit. Mirrors POST /default.

All three are called from `cash_mode.lobby.refresh_unseated_tables`
once per lobby refresh, after the per-table sim/movement loop. The
dispatcher iterates AIs with at least one outstanding carry (single
query, O(carries) not O(all-AIs)).

The settlement math and stake-row mutations reuse the existing repo
primitives unchanged. The only new surface here is the *triggers*
that decide when each existing path fires for AIs.

Spec: `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` Phase 4.5.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from cash_mode.stakes import (
    BORROWER_KIND_PERSONALITY,
    STAKE_STATUS_DEFAULTED,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_HUMAN,
    STAKER_KIND_PERSONALITY,
    Stake,
)

logger = logging.getLogger(__name__)


# --- Constants (tunable; defaults per the handoff) ---

# Voluntary payoff (Commit 3, redesigned).
PAYOFF_BANKROLL_FACTOR_FLOOR = 5.0
"""bankroll_factor = bankroll / total_carries. Retained for the
legacy `_payoff_probability(factor)` helper used by per-tick fallback
rolls; the new score-driven decision (see `_payoff_score`) doesn't
consume this constant directly."""

PAYOFF_BASE_PROB = 0.05
"""Legacy per-refresh base probability — preserved for the slow
per-tick fallback. The score-driven decision uses its own
`base_rate` parameter (1.0 for event-gated triggers,
`PAYOFF_TICK_BASE_RATE` for the long-tail catch-all)."""

PAYOFF_TICK_BASE_RATE = 0.005
"""Per-tick fallback rate for the long-tail catch-all when no event
trigger has fired. ~10× slower than the old PAYOFF_BASE_PROB so
ancient debts eventually clear without per-tick rolling dominating
the resolution mix."""

PAYOFF_EVENT_BASE_RATE = 1.0
"""Multiplier for event-gated triggers (aspiration-ask, leave-with-
profit). At 1.0 the score IS the probability — high-score AIs act
decisively at meaningful moments rather than waiting on luck."""

PAYOFF_AGE_RAMP_DAYS = 14.0
"""Carry age (in days) at which `_carry_age_factor` saturates to 1.0.
Linear ramp from 0 days → 0 to 14 days → 1. Captures "old debt
nags more than yesterday's"."""

PAYOFF_WEALTH_GAP_RAMP = 4.0
"""Bankroll multiples above target_min_buy_in at which
`_wealth_gap_factor` saturates to 1.0. 1× → 0, 5× → 1. Mirrors the
"comfortably flush enough to climb" curve aspiration uses, so the
two pulls are commensurate."""

# Forgiveness ask (Commit 4).
FORGIVENESS_ASK_BASE_PROB = 0.05
"""Per-refresh ask probability scales inversely with bankroll_factor
— poor AIs ask, flush AIs would rather pay off than ask."""

FORGIVENESS_RATE_LIMIT_SECONDS = 7 * 24 * 60 * 60
"""7-day window between asks against the same stake. Tighter than
the human path's 24h because the AI rolls automatically every
refresh; without the wider window the staker would get spam-asked
once a day. Stamped on both granted and refused paths."""

# Forgiveness decision math mirrors the human route exactly so the
# two surfaces share the same threshold semantics.
FORGIVENESS_LIKABILITY_WEIGHT = 0.5
FORGIVENESS_RESPECT_WEIGHT = 0.4
FORGIVENESS_HEAT_WEIGHT = 0.3
FORGIVENESS_THRESHOLD = 0.55

# Explicit default (Commit 5).
DEFAULT_PRESSURE_THRESHOLD = 0.6
"""Pressure score above this triggers a chance to default. Below
this, the AI keeps the carry around and the natural tier/garnishment
pressure pushes them through alternative paths."""

DEFAULT_BASE_PROB = 0.10
"""Per-refresh per-carry default probability ABOVE the threshold.
Multiplied by (pressure - threshold) / (1 - threshold) so the chance
ramps from 0 at threshold to base at pressure=1.0."""

DEFAULT_DROWNING_RATIO = 0.5
"""bankroll_factor below this contributes +0.4 pressure (drowning
in debt). The biggest single pressure signal — when the AI can't
even afford to pay off their carries, default looks attractive."""

DEFAULT_ENERGY_FLOOR = 0.3
"""Energy below this contributes +0.3 pressure (tilted/tired AI
is more willing to burn relationships)."""

DEFAULT_RESPECT_FLOOR = -0.2
"""Staker's respect for borrower below this contributes +0.2
pressure (already on bad terms — less reputation to lose)."""

# Threshold for surfacing payoff / forgiveness events on the lobby
# ticker lives in `cash_mode.activity` (re-exported alongside the
# Phase 4 AI_STAKE_TICKER_THRESHOLD so both surfaces share one drama
# floor). Re-import here so existing references keep working without
# every caller knowing the canonical home.
from cash_mode.activity import AI_CARRY_TICKER_THRESHOLD  # noqa: F401, E402


# --- Result dataclasses ---


@dataclass(frozen=True)
class CarryResolutionResult:
    """One AI-initiated carry resolution outcome.

    All three behaviors (payoff, forgiveness, default) return a row
    of this shape so the dispatcher's emit-events pass can format
    each one uniformly. The fields are deliberately broad — not every
    action populates every field (e.g., forgiveness has no chip
    transfer), and consumers branch on `kind`.
    """

    kind: str  # 'payoff' | 'forgiven' | 'forgiveness_refused' | 'default'
    stake_id: str
    staker_id: str
    borrower_id: str
    stake_tier: str
    amount: int  # chips moved (payoff) OR carry cleared (forgiven/default)
    score: Optional[float] = None  # forgiveness score; None for non-forgiveness


@dataclass
class CarryResolutionBatch:
    """Aggregate result of running all three behaviors across all AIs
    with carries in one lobby refresh.

    Holds enough info for the dispatcher to emit ticker events without
    re-running any of the per-AI logic. Consumed by the lobby's
    event-emission code path."""

    results: List[CarryResolutionResult] = field(default_factory=list)


# --- Internals ---


def _payoff_probability(bankroll_factor: float) -> float:
    """Probability of a voluntary payoff attempt this refresh.

    Ramps from 0 at factor=1 (just barely solvent) to PAYOFF_BASE_PROB
    at factor>=FLOOR (comfortably flush). Below factor=1 returns 0 —
    an AI that can't cover their carries shouldn't try to pay them
    off from bankroll.
    """
    if bankroll_factor <= 1.0:
        return 0.0
    if bankroll_factor >= PAYOFF_BANKROLL_FACTOR_FLOOR:
        return PAYOFF_BASE_PROB
    span = PAYOFF_BANKROLL_FACTOR_FLOOR - 1.0
    return PAYOFF_BASE_PROB * (bankroll_factor - 1.0) / span


def _forgiveness_ask_probability(bankroll_factor: float) -> float:
    """Probability of attempting a forgiveness ask this refresh.

    Inverse of bankroll_factor — poor AIs ask, flush AIs would pay.
    `bankroll_factor` is clamped to [1, ∞) before the divide so we
    don't divide by 0 or amplify near-zero ratios into giant probs.
    """
    return FORGIVENESS_ASK_BASE_PROB / max(1.0, bankroll_factor)


def _forgiveness_score(*, likability: float, respect: float, heat: float) -> float:
    """Identical to the human-route forgiveness score in cash_routes.

    Kept duplicated here rather than imported because the import
    creates a circular dependency (cash_routes imports from
    cash_mode, not the other way around). The constants are
    module-level so a refactor that consolidates them later remains
    a single-edit change.
    """
    return (
        likability * FORGIVENESS_LIKABILITY_WEIGHT
        + respect * FORGIVENESS_RESPECT_WEIGHT
        - heat * FORGIVENESS_HEAT_WEIGHT
    )


def _default_pressure(
    *,
    bankroll_factor: float,
    energy: float,
    staker_respect_for_borrower: float,
    carry_age_days: float,
    oldest_age_days: float,
) -> float:
    """Pressure score for explicit default. Higher = more likely.

    Four additive components per the handoff spec:
      - +0.4 if bankroll_factor < DEFAULT_DROWNING_RATIO
      - +0.3 if energy < DEFAULT_ENERGY_FLOOR
      - +0.2 if staker_respect_for_borrower < DEFAULT_RESPECT_FLOOR
      - +0.1 if this carry IS the borrower's oldest

    The "oldest" bonus is small (+0.1) because in early-game state
    every carry is roughly the same age — without something to break
    ties it'd make the borrower's first-ever carry the default
    target for years. Cap at 1.0 so a default chance scales
    consistently against the threshold.
    """
    pressure = 0.0
    if bankroll_factor < DEFAULT_DROWNING_RATIO:
        pressure += 0.4
    if energy < DEFAULT_ENERGY_FLOOR:
        pressure += 0.3
    if staker_respect_for_borrower < DEFAULT_RESPECT_FLOOR:
        pressure += 0.2
    # Use a tolerant equality check so "same created_at second"
    # carries both qualify; in practice the oldest-first selection
    # below handles ordering deterministically.
    if abs(carry_age_days - oldest_age_days) < 1e-6:
        pressure += 0.1
    return min(1.0, pressure)


def _bankroll_factor(bankroll_chips: int, total_carries: int) -> float:
    """Bankroll-to-carry ratio. Returns a large sentinel when total_carries
    is 0 (caller already-no-carries skips this path, but defensive)."""
    if total_carries <= 0:
        return float('inf')
    return bankroll_chips / total_carries


def _carries_sum(carries: List[Stake]) -> int:
    return sum(int(c.carry_amount) for c in carries)


def _carry_age_days(stake: Stake, now: datetime) -> float:
    if stake.created_at is None:
        return 0.0
    delta = now - stake.created_at
    return delta.total_seconds() / 86400.0


def _within_rate_limit(stake: Stake, now: datetime, window_seconds: int) -> bool:
    """True iff this stake was asked-for-forgiveness within `window_seconds`.

    Used to throttle AI forgiveness asks at FORGIVENESS_RATE_LIMIT_SECONDS
    so a staker doesn't get spam-asked every lobby refresh."""
    if stake.forgiveness_last_asked is None:
        return False
    elapsed = (now - stake.forgiveness_last_asked).total_seconds()
    return elapsed < window_seconds


# --- Score-driven payoff decision helpers --------------------------------


def _carry_age_factor(stake: Stake, now: datetime) -> float:
    """0 at fresh, ramps linearly to 1.0 at PAYOFF_AGE_RAMP_DAYS.

    The "old debts nag more" intuition. Saturates flat at 1.0 past
    the ramp so a year-old carry isn't worth more pressure than a
    two-week-old one — eventually default does that work.
    """
    age = _carry_age_days(stake, now)
    if PAYOFF_AGE_RAMP_DAYS <= 0:
        return 1.0
    return max(0.0, min(1.0, age / PAYOFF_AGE_RAMP_DAYS))


def _wealth_gap_factor(bankroll_chips: int, target_min_buy_in: int) -> float:
    """0 if bankroll can't reach the target tier; ramps to 1.0 at 5×.

    Mirrors the shape of the aspiration_ask wealth signal so the two
    pulls live on the same scale. An AI with 1× their next tier's
    buy-in has a wealth_gap of 0 (no surplus to climb); at 5× they
    have ample chips to either climb or pay debts.
    """
    if target_min_buy_in <= 0:
        return 0.0
    ratio = float(bankroll_chips) / float(target_min_buy_in)
    if ratio < 1.0:
        return 0.0
    if PAYOFF_WEALTH_GAP_RAMP <= 0:
        return 1.0
    return min(1.0, (ratio - 1.0) / PAYOFF_WEALTH_GAP_RAMP)


def _pay_pull(*, age_factor: float, heat: float) -> float:
    """How much the AI "should" pay this debt — debt urgency.

    Pure mean of carry-age pressure and staker-relationship heat.
    Heat clamped at 0 from below (mild affection doesn't push you to
    pay faster — only friction does). Both terms live in [0, 1] so
    the average does too.
    """
    return (max(0.0, min(1.0, age_factor)) + max(0.0, min(1.0, heat))) / 2.0


def _hold_pull(*, aspiration_bias: float, wealth_gap: float) -> float:
    """How much the AI wants to keep chips for climbing instead.

    Product of personality "want to climb" knob and "can afford to
    climb" signal — both have to be present for hold_pull to be
    meaningful. A poverty-bound aspirer with no surplus doesn't
    really have chips to hoard; a flush content-AI doesn't care.
    """
    a = max(0.0, min(1.0, aspiration_bias))
    w = max(0.0, min(1.0, wealth_gap))
    return a * w


def _payoff_score(
    *,
    payoff_eagerness: float,
    pay_pull: float,
    hold_pull: float,
) -> float:
    """Composite score — the AI's appetite to pay this carry.

    `eagerness × pay_pull − (1 − eagerness) × hold_pull` clamped
    to [0, 1]. Captures the two competing forces weighted by the
    personality's conscientiousness:
      - Conscientious AI (eagerness ≈ 1): full pay_pull, no
        hold_pull → conscientious AIs always pay when there's any
        urgency.
      - Gambler (eagerness ≈ 0): zero pay_pull, full hold_pull →
        even old debts lose to a climb opportunity.
      - Baseline (eagerness 0.5): pulls cancel symmetrically; the
        winning side wins by half the differential.
    """
    e = max(0.0, min(1.0, payoff_eagerness))
    raw = e * max(0.0, pay_pull) - (1.0 - e) * max(0.0, hold_pull)
    return max(0.0, min(1.0, raw))


def _min_tier_buy_in_buffer() -> int:
    """Cheapest tier's min buy-in — the affordability floor.

    Affordability gate: after paying off, the AI must still have at
    least this many chips, so they can still sit at SOME table.
    Cheaper floor than "current tier" by design — paying off should
    be allowed even when it means dropping a tier, just not when it
    means losing seat access entirely.

    Lazy-imported to avoid a hard ladder dependency at module import
    time.
    """
    from cash_mode.stakes_ladder import STAKES_ORDER, table_buy_in_window
    if not STAKES_ORDER:
        return 0
    _, min_buy_in, _ = table_buy_in_window(STAKES_ORDER[0])
    return int(min_buy_in)


def _affordability_gate(bankroll_chips: int, carry_amount: int) -> bool:
    """Hard gate: can they pay AND keep enough for a min-tier seat?

    Without this, an AI could pay off a debt that drops them below
    every table's buy-in — leaving them with no playable seat. The
    gate uses the CHEAPEST tier's min buy-in as the floor so we
    don't over-block (paying off + dropping to micro stakes is fine;
    paying off + leaving the lobby entirely is not).
    """
    if carry_amount <= 0:
        return False
    floor = _min_tier_buy_in_buffer()
    return (bankroll_chips - carry_amount) >= floor


# --- Matcher penalty for borrowers carrying debt -------------------------

CARRY_MATCHER_PENALTY_BASE = 0.5
"""Per-carry multiplicative penalty on matcher success probability.
0 carries → 1.0 (no penalty), 1 carry → 0.5, 2 carries → 0.25, etc.
Tunes the "future stake access" loop: borrowers with debt have a
harder time getting backed for new stakes (climb or bailout), which
makes the AI's "should I pay first?" decision a real strategic
choice rather than a vibe — gamblers who skip payoff keep failing
matcher rolls until they default."""


def carry_penalty_probability(active_carry_count: int) -> float:
    """Probability the matcher should *succeed* given this carry count.

    `CARRY_MATCHER_PENALTY_BASE ** N`. Callers use this as a gate:
    roll RNG, succeed if `rng.random() < carry_penalty_probability(N)`.

    Zero carries returns 1.0 (no penalty, clean borrower). Each
    outstanding carry halves the success probability. The decay is
    exponential — a borrower with 4+ carries is effectively blocked
    from new stakes until they clear some, which is the intended
    pressure to settle debts before reaching for more.
    """
    if active_carry_count <= 0:
        return 1.0
    return CARRY_MATCHER_PENALTY_BASE ** int(active_carry_count)


# --- Public entry points (one per commit) ---


def try_ai_voluntary_payoff(
    *,
    personality_id: str,
    carries: List[Stake],
    bankroll_repo,
    stake_repo,
    relationship_repo,
    chip_ledger_repo,
    sandbox_id: Optional[str],
    rng: random.Random,
    now: datetime,
    base_rate: float = PAYOFF_TICK_BASE_RATE,
) -> Optional[CarryResolutionResult]:
    """Roll for a voluntary payoff against this AI's oldest carry.

    Score-driven model: the AI weighs *paying* (carry age + staker
    heat) against *holding* (aspiration × wealth surplus), weighted
    by their personality's `payoff_eagerness` knob. The product of
    `_payoff_score()` and `base_rate` is the fire probability.

    `base_rate` lets callers tune intensity per trigger event:
      - Per-tick dispatcher → `PAYOFF_TICK_BASE_RATE` (slow long-tail).
      - Aspiration-ask gate → `PAYOFF_EVENT_BASE_RATE` (= 1.0;
        score IS the probability, so a conscientious AI with a real
        debt commits immediately when about to climb).

    Hard gates (applied before the roll):
      - At least one carry exists.
      - Borrower bankroll row exists; projected chips ≥ carry_amount.
      - Staker id is non-null (house carries don't reach this path).
      - For human-staker carries: `player_bankroll_state` row exists
        (otherwise the credit would vaporize chips).
      - **Affordability**: bankroll minus carry_amount must leave at
        least the cheapest tier's min buy-in. Without this, the AI
        could pay off into a state with no playable seat.

    Returns None when no payoff fired.
    """
    if not carries:
        return None

    # Load the stored row so we can compute the projected chip count
    # AND fire the ai_regen ledger entry for the regen portion of the
    # write. Going through `load_ai_bankroll_current` would lose the
    # stored chip count we need for the ledger entry's `stored_chips`
    # field. Mirrors the shape of `credit_ai_cash_out` so the two
    # surfaces stay calibrated against the same audit semantics.
    from cash_mode.bankroll import AIBankrollState, credit_ai_cash_out, project_bankroll
    stored = bankroll_repo.load_ai_bankroll(personality_id, sandbox_id=sandbox_id)
    if stored is None:
        # No bankroll row — can't pay from nothing. The seed path
        # (ensure_ai_bankrolls_seeded) should have created one, but
        # defensively skip rather than fail the lobby refresh.
        return None
    knobs = bankroll_repo.load_personality_knobs(personality_id)
    projected = project_bankroll(
        stored, knobs.starting_bankroll, knobs.bankroll_rate, now,
    )

    target = carries[0]  # oldest first — repo returns ASC by created_at
    carry_amount = int(target.carry_amount)
    if carry_amount <= 0:
        return None
    if projected < carry_amount:
        # Can't afford the oldest carry — could pick a smaller one,
        # but that'd be cherry-picking. Skip this refresh; next time
        # the floor check will reflect any chip changes.
        return None
    if target.staker_id is None:
        # House carries shouldn't exist (settle_stake_on_leave
        # forgives them). Defensive skip.
        return None

    # Affordability gate — paying off must leave enough for a
    # min-tier seat. Cheaper than the old bankroll_factor floor
    # (which required 1× total carries comfortably) because the
    # score model handles "comfort" via wealth_gap; the gate just
    # guards against losing all lobby access.
    if not _affordability_gate(projected, carry_amount):
        return None

    # Score the decision. Load borrower profile (eagerness +
    # aspiration_bias) and staker→borrower relationship (heat).
    # Both reads are local — the per-AI carry list is already
    # bounded by `resolve_ai_carries`'s outer loop, so we don't
    # batch these.
    try:
        profile = bankroll_repo.load_borrower_profile(personality_id)
    except Exception as exc:
        logger.warning(
            "[CASH][AI_PAYOFF] borrower profile load failed pid=%r: %s",
            personality_id, exc,
        )
        return None

    heat = 0.0
    if relationship_repo is not None:
        try:
            rel = relationship_repo.load_relationship_state(
                observer_id=target.staker_id,
                opponent_id=personality_id,
                now=now,
            )
            if rel is not None:
                heat = float(getattr(rel, 'heat', 0.0))
        except Exception as exc:
            logger.debug(
                "[CASH][AI_PAYOFF] relationship load failed staker=%r "
                "borrower=%r: %s",
                target.staker_id, personality_id, exc,
            )

    # Target tier for the hold_pull wealth calc — what tier they're
    # implicitly competing against. The carry's own stake_tier is
    # the right reference: it's the level they busted from / aspire
    # back to. If the tier isn't on the ladder anymore (unlikely
    # but defensive), wealth_gap reads 0 → hold_pull is 0 → score
    # collapses to pure pay_pull weighted by eagerness.
    try:
        from cash_mode.stakes_ladder import table_buy_in_window
        _, target_min_buy_in, _ = table_buy_in_window(target.stake_tier)
    except (KeyError, Exception):
        target_min_buy_in = 0

    age_factor = _carry_age_factor(target, now)
    wealth_gap = _wealth_gap_factor(projected, target_min_buy_in)
    pay = _pay_pull(age_factor=age_factor, heat=heat)
    hold = _hold_pull(
        aspiration_bias=profile.aspiration_bias, wealth_gap=wealth_gap,
    )
    score = _payoff_score(
        payoff_eagerness=profile.payoff_eagerness,
        pay_pull=pay,
        hold_pull=hold,
    )
    prob = max(0.0, min(1.0, score * float(base_rate)))
    if prob <= 0 or rng.random() >= prob:
        return None

    # Human-staker pre-flight: the credit must land on
    # `player_bankroll_state`, not an AI bankroll row. Confirm the row
    # exists before we debit the AI — otherwise we'd vaporize chips
    # from the universe on a missing-staker corner case.
    human_staker_bankroll = None
    if target.staker_kind == STAKER_KIND_HUMAN:
        from cash_mode.bankroll import PlayerBankrollState  # noqa: F401
        human_staker_bankroll = bankroll_repo.load_player_bankroll(
            target.staker_id,
        )
        if human_staker_bankroll is None:
            logger.warning(
                "[CASH][AI_PAYOFF] human staker bankroll missing — skipping "
                "payoff staker=%r stake=%r",
                target.staker_id, target.stake_id,
            )
            return None

    # Commit the projected-and-debited chip count with a fresh
    # last_regen_tick. The `projected - stored` delta is regen that
    # just entered the universe via this write — fire `ai_regen` so
    # the chip-ledger audit balances. Without this, `ai_bankrolls_stored`
    # silently inflates on every voluntary payoff (regen chips appear
    # on the borrower's row with no matching ledger creation).
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(
            personality_id=personality_id,
            chips=max(0, projected - carry_amount),
            last_regen_tick=now,
        ),
        sandbox_id=sandbox_id,
    )
    if chip_ledger_repo is not None and projected > stored.chips:
        from core.economy import ledger as chip_ledger
        chip_ledger.record_ai_regen(
            chip_ledger_repo,
            personality_id=personality_id,
            stored_chips=stored.chips,
            projected_chips=projected,
            context={
                'stake_id': target.stake_id,
                'site': 'ai_voluntary_payoff',
                'sandbox_id': sandbox_id,
            },
            sandbox_id=sandbox_id,
        )

    if target.staker_kind == STAKER_KIND_HUMAN:
        # Route the credit to player_bankroll_state. credit_ai_cash_out
        # would write into a phantom AI bankroll keyed by the human's
        # owner_id (the bug this branch fixes).
        from cash_mode.bankroll import PlayerBankrollState
        bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=human_staker_bankroll.player_id,
                chips=human_staker_bankroll.chips + carry_amount,
                starting_bankroll=human_staker_bankroll.starting_bankroll,
            ),
        )
    else:
        credit_ai_cash_out(
            bankroll_repo, target.staker_id, carry_amount,
            sandbox_id=sandbox_id,
            now=now,
            chip_ledger_repo=chip_ledger_repo,
            ledger_context={
                'stake_id': target.stake_id,
                'site': 'ai_voluntary_payoff',
            },
        )

    stake_repo.update_carry_amount(target.stake_id, 0)
    stake_repo.update_status(target.stake_id, STAKE_STATUS_SETTLED, settled_at=now)

    # v106 payout accounting on AI voluntary payoff — mirrors the
    # human-route path so the Net Worth history's net P&L stays
    # accurate when a carry is later cleared via chip flow rather
    # than write-off (forgive/default). See the human payoff route's
    # comment for the rationale.
    prior_staker_payout = target.staker_payout or 0
    prior_borrower_payout = target.borrower_payout or 0
    stake_repo.update_payouts(
        target.stake_id,
        staker_payout=prior_staker_payout + carry_amount,
        borrower_payout=prior_borrower_payout - carry_amount,
    )

    # STAKE_REPAID event: actor=staker, target=borrower. Same dispatch
    # the human-payoff route fires. Best-effort — log on failure.
    try:
        from poker.memory import OpponentModelManager
        from poker.memory.relationship_events import RelationshipEvent
        if relationship_repo is not None:
            mgr = OpponentModelManager(relationship_repo=relationship_repo)
            mgr.record_event(
                actor_id=target.staker_id,
                target_id=personality_id,
                event=RelationshipEvent.STAKE_REPAID,
            )
    except Exception as exc:
        logger.warning(
            "[CASH][AI_PAYOFF] STAKE_REPAID failed stake=%r: %s",
            target.stake_id, exc,
        )

    logger.info(
        "[STAKE][AI_PAYOFF] %r paid %d to %r stake_id=%r",
        personality_id, carry_amount, target.staker_id, target.stake_id,
    )

    return CarryResolutionResult(
        kind='payoff',
        stake_id=target.stake_id,
        staker_id=target.staker_id,
        borrower_id=personality_id,
        stake_tier=target.stake_tier,
        amount=carry_amount,
    )


def try_ai_forgiveness_ask(
    *,
    personality_id: str,
    carries: List[Stake],
    bankroll_repo,
    stake_repo,
    relationship_repo,
    sandbox_id: Optional[str],
    rng: random.Random,
    now: datetime,
) -> Optional[CarryResolutionResult]:
    """Roll for a forgiveness ask against the friendliest available staker.

    Phase 4.5 Commit 4.

    Picks the eligible carry (not rate-limited within the 7-day
    window) whose staker has the highest likability toward this
    borrower. Computes the same `score` the human route uses
    (`L×0.5 + R×0.4 - H×0.3`) against FORGIVENESS_THRESHOLD.

    On a grant: clear carry, mark settled, fire STAKE_FORGIVEN.
    On a refusal: stamp rate-limit timer, fire STAKE_FORGIVENESS_REFUSED.

    Both paths stamp `forgiveness_last_asked` so the same stake can't
    be asked again until the window passes.
    """
    if not carries or relationship_repo is None:
        return None

    bankroll_chips_opt = bankroll_repo.load_ai_bankroll_current(
        personality_id, sandbox_id=sandbox_id, now=now,
    )
    bankroll_chips = int(bankroll_chips_opt) if bankroll_chips_opt is not None else 0
    total_carries = _carries_sum(carries)
    factor = _bankroll_factor(bankroll_chips, total_carries)
    prob = _forgiveness_ask_probability(factor)
    if prob <= 0 or rng.random() >= prob:
        return None

    # Filter out rate-limited carries; for the rest, look up the
    # staker's likability and pick the highest. Ties broken by oldest
    # carry first (carries are already sorted ASC by created_at).
    eligible: List[Tuple[Stake, float, float, float]] = []
    for c in carries:
        if c.staker_id is None:
            continue
        if c.staker_kind == STAKER_KIND_HUMAN:
            # Human-staker carries route through a consent flow (the
            # player decides whether to forgive — auto-grant would
            # silently vanish their chips with no opportunity to refuse).
            # See the staker-forgive route follow-up.
            continue
        if _within_rate_limit(c, now, FORGIVENESS_RATE_LIMIT_SECONDS):
            continue
        rel = relationship_repo.load_relationship_state(
            observer_id=c.staker_id, opponent_id=personality_id, now=now,
        )
        likability = rel.likability if rel is not None else 0.5
        respect = rel.respect if rel is not None else 0.5
        heat = rel.heat if rel is not None else 0.0
        eligible.append((c, likability, respect, heat))

    if not eligible:
        return None

    # Sort by likability DESC — friendliest staker first. Stable sort
    # preserves the oldest-first tiebreak from `carries`.
    eligible.sort(key=lambda t: t[1], reverse=True)
    target, likability, respect, heat = eligible[0]
    score = _forgiveness_score(
        likability=likability, respect=respect, heat=heat,
    )
    granted = score > FORGIVENESS_THRESHOLD

    # Stamp the rate-limit window on both paths.
    stake_repo.mark_forgiveness_asked(target.stake_id, now)

    from poker.memory import OpponentModelManager
    from poker.memory.relationship_events import RelationshipEvent
    mgr = OpponentModelManager(relationship_repo=relationship_repo)

    if granted:
        carry_amount = int(target.carry_amount)
        stake_repo.update_carry_amount(target.stake_id, 0)
        stake_repo.update_status(target.stake_id, STAKE_STATUS_SETTLED, settled_at=now)
        try:
            mgr.record_event(
                actor_id=target.staker_id,
                target_id=personality_id,
                event=RelationshipEvent.STAKE_FORGIVEN,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][AI_FORGIVENESS] STAKE_FORGIVEN failed stake=%r: %s",
                target.stake_id, exc,
            )
        logger.info(
            "[STAKE][AI_FORGIVENESS] %r forgave %r carry=%d stake_id=%r score=%.3f",
            target.staker_id, personality_id, carry_amount,
            target.stake_id, score,
        )
        return CarryResolutionResult(
            kind='forgiven',
            stake_id=target.stake_id,
            staker_id=target.staker_id,
            borrower_id=personality_id,
            stake_tier=target.stake_tier,
            amount=carry_amount,
            score=score,
        )

    try:
        mgr.record_event(
            actor_id=target.staker_id,
            target_id=personality_id,
            event=RelationshipEvent.STAKE_FORGIVENESS_REFUSED,
        )
    except Exception as exc:
        logger.warning(
            "[CASH][AI_FORGIVENESS] STAKE_FORGIVENESS_REFUSED failed stake=%r: %s",
            target.stake_id, exc,
        )
    logger.info(
        "[STAKE][AI_FORGIVENESS] %r refused %r stake_id=%r score=%.3f",
        target.staker_id, personality_id, target.stake_id, score,
    )
    return CarryResolutionResult(
        kind='forgiveness_refused',
        stake_id=target.stake_id,
        staker_id=target.staker_id,
        borrower_id=personality_id,
        stake_tier=target.stake_tier,
        amount=int(target.carry_amount),
        score=score,
    )


def try_ai_explicit_default(
    *,
    personality_id: str,
    carries: List[Stake],
    bankroll_repo,
    stake_repo,
    relationship_repo,
    sandbox_id: Optional[str],
    energy_lookup,
    rng: random.Random,
    now: datetime,
) -> Optional[CarryResolutionResult]:
    """Roll an explicit default against this AI's worst-relationship carry.

    Phase 4.5 Commit 5.

    Per-carry pressure score (see `_default_pressure`); the carry
    whose staker has the highest heat is checked first (worst-
    relationship debts cleared first). If pressure crosses
    DEFAULT_PRESSURE_THRESHOLD, rolls `DEFAULT_BASE_PROB ×
    (pressure - threshold) / (1 - threshold)` for the default.

    On a fire:
      - Zero carry_amount, flip status='defaulted', stamp settled_at.
      - Fire STAKE_DEFAULTED (sharpest negative axis shift in the
        dispatch table).
      - Return a CarryResolutionResult so the dispatcher emits the
        ticker event (distinguished from natural-carry default by
        the explicit-default message verb).
    """
    if not carries or relationship_repo is None:
        return None

    bankroll_chips_opt = bankroll_repo.load_ai_bankroll_current(
        personality_id, sandbox_id=sandbox_id, now=now,
    )
    bankroll_chips = int(bankroll_chips_opt) if bankroll_chips_opt is not None else 0
    total_carries = _carries_sum(carries)
    factor = _bankroll_factor(bankroll_chips, total_carries)
    energy = float(energy_lookup(personality_id)) if energy_lookup else 0.5

    oldest_age = _carry_age_days(carries[0], now) if carries else 0.0

    # Score each carry; sort by heat DESC so the staker who's
    # angriest with the borrower is the first cord cut. (Matches the
    # handoff spec's "highest-heat staker first" selection rule.)
    scored: List[Tuple[Stake, float, float]] = []
    for c in carries:
        if c.staker_id is None:
            continue
        rel = relationship_repo.load_relationship_state(
            observer_id=c.staker_id, opponent_id=personality_id, now=now,
        )
        respect = rel.respect if rel is not None else 0.5
        heat = rel.heat if rel is not None else 0.0
        pressure = _default_pressure(
            bankroll_factor=factor,
            energy=energy,
            staker_respect_for_borrower=respect,
            carry_age_days=_carry_age_days(c, now),
            oldest_age_days=oldest_age,
        )
        scored.append((c, pressure, heat))

    if not scored:
        return None

    # Sort by heat DESC (worst-relationship first). Stable sort
    # preserves the oldest-first order from `carries` for ties.
    scored.sort(key=lambda t: t[2], reverse=True)

    # Walk the sorted list; trigger the first carry whose pressure
    # crosses the threshold + probability roll. We don't default
    # multiple carries in one refresh — that'd be a torrent.
    for target, pressure, _heat in scored:
        if pressure < DEFAULT_PRESSURE_THRESHOLD:
            continue
        span = 1.0 - DEFAULT_PRESSURE_THRESHOLD
        if span <= 0:
            ramp = 1.0
        else:
            ramp = (pressure - DEFAULT_PRESSURE_THRESHOLD) / span
        prob = DEFAULT_BASE_PROB * ramp
        if rng.random() >= prob:
            continue

        former_carry = int(target.carry_amount)
        stake_repo.update_carry_amount(target.stake_id, 0)
        stake_repo.update_status(
            target.stake_id, STAKE_STATUS_DEFAULTED, settled_at=now,
        )

        try:
            from poker.memory import OpponentModelManager
            from poker.memory.relationship_events import RelationshipEvent
            mgr = OpponentModelManager(relationship_repo=relationship_repo)
            mgr.record_event(
                actor_id=target.staker_id,
                target_id=personality_id,
                event=RelationshipEvent.STAKE_DEFAULTED,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][AI_DEFAULT] STAKE_DEFAULTED failed stake=%r: %s",
                target.stake_id, exc,
            )

        logger.info(
            "[STAKE][AI_DEFAULT] %r burned %r former_carry=%d stake_id=%r pressure=%.3f",
            personality_id, target.staker_id, former_carry,
            target.stake_id, pressure,
        )
        return CarryResolutionResult(
            kind='default',
            stake_id=target.stake_id,
            staker_id=target.staker_id,
            borrower_id=personality_id,
            stake_tier=target.stake_tier,
            amount=former_carry,
        )

    return None


# --- Dispatcher ---


def resolve_ai_carries(
    *,
    bankroll_repo,
    stake_repo,
    relationship_repo,
    chip_ledger_repo,
    sandbox_id: Optional[str],
    energy_lookup,
    rng: random.Random,
    now: datetime,
) -> CarryResolutionBatch:
    """Run all three Phase 4.5 behaviors across every AI with carries.

    One pass per lobby refresh. The work is O(unique borrowers with
    carries), not O(all AIs) — bulk-fetch carries from the repo once,
    group by borrower_id, then iterate. Each borrower gets at most
    one outcome per refresh (payoff OR forgiveness OR default — if
    multiple rolls succeed, the dispatcher would return them all,
    but each helper short-circuits after the first action).

    Returns the batch so the lobby can emit ticker events without
    re-running any of the per-AI logic.
    """
    batch = CarryResolutionBatch()
    if stake_repo is None:
        return batch

    # Bulk-fetch every active carry, group by borrower_id. We could
    # add a `list_all_carries_by_borrower_kind` repo method, but a
    # single SELECT scan + Python group is fine for the carry
    # volumes we expect (≤ a few hundred at most).
    try:
        with stake_repo._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT stake_id, session_id, staker_id, staker_kind,
                       borrower_id, borrower_kind, format,
                       principal, match_amount, origination_fee, cut,
                       status, carry_amount, stake_tier,
                       created_at, settled_at, forgiveness_last_asked,
                       staker_payout, borrower_payout
                FROM stakes
                WHERE status = 'carry'
                  AND borrower_kind = ?
                  AND staker_id IS NOT NULL
                ORDER BY borrower_id ASC, created_at ASC
                """,
                (BORROWER_KIND_PERSONALITY,),
            ).fetchall()
    except Exception as exc:
        logger.warning("[CASH][AI_CARRY] bulk carry fetch failed: %s", exc)
        return batch

    if not rows:
        return batch

    # Convert rows to Stake objects via the repo's helper.
    from poker.repositories.stake_repository import _row_to_stake
    by_borrower: Dict[str, List[Stake]] = {}
    for row in rows:
        stake = _row_to_stake(row)
        by_borrower.setdefault(stake.borrower_id, []).append(stake)

    for borrower_id, carries in by_borrower.items():
        # Stable iteration order for testability — the helper functions
        # already do the per-carry selection logic. Best-effort across
        # all three behaviors so one helper's failure doesn't poison
        # the others.

        # Payoff first — clearing the deck is the AI's preferred move
        # when they can afford it. If a payoff fires, skip the other
        # two for this AI (resolution doesn't compound in one tick).
        try:
            result = try_ai_voluntary_payoff(
                personality_id=borrower_id,
                carries=carries,
                bankroll_repo=bankroll_repo,
                stake_repo=stake_repo,
                relationship_repo=relationship_repo,
                chip_ledger_repo=chip_ledger_repo,
                sandbox_id=sandbox_id,
                rng=rng,
                now=now,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][AI_CARRY] try_ai_voluntary_payoff(%r) failed: %s",
                borrower_id, exc,
            )
            result = None
        if result is not None:
            batch.results.append(result)
            continue

        # Forgiveness ask next — costs nothing to attempt, axes shift
        # mildly on refusal.
        try:
            result = try_ai_forgiveness_ask(
                personality_id=borrower_id,
                carries=carries,
                bankroll_repo=bankroll_repo,
                stake_repo=stake_repo,
                relationship_repo=relationship_repo,
                sandbox_id=sandbox_id,
                rng=rng,
                now=now,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][AI_CARRY] try_ai_forgiveness_ask(%r) failed: %s",
                borrower_id, exc,
            )
            result = None
        if result is not None:
            batch.results.append(result)
            continue

        # Explicit default last — narrative rupture, rare path.
        try:
            result = try_ai_explicit_default(
                personality_id=borrower_id,
                carries=carries,
                bankroll_repo=bankroll_repo,
                stake_repo=stake_repo,
                relationship_repo=relationship_repo,
                sandbox_id=sandbox_id,
                energy_lookup=energy_lookup,
                rng=rng,
                now=now,
            )
        except Exception as exc:
            logger.warning(
                "[CASH][AI_CARRY] try_ai_explicit_default(%r) failed: %s",
                borrower_id, exc,
            )
            result = None
        if result is not None:
            batch.results.append(result)
            continue

    return batch
