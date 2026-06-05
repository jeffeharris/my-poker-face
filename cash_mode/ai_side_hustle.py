"""AI side hustle — the active, off-grid earning mechanic.

The mirror of vice (`cash_mode/ai_vice_spending.py`). Vice drains chips
from *rich* AIs into the bank pool; the side hustle draws chips back
*out* of the pool for *broke* AIs. It replaces passive idle regen: an AI
that can't afford to play no longer accrues chips while sitting idle —
instead it goes off-grid to a personality-flavored side hustle for a
bounded duration and returns with a lump drawn from the recyclable pool.

Two passes wire into `refresh_unseated_tables` (Phase 6), exactly like
vice:

  - `resolve_ai_side_hustle` (post-loop): for each broke candidate, roll
    an earning target, take the neediest up to `HUSTLE_STARTS_PER_REFRESH`,
    make the narration call (sync — the duration bucket comes back with
    the narration), insert the state row, and **pay the earning up front** —
    draw it from the bank pool now (clamped to live depth) and credit the
    bankroll immediately, so the AI is off the grid for the duration with
    its earnings already banked.
  - `tick_side_hustle_expirations` (start of refresh): for each hustle
    whose `ends_at` has passed, delete the row and return a
    `HustleEndResult` so the lobby can emit a "returned" ticker row. No
    chips move here — the payout was already credited at start.

Paying up front (rather than at expiry) fixes the bug where an AI left for
a hustle, the pool drained while it was away, and it returned to an empty
bank with nothing to show. The pool is checked when the AI leaves, so a
hustle only fires if the bank can fund it.

The earning amount is the inverse of `compute_vice_amount`: vice rolls a
fraction of how far an AI is *above* baseline; the hustle rolls a
fraction of how far it is *below* it. Both are jittered.

Spec: `docs/plans/CASH_MODE_SIDE_HUSTLE.md`.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Set, Tuple

from cash_mode import presence_shadow

# Shared off-grid duration helpers — the bucket model is identical to
# vice's (the LLM picks short/medium/long; the exact delta is sampled
# within the bucket). Reused rather than duplicated; if hustles ever need
# their own ranges, lift these into a shared module then.
from cash_mode.ai_vice_spending import (
    DEFAULT_DURATION_BUCKET,
    DURATION_RANGES,
    duration_for_bucket,
)
from cash_mode.presence import PresenceEvent, ai_entity_id

logger = logging.getLogger(__name__)


# --- Amount constants -------------------------------------------------------

HUSTLE_BASE_FRACTION = 0.05
"""Base earn as a fraction of `starting_bankroll`, before the deficit
bonus and jitter. A persona at its baseline earns ~5% per hustle; the
deficit bonus pushes deeper-in-the-hole AIs higher."""

HUSTLE_DEFICIT_WEIGHT = 0.15
"""Extra earn fraction per unit of deficit_ratio. A fully-broke AI
(deficit_ratio ≈ 1.0) earns up to BASE + WEIGHT ≈ 20% of starting per
hustle, so it recovers in a handful of sessions rather than dozens."""

AMOUNT_JITTER_LOW = 0.5
AMOUNT_JITTER_HIGH = 1.5
"""Multiplicative jitter — the probabilistic roll within the bounded
band, so two same-deficit hustles don't return identical amounts."""

HUSTLE_MIN_AMOUNT = 50
"""Below this rolled target, skip the hustle. Mirrors vice's floor."""

HUSTLE_STARTS_PER_REFRESH = 2
"""How many hustle STARTS fire per refresh. Bounds the LLM narration
latency added to the refresh path (one sync call per start). Surplus
candidates re-roll next refresh."""


# --- Deadlock escape valve --------------------------------------------------

HUSTLE_FLOOR_WAGE = 0
"""Guaranteed minimum payout even when the pool can't cover it. 0 keeps
the loop strictly closed (a broke AI earns nothing when the pool is dry
and stays stuck until rake/vice refill it). A non-zero value is a
deliberate escape valve against the "everyone broke, pool empty, nobody
plays, pool never refills" deadlock — it draws beyond pool depth (dipping
the central bank), so it mildly breaks closed-ness. Off by default; see
CASH_MODE_SIDE_HUSTLE.md 'deadlock risk'."""


# --- Energy effect ----------------------------------------------------------

