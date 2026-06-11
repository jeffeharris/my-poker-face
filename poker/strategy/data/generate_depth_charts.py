"""Generate depth-aware 6-max preflop charts from the 100bb baseline.

Source-of-truth for the rules: `depth_charts_README.md` (sibling file).

The 100bb chart (`preflop_100bb_6max.json`) is depth-agnostic — the tiered
bot was measured playing a *byte-identical* preflop game at 100/50/25bb
(`SOLVER_CHART_SCOPE.md` DIAGNOSED section): VPIP 18 / PFR 14 / jam 0.4% /
open 3.3bb at every depth, flat-calling 18% vs opens. That zero depth-
adjustment is the diagnosed short-stack leak (−18 to −22 bb/100 at 25–50bb
vs the Jeff_clone human model).

This script derives `preflop_50bb_6max.json` and `preflop_25bb_6max.json`
by transforming the 100bb chart with hand-authored depth rules. The
governing principle as stacks shorten: **less flatting, more jamming /
polarization**. A 3-bet at 25bb commits ~⅓ of the stack, so 3-bets become
jams; flatting an open OOP with no implied odds is a commitment error, so
speculative flats fold and value flats jam.

These are deliberately *coarse* — the diagnosis says even a coarse depth
chart should recover most of the leak (the cheap "100bb → fix" pattern).
The knobs below are the calibration surface; re-run after editing.

Re-run after edits:
    docker compose exec backend python -m poker.strategy.data.generate_depth_charts

Action labels by scenario (must match the 100bb chart):
    rfi      : raise_2.5bb | fold              (the open)
    vs_open  : raise_3x     | call | fold       (raise = the 3-bet)
    vs_3bet  : raise_2.2x   | call | fold       (raise = the 4-bet)
    vs_4bet  : jam          | call | fold
"""

from __future__ import annotations

import json
import os
from typing import Dict

# ── Calibration knobs ───────────────────────────────────────────────
# All fractions are of the *original 100bb* probability mass for that
# action. They are the hand-authored surface; tuning = edit + re-run.

# A hand is "value" (jam-worthy when shallow) only when the 100bb chart
# RAISES it at high frequency — a polarized value-raise, not the thin
# 3-bet/4-bet *bluff* frequency the chart sprinkles on speculative hands.
# Gating on mere raise-presence would turn 76s's 0.15 bluff-3bet into a
# 25bb jam (−EV, and badly so vs a calling-station eval). 0.5 cleanly
# separates premiums/strong-broadways (jam) from bluff-raises (fold).
VALUE_RAISE_THRESHOLD = 0.50

# 25bb — commit-or-fold regime.
#   vs_open, NON-value (marginal/bluff) hands: facing a single open you are
#   NOT committed, so fold the bluff-3bet + most flats, keep only a thin flat
#   (no profitable shallow OOP flat, no bluff-jam vs a station).
J25_VSOPEN_SPEC_FLAT_KEEP = 0.30      # fraction of original call kept as a flat
#   ...EXCEPT the BB, which closes the action and gets a price: short-stacked it
#   JAMS its flat defense vs a steal rather than over-folding (without this, 25bb
#   BB defense collapses ~38pp below 100bb — far past MDF). Fraction of the BB's
#   non-value continue range converted to a jam at 25bb.
J25_VSOPEN_BB_JAM_KEEP = 0.65
#   vs_3bet: facing a re-raise you ARE near-committed shallow. Gate on the
#   100bb FOLD frequency (not raise-dominance — value hands like JJ/TT
#   continue by *calling*, raise≈0.2, yet must jam shallow). If the chart
#   already mostly folds the hand to a 3-bet, fold it; otherwise jam the
#   whole continue range.
J25_VS3BET_FOLD_GATE = 0.50
#   vs_4bet: calling a 4-bet at 25bb = jamming anyway.
J25_VS4BET_JAM_FROM_CALL = 1.00

