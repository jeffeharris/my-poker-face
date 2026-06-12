"""Behavioral proof of the stop-bluffing-vs-station HARD OVERRIDE (step 4).

Proves the re-architected exploit end-to-end on the cleanest target (a pure
CallStation, where station detection is rock-solid). Measures the EXACT class
the override targets — `air_no_draw` (pure air, no equity) — by reading the
controller's own postflop hand_strength off the pipeline snapshot, not the
collapsed node_key bucket the generic probe uses.

Three arms, TAG hero vs CallStation HU:
  - OFF       : exploitation_strength 0.0          → override never fires (control)
  - ON        : exploitation_strength 1.0, composed → override fires (give up air)
  - ON+TILTED : exploitation_strength 1.0, tilt_factor forced 0.0
                → override SUPPRESSED ("can't be on tilt and out-levelling")

Pass criteria:
  - air_no_draw aggression: ON ≪ OFF   (the offset alone moved it 59.9→59.8)
  - air_no_draw aggression: ON+TILTED ≈ OFF  (psychology gate holds)

Run: docker compose exec -T backend python -m experiments.stop_bluff_probe
"""

import sys
from collections import Counter

import experiments.simulate_bb100 as sim
from poker.tiered_bot_controller import TieredBotController

HANDS = 2500
SEED = 42
AGGRO = {"bet", "raise", "all_in"}


def _run_arm(strength, force_tilt_factor=None):
    TieredBotController.exploitation_strength = strength
    agg = Counter()
    tot = Counter()
    orig_dec = TieredBotController._get_postflop_decision
    orig_tilt = TieredBotController._zone_to_tilt_factor

    def wrapped(self, *a, **k):
        d = orig_dec(self, *a, **k)
        snap = getattr(self, "_last_pipeline_snapshot", {}) or {}
        hs = snap.get("hand_strength")
        act = snap.get("resolved_action")
        if hs and act:
            tot[hs] += 1
            if act in AGGRO:
                agg[hs] += 1
        return d

    TieredBotController._get_postflop_decision = wrapped
    if force_tilt_factor is not None:
        TieredBotController._zone_to_tilt_factor = lambda self, emotional_state: force_tilt_factor
    try:
        st = sim.load_strategy_table()
        sim.run_matchup(
            "TAG",
            "CallStation",
            HANDS,
            st,
            big_blind=100,
            starting_stack=10000,
            base_seed=SEED,
        )
    finally:
        TieredBotController._get_postflop_decision = orig_dec
        TieredBotController._zone_to_tilt_factor = orig_tilt
    return agg, tot


def _rate(agg, tot, cls):
    a, t = agg[cls], tot[cls]
    return (100.0 * a / t if t else 0.0), t


def main():
    print("=== stop-bluff HARD OVERRIDE — TAG vs CallStation HU ===")
    arms = {
        "OFF": _run_arm(0.0),
        "ON": _run_arm(1.0),
        "ON+TILTED": _run_arm(1.0, force_tilt_factor=0.0),
    }
    print(f"{'arm':<12}{'air_no_draw aggr':>18}{'air_strong_draw aggr':>22}")
    res = {}
    for name, (agg, tot) in arms.items():
        nd, nnd = _rate(agg, tot, "air_no_draw")
        sd, nsd = _rate(agg, tot, "air_strong_draw")
        res[name] = nd
        print(f"{name:<12}{nd:8.1f}% (n={nnd:<5}){sd:13.1f}% (n={nsd})")
    off, on, tilt = res["OFF"], res["ON"], res["ON+TILTED"]
    print()
    print(f"  Δ ON−OFF (air_no_draw):      {on - off:+.1f}pp   (want strongly −)")
    print(f"  Δ ON+TILTED−OFF (air_no_draw):{tilt - off:+.1f}pp   (want ≈ 0 — gate holds)")
    ok_behavior = on < off - 10.0
    ok_gate = abs(tilt - off) < 10.0
    print()
    print(f"  behavior change (ON drops air bluffs): {'PASS' if ok_behavior else 'FAIL'}")
    print(f"  psychology gate (tilt suppresses):     {'PASS' if ok_gate else 'FAIL'}")
    return 0 if (ok_behavior and ok_gate) else 1


if __name__ == "__main__":
    sys.exit(main())