HUSTLE_ENERGY_MODE = 'frozen'
"""How the side hustle affects the `energy` dynamic axis on return.

While an AI is off-grid no hands play, so the per-hand `recover()` never
fires — the axes are frozen unless we explicitly move them.

  - `'frozen'` (default): leave energy untouched. Simplest; "static"
    reduces to this in practice.
  - `'drain'`: the grind is tiring — reduce energy by
    `HUSTLE_ENERGY_DRAIN`. Note the downstream coupling: pressure =
    1 − min(axes) drives vice probability, so draining energy makes the
    AI more likely to vice once it's rich again.

See CASH_MODE_SIDE_HUSTLE.md 'Psychology: energy while hustling'. v1
ships 'frozen'; 'drain' is a one-flag experiment."""

HUSTLE_ENERGY_DRAIN = 0.15
"""Energy reduction applied on return when HUSTLE_ENERGY_MODE == 'drain'."""


# --- Result dataclasses -----------------------------------------------------


@dataclass(frozen=True)
class HustleStartResult:
    """One hustle-fire outcome.

    Returned by `resolve_ai_side_hustle` so the lobby's event-emission
    pass can format a ticker row without re-running the per-AI logic.
    `amount` is the earning granted and credited up front (already clamped
    to bank-pool depth at start).
    """

    personality_id: str
    amount: int
    duration_bucket: str
    started_at: datetime
    ends_at: datetime
    narration: str
    deficit_ratio: float


@dataclass(frozen=True)
class HustleEndResult:
    """One hustle-expiry outcome.

    Chips were already credited at start, so `paid_amount` == `target_amount`
    == the amount granted on departure (echoed here for the "returned" ticker
    row, not a fresh payment). `energy_applied` is True iff the 'drain' energy
    effect ran.
    """

    personality_id: str
    started_at: datetime
    ends_at: datetime
    target_amount: int
    paid_amount: int
    duration_bucket: str
    narration: str
    energy_applied: bool


@dataclass
class SideHustleBatch:
    """Aggregate of starts + ends from one lobby refresh."""

    starts: List[HustleStartResult] = field(default_factory=list)
    ends: List[HustleEndResult] = field(default_factory=list)


# --- Pure formulas ----------------------------------------------------------


def compute_deficit_ratio(bankroll: int, starting_bankroll: int) -> float:
    """How far below baseline the AI is, as a fraction of starting.

    `deficit_ratio = max(0, (starting − bankroll) / starting)`. Returns
    0 for an AI at or above its baseline (no deficit → no hustle), and
    approaches 1.0 as the AI nears broke. The inverse of vice's
    `compute_excess_ratio`.
    """
    if starting_bankroll <= 0:
        return 0.0
    return max(0.0, (starting_bankroll - bankroll) / starting_bankroll)


def compute_hustle_amount(
    bankroll: int,
    starting_bankroll: int,
    rng: random.Random,
) -> int:
    """Roll the earning target for a hustle.

    `target = starting × (BASE + deficit_ratio × WEIGHT) × jitter`,
    capped at the gap to baseline so a single hustle never overshoots
    `starting_bankroll`. This is the rolled target only — the caller
    (`tick_side_hustle_expirations`) clamps it to live bank-pool depth
    at payout time.

    Returns 0 when the AI has no deficit (at/above baseline) or the
    target falls below `HUSTLE_MIN_AMOUNT`.
    """
    if starting_bankroll <= 0 or bankroll >= starting_bankroll:
        return 0
    deficit = compute_deficit_ratio(bankroll, starting_bankroll)
    earn_fraction = HUSTLE_BASE_FRACTION + deficit * HUSTLE_DEFICIT_WEIGHT
    jitter = rng.uniform(AMOUNT_JITTER_LOW, AMOUNT_JITTER_HIGH)
    raw = int(starting_bankroll * earn_fraction * jitter)
    gap = starting_bankroll - bankroll  # never overshoot baseline
    amount = min(raw, gap)
    if amount < HUSTLE_MIN_AMOUNT:
        return 0
    return amount


