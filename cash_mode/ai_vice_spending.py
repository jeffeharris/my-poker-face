"""AI vice spending — the chip sink + psych regulator for flush AIs.

Vice is a *status*, not an atomic event. When an AI is flush, they
probabilistically blow some chips on something character-appropriate
and disappear from the lobby for a character-chosen duration. On
return their psyche is partly restored (one-shot pull toward the
three dynamic axis baselines).

Two passes wire into `refresh_unseated_tables`:

  - `tick_vice_expirations` (start of refresh): expires vice rows
    whose `ends_at` has passed, applies the psych-recovery side
    effect, returns `ViceEndResult`s so the lobby can emit ticker
    rows.
  - `resolve_ai_vice_spending` (post-loop, after carry resolution):
    for each idle-pool candidate, rolls vice_prob. On a fire, makes
    the narration call (sync — duration comes back with the
    narration), inserts the state row, applies the chip move + ledger
    entry, returns `ViceStartResult`s.

Spec: `docs/plans/CASH_MODE_AI_VICE_SPENDING.md`.
"""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Set, Tuple

from cash_mode import presence_shadow
from cash_mode.presence import PresenceEvent, ai_entity_id

logger = logging.getLogger(__name__)


# --- Trigger constants ------------------------------------------------------

CONCENTRATION_FLOOR = 2.5
"""An AI must hold >= this multiple of the cast median to be eligible
for vice. With CONCENTRATION_FLOOR = 2.5, an AI at 2.5x the cast
median has excess_ratio = 0 (just at threshold); at 5x median,
excess_ratio = 2.5 (clearly above). The dominant outlier ($2M vs a
$14K median) produces a huge excess_ratio that's capped by MAX_PROB.

Replaces the prior per-personality `starting_bankroll × COMFORT_FLOOR`
gate — that produced character-by-character thresholds where a
low-baseline AI could "qualify as wealthy" while being objectively
poor compared to the cast. Concentration is cast-relative, so vice
targets actual wealth concentration rather than personal comfort.
"""

MIN_CAST_MEDIAN_FOR_VICE = 5_000
"""Suppress the vice pass entirely when the cast median is below this.
"Everyone is broke" should not produce vice — there's no real "top"
to drain in a poor cast, and a near-zero median makes the
concentration ratio meaningless (every chip is "concentrated")."""

EXCESS_WEIGHT = 0.04
"""Per-unit excess_ratio contribution to vice probability."""

PRESSURE_BOOST = 0.6
"""Multiplicative boost from pressure. A fully-pressured AI vices
~50-60% more often than a calm AI of equal wealth. Tuned against the
natural 0.4-floor pressure of an at-rest AI."""

MAX_PROB = 0.25
"""Per-refresh vice probability ceiling. Combined with the duration
buckets (15min - 4hr), this means a maximally-flush AI is off-grid a
meaningful share of the time."""


# --- Amount constants -------------------------------------------------------

BASE_FRACTION = 0.02
"""Base spend as a fraction of bankroll once vice triggers."""

EXCESS_FRACTION_WEIGHT = 0.03
"""Per-unit excess_ratio contribution to the spend fraction."""

AMOUNT_JITTER_LOW = 0.5
AMOUNT_JITTER_HIGH = 1.5
"""Multiplicative jitter so vice events aren't visually uniform."""

MIN_VICE_AMOUNT = 50
"""Below this raw amount, skip the event entirely. Backstop — by the
time vice_prob is non-trivial, raw_amount is well above 50."""

MAX_VICE_FRACTION = 0.15
"""Hard cap: never spend more than 15% of bankroll in one event."""

FLOOR_PROTECTION_FRACTION = 0.5
"""Post-vice bankroll must stay above starting_bankroll × this. With
the wealth gate (COMFORT_FLOOR = 1.2) plus the max-fraction cap,
this clause is a guardrail in case tuning shifts."""


# --- Recovery constants -----------------------------------------------------

BASE_RECOVERY = 0.25
"""Every vice end pulls each dynamic axis by at least this factor
toward its baseline. The wealthy don't heal faster *per event* — they
heal faster because they vice more often."""

AMOUNT_BONUS = 0.05
"""Bonus recovery per log10(amount/MIN_VICE_AMOUNT). A $5K vice gets
0.10 bonus over baseline; a $50K vice would get 0.15 but is capped."""

MAX_RECOVERY = 0.40
"""Hard ceiling on the recovery factor regardless of amount."""


# --- Duration constants -----------------------------------------------------

DURATION_RANGES: Dict[str, Tuple[timedelta, timedelta]] = {
    'short': (timedelta(minutes=15), timedelta(minutes=30)),
    'medium': (timedelta(minutes=30), timedelta(minutes=90)),
    'long': (timedelta(minutes=90), timedelta(minutes=240)),
}
"""Wall-time ranges per bucket. The LLM picks the bucket; the duration
is sampled uniformly within the range so two same-bucket vices don't
land on the same minute."""

DEFAULT_DURATION_BUCKET = 'medium'
"""Used when the narration callback fails or returns an unknown bucket."""


