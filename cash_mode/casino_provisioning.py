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

This module also owns `resolve_whale_provisioning` — the `$200`+ relief
gate that replaces the retired `$200` casino. A whale is the same kind of
pool-funded fish entity, just seated at a real cardroom (lobby) table with
a much deeper prefund, so it reuses the conservation helpers here. See the
"Whale provisioning" section below and `CASH_MODE_WHALE_AT_CARDROOM.md`.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple  # noqa: F401 — Tuple used in return type hint

from cash_mode import presence_shadow
from cash_mode.presence import PresenceEvent, ai_entity_id

from cash_mode.bankroll import (
    AIBankrollState,
    debit_bankroll_for_seat,
)
from cash_mode.closed_economy import (
    compute_bank_pool_reserves,
    list_hungry_grinders,
)
from cash_mode.stakes_ladder import (
    STAKES_ORDER,
    table_buy_in_window,
)
from cash_mode.tables import (
    TABLE_SEAT_COUNT,
    CashTableState,
    ai_slot_fish,
    open_slot,
)
from core.economy.ledger import (
    record_casino_seat_return,
    record_casino_seat_seed,
)

logger = logging.getLogger(__name__)


# --- Tuning constants -------------------------------------------------

# Pool depth required to spawn a casino at each stake. The $2 casino
# needs enough to cover N fish × min_buy_in (40 BB × N fish). For
# default 4 fish at $2: 4 × 80 = 320 chips minimum. The 5K threshold
# leaves a generous buffer for refills + a $10 casino's chance to grow.
CASINO_SPAWN_THRESHOLDS: Dict[str, int] = {
    '$2': 5_000,
    '$10': 50_000,
    # High-stakes casino. Fish bleed scales with the big blind, so this
    # is the magnitude lever for "fish feed the population" — a $50 table
    # moves ~5x the $10 rate. Predators are rich AIs that seat by
    # affordability and stay via predator-retention (see
    # `_coerce_predator_retention`). The threshold covers ~2 fish prefund
    # (2.5-3.6x the tier's max buy-in) plus a refill buffer: $50 max
    # buy-in 5k → ~36k for 2 fish.
    #
    # Casinos cap at $50: the $200+ band is whale-only (a rare pool-funded
    # high roller at a real cardroom/lobby table, not a synthetic casino).
    # See `resolve_whale_provisioning` + `WHALE_POOL_THRESHOLDS` below and
    # `docs/plans/CASH_MODE_WHALE_AT_CARDROOM.md`.
    '$50': 100_000,
}

# Dam wind-down floor for the high-stakes gate. The $50 casino is a
# relief valve: opening it draws its fish prefund from the pool and the
# fish bleed those pool-origin chips into predator bankrolls, so the gate
# *drains* the reservoir. The floor closes it as the pool falls, so it
# doesn't drain a normalizing pool to empty — the pool settles into a
# band instead. The floor sits well below the open threshold (gap > the
# ~max fish-prefund draw) so opening the gate can't instantly trip its
# own close. Base tiers ($2/$10) have no floor — they're the always-on
# base and only close via the natural out-of-fish teardown.
CASINO_CLOSE_THRESHOLDS: Dict[str, int] = {
    '$50': 45_000,
}

# Fish-per-casino range. Spawns pick a random count in [MIN, MAX];
# the refill pass keeps casinos topped up to MAX as long as the pool
# can fund it. Kept LOW (well under TABLE_SEAT_COUNT=6) so each fish is
# surrounded by grinders, not other fish: a fish only transfers its
# stake to the population if it loses to a *predator*, and fish-vs-fish
# pots just recycle chips among fish (back to the pool when they leave).
# 1-2 fish + 4-5 grinders means most pots a fish plays are vs a grinder,
# so the fish actually bleeds out to the population. Sim-validated: a
# 4-fish table left fish trading amongst themselves (390/400 movement
# decisions were "stay", ~0 net to grinders). Tunable: raise for more
# donation throughput per table at the cost of more fish-vs-fish recycle.
CASINO_FISH_MIN = 1
CASINO_FISH_MAX = 2

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


# --- Whale provisioning: the $200+ relief gate -----------------------
#
# A whale is the high-stakes relief valve that replaces the (retired)
# $200 casino: a single, rare pool-funded high roller seated at a real
# cardroom (lobby) table — not a synthetic casino. Mechanically a whale
# is JUST a fish: an `archetype='fish'` persona with a pool-funded
# bankroll and ordinary fish movement (reload-until-broke, storm off on
# tilt). The only difference is the depth of the stack — the dormant
# `_fish_prefund(whale=True)` grant is 10-18x the table max buy-in (vs a
# casino fish's 2.5-3.6x). So a $200 whale is a single ~200k-360k draw
# from the pool, the right "open the big gate when the reservoir is
# bloated" release.
#
# Because regular fish are casino-only, "a fish seated at a LOBBY table"
# IS the whale — no separate archetype or seat flag is needed, and all
# the fish machinery (movement, predator-retention, the pool↔seat
# conservation helpers, the drain-on-exit sweep) is reused verbatim.
#
# Spec: `docs/plans/CASH_MODE_WHALE_AT_CARDROOM.md`.

# Per-stake high-watermark. Spawn a whale at this cardroom stake only
# when the pool can cover the deep prefund (up to 18x max buy-in) AND
# leave a healthy floor. Sits well above the $50 casino threshold (the
# whale is the biggest release). Restrict this dict to a single stake to
# A/B a $50-cardroom whale against a $200-cardroom whale:
#   $50  → max buy-in 5k,  whale prefund 50k-90k    → 150k clears it + ~60k floor
#   $200 → max buy-in 20k, whale prefund 200k-360k  → 500k clears it + ~140k floor
WHALE_POOL_THRESHOLDS: Dict[str, int] = {
    '$50': 150_000,
    '$200': 500_000,
}

# Per-stake low-watermark. A live whale winds down (leaves; its seat
# residual + remaining bankroll return to the pool) once reserves fall
# below this — the relief valve recalls the whale's unused stake when the
# pool needs the chips elsewhere, so a one-time deep draw isn't stranded
# forever at an un-farmed table. Each floor sits well below its spawn
# threshold (gap > the max prefund draw) so spawning can't instantly trip
# the whale's own wind-down.
WHALE_POOL_FLOORS: Dict[str, int] = {
    '$50': 30_000,
    '$200': 80_000,
}


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
        table_id,
        sandbox_id=sandbox_id,
        countdown=countdown,
    )


def is_closing(cash_table_repo, sandbox_id: str, table_id: str) -> bool:
    """True iff this casino is currently in closing state."""
    if cash_table_repo is None:
        return False
    return (
        cash_table_repo.get_closing_countdown(
            table_id,
            sandbox_id=sandbox_id,
        )
        is not None
    )


def get_closing_countdown(
    cash_table_repo,
    sandbox_id: str,
    table_id: str,
) -> Optional[int]:
    """Hands remaining for a closing casino, or None if not closing."""
    if cash_table_repo is None:
        return None
    return cash_table_repo.get_closing_countdown(
        table_id,
        sandbox_id=sandbox_id,
    )


def decrement_closing_hands(
    cash_table_repo,
    sandbox_id: str,
    table_id: str,
    count: int = 1,
) -> Optional[int]:
    """Tick down the closing countdown. Returns the new count, or None
    if the casino isn't in closing state.

    Called once per casino-provisioning resolution for a closing table
    (see `resolve_casino_provisioning` Pass 2). Closing is only entered
    once a casino is empty of fish, and an empty table plays no hands —
    so a per-hand hook would never fire. On reaching 0, a later
    resolution actually deletes the row.
    """
    if cash_table_repo is None:
        return None
    current = cash_table_repo.get_closing_countdown(
        table_id,
        sandbox_id=sandbox_id,
    )
    if current is None:
        return None
    new_count = max(0, current - count)
    cash_table_repo.set_closing_countdown(
        table_id,
        sandbox_id=sandbox_id,
        countdown=new_count,
    )
    return new_count


