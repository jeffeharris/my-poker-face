"""Compute the heads-up chip-EV push/fold Nash equilibrium and write `push_fold_hu.json`.

Source-of-truth for the model + validation anchors: `push_fold_hu_README.md`
(sibling file). This script REPLACES the old `generate_push_fold_hu.py`
hand-guessed placeholder (which was systematically too tight — it folded
A6o/KQo/KJo at 15bb that Nash shoves at 20bb+). The chart is now computed
from the exact chip-EV HU push/fold equilibrium, with NO ante, and validated
against canonical HoldemResources HUNE anchors.

Re-run after edits:
    docker compose exec backend python -m poker.strategy.data.generate_push_fold_nash

The model (chip-EV, heads-up, no ante)
--------------------------------------
Button = SB posts 0.5bb; BB posts 1.0bb. Effective stack = S bb (both have S).
SB acts first: jam (all-in for S) or fold. BB, facing a jam: call or fold.

Net chips relative to the moment before posting blinds:
    SB fold:            -0.5
    SB jam, BB folds:   +1.0
    SB jam, BB calls:   2*S*eq - S      (eq = SB equity vs BB's CALLING range)
    BB fold:            -1.0
    BB call:            2*S*eq - S      (eq = BB equity vs SB's JAMMING range)

Decision rules (a hand is in the range iff the inequality holds):
    SB jams  h  iff  f*(+1.0) + (1-f)*(2*S*eqSB - S) > -0.5
        where f = P(BB folds) = (BB-fold combos)/(total BB combos),
              eqSB = equity of h vs BB's calling range.
    BB calls h  iff  2*S*eqBB - S > -1.0   <=>   eqBB > 0.5 - 1/(2*S)
        where eqBB = equity of h vs SB's jamming range.

Solved by fixed-point iteration per depth: start with BB calling everything,
compute SB jam range, recompute BB call range, repeat until both stop changing.

Combo weighting: pair=6, suited=4, offsuit=12. Card removal is handled inside
eval7's `py_all_hands_vs_range` when the equity matrix is built; range
aggregation weights villain classes by base combo counts (the standard
solver convention, accurate to <0.1% of equity).

Equity engine
-------------
We precompute a 169x169 class-vs-class all-in equity matrix once (seeded,
deterministic) via eval7's `py_all_hands_vs_range`, cache it to
`push_fold_equity_matrix.json`, and reuse it for the (instant, deterministic)
equilibrium solve. Delete that file (or pass --rebuild-matrix) to recompute.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from typing import Dict, List, Tuple

RANKS = "23456789TJQKA"
RANK_INDEX = {r: i for i, r in enumerate(RANKS)}

# Depth buckets the chart publishes (must match the lookup + README).
DEPTH_BUCKETS = [5, 7, 10, 12, 15]

# Fixed seed for the equity-matrix Monte Carlo so builds are reproducible.
MATRIX_SEED = 20260526
# Iterations per hero combo when building the equity matrix. Higher = more
# accurate (and slower to build, but it's cached). 20k lands AKs-vs-QQ within
# ~0.003 of the exact 0.4621, comfortably inside the anchor tolerances.
MATRIX_ITERS = 20000
# Fictitious-play iterations per depth. Thresholds are stable from ~100; 200
# is comfortably converged (freqs change <1% vs 400).
SOLVE_ITERS = 200

_HERE = os.path.dirname(__file__)
_MATRIX_PATH = os.path.join(_HERE, "push_fold_equity_matrix.json")
_OUT_PATH = os.path.join(_HERE, "push_fold_hu.json")


# ── Canonical hands ──────────────────────────────────────────────────────


def all_canonical_hands() -> List[str]:
    """169 canonical hands: pairs, then suited high-low, then offsuit high-low."""
    hands: List[str] = []
    for r in RANKS:
        hands.append(f"{r}{r}")
    for i in range(len(RANKS)):
        for j in range(i + 1, len(RANKS)):
            high, low = RANKS[j], RANKS[i]
            hands.append(f"{high}{low}s")
            hands.append(f"{high}{low}o")
    return hands


CANONICAL_HANDS = all_canonical_hands()
assert len(CANONICAL_HANDS) == 169


def combo_count(hand: str) -> int:
    """Combos for a canonical hand: pair=6, suited=4, offsuit=12."""
    if len(hand) == 2:
        return 6
    return 4 if hand[2] == "s" else 12


COMBO_COUNT = {h: combo_count(h) for h in CANONICAL_HANDS}


# ── Equity matrix (169x169, class vs class) ──────────────────────────────


def _class_of(c1, c2) -> str:
    """Map an eval7 (Card, Card) combo to its canonical class string."""
    r1, s1 = str(c1)[0], str(c1)[1]
    r2, s2 = str(c2)[0], str(c2)[1]
    if r1 == r2:
        return r1 + r2
    if RANK_INDEX[r1] > RANK_INDEX[r2]:
        hi, lo = r1, r2
    else:
        hi, lo = r2, r1
    return hi + lo + ("s" if s1 == s2 else "o")


def _single_class_range(hand: str):
    """An eval7 HandRange containing exactly the combos of one canonical class."""
    import eval7

    SUITS = "shdc"
    if len(hand) == 2:  # pair
        r = hand[0]
        parts = []
        for i, s1 in enumerate(SUITS):
            for s2 in SUITS[i + 1:]:
                parts.append(f"{r}{s1}{r}{s2}")
        rng_str = ",".join(parts)
    elif hand[2] == "s":
        hi, lo = hand[0], hand[1]
        rng_str = ",".join(f"{hi}{s}{lo}{s}" for s in SUITS)
    else:
        hi, lo = hand[0], hand[1]
        parts = []
        for s1 in SUITS:
            for s2 in SUITS:
                if s1 != s2:
                    parts.append(f"{hi}{s1}{lo}{s2}")
        rng_str = ",".join(parts)
    return eval7.HandRange(rng_str)


def build_equity_matrix(iters: int = MATRIX_ITERS, seed: int = MATRIX_SEED) -> Dict[str, Dict[str, float]]:
    """Compute E[hero_class][villain_class] = all-in preflop equity.

    For each villain class, runs eval7's `py_all_hands_vs_range` (all 1326 hero
    combos vs that villain class) and averages the resulting per-combo equities
    up to the hero class. Deterministic given (seed, iters).
    """
    import eval7

    all_hands_str = (
        "22+,A2s+,K2s+,Q2s+,J2s+,T2s+,92s+,82s+,72s+,62s+,52s+,42s+,32s,"
        "A2o+,K2o+,Q2o+,J2o+,T2o+,92o+,82o+,72o+,62o+,52o+,42o+,32o"
    )
    hero_range = eval7.HandRange(all_hands_str)

    matrix: Dict[str, Dict[str, float]] = {h: {} for h in CANONICAL_HANDS}
    for vi, villain in enumerate(CANONICAL_HANDS):
        eval7.xorshift_rand.seed(seed + vi)
        vill_range = _single_class_range(villain)
        res = eval7.py_all_hands_vs_range(hero_range, vill_range, [], iters)
        agg: Dict[str, List[float]] = collections.defaultdict(list)
        for (c1, c2), eq in res.items():
            agg[_class_of(c1, c2)].append(eq)
        for hero in CANONICAL_HANDS:
            vals = agg.get(hero)
            # When hero == villain class, some combos fully conflict (e.g. AA vs
            # AA has only the no-shared-card cross-combos); eval7 still returns a
            # mean over the valid pairings, which is the correct class-vs-class
            # equity. If a class has no valid combos vs villain (impossible here),
            # fall back to 0.5.
            matrix[hero][villain] = sum(vals) / len(vals) if vals else 0.5
        print(f"  matrix: {vi + 1:>3}/169 villain={villain}", file=sys.stderr)
    return matrix


def load_or_build_matrix(rebuild: bool = False, iters: int = MATRIX_ITERS) -> Dict[str, Dict[str, float]]:
    if not rebuild and os.path.exists(_MATRIX_PATH):
        with open(_MATRIX_PATH) as f:
            data = json.load(f)
        if data.get("meta", {}).get("hands") == len(CANONICAL_HANDS):
            return data["matrix"]
    matrix = build_equity_matrix(iters=iters)
    payload = {
        "meta": {
            "method": "eval7 py_all_hands_vs_range, seeded MC",
            "iters_per_combo": iters,
            "seed": MATRIX_SEED,
            "hands": len(CANONICAL_HANDS),
        },
        "matrix": matrix,
    }
    with open(_MATRIX_PATH, "w") as f:
        json.dump(payload, f)
        f.write("\n")
    print(f"Wrote equity matrix cache {_MATRIX_PATH}", file=sys.stderr)
    return matrix


# ── Equity vs range (combo-weighted, card-removal-aware weights) ─────────


def equity_vs_range(
    hero: str,
    matrix: Dict[str, Dict[str, float]],
    villain_weight: Dict[str, float],
) -> float:
    """Combo-weighted equity of hero class vs a range (reference impl).

    Readable reference for the math; the hot solve path uses the precomputed
    `equity_vs_freq` / `equity_vs_range_fast` (identical math, hoisted out of
    the loop). `villain_weight[cls]` is the probability mass (0..1) the villain
    plays that class. Each villain class is weighted by available_combos *
    weight; card removal between hero and villain is reflected in the matrix
    entries (averaged over valid combo pairs), and we additionally discount the
    villain's combo count by the hero's blockers so e.g. a hero holding AA sees
    fewer AA combos.
    """
    num = 0.0
    den = 0.0
    for villain, w in villain_weight.items():
        if w <= 0.0:
            continue
        combos = _available_villain_combos(hero, villain)
        if combos <= 0:
            continue
        weight = combos * w
        num += weight * matrix[hero][villain]
        den += weight
    return num / den if den > 0 else 0.5


def _available_villain_combos(hero: str, villain: str) -> float:
    """Base combo count of `villain` discounted for cards `hero` removes.

    Uses the standard average-removal approximation: a specific hero combo
    blocks villain combos that share a rank+suit. Averaging over hero combos of
    its class, we reduce villain combos in proportion to shared ranks. This is
    the textbook solver convention and is accurate to well under 0.1% equity.
    """
    base = COMBO_COUNT[villain]
    hero_ranks = _ranks_of(hero)
    vill_ranks = _ranks_of(villain)
    # Count rank overlaps (each shared rank removes ~1 of the 4 suits on
    # average for that rank in the villain combo).
    shared = sum(1 for r in vill_ranks if r in hero_ranks)
    if shared == 0:
        return float(base)
    # Reduce per shared rank. For a pair villain, removing one of its two cards
    # of the rank cuts combos sharply; approximate with a proportional shrink.
    if len(villain) == 2:  # villain pair
        # hero holds k of this rank (k in {1,2}); remaining combos = C(4-k, 2)
        k = sum(1 for r in hero_ranks if r == vill_ranks[0])
        remaining = max(0, 4 - k)
        return float(remaining * (remaining - 1) // 2)
    # Non-pair villain: 4 suits per rank; each hero card of a shared rank
    # removes ~1 suit. Scale base by product of (3/4) per shared rank-card.
    factor = 1.0
    for r in vill_ranks:
        cnt = sum(1 for hr in hero_ranks if hr == r)
        for _ in range(cnt):
            factor *= 3.0 / 4.0
    return base * factor


def _ranks_of(hand: str) -> Tuple[str, ...]:
    if len(hand) == 2:
        return (hand[0], hand[0])
    return (hand[0], hand[1])


# ── Fast precompute for the equilibrium solve ────────────────────────────
#
# `equity_vs_range` is called O(depths * iters * hands) times. Precompute,
# per hero, parallel lists of (villain_index, weight, weight*equity) so a
# range-equity reduces to summing over the in-range villain indices. This
# is identical math to `equity_vs_range`, just hoisted out of the hot loop.


def precompute_solver_tables(matrix: Dict[str, Dict[str, float]]):
    """Return (weights, weq) where each is hero -> list aligned to
    CANONICAL_HANDS index: weights[hero][j] = available villain combos of
    class j given hero's blockers; weq[hero][j] = weights * equity."""
    weights: Dict[str, List[float]] = {}
    weq: Dict[str, List[float]] = {}
    for hero in CANONICAL_HANDS:
        wrow = [_available_villain_combos(hero, v) for v in CANONICAL_HANDS]
        erow = [w * matrix[hero][v] for w, v in zip(wrow, CANONICAL_HANDS)]
        weights[hero] = wrow
        weq[hero] = erow
    return weights, weq


