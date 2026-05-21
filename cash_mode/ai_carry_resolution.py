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
    STAKER_KIND_PERSONALITY,
    Stake,
)

logger = logging.getLogger(__name__)


# --- Constants (tunable; defaults per the handoff) ---

# Voluntary payoff (Commit 3).
PAYOFF_BANKROLL_FACTOR_FLOOR = 5.0
"""bankroll_factor = bankroll / total_carries. At ≥5× a flush AI
gets a non-trivial chance to pay off; below 1× they never do. Tuned
to feel like "AIs clear their books when they're comfortable"
rather than "AIs constantly liquidate every spare chip"."""

PAYOFF_BASE_PROB = 0.05
"""Per-refresh base probability to attempt a voluntary payoff when
the AI clears the floor. Combined with the bankroll_factor signal:
chance = base × min(1, (factor - 1) / (FLOOR - 1)) so the curve
ramps from 0 at factor=1 to base at factor>=FLOOR."""

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
) -> Optional[CarryResolutionResult]:
    """Roll for a voluntary payoff against this AI's oldest carry.

    Phase 4.5 Commit 3.

    Picks the oldest outstanding carry (`carries[0]` — repo returns
    oldest-first), checks bankroll_factor against the floor, rolls
    `_payoff_probability(factor)`. On a hit:
      - Debit borrower bankroll by carry_amount.
      - Credit staker bankroll via `credit_ai_cash_out` (mirrors the
        human-route /payoff path — same regen + cap-clamp semantics).
      - Mark stake settled; fire STAKE_REPAID.
      - Return a CarryResolutionResult so the dispatcher can emit
        the ticker event.

    Returns None when no payoff fired (no carries, insufficient
    bankroll, probability roll failed).
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

    total_carries = _carries_sum(carries)
    factor = _bankroll_factor(projected, total_carries)
    prob = _payoff_probability(factor)
    if prob <= 0 or rng.random() >= prob:
        return None

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