def clear_closing(
    cash_table_repo,
    sandbox_id: str,
    table_id: str,
) -> None:
    """Reset the countdown to NULL ('active' state). Used when an
    actual delete fires (defensive — the delete removes the row) or
    if external logic ever wants to un-close a table."""
    if cash_table_repo is None:
        return
    cash_table_repo.set_closing_countdown(
        table_id,
        sandbox_id=sandbox_id,
        countdown=None,
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


@dataclass(frozen=True)
class WhaleSpawn:
    """A whale sat down at a cardroom (lobby) table."""

    table_id: str
    stake_label: str
    whale_id: str
    name: str
    buy_in: int
    bank_pool_drawn: int


@dataclass(frozen=True)
class WhaleTeardown:
    """A whale was recalled from a cardroom table (dam wind-down)."""

    table_id: str
    stake_label: str
    whale_id: str
    name: str
    reason: str


@dataclass
class WhaleProvisioningBatch:
    """Result of one whale-provisioning tick — at most one of each, since
    there is only ever one whale live at a time. Mutable: the resolver
    assigns `spawn`/`teardown` by attribute, which a `frozen=True`
    dataclass would forbid. (The casino batch can be frozen because it
    only ever `.append()`s to its list fields, never reassigns them.)"""

    spawn: Optional[WhaleSpawn] = None
    teardown: Optional[WhaleTeardown] = None


# --- Helpers ----------------------------------------------------------


def _casino_table_id(stake_label: str, suffix: str = "001") -> str:
    """Return the canonical casino table_id for a stake."""
    slug = stake_label[1:] if stake_label.startswith('$') else stake_label
    return f"{CASINO_TABLE_ID_PREFIX}-{slug}-{suffix}"


def _reclaim_zombie_casino_seats(
    cash_table_repo,
    chip_ledger_repo,
    *,
    sandbox_id: str,
    valid_pids: Set[str],
    fish_ids: Set[str],
    now: datetime,
) -> int:
    """Open stale casino AI seats that nothing else can clear.

    Two seat classes qualify, both of which permanently consume a seat the
    human or a live-filling grinder could take (refill only adds fish;
    teardown only sweeps `archetype='fish'` seats):

      1. **Zombie** — `personality_id` no longer resolves: old-model
         `tourist-<uuid>` seats from before the fish-as-personas migration,
         or any persona deleted while seated.
      2. **Un-stamped fish** — a seat holding a fish *persona*
         (`personality_id in fish_ids`) that lacks the `archetype='fish'`
         seat stamp. These are pre-migration `<fish>__eph_<hash>` seats
         placed via `ai_slot` (no stamp). They're invisible to the
         stamp-based fish count, so provisioning treats the casino as full
         of fish and never refills/tears it down, while the player sees no
         fish — the wedge this reclaim breaks. Re-opened seats are then
         refilled with properly-stamped fish; the freed persona's residual
         bankroll drains to the pool via the drain-on-exit sweep.

    This is the same ghost-seat failure class the cash code has hit before,
    so guard it structurally rather than one-off.

    Casino seats are pool-funded, so a stale seat's residual chips return
    to the bank pool (`casino_seat_return`) before the seat is opened — the
    pool is the conservation-safe sink for orphaned chips. A seat is only
    opened once its chips are safely returned (or it had none); a failed
    return leaves the seat untouched to retry next resolve, so chips
    never vanish from the universe.

    Returns the number of seats reclaimed.
    """
    reclaimed = 0
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        if table.table_type != 'casino':
            continue
        new_seats = list(table.seats)
        changed = False
        reclaimed_pids: List[str] = []
        for idx, slot in enumerate(table.seats):
            if slot.get('kind') != 'ai':
                continue
            pid = slot.get('personality_id')
            unresolved = pid not in valid_pids
            # Old-model fish seat: holds a fish persona but was placed
            # without the `archetype='fish'` stamp. A stamped fish seat
            # (the healthy case) is skipped here.
            unstamped_fish = pid in fish_ids and slot.get('archetype') != 'fish'
            if not unresolved and not unstamped_fish:
                continue
            reason = 'unresolved_personality' if unresolved else 'unstamped_fish_seat'
            # Stale seat — return residual chips to the pool first.
            chips = int(slot.get('chips') or 0)
            if chips > 0 and pid:
                try:
                    row_id = record_casino_seat_return(
                        chip_ledger_repo,
                        personality_id=pid,
                        amount=chips,
                        context={
                            'site': 'casino_zombie_reclaim',
                            'table_id': table.table_id,
                            'stake_label': table.stake_label,
                            'reason': reason,
                        },
                        sandbox_id=sandbox_id,
                    )
                except Exception as exc:
                    row_id = None
                    logger.warning(
                        "[CASH][CASINO] zombie seat-return raised for %s/%s " "(%d chips): %s",
                        table.table_id,
                        pid,
                        chips,
                        exc,
                    )
                if row_id is None:
                    # Return failed — leave the seat to retry next resolve
                    # rather than vanish the chips.
                    logger.warning(
                        "[CASH][CASINO] zombie reclaim deferred for %s/%s "
                        "(%d chips, seat-return failed)",
                        table.table_id,
                        pid,
                        chips,
                    )
                    continue
            new_seats[idx] = open_slot()
            changed = True
            reclaimed += 1
            if pid:
                reclaimed_pids.append(pid)
            logger.info(
                "[CASH][CASINO] reclaimed stale seat %s/%s (%s, %d chips -> pool)",
                table.table_id,
                pid,
                reason,
                chips,
            )
        if changed:
            updated = CashTableState(
                table_id=table.table_id,
                stake_label=table.stake_label,
                seats=new_seats,
                created_at=table.created_at,
                last_activity_at=table.last_activity_at,
                name=table.name,
                table_type='casino',
                dealer_idx=table.dealer_idx,
                closing_hand_countdown=table.closing_hand_countdown,
            )
            try:
                cash_table_repo.save_table(updated, sandbox_id=sandbox_id, now=now)
            except Exception as exc:
                logger.warning(
                    "[CASH][CASINO] zombie reclaim save_table failed for %s: %s",
                    table.table_id,
                    exc,
                )
            else:
                # SHADOW (Presence cutover Phase 1): each reclaimed casino AI
                # seat returns its pool-funded identity to POOL. Emitted only
                # after the authoritative seat write above succeeded.
                for rpid in reclaimed_pids:
                    presence_shadow.shadow_transition(
                        entity_id=ai_entity_id(rpid),
                        sandbox_id=sandbox_id,
                        event=PresenceEvent.RETURN_TO_POOL,
                    )
    return reclaimed


def _existing_casinos_by_stake(
    cash_table_repo,
    *,
    sandbox_id: str,
) -> Dict[str, List[CashTableState]]:
    """Group active casino tables by stake_label."""
    by_stake: Dict[str, List[CashTableState]] = {}
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        if table.table_type != 'casino':
            continue
        by_stake.setdefault(table.stake_label, []).append(table)
    return by_stake


def _casino_has_seated_fish(table: CashTableState) -> bool:
    """True iff any seat holds a pool-funded fish.

    Fish are identified by the `archetype='fish'` stamp that
    `ai_slot_fish` writes at spawn — not by a global fish-id set or an
    inline personality blob. Seats without the stamp (grinders who
    live-filled in, the human) don't count: their chips come from their
    own bankroll, not the pool, so they must never be swept back to it.
    """
    for slot in table.seats:
        if slot.get('kind') == 'ai' and slot.get('archetype') == 'fish':
            return True
    return False


def _return_seat_residuals_to_pool(
    table: CashTableState,
    *,
    chip_ledger_repo,
    sandbox_id: str,
    reason_detail: str,
) -> Tuple[int, int]:
    """Write `casino_seat_return` ledger rows for any fish seats with
    chips remaining.

    Returns `(total_returned, total_stranded)`. `total_stranded` is chips
    that *should* have been returned but the ledger write failed —
    caller must NOT proceed with `delete_table` when this is non-zero,
    otherwise conservation breaks (chips vanish from the universe).

    Fish chips are pool-funded (`casino_seat_seed`), not drawn from a
    bankroll, so whatever remains on a fish seat at teardown must return
    directly to the bank pool to close the loop. Only `archetype='fish'`
    seats are swept — grinder/human seats hold chips funded from their
    own bankrolls and must be left untouched (sweeping them would mint
    chips into the pool). Per-seat try/except ensures one failing write
    doesn't strand the others — but the caller is on the hook for
    handling any stranded amount.
    """
    total_returned = 0
    total_stranded = 0
    for slot in table.seats:
        if slot.get('kind') != 'ai':
            continue
        if slot.get('archetype') != 'fish':
            continue
        chips = int(slot.get('chips', 0))
        if chips <= 0:
            continue
        pid = slot.get('personality_id')
        if not pid:
            continue
        try:
            row_id = record_casino_seat_return(
                chip_ledger_repo,
                personality_id=pid,
                amount=chips,
                context={
                    'site': 'casino_teardown',
                    'table_id': table.table_id,
                    'stake_label': table.stake_label,
                    'reason': reason_detail,
                },
                sandbox_id=sandbox_id,
            )
            if row_id is None:
                # Helper rejected the write (e.g., ledger validation
                # failed). Same impact as an exception — chip move never
                # committed, so don't claim it succeeded.
                total_stranded += chips
                logger.warning(
                    "[CASH][CASINO] casino_seat_return rejected for %s/%s " "(%d chips stranded)",
                    table.table_id,
                    pid,
                    chips,
                )
            else:
                total_returned += chips
        except Exception as exc:
            total_stranded += chips
            logger.warning(
                "[CASH][CASINO] casino_seat_return write failed for "
                "%s/%s (%d chips stranded): %s",
                table.table_id,
                pid,
                chips,
                exc,
            )
    return total_returned, total_stranded


# Fish bankroll pre-fund as a multiple of the table max buy-in. ~3x
# (jittered) gives a fish a real stake for the night — enough to re-buy
# a couple times via the normal short-stack rebuy path, and occasionally
# go home broke — without one fish soaking up the whole pool. A whale
# shows up rarely with a much deeper stack: the relief valve for a pool
# accruing faster than grinders can farm it down.
FISH_PREFUND_MIN_MULT = 2.5
FISH_PREFUND_MAX_MULT = 3.6
WHALE_PREFUND_MIN_MULT = 10.0
WHALE_PREFUND_MAX_MULT = 18.0


def _fish_prefund(
    table_max_buy_in: int,
    rng: random.Random,
    *,
    whale: bool = False,
) -> int:
    """Pool-funded bankroll grant for one fish, a jittered multiple of
    the table's max buy-in. `whale=True` produces a much deeper stack.
    """
    lo, hi = (
        (WHALE_PREFUND_MIN_MULT, WHALE_PREFUND_MAX_MULT)
        if whale
        else (FISH_PREFUND_MIN_MULT, FISH_PREFUND_MAX_MULT)
    )
    return int(table_max_buy_in * rng.uniform(lo, hi))


def _load_ai_chips(bankroll_repo, personality_id: str, sandbox_id: str) -> int:
    """Current stored bankroll chips for `personality_id`, or 0 if no row.

    Tolerates repos whose `load_ai_bankroll` predates the `sandbox_id`
    kwarg (mirrors `debit_bankroll_for_seat`).
    """
    try:
        state = bankroll_repo.load_ai_bankroll(personality_id, sandbox_id=sandbox_id)
    except TypeError as e:
        if "sandbox_id" not in str(e):
            raise
        state = bankroll_repo.load_ai_bankroll(personality_id)
    return int(state.chips) if state is not None else 0


def _prefund_fish_from_pool(
    bankroll_repo,
    chip_ledger_repo,
    *,
    personality_id: str,
    target_chips: int,
    sandbox_id: str,
    now: datetime,
    context: Dict,
) -> int:
    """Top a fish's bankroll up to `target_chips`, drawing the shortfall
    from the bank pool. Returns the amount drawn.

    Conservation: the draw is a `casino_seat_seed` (bank-pool draw) and
    the bankroll is written WITHOUT a `chip_ledger_repo` so no `ai_seed`
    mint fires — chips MOVE from pool to bankroll, not created. Only the
    shortfall `target - existing` is drawn; the invariant is that a fish
    is drained to 0 on casino exit, so `existing` is normally 0, but
    drawing the delta keeps conservation correct if it isn't.
    """
    existing = _load_ai_chips(bankroll_repo, personality_id, sandbox_id)
    draw = target_chips - existing
    if draw <= 0:
        return 0
    # Never draw more than the pool holds — caps the prefund so the pool
    # can't go negative when it can't fund the full (jittered ~3x) target.
    # Callers gate on pool >= one buy-in, so the capped bankroll still
    # covers the buy-in debit.
    pool = compute_bank_pool_reserves(chip_ledger_repo, sandbox_id=sandbox_id)
    draw = min(draw, pool)
    if draw <= 0:
        return 0
    record_casino_seat_seed(
        chip_ledger_repo,
        personality_id=personality_id,
        amount=draw,
        context=context,
        sandbox_id=sandbox_id,
    )
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(
            personality_id=personality_id,
            chips=existing + draw,
            last_regen_tick=now,
        ),
        sandbox_id=sandbox_id,
    )
    return draw