# --- Cost gates -------------------------------------------------------------

VICE_STARTS_PER_REFRESH = 2
"""How many vice STARTS can fire per refresh. Bounds LLM latency added
to the refresh path (each start is one synchronous narration call) and
ticker noise. Ends aren't capped — they're timer-driven and cheap."""


# --- Result dataclasses -----------------------------------------------------


@dataclass(frozen=True)
class ViceStartResult:
    """One vice-fire outcome.

    Returned by `resolve_ai_vice_spending` so the lobby's event-
    emission pass can format a ticker row without re-running any of
    the per-AI logic.
    """

    personality_id: str
    amount: int
    duration_bucket: str
    started_at: datetime
    ends_at: datetime
    narration: str
    excess_ratio: float
    pressure: float


@dataclass(frozen=True)
class ViceEndResult:
    """One vice-expiry outcome.

    `recovery_applied` tells the caller whether psych recovery actually
    ran. False when the AI's psych state couldn't be loaded (no row
    in `emotional_state_json`) — the vice still ends; only the side
    benefit is skipped.
    """

    personality_id: str
    started_at: datetime
    ends_at: datetime
    amount: int
    duration_bucket: str
    narration: str
    recovery_applied: bool


@dataclass
class ViceSpendingBatch:
    """Aggregate of starts + ends from one lobby refresh."""

    starts: List[ViceStartResult] = field(default_factory=list)
    ends: List[ViceEndResult] = field(default_factory=list)


# --- Pure formulas ----------------------------------------------------------


