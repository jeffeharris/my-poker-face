"""Behavioral validation for blind squeeze-defense (VS_SQUEEZE_DEFENSE_HANDOFF).

Confirms the feature FIRES and produces sane behavior BEFORE any long bb/100 run
(feedback_measure_spot_before_building / "quick-pass-confirm-it-FIRES first"). It does
NOT re-price EV — the defend tiers were derived from vs_squeeze_ev_probe's +EV sets, so
the question here is purely: with the flag ON + a sharp hero, do the blinds stop folding
100% to a squeeze, continue the value FLOOR unconditionally, and WIDEN vs the maniacs?

Two arms over the handoff field ([Maniac, Maniac, LAG, Rock, Rock]):
  OFF: flag off, knob 0  → blinds fold ~100% of squeeze spots (today's behavior)
  ON : flag forced on, hero knob 0.85 (shark) → floor continues + read-gated widen

Sims bypass __init__ (knob defaults 0), so ON sets the knob at the class level and
forces `_vs_squeeze_defense_enabled` True — mirroring stop_bluff_probe's
`exploitation_strength` override. The hero's opponent_model_manager (attached by
run_6max_matchup) matures the maniacs' VPIP so the widen read fires.

Pass criteria:
  - OFF blind-squeeze continue% ≈ 0
  - ON  continues the value FLOOR (AA/KK/QQ/AK) ~100% of the time it holds one
  - ON  continue% rises with the squeezer's VPIP (widen is read-gated, not blanket)

Run: docker compose exec -T backend python3 -m experiments.vs_squeeze_defense_validate
     HANDS=1500 SEEDS=7,107 ...
"""

from __future__ import annotations

import os
import sys
from collections import Counter

import experiments.simulate_bb100 as sim
import poker.tiered_bot_controller as tbc
from poker.tiered_bot_controller import TieredBotController

HERO = "TAG"
FIELD = ["Maniac", "Maniac", "LAG", "Rock", "Rock"]
SEEDS = [7, 107]
HANDS_PER_SEED = 1500
BB = 100
FLOOR = set(tbc.SQUEEZE_DEFENSE_TIERS[0])  # AA/KK/QQ/AKs/AKo — continues vs any squeeze


class _Rec:
    def __init__(self):
        self.reset()

    def reset(self):
        self.spots = 0
        self.continued = 0
        self.floor_spots = 0
        self.floor_continued = 0
        self.by_vpip_band = Counter()  # band -> [spots, continued]
        self.by_vpip_cont = Counter()
        self.action = Counter()  # resolved action among blind squeeze spots


_R = _Rec()
_orig = TieredBotController._get_ai_decision


def _wrapped(self, message, **context):
    result = _orig(self, message, **context)
    # HERO-ONLY: in the 6-max harness only the hero seat gets an
    # opponent_model_manager attached, so the read/widen can only fire for it.
    # Recording every TieredBot (all 6 seats are tiered) would pollute the readout
    # with manager-less opponent decisions (which only ever hit the no-read floor).
    if getattr(self, "opponent_model_manager", None) is None:
        return result
    snap = getattr(self, "_last_pipeline_snapshot", {}) or {}
    if snap.get("phase") != "PRE_FLOP":
        return result
    parts = snap.get("node_key", "").split("|")
    if len(parts) != 4 or parts[0] != "vs_squeeze" or parts[1] not in ("BB", "SB"):
        return result
    if snap.get("chart_lookup_source") not in ("miss", "masked_out"):
        return result
    hand = parts[3]
    _R.spots += 1
    vsd = snap.get("vs_squeeze_defense") or {}
    continued = bool(vsd.get("continued"))
    action = snap.get("resolved_action", "?")
    _R.action[action] += 1
    if continued:
        _R.continued += 1
    if hand in FLOOR:
        _R.floor_spots += 1
        if continued:
            _R.floor_continued += 1
    vpip = vsd.get("squeezer_vpip")
    band = "no-read" if vpip is None else f"{int(vpip * 10) * 10:>2}-{int(vpip * 10) * 10 + 10}%"
    _R.by_vpip_band[band] += 1
    if continued:
        _R.by_vpip_cont[band] += 1
    return result


def _run_arm(on: bool) -> None:
    _R.reset()
    TieredBotController._get_ai_decision = _wrapped
    orig_enabled = tbc._vs_squeeze_defense_enabled
    had_attr = "vs_squeeze_defense" in TieredBotController.__dict__
    try:
        if on:
            tbc._vs_squeeze_defense_enabled = lambda: True
            TieredBotController.vs_squeeze_defense = 0.85  # shark, class-level default
        else:
            tbc._vs_squeeze_defense_enabled = lambda: False
            TieredBotController.vs_squeeze_defense = 0.0
        st = sim.load_strategy_table()
        for seed in SEEDS:
            sim.run_6max_matchup(
                HERO, HANDS_PER_SEED, st, big_blind=BB, starting_stack=100 * BB,
                base_seed=seed, opponents=FIELD,
            )
    finally:
        TieredBotController._get_ai_decision = _orig
        tbc._vs_squeeze_defense_enabled = orig_enabled
        if not had_attr and "vs_squeeze_defense" in TieredBotController.__dict__:
            del TieredBotController.vs_squeeze_defense


def _report(label):
    spots = _R.spots
    cont = _R.continued
    print(f"\n[{label}] blind vs_squeeze spots: {spots}")
    if not spots:
        print("  (none — field didn't squeeze the hero in the blinds)")
        return 0.0
    print(f"  continue%: {100.0 * cont / spots:.1f}%   action mix: {dict(_R.action)}")
    if _R.floor_spots:
        print(
            f"  VALUE FLOOR (AA/KK/QQ/AK): {_R.floor_continued}/{_R.floor_spots} continued "
            f"= {100.0 * _R.floor_continued / _R.floor_spots:.0f}%"
        )
    print("  continue% by squeezer VPIP read (widen should rise with width):")
    for band in sorted(_R.by_vpip_band):
        n = _R.by_vpip_band[band]
        c = _R.by_vpip_cont[band]
        print(f"    {band:>8}: {c:>4}/{n:<4} = {100.0 * c / n:.0f}%")
    return 100.0 * cont / spots


def main() -> int:
    global HANDS_PER_SEED
    if os.environ.get("HANDS"):
        HANDS_PER_SEED = int(os.environ["HANDS"])
    if os.environ.get("SEEDS"):
        SEEDS[:] = [int(s) for s in os.environ["SEEDS"].split(",")]
    print("=" * 84)
    print("vs_squeeze DEFENSE — behavioral validation (does it FIRE + widen?)")
    print(f"hero={HERO} field={FIELD} {len(SEEDS)}x{HANDS_PER_SEED} hands/arm")
    print("=" * 84)

    _run_arm(on=False)
    off_cont = _report("OFF")
    _run_arm(on=True)
    on_cont = _report("ON")

    floor_ok = _R.floor_spots == 0 or _R.floor_continued / _R.floor_spots >= 0.95
    fires = on_cont > off_cont + 1.0
    print("\n" + "=" * 84)
    print(f"  OFF continue% ≈ 0:        {'PASS' if off_cont < 1.0 else 'FAIL'} ({off_cont:.1f}%)")
    print(f"  ON fires (continue ↑):    {'PASS' if fires else 'FAIL'} "
          f"({off_cont:.1f}% → {on_cont:.1f}%)")
    print(f"  value floor continues:    {'PASS' if floor_ok else 'FAIL'}")
    print("  (widen-rises-with-VPIP: eyeball the by-band table above — should trend up.)")
    return 0 if (off_cont < 1.0 and fires and floor_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
