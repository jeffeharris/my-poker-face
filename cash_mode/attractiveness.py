"""Cash-mode table attractiveness — pure AI-facing table scoring.

Spec: `docs/plans/CASH_MODE_TABLE_ATTRACTIVENESS.md` (v1).

An idle AI ranks candidate tables by `table_attractiveness(table, ai)`
and greedily picks the best affordable, open one (the loop *inversion*
in §2 of the spec). This module is the pure scoring core — no I/O, no
repo access, no rng, no global state. Deterministic math over scalars,
mirroring the pure-helper pattern of `cash_mode/aspiration.py` and
`cash_mode/stakes_ladder.py`. The selection loop and the data-gathering
that feeds these scalars live in the seating path
(`cash_mode/movement.py` + `cash_mode/lobby.py`).

Layering (spec §1):

    attractiveness(table, ai) =
          base_attractor(ai, table)
        × (1 + W_HUNGER · hunger(ai) · fish_present)
        × (W_FISH · fish_stacks + W_WHALE · whale_stacks + BASE_DRAW)
        − W_CROWD · other_grinders

    base_attractor(ai, table) =
          stake_fit(ai, table)
        + W_CLIMB · room_prestige(table) · wealth(ai)

`stake_fit` is the base attractor ("which stakes do I even play"); the
fish/whale draw rides on top ("which of those is juiciest"), driven by
*chips* not headcount (a fish down to 20 chips isn't worth chasing).
`room_prestige · wealth` is the only "prestige" in v1 — two numbers that
already exist (a tier rank and the AI's bankroll), pulling the rich
upward to glamorous rooms.

Occupant/social ("marquee") prestige — the spec's "Deferred to v2" layer —
is now implemented as the optional `W_MARQUEE · occ_prestige(table) ·
status_appetite(ai)` term, added into the base attractor. It is fed by the
Renown-v2 stat (`victim_percentile`, a field-relative renown percentile) and
gated by `economy_flags.PRESTIGE_SEEKING_ENABLED` at the lobby layer — this
pure module just takes the two scalars (both default 0, so the term vanishes
when the flag is off or there's no renown data). See
`docs/plans/RENOWN_V2_AI_WIRING_PLAN.md` (Stage B / B4). All constants below
are sim-tunable starting points, not calibrated values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from cash_mode.stakes_ladder import STAKES_ORDER, table_buy_in_window

# --- Draw layer (the multiplicative "how much meat is on the table") ---
# Fish/whale seat chips are normalized to *stacks* (chips ÷ table max
# buy-in) before weighting, so the term stays O(1–10) instead of
# O(chips). Whales weigh heavier than fish AND, being deeper-stacked,
# dominate the sum — exactly the spec's "a whale on 7,000 beats a fish
# on 20."
W_FISH = 1.0
W_WHALE = 2.0
BASE_DRAW = 1.0  # floor so a fishless lobby table still has positive draw
W_HUNGER = 2.0  # how hard low bankroll amplifies the fish pull
# Per-other-grinder subtractive penalty. Load-bearing: without it every
# grinder dogpiles the single juiciest table (the failure we have today,
# just *motivated* instead of blind). The sequential-greedy seating loop
# recomputes this between picks so sharks spread across fish.
W_CROWD = 0.5

# --- Base attractor: the room-prestige climb (the rich are pulled up) ---
W_CLIMB = 1.0  # strength of the rich → prestigious-room pull
ROOM_PRESTIGE_CURVE_EXP = 2.0  # >1 makes the top room stand out (squared)
# `wealth_over_tier` at which `wealth(ai)` reaches 0.5 (saturating curve).
WEALTH_KNEE = 5.0

# --- Occupant ("marquee") prestige: status-seekers chase famous tables ---
# Renown-v2 B4. A table seating high-renown players draws status-seekers; the
# bonus `W_MARQUEE · occ_prestige(table) · status_appetite(ai)` is added into
# the base attractor (so it composes with hunger / fish-draw like the climb
# term). Both inputs are field-relative renown PERCENTILES in [0,1] (persisted
# `victim_percentile`), so the term is scale-stable and degrades to 0 with no
# renown data. Gated at the lobby by `PRESTIGE_SEEKING_ENABLED`.
# strength of the occupant-prestige pull. CALIBRATED 2026-06-02 via the
# event-level probe (scripts/sim_prestige_probe.py): the marquee bonus is linear
# in W, so one instrumented run measures, per seat decision, the W at which it
# swings the pick to a higher-prestige table. Influence rate vs W (238 contested
# decisions): W=1 -> 17%, W=1.5 -> ~28%, W=2 -> 40%, saturating ~80% by W>=8.
# The "felt but not domineering" band (~15-35%) centers at W~1.5, which lifts the
# mean prestige of the chosen table from 0.24 (no marquee) to ~0.45. (The earlier
# "1.0 too weak" note was an ARTIFACT of the churn A/B's decoherence; the probe
# is the trustworthy instrument -- see RENOWN_V2_AI_WIRING_PLAN.md.) Fish-table
# starvation separately ruled out (Hetzner sweep: fish-table grinders stayed
# 108-131% of baseline at every W). Flag still default OFF.
W_MARQUEE = 1.5
P_LINEUP = 0.25  # damped weight on additional notables beyond the top occupant
# `status_appetite` is a per-AI "how much do I chase prestige" rank — a weighted
# blend of NAMED factors, deliberately extensible (add a factor + weight here).
# renown: the famous chase the famous; glory: showmen (expressive, big ego)
# chase the spotlight. Start ~50/50, sim-tune.
W_APPETITE_RENOWN = 0.5
W_APPETITE_GLORY = 0.5

# --- stake_fit shape ---
# Attractiveness lost per *tier* of distance from the AI's fit center.
# At 1.0, a table two tiers off the fit center scores ~0 stake-fit.
STAKE_FIT_TAPER = 0.5
# How many min-buy-ins of bankroll count as "comfortably rolled" for a
# tier — matches `aspiration.WEALTH_GAP_SAFE_BUY_IN_COUNT` (5 buy-ins is
# the low end of standard bankroll-management cushion).
AFFORDABLE_BAND_BUYINS = 5.0
# How far current wealth drags the fit center away from the personality's
# `stake_comfort_zone` anchor toward what they can currently afford.
# 0.0 = pure character (never drifts); 1.0 = always plays max affordable.
# 0.5 = a flush nit drifts halfway up; a crushed grinder drifts down.
ANCHOR_DRIFT = 0.5

# --- venue appeal (baseline desirability by table flavor) ---
# A multiplier on a table's *baseline* attractiveness, before the fish/whale
# draw. Casino tables are the low-rent public grind room: less appealing in
# general (< 1), so an AI prefers an equivalent lobby table when it has the
# choice — BUT the fish draw rides on top of this (a fishy casino can still
# out-pull a dead lobby table), and because the score stays positive a casino
# remains a valid fallback the greedy pass picks when nothing else is open
# (it's open to the public — anyone can always sit). Lobby/private tables sit
# at 1.0. Sim-tunable.
CASINO_VENUE_APPEAL = 0.5

# --- hunger (continuous generalization of the binary hungry-grinder gate) ---
# Today's gate is binary at bankroll < starting × 0.8
# (`closed_economy.GRINDER_HUNGER_THRESHOLD`). Generalize to a continuous
# pull: 0 at/above a full roll, ramping to 1 when desperate.
HUNGER_FULL_ROLL_RATIO = 1.0  # bankroll ≥ starting → hunger 0
HUNGER_DESPERATE_RATIO = 0.2  # bankroll ≤ 20% of starting → hunger 1

# --- dead-table push (feeds the Phase B `dead` leave-pressure term) ---
# A casino that's lost its fish is a "dead all-shark table": grinders came
# to farm fish and there are none, so push them to go find action elsewhere
# (routes to bored_move → idle → the attractiveness pull re-seats them at a
# live table). A table WITH fish is not dead; a LOBBY table is never dead
# (grinders playing each other IS the game there, not a failed feeding
# ground). This many fishless grinders at a casino → fully dead.
CASINO_DEAD_GRINDER_SCALE = 3.0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def room_prestige(stake_label: str, *, override: float | None = None) -> float:
    """Tier-derived glamour of a room, in [0, 1].

    The `$1000` Pit is a draw *because* it's the Pit. Normalizes the
    stake's index on `STAKES_ORDER` (`$2`≈0 → `$1000`≈1) and curves it so
    the top stands out (`ROOM_PRESTIGE_CURVE_EXP`, squared by default).

    `override` is the optional per-room flavor hook (spec §1: a future
    `prestige` field on `LobbyTableEntry`) — when set, it wins verbatim
    (clamped), so two same-stake rooms can differ. v1 always passes None
    → pure tier-derived. Unknown stake → 0.0.
    """
    if override is not None:
        return _clamp01(float(override))
    if stake_label not in STAKES_ORDER:
        return 0.0
    n = len(STAKES_ORDER)
    if n <= 1:
        return 0.0
    norm = STAKES_ORDER.index(stake_label) / (n - 1)
    return _clamp01(norm**ROOM_PRESTIGE_CURVE_EXP)


def wealth_over_tier(projected_bankroll: int, stake_label: str) -> float:
    """Excess wealth as multiples of a tier's *max* buy-in, ≥ 0.

    `max(0, bankroll / max_buy_in − 1)`. A 300k AI at `$50` (max 5,000)
    reads ~59 → "I'm slumming it." Drives the wealth-driven `stake_up`
    pressure and retention override in `cash_mode/movement.py` (Phase B).
    Unbounded by design — callers scale it (e.g. `W_SLUM`). Unknown stake
    or non-positive window → 0.0.
    """
    try:
        _, _, max_bi = table_buy_in_window(stake_label)
    except KeyError:
        return 0.0
    if max_bi <= 0:
        return 0.0
    return max(0.0, projected_bankroll / max_bi - 1.0)


def wealth(projected_bankroll: int) -> float:
    """Absolute richness across the whole ladder, in [0, 1].

    Distinct from `wealth_over_tier` (which is tier-relative): this is an
    AI-level property used for the room-prestige *bend*
    (`room_prestige(table) × wealth(ai)`), so a broke AI gets ~0 pull
    toward any room and a rich AI gets pulled hard toward glamorous ones.

    0 at/below the cheapest tier's min buy-in; saturating toward 1 as
    bankroll climbs past the top tier's max buy-in. Log-scaled across the
    ladder's buy-in span so each multiplicative jump in bankroll adds
    roughly equal richness.
    """
    _, lo_min, _ = table_buy_in_window(STAKES_ORDER[0])
    _, _, hi_max = table_buy_in_window(STAKES_ORDER[-1])
    if projected_bankroll <= lo_min or lo_min <= 0:
        return 0.0
    if projected_bankroll >= hi_max:
        return 1.0
    return _clamp01(
        (math.log(projected_bankroll) - math.log(lo_min)) / (math.log(hi_max) - math.log(lo_min))
    )


def _affordable_tier_index(projected_bankroll: int, buy_in_multiplier: float = 1.0) -> float:
    """Continuous position on the stake ladder the AI is comfortably rolled for.

    A tier is "comfortably affordable" at `AFFORDABLE_BAND_BUYINS ×
    min_buy_in × buy_in_multiplier`. Returns the index of the highest
    such tier plus a fractional (log-interpolated) step toward the next —
    so a stack run-up drifts the position up smoothly rather than in
    discrete jumps. Clamped to `[0, len-1]`.
    """
    reqs = [
        AFFORDABLE_BAND_BUYINS * table_buy_in_window(label)[1] * buy_in_multiplier
        for label in STAKES_ORDER
    ]
    if projected_bankroll <= reqs[0]:
        return 0.0
    for i in range(len(reqs) - 1):
        if projected_bankroll < reqs[i + 1]:
            frac = (math.log(projected_bankroll) - math.log(reqs[i])) / (
                math.log(reqs[i + 1]) - math.log(reqs[i])
            )
            return i + _clamp01(frac)
    return float(len(reqs) - 1)


def stake_fit(
    projected_bankroll: int,
    comfort_zone: str,
    stake_label: str,
    *,
    buy_in_multiplier: float = 1.0,
) -> float:
    """How well a table's stake fits this AI right now, in [0, 1].

    Peaks at the AI's *fit center* and tapers with tier-distance. The fit
    center is the personality's `stake_comfort_zone` anchor dragged
    `ANCHOR_DRIFT` of the way toward what current bankroll can comfortably
    afford (`_affordable_tier_index`). So a nit grinds low even when flush
    (character preserved), a grinder who runs up a stack drifts upward,
    and one who's crushed drops down — none abandoning character.

    Hard affordability is enforced separately by the selection loop (it
    only ranks tables the AI can actually buy into); this just shapes the
    *preference* among them. Unknown stake → 0.0; an unknown
    `comfort_zone` falls back to the affordable position as the anchor.
    """
    if stake_label not in STAKES_ORDER:
        return 0.0
    table_idx = STAKES_ORDER.index(stake_label)
    afford_idx = _affordable_tier_index(projected_bankroll, buy_in_multiplier)
    anchor_idx = (
        float(STAKES_ORDER.index(comfort_zone)) if comfort_zone in STAKES_ORDER else afford_idx
    )
    fit_center = anchor_idx + ANCHOR_DRIFT * (afford_idx - anchor_idx)
    distance = abs(table_idx - fit_center)
    return max(0.0, 1.0 - STAKE_FIT_TAPER * distance)


def hunger(projected_bankroll: int, starting_bankroll: int) -> float:
    """Continuous bankroll desperation in [0, 1] — amplifies the fish pull.

    0 at/above a full roll (bankroll ≥ `starting`), ramping linearly to 1
    when desperate (≤ `HUNGER_DESPERATE_RATIO` of starting). A flush
    grinder is mildly drawn to fish; a near-broke one is pulled hard
    toward the casino. Generalizes today's binary hungry-grinder gate.
    Non-positive `starting` → 0.0 (no signal).
    """
    if starting_bankroll <= 0:
        return 0.0
    ratio = projected_bankroll / starting_bankroll
    if ratio >= HUNGER_FULL_ROLL_RATIO:
        return 0.0
    if ratio <= HUNGER_DESPERATE_RATIO:
        return 1.0
    span = HUNGER_FULL_ROLL_RATIO - HUNGER_DESPERATE_RATIO
    return _clamp01((HUNGER_FULL_ROLL_RATIO - ratio) / span)


def table_deadness(*, is_casino: bool, has_fish: bool, grinder_count: int) -> float:
    """How "dead" a table is for the grinders sitting there, in [0, 1].

    Feeds `MovementContext.table_deadness` → the Phase B `dead` leave term.
    0 unless the table is a casino that has lost its fish (an all-shark
    feeding ground with nothing to feed on); then it rises with how many
    grinders are stuck competing over nothing, saturating at
    `CASINO_DEAD_GRINDER_SCALE`. Tables with fish, and all non-casino
    tables, return 0 (not dead).
    """
    if has_fish or not is_casino or grinder_count <= 0:
        return 0.0
    return _clamp01(grinder_count / CASINO_DEAD_GRINDER_SCALE)


def occ_prestige(occupant_percentiles: Sequence[float]) -> float:
    """Table marquee draw in [0,1] — how famous the table's occupants are.

    The single biggest name dominates; additional notables add a damped bonus
    (`P_LINEUP`), so a table with a Beloved Legend AND a rival reads as a bigger
    event than either alone. Inputs are each seated entity's persisted
    field-renown percentile (`victim_percentile`, AI or human). Empty / all-zero
    → 0 (no marquee), so the term vanishes when nobody notable is seated.
    """
    vals = sorted((p for p in occupant_percentiles if p and p > 0.0), reverse=True)
    if not vals:
        return 0.0
    return _clamp01(vals[0] + P_LINEUP * sum(vals[1:]))


def glory_appetite(*, expressiveness: float = 0.5, ego: float = 0.5) -> float:
    """The showman/status-hunger trait factor in [0,1] for `status_appetite`.

    A glory-hunter — expressive and big-egoed — chases the spotlight. Pure blend
    of two curated personality anchors; the 0.5 defaults give a neutral appetite
    when anchors are missing.
    """
    return _clamp01(0.5 * _clamp01(expressiveness) + 0.5 * _clamp01(ego))


def status_appetite(
    *,
    own_percentile: float = 0.0,
    glory: float = 0.0,
    w_renown: float = W_APPETITE_RENOWN,
    w_glory: float = W_APPETITE_GLORY,
) -> float:
    """A per-AI "how much do I chase prestige" rank in [0,1].

    A weighted blend of named factors — deliberately a small composite so more
    factors (a rivalry signal, a heater, a tilt) can be added without touching
    the call sites: just extend the blend. The two seeded factors:
      - `own_percentile` — this AI's own field-renown percentile (the famous
        chase the famous).
      - `glory` — the showman trait from :func:`glory_appetite`.
    Returns 0 when both are 0; combined with `occ_prestige` (also 0 with no
    renown), the whole marquee term then vanishes — a clean no-op default.
    """
    return _clamp01(w_renown * _clamp01(own_percentile) + w_glory * _clamp01(glory))


def base_attractor(
    *,
    projected_bankroll: int,
    comfort_zone: str,
    stake_label: str,
    buy_in_multiplier: float = 1.0,
    prestige_override: float | None = None,
) -> float:
    """`stake_fit` plus the wealth-driven room-prestige climb (spec §1).

    The climb term `W_CLIMB · room_prestige · wealth` is meaningful only
    at high tiers held by wealthy AIs: a broke AI has `wealth ≈ 0` so the
    model reduces to the plain anchor, while a rich AI gets pulled *above*
    its anchor toward glamorous rooms (where `stake_fit` alone would taper
    to ~0). Always ≥ 0.
    """
    fit = stake_fit(
        projected_bankroll,
        comfort_zone,
        stake_label,
        buy_in_multiplier=buy_in_multiplier,
    )
    climb = (
        W_CLIMB
        * room_prestige(stake_label, override=prestige_override)
        * wealth(projected_bankroll)
    )
    return fit + climb


def table_attractiveness(
    *,
    projected_bankroll: int,
    starting_bankroll: int,
    comfort_zone: str,
    stake_label: str,
    fish_chips: int,
    whale_chips: int,
    other_grinders: int,
    buy_in_multiplier: float = 1.0,
    prestige_override: float | None = None,
    venue_appeal: float = 1.0,
    marquee_prestige: float = 0.0,
    status_appetite: float = 0.0,
) -> float:
    """Full attractiveness of a table for an AI (spec §1).

    Combines the base attractor (stake-fit + climb), the hunger-amplified
    fish/whale chip draw, and the self-balancing crowd penalty. Higher is
    more attractive; the selection loop takes the `argmax` over the AI's
    affordable, open tables.

    Inputs are plain scalars the seating path gathers per (table, AI):
      - `fish_chips` / `whale_chips`: Σ seat chips of seated fish / whales
        at the table (normalized here to *stacks* via the table max buy-in
        so the term is scale-stable across stakes).
      - `other_grinders`: count of non-fish AIs already seated (the crowd).

    Can go negative for a crowded, fishless, ill-fitting table — that's
    fine for ranking. Lobby tables stay positively attractive for the rich
    via the climb term even with zero fish.
    """
    base = base_attractor(
        projected_bankroll=projected_bankroll,
        comfort_zone=comfort_zone,
        stake_label=stake_label,
        buy_in_multiplier=buy_in_multiplier,
        prestige_override=prestige_override,
    )
    # Marquee pull (Renown-v2 B4): a status-seeking AI is drawn to a table
    # seating famous players. Added into the base attractor so it rides the
    # hunger / fish-draw multipliers like the climb term. Both scalars default
    # to 0 (flag off / no renown), so this is a no-op until prestige-seeking is
    # enabled and the field has renown.
    base += W_MARQUEE * marquee_prestige * status_appetite
    # A hungry grinder is pulled harder toward *any* live bait — a fish OR
    # a whale (both are chips to be farmed). Gate on either being present.
    bait_present = 1.0 if (fish_chips > 0 or whale_chips > 0) else 0.0
    hunger_mult = 1.0 + W_HUNGER * hunger(projected_bankroll, starting_bankroll) * bait_present
    try:
        _, _, max_bi = table_buy_in_window(stake_label)
    except KeyError:
        max_bi = 0
    fish_stacks = (fish_chips / max_bi) if max_bi > 0 else 0.0
    whale_stacks = (whale_chips / max_bi) if max_bi > 0 else 0.0
    draw = W_FISH * fish_stacks + W_WHALE * whale_stacks + BASE_DRAW
    crowd = W_CROWD * max(0, other_grinders)
    # `venue_appeal` scales the baseline desirability (casino < lobby) but
    # the fish/whale draw rides on top, so a fishy casino can still out-pull
    # a dead lobby table; the score stays positive so a casino is a valid
    # fallback when it's the only open seat.
    return venue_appeal * base * hunger_mult * draw - crowd


# --- Greedy seat selection (the loop inversion, spec §2) ----------------
#
# The pure core of the AI-centric seating. Given the idle AIs that want a
# seat this tick and the tables with open seats, each seeker (in priority
# order) picks the single most attractive table it can afford and has an
# open seat at; occupancy is recomputed BETWEEN picks so `W_CROWD` spreads
# sharks across fish rather than dogpiling one table. No I/O, no rng, no
# cooldown/recovery logic — the lobby owns those impure gates and feeds the
# already-eligible candidates in via `allowed_table_ids`.

# Per-refresh probability that an idle AI goes room-hunting (replaces the
# per-seat-per-hand `live_fill_prob` Bernoulli). The lobby rolls this per
# idle AI and may scale it with the catch-up gap; sim-tunable (Phase D).
DEFAULT_SEEK_RATE = 0.35


@dataclass(frozen=True)
class SeatSeeker:
    """An idle AI looking for a seat this tick (a pre-pass candidate).

    `allowed_table_ids` is the set of tables the lobby has already cleared
    for this AI through the impure gates (per-table leave cooldown, idle
    recovery, target-stake stickiness). The greedy core adds only the pure
    affordability check (bankroll ≥ this AI's buy-in) on top.
    """

    personality_id: str
    projected_bankroll: int
    starting_bankroll: int
    comfort_zone: str
    allowed_table_ids: frozenset
    buy_in_multiplier: float = 1.0
    # Renown-v2 B4: this AI's prestige-seeking rank in [0,1] (see
    # `status_appetite`). 0 = doesn't chase marquee tables (the default, so the
    # marquee term is inert until the lobby populates it behind the flag).
    status_appetite: float = 0.0


@dataclass
class FillableTable:
    """A table with at least one open seat, scored as sharks arrive.

    `open_count` and `grinder_count` are MUTATED by `assign_seats_greedy`
    as it seats AIs, so the next seeker ranks against current occupancy.
    """

    table_id: str
    stake_label: str
    min_buy_in: int
    max_buy_in: int
    open_count: int
    grinder_count: int
    fish_chips: int = 0
    whale_chips: int = 0
    prestige_override: Optional[float] = None
    venue_appeal: float = 1.0
    # Renown-v2 B4: occupant marquee draw in [0,1] (see `occ_prestige`). 0 = no
    # notable occupants (the default, so the marquee term is inert until the
    # lobby populates it behind the flag).
    marquee_prestige: float = 0.0


def seeker_buy_in(table: FillableTable, buy_in_multiplier: float) -> int:
    """This AI's buy-in at a table (mirrors lobby `_buy_in_for`).

    `round(min_buy_in × buy_in_multiplier)`, capped at the table max.
    """
    return min(round(table.min_buy_in * buy_in_multiplier), table.max_buy_in)


def assign_seats_greedy(
    seekers: List[SeatSeeker],
    tables: dict,
) -> List[Tuple[str, str]]:
    """Sequentially seat each seeker at its most attractive affordable table.

    `seekers` is processed in order — **caller-chosen priority** (e.g. most
    desperate first). `tables` maps `table_id -> FillableTable` and is
    mutated in place (`open_count` down, `grinder_count` up) as seats fill,
    so `W_CROWD` makes later seekers spread out. Candidate tables are sorted
    by id for deterministic tie-breaking. Returns the `(personality_id,
    table_id)` assignments in seating order; AIs with no affordable, open,
    allowed table are simply omitted.
    """
    assignments: List[Tuple[str, str]] = []
    for seeker in seekers:
        best_id: Optional[str] = None
        best_score: Optional[float] = None
        for tid in sorted(seeker.allowed_table_ids):
            table = tables.get(tid)
            if table is None or table.open_count <= 0:
                continue
            if seeker.projected_bankroll < seeker_buy_in(table, seeker.buy_in_multiplier):
                continue
            score = table_attractiveness(
                projected_bankroll=seeker.projected_bankroll,
                starting_bankroll=seeker.starting_bankroll,
                comfort_zone=seeker.comfort_zone,
                stake_label=table.stake_label,
                fish_chips=table.fish_chips,
                whale_chips=table.whale_chips,
                other_grinders=table.grinder_count,
                buy_in_multiplier=seeker.buy_in_multiplier,
                prestige_override=table.prestige_override,
                venue_appeal=table.venue_appeal,
                marquee_prestige=table.marquee_prestige,
                status_appetite=seeker.status_appetite,
            )
            if best_score is None or score > best_score:
                best_score = score
                best_id = tid
        if best_id is None:
            continue
        chosen = tables[best_id]
        chosen.open_count -= 1
        chosen.grinder_count += 1
        assignments.append((seeker.personality_id, best_id))
    return assignments
