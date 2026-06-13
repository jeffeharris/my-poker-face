"""Behavior probe: does the light-3-bet counter FIRE vs a fold-happy opener
(nit_3bet_folder), adding 3-bets the base chart would fold/flat — after the
fold_to_3bet read matures?"""

import os
import sys
from collections import Counter

import experiments.simulate_bb100 as sim
from poker.human_clone import load_profile_from_file, register_clone_strategy
from poker.tiered_bot_controller import TieredBotController

CLONE_DIR = os.path.join(os.path.dirname(__file__), "clone_profiles")
HANDS = int(__import__("os").environ.get("H3_HANDS", "6000"))


def reg(name):
    p = load_profile_from_file(os.path.join(CLONE_DIR, f"{name}.json"))
    src = p.source_player
    register_clone_strategy(f"clone_{src.lower()}", p)
    ak = f"{src}_clone"
    sim.ARCHETYPES[ak] = {"kind": "rule_bot", "strategy": src and f"clone_{src.lower()}"}
    return ak


def run(knob):
    TieredBotController.light_3bet = knob
    c = Counter()
    acts = Counter()
    orig = TieredBotController._get_ai_decision

    def w(self, *a, **k):
        d = orig(self, *a, **k)
        snap = getattr(self, "_last_pipeline_snapshot", {}) or {}
        lt = snap.get("light_3bet")
        if lt is not None:
            c["considered"] += 1
            if lt.get("fired"):
                c["fired"] += 1
                acts[snap.get("resolved_action", "?")] += 1
        return d

    TieredBotController._get_ai_decision = w
    try:
        st = sim.load_strategy_table()
        sim.run_matchup(
            "TAG",
            reg("nit_3bet_folder"),
            HANDS,
            st,
            big_blind=100,
            starting_stack=10000,
            base_seed=42,
        )
    finally:
        TieredBotController._get_ai_decision = orig
    print(
        f"  knob={knob}: vs_open+foldy-read spots considered={c['considered']}  light-3bet FIRED={c['fired']}  resolved_actions={dict(acts)}"
    )


def main():
    print(f"=== light-3-bet counter — TAG vs nit_3bet_folder HU ({HANDS} hands) ===")
    run(0.0)
    run(0.85)
    print("\nWANT: knob 0 fires 0; knob 0.85 fires >0 with resolved_action a raise (3bet added)")


if __name__ == "__main__":
    sys.exit(main())