def _drain_fish_bankroll_to_pool(
    bankroll_repo,
    chip_ledger_repo,
    *,
    personality_id: str,
    sandbox_id: str,
    now: datetime,
    reason_detail: str,
) -> Tuple[int, int]:
    """Return a fish's entire bankroll to the bank pool and zero it.

    Returns `(returned, stranded)`. `stranded` is non-zero when the
    ledger write failed — the caller must then NOT treat the fish as
    fully exited (its pool-funded chips would otherwise vanish). The
    bankroll half of the seat-residual return: a fish's bankroll is
    pool-funded, so on any casino exit (go-home, bust, teardown) the
    remainder goes back to the pool to close the loop. The row is then
    zeroed (written WITHOUT a ledger so no spurious seed/regen fires),
    preserving the invariant that an un-seated fish holds 0 chips.
    """
    chips = _load_ai_chips(bankroll_repo, personality_id, sandbox_id)
    if chips <= 0:
        return 0, 0
    try:
        row_id = record_casino_seat_return(
            chip_ledger_repo,
            personality_id=personality_id,
            amount=chips,
            context={'site': 'casino_fish_exit', 'reason': reason_detail},
            sandbox_id=sandbox_id,
        )
        if row_id is None:
            logger.warning(
                "[CASH][CASINO] fish bankroll drain rejected for %s " "(%d chips stranded)",
                personality_id,
                chips,
            )
            return 0, chips
    except Exception as exc:
        logger.warning(
            "[CASH][CASINO] fish bankroll drain failed for %s " "(%d chips stranded): %s",
            personality_id,
            chips,
            exc,
        )
        return 0, chips
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(personality_id=personality_id, chips=0, last_regen_tick=now),
        sandbox_id=sandbox_id,
    )
    # SHADOW (Presence cutover Phase 1): a fish's bankroll draining to the pool
    # marks its exit. Casino fish are POOL-funded, so the POOL-origin event is
    # RETURN_TO_POOL (not GO_OFFLINE). Emitted only after the authoritative
    # bankroll write succeeded. This site is reached from several exits
    # (teardown / reap / refill-unwind / spawn-abort); the SEATED→leaving seat
    # write is shadowed by the caller (reclaim/shed/teardown) — this records the
    # off-grid (POOL) half so a fish that left via movement still lands in POOL.
    presence_shadow.shadow_transition(
        entity_id=ai_entity_id(personality_id),
        sandbox_id=sandbox_id,
        event=PresenceEvent.RETURN_TO_POOL,
    )
    return chips, 0


# --- Resolver ---------------------------------------------------------


