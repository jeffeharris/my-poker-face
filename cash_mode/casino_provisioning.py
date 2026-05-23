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
    compute_bank_pool_reserves,
    load_fish_ids,
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

# How many fish to seat per casino. Less than TABLE_SEAT_COUNT so
# grinders have seats to fill. 3-4 fish + 2-3 grinder seats is the
# canonical casino-table dynamic.
CASINO_FISH_PER_TABLE = 4

# Fish buy-in as a multiple of table min_buy_in. 1.0 = standard short
# buy-in (40 BB). Higher gives fish more chips to lose before busting;
# lower means more frequent casino refills.
CASINO_FISH_BUY_IN_MULTIPLIER = 1.0

# Casino table_id format. Distinct from lobby (`cash-table-...`) so
# the audit can pick them apart.
CASINO_TABLE_ID_PREFIX = "cash-casino"


# --- Dataclasses ------------------------------------------------------


@dataclass(frozen=True)
class CasinoSpawn:
    """A casino spawn event."""
    table_id: str
    stake_label: str
    fish_seated: List[str]
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


def _pick_fish_for_spawn(
    fish_ids: Set[str],
    *,
    already_seated: Set[str],
    count: int,
    rng: random.Random,
) -> List[str]:
    """Pick up to `count` fish not currently seated elsewhere.

    Returns shuffled order. Falls back to fewer fish if not enough
    are available (caller decides whether to spawn anyway or skip).
    """
    available = [pid for pid in fish_ids if pid not in already_seated]
    rng.shuffle(available)
    return available[:count]


# --- Resolver ---------------------------------------------------------