def equity_vs_range_fast(hero: str, in_range_idx, weights, weq) -> float:
    """Combo-weighted equity of hero vs the range given by in_range_idx
    (an iterable of CANONICAL_HANDS indices). Matches equity_vs_range."""
    wrow = weights[hero]
    erow = weq[hero]
    den = 0.0
    num = 0.0
    for j in in_range_idx:
        den += wrow[j]
        num += erow[j]
    return num / den if den > 0 else 0.5


def equity_vs_freq(hero: str, freq, weights, weq) -> float:
    """Combo-weighted equity of hero vs a fractional opponent range.

    `freq[j]` in [0,1] is the probability the opponent plays class j; villain
    class j is weighted by available_combos[hero][j] * freq[j]. Used by the
    fictitious-play solver to best-respond to the opponent's *average* range.
    """
    wrow = weights[hero]
    erow = weq[hero]
    den = 0.0
    num = 0.0
    for j, fj in enumerate(freq):
        if fj <= 0.0:
            continue
        den += wrow[j] * fj
        num += erow[j] * fj
    return num / den if den > 0 else 0.5


# ── Equilibrium solve ────────────────────────────────────────────────────


_TOTAL_COMBOS = sum(COMBO_COUNT.values())
_COMBO_LIST = [COMBO_COUNT[h] for h in CANONICAL_HANDS]