def _count_seated_fish(table: CashTableState) -> int:
    """Return the number of pool-funded fish currently seated at this casino.

    Counts by the ``archetype='fish'`` SEAT stamp — the single source of
    truth for "this seat is a fish" (see ``ai_slot_fish``), the same signal
    the teardown chip-return and the lobby UI read. Counting by
    ``personality_id in fish_ids`` instead would also count old-model
    un-stamped seats that merely hold a fish *persona* (e.g. pre-migration
    ``<fish>__eph_<hash>`` seats placed via ``ai_slot``), inflating the
    count and wedging the casino: provisioning believes it's full of fish
    and skips both refill and teardown, while the player sees no fish at
    all. ``_reclaim_zombie_casino_seats`` clears those un-stamped seats.
    """
    return sum(
        1 for slot in table.seats if slot.get('kind') == 'ai' and slot.get('archetype') == 'fish'
    )


def _open_seat_indices(table: CashTableState) -> List[int]:
    """Return seat indices currently open (live-fillable)."""
    return [i for i, slot in enumerate(table.seats) if slot.get('kind') == 'open']


def _refill_one_fish(
    table: CashTableState,
    *,
    stake_label: str,
    fish_buy_in: int,
    table_max_buy_in: int,
    chip_ledger_repo,
    cash_table_repo,
    bankroll_repo,
    sandbox_id: str,
    rng: random.Random,
    now: datetime,
    already_seated: Set[str],
    fish_ids: Set[str],
) -> Optional[CasinoRefill]:
    """Seat one unseated fish persona at an open casino seat.

    Funds it the regular way: pool → fish bankroll (prefund) → seat
    buy-in. Returns None when no seat is open, no unseated fish remains,
    the pool can't fund the prefund, or a write fails. Mutates
    `already_seated` so later passes in the same refresh don't re-pick
    the same fish.
    """
    open_seats = _open_seat_indices(table)
    if not open_seats:
        return None
    available = sorted(fish_ids - already_seated)
    if not available:
        return None
    pid = rng.choice(available)

    # Pool → fish bankroll. Drawing the prefund (not just the buy-in) is
    # what lets the fish re-buy from a real stake via the normal
    # short-stack rebuy path before going home broke.
    drawn = _prefund_fish_from_pool(
        bankroll_repo,
        chip_ledger_repo,
        personality_id=pid,
        target_chips=_fish_prefund(table_max_buy_in, rng),
        sandbox_id=sandbox_id,
        now=now,
        context={
            'site': 'casino_refill',
            'stake_label': stake_label,
            'table_id': table.table_id,
        },
    )
    if drawn <= 0:
        return None

    # Buy in from the now-funded bankroll. Pass chip_ledger_repo + now
    # so any pending regen commits via `ai_regen` (no clamp leak). For
    # fish this is a no-op — `bankroll_rate=0` means projected == stored —
    # but keeping the call shape uniform with other call sites.
    if (
        debit_bankroll_for_seat(
            bankroll_repo,
            pid,
            fish_buy_in,
            sandbox_id=sandbox_id,
            chip_ledger_repo=chip_ledger_repo,
            now=now,
        )
        is None
    ):
        # Shouldn't happen post-prefund; unwind the prefund to the pool.
        _drain_fish_bankroll_to_pool(
            bankroll_repo,
            chip_ledger_repo,
            personality_id=pid,
            sandbox_id=sandbox_id,
            now=now,
            reason_detail='refill_buyin_failed',
        )
        return None

    seat_idx = rng.choice(open_seats)
    new_seats = list(table.seats)
    new_seats[seat_idx] = ai_slot_fish(pid, fish_buy_in)
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
            table.table_id,
            exc,
        )
        # Unwind: prefund drew chips from the pool into the fish bankroll,
        # and debit_bankroll_for_seat moved part of that to the in-memory
        # seat — but the seat write never persisted. Without this drain,
        # the fish's bankroll holds the (prefund − buy_in) remainder and
        # the buy_in chips are stranded with no chip-bearing surface
        # backing them (positive audit drift = +fish_buy_in). Returning
        # the full bankroll to the pool restores conservation.
        _drain_fish_bankroll_to_pool(
            bankroll_repo,
            chip_ledger_repo,
            personality_id=pid,
            sandbox_id=sandbox_id,
            now=now,
            reason_detail='refill_save_failed',
        )
        return None
    # SHADOW (Presence cutover Phase 1): a refilled casino fish is a POOL-funded
    # AI seeded into the sandbox (SEED → POOL) then seated (SIT → SEATED with
    # table_id+seat_index). Reached only after the authoritative seat write
    # above succeeded. The shadow helper is best-effort; SEED is replayed each
    # refill to (idempotently) anchor the POOL origin before the SIT even if a
    # prior cycle was missed.
    presence_shadow.shadow_transition(
        entity_id=ai_entity_id(pid),
        sandbox_id=sandbox_id,
        event=PresenceEvent.SEED,
    )
    presence_shadow.shadow_transition(
        entity_id=ai_entity_id(pid),
        sandbox_id=sandbox_id,
        event=PresenceEvent.SIT,
        table_id=table.table_id,
        seat_index=seat_idx,
    )
    already_seated.add(pid)
    return CasinoRefill(
        table_id=table.table_id,
        stake_label=stake_label,
        fish_id=pid,
        bank_pool_drawn=drawn,
    )


