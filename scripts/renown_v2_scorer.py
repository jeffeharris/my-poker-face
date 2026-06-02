#!/usr/bin/env python3
"""Offline Renown-v2 scorer + Rung-1 synthetic-archetype probe.

This is a THROWAWAY design instrument, not production code. Renown is a
read-side projection over data the game already produces, so we can validate
the *balance* of a candidate formula entirely offline — no schema migration,
no ticker change, no hooks, no UI — before committing to the build.

Rung 1 (this file's `main`) is the cheapest gate: hand-construct a handful of
canonical entities with stipulated stats and ask two existential questions:

    1. Do the four ★ routes (grinder / whale / patron / villain) EACH reach
       "high renown"?  (the design promise — there must be >1 way up)
    2. Does a high-VOLUME bot dominate?  (the treadmill failure mode)

The formula here is the agreed Renown-v2 design:
    - UNCAPPED lifetime points ledger (no [0,1] cap), but every driver is
      CONCAVE in its input (sqrt / log1p) so progress is smooth and early,
      yet "there is always more fame" and nothing explodes.
    - The four ★ core drivers: renown-weighted scalps, time-at-#1 net worth,
      kingmaker/backing, legendary hands.
    - Volume-ish drivers (tenure, breadth, stakes mastery) are denominated in
      WALL-CLOCK, not hand-count — the anti-treadmill governor, BY DESIGN.
    - "High renown" is RELATIVE: top-N% of the field (self-scaling, AI-
      symmetric), not an absolute threshold (v1's `0.40`, which playtesting
      found too binary).
    - Scalp weighting uses the victim's renown via a single pass over the
      PREVIOUS-tick renown (last-tick proxy) — no fixed-point iteration.

If Rung 1 passes, this scorer becomes the spec for the real `compute_prestige`
v2 and the oracle for its unit tests. If the volume bot tops the board, the
formula is wrong and we learned it in an afternoon with fixtures.

Run:  python3 scripts/renown_v2_scorer.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


# ---------------------------------------------------------------------------
# Inputs — SYMMETRIC: every field is populated for a human or an AI alike.
# Field names mirror the data sources compute_prestige already reads
# (cash_pair_stats, completed sessions, inbound relationship edges) plus the
# v2 additions (scalps, time-at-#1, backing, legendary nuggets, wall-clock).
# ---------------------------------------------------------------------------


@dataclass
class RenownInputs:
    label: str

    # --- ★ scalps: {victim_id: times_busted}. Weighted by victim renown. ---
    scalps: Dict[str, int] = field(default_factory=dict)

    # --- ★ time at #1 net worth (standing) ---
    ticks_at_number_one: int = 0       # ticks spent atop the field net-worth rank
    peak_net_worth: float = 0.0        # ratchets; in chips

    # --- ★ kingmaker / backing ---
    backing_volume: float = 0.0        # total chips staked to others (lifetime)
    backing_profit: float = 0.0        # net return on backing (can be negative)

    # --- ★ legendary hands: sum of per-hand nugget weights (rare events) ---
    legendary_points: float = 0.0

    # --- volume-ish drivers (WALL-CLOCK denominated by design) ---
    wall_clock_hours: float = 0.0      # presence at the felt, wall-clock
    total_hands: int = 0               # raw hands — the TREADMILL axis (naive)
    breadth_opponents: Dict[str, int] = field(default_factory=dict)  # opp -> hands vs them

    # --- stakes mastery: {stake_label: hands_at_that_tier} ---
    stakes_hands: Dict[str, int] = field(default_factory=dict)

    # --- apex: net chips vs the whole roster (can be negative) ---
    roster_net: float = 0.0

    # --- regard inputs (orthogonal to renown; shown for the quadrant) ---
    # mean inbound (likability-0.5), (respect-0.5), heat over edges that know you
    regard_likability: float = 0.0
    regard_respect: float = 0.0
    regard_heat: float = 0.0


# ---------------------------------------------------------------------------
# Weights — the ONLY thing Rung 3 will sweep. Defaults = the v2 design point.
# ---------------------------------------------------------------------------


@dataclass
class Weights:
    # ★ core
    w_scalp: float = 4.0
    scalp_base: float = 0.3            # a nobody's scalp is worth this fraction
    scalp_quality: float = 1.0         # ...a TOP-of-field victim's scalp this much more
    #   quality = scalp_base + scalp_quality * victim_field_percentile (∈[0,1]).
    #   Percentile (NOT raw renown) keeps the term bounded now that renown is
    #   uncapped — busting a "big name" is RELATIVE fame, robust to outliers.
    w_top1: float = 0.8                # sqrt(ticks at #1)
    w_peak_worth: float = 0.6          # log1p(peak_net_worth / unit)
    worth_unit: float = 5000.0
    w_backing: float = 3.0             # log1p(volume/unit) + profit bonus
    backing_unit: float = 10000.0
    w_legendary: float = 1.5

    # volume-ish (denominated per `volume_denominator`)
    w_tenure: float = 0.5
    # Breadth & backing are FIELD-RELATIVE: contribution = w · log1p(raw/median).
    # The Rung-1 rule ("uncapped → relative") generalised — a median-relative
    # log self-scales (median entity → log1p(1)=0.69; 10× → 2.4), which both
    # compresses a volume runaway AND restores discrimination when a driver is
    # near-universal (e.g. every AI backs). Bonus: the raw/median RATIO is
    # ~denominator-robust, so the hands-denominated offline read proxies the
    # wall-clock design. Weights are larger because the log term is small.
    w_breadth: float = 9.0
    breadth_per_opp_cap_hands: float = 200.0   # concavity knee per opponent
    w_stakes: float = 0.4
    w_apex: float = 0.4
    apex_unit: float = 50000.0

    # which axis the volume drivers count. 'wallclock' = the design (anti-
    # treadmill); 'hands' = the naive counterfactual we run to PROVE the lever.
    volume_denominator: str = "wallclock"

    # relative quadrant. "High renown" = top-fraction of the field AND at least
    # `median_multiple`× the field median renown. The percentile caps HOW MANY
    # can be figures (prevents star-inflation as renown ratchets up forever);
    # the median floor is a self-scaling QUALITY bar (a tourist-heavy field
    # can't manufacture fake stars — the v1 absolute-threshold trap, avoided).
    high_renown_top_fraction: float = 0.20
    high_renown_median_multiple: float = 3.0

    # stake tiers, low->high, for stakes-mastery depth credit
    stake_order: Tuple[str, ...] = ("$2", "$10", "$50", "$200", "$1000")


# ---------------------------------------------------------------------------
# Concave accrual helpers — unbounded but diminishing ("always more, but the
# next point costs more"). This is how a driver is uncapped yet can't explode.
# ---------------------------------------------------------------------------


def _sqrt(x: float) -> float:
    return math.sqrt(max(0.0, x))


def _log1p(x: float) -> float:
    return math.log1p(max(0.0, x))


def _relative(raw: float, median: float, fallback_unit: float) -> float:
    """Field-relative concave contribution: log1p(raw / median).

    median entity → log1p(1)=0.69; 10× median → 2.4; 0.1× → 0.095. Self-scales
    to the field, so it both compresses a runaway and restores discrimination
    when the driver is near-universal. Falls back to an absolute unit only if
    the field has no positive values (median == 0)."""
    denom = median if median > 0 else fallback_unit
    return _log1p(raw / denom)


def _breadth_depth_sum(inp: "RenownInputs", w: "Weights") -> float:
    """Raw breadth depth (Σ per-opponent depth) BEFORE field-relativisation.

    Per-opponent depth is concave and denominated per `volume_denominator`, so
    you can't farm one bot. The SUM is what gets median-relativised in
    compute_components (diminishing returns on breadth itself)."""
    total = 0.0
    for hands_vs in inp.breadth_opponents.values():
        if w.volume_denominator == "wallclock":
            if inp.total_hands > 0:
                opp_hours = inp.wall_clock_hours * (hands_vs / inp.total_hands)
                total += _sqrt(opp_hours)
        else:  # 'hands' — the naive treadmill counterfactual
            total += _sqrt(min(hands_vs, w.breadth_per_opp_cap_hands))
    return total


# ---------------------------------------------------------------------------
# Per-entity component computation
# ---------------------------------------------------------------------------


@dataclass
class FieldContext:
    """Field-level aggregates needed to make drivers field-relative."""
    median_backing_volume: float = 0.0
    median_breadth_depth: float = 0.0


def compute_components(
    inp: RenownInputs,
    w: Weights,
    victim_percentile: Dict[str, float],
    fctx: "FieldContext",
) -> Dict[str, float]:
    """Return {driver_name: points}. Sum = total renown (uncapped).

    ``victim_percentile`` maps entity_id -> its field renown percentile in
    [0,1] (from the previous pass) — used to weight scalp quality WITHOUT
    referencing raw uncapped renown (which would blow up super-linearly).
    ``fctx`` carries field medians so backing/breadth are field-relative.
    """
    c: Dict[str, float] = {}

    # ★ Renown-weighted scalps. log1p per victim so you can't farm one bot;
    # quality scales with the victim's FIELD PERCENTILE (busting a big name ≫
    # a nobody), bounded to [scalp_base, scalp_base+scalp_quality].
    scalp_pts = 0.0
    for vid, count in inp.scalps.items():
        pct = victim_percentile.get(vid, 0.0)
        quality = w.scalp_base + w.scalp_quality * pct
        scalp_pts += _log1p(count) * quality
    c["scalps"] = w.w_scalp * scalp_pts

    # ★ Time at #1 + peak net worth (standing; ratchets).
    c["top1"] = w.w_top1 * _sqrt(inp.ticks_at_number_one)
    c["peak_worth"] = w.w_peak_worth * _log1p(inp.peak_net_worth / w.worth_unit)

    # ★ Kingmaker / backing — FIELD-RELATIVE volume + profit bonus (losses
    # don't pay). Relativising fixes the Rung-2 collapse where near-universal
    # AI staking made every AI ~equally "high backing".
    backing = _relative(inp.backing_volume, fctx.median_backing_volume, w.backing_unit)
    backing += 0.5 * _relative(max(0.0, inp.backing_profit),
                               fctx.median_backing_volume, w.backing_unit)
    c["backing"] = w.w_backing * backing

    # ★ Legendary nuggets (already a rare-event weighted sum; concave-light).
    c["legendary"] = w.w_legendary * _sqrt(inp.legendary_points)

    # --- volume-ish drivers ---
    tenure_input = inp.wall_clock_hours if w.volume_denominator == "wallclock" else inp.total_hands
    c["tenure"] = w.w_tenure * _sqrt(tenure_input)
    # Breadth: raw per-opponent depth sum, then FIELD-RELATIVE (diminishing
    # returns on breadth itself) — fixes the Rung-2 human runaway where one
    # high-volume entity's opponent count dwarfed the field.
    raw_breadth = _breadth_depth_sum(inp, w)
    c["breadth"] = w.w_breadth * _relative(raw_breadth, fctx.median_breadth_depth, 1.0)

    # Stakes mastery: depth at each tier, tiers weighted by their rank.
    stakes_pts = 0.0
    n = max(1, len(w.stake_order) - 1)
    for label, hands in inp.stakes_hands.items():
        try:
            rank = w.stake_order.index(label)
        except ValueError:
            continue
        tier_weight = 0.5 + (rank / n)  # higher tiers worth more per hand of depth
        if w.volume_denominator == "wallclock" and inp.total_hands > 0:
            depth = inp.wall_clock_hours * (hands / inp.total_hands)
        else:
            depth = hands
        stakes_pts += tier_weight * _sqrt(depth)
    c["stakes"] = w.w_stakes * stakes_pts

    # Apex: net-positive vs the whole roster (a winner's premium; concave).
    apex = inp.roster_net / w.apex_unit
    c["apex"] = w.w_apex * (_sqrt(apex) if apex > 0 else 0.0)

    return c


def total_renown(components: Dict[str, float]) -> float:
    return sum(components.values())


# ---------------------------------------------------------------------------
# Field scoring — two-pass for the scalp/victim-renown dependency.
# ---------------------------------------------------------------------------


def _percentile_map(renowns: Dict[str, float]) -> Dict[str, float]:
    """entity_id -> fraction of the field with strictly lower renown ∈ [0,1].

    Rank-based, so it's robust to an uncapped outlier (a 200-point villain
    and a 50-point legend both map near 1.0 — busting either is 'a big name')."""
    n = len(renowns)
    if n <= 1:
        return {eid: 0.0 for eid in renowns}
    out = {}
    vals = list(renowns.values())
    for eid, r in renowns.items():
        lower = sum(1 for v in vals if v < r)
        out[eid] = lower / (n - 1)
    return out


def _field_context(entities: Dict[str, RenownInputs], w: Weights) -> FieldContext:
    """Field medians for the relativised drivers (over POSITIVE values only —
    a field where half don't back shouldn't drag the backing median to 0)."""
    backing = [i.backing_volume for i in entities.values() if i.backing_volume > 0]
    breadth = [d for d in (_breadth_depth_sum(i, w) for i in entities.values()) if d > 0]
    return FieldContext(
        median_backing_volume=_median(backing),
        median_breadth_depth=_median(breadth),
    )


def score_field(
    entities: Dict[str, RenownInputs], w: Weights
) -> Dict[str, Dict[str, float]]:
    """Return {id: components}. Two-pass: scalps weight by last-pass victim
    field-percentile (the 'last-tick renown' proxy — NOT a fixed point)."""
    fctx = _field_context(entities, w)  # raw-input medians; pass-invariant
    victim_percentile = {eid: 0.0 for eid in entities}
    for _ in range(2):  # one refinement pass is plenty
        scored = {
            eid: compute_components(inp, w, victim_percentile, fctx)
            for eid, inp in entities.items()
        }
        renowns = {eid: total_renown(c) for eid, c in scored.items()}
        victim_percentile = _percentile_map(renowns)
    return scored


def regard_of(inp: RenownInputs) -> float:
    """Orthogonal valence axis (unchanged from v1's shape), for the quadrant."""
    return inp.regard_likability + 0.5 * inp.regard_respect - inp.regard_heat


def quadrant(renown: float, regard: float, high_cut: float) -> str:
    high = renown >= high_cut
    warm = regard >= 0.05
    if high and warm:
        return "Beloved Legend"
    if high and not warm:
        return "Infamous Villain"
    if not high and warm:
        return "Up-and-comer"
    return "Disliked Nobody"


def percentile_cut(renowns: List[float], top_fraction: float) -> float:
    """The renown value at the top_fraction boundary (relative 'high renown')."""
    if not renowns:
        return 0.0
    ordered = sorted(renowns, reverse=True)
    idx = max(0, min(len(ordered) - 1, int(round(top_fraction * len(ordered))) - 1))
    return ordered[idx]


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def high_renown_cut(renowns: List[float], w: Weights) -> float:
    """Effective 'high renown' threshold = max(top-X% boundary, k×median).

    A single number because (renown ≥ pct_cut) AND (renown ≥ floor) is just
    (renown ≥ max(pct_cut, floor)). Both inputs are field-relative, so the cut
    self-scales with the field and needs no absolute constant."""
    pct = percentile_cut(renowns, w.high_renown_top_fraction)
    floor = w.high_renown_median_multiple * _median(renowns)
    return max(pct, floor)


# ---------------------------------------------------------------------------
# Rung 1 — synthetic archetype probe
# ---------------------------------------------------------------------------


def build_archetypes() -> Dict[str, RenownInputs]:
    """Seven canonical entities. The four routes + a control + the bogey + a
    high-renown 'legend' that exists mainly to be a valuable scalp target."""

    # A pre-existing legend (beloved champion). Also the villain's prize scalp.
    legend = RenownInputs(
        label="Old Champion (legend)",
        ticks_at_number_one=400,
        peak_net_worth=900_000,
        legendary_points=6.0,
        wall_clock_hours=120,
        total_hands=30_000,
        stakes_hands={"$200": 8_000, "$1000": 6_000},
        roster_net=600_000,
        breadth_opponents={f"ai{i}": 400 for i in range(20)},
        regard_likability=0.30, regard_respect=0.35, regard_heat=0.02,
    )

    # ROUTE 1 — Grinder: the dedicated regular everyone knows. Volume is the
    # deliberately-WEAKEST route (anti-treadmill), so reaching figure status
    # demands genuine commitment — lots of wall-clock, the broadest network,
    # and a modest winning record — not a casual session count.
    grinder = RenownInputs(
        label="Grinder",
        wall_clock_hours=340,
        total_hands=70_000,
        stakes_hands={"$2": 45_000, "$10": 25_000},
        breadth_opponents={f"ai{i}": 300 for i in range(48)},
        roster_net=90_000,
        regard_likability=0.10, regard_respect=0.15, regard_heat=0.0,
    )

    # ROUTE 2 — Whale: huge net worth, time at #1, high stakes, few hands.
    whale = RenownInputs(
        label="Whale",
        ticks_at_number_one=300,
        peak_net_worth=1_500_000,
        wall_clock_hours=40,
        total_hands=6_000,
        stakes_hands={"$1000": 5_000, "$200": 1_000},
        breadth_opponents={f"ai{i}": 200 for i in range(8)},
        roster_net=500_000,
        scalps={"ai_nobody1": 3, "ai_nobody2": 2},
        backing_volume=40_000,  # whales dabble in backing, not their route
        regard_likability=0.05, regard_respect=0.20, regard_heat=0.05,
    )

    # ROUTE 3 — Patron: big backing volume + profit, modest own play.
    patron = RenownInputs(
        label="Patron",
        backing_volume=600_000,
        backing_profit=120_000,
        wall_clock_hours=50,
        total_hands=9_000,
        stakes_hands={"$50": 6_000, "$200": 3_000},
        breadth_opponents={f"ai{i}": 150 for i in range(12)},
        roster_net=60_000,
        regard_likability=0.25, regard_respect=0.20, regard_heat=0.0,
    )

    # ROUTE 4 — Villain: many HIGH-VALUE scalps (busts the legend + whale),
    # cruel legendary coolers, high heat (renown high, regard negative).
    villain = RenownInputs(
        label="Villain",
        scalps={"Old Champion (legend)": 4, "Whale": 3,
                "ai_nobody1": 5, "ai_nobody2": 6},
        legendary_points=5.0,
        ticks_at_number_one=80,
        peak_net_worth=700_000,
        wall_clock_hours=70,
        total_hands=14_000,
        stakes_hands={"$200": 7_000, "$1000": 4_000},
        breadth_opponents={f"ai{i}": 220 for i in range(15)},
        roster_net=300_000,
        backing_volume=25_000,  # villains stake a little too
        regard_likability=-0.30, regard_respect=0.25, regard_heat=0.55,
    )

    # CONTROL — Up-and-comer: modest everything, SHOULD be low renown.
    upcomer = RenownInputs(
        label="Up-and-comer",
        wall_clock_hours=20,
        total_hands=3_000,
        stakes_hands={"$2": 2_000, "$10": 1_000},
        breadth_opponents={f"ai{i}": 80 for i in range(6)},
        roster_net=5_000,
        regard_likability=0.08, regard_respect=0.05, regard_heat=0.0,
    )

    # THE BOGEY — Fast bot: astronomical hand count in LITTLE wall-clock,
    # otherwise mediocre. Breaks even, no scalps/backing/legendary. Under a
    # hand-count formula this SHOULD dominate; under wall-clock it must NOT.
    fast_bot = RenownInputs(
        label="Fast bot (volume bogey)",
        wall_clock_hours=12,                 # barely present in wall-clock
        total_hands=600_000,                 # but plays forever
        stakes_hands={"$2": 400_000, "$10": 200_000},
        breadth_opponents={f"ai{i}": 6_000 for i in range(40)},
        roster_net=8_000,                    # break-even
        regard_likability=0.02, regard_respect=0.02, regard_heat=0.0,
    )

    field = {
        "Old Champion (legend)": legend,
        "Grinder": grinder,
        "Whale": whale,
        "Patron": patron,
        "Villain": villain,
        "Up-and-comer": upcomer,
        "Fast bot (volume bogey)": fast_bot,
    }

    # --- Realistic filler: the real field is ~106 mostly-low-renown AIs, so a
    # percentile cut is only meaningful against a populated tail. Deterministic
    # spread (no RNG): a handful of mid regulars + a long low tourist tail. The
    # up-and-comer should sit ABOVE the tail but BELOW the top cut. ---
    for i in range(30):
        intensity = (i % 6) / 6.0  # 0..0.83 repeating → a graded distribution
        hands = int(500 + intensity * 6000)
        field[f"filler{i:02d}"] = RenownInputs(
            label=f"filler{i:02d}",
            wall_clock_hours=3 + intensity * 25,
            total_hands=hands,
            stakes_hands={"$2": hands},
            breadth_opponents={f"ai{j}": int(40 + intensity * 200)
                               for j in range(2 + i % 5)},
            roster_net=intensity * 20_000 - 4_000,  # mostly break-even/small
            # most AIs do SOME backing (Rung-2 showed it's near-universal) — so
            # the field median is meaningful and the Patron reads as an outlier.
            backing_volume=intensity * 30_000,
            regard_likability=0.02, regard_respect=0.01, regard_heat=0.0,
        )
    return field


DRIVER_ORDER = ["scalps", "top1", "peak_worth", "backing", "legendary",
                "tenure", "breadth", "stakes", "apex"]


def _print_board(title: str, entities, w: Weights):
    scored = score_field(entities, w)
    renowns = {eid: total_renown(c) for eid, c in scored.items()}
    cut = high_renown_cut(list(renowns.values()), w)
    order = sorted(renowns, key=renowns.get, reverse=True)

    n_high = sum(1 for r in renowns.values() if r >= cut)
    print(f"\n{'='*92}\n{title}")
    print(f"(field={len(entities)}; volume denominator = {w.volume_denominator!r}; "
          f"high-renown = top {int(w.high_renown_top_fraction*100)}% AND "
          f"≥{w.high_renown_median_multiple:g}×median → renown ≥ {cut:.2f}; "
          f"{n_high} entities high)")
    print("-" * 92)
    hdr = f"{'#':>3} {'entity':24} {'renown':>7} {'quad':>16}  dominant driver"
    print(hdr)
    print("-" * 92)
    for rank, eid in enumerate(order, 1):
        if eid.startswith("filler"):
            continue  # ranked within the field, but not printed individually
        c = scored[eid]
        ren = renowns[eid]
        reg = regard_of(entities[eid])
        q = quadrant(ren, reg, cut)
        top_driver = max(c, key=c.get)
        share = (c[top_driver] / ren * 100) if ren > 0 else 0
        flag = "  ⟵ HIGH" if ren >= cut else ""
        print(f"{rank:>3} {entities[eid].label:24} {ren:7.2f} {q:>16}  "
              f"{top_driver} ({share:.0f}%){flag}")
    fillers = sorted((renowns[e] for e in entities if e.startswith("filler")))
    if fillers:
        print(f"    [{len(fillers)} filler AIs: renown {fillers[0]:.2f}–"
              f"{fillers[-1]:.2f}, median {fillers[len(fillers)//2]:.2f}]")
    return scored, renowns, cut


def main():
    entities = build_archetypes()
    w = Weights()

    print("RENOWN v2 — RUNG 1: synthetic archetype probe")
    print("Pure fixtures, no DB, no sim. Two questions: (1) do the 4 routes "
          "each reach\nhigh renown?  (2) does the volume bot dominate?")

    # --- The design board (wall-clock denomination) ---
    scored, renowns, cut = _print_board(
        "BOARD A — DESIGN (wall-clock denominated volume)", entities, w)

    # --- Per-driver breakdown so the routes are legible ---
    print(f"\n{'-'*92}\nPER-DRIVER BREAKDOWN (Board A)\n{'-'*92}")
    print(f"{'entity':24} " + " ".join(f"{d[:6]:>6}" for d in DRIVER_ORDER))
    for eid in sorted(renowns, key=renowns.get, reverse=True):
        if eid.startswith("filler"):
            continue
        c = scored[eid]
        print(f"{entities[eid].label:24} "
              + " ".join(f"{c[d]:6.2f}" for d in DRIVER_ORDER))

    # --- The treadmill counterfactual (hand-count denomination) ---
    w_naive = Weights(volume_denominator="hands")
    _print_board("BOARD B — NAIVE COUNTERFACTUAL (hand-count denominated volume)",
                 entities, w_naive)

    # -----------------------------------------------------------------
    # Automated Rung-1 verdicts
    # -----------------------------------------------------------------
    print(f"\n{'='*92}\nRUNG-1 VERDICTS\n{'='*92}")

    routes = ["Grinder", "Whale", "Patron", "Villain"]
    print("\n[Q1] Do the four ★ routes EACH reach high renown (Board A)?")
    all_routes_high = True
    for r in routes:
        ok = renowns[r] >= cut
        all_routes_high &= ok
        print(f"   {'PASS' if ok else 'FAIL'}  {r:14} renown={renowns[r]:.2f} "
              f"(cut {cut:.2f})")
    ctrl_ok = renowns["Up-and-comer"] < cut
    print(f"   {'PASS' if ctrl_ok else 'FAIL'}  {'Up-and-comer':14} "
          f"renown={renowns['Up-and-comer']:.2f}  (control must be BELOW cut)")

    print("\n[Q2] Does the volume bot dominate?")
    # Board A (design): fast bot must NOT be high renown.
    fb = "Fast bot (volume bogey)"
    fb_design_high = renowns[fb] >= cut
    print(f"   Board A (wall-clock): fast-bot renown={renowns[fb]:.2f} "
          f"→ {'HIGH ❌' if fb_design_high else 'not high ✅'}")
    # Board B (naive): demonstrate the lever — fast bot SHOULD top the board.
    naive_scored = score_field(entities, w_naive)
    naive_ren = {eid: total_renown(c) for eid, c in naive_scored.items()}
    naive_rank = sorted(naive_ren, key=naive_ren.get, reverse=True).index(fb) + 1
    design_rank = sorted(renowns, key=renowns.get, reverse=True).index(fb) + 1
    print(f"   Fast-bot rank: Board A (design) #{design_rank} of {len(entities)}"
          f"   |   Board B (naive hands) #{naive_rank} of {len(entities)}")
    print(f"   → wall-clock denomination moves the bogey from #{naive_rank} "
          f"to #{design_rank}: the anti-treadmill lever {'WORKS ✅' if design_rank > naive_rank else 'NO EFFECT ❌'}")

    # Driver-dominance: no single driver should carry the whole field.
    print("\n[Q3] Single-driver dominance check (Board A):")
    dominated = []
    for eid in entities:
        c = scored[eid]
        ren = renowns[eid]
        if ren <= 0:
            continue
        top = max(c, key=c.get)
        share = c[top] / ren
        if share > 0.85:
            dominated.append((entities[eid].label, top, share))
    if dominated:
        for lbl, d, s in dominated:
            print(f"   note: {lbl} is {s*100:.0f}% '{d}' (single-route, expected "
                  f"for a pure archetype)")
    else:
        print("   no entity is >85% one driver")

    print(f"\n{'='*92}")
    verdict = all_routes_high and ctrl_ok and (not fb_design_high) and (design_rank > naive_rank)
    print(f"RUNG 1: {'PASS ✅ — proceed to Rung 2 (score the real field)' if verdict else 'FAIL ❌ — retune before building'}")
    print(f"{'='*92}")


if __name__ == "__main__":
    main()