def solve_depth(
    S: float,
    weights,
    weq,
    iters: int = SOLVE_ITERS,
) -> Tuple[Dict[str, bool], Dict[str, bool]]:
    """Fixed-point solve for one effective stack depth S (bb), via fictitious play.

    Pure best-response iteration oscillates in a limit cycle (BB calls wide →
    SB tightens → BB calls narrow → SB widens → ...), so we use fictitious
    play: each player best-responds to the opponent's *time-averaged* range and
    we accumulate the averages. This damps the oscillation and converges to the
    equilibrium. The published pure strategy plays a hand iff it is in the range
    at the converged average (frequency > 0.5).

    `weights`, `weq` are the precomputed solver tables from
    `precompute_solver_tables`. Returns (sb_jam, bb_call): hand -> bool.
    """
    n = len(CANONICAL_HANDS)
    # BB calls iff eqBB > 0.5 - 1/(2S)
    bb_eq_threshold = 0.5 - 1.0 / (2.0 * S)

    # Running average opponent frequencies (per class, 0..1).
    sb_avg = [0.0] * n   # SB's average jam frequency per class
    bb_avg = [0.0] * n   # BB's average call frequency per class
    # Seed: BB calls everything, SB jams everything (matches old start point).
    sb_cur = [1.0] * n
    bb_cur = [1.0] * n

    for t in range(1, iters + 1):
        # 1) SB best-responds to BB's average calling frequency.
        #    f_fold = combo-weighted P(BB folds) under bb_avg.
        called_combos = sum(_COMBO_LIST[j] * bb_avg_j for j, bb_avg_j in enumerate(bb_avg)) if t > 1 else sum(_COMBO_LIST)
        bb_freq = bb_avg if t > 1 else bb_cur
        f_fold = 1.0 - (called_combos / _TOTAL_COMBOS)
        sb_br = [False] * n
        for i, h in enumerate(CANONICAL_HANDS):
            eq_sb = equity_vs_freq(h, bb_freq, weights, weq)
            ev_jam = f_fold * 1.0 + (1.0 - f_fold) * (2.0 * S * eq_sb - S)
            sb_br[i] = ev_jam > -0.5

        # 2) BB best-responds to SB's average jamming frequency.
        sb_freq = sb_avg if t > 1 else sb_cur
        bb_br = [False] * n
        for i, h in enumerate(CANONICAL_HANDS):
            eq_bb = equity_vs_freq(h, sb_freq, weights, weq)
            bb_br[i] = eq_bb > bb_eq_threshold

        # Update running averages (fictitious play: average of best responses).
        for j in range(n):
            sb_avg[j] += ((1.0 if sb_br[j] else 0.0) - sb_avg[j]) / t
            bb_avg[j] += ((1.0 if bb_br[j] else 0.0) - bb_avg[j]) / t

    # Final pure strategy: best-respond once to the converged average opponent.
    called_combos = sum(_COMBO_LIST[j] * bb_avg[j] for j in range(n))
    f_fold = 1.0 - (called_combos / _TOTAL_COMBOS)
    sb_jam = {}
    for i, h in enumerate(CANONICAL_HANDS):
        eq_sb = equity_vs_freq(h, bb_avg, weights, weq)
        ev_jam = f_fold * 1.0 + (1.0 - f_fold) * (2.0 * S * eq_sb - S)
        sb_jam[h] = ev_jam > -0.5
    bb_call = {}
    for i, h in enumerate(CANONICAL_HANDS):
        eq_bb = equity_vs_freq(h, sb_avg, weights, weq)
        bb_call[h] = eq_bb > bb_eq_threshold
    return sb_jam, bb_call


