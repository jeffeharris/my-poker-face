"""Short-stack bb/100 A/B for the 6max RESHOVE table (PUSH_FOLD_6MAX_SCOPE.md
step 7). Reshove OFF vs ON at 8/10/12 BB, hero vs the rule-bot mix, pooled over
seeds. Direction-only (bb/100 is noisy at short stacks); the action-distribution
gate is the primary one and already passed (~17%->98% routing coverage).

Run: docker compose exec -T backend python -m experiments.reshove_bb100_probe
"""

import os
import sys

import experiments.simulate_bb100 as sim
import poker.tiered_bot_controller as tbc

DEPTHS = [8, 10, 12]
HEROES = ["TAG"]
SEEDS = [42, 142, 242]
HANDS_PER_SEED = 2000
BB = 100
# Field: default = the call-happy rule-bot mix; FIELD=competent = openers that
# fold to reshoves more correctly (isolates whether reshove is -EV by ranges or
# by lack of fold equity vs stations).
_FIELD = os.environ.get("FIELD", "rulemix")
COMPETENT = ["GTO-Lite", "ABCBot", "GTO-Lite", "ABCBot", "GTO-Lite"]


def _opponents():
    return COMPETENT if _FIELD == "competent" else sim.DEFAULT_RULE_OPPONENTS


def _run_arm(hero, depth, reshove_on):
    tbc._reshove_6max_enabled = lambda: reshove_on
    st = sim.load_strategy_table()
    deltas = []
    for seed in SEEDS:
        deltas += sim.run_6max_matchup(
            hero,
            HANDS_PER_SEED,
            st,
            big_blind=BB,
            starting_stack=depth * BB,
            base_seed=seed,
            opponents=_opponents(),
        )
    return sim.compute_stats(deltas, BB)


def main():
    print(
        f"RESHOVE bb/100 A/B [{_FIELD}] — {len(SEEDS)}x{HANDS_PER_SEED} hands/arm "
        f"({len(SEEDS) * HANDS_PER_SEED} pooled), vs {_opponents()}"
    )
    print(f"{'hero':5} {'depth':>5}  {'OFF bb/100 (CI)':>24}  {'ON bb/100 (CI)':>24}  {'delta':>8}")
    for hero in HEROES:
        for d in DEPTHS:
            off = _run_arm(hero, d, False)
            on = _run_arm(hero, d, True)
            delta = on.bb100 - off.bb100
            print(
                f"{hero:5} {d:>4}BB  "
                f"{off.bb100:>8.1f} [{off.ci_lo:>6.0f},{off.ci_hi:>6.0f}]  "
                f"{on.bb100:>8.1f} [{on.ci_lo:>6.0f},{on.ci_hi:>6.0f}]  "
                f"{delta:>+8.1f}",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
