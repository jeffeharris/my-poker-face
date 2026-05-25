"""Closed-economy: bank-pool plumbing + fake-vice testbed + grinder demand.

Shipping pieces of the closed-loop economy thesis in
`docs/plans/CASH_MODE_CLOSED_ECONOMY.md` + the fish-as-personas pivot in
`docs/plans/CASH_MODE_FISH_AS_PERSONAS.md`.

What lives here:

  - **Bank-pool query** (`compute_bank_pool_reserves`, `seed_bank_pool`):
    virtual depth = Σ(BANK_POOL_DEPOSIT_REASONS) − Σ(BANK_POOL_DRAW_REASONS),
    computed on demand from `chip_ledger_entries`. No state table.
    `seed_bank_pool` writes a drift-safe paired entry for sim cold-start.

  - **Grinder demand signal** (`is_hungry_grinder`, `list_hungry_grinders`):
    identifies AIs whose bankroll has dropped below
    `GRINDER_HUNGER_THRESHOLD × starting_bankroll` and whose comfort zone
    is the casino tier. Casino spawn gates on this.

  - **`resolve_fake_vice_deposits`** — stub for real AI vice. Drains chips
    from rich AIs into the recyclable bank pool. Same probability / amount
    shape as `CASH_MODE_AI_VICE_SPENDING.md` but without the psych-pressure
    modifier (testbed doesn't depend on cached controller state). Real vice
    drops in over it.

Casino bankroll funding is **not** here — it lives in
`cash_mode/casino_provisioning.py` (`_prefund_fish_from_pool`,
`_drain_fish_bankroll_to_pool`) so the spawn/refill/teardown lifecycle
keeps the chip-flow code adjacent to the table-lifecycle code.

The conservation invariant from `CASH_MODE_ECONOMY.md` holds: every chip
movement writes a ledger row, so `drift == 0` stays correct as long as
bankroll writes pair with their ledger entries.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Set

from core.economy import ledger as chip_ledger
from core.economy.ledger import (
    BANK_POOL_DEPOSIT_REASONS,
    BANK_POOL_DRAW_REASONS,
    ai,
    record_bank_pool_deposit,
    record_bank_pool_sim_seed_pair,
)
from cash_mode.bankroll import AIBankrollState, project_bankroll

logger = logging.getLogger(__name__)


# --- Tuning constants -------------------------------------------------
# Mirror `CASH_MODE_AI_VICE_SPENDING.md` shape so real vice can drop in.

# Fish seating — fish-archetype personalities only seat at these stake
# labels. Mirrors the diagram's casino tier (the place tourists arrive).
# Defined here so the filter point in `cash_mode/lobby.py` and the
# eligibility tests can share one source of truth.
CASINO_TIER_STAKE_LABELS = frozenset({'$2'})

# Fake-vice trigger / amount
FAKE_VICE_COMFORT_FLOOR = 1.2
FAKE_VICE_EXCESS_WEIGHT = 0.04
FAKE_VICE_MAX_PROB = 0.25
FAKE_VICE_BASE_FRACTION = 0.02
FAKE_VICE_EXCESS_FRACTION_WEIGHT = 0.03
FAKE_VICE_MAX_FRACTION = 0.15
FAKE_VICE_FLOOR_PROTECTION = 0.5
MIN_VICE_AMOUNT = 50
FAKE_VICE_DEPOSITS_PER_REFRESH = 3

# Grinder definition — the AIs that come to the casino to farm fish.
# A "hungry grinder" satisfies all three:
#   • archetype != 'fish' (fish farm nobody)
#   • stake_comfort_zone in {'$2', '$10'} (casino is their natural tier)
#   • current bankroll < starting × GRINDER_HUNGER_THRESHOLD
# The hunger condition is the load-bearing one — a grinder at peak
# wealth has no economic pressure to farm; a grinder at 40% of their
# starting bankroll is desperate to recover.
GRINDER_HUNGER_THRESHOLD = 0.8
GRINDER_COMFORT_ZONES = frozenset({'$2', '$10'})

# --- Dataclasses ------------------------------------------------------


@dataclass(frozen=True)
class FakeViceDeposit:
    """One stub-vice event: chips drained from a rich AI into the bank pool."""

    personality_id: str
    amount: int
    excess_ratio: float
    vice_prob: float


@dataclass(frozen=True)
class ClosedEconomyBatch:
    """Result of one closed-economy resolution tick.

    Post-EPHEMERAL_TOURISTS: the `injections` field was removed (tourist
    injection no longer fires — ephemeral tourists have no bankrolls to
    refill). Callers that historically read `batch.injections` should
    treat the field as gone; pool depth is derived from `bank_pool_before`
    vs `bank_pool_after` instead.
    """

    deposits: List[FakeViceDeposit] = field(default_factory=list)
    bank_pool_before: int = 0
    bank_pool_after: int = 0


# --- Pure formulas ----------------------------------------------------


def compute_excess_ratio(bankroll: int, starting_bankroll: int) -> float:
    """Wealth above the comfort floor, expressed as multiples of starting.

    `excess_ratio = max(0, (bankroll − starting × COMFORT_FLOOR) / starting)`.
    Returns 0.0 for broke / floor-protected AIs.
    """
    if starting_bankroll <= 0:
        return 0.0
    floor = starting_bankroll * FAKE_VICE_COMFORT_FLOOR
    return max(0.0, (bankroll - floor) / starting_bankroll)


def compute_vice_probability(excess_ratio: float) -> float:
    """Probability of a vice event firing for this AI on this refresh.

    Capped at `FAKE_VICE_MAX_PROB`. Returns 0 for non-excess AIs.
    Production AI vice will multiply this by a pressure factor; the
    testbed omits pressure to keep the model dependency-free.
    """
    if excess_ratio <= 0:
        return 0.0
    return min(FAKE_VICE_MAX_PROB, excess_ratio * FAKE_VICE_EXCESS_WEIGHT)


def compute_vice_amount(
    bankroll: int, excess_ratio: float, rng: random.Random,
) -> int:
    """Amount drained on a fire. Scales with excess; random multiplier
    spreads events across visually distinct sizes.

    Capped at `bankroll × FAKE_VICE_MAX_FRACTION` per event.
    """
    if bankroll <= 0 or excess_ratio <= 0:
        return 0
    fraction = FAKE_VICE_BASE_FRACTION + excess_ratio * FAKE_VICE_EXCESS_FRACTION_WEIGHT
    raw = bankroll * fraction * rng.uniform(0.5, 1.5)
    max_per_event = bankroll * FAKE_VICE_MAX_FRACTION
    return int(min(raw, max_per_event))


# --- Bank pool query --------------------------------------------------


def seed_bank_pool(
    chip_ledger_repo,
    *,
    sandbox_id: str,
    amount: int,
) -> int:
    """Inflate the bank pool by `amount` at sandbox / sim start.

    Thin wrapper around `record_bank_pool_sim_seed_pair` — exposed
    here so closed-economy callers don't have to reach into the
    ledger module. Returns the amount actually seeded (matches input
    on success, 0 when the repo / amount is invalid).

    Use at the start of a sim run to overcome the cold-start
    chicken-and-egg (without a seed, no tourist injection can fire
    until rich AIs vice first). Operator-controlled inflation —
    drift stays at 0 via the paired creation/destruction.
    """
    if chip_ledger_repo is None or amount <= 0:
        return 0
    record_bank_pool_sim_seed_pair(
        chip_ledger_repo,
        amount=amount,
        sandbox_id=sandbox_id,
    )
    return int(amount)


def compute_bank_pool_reserves(
    chip_ledger_repo,
    *,
    sandbox_id: Optional[str] = None,
) -> int:
    """Bank pool depth = Σ(deposit_reasons) − Σ(draw_reasons).

    Pool is virtual — no row stores it. Reads ledger sums directly via
    the same helpers the audit endpoint uses. Per-sandbox by default.

    Deposit reasons include `bank_pool_deposit` (fake-vice + future real
    vice). Draw reasons include `tourist_injection` (bankroll refill)
    and `casino_seat_seed` (atomic casino spawn). Adding another
    deposit or draw is a one-line frozenset update.

    Returns 0 when `chip_ledger_repo` is None (test paths that don't
    care about ledger state) or when the ledger has no relevant rows.
    """
    if chip_ledger_repo is None:
        return 0
    destructions = chip_ledger_repo.sum_destructions_by_reason(sandbox_id=sandbox_id)
    creations = chip_ledger_repo.sum_creations_by_reason(sandbox_id=sandbox_id)
    deposits = sum(destructions.get(r, 0) for r in BANK_POOL_DEPOSIT_REASONS)
    draws = sum(creations.get(r, 0) for r in BANK_POOL_DRAW_REASONS)
    return int(deposits - draws)


# --- Fish discovery ---------------------------------------------------


def is_hungry_grinder(
    personality_id: str,
    *,
    bankroll_repo,
    sandbox_id: str,
    now: datetime,
) -> bool:
    """True iff this AI is a casino-tier grinder currently below hunger threshold.

    Three filters AND'd:
      1. archetype != 'fish' (fish are donors, not grinders)
      2. stake_comfort_zone in `GRINDER_COMFORT_ZONES` ($2 or $10)
      3. projected bankroll < `starting_bankroll × GRINDER_HUNGER_THRESHOLD`

    Used by:
      • Casino spawn demand signal (need ≥ MIN_HUNGRY_GRINDERS before
        a casino opens — no point spawning if nobody wants to play).
      • Grinder pull at casino tables (sort the idle pool by hunger
        so the most desperate grinders get casino seats first).
    """
    if bankroll_repo is None:
        return False
    if bankroll_repo.load_archetype(personality_id) == 'fish':
        return False
    knobs = bankroll_repo.load_personality_knobs(personality_id)
    if knobs.stake_comfort_zone not in GRINDER_COMFORT_ZONES:
        return False
    if knobs.starting_bankroll <= 0:
        return False
    state = bankroll_repo.load_ai_bankroll(personality_id, sandbox_id=sandbox_id)
    if state is None:
        # Never been seeded — treat as "not currently hungry" (will get
        # picked up by other seating paths once they have a bankroll).
        return False
    projected = project_bankroll(
        state, knobs.starting_bankroll, knobs.bankroll_rate, now,
    )
    return projected < knobs.starting_bankroll * GRINDER_HUNGER_THRESHOLD


def list_hungry_grinders(
    bankroll_repo,
    *,
    sandbox_id: str,
    now: datetime,
    exclude: Optional[Set[str]] = None,
) -> List[str]:
    """Return personality_ids of hungry grinders, most-desperate first.

    Sort order: ascending `projected / starting_bankroll` ratio — so
    the AI with the deepest deficit comes first. Ties broken by
    personality_id for determinism.

    `exclude` is a set of pids to skip (e.g. AIs already seated). Pass
    the global-seated set to avoid double-picking the same pid for
    multiple tables.
    """
    if bankroll_repo is None:
        return []
    exclude = exclude or set()
    pids = bankroll_repo.iter_personality_ids_with_bankrolls(sandbox_id=sandbox_id)
    ratios: List[tuple] = []  # (ratio, pid)
    for pid in pids:
        if pid in exclude:
            continue
        if not is_hungry_grinder(
            pid, bankroll_repo=bankroll_repo, sandbox_id=sandbox_id, now=now,
        ):
            continue
        knobs = bankroll_repo.load_personality_knobs(pid)
        state = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
        projected = project_bankroll(
            state, knobs.starting_bankroll, knobs.bankroll_rate, now,
        )
        ratio = projected / knobs.starting_bankroll
        ratios.append((ratio, pid))
    ratios.sort(key=lambda r: (r[0], r[1]))
    return [pid for _, pid in ratios]


def list_affordable_predators(
    bankroll_repo,
    *,
    sandbox_id: str,
    min_buy_in: int,
    now: datetime,
    exclude: Optional[Set[str]] = None,
) -> List[str]:
    """Return non-fish AIs that can afford `min_buy_in`, richest first.

    The whale's predator pool. A whale sits at a high-stakes cardroom
    ($50 / $200), so the casino-tier "hungry grinder" signal (bankroll
    below 80% of starting AND comfort zone in {$2, $10}) doesn't fit —
    nobody hungry for $2 can sit at $200. Here the gate is simply
    affordability: whoever can buy into the whale's table is a candidate,
    and the deepest-pocketed come first (they can stay and grind the deep
    stack down rather than busting out after one cooler).

    Fish are excluded (a fish farms nobody, and the whale itself is a
    fish — we never pull it toward its own table). Sort: descending
    projected bankroll, ties broken by personality_id for determinism.
    `exclude` skips pids (e.g. the globally-seated set).
    """
    if bankroll_repo is None:
        return []
    exclude = exclude or set()
    fish = load_fish_ids(bankroll_repo, sandbox_id=sandbox_id)
    pids = bankroll_repo.iter_personality_ids_with_bankrolls(sandbox_id=sandbox_id)
    scored: List[tuple] = []  # (projected, pid)
    for pid in pids:
        if pid in exclude or pid in fish:
            continue
        knobs = bankroll_repo.load_personality_knobs(pid)
        state = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
        projected = project_bankroll(
            state, knobs.starting_bankroll, knobs.bankroll_rate, now,
        )
        if projected >= min_buy_in:
            scored.append((projected, pid))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [pid for _, pid in scored]


def load_fish_ids(bankroll_repo, *, sandbox_id: Optional[str] = None) -> Set[str]:
    """Personality_ids tagged `archetype: "fish"` in `config_json`.

    Walks `iter_personality_ids_with_bankrolls(sandbox_id=...)`, calls
    `load_archetype` on each, filters to fish. Fish not yet seeded
    into this sandbox's `ai_bankroll_state` won't appear — they only
    enter the eligibility loop once they've been seated at least once.
    Returns the curated, permanent fish personas (`vacation_greg`,
    etc.) that have been seeded into this sandbox. Fish are real
    personalities now — there are no synthetic instances to
    distinguish (see CASH_MODE_FISH_AS_PERSONAS.md).

    For the seat-eligible pool (every curated fish persona regardless
    of sandbox state), use `personality_repo.list_fish_for_cash_mode`
    instead.
    """
    if bankroll_repo is None:
        return set()
    pids = bankroll_repo.iter_personality_ids_with_bankrolls(sandbox_id=sandbox_id)
    return {pid for pid in pids if bankroll_repo.load_archetype(pid) == 'fish'}


# --- Resolvers --------------------------------------------------------


def resolve_fake_vice_deposits(
    *,
    bankroll_repo,
    chip_ledger_repo,
    sandbox_id: str,
    rng: random.Random,
    now: datetime,
    fish_ids: Set[str],
) -> List[FakeViceDeposit]:
    """Drain chips from rich non-fish AIs into the bank pool.

    Iterates every AI with a bankroll in the sandbox, rolls the vice
    formula, commits the chip move + ledger entry on a fire. Fish are
    excluded — they receive injections, they don't contribute.

    Cap is `FAKE_VICE_DEPOSITS_PER_REFRESH`; if more candidates roll
    positive, the largest amounts win. The rest re-roll next refresh.
    """
    if bankroll_repo is None or chip_ledger_repo is None:
        return []

    candidates = bankroll_repo.iter_personality_ids_with_bankrolls(
        sandbox_id=sandbox_id,
    )

    rolls: List[tuple] = []  # (pid, state, knobs, projected, amount, excess, prob)
    for pid in candidates:
        if pid in fish_ids:
            continue
        state = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
        if state is None:
            continue
        knobs = bankroll_repo.load_personality_knobs(pid)
        starting = knobs.starting_bankroll
        if starting <= 0:
            continue
        projected = project_bankroll(state, starting, knobs.bankroll_rate, now)
        excess = compute_excess_ratio(projected, starting)
        if excess <= 0:
            continue
        prob = compute_vice_probability(excess)
        if rng.random() >= prob:
            continue
        amount = compute_vice_amount(projected, excess, rng)
        if amount < MIN_VICE_AMOUNT:
            continue
        # Floor protection — never drop below 50% of starting in one event.
        floor = int(starting * FAKE_VICE_FLOOR_PROTECTION)
        if projected - amount < floor:
            amount = max(0, projected - floor)
            if amount < MIN_VICE_AMOUNT:
                continue
        rolls.append((pid, state, knobs, projected, amount, excess, prob))

    # Cap per refresh — largest deposits first.
    rolls.sort(key=lambda r: r[4], reverse=True)
    rolls = rolls[:FAKE_VICE_DEPOSITS_PER_REFRESH]

    deposits: List[FakeViceDeposit] = []
    for pid, state, knobs, projected, amount, excess, prob in rolls:
        # Commit any uncommitted regen first (matches the `try_ai_voluntary_payoff`
        # pattern in ai_carry_resolution — the bankroll write captures the
        # projected value, so the regen delta needs a ledger row.)
        if projected > state.chips:
            chip_ledger.record_ai_regen(
                chip_ledger_repo,
                personality_id=pid,
                stored_chips=state.chips,
                projected_chips=projected,
                context={'site': 'fake_vice_regen_commit'},
                sandbox_id=sandbox_id,
            )
        new_chips = max(0, projected - amount)
        bankroll_repo.save_ai_bankroll(
            AIBankrollState(
                personality_id=pid,
                chips=new_chips,
                last_regen_tick=now,
            ),
            sandbox_id=sandbox_id,
        )
        record_bank_pool_deposit(
            chip_ledger_repo,
            source=ai(pid),
            amount=amount,
            context={
                'site': 'fake_vice_deposit',
                'excess_ratio': round(excess, 3),
                'vice_prob': round(prob, 3),
            },
            sandbox_id=sandbox_id,
        )
        deposits.append(FakeViceDeposit(
            personality_id=pid,
            amount=amount,
            excess_ratio=round(excess, 3),
            vice_prob=round(prob, 3),
        ))
    return deposits


def resolve_closed_economy(
    *,
    bankroll_repo,
    chip_ledger_repo,
    sandbox_id: str,
    rng: random.Random,
    now: datetime,
) -> ClosedEconomyBatch:
    """One closed-economy resolution tick.

    Runs fake-vice deposits and captures the bank pool delta. Post-
    EPHEMERAL_TOURISTS, tourist injection was removed — pool reserves
    now fund on-demand casino spawns instead of refilling named fish
    bankrolls. Wrapped in try/except so a vice failure doesn't tank
    the resolve (mirrors the carry-resolution best-effort pattern).
    """
    pool_before = compute_bank_pool_reserves(
        chip_ledger_repo, sandbox_id=sandbox_id,
    )
    fish_ids = load_fish_ids(bankroll_repo, sandbox_id=sandbox_id)
    deposits: List[FakeViceDeposit] = []
    try:
        deposits = resolve_fake_vice_deposits(
            bankroll_repo=bankroll_repo,
            chip_ledger_repo=chip_ledger_repo,
            sandbox_id=sandbox_id,
            rng=rng,
            now=now,
            fish_ids=fish_ids,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort resolution
        logger.warning(
            "[CLOSED_ECONOMY] fake-vice deposit failed for sandbox %s: %s",
            sandbox_id, exc,
        )
    pool_after = compute_bank_pool_reserves(
        chip_ledger_repo, sandbox_id=sandbox_id,
    )
    return ClosedEconomyBatch(
        deposits=deposits,
        bank_pool_before=pool_before,
        bank_pool_after=pool_after,
    )