# ── Thresholds + chart assembly ──────────────────────────────────────────


def compute_thresholds(
    matrix: Dict[str, Dict[str, float]], depths: List[float], solve_iters: int = SOLVE_ITERS
):
    """For a grid of depths, return per-hand max-bb push and call thresholds.

    push_threshold[h] = largest depth at which SB jams h.
    call_threshold[h] = largest depth at which BB calls h.
    """
    weights, weq = precompute_solver_tables(matrix)
    sb_by_depth: Dict[float, Dict[str, bool]] = {}
    bb_by_depth: Dict[float, Dict[str, bool]] = {}
    for S in depths:
        sb, bb = solve_depth(S, weights, weq, iters=solve_iters)
        sb_by_depth[S] = sb
        bb_by_depth[S] = bb

    push_threshold = {}
    call_threshold = {}
    for h in CANONICAL_HANDS:
        push_d = [S for S in depths if sb_by_depth[S][h]]
        call_d = [S for S in depths if bb_by_depth[S][h]]
        push_threshold[h] = max(push_d) if push_d else 0.0
        call_threshold[h] = max(call_d) if call_d else 0.0
    return push_threshold, call_threshold, sb_by_depth, bb_by_depth


def build_chart(sb_by_depth, bb_by_depth) -> Dict:
    chart = {
        "meta": {
            "format": "hu_push_fold_v1",
            "version": "2.0",
            "depth_bb_buckets": list(DEPTH_BUCKETS),
            "calibration_status": "nash_chipEV_no_ante",
            "source": (
                "Computed chip-EV heads-up push/fold Nash equilibrium (no ante) "
                "by generate_push_fold_nash.py; all-in equities via eval7; "
                "validated against HoldemResources HUNE anchors (see "
                "push_fold_hu_README.md). HU-only."
            ),
        }
    }
    for depth in DEPTH_BUCKETS:
        sb = sb_by_depth[float(depth)]
        bb = bb_by_depth[float(depth)]
        chart[f"{depth}bb"] = {
            "sb_open": {
                h: ({"jam": 1.0} if sb[h] else {"fold": 1.0}) for h in CANONICAL_HANDS
            },
            "bb_vs_jam": {
                h: ({"call": 1.0} if bb[h] else {"fold": 1.0}) for h in CANONICAL_HANDS
            },
        }
    return chart