def compute_field_hustle_amount(
    current_liquid: int,
    field_target: int,
    rng: random.Random,
) -> int:
    """Field-relative hustle target: top up toward a FIELD percentile.

    The 'field_liquid' analogue of `compute_hustle_amount` — the deficit
    is measured against `field_target` (a field-wide liquid percentile)
    instead of the AI's own starting bankroll, so a low-baseline persona
    that's poor by the FIELD's standard still earns, and a high-baseline
    persona that's already field-rich does not. Never overshoots the
    target. Returns 0 when at/above target or below the minimum.
    """
    if field_target <= 0 or current_liquid >= field_target:
        return 0
    deficit = (field_target - current_liquid) / field_target
    earn_fraction = HUSTLE_BASE_FRACTION + deficit * HUSTLE_DEFICIT_WEIGHT
    jitter = rng.uniform(AMOUNT_JITTER_LOW, AMOUNT_JITTER_HIGH)
    raw = int(field_target * earn_fraction * jitter)
    gap = field_target - current_liquid  # never overshoot the field target
    amount = min(raw, gap)
    if amount < HUSTLE_MIN_AMOUNT:
        return 0
    return amount


# --- Narration callback type ------------------------------------------------


NarrateFn = Callable[[str, int], Tuple[str, str]]
"""Signature: (personality_id, amount) -> (narration, duration_bucket).
The bucket is one of 'short' / 'medium' / 'long'.

Phase 4 uses `_templated_narrate_fn`; Phase 7 plugs in the LLM-backed
narrator. Unlike vice, the hustle narration doesn't take a psych
snapshot — it's flavored by persona identity, not emotional state."""


def _templated_narrate_fn(personality_id: str, amount: int) -> Tuple[str, str]:
    """Fallback narrator — plain templated line, medium bucket.

    Used in Phase 4 and by the Phase 7 LLM narrator on failure.
    """
    return (
        f"{personality_id} stepped out to earn ${amount:,} on the side",
        DEFAULT_DURATION_BUCKET,
    )


# --- Public entry points ----------------------------------------------------