def compute_cast_median(bankrolls: List[int]) -> int:
    """Median of a list of bankroll chip counts.

    Used by the vice mechanic to anchor the concentration trigger.
    Returns 0 when the input is empty.

    Pure / deterministic — the caller controls when the median is
    re-computed (once per lobby refresh in practice). Median is more
    robust to outliers than mean: a single $2M outlier in a cast of
    80 doesn't move the median, but pulls the mean up dramatically
    and would make the concentration gate too strict for everyone else.
    """
    if not bankrolls:
        return 0
    sorted_brs = sorted(bankrolls)
    n = len(sorted_brs)
    if n % 2 == 1:
        return int(sorted_brs[n // 2])
    return int((sorted_brs[n // 2 - 1] + sorted_brs[n // 2]) // 2)


def compute_excess_ratio(bankroll: int, cast_median: int) -> float:
    """Wealth concentration above the floor, used to drive vice_prob.

    `concentration = bankroll / cast_median` — the AI's bankroll as a
    multiple of the typical AI's. `excess_ratio = max(0, concentration
    − CONCENTRATION_FLOOR)`.

    Returns 0 when:
      - cast_median <= 0 (no reference to compare against)
      - concentration <= CONCENTRATION_FLOOR (not concentrated enough)

    Unbounded upward — the $2M outlier in an otherwise-modest cast
    produces a large excess_ratio, which then gets capped by MAX_PROB
    inside `compute_vice_probability`.
    """
    if cast_median <= 0:
        return 0.0
    concentration = bankroll / cast_median
    if concentration <= CONCENTRATION_FLOOR:
        return 0.0
    return concentration - CONCENTRATION_FLOOR


def compute_pressure(
    confidence: float,
    composure: float,
    energy: float,
) -> float:
    """Pressure = 1 − min(conf, comp, energy).

    "Whichever dynamic axis is in the worst shape drives the urge to
    indulge." Catches a drained-but-collected Hemingway (low energy),
    a confident-but-tilted Napoleon (low composure), and a shaken-
    but-poised Bezos (low confidence).

    Note: the three axes typically sit around 0.5-0.9 when nothing
    is wrong, so this naturally floors around 0.4-0.5 for a calm AI.
    `PRESSURE_BOOST` is tuned with that floor in mind.
    """
    worst = min(confidence, composure, energy)
    return max(0.0, 1.0 - worst)


def compute_vice_probability(
    excess_ratio: float,
    pressure: float,
) -> float:
    """Per-refresh vice probability.

    Wealth gates the trigger; pressure amplifies. Broke AIs never vice
    (excess_ratio = 0 → vice_prob = 0). The product caps at MAX_PROB.
    """
    if excess_ratio <= 0:
        return 0.0
    prob = excess_ratio * EXCESS_WEIGHT * (1 + pressure * PRESSURE_BOOST)
    return min(MAX_PROB, max(0.0, prob))


def compute_vice_amount(
    bankroll: int,
    starting_bankroll: int,
    excess_ratio: float,
    rng: random.Random,
) -> int:
    """Compute the chip amount for a fired vice.

    Returns 0 if the result is below `MIN_VICE_AMOUNT` (skip the
    event entirely) or if the post-vice bankroll would breach the
    floor-protection guard.

    Order of clamps:
      1. raw_amount = bankroll × spend_fraction × jitter
      2. apply MAX_VICE_FRACTION cap
      3. apply floor protection (skip if it would breach)
      4. drop to 0 if final amount < MIN_VICE_AMOUNT
    """
    if bankroll <= 0 or excess_ratio <= 0:
        return 0
    spend_fraction = BASE_FRACTION + excess_ratio * EXCESS_FRACTION_WEIGHT
    jitter = rng.uniform(AMOUNT_JITTER_LOW, AMOUNT_JITTER_HIGH)
    raw_amount = int(bankroll * spend_fraction * jitter)
    cap = int(bankroll * MAX_VICE_FRACTION)
    amount = min(raw_amount, cap)
    floor_protection = int(starting_bankroll * FLOOR_PROTECTION_FRACTION)
    if bankroll - amount < floor_protection:
        # Would strand the AI below half-starting. Skip rather than
        # clamp — clamping could produce sub-MIN amounts that read as
        # noise on the ticker.
        return 0
    if amount < MIN_VICE_AMOUNT:
        return 0
    return amount


def compute_recovery_factor(amount: int) -> float:
    """Logarithmic recovery scaling — money buys some happiness but not unbounded.

    A $50 vice yields BASE_RECOVERY (0.25); a $5,000 vice yields ~0.35;
    a $50,000+ vice caps at MAX_RECOVERY (0.40). The wealthy recover
    faster only because they vice more often, not because each event
    is more powerful.
    """
    if amount <= 0:
        return 0.0
    safe = max(MIN_VICE_AMOUNT, amount)
    bonus = AMOUNT_BONUS * math.log10(safe / MIN_VICE_AMOUNT)
    return min(MAX_RECOVERY, BASE_RECOVERY + bonus)


def compute_recovered_axes(
    confidence: float,
    composure: float,
    energy: float,
    baseline_confidence: float,
    baseline_composure: float,
    baseline_energy: float,
    recovery_factor: float,
) -> Tuple[float, float, float]:
    """One-shot pull-toward-baseline on the three dynamic axes.

    Returns `(new_conf, new_comp, new_energy)`. Pure; caller handles
    persistence. Anchors are immutable identity and NOT modified here.

    The pull is symmetric — same factor regardless of whether the axis
    is above or below baseline. Distinct from `PlayerPsychology.recover`,
    which is asymmetric (stickier below baseline). The two stack
    cleanly: per-hand recover() handles drift, vice end adds an extra
    pull at the moment the AI returns from indulgence.
    """
    if recovery_factor <= 0:
        return confidence, composure, energy
    return (
        _clamp01(confidence + (baseline_confidence - confidence) * recovery_factor),
        _clamp01(composure + (baseline_composure - composure) * recovery_factor),
        _clamp01(energy + (baseline_energy - energy) * recovery_factor),
    )


def duration_for_bucket(bucket: str, rng: random.Random) -> timedelta:
    """Sample a wall-time delta from the bucket's range.

    Falls back to `DEFAULT_DURATION_BUCKET` when the bucket is unknown
    (defensive against malformed LLM responses).
    """
    if bucket not in DURATION_RANGES:
        bucket = DEFAULT_DURATION_BUCKET
    low, high = DURATION_RANGES[bucket]
    span_s = (high - low).total_seconds()
    delta_s = low.total_seconds() + rng.random() * span_s
    return timedelta(seconds=delta_s)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# --- Narration callback type ------------------------------------------------


NarrateFn = Callable[[str, int, Optional[Dict]], Tuple[str, str]]
"""Signature: (personality_id, amount, psychology_snapshot) ->
(narration, duration_bucket). The bucket is one of 'short' /
'medium' / 'long'.

In Commit 1 the dispatcher uses `_templated_narrate_fn` as a stub.
Commit 3 plugs in the real LLM-backed narrator.
"""


def _templated_narrate_fn(
    personality_id: str,
    amount: int,
    psychology_snapshot: Optional[Dict],
) -> Tuple[str, str]:
    """Fallback narrator used by Commit 1 and by Commit 3 on LLM failure.

    Produces a plain templated line and always picks the medium bucket.
    """
    return (
        f"{personality_id} stepped out to spend ${amount:,} on something",
        DEFAULT_DURATION_BUCKET,
    )


# --- Psych helpers ----------------------------------------------------------


def _load_psych_snapshot(
    *,
    bankroll_repo,
    personality_id: str,
    sandbox_id: str,
) -> Optional[Dict[str, float]]:
    """Return `{confidence, composure, energy}` for an AI, or None.

    Reads the persisted `emotional_state_json` blob and pulls the
    three dynamic axes out. Returns None when the blob is missing or
    unparseable — the caller skips the AI rather than crashing.
    """
    try:
        blob = bankroll_repo.load_emotional_state_json(
            personality_id,
            sandbox_id=sandbox_id,
        )
    except Exception as exc:
        logger.warning(
            "[VICE] load_emotional_state_json failed pid=%r: %s",
            personality_id,
            exc,
        )
        return None
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "[VICE] emotional_state_json malformed pid=%r: %s",
            personality_id,
            exc,
        )
        return None
    # The blob structure is `PlayerPsychology.to_dict()` — axes live
    # under the top-level `axes` key. Defensive against absence.
    axes = data.get('axes') or {}
    return {
        'confidence': float(axes.get('confidence', 0.5)),
        'composure': float(axes.get('composure', 0.7)),
        'energy': float(axes.get('energy', 0.5)),
    }


def _load_anchors_dict(
    *,
    personality_repo,
    personality_id: str,
) -> Optional[Dict[str, float]]:
    """Return the personality's anchor dict, or None on failure.

    Used at vice-end to compute baselines for the recovery pull.
    Reads from `personalities.config_json['anchors']` — the same
    source `PlayerPsychology.from_dict` uses.
    """
    try:
        config = personality_repo.load_personality_by_id(personality_id)
    except Exception as exc:
        logger.warning(
            "[VICE] load_personality_by_id failed pid=%r: %s",
            personality_id,
            exc,
        )
        return None
    if not isinstance(config, dict):
        return None
    anchors_blob = config.get('anchors')
    if not isinstance(anchors_blob, dict):
        return None
    return {k: float(v) for k, v in anchors_blob.items() if isinstance(v, int | float)}


def _apply_psych_recovery(
    *,
    bankroll_repo,
    personality_repo,
    personality_id: str,
    sandbox_id: str,
    amount: int,
) -> bool:
    """Pull the three dynamic axes toward their baselines.

    Returns True iff psych state was loaded, mutated, and re-persisted.
    Returns False (cleanly) if no psych state exists for the AI — the
    vice still ends; only the side benefit is skipped.

    Stays defensive: any exception in the persistence path is swallowed
    with a log, so a vice's economic resolution is never blocked by a
    psych failure.
    """
    try:
        from poker.psychology_model import (
            PersonalityAnchors,
            compute_baseline_composure,
            compute_baseline_confidence,
        )
    except ImportError as exc:
        logger.warning("[VICE] psychology_model import failed: %s", exc)
        return False

    blob = None
    try:
        blob = bankroll_repo.load_emotional_state_json(
            personality_id,
            sandbox_id=sandbox_id,
        )
    except Exception as exc:
        logger.warning(
            "[VICE] load_emotional_state_json failed pid=%r: %s",
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
    current_conf = float(axes.get('confidence', 0.5))
    current_comp = float(axes.get('composure', 0.7))
    current_energy = float(axes.get('energy', 0.5))

    # Anchors: prefer the personality config; fall back to defaults
    # baked into PersonalityAnchors so the recovery still does
    # something even if the personality_repo lookup fails.
    anchors_dict = _load_anchors_dict(
        personality_repo=personality_repo,
        personality_id=personality_id,
    )
    if anchors_dict is not None:
        try:
            anchors = PersonalityAnchors.from_dict(anchors_dict)
        except Exception:
            anchors = PersonalityAnchors.from_dict({})
    else:
        anchors = PersonalityAnchors.from_dict({})

    baseline_conf = compute_baseline_confidence(anchors)
    baseline_comp = compute_baseline_composure(anchors)
    baseline_energy = anchors.baseline_energy

    factor = compute_recovery_factor(amount)
    new_conf, new_comp, new_energy = compute_recovered_axes(
        current_conf,
        current_comp,
        current_energy,
        baseline_conf,
        baseline_comp,
        baseline_energy,
        factor,
    )

    # Mutate just the axes; preserve everything else in the blob.
    axes['confidence'] = new_conf
    axes['composure'] = new_comp
    axes['energy'] = new_energy
    data['axes'] = axes

    try:
        bankroll_repo.save_emotional_state_json(
            personality_id,
            json.dumps(data),
            sandbox_id=sandbox_id,
        )
    except Exception as exc:
        logger.warning(
            "[VICE] save_emotional_state_json failed pid=%r: %s",
            personality_id,
            exc,
        )
        return False
    return True


# --- Public entry points ----------------------------------------------------


def tick_vice_expirations(
    *,
    vice_repo,
    bankroll_repo,
    personality_repo,
    sandbox_id: str,
    now: datetime,
) -> List[ViceEndResult]:
    """Expire vice rows whose `ends_at <= now`.

    For each expired row:
      1. Apply the one-shot psych recovery (best-effort)
      2. Delete the vice row
      3. Return a `ViceEndResult` so the lobby can emit a ticker event

    Best-effort: failures on individual AIs are logged and skipped so
    one bad row doesn't poison the rest of the batch.
    """
    out: List[ViceEndResult] = []
    if vice_repo is None:
        return out
    try:
        expired = vice_repo.list_expired(sandbox_id=sandbox_id, now=now)
    except Exception as exc:
        logger.warning("[VICE] list_expired failed: %s", exc)
        return out

    for v in expired:
        recovery_applied = False
        try:
            recovery_applied = _apply_psych_recovery(
                bankroll_repo=bankroll_repo,
                personality_repo=personality_repo,
                personality_id=v.personality_id,
                sandbox_id=sandbox_id,
                amount=v.amount,
            )
        except Exception as exc:
            logger.warning(
                "[VICE] recovery failed pid=%r: %s",
                v.personality_id,
                exc,
            )

        try:
            vice_repo.delete(v.personality_id, sandbox_id=sandbox_id)
        except Exception as exc:
            logger.warning(
                "[VICE] delete failed pid=%r: %s",
                v.personality_id,
                exc,
            )
            # If we couldn't delete, don't emit the end event — better
            # to retry next refresh than to mislead the player.
            continue

        # Phase 1 dual-write SHADOW (CASH_MODE_PRESENCE_MIGRATION.md §E):
        # VICE -> IDLE via the timer-driven END_OFFGRID, mirroring the
        # authoritative ai_vice_state DELETE above. Flag-gated + swallows
        # illegal transitions inside the helper.
        presence_shadow.shadow_transition(
            entity_id=ai_entity_id(v.personality_id),
            sandbox_id=sandbox_id,
            event=PresenceEvent.END_OFFGRID,
        )

        out.append(
            ViceEndResult(
                personality_id=v.personality_id,
                started_at=v.started_at,
                ends_at=v.ends_at,
                amount=v.amount,
                duration_bucket=v.duration_bucket,
                narration=v.narration,
                recovery_applied=recovery_applied,
            )
        )

    return out


def reserve_vice_multiplier(ratio: float) -> float:
    """Scale vice intensity by the bank-pool reserve ratio (reserve-aware refill).

    Vice is the refill that funds the next Main Event, and it tapers full→off
    across `RESERVE_HEALTHY → RESERVE_VICE_CEILING`. The CEILING sits ABOVE the
    trigger, so vice is still ~half-on AT the trigger and pushes reserves ACROSS
    it (rather than asymptoting at it — the prior bug, where vice hit 0 right at
    the trigger and the last sliver of climb stalled on the weak base rake). Above
    the trigger vice keeps easing → a BRAKE when the bank runs hot. `ratio` is
    reserves/holdings:
      * 1.0 at/below `RESERVE_HEALTHY` — bank well short of a tournament, refill hard,
      * ~0.5 at `RESERVE_TRIGGER` (default ceiling 0.18) — still pushing across,
      * 0.0 at/above `RESERVE_VICE_CEILING` — bank hot, vice fully off (braked),
      * a linear taper between.

    (Vice ALSO self-targets the wealthy via the concentration gate, so it does the
    most work when a few AIs are running away — the de-concentration instrument.
    The complementary even-skim instrument for a flat field is the rake.)

    Band edges come from the shared canonical ladder in `economy_signal`. Pure;
    the caller decides whether the gate is active (`VICE_RESERVE_GATED`).
    """
    from core.economy.economy_signal import RESERVE_HEALTHY, RESERVE_VICE_CEILING

    full = RESERVE_HEALTHY  # at/below → refill at full intensity
    off = RESERVE_VICE_CEILING  # at/above → bank hot, vice fully off (braked)
    if ratio >= off:
        return 0.0
    if ratio <= full:
        return 1.0
    span = off - full
    if span <= 0:
        return 1.0
    return (off - ratio) / span


def resolve_ai_vice_spending(
    *,
    candidates: Set[str],
    vice_repo,
    bankroll_repo,
    chip_ledger_repo,
    sandbox_id: str,
    rng: random.Random,
    now: datetime,
    narrate_fn: Optional[NarrateFn] = None,
    max_starts: int = VICE_STARTS_PER_REFRESH,
    concentration_floor: float = CONCENTRATION_FLOOR,
    field_snapshot=None,
) -> List[ViceStartResult]:
    """Roll vice for each candidate; fire up to `max_starts` this refresh.

    `candidates` is the idle-pool AI set minus any AIs already on a
    vice (the lobby builds this set before calling). The dispatcher:

      0. Loads ALL cast bankrolls once, computes cast median. If the
         median is below MIN_CAST_MEDIAN_FOR_VICE, the whole pass
         short-circuits (no point draining "the rich" when nobody is).
      1. Per candidate, loads bankroll + psych snapshot + computes
         vice_prob using the concentration-based excess_ratio. Skips
         candidates with probability 0 or that fail the rng roll.
      2. Computes the amount; skip if it falls below MIN_VICE_AMOUNT
         or breaches floor protection (compute_vice_amount returns 0).
      3. Sorts the surviving fires by amount DESC, takes the top
         `max_starts`.
      4. For each fire: calls `narrate_fn` (sync — duration bucket
         comes back here), inserts vice state row, debits bankroll,
         records ledger entry.

    `concentration_floor` is exposed as a kwarg so tests can override
    the gate independently of the module-level default.

    Returns `ViceStartResult`s in the order they were committed.
    """
    if not candidates or vice_repo is None or bankroll_repo is None:
        return []
    if narrate_fn is None:
        narrate_fn = _templated_narrate_fn

    # Phase 0: establish the cast median wealth — the reference each
    # candidate's concentration is measured against. In field-liquid mode
    # this is the median of LIQUID net worth (bankroll + seat) across the
    # field (passed in as a precomputed snapshot, so seated AIs count
    # their stacks); otherwise it's the legacy median of off-table
    # bankrolls only. Either way the per-candidate logic below is
    # identical — an idle candidate's `current` IS its liquid wealth.
    if field_snapshot is not None:
        from cash_mode import economy_flags as _eflags

        cast_median = field_snapshot.median()
        min_median = _eflags.MIN_FIELD_MEDIAN_FOR_VICE
    else:
        try:
            cast_chips = bankroll_repo.list_all_ai_bankroll_chips(
                sandbox_id=sandbox_id,
            )
        except Exception as exc:
            logger.warning("[VICE] list_all_ai_bankroll_chips failed: %s", exc)
            return []
        cast_median = compute_cast_median(cast_chips)
        min_median = MIN_CAST_MEDIAN_FOR_VICE
    if cast_median < min_median:
        # Cast is too poor for the "drain the wealthy" framing to make
        # sense. Skip the pass entirely; AIs grind back to wealth via
        # the normal regen/play loop and vice resumes when the median
        # crosses the floor.
        return []

    # Reserve-aware intensity (flag-gated, OFF by default): vice is a refill
    # faucet, so scale the whole pass by the bank-pool deficit — a flush bank
    # needs no refill and stops taxing the field; as reserves fall, vice ramps
    # back up. When the gate is off, vice_mult stays 1.0 (current behaviour).
    from cash_mode import economy_flags as _eflags

    vice_mult = 1.0
    if _eflags.VICE_RESERVE_GATED and chip_ledger_repo is not None:
        try:
            from core.economy.economy_signal import signal

            state = signal(chip_ledger_repo, sandbox_id=sandbox_id)
            vice_mult = reserve_vice_multiplier(state.ratio)
        except Exception as exc:
            logger.warning("[VICE] reserve gate failed: %s; running ungated", exc)
            vice_mult = 1.0
        if vice_mult <= 0.0:
            # Reserves healthy — nothing to refill, so the whole pass is off.
            return []

    # Phase 1: collect candidates that pass the probability roll, with
    # their computed amounts. Phase 2 picks top-N and commits.
    pending: List[Tuple[str, int, float, float, Optional[Dict[str, float]]]] = []
    for pid in candidates:
        try:
            current = bankroll_repo.load_ai_bankroll_current(
                pid,
                sandbox_id=sandbox_id,
                now=now,
            )
        except Exception as exc:
            logger.warning(
                "[VICE] load_ai_bankroll_current failed pid=%r: %s",
                pid,
                exc,
            )
            continue
        if current is None or current <= 0:
            continue

        try:
            knobs = bankroll_repo.load_personality_knobs(pid)
        except Exception as exc:
            logger.warning(
                "[VICE] load_personality_knobs failed pid=%r: %s",
                pid,
                exc,
            )
            continue

        # Concentration gate (with optional test override).
        if concentration_floor != CONCENTRATION_FLOOR:
            concentration = current / cast_median if cast_median > 0 else 0.0
            excess = max(0.0, concentration - concentration_floor)
        else:
            excess = compute_excess_ratio(current, cast_median)
        if excess <= 0:
            continue

        psych = _load_psych_snapshot(
            bankroll_repo=bankroll_repo,
            personality_id=pid,
            sandbox_id=sandbox_id,
        )
        if psych is None:
            # No psych state yet — use neutral floor (calm AI).
            pressure = compute_pressure(0.7, 0.7, 0.7)
        else:
            pressure = compute_pressure(
                psych['confidence'],
                psych['composure'],
                psych['energy'],
            )

        prob = compute_vice_probability(excess, pressure) * vice_mult
        if prob <= 0:
            continue
        if rng.random() >= prob:
            continue

        amount = compute_vice_amount(
            current,
            knobs.starting_bankroll,
            excess,
            rng,
        )
        if amount <= 0:
            continue

        pending.append((pid, amount, excess, pressure, psych))

    if not pending:
        return []

    # Phase 2: sort by amount DESC, take top N. The biggest spenders
    # get the visible/narrated treatment — small fires are quiet.
    pending.sort(key=lambda t: t[1], reverse=True)
    selected = pending[:max_starts]

    out: List[ViceStartResult] = []
    for pid, amount, excess, pressure, psych in selected:
        # Synchronous narration — duration comes back with the line.
        try:
            narration, duration_bucket = narrate_fn(pid, amount, psych)
        except Exception as exc:
            logger.warning(
                "[VICE] narrate_fn failed pid=%r: %s; using fallback",
                pid,
                exc,
            )
            narration, duration_bucket = _templated_narrate_fn(pid, amount, psych)
        if duration_bucket not in DURATION_RANGES:
            duration_bucket = DEFAULT_DURATION_BUCKET

        delta = duration_for_bucket(duration_bucket, rng)
        ends_at = now + delta

        committed = _commit_vice_start(
            bankroll_repo=bankroll_repo,
            chip_ledger_repo=chip_ledger_repo,
            vice_repo=vice_repo,
            sandbox_id=sandbox_id,
            personality_id=pid,
            amount=amount,
            duration_bucket=duration_bucket,
            narration=narration,
            started_at=now,
            ends_at=ends_at,
            excess_ratio=excess,
            pressure=pressure,
        )
        if committed:
            out.append(committed)
    return out


def commit_leave_vice(
    *,
    personality_id: str,
    cast_median: int,
    vice_repo,
    bankroll_repo,
    chip_ledger_repo,
    sandbox_id: str,
    rng: random.Random,
    now: datetime,
    narrate_fn: Optional[NarrateFn] = None,
) -> Optional[ViceStartResult]:
    """Commit a vice for an AI whose LEAVE was intercepted by a vice roll.

    The counterpart to `resolve_ai_vice_spending` for the seated→leave
    path (`refresh_table_roster`'s `go_vice`). The probability roll already
    happened at the leave, and there is no `max_starts` cap here — this
    only *sizes* the spend (same `compute_vice_amount` as the idle path),
    narrates, and commits (debit bankroll → bank pool via
    `_commit_vice_start`).

    The caller must have already applied the seat's `from_seat` bankroll
    credit, so `load_ai_bankroll_current` reflects the whole bankroll. As
    a guard it re-derives `excess_ratio` against the live `cast_median`
    and skips (returns None) if the AI isn't actually above the
    concentration floor, or if the sized amount rounds out / persistence
    fails.
    """
    if vice_repo is None or bankroll_repo is None:
        return None
    if narrate_fn is None:
        narrate_fn = _templated_narrate_fn

    try:
        current = bankroll_repo.load_ai_bankroll_current(
            personality_id,
            sandbox_id=sandbox_id,
            now=now,
        )
    except Exception as exc:
        logger.warning(
            "[VICE] leave-vice load_ai_bankroll_current failed pid=%r: %s",
            personality_id,
            exc,
        )
        return None
    if current is None or current <= 0:
        return None

    try:
        knobs = bankroll_repo.load_personality_knobs(personality_id)
    except Exception as exc:
        logger.warning(
            "[VICE] leave-vice load_personality_knobs failed pid=%r: %s",
            personality_id,
            exc,
        )
        return None

    excess = compute_excess_ratio(current, cast_median)
    if excess <= 0:
        return None

    psych = _load_psych_snapshot(
        bankroll_repo=bankroll_repo,
        personality_id=personality_id,
        sandbox_id=sandbox_id,
    )
    if psych is None:
        pressure = compute_pressure(0.7, 0.7, 0.7)
    else:
        pressure = compute_pressure(
            psych['confidence'],
            psych['composure'],
            psych['energy'],
        )

    amount = compute_vice_amount(current, knobs.starting_bankroll, excess, rng)
    if amount <= 0:
        return None

    try:
        narration, duration_bucket = narrate_fn(personality_id, amount, psych)
    except Exception as exc:
        logger.warning(
            "[VICE] leave-vice narrate_fn failed pid=%r: %s; using fallback",
            personality_id,
            exc,
        )
        narration, duration_bucket = _templated_narrate_fn(personality_id, amount, psych)
    if duration_bucket not in DURATION_RANGES:
        duration_bucket = DEFAULT_DURATION_BUCKET

    ends_at = now + duration_for_bucket(duration_bucket, rng)
    return _commit_vice_start(
        bankroll_repo=bankroll_repo,
        chip_ledger_repo=chip_ledger_repo,
        vice_repo=vice_repo,
        sandbox_id=sandbox_id,
        personality_id=personality_id,
        amount=amount,
        duration_bucket=duration_bucket,
        narration=narration,
        started_at=now,
        ends_at=ends_at,
        excess_ratio=excess,
        pressure=pressure,
    )


# --- Internals --------------------------------------------------------------


def _commit_vice_start(
    *,
    bankroll_repo,
    chip_ledger_repo,
    vice_repo,
    sandbox_id: str,
    personality_id: str,
    amount: int,
    duration_bucket: str,
    narration: str,
    started_at: datetime,
    ends_at: datetime,
    excess_ratio: float,
    pressure: float,
) -> Optional[ViceStartResult]:
    """Apply the chip move + ledger entry + state row in one shot.

    Mirrors the chip-flow pattern in `ai_carry_resolution.try_ai_voluntary_payoff`:
    load stored state, project forward, fire `ai_regen` for the delta,
    debit the vice amount, write the new state. Then fire
    `vice_spending` for the destruction side.

    Returns the `ViceStartResult` on success, None on persistence failure.
    """
    from cash_mode.bankroll import AIBankrollState, project_bankroll
    from core.economy import ledger as chip_ledger

    try:
        stored = bankroll_repo.load_ai_bankroll(
            personality_id,
            sandbox_id=sandbox_id,
        )
    except Exception as exc:
        logger.warning(
            "[VICE] load_ai_bankroll failed pid=%r: %s",
            personality_id,
            exc,
        )
        return None
    if stored is None:
        # No row yet — can't debit from nothing. The seed path should
        # have created one for every eligible AI; defensive skip.
        return None

    try:
        knobs = bankroll_repo.load_personality_knobs(personality_id)
    except Exception as exc:
        logger.warning(
            "[VICE] load_personality_knobs failed pid=%r: %s",
            personality_id,
            exc,
        )
        return None

    projected = project_bankroll(
        stored,
        knobs.starting_bankroll,
        knobs.bankroll_rate,
        started_at,
    )
    # Defensive double-check: the candidate-set roll already ran the
    # floor-protection guard, but bankroll may have changed since
    # (concurrent sim hand, race with another path). Skip if so.
    floor_protection = int(knobs.starting_bankroll * FLOOR_PROTECTION_FRACTION)
    if projected - amount < floor_protection:
        return None

    # Record the regen creation BEFORE the debit so the audit reads
    # the right order. Same shape as ai_carry_resolution.py:354.
    # The floor-protection guard above guarantees projected - amount >=
    # floor_protection >= 0, so this never goes negative. PRH-16: the old
    # `max(0, projected - amount)` clamp was mint-shaped — if that guard ever
    # drifted it would zero the bankroll while the ledger still recorded the
    # full `amount` destroyed, leaving (amount - projected) chips untracked.
    # Subtract directly so any future regression surfaces as a negative
    # bankroll the audit flags, not a silent mint.
    new_chips = projected - amount
    new_state = AIBankrollState(
        personality_id=personality_id,
        chips=new_chips,
        last_regen_tick=started_at,
    )
    # Chip-custody atomicity: the pending-regen creation, the int debit, and
    # the `vice_spending` destruction commit in ONE transaction. The separate
    # vice_state row (below) is intentionally NOT in this txn — it's a different
    # repo and we'd rather have a phantom debit than a vice with no expiry.
    # `conn` is None for test doubles / cross-DB → prior separate writes.
    from cash_mode.bankroll import chip_unit_of_work

    with chip_unit_of_work(bankroll_repo, ledger_repo=chip_ledger_repo) as conn:
        if chip_ledger_repo is not None and projected > stored.chips:
            chip_ledger.record_ai_regen(
                chip_ledger_repo,
                personality_id=personality_id,
                stored_chips=stored.chips,
                projected_chips=projected,
                context={
                    'site': 'lobby_refresh_vice',
                    'sandbox_id': sandbox_id,
                },
                sandbox_id=sandbox_id,
                conn=conn,
            )
        try:
            if conn is not None:
                bankroll_repo.save_ai_bankroll(new_state, sandbox_id=sandbox_id, conn=conn)
            else:
                bankroll_repo.save_ai_bankroll(new_state, sandbox_id=sandbox_id)
        except Exception as exc:
            logger.warning(
                "[VICE] save_ai_bankroll failed pid=%r: %s",
                personality_id,
                exc,
            )
            return None

        # Destruction-side ledger entry, now in the same transaction as the int
        # debit (best-effort/swallowed — int stays authoritative).
        if chip_ledger_repo is not None:
            chip_ledger.record_vice_spending(
                chip_ledger_repo,
                personality_id=personality_id,
                amount=amount,
                context={
                    'site': 'lobby_refresh_vice',
                    'excess_ratio': round(excess_ratio, 3),
                    'pressure': round(pressure, 3),
                    'duration_bucket': duration_bucket,
                },
                sandbox_id=sandbox_id,
                conn=conn,
            )

    # Finally, the vice state row. If the insert fails the chip move
    # is already committed — we'd rather have a phantom debit than
    # a vice with no expiry timer. Log + continue.
    from poker.repositories.vice_state_repository import ViceState

    try:
        vice_repo.insert_vice_state(
            ViceState(
                personality_id=personality_id,
                sandbox_id=sandbox_id,
                started_at=started_at,
                ends_at=ends_at,
                amount=amount,
                duration_bucket=duration_bucket,
                narration=narration,
            )
        )
        # Phase 1 dual-write SHADOW (CASH_MODE_PRESENCE_MIGRATION.md §E):
        # mirror the authoritative ai_vice_state INSERT into the Presence
        # machine, only on a successful insert (kept inside this try so a
        # phantom-debit/no-row case below does not also emit a shadow START).
        # AI-only; off-grid carries no seat. Flag-gated + try/except inside
        # the helper. START_VICE is only legal from IDLE; a broke AI that went
        # off-grid straight from being unseated may have no IDLE shadow row
        # yet, in which case the helper SWALLOWS the illegal transition — the
        # expected divergence this shadow phase exists to surface, not a bug.
        presence_shadow.shadow_transition(
            entity_id=ai_entity_id(personality_id),
            sandbox_id=sandbox_id,
            event=PresenceEvent.START_VICE,
        )
    except Exception as exc:
        logger.warning(
            "[VICE] insert_vice_state failed pid=%r: %s",
            personality_id,
            exc,
        )
        # The chip move stuck but no state row exists. Best-effort:
        # return the result so the ticker still surfaces the event,
        # but the AI won't be filtered out of seating. Edge case —
        # surface in logs for monitoring.

    logger.info(
        "[VICE] fired pid=%r amount=%d bucket=%s ends=%s",
        personality_id,
        amount,
        duration_bucket,
        ends_at.isoformat(),
    )

    return ViceStartResult(
        personality_id=personality_id,
        amount=amount,
        duration_bucket=duration_bucket,
        started_at=started_at,
        ends_at=ends_at,
        narration=narration,
        excess_ratio=excess_ratio,
        pressure=pressure,
    )