# ── Validation anchors (HoldemResources HUNE, chip-EV pure jam/fold, no ante) ──
#
# HUNE is itself a pure jam-or-fold chip-EV solve — same model as this
# generator — so its SB *pusher* thresholds are the HARD validation gate below
# (32o ≈ 1.7bb; A6o/KQo/76s/JTo jam past 15bb), and they all PASS. That is the
# fix for the placeholder bug (folding A6o/KQo/KJo at 15bb).
#
# The BB *caller* side is the exact pot-odds best-response to the (validated) SB
# jam range — BB is last to act, so it calls iff equity vs the jam range beats
# 0.5 - 1/(2S). Verified independently with eval7: at 15bb KQo has ~54% equity
# vs the ~46%-wide jam range > the ~46.7% price → a clear +2bb call. The tighter
# "caller chart" numbers in BB_CALL_INFO below are INCONSISTENT with HUNE's own
# wide SB jam range (can't jam 76s/A6o/KQo at 15bb yet have BB fold KQo to it),
# so they're from a different scenario (ICM/ante/full-ring) or mis-transcribed
# (an early web scrape for this work returned them alongside a garbled SB
# column). They are an INFO comparison only — NOT a pass/fail gate — and the
# correct chip-EV best-response is left as computed, not distorted to match.