# 50bb — milder polarization; some flatting still profitable. 50bb is deep
# enough to 3-bet-and-play, so this only tightens flats and adds partial
# commitment. NOTE: an aggressive variant (0.35/0.55/0.40) was measured and
# moved bb/100 by ~0 (−14.0 → −13.8, noise) — the 50bb residual is the
# DEFERRED low-SPR postflop passivity (AggFactor 0.16 vs 0.27 at 100bb),
# not a preflop-range problem. Pushing preflop further over-folds playable
# hands for no gain, so we keep the principled mild rules.
J50_VSOPEN_VAL_RAISE_FROM_CALL = 0.20  # value hands: push a little more to 3-bet
J50_VSOPEN_VAL_FOLD_FROM_CALL = 0.20   # tighten value-hand flats
J50_VSOPEN_SPEC_FOLD_FROM_CALL = 0.35  # tighten speculative flats
J50_VS3BET_JAM_FROM_CALL = 0.25        # some strong flats commit (jam)
J50_VS3BET_FOLD_FROM_CALL = 0.25       # tighten the rest
J50_VS4BET_JAM_FROM_CALL = 0.50        # low SPR → half of calls become jams


def _norm(profile: Dict[str, float]) -> Dict[str, float]:
    """Drop ~zero entries, round to 3dp, renormalize to sum 1.0."""
    clean = {a: p for a, p in profile.items() if p > 1e-9}
    total = sum(clean.values())
    if total <= 0:
        return {"fold": 1.0}
    out = {a: round(p / total, 3) for a, p in clean.items()}
    # Fix rounding drift onto the largest entry so probs sum to exactly 1.
    drift = round(1.0 - sum(out.values()), 3)
    if abs(drift) >= 0.001:
        top = max(out, key=out.get)
        out[top] = round(out[top] + drift, 3)
    return out


def _is_pure_fold(p: Dict[str, float]) -> bool:
    return p.get("fold", 0.0) >= 0.999


# ── Per-scenario transforms ─────────────────────────────────────────

def t_rfi(p: Dict[str, float], depth: int) -> Dict[str, float]:
    """Opening ranges/sizes are already fine at these depths — the leak is
    in the facing-action flats, not RFI. Keep unchanged (preserves the
    opening aggression the bot already has)."""
    return dict(p)


def t_vs_open(p: Dict[str, float], depth: int, is_bb: bool = False) -> Dict[str, float]:
    """Hero faces an open; decides 3-bet (raise_3x) / call / fold.

    ``is_bb`` flags a BB-defends node: the BB closes the action, so short-stacked
    it commits its defense by jamming rather than over-folding (see the 25bb branch).
    """
    raise_, call, fold = p.get("raise_3x", 0.0), p.get("call", 0.0), p.get("fold", 0.0)
    if _is_pure_fold(p):
        return dict(p)
    is_value = raise_ >= VALUE_RAISE_THRESHOLD

    if depth == 25:
        if is_value:
            # Stack off: the whole continue range (3-bet + flats) jams.
            return _norm({"jam": raise_ + call, "fold": fold})
        if is_bb:
            # BB closes the action and gets a price — jam its flat defense vs a
            # steal rather than over-fold (cold-defenders below correctly don't).
            jam = (raise_ + call) * J25_VSOPEN_BB_JAM_KEEP
            return _norm({"jam": jam, "fold": 1.0 - jam})
        # Cold-defender, marginal/bluff: drop the bluff-3bet and most flats to
        # fold; keep only a thin flat. No bluff-jam (−EV vs a station).
        new_call = call * J25_VSOPEN_SPEC_FLAT_KEEP
        new_fold = fold + raise_ + call * (1 - J25_VSOPEN_SPEC_FLAT_KEEP)
        return _norm({"call": new_call, "fold": new_fold})

    if depth == 50:
        if is_value:
            new_raise = raise_ + J50_VSOPEN_VAL_RAISE_FROM_CALL * call
            new_fold = fold + J50_VSOPEN_VAL_FOLD_FROM_CALL * call
            new_call = call * (1 - J50_VSOPEN_VAL_RAISE_FROM_CALL - J50_VSOPEN_VAL_FOLD_FROM_CALL)
            return _norm({"raise_3x": new_raise, "call": new_call, "fold": new_fold})
        # Milder: keep the 3-bet (incl. bluff) at 50bb, just tighten flats.
        new_fold = fold + J50_VSOPEN_SPEC_FOLD_FROM_CALL * call
        new_call = call * (1 - J50_VSOPEN_SPEC_FOLD_FROM_CALL)
        return _norm({"raise_3x": raise_, "call": new_call, "fold": new_fold})

    return dict(p)