def resolve_ai_side_hustle(
    *,
    candidates: Set[str],
    side_hustle_repo,
    bankroll_repo,
    sandbox_id: str,
    rng: random.Random,
    now: datetime,
    narrate_fn: Optional[NarrateFn] = None,
    max_starts: int = HUSTLE_STARTS_PER_REFRESH,
    field_snapshot=None,
    chip_ledger_repo=None,
) -> List[HustleStartResult]:
    """Send broke candidates off to a side hustle; fire up to `max_starts`.

    `candidates` is the set of idle AIs the lobby has already filtered to
    "can't afford to play anywhere" (minus anyone already on a hustle or
    vice). This function rolls an earning target per candidate, takes the
    neediest `max_starts`, narrates, inserts the state row, and **pays the
    earning up front** — the payout is drawn from the bank pool now (clamped
    to live depth) and credited immediately, so the AI returns to chips that
    are already in its bankroll. This fixes the prior deferred-payout bug
    where an AI left, the pool drained while it was away, and it returned to
    an empty bank.

    Reserve-aware: with a `chip_ledger_repo`, a hustle that the pool can't
    fund does not fire (the AI stays idle and retries next refresh) unless
    `HUSTLE_FLOOR_WAGE` is set. Without a ledger (sim/test), the row is still
    inserted but no chips move (a credit with no paired pool draw would mint).

    The neediest-first ordering (deepest deficit ratio) means the AIs in
    the deepest hole get the narrated treatment; the rest re-roll next
    refresh. Returns `HustleStartResult`s in commit order.
    """
    if not candidates or side_hustle_repo is None or bankroll_repo is None:
        return []
    if narrate_fn is None:
        narrate_fn = _templated_narrate_fn

    # Phase 1: roll a target for each candidate; keep the ones that clear
    # the minimum, tagged with deficit_ratio for ordering.
    pending: List[Tuple[str, int, float]] = []  # (pid, amount, deficit_ratio)
    for pid in candidates:
        try:
            current = bankroll_repo.load_ai_bankroll_current(
                pid,
                sandbox_id=sandbox_id,
                now=now,
            )
        except Exception as exc:
            logger.warning(
                "[HUSTLE] load_ai_bankroll_current failed pid=%r: %s",
                pid,
                exc,
            )
            continue
        if current is None:
            continue
        try:
            knobs = bankroll_repo.load_personality_knobs(pid)
        except Exception as exc:
            logger.warning(
                "[HUSTLE] load_personality_knobs failed pid=%r: %s",
                pid,
                exc,
            )
            continue
        starting = knobs.starting_bankroll
        if field_snapshot is not None:
            # Field-relative: top up toward a field liquid percentile, and
            # order by how far below that target the AI sits.
            from cash_mode import economy_flags as _eflags

            target = int(field_snapshot.percentile(_eflags.FIELD_HUSTLE_TARGET_PERCENTILE))
            amount = compute_field_hustle_amount(int(current), target, rng)
            order_deficit = (target - current) / target if target > 0 else 0.0
        else:
            amount = compute_hustle_amount(current, starting, rng)
            order_deficit = compute_deficit_ratio(current, starting)
        if amount <= 0:
            continue
        pending.append((pid, amount, order_deficit))

    if not pending:
        return []

    # Phase 2: neediest first (deepest deficit), then pid for determinism.
    pending.sort(key=lambda t: (-t[2], t[0]))
    selected = pending[:max_starts]

    # Live bank-pool depth, decremented as we pay each start so several starts
    # in one refresh can't aggregate past what the pool holds. None == no ledger
    # wired (sim/test): go off-grid with the rolled amount, but move no chips.
    remaining_pool: Optional[int] = None
    if chip_ledger_repo is not None:
        from cash_mode.closed_economy import compute_bank_pool_reserves

        try:
            remaining_pool = compute_bank_pool_reserves(
                chip_ledger_repo,
                sandbox_id=sandbox_id,
            )
        except Exception as exc:
            logger.warning("[HUSTLE] compute_bank_pool_reserves failed: %s", exc)
            remaining_pool = 0

    out: List[HustleStartResult] = []
    for pid, rolled, deficit_ratio in selected:
        if remaining_pool is None:
            payout = rolled
        else:
            # Clamp the up-front payout to live pool depth; HUSTLE_FLOOR_WAGE is
            # the deliberate (off-by-default) escape valve that pays past depth.
            payout = min(rolled, max(0, remaining_pool))
            if HUSTLE_FLOOR_WAGE > 0 and payout < HUSTLE_FLOOR_WAGE:
                payout = HUSTLE_FLOOR_WAGE
            if payout <= 0:
                # Pool can't fund this hustle — don't send the AI off-grid to
                # earn nothing; it stays idle and retries next refresh.
                continue

        try:
            narration, duration_bucket = narrate_fn(pid, payout)
        except Exception as exc:
            logger.warning(
                "[HUSTLE] narrate_fn failed pid=%r: %s; using fallback",
                pid,
                exc,
            )
            narration, duration_bucket = _templated_narrate_fn(pid, payout)
        if duration_bucket not in DURATION_RANGES:
            duration_bucket = DEFAULT_DURATION_BUCKET

        ends_at = now + duration_for_bucket(duration_bucket, rng)

        # Insert the off-grid row FIRST, then credit. Insert-then-credit keeps
        # this drift-safe: if the credit fails the AI is off-grid having earned
        # nothing (drift stays 0), never paid-without-a-row (which could let the
        # still-broke AI hustle and double-draw next refresh).
        committed = _commit_hustle_start(
            side_hustle_repo=side_hustle_repo,
            sandbox_id=sandbox_id,
            personality_id=pid,
            amount=payout,
            duration_bucket=duration_bucket,
            narration=narration,
            started_at=now,
            ends_at=ends_at,
            deficit_ratio=deficit_ratio,
        )
        if not committed:
            continue

        if remaining_pool is not None and payout > 0:
            paid = _credit_hustle_payout(
                bankroll_repo=bankroll_repo,
                chip_ledger_repo=chip_ledger_repo,
                sandbox_id=sandbox_id,
                personality_id=pid,
                payout=payout,
                started_at=now,
                duration_bucket=duration_bucket,
                now=now,
            )
            remaining_pool -= paid

        out.append(committed)
    return out