def resolve_casino_provisioning(
    *,
    cash_table_repo,
    bankroll_repo,
    chip_ledger_repo,
    sandbox_id: str,
    rng: random.Random,
    now: datetime,
) -> CasinoProvisioningBatch:
    """Spawn / teardown casino tables based on bank pool depth.

    For each stake in `CASINO_SPAWN_THRESHOLDS`, in ascending order:
      - If no active casino at this stake AND pool ≥ threshold AND
        enough fish are available → spawn one.
      - For each existing casino at this stake: if no fish are seated
        AND the pool can't fund a refill → tear down.

    Spawns draw from the pool atomically: N × buy_in chips per spawn,
    one `casino_seat_seed` ledger row per fish. Bankrolls are NOT
    touched (chips land at the seat directly).

    Returns the batch so callers can emit ticker events / capture
    metrics. Best-effort failures inside per-stake loops are logged
    and don't tank the whole resolve.
    """
    batch = CasinoProvisioningBatch()
    if cash_table_repo is None or chip_ledger_repo is None:
        return batch
    if bankroll_repo is None:
        return batch

    fish_ids = load_fish_ids(bankroll_repo, sandbox_id=sandbox_id)
    if not fish_ids:
        # No fish exist in this sandbox — nothing to provision.
        return batch

    # Existing casinos snapshot — used for both teardown decisions and
    # to skip spawn when a casino at this stake already exists.
    by_stake = _existing_casinos_by_stake(cash_table_repo, sandbox_id=sandbox_id)

    # Globally-seated fish set so we don't try to put the same fish at
    # multiple casinos in one tick.
    already_seated: Set[str] = set()
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        for slot in table.seats:
            if slot.get('kind') == 'ai':
                pid = slot.get('personality_id')
                if pid:
                    already_seated.add(pid)

    # Process stakes in ascending ladder order — $2 first, then $10.
    # The $2 spawn might consume some pool reserves, leaving the $10
    # threshold unmet on this tick (which is fine — $10 spawns when
    # vice deposits later push the pool above its higher threshold).
    threshold_order = [s for s in STAKES_ORDER if s in CASINO_SPAWN_THRESHOLDS]

    for stake_label in threshold_order:
        threshold = CASINO_SPAWN_THRESHOLDS[stake_label]
        active = by_stake.get(stake_label, [])

        # Teardown pass first — frees fish for potential re-spawn this
        # tick at the same or a different stake.
        for table in active:
            if _casino_has_seated_fish(table, fish_ids):
                continue
            # No fish seated. Tear down only if pool also can't refill —
            # otherwise the spawn pass below will refill this casino.
            current_pool = compute_bank_pool_reserves(
                chip_ledger_repo, sandbox_id=sandbox_id,
            )
            _, min_buy_in, _ = table_buy_in_window(stake_label)
            refill_cost = CASINO_FISH_PER_TABLE * int(
                min_buy_in * CASINO_FISH_BUY_IN_MULTIPLIER
            )
            if current_pool >= refill_cost:
                # Pool can support a refill — skip teardown; the spawn
                # pass below will reuse this table.
                continue
            try:
                cash_table_repo.delete_table(
                    table.table_id, sandbox_id=sandbox_id,
                )
                batch.teardowns.append(CasinoTeardown(
                    table_id=table.table_id,
                    stake_label=stake_label,
                    reason='fish_busted_pool_empty',
                ))
                logger.info(
                    "[CASH][CASINO] teardown %s: fish busted, pool empty",
                    table.table_id,
                )
                # Remove from local cache for the spawn check below.
                by_stake[stake_label] = [
                    t for t in active if t.table_id != table.table_id
                ]
                active = by_stake[stake_label]
            except Exception as exc:
                logger.warning(
                    "[CASH][CASINO] teardown failed for %s: %s",
                    table.table_id, exc,
                )

        # Spawn pass — gate on (a) no active casino at this stake,
        # (b) pool ≥ threshold, (c) enough free fish.
        if active:
            # Either an active casino exists OR was preserved by the
            # teardown guard. Skip spawning a second casino at this stake.
            continue

        current_pool = compute_bank_pool_reserves(
            chip_ledger_repo, sandbox_id=sandbox_id,
        )
        if current_pool < threshold:
            continue

        _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
        fish_buy_in = min(
            int(min_buy_in * CASINO_FISH_BUY_IN_MULTIPLIER),
            max_buy_in,
        )
        spawn_cost = CASINO_FISH_PER_TABLE * fish_buy_in
        if current_pool < spawn_cost:
            # Threshold met but per-fish cost arithmetic doesn't fit.
            # Skip — next tick may have more chips in the pool.
            continue

        picked_fish = _pick_fish_for_spawn(
            fish_ids,
            already_seated=already_seated,
            count=CASINO_FISH_PER_TABLE,
            rng=rng,
        )
        if len(picked_fish) < 1:
            # No free fish — every fish already at another table.
            continue

        # Allocate seat positions — fish get the first N positions,
        # remaining are open for grinders to live-fill into.
        seats = [open_slot() for _ in range(TABLE_SEAT_COUNT)]
        fish_positions = sorted(rng.sample(
            range(TABLE_SEAT_COUNT), len(picked_fish),
        ))

        # Write the ledger rows + place fish in seats. Each fish gets
        # exactly `fish_buy_in` chips at their seat; their bankroll
        # is NOT touched.
        seated: List[str] = []
        total_drawn = 0
        for pid, seat_idx in zip(picked_fish, fish_positions):
            seats[seat_idx] = ai_slot(pid, fish_buy_in)
            record_casino_seat_seed(
                chip_ledger_repo,
                personality_id=pid,
                amount=fish_buy_in,
                context={
                    'site': 'casino_spawn',
                    'stake_label': stake_label,
                    'fish_count': len(picked_fish),
                },
                sandbox_id=sandbox_id,
            )
            seated.append(pid)
            total_drawn += fish_buy_in
            already_seated.add(pid)

        table_id = _casino_table_id(stake_label)
        # If a previous teardown freed up this ID earlier in the same
        # tick, the spawn re-uses it cleanly (save_table is an upsert).
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
        batch.spawns.append(CasinoSpawn(
            table_id=table_id,
            stake_label=stake_label,
            fish_seated=seated,
            bank_pool_drawn=total_drawn,
        ))
        # Update local cache so the next stake iteration sees this
        # spawn (relevant when the same stake somehow appears twice).
        by_stake.setdefault(stake_label, []).append(new_state)
        logger.info(
            "[CASH][CASINO] spawn %s (%s): %d fish, %d chips drawn from pool",
            table_id, stake_label, len(seated), total_drawn,
        )

    return batch
