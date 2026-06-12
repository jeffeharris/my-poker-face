"""Short-stack bb/100 + ROUTING-COVERAGE A/B for the over-a-limper ISO jam
(PUSH_FOLD_FIRST_IN_OVER_LIMPER_ENABLED; PUSH_FOLD_6MAX_SCOPE.md "Over-a-limper").

The iso path only fires when hero is first-in-to-raise with EXACTLY ONE limper in
front, so the field is one LIMPS_EVERY_HAND fish + four tight folders (the rocks
fold most hands → single-limper spots arise). Primary gate = does the path FIRE
(coverage + action mix); bb/100 is direction-only (noisy at short stacks, and the
spot is rare so the pooled delta is mostly non-iso hands).

Run: docker compose exec -T backend python -m experiments.iso_over_limper_probe
     QUICK=1 ... (1 seed, depth 10 only, coverage only)
"""

import os
import sys

import experiments.simulate_bb100 as sim
import poker.tiered_bot_controller as tbc

DEPTHS = [10, 12]
HERO = "TAG"
SEEDS = [42, 142]
HANDS_PER_SEED = 2000
BB = 100

# One limper + four tight folders → hero frequently faces a single limper.
sim.ARCHETYPES.setdefault(
    "Limper", {"kind": "rule_bot", "strategy": "fish", "fish_leak": "limps_every_hand"}
)
FIELD = ["Limper", "Rock", "Rock", "Rock", "Rock"]

# ── iso-fire instrumentation ────────────────────────────────────────────────
_real_lookup = tbc.lookup_push_fold_action_6max


class _Counter:
    def __init__(self):
        self.reset()

    def reset(self):
        self.total = 0
        self.iso = 0
        self.iso_jam = 0
        self.iso_fold = 0


_C = _Counter()


def _counting_lookup(*args, **kwargs):
    _C.total += 1
    action = _real_lookup(*args, **kwargs)
    if kwargs.get("over_limper"):
        _C.iso += 1
        if action == "jam":
            _C.iso_jam += 1
        elif action == "fold":
            _C.iso_fold += 1
    return action


tbc.lookup_push_fold_action_6max = _counting_lookup


def _run_arm(depth, iso_on):
    tbc._iso_over_limper_enabled = lambda: iso_on
    _C.reset()
    st = sim.load_strategy_table()
    deltas = []
    for seed in SEEDS:
        deltas += sim.run_6max_matchup(
            HERO,
            HANDS_PER_SEED,
            st,
            big_blind=BB,
            starting_stack=depth * BB,
            base_seed=seed,
            opponents=FIELD,
        )
    return sim.compute_stats(deltas, BB), (_C.iso, _C.iso_jam, _C.iso_fold, _C.total)


def main():
    quick = os.environ.get("QUICK")
    depths = [10] if quick else DEPTHS
    seeds_n = 1 if quick else len(SEEDS)
    if quick:
        del SEEDS[1:]
    hands = seeds_n * HANDS_PER_SEED
    print(
        f"ISO-OVER-LIMPER A/B — hero={HERO}, field={FIELD}, "
        f"{seeds_n}x{HANDS_PER_SEED} ({hands}) hands/arm"
    )
    print(
        f"{'depth':>5}  {'OFF bb/100 (CI)':>22}  {'ON bb/100 (CI)':>22}  {'delta':>7}  "
        f"{'iso fires (jam/fold)':>22}"
    )
    for d in depths:
        off, _ = _run_arm(d, False)
        on, cov = _run_arm(d, True)
        iso, jam, fold, total = cov
        delta = on.bb100 - off.bb100
        print(
            f"{d:>4}BB  "
            f"{off.bb100:>8.1f} [{off.ci_lo:>5.0f},{off.ci_hi:>5.0f}]  "
            f"{on.bb100:>8.1f} [{on.ci_lo:>5.0f},{on.ci_hi:>5.0f}]  "
            f"{delta:>+7.1f}  "
            f"{iso:>5} ({jam}/{fold}) of {total} pf",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
