"""Closed-economy testbed: fake-vice deposits feed the bank pool.

This is the SIM testbed for the closed-loop economy thesis in
`docs/plans/CASH_MODE_CLOSED_ECONOMY.md`.

  `resolve_fake_vice_deposits` — stub for real AI vice. Drains
  chips from rich AIs into the central bank's recyclable pool.
  Same probability/amount shape as `CASH_MODE_AI_VICE_SPENDING.md`
  but without the psych-pressure modifier (testbed doesn't depend
  on cached controller state). Real vice replaces this drop-in.

The companion to vice deposits — refilling fish bankrolls via
`tourist_injection` — was removed when ephemeral tourists replaced
persistent fish (see `docs/plans/CASH_MODE_EPHEMERAL_TOURISTS.md`).
The bank pool now funds on-demand casino spawns instead of refilling
named fish, so injection became unnecessary.

Bank pool depth is `Σ(BANK_POOL_DEPOSIT_REASONS) − Σ(BANK_POOL_DRAW_REASONS)`,
computed on demand from `chip_ledger_entries`. No new state table; the
pool is virtual.

The conservation invariant from `CASH_MODE_ECONOMY.md` holds:
every chip movement here writes a ledger row, so `drift == 0` stays
correct as long as the bankroll writes pair with their ledger entries.
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


def load_fish_ids(bankroll_repo, *, sandbox_id: Optional[str] = None) -> Set[str]:
    """Personality_ids tagged `archetype: "fish"` in `config_json`.

    Walks `iter_personality_ids_with_bankrolls(sandbox_id=...)`, calls
    `load_archetype` on each, filters to fish. Fish not yet seeded
    into this sandbox's `ai_bankroll_state` won't appear — they only
    enter the eligibility loop once they've been seated at least once.

    Returns both template fish (`vacation_greg`, etc.) and ephemeral
    instances (`vacation_greg__eph_*`). Use `load_is_ephemeral_fish`
    to distinguish if needed.
    """
    if bankroll_repo is None:
        return set()
    pids = bankroll_repo.iter_personality_ids_with_bankrolls(sandbox_id=sandbox_id)
    return {pid for pid in pids if bankroll_repo.load_archetype(pid) == 'fish'}


# --- Ephemeral fish spawning ------------------------------------------
#
# Fish are a casino-only player class with no long-term lifecycle. The
# 4 base entries in `personalities.json` (Vacation Greg, Bachelorette
# Brenda, Cruise Carl, Birthday Bobby) are **templates** — they're
# never seated directly. Casino spawn / refill clones a template into
# an ephemeral instance with an alliterative first-name variant. When
# the casino tears down, those instances get deleted.
#
# The pid pattern `<template>__eph_<6char>` doubles as the
# template→instance link and the cleanup filter.


# Per-template alliterative name pools. Each entry replaces the
# template's first name (e.g. Vacation **Greg** → Vacation **Glenn**)
# so casino dossiers feel like a rotating cast rather than the same
# four NPCs over and over.
EPHEMERAL_FISH_NAME_POOLS: dict = {
    'vacation_greg': [
        'Gary', 'Glenn', 'Gus', 'Greta', 'Gabe', 'Gordon', 'Gertie',
        'Gwen', 'Geoff', 'Gail', 'Gunther', 'Gloria',
    ],
    'bachelorette_brenda': [
        'Bea', 'Becca', 'Bridget', 'Belinda', 'Bonnie', 'Betsy',
        'Brittany', 'Babs', 'Blair', 'Britt', 'Bianca', 'Beth',
    ],
    'cruise_carl': [
        'Cal', 'Casey', 'Chuck', 'Curtis', 'Cliff', 'Cooper',
        'Cody', 'Connor', 'Cyrus', 'Clement', 'Conrad', 'Clark',
    ],
    'birthday_bobby': [
        'Brett', 'Boyd', 'Brad', 'Bruno', 'Bart', 'Burt', 'Buddy',
        'Bo', 'Beau', 'Buster', 'Buck', 'Barry',
    ],
}

# All templates supported by the spawn logic. Keeping this explicit
# (vs. discovered at runtime) so a new template fails loudly when
# its name pool is missing.
EPHEMERAL_FISH_TEMPLATES = tuple(EPHEMERAL_FISH_NAME_POOLS.keys())


def _ephemeral_pid(template_pid: str, rng: random.Random) -> str:
    """Generate `<template>__eph_<6char>` from a 32-char hex pool."""
    hex_chars = '0123456789abcdef'
    suffix = ''.join(rng.choices(hex_chars, k=6))
    return f"{template_pid}__eph_{suffix}"


def is_ephemeral_fish_pid(pid: str) -> bool:
    """True iff `pid` matches the ephemeral-fish naming convention."""
    return '__eph_' in pid


def spawn_ephemeral_fish(
    *,
    template_pid: str,
    personality_repo,
    bankroll_repo,
    rng: random.Random,
    sandbox_id: str,
    now: datetime,
    chip_ledger_repo=None,
) -> Optional[tuple]:
    """Clone a template fish into a new ephemeral instance.

    Picks an alliterative first-name variant from the template's pool,
    generates a unique pid, writes a personality row (with
    `is_ephemeral: True` in config_json), and creates a zero-chip
    bankroll state row in the sandbox. The casino spawner credits
    actual chips at seat time via `record_casino_seat_seed`.

    Returns `(personality_id, display_name)` on success, or None when
    the template is unknown / repo writes fail.

    Caller is responsible for seating the new pid and recording the
    seat-seed ledger entry — this function does NOT touch the bank
    pool. (Decoupled so the same generator can be reused for refills
    where chips and personality come from separate code paths.)
    """
    if template_pid not in EPHEMERAL_FISH_NAME_POOLS:
        logger.warning(
            "[CASH][FISH] unknown ephemeral fish template %r — "
            "no name pool registered; skipping",
            template_pid,
        )
        return None

    name_pool = EPHEMERAL_FISH_NAME_POOLS[template_pid]
    variant_name = rng.choice(name_pool)

    # Pull the template config so the new instance inherits anchors,
    # tics, knobs, etc. `load_personality_by_id` returns the config
    # dict directly with `name` + `id` populated.
    template_config = personality_repo.load_personality_by_id(template_pid)
    if not template_config:
        logger.warning(
            "[CASH][FISH] template %r not in DB; cannot spawn ephemeral",
            template_pid,
        )
        return None

    # Build the variant display name by swapping the template's first
    # word (e.g. "Vacation Greg" → "Vacation Glenn"). Falls back to
    # `template_name + variant_name` when the template name is
    # single-word (shouldn't happen with current templates).
    template_name = template_config.get('name', '') or ''
    parts = template_name.split(' ', 1)
    if len(parts) >= 2:
        display_name = f"{parts[0]} {variant_name}"
    else:
        display_name = f"{template_name} {variant_name}".strip()

    # Compose the ephemeral config: inherit everything, mark as
    # ephemeral + record the template lineage for cleanup / debug.
    ephemeral_config = dict(template_config)
    ephemeral_config['is_ephemeral'] = True
    ephemeral_config['template_personality_id'] = template_pid
    ephemeral_config.pop('id', None)  # let save_personality assign
    ephemeral_config.pop('name', None)  # name is the row name; not in config_json
    # Drop the template's pre-baked bankroll knobs that don't apply to
    # an instance (e.g. fixed starting_bankroll) — the casino seat
    # seeds chips directly; the bankroll row stays at 0.

    new_pid = _ephemeral_pid(template_pid, rng)
    try:
        personality_repo.save_personality(
            display_name,
            ephemeral_config,
            source='ephemeral_fish',
            personality_id=new_pid,
        )
    except Exception as exc:
        logger.warning(
            "[CASH][FISH] save_personality failed for %s: %s",
            new_pid, exc,
        )
        return None

    # Bankroll row starts at 0 — fish chips live at the seat, not in
    # a bankroll account. The seat seed (`casino_seat_seed`) handles
    # the actual chip creation from the bank pool.
    try:
        bankroll_repo.save_ai_bankroll(
            AIBankrollState(
                personality_id=new_pid,
                chips=0,
                last_regen_tick=now,
            ),
            sandbox_id=sandbox_id,
            chip_ledger_repo=chip_ledger_repo,
        )
    except Exception as exc:
        logger.warning(
            "[CASH][FISH] save_ai_bankroll failed for %s: %s",
            new_pid, exc,
        )
        # Personality row exists but bankroll doesn't — caller will
        # see this as a failure via the None return and skip seating.
        return None

    logger.info(
        "[CASH][FISH] spawned ephemeral fish %s (%s) from template %s",
        new_pid, display_name, template_pid,
    )
    return (new_pid, display_name)


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
