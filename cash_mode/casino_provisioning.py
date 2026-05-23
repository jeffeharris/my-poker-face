"""Casino provisioning — spawn / teardown `table_type='casino'` venues.

Spec: `docs/plans/CASH_MODE_CLOSED_ECONOMY.md`. This is the table-level
companion to `closed_economy.py`'s bank-pool / tourist-injection flows.

What a casino is:
  - A `cash_tables` row with `table_type='casino'`, distinct from the
    public lobby tables (`table_type='lobby'`).
  - Spawned when the bank pool has enough reserves to fund a full
    fish lineup at one of the configured casino stakes.
  - Pre-seeded at spawn: N fish placed in seats with `casino_seat_seed`
    chips drawn from the pool. Open seats are live-fillable by
    grinder AIs who come to farm.
  - Torn down when all fish are busted AND the pool can't fund more.

Spawn / teardown both run inside `refresh_unseated_tables` (lobby
refresh), wrapped best-effort. The resolver is idempotent across
ticks — it's safe to call every refresh.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from cash_mode.closed_economy import (
    CASINO_TIER_STAKE_LABELS,
    EPHEMERAL_FISH_TEMPLATES,
    compute_bank_pool_reserves,
    is_ephemeral_fish_pid,
    is_hungry_grinder,
    list_hungry_grinders,
    load_fish_ids,
    spawn_ephemeral_fish,
)
from cash_mode.stakes_ladder import (
    STAKES_LADDER,
    STAKES_ORDER,
    table_buy_in_window,
)
from cash_mode.tables import (
    CashTableState,
    TABLE_SEAT_COUNT,
    ai_slot,
    open_slot,
)
from core.economy.ledger import record_casino_seat_seed

logger = logging.getLogger(__name__)


# --- Tuning constants -------------------------------------------------

# Pool depth required to spawn a casino at each stake. The $2 casino
# needs enough to cover N fish × min_buy_in (40 BB × N fish). For
# default 4 fish at $2: 4 × 80 = 320 chips minimum. The 5K threshold
# leaves a generous buffer for refills + a $10 casino's chance to grow.
CASINO_SPAWN_THRESHOLDS: Dict[str, int] = {
    '$2': 5_000,
    '$10': 50_000,
}

# Fish-per-casino range. Spawns pick a random count in [MIN, MAX];
# the refill pass keeps casinos topped up to MAX as long as the pool
# can fund it. Less than TABLE_SEAT_COUNT (6) so grinders have seats
# to fill. 2-4 fish + 2-4 grinder seats is the canonical mix.
CASINO_FISH_MIN = 2
CASINO_FISH_MAX = 4

# Minimum hungry grinders required in the idle pool before a casino
# opens. The demand signal — no point spawning a casino if nobody is
# trying to farm fish.
CASINO_MIN_HUNGRY_GRINDERS = 1

# Fish buy-in as a multiple of table min_buy_in. 1.0 = standard short
# buy-in (40 BB). Higher gives fish more chips to lose before busting;
# lower means more frequent casino refills.
CASINO_FISH_BUY_IN_MULTIPLIER = 1.0

# Casino table_id format. Distinct from lobby (`cash-table-...`) so
# the audit can pick them apart.
CASINO_TABLE_ID_PREFIX = "cash-casino"

# Smooth shutdown — when teardown conditions are met, the casino enters
# a 'closing' state with this many hands remaining instead of being
# deleted immediately. Each hand played at the casino decrements the
# counter; on 0, the table is actually deleted. During closing:
#   • No fish refills (we're winding down)
#   • No new casinos can spawn at the same stake (one slot per stake)
#   • The display name surfaces the countdown so the lobby UI can
#     warn players
CASINO_CLOSING_HAND_COUNTDOWN = 10


# --- DB-backed closing-state API -------------------------------------
#
# Closing state lives in the `cash_tables.closing_hand_countdown`
# column (v113). The helpers below are thin wrappers around the repo;
# they exist so call sites (lobby refresh, hand-boundary hooks) don't
# have to thread the repo through every layer of casino logic.
#
# Survives backend restart (the column is durable). Concurrent
# decrement is repo-local (one process per backend, one DB connection
# per call).


def enter_closing(
    cash_table_repo,
    sandbox_id: str,
    table_id: str,
    countdown: int = CASINO_CLOSING_HAND_COUNTDOWN,
) -> None:
    """Mark a casino as closing with `countdown` hands remaining."""
    if cash_table_repo is None:
        return
    cash_table_repo.set_closing_countdown(
        table_id, sandbox_id=sandbox_id, countdown=countdown,
    )


def is_closing(cash_table_repo, sandbox_id: str, table_id: str) -> bool:
    """True iff this casino is currently in closing state."""
    if cash_table_repo is None:
        return False
    return cash_table_repo.get_closing_countdown(
        table_id, sandbox_id=sandbox_id,
    ) is not None


def get_closing_countdown(
    cash_table_repo, sandbox_id: str, table_id: str,
) -> Optional[int]:
    """Hands remaining for a closing casino, or None if not closing."""
    if cash_table_repo is None:
        return None
    return cash_table_repo.get_closing_countdown(
        table_id, sandbox_id=sandbox_id,
    )


def decrement_closing_hands(
    cash_table_repo, sandbox_id: str, table_id: str, count: int = 1,
) -> Optional[int]:
    """Tick down the closing countdown. Returns the new count, or None
    if the casino isn't in closing state.

    Called from each hand-boundary path — the full-sim loop and the
    human-play `leave_hand` hook — once per completed hand at the
    table. On reaching 0, the next provisioning resolution actually
    deletes the row.
    """
    if cash_table_repo is None:
        return None
    current = cash_table_repo.get_closing_countdown(
        table_id, sandbox_id=sandbox_id,
    )
    if current is None:
        return None
    new_count = max(0, current - count)
    cash_table_repo.set_closing_countdown(
        table_id, sandbox_id=sandbox_id, countdown=new_count,
    )
    return new_count


def clear_closing(
    cash_table_repo, sandbox_id: str, table_id: str,
) -> None:
    """Reset the countdown to NULL ('active' state). Used when an
    actual delete fires (defensive — the delete removes the row) or
    if external logic ever wants to un-close a table."""
    if cash_table_repo is None:
        return
    cash_table_repo.set_closing_countdown(
        table_id, sandbox_id=sandbox_id, countdown=None,
    )


# --- Dataclasses ------------------------------------------------------


@dataclass(frozen=True)
class CasinoSpawn:
    """A full casino spawn event (new table + initial fish lineup)."""
    table_id: str
    stake_label: str
    fish_seated: List[str]
    bank_pool_drawn: int


@dataclass(frozen=True)
class CasinoRefill:
    """An incremental refill at an active casino (one fish, one open seat)."""
    table_id: str
    stake_label: str
    fish_id: str
    bank_pool_drawn: int


@dataclass(frozen=True)
class CasinoTeardown:
    """A casino teardown event."""
    table_id: str
    stake_label: str
    reason: str


@dataclass(frozen=True)
class CasinoProvisioningBatch:
    """Result of one provisioning tick."""
    spawns: List[CasinoSpawn] = field(default_factory=list)
    refills: List[CasinoRefill] = field(default_factory=list)
    teardowns: List[CasinoTeardown] = field(default_factory=list)


# --- Helpers ----------------------------------------------------------


def _casino_table_id(stake_label: str, suffix: str = "001") -> str:
    """Return the canonical casino table_id for a stake."""
    slug = stake_label[1:] if stake_label.startswith('$') else stake_label
    return f"{CASINO_TABLE_ID_PREFIX}-{slug}-{suffix}"


def _existing_casinos_by_stake(
    cash_table_repo, *, sandbox_id: str,
) -> Dict[str, List[CashTableState]]:
    """Group active casino tables by stake_label."""
    by_stake: Dict[str, List[CashTableState]] = {}
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        if table.table_type != 'casino':
            continue
        by_stake.setdefault(table.stake_label, []).append(table)
    return by_stake


def _casino_has_seated_fish(
    table: CashTableState, fish_ids: Set[str],
) -> bool:
    """True iff any seat holds a fish-archetype personality."""
    for slot in table.seats:
        if slot.get('kind') == 'ai' and slot.get('personality_id') in fish_ids:
            return True
    return False


# --- Resolver ---------------------------------------------------------


def _count_seated_fish(table: CashTableState, fish_ids: Set[str]) -> int:
    """Return the number of fish currently seated at this casino."""
    return sum(
        1 for slot in table.seats
        if slot.get('kind') == 'ai' and slot.get('personality_id') in fish_ids
    )


def _open_seat_indices(table: CashTableState) -> List[int]:
    """Return seat indices currently open (live-fillable)."""
    return [i for i, slot in enumerate(table.seats) if slot.get('kind') == 'open']


def _refill_one_fish(
    table: CashTableState,
    *,
    stake_label: str,
    fish_buy_in: int,
    chip_ledger_repo,
    cash_table_repo,
    personality_repo,
    bankroll_repo,
    sandbox_id: str,
    rng: random.Random,
    now: datetime,
    already_seated: Set[str],
    fish_ids: Set[str],
) -> Optional[CasinoRefill]:
    """Spawn a new ephemeral fish and seat it at one open seat.

    Returns None when no seat is open, ephemeral spawn fails, or
    save_table fails. Mutates `already_seated` and `fish_ids` to
    include the new pid so subsequent passes in the same refresh
    see it.
    """
    open_seats = _open_seat_indices(table)
    if not open_seats:
        return None

    template = rng.choice(EPHEMERAL_FISH_TEMPLATES)
    spawned = spawn_ephemeral_fish(
        template_pid=template,
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        rng=rng,
        sandbox_id=sandbox_id,
        now=now,
        chip_ledger_repo=chip_ledger_repo,
    )
    if spawned is None:
        return None
    pid, _name = spawned
    seat_idx = rng.choice(open_seats)
    new_seats = list(table.seats)
    new_seats[seat_idx] = ai_slot(pid, fish_buy_in)
    updated = CashTableState(
        table_id=table.table_id,
        stake_label=table.stake_label,
        seats=new_seats,
        created_at=table.created_at,
        last_activity_at=now,
        name=table.name,
        table_type='casino',
        dealer_idx=table.dealer_idx,
        # Carry forward closing state (defensive — refill never fires
        # on closing casinos, but we don't want the rebuild path to
        # silently clobber the column if that invariant ever changes).
        closing_hand_countdown=table.closing_hand_countdown,
    )
    try:
        cash_table_repo.save_table(updated, sandbox_id=sandbox_id, now=now)
    except Exception as exc:
        logger.warning(
            "[CASH][CASINO] refill save_table failed for %s: %s",
            table.table_id, exc,
        )
        return None
    record_casino_seat_seed(
        chip_ledger_repo,
        personality_id=pid,
        amount=fish_buy_in,
        context={
            'site': 'casino_refill',
            'stake_label': stake_label,
            'table_id': table.table_id,
            'template_pid': template,
        },
        sandbox_id=sandbox_id,
    )
    already_seated.add(pid)
    fish_ids.add(pid)
    return CasinoRefill(
        table_id=table.table_id,
        stake_label=stake_label,
        fish_id=pid,
        bank_pool_drawn=fish_buy_in,
    )


def resolve_casino_provisioning(
    *,
    cash_table_repo,
    bankroll_repo,
    personality_repo,
    chip_ledger_repo,
    sandbox_id: str,
    rng: random.Random,
    now: datetime,
) -> CasinoProvisioningBatch:
    """Three-pass provisioning: refill / teardown / spawn.

    Per refresh, for each stake in `CASINO_SPAWN_THRESHOLDS`:

      1. **Refill pass** — for active (not-closing) casinos with fewer
         than `CASINO_FISH_MAX` fish seated, generate one ephemeral
         fish (alliterative variant of a template) and seat it if the
         pool can fund the buy-in. Creates the "trickle in" feel.

      2. **Teardown pass** — for casinos with zero fish AND pool
         can't refund a new fish: enter `closing` state with countdown
         (smooth shutdown). For casinos already closing with countdown=0:
         actually delete the row.

      3. **Spawn pass** — for stakes with no active OR closing casino:
         if pool ≥ threshold AND ≥ MIN_HUNGRY_GRINDERS in the idle
         pool, spawn a new casino. Fish are generated on demand from
         the ephemeral pool — no pre-existing fish supply gated.

    Spawns draw `N × buy_in` from the pool atomically; refills draw
    one buy-in. Best-effort wrapping at each pass.
    """
    batch = CasinoProvisioningBatch()
    if cash_table_repo is None or chip_ledger_repo is None:
        return batch
    if bankroll_repo is None or personality_repo is None:
        return batch

    # Snapshot of current fish (templates + already-spawned ephemerals)
    # for `_count_seated_fish`. Mutated by spawn/refill passes so
    # downstream passes see freshly-generated fish.
    fish_ids = load_fish_ids(bankroll_repo, sandbox_id=sandbox_id)

    # Globally-seated personalities — avoid double-picking the same pid
    # for multiple tables in one resolve.
    already_seated: Set[str] = set()
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        for slot in table.seats:
            if slot.get('kind') == 'ai':
                pid = slot.get('personality_id')
                if pid:
                    already_seated.add(pid)

    by_stake = _existing_casinos_by_stake(cash_table_repo, sandbox_id=sandbox_id)

    # --- Pass 1: refill --------------------------------------------------
    for stake_label, tables_here in by_stake.items():
        _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
        fish_buy_in = min(
            int(min_buy_in * CASINO_FISH_BUY_IN_MULTIPLIER),
            max_buy_in,
        )
        for table in tables_here:
            # Skip closing casinos — they're winding down, not topping up.
            if is_closing(cash_table_repo, sandbox_id, table.table_id):
                continue
            seated_count = _count_seated_fish(table, fish_ids)
            if seated_count >= CASINO_FISH_MAX:
                continue
            current_pool = compute_bank_pool_reserves(
                chip_ledger_repo, sandbox_id=sandbox_id,
            )
            if current_pool < fish_buy_in:
                continue
            refill = _refill_one_fish(
                table,
                stake_label=stake_label,
                fish_buy_in=fish_buy_in,
                chip_ledger_repo=chip_ledger_repo,
                cash_table_repo=cash_table_repo,
                personality_repo=personality_repo,
                bankroll_repo=bankroll_repo,
                sandbox_id=sandbox_id,
                rng=rng,
                now=now,
                fish_ids=fish_ids,
                already_seated=already_seated,
            )
            if refill is not None:
                batch.refills.append(refill)
                logger.info(
                    "[CASH][CASINO] refill %s: +%s (%d chips)",
                    table.table_id, refill.fish_id, refill.bank_pool_drawn,
                )

    # Refresh by_stake after the refill pass — seats changed but the
    # table list didn't. Re-fetch only if a refill happened.
    if batch.refills:
        by_stake = _existing_casinos_by_stake(
            cash_table_repo, sandbox_id=sandbox_id,
        )

    # --- Pass 2: teardown (with smooth shutdown via closing state) -------
    for stake_label, tables_here in list(by_stake.items()):
        _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
        fish_buy_in = min(
            int(min_buy_in * CASINO_FISH_BUY_IN_MULTIPLIER),
            max_buy_in,
        )
        for table in tables_here:
            seated_count = _count_seated_fish(table, fish_ids)
            currently_closing = is_closing(cash_table_repo, sandbox_id, table.table_id)
            countdown = get_closing_countdown(cash_table_repo, sandbox_id, table.table_id)

            # Already closing? Check countdown.
            if currently_closing:
                if countdown is not None and countdown <= 0:
                    # Countdown elapsed — actually delete.
                    # First snapshot ephemeral fish that were seated
                    # here so we can clean them up after the table
                    # row is gone (no other place to find them once
                    # the seats_json blob is deleted).
                    ephemeral_pids = [
                        slot.get('personality_id')
                        for slot in table.seats
                        if slot.get('kind') == 'ai'
                        and slot.get('personality_id')
                        and is_ephemeral_fish_pid(slot['personality_id'])
                    ]
                    try:
                        cash_table_repo.delete_table(
                            table.table_id, sandbox_id=sandbox_id,
                        )
                        clear_closing(cash_table_repo, sandbox_id, table.table_id)
                        # Ephemeral fish cleanup — best-effort. A failure
                        # here leaves an orphan personality row but
                        # doesn't break the teardown.
                        for pid in ephemeral_pids:
                            try:
                                bankroll_repo.delete_ai_bankroll(
                                    pid, sandbox_id=sandbox_id,
                                )
                                personality_repo.delete_personality_by_id(pid)
                                fish_ids.discard(pid)
                            except Exception as exc:
                                logger.warning(
                                    "[CASH][CASINO] ephemeral cleanup "
                                    "failed for %s: %s", pid, exc,
                                )
                        if ephemeral_pids:
                            logger.info(
                                "[CASH][CASINO] cleaned up %d ephemeral fish "
                                "from %s", len(ephemeral_pids), table.table_id,
                            )
                        batch.teardowns.append(CasinoTeardown(
                            table_id=table.table_id,
                            stake_label=stake_label,
                            reason='closing_countdown_elapsed',
                        ))
                        by_stake[stake_label] = [
                            t for t in tables_here
                            if t.table_id != table.table_id
                        ]
                        logger.info(
                            "[CASH][CASINO] teardown %s: closing countdown elapsed",
                            table.table_id,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[CASH][CASINO] teardown failed for %s: %s",
                            table.table_id, exc,
                        )
                continue

            # Not yet closing. Check if we should enter closing state.
            if seated_count > 0:
                continue
            current_pool = compute_bank_pool_reserves(
                chip_ledger_repo, sandbox_id=sandbox_id,
            )
            if current_pool >= fish_buy_in:
                # Pool can support a refill — skip the closing trigger;
                # the next refill pass will repopulate.
                continue
            # No fish + pool can't refill → enter closing state.
            enter_closing(
                cash_table_repo, sandbox_id, table.table_id,
                CASINO_CLOSING_HAND_COUNTDOWN,
            )
            batch.teardowns.append(CasinoTeardown(
                table_id=table.table_id,
                stake_label=stake_label,
                reason=f'closing_announced_{CASINO_CLOSING_HAND_COUNTDOWN}_hands',
            ))
            logger.info(
                "[CASH][CASINO] %s entering closing state (%d hands)",
                table.table_id, CASINO_CLOSING_HAND_COUNTDOWN,
            )

    # --- Pass 3: spawn ----------------------------------------------------
    # Re-fetch in case teardowns ran.
    by_stake_after_teardown = _existing_casinos_by_stake(
        cash_table_repo, sandbox_id=sandbox_id,
    )
    # Hungry-grinder demand signal — count ALL hungry grinders in the
    # sandbox, including those currently at lobby tables. They're the
    # casino's target customer base; the casino spawns precisely to
    # attract them away from low-EV lobby tables. Excluding
    # already-seated grinders here would collapse the signal to zero
    # any time the lobby refresh seats first (which is always — the
    # casino check runs after the lobby loop).
    hungry_grinders = list_hungry_grinders(
        bankroll_repo,
        sandbox_id=sandbox_id,
        now=now,
    )

    threshold_order = [s for s in STAKES_ORDER if s in CASINO_SPAWN_THRESHOLDS]
    for stake_label in threshold_order:
        threshold = CASINO_SPAWN_THRESHOLDS[stake_label]
        # Don't spawn if any casino (active OR closing) exists at this
        # stake — one slot per stake, closing tables hold the slot
        # until their countdown elapses.
        if by_stake_after_teardown.get(stake_label):
            continue
        # Economic + demand gates only. Fish supply is generated on
        # demand from `EPHEMERAL_FISH_TEMPLATES` — no pre-existing
        # fish pool to check.
        current_pool = compute_bank_pool_reserves(
            chip_ledger_repo, sandbox_id=sandbox_id,
        )
        if current_pool < threshold:
            continue
        if len(hungry_grinders) < CASINO_MIN_HUNGRY_GRINDERS:
            continue

        _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
        fish_buy_in = min(
            int(min_buy_in * CASINO_FISH_BUY_IN_MULTIPLIER),
            max_buy_in,
        )
        # Variable spawn size — random [MIN, MAX]; capped by pool depth.
        # Pool funds buy-in × N; N is bounded by what the pool can
        # cover. MIN guards against spawning a casino too small to
        # feel populated.
        target_count = rng.randint(CASINO_FISH_MIN, CASINO_FISH_MAX)
        target_count = min(target_count, current_pool // fish_buy_in)
        if target_count < CASINO_FISH_MIN:
            continue

        seats = [open_slot() for _ in range(TABLE_SEAT_COUNT)]
        fish_positions = sorted(rng.sample(
            range(TABLE_SEAT_COUNT), target_count,
        ))
        seated: List[str] = []
        total_drawn = 0
        for seat_idx in fish_positions:
            template = rng.choice(EPHEMERAL_FISH_TEMPLATES)
            spawned = spawn_ephemeral_fish(
                template_pid=template,
                personality_repo=personality_repo,
                bankroll_repo=bankroll_repo,
                rng=rng,
                sandbox_id=sandbox_id,
                now=now,
                chip_ledger_repo=chip_ledger_repo,
            )
            if spawned is None:
                # Personality / bankroll write failed — leave the
                # seat open and keep going. The next refresh's refill
                # pass will retry.
                continue
            pid, _name = spawned
            seats[seat_idx] = ai_slot(pid, fish_buy_in)
            record_casino_seat_seed(
                chip_ledger_repo,
                personality_id=pid,
                amount=fish_buy_in,
                context={
                    'site': 'casino_spawn',
                    'stake_label': stake_label,
                    'fish_count': target_count,
                    'template_pid': template,
                },
                sandbox_id=sandbox_id,
            )
            seated.append(pid)
            total_drawn += fish_buy_in
            already_seated.add(pid)
            fish_ids.add(pid)

        # If we couldn't spawn enough fish (every spawn attempt
        # failed), skip the table — the next refresh will try again.
        if len(seated) < CASINO_FISH_MIN:
            continue

        table_id = _casino_table_id(stake_label)
        new_state = CashTableState(
            table_id=table_id,
            stake_label=stake_label,
            seats=seats,
            created_at=now,
            last_activity_at=now,
            name=f"Casino — {stake_label}",
            table_type='casino',
        )
        try:
            cash_table_repo.save_table(new_state, sandbox_id=sandbox_id, now=now)
        except Exception as exc:
            logger.warning(
                "[CASH][CASINO] save_table failed for %s: %s",
                table_id, exc,
            )
            continue
        # Make sure no leftover closing-state row haunts this table_id.
        # (Defensive — the spawn upsert sets the column NULL anyway via
        # the new CashTableState created above, but clear_closing makes
        # the intent explicit.)
        clear_closing(cash_table_repo, sandbox_id, table_id)
        batch.spawns.append(CasinoSpawn(
            table_id=table_id,
            stake_label=stake_label,
            fish_seated=seated,
            bank_pool_drawn=total_drawn,
        ))
        by_stake_after_teardown.setdefault(stake_label, []).append(new_state)
        logger.info(
            "[CASH][CASINO] spawn %s (%s): %d fish, %d chips drawn",
            table_id, stake_label, len(seated), total_drawn,
        )

    return batch