def _shed_excess_fish(
    cash_table_repo,
    chip_ledger_repo,
    *,
    sandbox_id: str,
    now: datetime,
) -> int:
    """Open seats at casinos holding more than `CASINO_FISH_MAX` fish.

    The refill pass only *adds* fish (up to MAX); it never sheds. So when
    the cap is lowered — or a casino over-seats for any reason — running
    casinos never rebalance on their own, because content fish rarely
    leave (they stay and reload until bust). Shed the excess so the
    configured mix (more grinders per fish) actually takes hold: a fish
    only feeds the population if it loses to a grinder, not another fish.

    Conservation-safe, mirroring `_reclaim_zombie_casino_seats`: a shed
    fish's seat chips return to the pool (`casino_seat_return`) before the
    seat is opened, and its residual bankroll returns via the
    drain-on-exit sweep on this same resolve (it's no longer seated). A
    failed seat-return leaves the seat to retry next resolve rather than
    vanish chips. Closing casinos are skipped (winding down anyway).

    Returns the number of seats shed.
    """
    shed = 0
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        if table.table_type != 'casino':
            continue
        if is_closing(cash_table_repo, sandbox_id, table.table_id):
            continue
        fish_idx = [
            i
            for i, s in enumerate(table.seats)
            if s.get('kind') == 'ai' and s.get('archetype') == 'fish'
        ]
        excess = len(fish_idx) - CASINO_FISH_MAX
        if excess <= 0:
            continue
        new_seats = list(table.seats)
        changed = False
        shed_pids: List[str] = []
        # Shed the trailing `excess` fish — deterministic, order-stable.
        for idx in fish_idx[-excess:]:
            slot = table.seats[idx]
            pid = slot.get('personality_id')
            chips = int(slot.get('chips') or 0)
            if chips > 0 and pid:
                try:
                    row_id = record_casino_seat_return(
                        chip_ledger_repo,
                        personality_id=pid,
                        amount=chips,
                        context={
                            'site': 'casino_shed_excess_fish',
                            'table_id': table.table_id,
                            'stake_label': table.stake_label,
                        },
                        sandbox_id=sandbox_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "[CASH][CASINO] shed seat-return raised for %s/%s " "(%d chips): %s",
                        table.table_id,
                        pid,
                        chips,
                        exc,
                    )
                    continue
                if row_id is None:
                    # Return failed — leave the seat; retry next resolve.
                    continue
            new_seats[idx] = open_slot()
            changed = True
            shed += 1
            if pid:
                shed_pids.append(pid)
        if changed:
            updated = CashTableState(
                table_id=table.table_id,
                stake_label=table.stake_label,
                seats=new_seats,
                created_at=table.created_at,
                last_activity_at=now,
                name=table.name,
                table_type='casino',
                dealer_idx=table.dealer_idx,
                closing_hand_countdown=table.closing_hand_countdown,
            )
            try:
                cash_table_repo.save_table(updated, sandbox_id=sandbox_id, now=now)
            except Exception as exc:
                logger.warning(
                    "[CASH][CASINO] shed save_table failed for %s: %s",
                    table.table_id,
                    exc,
                )
            else:
                # SHADOW (Presence cutover Phase 1): each shed casino fish
                # returns its POOL-funded identity to POOL (the seat is opened;
                # its residual bankroll drains via the drain-on-exit sweep, which
                # is shadowed in _drain_fish_bankroll_to_pool). Emitted only after
                # the authoritative seat write succeeded.
                for spid in shed_pids:
                    presence_shadow.shadow_transition(
                        entity_id=ai_entity_id(spid),
                        sandbox_id=sandbox_id,
                        event=PresenceEvent.RETURN_TO_POOL,
                    )
    return shed


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

    Fish are curated `archetype='fish'` personalities (see
    `PersonalityRepository.list_fish_for_cash_mode`) seated at casinos
    with **pool-funded bankrolls**: the bank pool seeds a fish's bankroll
    (~3x buy-in, jittered), the fish buys in (and re-buys, via the normal
    short-stack movement path) from it, and whatever remains drains back
    to the pool when the fish leaves the casino. Invariant: an un-seated
    fish's bankroll is 0, so every seating prefunds fresh from the pool.

    Per refresh, for each stake in `CASINO_SPAWN_THRESHOLDS`:

      1. **Refill** — active (non-closing) casinos below `CASINO_FISH_MAX`
         fish get one more unseated fish seated (the "trickle in" feel),
         when the pool can fund it.
      2. **Teardown** — a casino with zero fish that the pool can't refill
         enters `closing` state with a countdown (smooth shutdown). When a
         closing casino's countdown reaches 0, ALL pool-funded chips (seat
         residuals + each fish's remaining bankroll) return to the pool and
         the row is deleted. If any return write fails the teardown ABORTS
         (retried next tick) so chips never vanish. Persona/bankroll rows
         are NEVER deleted — fish are permanent.
      3. **Spawn** — a stake with no active OR closing casino, pool >=
         threshold, and >= `CASINO_MIN_HUNGRY_GRINDERS` hungry grinders,
         gets a fresh casino seeded with `[MIN, MAX]` unseated fish.

    Best-effort wrapping at each pass.
    """
    batch = CasinoProvisioningBatch()
    if cash_table_repo is None or chip_ledger_repo is None:
        return batch
    if bankroll_repo is None or personality_repo is None:
        return batch

    # The casino fish pool: real, curated `archetype='fish'` personas.
    # Discovery is by persona (no bankroll dependency), so a fresh sandbox
    # with the personas seeded can spawn a casino cold — bankrolls are
    # pool-funded on seating.
    fish_ids: Set[str] = {
        p['personality_id']
        for p in personality_repo.list_fish_for_cash_mode()
        if p.get('personality_id')
    }
    if not fish_ids:
        return batch

    # Self-heal: reclaim zombie AI seats whose persona no longer resolves
    # (old-model tourist-<uuid> seats from before the fish-as-personas
    # migration, or any persona deleted while seated). They permanently
    # consume seats the human / live-filling grinders could take. Runs
    # before the passes so freed seats are refillable this same resolve;
    # the later `already_seated`/`by_stake` reads re-fetch and see the
    # cleaned tables. Best-effort — a reclaim hiccup must not tank the
    # whole provisioning resolve.
    try:
        reclaimed = _reclaim_zombie_casino_seats(
            cash_table_repo,
            chip_ledger_repo,
            sandbox_id=sandbox_id,
            valid_pids=personality_repo.list_all_personality_ids(),
            fish_ids=fish_ids,
            now=now,
        )
        if reclaimed:
            logger.info(
                "[CASH][CASINO] reclaimed %d zombie seat(s) in sandbox %s",
                reclaimed,
                sandbox_id,
            )
    except Exception as exc:
        logger.warning("[CASH][CASINO] zombie-seat reclaim failed: %s", exc)

    # Shed fish over the per-casino cap (e.g. after lowering CASINO_FISH_MAX)
    # so running casinos rebalance toward the configured grinder-heavy mix.
    # Runs before the drain-on-exit sweep below so a shed fish's bankroll
    # returns to the pool on this same resolve.
    try:
        shed = _shed_excess_fish(
            cash_table_repo,
            chip_ledger_repo,
            sandbox_id=sandbox_id,
            now=now,
        )
        if shed:
            logger.info(
                "[CASH][CASINO] shed %d over-cap fish seat(s) in sandbox %s",
                shed,
                sandbox_id,
            )
    except Exception as exc:
        logger.warning("[CASH][CASINO] excess-fish shed failed: %s", exc)

    # Globally-seated pids — never seat the same fish at two tables in one
    # resolve (each persona is one identity; the player map is name-keyed
    # and can't hold a pid twice).
    already_seated: Set[str] = set()
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        for slot in table.seats:
            if slot.get('kind') == 'ai':
                pid = slot.get('personality_id')
                if pid:
                    already_seated.add(pid)

    # Drain-on-exit sweep. Any fish that left a casino since the last
    # resolve (busted out, went home via movement, or got bumped) still
    # holds its pool-funded bankroll. Return it to the pool now so the
    # invariant "an un-seated fish's bankroll is 0" holds and the pool
    # doesn't slowly bleed into idle fish. Currently-seated fish (in
    # `already_seated`) are skipped — their bankroll is live.
    for pid in sorted(fish_ids - already_seated):
        if _load_ai_chips(bankroll_repo, pid, sandbox_id) > 0:
            _drain_fish_bankroll_to_pool(
                bankroll_repo,
                chip_ledger_repo,
                personality_id=pid,
                sandbox_id=sandbox_id,
                now=now,
                reason_detail='fish_left_casino',
            )

    by_stake = _existing_casinos_by_stake(cash_table_repo, sandbox_id=sandbox_id)

    # --- Pass 1: refill --------------------------------------------------
    for stake_label, tables_here in by_stake.items():
        # Retired tier (e.g. the old $200 casino, replaced by the cardroom
        # whale): never refill it — Pass 2 winds it down instead.
        if stake_label not in CASINO_SPAWN_THRESHOLDS:
            continue
        _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
        fish_buy_in = min(int(min_buy_in * CASINO_FISH_BUY_IN_MULTIPLIER), max_buy_in)
        for table in tables_here:
            if is_closing(cash_table_repo, sandbox_id, table.table_id):
                continue
            if _count_seated_fish(table) >= CASINO_FISH_MAX:
                continue
            if compute_bank_pool_reserves(chip_ledger_repo, sandbox_id=sandbox_id) < fish_buy_in:
                continue
            refill = _refill_one_fish(
                table,
                stake_label=stake_label,
                fish_buy_in=fish_buy_in,
                table_max_buy_in=max_buy_in,
                chip_ledger_repo=chip_ledger_repo,
                cash_table_repo=cash_table_repo,
                bankroll_repo=bankroll_repo,
                sandbox_id=sandbox_id,
                rng=rng,
                now=now,
                already_seated=already_seated,
                fish_ids=fish_ids,
            )
            if refill is not None:
                batch.refills.append(refill)
                logger.info(
                    "[CASH][CASINO] refill %s: +%s (%d chips drawn from pool)",
                    table.table_id,
                    refill.fish_id,
                    refill.bank_pool_drawn,
                )

    if batch.refills:
        by_stake = _existing_casinos_by_stake(cash_table_repo, sandbox_id=sandbox_id)

    # --- Pass 2: teardown (smooth shutdown via closing state) ------------
    for stake_label, tables_here in list(by_stake.items()):
        _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
        fish_buy_in = min(int(min_buy_in * CASINO_FISH_BUY_IN_MULTIPLIER), max_buy_in)
        for table in tables_here:
            seated_count = _count_seated_fish(table)
            currently_closing = is_closing(cash_table_repo, sandbox_id, table.table_id)
            countdown = get_closing_countdown(cash_table_repo, sandbox_id, table.table_id)

            if currently_closing:
                if countdown is not None and countdown <= 0:
                    # Countdown elapsed. Return ALL pool-funded chips before
                    # deleting: seat residuals AND each fish's remaining
                    # bankroll. Abort (retry next tick) if any return write
                    # fails — deleting with un-returned chips breaks
                    # conservation. Persona/bankroll rows are NOT deleted.
                    fish_pids = [
                        slot['personality_id']
                        for slot in table.seats
                        if slot.get('kind') == 'ai'
                        and slot.get('archetype') == 'fish'
                        and slot.get('personality_id')
                    ]
                    seat_returned, seat_stranded = _return_seat_residuals_to_pool(
                        table,
                        chip_ledger_repo=chip_ledger_repo,
                        sandbox_id=sandbox_id,
                        reason_detail='casino_closing_elapsed',
                    )
                    bankroll_stranded = 0
                    for pid in fish_pids:
                        _, stranded = _drain_fish_bankroll_to_pool(
                            bankroll_repo,
                            chip_ledger_repo,
                            personality_id=pid,
                            sandbox_id=sandbox_id,
                            now=now,
                            reason_detail='casino_closing_elapsed',
                        )
                        bankroll_stranded += stranded
                    if seat_stranded or bankroll_stranded:
                        logger.warning(
                            "[CASH][CASINO] teardown ABORTED for %s: chips stranded "
                            "(seat=%d bankroll=%d); retrying next tick.",
                            table.table_id,
                            seat_stranded,
                            bankroll_stranded,
                        )
                        continue
                    try:
                        cash_table_repo.delete_table(table.table_id, sandbox_id=sandbox_id)
                        clear_closing(cash_table_repo, sandbox_id, table.table_id)
                        batch.teardowns.append(
                            CasinoTeardown(
                                table_id=table.table_id,
                                stake_label=stake_label,
                                reason='closing_countdown_elapsed',
                            )
                        )
                        by_stake[stake_label] = [
                            t for t in tables_here if t.table_id != table.table_id
                        ]
                        logger.info(
                            "[CASH][CASINO] teardown %s: closing elapsed "
                            "(returned %d seat chips to pool)",
                            table.table_id,
                            seat_returned,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[CASH][CASINO] teardown failed for %s: %s",
                            table.table_id,
                            exc,
                        )
                else:
                    # Tick the countdown down once per provisioning
                    # resolution. A casino only enters closing once it's
                    # empty of fish (above), and an empty table plays no
                    # hands — so a per-hand decrement would never fire and
                    # the countdown would stick forever. Counting down per
                    # resolution guarantees a closing table reaches 0 and
                    # tears down on a later pass.
                    decrement_closing_hands(
                        cash_table_repo,
                        sandbox_id,
                        table.table_id,
                    )
                continue

            # Retired tier: a casino at a stake no longer in
            # CASINO_SPAWN_THRESHOLDS (e.g. the old $200 casino, replaced by
            # the cardroom whale) shouldn't exist anymore. Wind it down even
            # with fish seated — the closing countdown plays out and teardown
            # returns all pool-funded chips, so a pre-existing casino from
            # before the tier was retired drains cleanly instead of lingering
            # forever (Pass 1 already declines to refill it).
            if stake_label not in CASINO_SPAWN_THRESHOLDS:
                enter_closing(
                    cash_table_repo, sandbox_id, table.table_id, CASINO_CLOSING_HAND_COUNTDOWN
                )
                batch.teardowns.append(
                    CasinoTeardown(
                        table_id=table.table_id,
                        stake_label=stake_label,
                        reason='retired_tier_wind_down',
                    )
                )
                logger.info(
                    "[CASH][CASINO] %s retired-tier wind-down (%s no longer spawns)",
                    table.table_id,
                    stake_label,
                )
                continue

            # Dam wind-down: a high-stakes gate closes as the reservoir
            # drains below its floor, even with fish still seated — it's a
            # relief valve, not a permanent fixture. The closing countdown
            # plays out and teardown returns all chips to the pool, so the
            # pool settles into a band rather than draining to empty.
            close_floor = CASINO_CLOSE_THRESHOLDS.get(stake_label)
            if (
                close_floor is not None
                and compute_bank_pool_reserves(chip_ledger_repo, sandbox_id=sandbox_id)
                < close_floor
            ):
                enter_closing(
                    cash_table_repo, sandbox_id, table.table_id, CASINO_CLOSING_HAND_COUNTDOWN
                )
                batch.teardowns.append(
                    CasinoTeardown(
                        table_id=table.table_id,
                        stake_label=stake_label,
                        reason='dam_wind_down_pool_below_floor',
                    )
                )
                logger.info(
                    "[CASH][CASINO] %s dam wind-down (pool below %d floor)",
                    table.table_id,
                    close_floor,
                )
                continue

            # Not closing yet. Enter closing only when there are no fish AND
            # the pool can't refill one.
            if seated_count > 0:
                continue
            if compute_bank_pool_reserves(chip_ledger_repo, sandbox_id=sandbox_id) >= fish_buy_in:
                continue
            enter_closing(
                cash_table_repo, sandbox_id, table.table_id, CASINO_CLOSING_HAND_COUNTDOWN
            )
            batch.teardowns.append(
                CasinoTeardown(
                    table_id=table.table_id,
                    stake_label=stake_label,
                    reason=f'closing_announced_{CASINO_CLOSING_HAND_COUNTDOWN}_hands',
                )
            )
            logger.info(
                "[CASH][CASINO] %s entering closing state (%d hands)",
                table.table_id,
                CASINO_CLOSING_HAND_COUNTDOWN,
            )

    # --- Pass 3: spawn ---------------------------------------------------
    by_stake_after_teardown = _existing_casinos_by_stake(cash_table_repo, sandbox_id=sandbox_id)
    # Hungry-grinder demand signal — count ALL hungry grinders (including
    # lobby-seated ones); they're the customers the casino spawns to lure.
    hungry_grinders = list_hungry_grinders(bankroll_repo, sandbox_id=sandbox_id, now=now)

    threshold_order = [s for s in STAKES_ORDER if s in CASINO_SPAWN_THRESHOLDS]
    for idx, stake_label in enumerate(threshold_order):
        threshold = CASINO_SPAWN_THRESHOLDS[stake_label]
        # One casino per stake; a closing table holds the slot until its
        # countdown elapses.
        if by_stake_after_teardown.get(stake_label):
            continue
        # Dam ladder: a higher gate opens only once the gate below it is
        # open — the reservoir fills past each stake before the next
        # releases ($2 → $10 → $50 → $200, gradual). A lower tier spawned
        # earlier in this same loop counts (by_stake_after_teardown is
        # updated on each spawn below), so a deep pool can cascade several
        # gates open in one resolve.
        if idx > 0 and not by_stake_after_teardown.get(threshold_order[idx - 1]):
            continue
        if compute_bank_pool_reserves(chip_ledger_repo, sandbox_id=sandbox_id) < threshold:
            continue
        if len(hungry_grinders) < CASINO_MIN_HUNGRY_GRINDERS:
            continue

        _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
        fish_buy_in = min(int(min_buy_in * CASINO_FISH_BUY_IN_MULTIPLIER), max_buy_in)

        available = sorted(fish_ids - already_seated)
        target_count = min(rng.randint(CASINO_FISH_MIN, CASINO_FISH_MAX), len(available))
        if target_count < CASINO_FISH_MIN:
            continue
        chosen = rng.sample(available, target_count)
        seat_positions = sorted(rng.sample(range(TABLE_SEAT_COUNT), target_count))

        # Prefund each fish's bankroll from the pool and place it in a seat
        # (in memory). Bail out of the lineup if the pool runs dry mid-fill.
        seats = [open_slot() for _ in range(TABLE_SEAT_COUNT)]
        seeded: List[str] = []
        seeded_seats: List[Tuple[str, int]] = []
        total_drawn = 0
        for pid, seat_idx in zip(chosen, seat_positions, strict=False):
            if compute_bank_pool_reserves(chip_ledger_repo, sandbox_id=sandbox_id) < fish_buy_in:
                break
            drawn = _prefund_fish_from_pool(
                bankroll_repo,
                chip_ledger_repo,
                personality_id=pid,
                target_chips=_fish_prefund(max_buy_in, rng),
                sandbox_id=sandbox_id,
                now=now,
                context={'site': 'casino_spawn', 'stake_label': stake_label},
            )
            if drawn <= 0:
                continue
            seats[seat_idx] = ai_slot_fish(pid, fish_buy_in)
            seeded.append(pid)
            seeded_seats.append((pid, seat_idx))
            total_drawn += drawn

        if len(seeded) < CASINO_FISH_MIN:
            # Not a viable lineup — return everything drawn and skip.
            for pid in seeded:
                _drain_fish_bankroll_to_pool(
                    bankroll_repo,
                    chip_ledger_repo,
                    personality_id=pid,
                    sandbox_id=sandbox_id,
                    now=now,
                    reason_detail='spawn_aborted',
                )
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
            logger.warning("[CASH][CASINO] spawn save_table failed for %s: %s", table_id, exc)
            for pid in seeded:
                _drain_fish_bankroll_to_pool(
                    bankroll_repo,
                    chip_ledger_repo,
                    personality_id=pid,
                    sandbox_id=sandbox_id,
                    now=now,
                    reason_detail='spawn_save_failed',
                )
            continue

        # SHADOW (Presence cutover Phase 1): each fish seated into the newly
        # spawned casino is a POOL-funded AI seeded into the sandbox
        # (SEED → POOL) then seated (SIT → SEATED with table_id+seat_index).
        # Reached only after the authoritative table write above succeeded.
        for pid, seat_idx in seeded_seats:
            presence_shadow.shadow_transition(
                entity_id=ai_entity_id(pid),
                sandbox_id=sandbox_id,
                event=PresenceEvent.SEED,
            )
            presence_shadow.shadow_transition(
                entity_id=ai_entity_id(pid),
                sandbox_id=sandbox_id,
                event=PresenceEvent.SIT,
                table_id=table_id,
                seat_index=seat_idx,
            )

        # Seats are persisted with their buy-in chips; debit each fish's
        # buy-in from its (prefunded) bankroll so bankroll + seat == prefund.
        for pid in seeded:
            debit_bankroll_for_seat(
                bankroll_repo,
                pid,
                fish_buy_in,
                sandbox_id=sandbox_id,
                chip_ledger_repo=chip_ledger_repo,
                now=now,
            )
            already_seated.add(pid)

        clear_closing(cash_table_repo, sandbox_id, table_id)
        batch.spawns.append(
            CasinoSpawn(
                table_id=table_id,
                stake_label=stake_label,
                fish_seated=seeded,
                bank_pool_drawn=total_drawn,
            )
        )
        by_stake_after_teardown.setdefault(stake_label, []).append(new_state)
        logger.info(
            "[CASH][CASINO] spawn %s (%s): %d fish, %d chips drawn from pool",
            table_id,
            stake_label,
            len(seeded),
            total_drawn,
        )

    return batch


# --- Whale provisioning -----------------------------------------------


def _find_seated_whale(
    cash_table_repo,
    *,
    sandbox_id: str,
) -> Optional[Tuple[CashTableState, int, str]]:
    """Return `(table, seat_idx, personality_id)` for the live whale, or
    None if no whale is seated.

    A whale is a `archetype='fish'` seat at a LOBBY table — regular fish
    are casino-only, so a fish stamp at a cardroom table is unambiguously
    the whale. One whale at a time, so the first match wins (lowest seat
    index of the first lobby table iterated).
    """
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        if table.table_type != 'lobby':
            continue
        for idx, slot in enumerate(table.seats):
            if slot.get('kind') == 'ai' and slot.get('archetype') == 'fish':
                pid = slot.get('personality_id')
                if pid:
                    return table, idx, pid
    return None


def _open_lobby_table_for_stake(
    cash_table_repo,
    *,
    sandbox_id: str,
    stake_label: str,
) -> Optional[CashTableState]:
    """First lobby table at `stake_label` with at least one open seat."""
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        if table.table_type != 'lobby' or table.stake_label != stake_label:
            continue
        if _open_seat_indices(table):
            return table
    return None


def resolve_whale_provisioning(
    *,
    cash_table_repo,
    bankroll_repo,
    personality_repo,
    chip_ledger_repo,
    sandbox_id: str,
    rng: random.Random,
    now: datetime,
) -> WhaleProvisioningBatch:
    """Spawn / wind down the cardroom whale — the top gate of the dam.

    One whale at a time. Mechanically the whale is just a fish seated at a
    LOBBY table with a deep (`whale=True`) pool-funded prefund, so the
    pool↔seat conservation is identical to a casino fish: pool → bankroll
    (seed) → seat buy-in; on exit, seat residual + bankroll return to the
    pool. The whale's natural exit (busts as it's farmed, or storms off on
    tilt) runs through ordinary fish movement and the casino resolver's
    drain-on-exit sweep — this resolver only handles the *spawn* and the
    *dam wind-down* (a forced recall when the pool drops below the stake's
    floor). Best-effort: a failure must not tank the lobby refresh.

    Returns a `WhaleProvisioningBatch` carrying at most one spawn or one
    teardown, for the caller (lobby refresh) to surface on the ticker.
    """
    batch = WhaleProvisioningBatch()
    if cash_table_repo is None or chip_ledger_repo is None:
        return batch
    if bankroll_repo is None or personality_repo is None:
        return batch

    # Name lookup for the ticker. `list_fish_for_cash_mode` is the whale's
    # persona pool too (a whale is a fish persona).
    fish_rows = personality_repo.list_fish_for_cash_mode()
    fish_ids: Set[str] = {r['personality_id'] for r in fish_rows if r.get('personality_id')}
    if not fish_ids:
        return batch
    name_of: Dict[str, str] = {
        r['personality_id']: r.get('name') or r['personality_id']
        for r in fish_rows
        if r.get('personality_id')
    }

    # --- One whale live: wind-down check only ---------------------------
    seated = _find_seated_whale(cash_table_repo, sandbox_id=sandbox_id)
    if seated is not None:
        table, seat_idx, pid = seated
        floor = WHALE_POOL_FLOORS.get(table.stake_label)
        if floor is None:
            return batch  # stake not whale-eligible (config changed) — leave it
        pool = compute_bank_pool_reserves(chip_ledger_repo, sandbox_id=sandbox_id)
        if pool >= floor:
            return batch  # reservoir healthy — let the whale ride
        teardown = _wind_down_whale(
            cash_table_repo,
            chip_ledger_repo,
            bankroll_repo,
            table=table,
            seat_idx=seat_idx,
            personality_id=pid,
            name=name_of.get(pid, pid),
            sandbox_id=sandbox_id,
            now=now,
        )
        if teardown is not None:
            batch.teardown = teardown
            logger.info(
                "[CASH][WHALE] %s wind-down at %s (pool below %d floor)",
                pid,
                table.table_id,
                floor,
            )
        return batch

    # --- No whale live: try to spawn one --------------------------------
    already_seated: Set[str] = set()
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        for slot in table.seats:
            if slot.get('kind') == 'ai' and slot.get('personality_id'):
                already_seated.add(slot['personality_id'])
    available = sorted(fish_ids - already_seated)
    if not available:
        return batch

    # Prefer the highest eligible stake the pool can fund — the biggest
    # release first (a $200 whale over a $50 whale when reserves allow).
    eligible_stakes = [s for s in STAKES_ORDER if s in WHALE_POOL_THRESHOLDS]
    for stake_label in reversed(eligible_stakes):
        threshold = WHALE_POOL_THRESHOLDS[stake_label]
        if compute_bank_pool_reserves(chip_ledger_repo, sandbox_id=sandbox_id) < threshold:
            continue
        lobby_table = _open_lobby_table_for_stake(
            cash_table_repo,
            sandbox_id=sandbox_id,
            stake_label=stake_label,
        )
        if lobby_table is None:
            continue  # no open cardroom seat at this stake — try a lower one
        whale_pid = rng.choice(available)
        spawn = _spawn_whale_at(
            lobby_table,
            stake_label=stake_label,
            personality_id=whale_pid,
            name=name_of.get(whale_pid, whale_pid),
            chip_ledger_repo=chip_ledger_repo,
            cash_table_repo=cash_table_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id=sandbox_id,
            rng=rng,
            now=now,
        )
        if spawn is None:
            continue
        batch.spawn = spawn
        logger.info(
            "[CASH][WHALE] %s sat down at %s (%s): buy-in %d, %d drawn from pool",
            spawn.whale_id,
            lobby_table.table_id,
            stake_label,
            spawn.buy_in,
            spawn.bank_pool_drawn,
        )
        return batch

    return batch


def _spawn_whale_at(
    table: CashTableState,
    *,
    stake_label: str,
    personality_id: str,
    name: str,
    chip_ledger_repo,
    cash_table_repo,
    bankroll_repo,
    sandbox_id: str,
    rng: random.Random,
    now: datetime,
) -> Optional[WhaleSpawn]:
    """Seat one whale at an open seat of a cardroom (lobby) table.

    Funds it exactly like a casino fish — pool → bankroll (deep
    `whale=True` prefund) → seat buy-in — but buys in for the table MAX
    (a deep, dramatic stack on the felt) and the rest of the prefund is
    rebuy reserve. Returns None (with everything unwound) when no seat is
    open, the pool can't fund the prefund, or a write fails.
    """
    open_seats = _open_seat_indices(table)
    if not open_seats:
        return None
    _, _, max_buy_in = table_buy_in_window(stake_label)
    buy_in = max_buy_in

    drawn = _prefund_fish_from_pool(
        bankroll_repo,
        chip_ledger_repo,
        personality_id=personality_id,
        target_chips=_fish_prefund(max_buy_in, rng, whale=True),
        sandbox_id=sandbox_id,
        now=now,
        context={
            'site': 'whale_spawn',
            'stake_label': stake_label,
            'table_id': table.table_id,
        },
    )
    if drawn <= 0:
        return None

    if (
        debit_bankroll_for_seat(
            bankroll_repo,
            personality_id,
            buy_in,
            sandbox_id=sandbox_id,
            chip_ledger_repo=chip_ledger_repo,
            now=now,
        )
        is None
    ):
        # Prefund (≥10x max buy-in) should always cover the max buy-in;
        # if it somehow didn't, unwind the draw to the pool.
        _drain_fish_bankroll_to_pool(
            bankroll_repo,
            chip_ledger_repo,
            personality_id=personality_id,
            sandbox_id=sandbox_id,
            now=now,
            reason_detail='whale_buyin_failed',
        )
        return None

    seat_idx = rng.choice(open_seats)
    new_seats = list(table.seats)
    new_seats[seat_idx] = ai_slot_fish(personality_id, buy_in)
    updated = CashTableState(
        table_id=table.table_id,
        stake_label=table.stake_label,
        seats=new_seats,
        created_at=table.created_at,
        last_activity_at=now,
        name=table.name,
        table_type=table.table_type,  # 'lobby' — the whale sits where players are
        dealer_idx=table.dealer_idx,
        closing_hand_countdown=table.closing_hand_countdown,
    )
    try:
        cash_table_repo.save_table(updated, sandbox_id=sandbox_id, now=now)
    except Exception as exc:
        logger.warning(
            "[CASH][WHALE] spawn save_table failed for %s: %s",
            table.table_id,
            exc,
        )
        # Seat write never persisted — return the whole funded bankroll
        # (prefund, incl. the part the buy-in debit moved to nowhere) to
        # the pool so the draw isn't stranded. Mirrors `_refill_one_fish`.
        _drain_fish_bankroll_to_pool(
            bankroll_repo,
            chip_ledger_repo,
            personality_id=personality_id,
            sandbox_id=sandbox_id,
            now=now,
            reason_detail='whale_save_failed',
        )
        return None

    return WhaleSpawn(
        table_id=table.table_id,
        stake_label=stake_label,
        whale_id=personality_id,
        name=name,
        buy_in=buy_in,
        bank_pool_drawn=drawn,
    )


def _wind_down_whale(
    cash_table_repo,
    chip_ledger_repo,
    bankroll_repo,
    *,
    table: CashTableState,
    seat_idx: int,
    personality_id: str,
    name: str,
    sandbox_id: str,
    now: datetime,
) -> Optional[WhaleTeardown]:
    """Recall a live whale: vacate its seat and return all pool-funded
    chips (seat residual + remaining bankroll) to the pool.

    Conservation-safe ordering — this is the SAME shape as a natural
    leave (`movement.py`'s `from_seat` → `credit_ai_cash_out` → next-tick
    drain), which is what makes it robust against partial-write failures:

      1. **Vacate the seat first** (`save_table` opening the slot). If this
         write fails, NOTHING else has happened — the whale stays seated,
         no ledger row was written, and the next tick retries cleanly. The
         whale is still found by `_find_seated_whale`, so the wind-down
         re-attempts; nothing double-counts.
      2. **Move the seat residual into the bankroll** — a ledger-SILENT
         surface move (chips already exist; they're just changing surface,
         exactly as `credit_ai_cash_out` folds a table stack into the
         bankroll without a ledger row).
      3. **Drain the whole bankroll to the pool** via
         `_drain_fish_bankroll_to_pool` (one `casino_seat_return`). If that
         ledger write strands, the chips sit safely in the bankroll and the
         casino resolver's drain-on-exit sweep recovers them next tick (the
         whale is unseated now) — no vanish, no double-count.

    Returns None if the seat couldn't be vacated (retry next tick).

    NB: deliberately NOT mirroring `_shed_excess_fish`, which writes the
    seat-return ledger row BEFORE opening the seat — a `save_table` failure
    there leaves a committed pool credit against chips still on the felt
    (double-count), and for a retriable wind-down that compounds per tick.
    """
    seat_chips = int(table.seats[seat_idx].get('chips') or 0)

    new_seats = list(table.seats)
    new_seats[seat_idx] = open_slot()
    updated = CashTableState(
        table_id=table.table_id,
        stake_label=table.stake_label,
        seats=new_seats,
        created_at=table.created_at,
        last_activity_at=now,
        name=table.name,
        table_type=table.table_type,
        dealer_idx=table.dealer_idx,
        closing_hand_countdown=table.closing_hand_countdown,
    )
    try:
        cash_table_repo.save_table(updated, sandbox_id=sandbox_id, now=now)
    except Exception as exc:
        logger.warning(
            "[CASH][WHALE] wind-down save_table failed for %s: %s — "
            "seat intact, no ledger written, will retry",
            table.table_id,
            exc,
        )
        return None

    # Seat is now open. Fold the residual into the bankroll (no ledger),
    # then drain the lot back to the pool.
    if seat_chips > 0:
        existing = _load_ai_chips(bankroll_repo, personality_id, sandbox_id)
        bankroll_repo.save_ai_bankroll(
            AIBankrollState(
                personality_id=personality_id,
                chips=existing + seat_chips,
                last_regen_tick=now,
            ),
            sandbox_id=sandbox_id,
        )
    _, stranded = _drain_fish_bankroll_to_pool(
        bankroll_repo,
        chip_ledger_repo,
        personality_id=personality_id,
        sandbox_id=sandbox_id,
        now=now,
        reason_detail='whale_wind_down',
    )
    if stranded:
        logger.warning(
            "[CASH][WHALE] wind-down bankroll drain partial for %s "
            "(%d stranded; drain-on-exit sweep recovers it next tick)",
            personality_id,
            stranded,
        )
    return WhaleTeardown(
        table_id=table.table_id,
        stake_label=table.stake_label,
        whale_id=personality_id,
        name=name,
        reason='dam_wind_down_pool_below_floor',
    )