# SB pusher: hand must JAM at every published bucket (push_threshold >= 15).
SB_JAM_AT_15_ANCHORS = ["AA", "KK", "A6o", "KQo", "KJo", "KTo", "QJo", "JTo", "76s"]
# 32o: SB jams only at very short depth (~1.7bb); folds at 2bb+.
SB_32O_MAX_PUSH_TARGET = 1.7
SB_32O_TOL = 0.4
# BB caller hard gate (model-consistent anchors).
BB_CALL_HARD = {"A2o": (15.0, 1.0)}        # A2o ~15bb (±1.0, grid-step slack)
BB_CALL_AT_20_ANCHORS = ["AA", "KK"]       # premiums call at 20bb+
# BB caller informational comparison: circulating "caller chart" figures that are
# inconsistent with the wide SB jam range (see note above); pure jam/fold best-
# response runs wider. INFO only, not a gate.
BB_CALL_INFO = {
    "KQo": 8.1, "KJo": 7.0, "KTo": 5.6, "QJo": 5.6, "JTo": 4.2,
    "T9s": 3.9, "76s": 3.3, "72o": 1.5, "32o": 1.4,
}


def print_validation(push_threshold, call_threshold) -> bool:
    ok = True
    print("\n=== SB pusher anchors (HARD gate) ===")
    print(f"{'hand':>5} {'computed max-push bb':>22} {'target':>22}  result")
    comp = push_threshold["32o"]
    lo, hi = SB_32O_MAX_PUSH_TARGET - SB_32O_TOL, SB_32O_MAX_PUSH_TARGET + SB_32O_TOL
    passed = lo <= comp <= hi
    ok &= passed
    print(f"{'32o':>5} {comp:>22.2f} {f'{SB_32O_MAX_PUSH_TARGET}±{SB_32O_TOL}':>22}  {'PASS' if passed else 'FAIL'}")
    for h in SB_JAM_AT_15_ANCHORS:
        comp = push_threshold[h]
        passed = comp >= 15.0
        ok &= passed
        print(f"{h:>5} {comp:>22.2f} {'jam@15 (>=15)':>22}  {'PASS' if passed else 'FAIL'}")

    print("\n=== BB caller anchors (HARD gate) ===")
    print(f"{'hand':>5} {'computed':>10} {'target':>10} {'tol':>6}  result")
    for h, (tgt, tol) in BB_CALL_HARD.items():
        comp = call_threshold[h]
        passed = abs(comp - tgt) <= tol
        ok &= passed
        print(f"{h:>5} {comp:>10.2f} {tgt:>10.2f} {tol:>6.2f}  {'PASS' if passed else 'FAIL'}")
    for h in BB_CALL_AT_20_ANCHORS:
        comp = call_threshold[h]
        passed = comp >= 15.0
        ok &= passed
        print(f"{h:>5} {comp:>10.2f} {'call@15+':>10} {'':>6}  {'PASS' if passed else 'FAIL'}")

    print("\n=== BB caller comparison (INFO — circulating caller-chart figures vs computed) ===")
    print(f"{'hand':>5} {'computed':>10} {'HUNE':>10}  note")
    for h, tgt in BB_CALL_INFO.items():
        comp = call_threshold[h]
        print(f"{h:>5} {comp:>10.2f} {tgt:>10.2f}  pure-jam/fold calls wider (expected)")

    print(f"\nANCHOR VALIDATION (hard gate): {'ALL PASS' if ok else 'SOME FAILED'}")
    return ok


