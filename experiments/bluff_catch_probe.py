"""Behavioral proof of the bluff-catch-vs-over-bluffer HARD OVERRIDE.

The mirror of stop_bluff_probe for the inverse leak. Where stop_bluff proves a
TAG stops bluffing a station (air aggression → ~0), this proves a TAG stops
FOLDING its bluff-catchers to a maniac — it calls the over-bluffer down.

Target: ManiacOverBluff (the over-bluff validation twin) HU. Measures the
class the override targets — bluff-catchers (`medium_made` / `weak_made`)
facing a bet — by reading the controller's own postflop hand_strength off the
pipeline snapshot, plus whether the override actually fired.

Three arms, TAG hero vs ManiacOverBluff HU:
  - OFF       : exploitation_strength 0.0          → override never fires (control)
  - ON        : exploitation_strength 1.0, composed → override fires (call down)
  - ON+TILTED : exploitation_strength 1.0, tilt_factor forced 0.0
                → override SUPPRESSED ("can't be on tilt and hero-calling a read")

Pass criteria:
  - bluff-catcher FOLD rate: ON ≪ OFF   (the override removes folds vs a maniac)
  - bluff-catcher FOLD rate: ON+TILTED ≈ OFF  (psychology gate holds)

Run: docker compose exec -T backend python -m experiments.bluff_catch_probe
"""

import sys
from collections import Counter

import experiments.simulate_bb100 as sim
from poker.tiered_bot_controller import TieredBotController

HANDS = 3000
SEED = 42
BLUFF_CATCH_CLASSES = {"medium_made", "weak_made"}


def _run_arm(strength, force_tilt_factor=None):
    TieredBotController.exploitation_strength = strength
    folds = Counter()
    calls = Counter()
    tot = Counter()
    fired = Counter()
    orig_dec = TieredBotController._get_postflop_decision
    orig_tilt = TieredBotController._zone_to_tilt_factor

    def wrapped(self, *a, **k):
        d = orig_dec(self, *a, **k)
        snap = getattr(self, "_last_pipeline_snapshot", {}) or {}
        hs = snap.get("hand_strength")
        act = snap.get("resolved_action")
        # Facing-a-bet only: a made hand folds/calls/raises ONLY when facing a
        # bet; 'bet'/'check' are opens (no bluff to catch). This isolates the
        # spots the override targets instead of diluting over the whole class.
        if hs in BLUFF_CATCH_CLASSES and act in ("fold", "call", "raise"):
            tot[hs] += 1
            if act == "fold":
                folds[hs] += 1
            if act == "call":
                calls[hs] += 1
            if snap.get("bluff_catch_override"):
                fired[hs] += 1
        return d

    TieredBotController._get_postflop_decision = wrapped
    if force_tilt_factor is not None:
        TieredBotController._zone_to_tilt_factor = lambda self, emotional_state: force_tilt_factor
    try:
        st = sim.load_strategy_table()
        sim.run_matchup(
            "TAG",
            "ManiacOverBluff",
            HANDS,
            st,
            big_blind=100,
            starting_stack=10000,
            base_seed=SEED,
        )
    finally:
        TieredBotController._get_postflop_decision = orig_dec
        TieredBotController._zone_to_tilt_factor = orig_tilt
    return folds, calls, tot, fired


def _fold_rate(folds, tot):
    f = sum(folds[c] for c in BLUFF_CATCH_CLASSES)
    t = sum(tot[c] for c in BLUFF_CATCH_CLASSES)
    return (100.0 * f / t if t else 0.0), t


def _call_rate(calls, tot):
    c = sum(calls[cl] for cl in BLUFF_CATCH_CLASSES)
    t = sum(tot[cl] for cl in BLUFF_CATCH_CLASSES)
    return 100.0 * c / t if t else 0.0


def main():
    print("=== bluff-catch HARD OVERRIDE — TAG vs ManiacOverBluff HU ===")
    arms = {
        "OFF": _run_arm(0.0),
        "ON": _run_arm(1.0),
        "ON+TILTED": _run_arm(1.0, force_tilt_factor=0.0),
    }
    print(f"{'arm':<12}{'bluff-catcher FOLD%':>20}{'CALL%':>9}{'n':>8}{'fired':>8}")
    res = {}
    for name, (folds, calls, tot, fired) in arms.items():
        fr, n = _fold_rate(folds, tot)
        cr = _call_rate(calls, tot)
        nf = sum(fired[c] for c in BLUFF_CATCH_CLASSES)
        res[name] = fr
        print(f"{name:<12}{fr:18.1f}%{cr:8.1f}%{n:>8}{nf:>8}")
    off, on, tilt = res["OFF"], res["ON"], res["ON+TILTED"]
    print()
    print(f"  Δ ON−OFF (fold rate):        {on - off:+.1f}pp   (want strongly −)")
    print(f"  Δ ON+TILTED−OFF (fold rate): {tilt - off:+.1f}pp   (want ≈ 0 — gate holds)")
    ok_behavior = on < off - 10.0
    ok_gate = abs(tilt - off) < 10.0
    print()
    print(f"  behavior change (ON drops bluff-catcher folds): {'PASS' if ok_behavior else 'FAIL'}")
    print(f"  psychology gate (tilt suppresses):              {'PASS' if ok_gate else 'FAIL'}")
    return 0 if (ok_behavior and ok_gate) else 1


if __name__ == "__main__":
    sys.exit(main())