def t_vs_3bet(p: Dict[str, float], depth: int) -> Dict[str, float]:
    """Hero opened, faces a 3-bet; decides 4-bet (raise_2.2x) / call / fold."""
    raise_, call, fold = p.get("raise_2.2x", 0.0), p.get("call", 0.0), p.get("fold", 0.0)
    if _is_pure_fold(p):
        return dict(p)

    if depth == 25:
        # Jam-or-fold. Fold-gate (not raise-dominance): hands the 100bb chart
        # mostly folds to a 3-bet stay folds; everything else commits — the
        # whole continue range (4-bet + flats) jams. This folds bluff-4bet
        # junk (76s: fold 0.75) while jamming value flats (JJ/TT: fold ≤0.25).
        if fold >= J25_VS3BET_FOLD_GATE:
            return {"fold": 1.0}  # incl. the bluff-4bet — no jam vs a station
        return _norm({"jam": raise_ + call, "fold": fold})

    if depth == 50:
        jam = J50_VS3BET_JAM_FROM_CALL * call
        new_fold = fold + J50_VS3BET_FOLD_FROM_CALL * call
        new_call = call * (1 - J50_VS3BET_JAM_FROM_CALL - J50_VS3BET_FOLD_FROM_CALL)
        return _norm({"raise_2.2x": raise_, "jam": jam, "call": new_call, "fold": new_fold})

    return dict(p)


def t_vs_4bet(p: Dict[str, float], depth: int) -> Dict[str, float]:
    """Hero faces a 4-bet; decides jam / call / fold. Already polarized;
    shorten further by converting calls to jams (low SPR = committed)."""
    jam, call, fold = p.get("jam", 0.0), p.get("call", 0.0), p.get("fold", 0.0)
    if _is_pure_fold(p):
        return dict(p)
    frac = J25_VS4BET_JAM_FROM_CALL if depth == 25 else J50_VS4BET_JAM_FROM_CALL
    new_jam = jam + frac * call
    new_call = call * (1 - frac)
    return _norm({"jam": new_jam, "call": new_call, "fold": fold})


_TRANSFORMS = {
    "rfi": t_rfi,
    "vs_open": t_vs_open,
    "vs_3bet": t_vs_3bet,
    "vs_4bet": t_vs_4bet,
}


def build_depth_chart(base: dict, depth: int) -> dict:
    out = {
        "meta": {
            "depth_bb": depth,
            "players": 6,
            "version": "1.0",
            "derived_from": "preflop_100bb_6max.json",
            "notes": (
                f"Depth-aware {depth}bb 6-max chart, generated by "
                f"generate_depth_charts.py from the 100bb baseline. Rule: "
                f"less flatting / more jam as stacks shorten. See "
                f"depth_charts_README.md."
            ),
        }
    }
    for scenario in ("rfi", "vs_open", "vs_3bet", "vs_4bet"):
        fn = _TRANSFORMS[scenario]
        nodes = {}
        for group, hands in base.get(scenario, {}).items():
            # vs_open BB-defends nodes commit by jamming short-stacked (t_vs_open).
            extra = {"is_bb": group.startswith("BB_")} if scenario == "vs_open" else {}
            nodes[group] = {hand: fn(profile, depth, **extra) for hand, profile in hands.items()}
        out[scenario] = nodes
    return out


def main() -> None:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "preflop_100bb_6max.json")) as f:
        base = json.load(f)

    for depth in (50, 25):
        chart = build_depth_chart(base, depth)
        path = os.path.join(here, f"preflop_{depth}bb_6max.json")
        with open(path, "w") as f:
            json.dump(chart, f, indent=2)
            f.write("\n")
        n = sum(len(chart[s]) for s in ("rfi", "vs_open", "vs_3bet", "vs_4bet"))
        print(f"wrote {path} ({n} scenario-groups)")


if __name__ == "__main__":
    main()