def tick_side_hustle_expirations(
    *,
    side_hustle_repo,
    bankroll_repo,
    sandbox_id: str,
    now: datetime,
) -> List[HustleEndResult]:
    """Expire hustles whose `ends_at <= now` — a pure off-grid → idle return.

    The payout was already credited up front at START
    (`resolve_ai_side_hustle`), so expiry moves NO chips. For each expired
    row, oldest first:
      1. Delete the row (mirroring END_OFFGRID into the presence shadow).
      2. Apply the energy effect if HUSTLE_ENERGY_MODE == 'drain'.
      3. Return a `HustleEndResult` whose `paid_amount` echoes the amount
         granted at start, so the lobby can emit a "returned" ticker row.

    Best-effort per row: a failure on one AI is logged and skipped so one
    bad row doesn't poison the batch. `bankroll_repo` is retained only for
    the optional energy-drain side effect.
    """
    out: List[HustleEndResult] = []
    if side_hustle_repo is None:
        return out
    try:
        expired = side_hustle_repo.list_expired(sandbox_id=sandbox_id, now=now)
    except Exception as exc:
        logger.warning("[HUSTLE] list_expired failed: %s", exc)
        return out
    if not expired:
        return out

    for h in expired:
        try:
            removed = side_hustle_repo.delete(h.personality_id, sandbox_id=sandbox_id)
        except Exception as exc:
            logger.warning(
                "[HUSTLE] delete failed pid=%r: %s",
                h.personality_id,
                exc,
            )
            continue
        if not removed:
            # Row already gone (concurrent path).
            continue

        # Phase 1 dual-write SHADOW (CASH_MODE_PRESENCE_MIGRATION.md §D):
        # SIDE_HUSTLE -> IDLE via the timer-driven END_OFFGRID, mirroring the
        # authoritative ai_side_hustle_state DELETE above. Flag-gated +
        # swallows illegal transitions inside the helper.
        presence_shadow.shadow_transition(
            entity_id=ai_entity_id(h.personality_id),
            sandbox_id=sandbox_id,
            event=PresenceEvent.END_OFFGRID,
        )

        energy_applied = False
        if HUSTLE_ENERGY_MODE == 'drain':
            energy_applied = _apply_energy_drain(
                bankroll_repo=bankroll_repo,
                personality_id=h.personality_id,
                sandbox_id=sandbox_id,
            )

        amount = int(h.amount)
        out.append(
            HustleEndResult(
                personality_id=h.personality_id,
                started_at=h.started_at,
                ends_at=h.ends_at,
                target_amount=amount,
                paid_amount=amount,
                duration_bucket=h.duration_bucket,
                narration=h.narration,
                energy_applied=energy_applied,
            )
        )

    return out


# --- Internals --------------------------------------------------------------


def _commit_hustle_start(
    *,
    side_hustle_repo,
    sandbox_id: str,
    personality_id: str,
    amount: int,
    duration_bucket: str,
    narration: str,
    started_at: datetime,
    ends_at: datetime,
    deficit_ratio: float,
) -> Optional[HustleStartResult]:
    """Insert the state row. The caller credits the up-front payout separately."""
    from poker.repositories.side_hustle_state_repository import SideHustleState

    try:
        side_hustle_repo.insert_side_hustle_state(
            SideHustleState(
                personality_id=personality_id,
                sandbox_id=sandbox_id,
                started_at=started_at,
                ends_at=ends_at,
                amount=amount,
                duration_bucket=duration_bucket,
                narration=narration,
            )
        )
    except Exception as exc:
        logger.warning(
            "[HUSTLE] insert_side_hustle_state failed pid=%r: %s",
            personality_id,
            exc,
        )
        return None

    # Phase 1 dual-write SHADOW (CASH_MODE_PRESENCE_MIGRATION.md §D): mirror
    # the authoritative ai_side_hustle_state INSERT above into the Presence
    # machine. AI-only (ai_entity_id); off-grid carries no seat. Flag-gated +
    # try/except inside the helper, so it never disturbs the real write.
    # START_HUSTLE is only legal from IDLE; a broke AI that went off-grid
    # straight from being unseated may have no IDLE shadow row yet, in which
    # case the helper SWALLOWS the illegal transition — that is the expected
    # divergence this shadow phase exists to surface, not a bug to "fix".
    presence_shadow.shadow_transition(
        entity_id=ai_entity_id(personality_id),
        sandbox_id=sandbox_id,
        event=PresenceEvent.START_HUSTLE,
    )

    logger.info(
        "[HUSTLE] started pid=%r target=%d bucket=%s ends=%s",
        personality_id,
        amount,
        duration_bucket,
        ends_at.isoformat(),
    )
    return HustleStartResult(
        personality_id=personality_id,
        amount=amount,
        duration_bucket=duration_bucket,
        started_at=started_at,
        ends_at=ends_at,
        narration=narration,
        deficit_ratio=deficit_ratio,
    )