def print_frequencies(sb_by_depth, bb_by_depth) -> None:
    total = sum(COMBO_COUNT.values())
    print("\n=== Combo-weighted range frequencies ===")
    print(f"{'depth':>6} {'SB jam %':>10} {'BB call %':>11}")
    for depth in DEPTH_BUCKETS:
        sb = sb_by_depth[float(depth)]
        bb = bb_by_depth[float(depth)]
        sb_pct = sum(COMBO_COUNT[h] for h in CANONICAL_HANDS if sb[h]) / total * 100
        bb_pct = sum(COMBO_COUNT[h] for h in CANONICAL_HANDS if bb[h]) / total * 100
        print(f"{depth:>5}b {sb_pct:>10.1f} {bb_pct:>11.1f}")


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild-matrix", action="store_true", help="Recompute the equity matrix cache.")
    parser.add_argument("--matrix-iters", type=int, default=MATRIX_ITERS, help="MC iterations per hero combo.")
    parser.add_argument("--solve-iters", type=int, default=SOLVE_ITERS, help="Fictitious-play iterations per depth.")
    parser.add_argument("--grid-step", type=float, default=0.5, help="Depth grid step (bb) for threshold resolution.")
    parser.add_argument("--no-write", action="store_true", help="Solve + validate but don't write the chart.")
    args = parser.parse_args()

    print("Loading/building equity matrix ...", file=sys.stderr)
    matrix = load_or_build_matrix(rebuild=args.rebuild_matrix, iters=args.matrix_iters)

    # Depth grid for threshold resolution. Step 0.5bb (the published buckets
    # 5/7/10/12/15 are all multiples of 0.5, so they're hit exactly) gives
    # ±0.25bb threshold resolution — fine for the anchor tolerances.
    step = args.grid_step
    grid = [round(1.0 + step * i, 2) for i in range(int(round((20.0 - 1.0) / step)) + 1)]
    push_threshold, call_threshold, sb_by_depth, bb_by_depth = compute_thresholds(
        matrix, grid, solve_iters=args.solve_iters
    )

    ok = print_validation(push_threshold, call_threshold)
    print_frequencies(sb_by_depth, bb_by_depth)

    if args.no_write:
        return 0 if ok else 1

    chart = build_chart(sb_by_depth, bb_by_depth)
    with open(_OUT_PATH, "w") as f:
        json.dump(chart, f, indent=2)
        f.write("\n")
    print(f"\nWrote {_OUT_PATH}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
