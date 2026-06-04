"""The economy-signal chairman — one derived read-model over the ledger,
consumed by both economy thermostats (tournament overlay/rake and the cash-table
rake schedule).

Why this module exists (see `docs/plans/TOURNAMENT_ECONOMY_ON_STATE_MODEL.md`
§"the economy-signal chairman"): two levers want the same input — the bank's
recyclable reserves measured against total chips in circulation. If each lever
computed its own aggregate and corrected independently, they would fight and
oscillate. The discipline is: **compute ONE `EconomyState` snapshot per decision
under the sandbox lock, and let both levers read that same value.**

Everything here is a **pure function** (the Presence/Custody-machine discipline):
`signal()` reads the ledger once; the two policy functions take the resulting
`EconomyState` and return a plan with zero I/O. The caller holds
`get_sandbox_lock(sandbox_id)` across read-signal → decide → apply-transfers so
the decision and its ledger writes commit atomically.

Constants are **sim-tuned, not guessed** — EXP_006 validated a proportional
overlay controller that parks reserves at the ~0.08 `reserves/holdings` setpoint
(`docs/experiments/EXP_006_BANK_RESERVE_THERMOSTAT.md`). The overlay *cadence*
(per-tournament here vs per-tick in the sim) must be re-validated before flipping
the thermostat on in production (P2 handoff §6); the *constants* transfer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.economy.ledger import (
    BANK_POOL_DEPOSIT_REASONS,
    BANK_POOL_DRAW_REASONS,
)

# --- Regimes --------------------------------------------------------------

FLUSH = 'flush'  # reserves high vs holdings → distribute (overlay), rake off
NEUTRAL = 'neutral'  # in-band → buy-ins only, no net bank flow
EMPTY = 'empty'  # reserves low vs holdings → refill (rake), overlay off


# --- Control-law constants (sim-tuned — EXP_006) --------------------------

# The reserves/holdings ratio at/above which the bank is "flush" and starts
# distributing. EXP_006: a proportional overlay above this setpoint parked
# reserves at ~0.087 (a small steady-state offset above 0.08 — textbook
# proportional control).
FLUSH_SETPOINT: float = 0.08

# Below this ratio the bank is "empty" and the (dormant-by-default) refill rake
# turns on. Not directly sim-validated for the *tournament* rake (that is the
# cash-rake sibling's job); a conservative band so the neutral zone is wide.
EMPTY_SETPOINT: float = 0.02

# Overlay as a fraction of current reserves when flush — the PER-TICK control
# constant EXP_006 originally tuned (held the band at 0.02/tick). It is RETAINED
# for reference but is NOT the production sizing: EXP_006 §6 re-validation showed
# that a fixed `pct × reserves` overlay does NOT transfer to the per-*tournament*
# cadence — the 30-min cooldown makes it ~225× too weak and reserves balloon
# (slope ~99 vs a baseline 130 chips/tick across 3 seeds). The production overlay
# uses the drain-to-setpoint law below instead (`tournament_funding`).
OVERLAY_DRAIN_PCT: float = 0.02

# Hard ceiling on a single overlay so one event can never empty the coffers
# (EXP_006 falsifier: "overlay empties the bank in one event ⇒ lever too blunt").
OVERLAY_CAP: int = 250_000

# Refill rake as a fraction of the gross pool when empty. Dormant by default
# (the tournament rake ships off — handoff "Rake default 0, mechanism present").
REFILL_RAKE_PCT: float = 0.05

# --- Canonical reserve bands (reserves/holdings ratio) ---------------------
#
# ONE source of truth for the reserve thresholds EVERY Director lever keys off,
# so vice (refill), the cash-rake schedule (throttle), and the tournament
# trigger/floor all share a single, tunable ladder instead of each writing out
# its own copy of 0.06 / 0.03. Three edges:
#
#   reserves/holdings   band        levers
#   ------------------  ----------  --------------------------------------------
#   < CRITICAL (0.03)   critical    rake widest + top rate; vice full
#   [CRITICAL, HEALTHY) low         rake adds $200 @ mid rate; vice full
#   [HEALTHY, TRIGGER)  climbing    rake $1000-only @ base; vice tapers full→off
#   >= TRIGGER (0.12)   trigger     fire a Main Event (vice off); drain to HEALTHY
#
# The vice refill stays engaged across the WHOLE climb to the trigger (not off at
# HEALTHY) — the Director keeps building the prize pool until it can open a Main
# Event, then the tournament hands those taxed chips back to players as prizes.
#
# The tournament FLOOR is deliberately HEALTHY (not CRITICAL): one event gives
# away (TRIGGER − HEALTHY) × holdings and leaves the bank in the healthy band, so
# reserves sawtooth HEALTHY→TRIGGER on the faucet and never bottom out. These
# supersede the old single FLUSH_SETPOINT trigger/floor and DEVIATE from EXP_006;
# re-validate in sim before flipping TOURNAMENT_CIRCUIT_ENABLED on. The regime
# setpoints below (FLUSH 0.08 / EMPTY 0.02) are a SEPARATE EXP_006 concern — the
# overlay-control classifier / display label — not these lever bands.
# See docs/plans/PROD_STARTING_CONDITIONS.md §1.2–1.4.
RESERVE_CRITICAL: float = 0.03  # below → bank critical
RESERVE_HEALTHY: float = 0.06  # at/above → healthy; also the tournament drain floor
RESERVE_TRIGGER: float = 0.12  # at/above → offer a Main Event


# --- The read-model -------------------------------------------------------


@dataclass(frozen=True)
class EconomyState:
    """One snapshot of the closed economy, derived from the ledger.

    `reserves` — recyclable bank-pool depth (Σ deposit reasons − Σ draw reasons).
    `holdings` — total chips outside the bank (Σ creations − Σ destructions); the
                 "size of the universe" the reserves are measured against.
    `ratio`    — `reserves / max(1, holdings)`; the signal both levers read.
    `regime`   — FLUSH | NEUTRAL | EMPTY, bucketed by the setpoints.
    """

    reserves: int
    holdings: int
    ratio: float
    regime: str


def _classify(ratio: float) -> str:
    if ratio >= FLUSH_SETPOINT:
        return FLUSH
    if ratio <= EMPTY_SETPOINT:
        return EMPTY
    return NEUTRAL


def signal(ledger_repo, *, sandbox_id: Optional[str] = None) -> EconomyState:
    """Read ONE economy snapshot from the ledger.

    Both `reserves` and `holdings` are derived from a single pair of ledger
    aggregate queries (one snapshot per decision — the anti-oscillation rule).
    `reserves`/`holdings` are *derived*, never separately stored (I4 single
    authority), so there is no second number to drift.

    Returns an all-zero NEUTRAL state when `ledger_repo` is None (test paths
    that don't wire a ledger) so callers don't have to guard.
    """
    if ledger_repo is None:
        return EconomyState(reserves=0, holdings=0, ratio=0.0, regime=NEUTRAL)
    creations = ledger_repo.sum_creations_by_reason(sandbox_id=sandbox_id)
    destructions = ledger_repo.sum_destructions_by_reason(sandbox_id=sandbox_id)
    reserves = sum(destructions.get(r, 0) for r in BANK_POOL_DEPOSIT_REASONS) - sum(
        creations.get(r, 0) for r in BANK_POOL_DRAW_REASONS
    )
    holdings = sum(creations.values()) - sum(destructions.values())
    ratio = reserves / max(1, holdings)
    # A cold / empty universe (no chips in circulation yet) carries no signal —
    # report NEUTRAL rather than EMPTY so a fresh sandbox doesn't read as
    # "bank starved" (there's nothing to refill). The setpoint buckets only
    # mean something once chips exist.
    regime = NEUTRAL if holdings <= 0 else _classify(ratio)
    return EconomyState(
        reserves=int(reserves),
        holdings=int(holdings),
        ratio=ratio,
        regime=regime,
    )


# --- Lever 1: tournament funding (built + wired in P2) --------------------


@dataclass(frozen=True)
class FundingPlan:
    """How one tournament's prize pool is funded. All amounts are real chips.

    Escrow-balance contract: `prize_pool == human_buy_in + ai_buy_in_total +
    bank_overlay − rake`, and after distribution the escrow nets to 0.
    """

    seat_price: int
    human_buy_in: int
    ai_buy_in_total: int
    bank_overlay: int
    rake: int
    prize_pool: int
    regime: str


def tournament_funding(
    state: EconomyState,
    *,
    field_size: int,
    seat_price: int,
    human_in: bool,
) -> FundingPlan:
    """Pure policy: turn an `EconomyState` + a seat price into a funding plan.

    v1 policy (constants above, all sim-tuned):
      - **Flush** → overlay = min(max(0, reserves − RESERVE_HEALTHY ×
        holdings), OVERLAY_CAP), rake = 0. Each event **drains the bank back to
        the floor** (a sawtooth: reserves climb floor→trigger on the faucet
        between events, one event drains them back to the floor). EXP_006 §6 chose
        this over the per-tick `reserves × OVERLAY_DRAIN_PCT` law: across a
        per-tournament cooldown a fixed-percent draw is far too weak and the bank
        balloons; drain-to-setpoint held the band (slope ~6–12 vs ~99 chips/tick,
        3 seeds), conservation-clean. The overlay is the only real source of an
        AI-only prize pool in v1 (busted AIs' chips are funny money).
      - **Neutral** → overlay = 0, rake = 0. Seat buy-ins only.
      - **Empty** → overlay = 0, rake = round(gross × REFILL_RAKE_PCT). Refills.

    `ai_buy_in_total` is 0 in v1 (AI seats are not charged a real buy-in —
    tourist peer-buy-ins are the deferred thermostat extension). `human_buy_in`
    is the seat price when the human opts in, else 0 (sit out ⇒ not prize-
    eligible). Negative seat prices are clamped to 0 (freeroll).
    """
    seat_price = max(0, int(seat_price))
    human_buy_in = seat_price if human_in else 0
    ai_buy_in_total = 0  # v1: AI seats bank-distributed via overlay, not charged
    gross = human_buy_in + ai_buy_in_total

    if state.regime == FLUSH:
        # Drain-to-FLOOR (trigger/floor split): size the overlay to bring reserves
        # down to RESERVE_HEALTHY in this one event — keeping the floor rather
        # than draining to the offer setpoint, so a meaningful prize goes out
        # while the bank keeps a base. Capped so even a very flush bank can't empty
        # in one event. Self-limiting — post-drain reserves sit below the trigger,
        # so the next signal won't offer until the faucet refills them.
        target = round(RESERVE_HEALTHY * state.holdings)
        bank_overlay = min(max(0, state.reserves - target), OVERLAY_CAP)
        rake = 0
    elif state.regime == EMPTY:
        bank_overlay = 0
        rake = round(gross * REFILL_RAKE_PCT)
    else:  # NEUTRAL
        bank_overlay = 0
        rake = 0

    prize_pool = human_buy_in + ai_buy_in_total + bank_overlay - rake
    return FundingPlan(
        seat_price=seat_price,
        human_buy_in=human_buy_in,
        ai_buy_in_total=ai_buy_in_total,
        bank_overlay=bank_overlay,
        rake=rake,
        prize_pool=prize_pool,
        regime=state.regime,
    )


# --- When to run a tournament: the chairman decides cadence, not a calendar ---
#
# The thermostat thesis: a FLUSH bank is the economic signal that it's time to
# run a redistribution event (drain reserves into the field). So the chairman
# owns BOTH "how big is the pool" (`tournament_funding`) AND "should there be an
# event at all" (`should_offer_event`). v1 runs the simplest version of the
# policy; the richer cases (graduated size by how-far-above-setpoint, the
# EMPTY-regime rake/"wealth-tax" refill event, a tiered daily+Main-Event slate,
# scheduled human-friendly windows) are future branches of THIS one function —
# additions, not a rearchitecture.


@dataclass(frozen=True)
class EventSpec:
    """The shape of a tournament the chairman decides to offer. Buy-in 0 (a
    freeroll) for the v1 flush event — the prize pool is the bank's overlay, so
    the human joins free to compete for distributed reserves; the field is
    funded bank → field. Kept tunable so a future tier/slate sets different
    specs per regime."""

    field_size: int
    table_size: int
    starting_stack: int
    buy_in: int


DEFAULT_MAIN_EVENT = EventSpec(field_size=18, table_size=6, starting_stack=10_000, buy_in=0)

# Minimum spacing between offers, as a belt-and-suspenders over the regime's own
# self-limiting (a successful overlay drains reserves below the setpoint, so the
# next signal isn't FLUSH). Guards the case where one event doesn't fully drain.
# Sim-tunable; wall-clock seconds compared against the last offer's timestamp.
MAIN_EVENT_COOLDOWN_SECONDS: int = 1800

# Registration window: how long an offered Main Event stays open before it
# auto-expires (→ runs autonomously, AI-only). This is the "expire = decline by
# inaction" timer — without it an offer waits forever for the player. 10 minutes
# gives the player a real decision window (the card shows a live countdown) while
# keeping the field moving if they're away. The simplest *predictability skin*
# (the future "open until 8pm" scheduled window) is just a different value
# computed per offer; the lifecycle is identical.
MAIN_EVENT_REGISTRATION_WINDOW_SECONDS: int = 600


def should_offer_event(
    state: EconomyState,
    *,
    cooldown_elapsed: bool,
    spec: EventSpec = DEFAULT_MAIN_EVENT,
) -> Optional[EventSpec]:
    """Pure policy: should the circuit offer a Main Event right now?

    Rule: **offer when reserves reach the high-water trigger
    (`ratio >= RESERVE_TRIGGER`) and the cooldown has elapsed** — the
    chairman's "time to distribute" signal. The trigger sits ABOVE the FLUSH
    regime boundary (0.12 vs 0.08) so an event only fires once the bank has
    genuinely accumulated, not the moment it crosses into flush. Below the
    trigger → no event. Returns the `EventSpec` to offer, or None.

    Time is kept OUT of this function (it takes `cooldown_elapsed` as a bool) so
    it stays pure and testable; the caller computes elapsedness from the last
    offer's timestamp against `MAIN_EVENT_COOLDOWN_SECONDS`.
    """
    if state.ratio >= RESERVE_TRIGGER and cooldown_elapsed:
        return spec
    return None


# --- Lever 2: cash-table rake schedule (signal lives here, WIRING in cash mode) ---


@dataclass(frozen=True)
class RakeSchedule:
    """Which cash-table stake tiers rake, and at what rate, for a given economy
    state. The SIBLING lever — exposed here so both levers share one signal, but
    *wired* in cash mode (it must be sim-modeled together with the tournament
    overlay before either flips on — handoff §6). Not consumed by P2.

    `stake_big_blinds` is the set of big-blind tiers that rake (mirrors
    `economy_flags.RAKE_STAKE_BIG_BLINDS`); `rate` is the top-tier rake fraction.
    """

    stake_big_blinds: frozenset[int]
    rate: float
    regime: str


# Graduated rake bands, keyed on the canonical reserve ladder above (RESERVE_*).
# As the bank empties the Director expands BOTH levers — the raked stake tiers
# AND the rate — and contracts both as reserves recover (EXP_006's lever:
# "$1000-only when flush; switch on $200, then $50 if dire"). Only the per-band
# tier sets and RATES are rake-specific; the band EDGES are the shared ladder, so
# vice (refill) and rake (throttle) can't drift apart. The $1000 tier is present
# in every band, so the structural rake is never switched off.
_RAKE_TIERS_HEALTHY: frozenset[int] = frozenset({1000})
_RAKE_TIERS_LOW: frozenset[int] = frozenset({1000, 200})
_RAKE_TIERS_CRITICAL: frozenset[int] = frozenset({1000, 200, 50})
_RAKE_RATE_BASE: float = 0.02
_RAKE_RATE_LOW: float = 0.03
_RAKE_RATE_CRITICAL: float = 0.04


def cash_rake_schedule(state: EconomyState) -> RakeSchedule:
    """Pure policy: graduated cash-rake response to the reserve ratio.

    Three bands (edges = the shared RESERVE_* ladder), each lifting BOTH the
    raked stake tiers and the rate as the deficit deepens:
      * healthy (ratio ≥ RESERVE_HEALTHY) → ``{1000}`` @ 2% (throttle inflow),
      * low (RESERVE_CRITICAL ≤ ratio < RESERVE_HEALTHY) → ``{1000, 200}`` @ 3%,
      * critical (ratio < RESERVE_CRITICAL) → ``{1000, 200, 50}`` @ 4%.

    Wired into cash mode via `economy_flags.resolve_rake_params`; here it is a
    pure, tested function so the chairman owns BOTH levers off one snapshot.
    """
    if state.ratio < RESERVE_CRITICAL:
        return RakeSchedule(
            stake_big_blinds=_RAKE_TIERS_CRITICAL,
            rate=_RAKE_RATE_CRITICAL,
            regime=state.regime,
        )
    if state.ratio < RESERVE_HEALTHY:
        return RakeSchedule(
            stake_big_blinds=_RAKE_TIERS_LOW,
            rate=_RAKE_RATE_LOW,
            regime=state.regime,
        )
    return RakeSchedule(
        stake_big_blinds=_RAKE_TIERS_HEALTHY,
        rate=_RAKE_RATE_BASE,
        regime=state.regime,
    )