def _credit_hustle_payout(
    *,
    bankroll_repo,
    chip_ledger_repo,
    sandbox_id: str,
    personality_id: str,
    payout: int,
    started_at: datetime,
    duration_bucket: str,
    now: datetime,
) -> int:
    """Credit `payout` chips to the bankroll + record the pool draw.

    Returns the amount actually credited (0 on a load/save failure so the
    caller doesn't decrement the pool for a payout that didn't land).

    Mirrors the vice commit's bankroll-write shape but in reverse: we add
    chips rather than removing them. With passive regen retired,
    `project_bankroll` == stored, so we credit stored + payout directly
    (no regen delta to commit first). The bankroll write deliberately
    omits `chip_ledger_repo` — the explicit `record_side_hustle_earning`
    below is the paired ledger entry.
    """
    from cash_mode.bankroll import AIBankrollState
    from core.economy import ledger as chip_ledger

    try:
        stored = bankroll_repo.load_ai_bankroll(
            personality_id,
            sandbox_id=sandbox_id,
        )
    except Exception as exc:
        logger.warning(
            "[HUSTLE] load_ai_bankroll failed pid=%r: %s",
            personality_id,
            exc,
        )
        return 0
    if stored is None:
        # No bankroll row to credit into — shouldn't happen (the AI was
        # playing/idle), defensive skip.
        logger.warning(
            "[HUSTLE] no bankroll row for pid=%r; skipping payout",
            personality_id,
        )
        return 0

    new_chips = int(stored.chips) + int(payout)
    new_state = AIBankrollState(
        personality_id=personality_id,
        chips=new_chips,
        last_regen_tick=now,
    )
    # Chip-custody atomicity: commit the int credit and the `side_hustle_earning`
    # pool-draw row in ONE transaction. `conn` is None for test doubles / cross-DB,
    # falling back to the prior separate writes.
    from cash_mode.bankroll import chip_unit_of_work

    with chip_unit_of_work(bankroll_repo, ledger_repo=chip_ledger_repo) as conn:
        try:
            if conn is not None:
                bankroll_repo.save_ai_bankroll(new_state, sandbox_id=sandbox_id, conn=conn)
            else:
                bankroll_repo.save_ai_bankroll(new_state, sandbox_id=sandbox_id)
        except Exception as exc:
            logger.warning(
                "[HUSTLE] save_ai_bankroll failed pid=%r: %s",
                personality_id,
                exc,
            )
            return 0

        # Paired ledger entry — the pool draw, now in the same transaction as
        # the int credit (best-effort/swallowed, so a row failure won't roll
        # back the credit — int stays authoritative).
        if chip_ledger_repo is not None:
            chip_ledger.record_side_hustle_earning(
                chip_ledger_repo,
                personality_id=personality_id,
                amount=payout,
                context={
                    'site': 'lobby_refresh_side_hustle',
                    'duration_bucket': duration_bucket,
                    'started_at': started_at.isoformat(),
                },
                sandbox_id=sandbox_id,
                conn=conn,
            )

    logger.info(
        "[HUSTLE] paid pid=%r payout=%d",
        personality_id,
        payout,
    )
    return int(payout)


def _apply_energy_drain(
    *,
    bankroll_repo,
    personality_id: str,
    sandbox_id: str,
) -> bool:
    """Reduce the `energy` axis by HUSTLE_ENERGY_DRAIN (mode='drain').

    Returns True iff energy state was loaded, mutated, and re-persisted.
    Best-effort: any failure is logged and returns False — the hustle
    still ends, only the side effect is skipped. Mirrors the shape of
    vice's `_apply_psych_recovery` but only touches `energy` and pulls it
    *down* (the grind is tiring) rather than toward baseline.
    """
    import json

    try:
        blob = bankroll_repo.load_emotional_state_json(
            personality_id,
            sandbox_id=sandbox_id,
        )
    except Exception as exc:
        logger.warning(
            "[HUSTLE] load_emotional_state_json failed pid=%r: %s",
            personality_id,
            exc,
        )
        return False
    if not blob:
        return False
    try:
        data = json.loads(blob)
    except (TypeError, ValueError):
        return False

    axes = data.get('axes') or {}
    current_energy = float(axes.get('energy', 0.5))
    axes['energy'] = max(0.0, current_energy - HUSTLE_ENERGY_DRAIN)
    data['axes'] = axes

    try:
        bankroll_repo.save_emotional_state_json(
            personality_id,
            json.dumps(data),
            sandbox_id=sandbox_id,
        )
    except Exception as exc:
        logger.warning(
            "[HUSTLE] save_emotional_state_json failed pid=%r: %s",
            personality_id,
            exc,
        )
        return False
    return True
