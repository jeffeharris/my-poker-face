"""Phase 8 v1 diagnostic sim — runs focused matchups across seeds and
dumps the playstyle-gated rule counters alongside the legacy
exploitation + value_override counters for Risk #1 inspection.

Reuses the 6-max matchup runner from simulate_bb100 but keeps the
OpponentModelManager visible at the end so we can inspect the
diagnostic counters Phase 8 added (eligible / enabled_eligible /
fired / superseded_by_override / diagnostic_only / blocked_by_bias_floor,
per archetype + rule family), plus the pre-existing legacy counters
(detected_<pattern>, value_override_fired, etc).

Also tracks per-opponent chip deltas so we can confirm whether
Phase 8 is shrinking the CaseBot drain specifically (the plan's
per-opponent decomposition metric).

Usage:
    docker compose exec backend python -m experiments.phase_8_diagnostics
        [--hero TAG] [--opponents CaseBot,CaseBot,ABCBot,ABCBot,GTO-Lite]
        [--hands 200] [--seeds 42,142,242] [--adaptation-bias 0.85]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from typing import Dict, List, Optional

from tqdm import tqdm

# Reuse simulate_bb100 helpers to avoid copy-paste drift.
from experiments.simulate_bb100 import (
    ARCHETYPES,
    _make_seat_names,
    apply_adaptation_bias_override,
    load_strategy_table,
    make_controller,
    make_game_state,
    run_hand,
)
from poker.memory.opponent_model import OpponentModelManager
from poker.poker_state_machine import PokerStateMachine

PHASE_8_PREFIXES = (
    'value_vs_station_',
)


def run_diagnostic_matchup(
    *,
    hero_archetype: str,
    opponents: List[str],
    n_hands: int,
    seed: int,
    hero_adaptation_bias: Optional[float],
    big_blind: int = 100,
    starting_stack: int = 10000,
) -> tuple[List[float], Dict[str, float], OpponentModelManager]:
    """Return (per-hand hero deltas, per-opponent total deltas, manager).

    Generic over table size: pass 1 opponent for HU, 5 for 6-max.
    Mirrors run_6max_matchup but keeps the manager visible to the caller
    so Phase 8 counters can be inspected, and tracks per-opponent chip
    deltas across the run so we can answer "did the CaseBot drain shrink"
    independently of headline bb/100.
    """
    if len(opponents) < 1:
        raise ValueError("opponents must have at least 1 entry")
    table_size = 1 + len(opponents)

    strategy_table = load_strategy_table()

    hero_name = hero_archetype if hero_archetype not in opponents else f"{hero_archetype}_hero"
    opponent_seats = _make_seat_names(opponents)
    if hero_name in opponent_seats:
        hero_name = f"{hero_archetype}_hero"
    all_names = [hero_name] + opponent_seats

    config_arch = apply_adaptation_bias_override(ARCHETYPES[hero_archetype], hero_adaptation_bias)
    opp_configs = [ARCHETYPES[o] for o in opponents]
    opp_desc = '+'.join(opponents) if len(set(opponents)) > 1 else f'5x {opponents[0]}'

    deltas: List[float] = []
    opp_deltas: Dict[str, float] = {seat: 0.0 for seat in opponent_seats}
    opponent_manager = OpponentModelManager()

    for hand_num in tqdm(
        range(n_hands),
        desc=f"  {hero_archetype} vs {opp_desc}",
        leave=False,
        file=sys.stderr,
    ):
        hand_seed = seed + hand_num
        dealer_idx = hand_num % table_size

        gs = make_game_state(
            player_names=all_names,
            big_blind=big_blind,
            starting_stack=starting_stack,
            dealer_idx=dealer_idx,
            seed=hand_seed,
        )
        sm = PokerStateMachine(gs)

        controllers = [
            make_controller(
                hero_name,
                config_arch,
                strategy_table,
                sm,
                rng_seed=hand_seed,
            )
        ]
        for i, (seat, cfg) in enumerate(zip(opponent_seats, opp_configs, strict=False)):
            controllers.append(
                make_controller(
                    seat,
                    cfg,
                    strategy_table,
                    sm,
                    rng_seed=hand_seed + 1_000_000 * (i + 1),
                )
            )

        controllers[0].opponent_model_manager = opponent_manager
        opponent_manager.record_hand_dealt(
            observer=hero_name,
            opponents=opponent_seats,
            hand_number=hand_num,
        )

        final_stacks = run_hand(
            sm,
            controllers,
            big_blind,
            verbose=False,
            opponent_manager=opponent_manager,
            hero_name=hero_name,
            hand_number=hand_num,
        )
        delta = final_stacks.get(hero_name, starting_stack) - starting_stack
        deltas.append(delta)
        for seat in opponent_seats:
            opp_deltas[seat] += final_stacks.get(seat, starting_stack) - starting_stack

    return deltas, opp_deltas, opponent_manager


def summarize_phase_8_counters(counters: Counter) -> Dict[str, Dict[str, int]]:
    """Group counters by rule family + counter type for readable output."""
    grouped: Dict[str, Dict[str, int]] = {}
    for key, value in counters.items():
        if not key.startswith(PHASE_8_PREFIXES):
            continue
        # Key shape: '<family>_<counter>_<archetype>'.
        # Family is value_vs_station; archetype is
        # always the last underscore-segment.
        parts = key.split('_')
        archetype = parts[-1]
        rest = '_'.join(parts[:-1])
        grouped.setdefault(archetype, {})[rest] = value
    return grouped


LEGACY_KEYS_OF_INTEREST = (
    'decisions',
    'cold_start',
    'fired',
    'detected_but_no_fire',
    'no_pattern_matched',
    'detected_hyper_aggressive',
    'detected_hyper_passive',
    'detected_passive_with_jams',
    'detected_tight_nit',
    'detected_high_fold_to_cbet',
    'hyper_passive_fold_mass_suppressed',
    'fired_high_fold_to_cbet',
    'fired_multiway_cbet',
    'multiway_cbet_opportunity_logged',
    'flop_as_preflop_aggressor_spots',
    'heads_up_cbet_spots',
    'value_override_eligible_strong',
    'value_override_eligible_aggro',
    'value_override_fired',
    'bluff_catch_eligible',
    'bluff_catch_fired',
)


def print_single_seed_report(
    *,
    hero_archetype: str,
    opponents: List[str],
    n_hands: int,
    seed: int,
    deltas: List[float],
    opp_deltas: Dict[str, float],
    counters: Counter,
    big_blind: int = 100,
):
    print("=" * 72)
    print(f"Seed {seed} — {hero_archetype} vs {opponents}, hands={n_hands}")
    print("=" * 72)

    total_delta = sum(deltas)
    bb100 = (total_delta / big_blind) / (n_hands / 100) if n_hands > 0 else 0
    print(f"\n  Net result: {total_delta:+.0f} chips ({bb100:+.1f} bb/100)")

    print("\n  Per-opponent deltas (hero pays positive opponent deltas):")
    for seat, d in sorted(opp_deltas.items(), key=lambda kv: kv[1], reverse=True):
        print(f"    {seat:<20} {d:+8.0f} chips")

    print(f"\n  Decisions tallied: {counters.get('decisions', 0)}")

    print("\n  Phase 8 counters:")
    grouped = summarize_phase_8_counters(counters)
    if not grouped:
        print("    (no Phase 8 counters fired)")
    else:
        for arch, by_counter in grouped.items():
            for fam in ('value_vs_station',):
                fam_counters = {
                    k.replace(f'{fam}_', ''): v
                    for k, v in by_counter.items()
                    if k.startswith(fam + '_')
                }
                if not fam_counters:
                    continue
                print(f"    {arch}/{fam}:")
                for name in (
                    'eligible',
                    'enabled_eligible',
                    'diagnostic_only',
                    'fired',
                    'superseded_by_override',
                    'blocked_by_bias_floor',
                ):
                    v = fam_counters.get(name, 0)
                    print(f"      {name:<32} {v:>6}")

    print("\n  Legacy exploitation + override counters:")
    for key in LEGACY_KEYS_OF_INTEREST:
        v = counters.get(key, 0)
        if v == 0:
            continue
        print(f"    {key:<40} {v:>6}")

    # Identity checks
    if grouped:
        print("\n  Identity checks:")
        for arch, by_counter in grouped.items():
            for fam in ('value_vs_station',):
                eligible = by_counter.get(f'{fam}_eligible', 0)
                if eligible == 0:
                    continue
                enabled = by_counter.get(f'{fam}_enabled_eligible', 0)
                diag = by_counter.get(f'{fam}_diagnostic_only', 0)
                fired = by_counter.get(f'{fam}_fired', 0)
                superseded = by_counter.get(f'{fam}_superseded_by_override', 0)
                blocked = by_counter.get(f'{fam}_blocked_by_bias_floor', 0)
                ok1 = eligible == enabled + diag
                ok2 = enabled == fired + superseded + blocked
                print(
                    f"    {arch}/{fam}: "
                    f"{'OK' if ok1 and ok2 else 'MISMATCH'} "
                    f"(eligible={eligible}, enabled={enabled}, "
                    f"diag={diag}, fired={fired}, super={superseded}, "
                    f"bias_block={blocked})"
                )


def print_multi_seed_summary(
    *,
    hero_archetype: str,
    opponents: List[str],
    n_hands: int,
    seeds: List[int],
    per_seed: List[Dict],
    big_blind: int = 100,
):
    """Cross-seed aggregate of bb/100, per-opponent deltas, and Phase 8
    firing rates. Lets us compare against the documented baselines
    without re-running the plan's full sweep.
    """
    print("\n" + "=" * 72)
    print(f"MULTI-SEED SUMMARY — {hero_archetype} vs {opponents}")
    print(f"  hands={n_hands} each, seeds={seeds}")
    print("=" * 72)

    print("\n  Headline bb/100 per seed:")
    bb100s = []
    for entry in per_seed:
        total_delta = sum(entry['deltas'])
        bb100 = (total_delta / big_blind) / (n_hands / 100)
        bb100s.append(bb100)
        print(
            f"    seed={entry['seed']:>4}  {bb100:+8.1f} bb/100  " f"(net {total_delta:+.0f} chips)"
        )
    mean_bb = sum(bb100s) / len(bb100s) if bb100s else 0.0
    print(
        f"\n    mean bb/100: {mean_bb:+.1f}  " f"(range {min(bb100s):+.1f} to {max(bb100s):+.1f})"
    )

    print("\n  Per-opponent mean delta across seeds (negative = hero loses to this seat):")
    seats = sorted(per_seed[0]['opp_deltas'].keys())
    for seat in seats:
        per_seed_chips = [e['opp_deltas'][seat] for e in per_seed]
        # opp_deltas are opponent gains, so HERO's loss to that seat
        # is just opp_deltas (they took chips, hero loses them). Flip
        # sign for "hero pays this opponent."
        mean_chips = sum(per_seed_chips) / len(per_seed_chips)
        print(
            f"    {seat:<20} mean {mean_chips:+8.0f} chips  "
            f"(per-seed: {', '.join(f'{x:+.0f}' for x in per_seed_chips)})"
        )

    print("\n  Phase 8 firing rate (across seeds, TAG decisions):")
    fired_total = 0
    decisions_total = 0
    for entry in per_seed:
        c = entry['counters']
        fired_total += c.get(f'value_vs_station_fired_{hero_archetype.lower()}', 0)
        decisions_total += c.get('decisions', 0)
    if decisions_total:
        rate = 100.0 * fired_total / decisions_total
        print(
            f"    value_vs_station_fired_{hero_archetype.lower()}: "
            f"{fired_total} / {decisions_total} decisions ({rate:.1f}%)"
        )

    print("\n  Risk #1 cross-check — hyper_passive firing rate:")
    hp_detected = sum(e['counters'].get('detected_hyper_passive', 0) for e in per_seed)
    fired_generic = sum(e['counters'].get('fired', 0) for e in per_seed)
    print(f"    detected_hyper_passive: {hp_detected}")
    print(f"    fired (legacy any-rule):  {fired_generic}")

    print("\n  Value override (Phase 6.5) cross-check:")
    vo_fired = sum(e['counters'].get('value_override_fired', 0) for e in per_seed)
    vo_strong = sum(e['counters'].get('value_override_eligible_strong', 0) for e in per_seed)
    print(f"    value_override_eligible_strong: {vo_strong}")
    print(f"    value_override_fired:           {vo_fired}")


def main():
    parser = argparse.ArgumentParser(
        description='Phase 8 v1 diagnostic sim — print rule-family counters',
    )
    parser.add_argument(
        '--hero',
        default='TAG',
        help='Archetype occupying the hero seat (default: TAG)',
    )
    parser.add_argument(
        '--opponents',
        default='CaseBot,CaseBot,ABCBot,ABCBot,GTO-Lite',
        help='Comma-separated 5 opponents (default: 2xCaseBot 2xABCBot GTO-Lite)',
    )
    parser.add_argument('--hands', type=int, default=200)
    parser.add_argument(
        '--seeds',
        default='42',
        help='Comma-separated seeds (default: 42). Plan recommends 42,142,242.',
    )
    parser.add_argument(
        '--adaptation-bias',
        type=float,
        default=0.85,
        help='Hero adaptation_bias override (default: 0.85)',
    )
    parser.add_argument(
        '--disable-phase-8',
        action='store_true',
        help='Control run: monkey-patch VALUE_VS_STATION_PLAYSTYLES to '
        'an empty frozenset before any hand runs, so the rule '
        'never fires and we get a baseline comparison.',
    )
    args = parser.parse_args()

    if args.disable_phase_8:
        import poker.strategy.exploitation as _exp

        _exp.VALUE_VS_STATION_PLAYSTYLES = frozenset()
        print("[control] Phase 8 disabled — VALUE_VS_STATION_PLAYSTYLES=frozenset()")

    opponents = [o.strip() for o in args.opponents.split(',')]
    seeds = [int(s.strip()) for s in args.seeds.split(',')]

    per_seed: List[Dict] = []
    for seed in seeds:
        deltas, opp_deltas, manager = run_diagnostic_matchup(
            hero_archetype=args.hero,
            opponents=opponents,
            n_hands=args.hands,
            seed=seed,
            hero_adaptation_bias=args.adaptation_bias,
        )
        counters = getattr(manager, '_exploitation_counters', Counter())
        print_single_seed_report(
            hero_archetype=args.hero,
            opponents=opponents,
            n_hands=args.hands,
            seed=seed,
            deltas=deltas,
            opp_deltas=opp_deltas,
            counters=counters,
        )
        per_seed.append(
            {
                'seed': seed,
                'deltas': deltas,
                'opp_deltas': opp_deltas,
                'counters': counters,
            }
        )

    if len(seeds) > 1:
        print_multi_seed_summary(
            hero_archetype=args.hero,
            opponents=opponents,
            n_hands=args.hands,
            seeds=seeds,
            per_seed=per_seed,
        )


if __name__ == '__main__':
    main()
