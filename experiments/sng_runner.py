#!/usr/bin/env python3
"""Single-table Winner-Take-All SNG eval (docs/plans/EVAL_HARNESS_PLAN.md §P1).

The honest, gold-standard absolute eval: equal starting stacks, **escalating
blinds**, **elimination**, **play to one winner**, **win-rate** — the structure
that matches the real game, not fixed-depth bb/100. It exercises the whole depth
progression (100bb → 50 → 25 → push/fold) that fixed-depth runs never touch, and
because it is winner-take-all, chip-EV = $-EV so accumulation and survival are
rewarded correctly.

The poker engine already does the hard parts (verified): one `PokerStateMachine`
plays continuously across hands — `hand_over_transition` carries stacks, drops
busted players (`reset_game_state_for_new_hand` filters `stack > 0`), rotates the
button over survivors, and escalates blinds via `BlindConfig`; heads-up blind
posting is handled. So this runner is a thin driver over that engine plus
win-rate bookkeeping. The per-hand action loop is reused from
`champion_challenger.run_cc_hand` (multistreet-aware, drives every seat).

Two modes (the field / the gate, by win-rate):
  - **field**  — N archetypes at the table; which archetype wins SNGs? The WTA
    analog of the Baseline-vs-TAG/LAG/Rock/Nit/GTO-Lite self-play check.
  - **champion_challenger** — N seats split challenger (change ON) / champion
    (change OFF), all one archetype; challenger-group win-rate vs the
    n_challenger/N null. The WTA-correct version of the P0 gate.

Usage:
    # field: which archetype wins single-table 6-max SNGs?
    docker compose exec backend python -m experiments.sng_runner \\
        --mode field --field Baseline,TAG,LAG,Rock,Nit,GTO-Lite --sngs 400

    # gate: does enabling multistreet win more SNGs than leaving it off?
    docker compose exec backend python -m experiments.sng_runner \\
        --mode champion_challenger --change multistreet --sngs 400
"""

import argparse
import logging
import math
import os
import random
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)

from experiments.champion_challenger import (
    CHANGES,
    _apply_flags,
    _challenger_seat_indices,
    run_cc_hand,
)
from experiments.simulate_bb100 import ARCHETYPES, TERMINAL_PHASES, make_controller, make_game_state
from poker.poker_state_machine import PokerPhase, PokerStateMachine
from poker.strategy.strategy_table import load_strategy_table

# A turbo-ish ramp: start 100bb deep, +50% every 10 hands. Over a 6-handed SNG
# this walks stacks down through ~50/25/push-fold and reliably ends in well
# under MAX_HANDS, so the runner exercises the full depth progression P0 misses.
DEFAULT_BLIND = {'growth': 1.5, 'hands_per_level': 10, 'max_blind': 0}
MAX_HANDS = 1000  # hard safety cap; escalating blinds end real SNGs far sooner


# ── One SNG ─────────────────────────────────────────────────────────────────


def play_sng(
    seat_specs: List[Tuple[str, dict, object, dict]],
    blind_config: dict,
    starting_stack: int,
    big_blind: int,
    sng_seed: int,
    max_hands: int = MAX_HANDS,
) -> Tuple[Optional[str], int, Dict[str, int]]:
    """Play one single-table WTA SNG to a winner.

    `seat_specs` is one (name, archetype_config, strategy_table, flags) per seat.
    Controllers are built once and persist across hands (the SM carries stacks);
    the engine drops busted players at each hand-over. Returns
    (winner_name, hands_played, final_stacks); winner is the lone survivor (or
    chip leader if `max_hands` is hit, which shouldn't happen with escalating
    blinds). `final_stacks` maps each surviving seat name to its stack — under
    WTA with no rake the winner holds every chip at a clean finish.
    """
    names = [s[0] for s in seat_specs]
    gs = make_game_state(
        player_names=names,
        big_blind=big_blind,
        starting_stack=starting_stack,
        dealer_idx=0,
        seed=sng_seed,
    )
    # record_snapshots=False: this table lives for the whole tournament, so the
    # per-transition snapshot tuple would grow unbounded otherwise.
    sm = PokerStateMachine(gs, blind_config=blind_config, record_snapshots=False)
    sm.current_hand_seed = sng_seed

    controllers = []
    for i, (name, cfg, table, flags) in enumerate(seat_specs):
        ctrl = make_controller(name, cfg, table, sm, rng_seed=sng_seed + 1_000_000 * i)
        # Tiered/baseline seats touch opponent_model_manager (no-op at
        # anchors=None); the bypassed __init__ never set it. Rule bots ignore it.
        ctrl.opponent_model_manager = None
        _apply_flags(ctrl, flags)
        controllers.append(ctrl)

    hand_count = 0
    while hand_count < max_hands:
        if len([p for p in sm.game_state.players if p.stack > 0]) <= 1:
            break
        # Per-hand global-random seed so rule-bot / clone draws are reproducible
        # (the deck is seeded separately via the SM's own hand-seed progression).
        random.seed(sng_seed * 1_000_003 + hand_count)
        run_cc_hand(sm, controllers, big_blind)
        # run_cc_hand stops at HAND_OVER; one advance fires hand_over_transition
        # → drops busted players, rotates button, escalates blinds, deals next.
        if sm.phase == PokerPhase.HAND_OVER:
            sm.advance_state()
        hand_count += 1

    survivors = [p for p in sm.game_state.players if p.stack > 0]
    final_stacks = {p.name: p.stack for p in survivors}
    if not survivors:
        return None, hand_count, final_stacks
    winner = max(survivors, key=lambda p: p.stack).name
    return winner, hand_count, final_stacks


# ── Seat construction per mode ──────────────────────────────────────────────


def _field_seat_specs(field: List[str], table, rotation: int) -> List[Tuple[str, dict, object, dict]]:
    """One archetype per seat, rotated by `rotation` so the field's starting
    seats (and thus first-button assignment) vary across SNGs."""
    rotated = field[rotation:] + field[:rotation]
    specs = []
    seen: Counter = Counter()
    for arch in rotated:
        seen[arch] += 1
        # Unique seat name even when the field repeats an archetype.
        name = f"{arch}#{seen[arch]}"
        specs.append((name, ARCHETYPES[arch], table, {}))
    return specs


def _cc_seat_specs(
    change: str, n_seats: int, n_challenger: int, champion_table, challenger_table, archetype: str
) -> Tuple[List[Tuple[str, dict, object, dict]], set]:
    """N seats of one archetype, split challenger (change ON) / champion (OFF)."""
    spec = CHANGES[change]
    challenger_idx = set(_challenger_seat_indices(n_seats, n_challenger))
    arch_cfg = ARCHETYPES[archetype]
    specs = []
    for i in range(n_seats):
        is_chal = i in challenger_idx
        name = f"{'CHAL' if is_chal else 'CHMP'}_{i}"
        specs.append(
            (
                name,
                arch_cfg,
                challenger_table if is_chal else champion_table,
                spec.challenger_flags if is_chal else spec.champion_flags,
            )
        )
    challenger_names = {specs[i][0] for i in challenger_idx}
    return specs, challenger_names


# ── Workers (ProcessPool: each runs a batch of SNGs) ────────────────────────


def _field_worker(args) -> Counter:
    field, blind_config, starting_stack, big_blind, seed_start, count = args
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)
    table = load_strategy_table()
    wins: Counter = Counter()
    for k in range(count):
        seed = seed_start + k
        specs = _field_seat_specs(field, table, rotation=seed % len(field))
        winner, _, _ = play_sng(specs, blind_config, starting_stack, big_blind, seed)
        if winner is not None:
            # Strip the "#n" seat suffix back to the archetype.
            wins[winner.rsplit('#', 1)[0]] += 1
    return wins


def _cc_worker(args) -> Tuple[int, int]:
    change, n_seats, n_challenger, archetype, blind_config, starting_stack, big_blind, seed_start, count = args
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)
    spec = CHANGES[change]
    champion_table = spec.champion_table()
    challenger_table = spec.challenger_table()
    specs, challenger_names = _cc_seat_specs(
        change, n_seats, n_challenger, champion_table, challenger_table, archetype
    )
    chal_wins = 0
    total = 0
    for k in range(count):
        seed = seed_start + k
        winner, _, _ = play_sng(specs, blind_config, starting_stack, big_blind, seed)
        if winner is None:
            continue
        total += 1
        if winner in challenger_names:
            chal_wins += 1
    return chal_wins, total


def _split(n_sngs: int, base_seed: int) -> List[Tuple[int, int]]:
    """Split n_sngs into one (seed_start, count) chunk per worker."""
    workers = min(os.cpu_count() or 1, max(1, n_sngs))
    base = n_sngs // workers
    rem = n_sngs % workers
    chunks = []
    cursor = base_seed
    for w in range(workers):
        count = base + (1 if w < rem else 0)
        if count:
            chunks.append((cursor, count))
            cursor += count
    return chunks


# ── Reporting ────────────────────────────────────────────────────────────────


def _wilson(wins: int, n: int) -> Tuple[float, float, float]:
    """Wilson 95% CI for a proportion (robust near 0/1 and small n)."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = wins / n
    z = 1.96
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return p, max(0.0, center - half), min(1.0, center + half)


def report_field(field: List[str], wins: Counter, n_sngs: int):
    null = 1.0 / len(field)
    print("\n" + "=" * 70)
    print(f"WTA-SNG FIELD: {n_sngs} single-table SNGs | seats={len(field)}")
    print(f"  field: {', '.join(field)}")
    print(f"  null (equal skill): each archetype wins {100*null:.1f}%")
    print("=" * 70)
    print(f"\n  {'archetype':<14} {'wins':>5} {'win%':>7}  {'95% CI':>16}")
    for arch, _ in sorted(wins.items(), key=lambda kv: -kv[1]):
        p, lo, hi = _wilson(wins[arch], n_sngs)
        flag = ''
        if lo > null:
            flag = '  ✅ > null'
        elif hi < null:
            flag = '  ❌ < null'
        print(f"  {arch:<14} {wins[arch]:>5} {100*p:>6.1f}%  [{100*lo:>4.1f},{100*hi:>4.1f}]{flag}")
    # Any archetype not present never won.
    for arch in field:
        if arch not in wins:
            print(f"  {arch:<14} {0:>5} {0.0:>6.1f}%  (never won)")


def report_cc(change: str, n_seats: int, n_challenger: int, chal_wins: int, total: int):
    spec = CHANGES[change]
    null = n_challenger / n_seats
    p, lo, hi = _wilson(chal_wins, total)
    print("\n" + "=" * 70)
    print(f"WTA-SNG CHAMPION vs CHALLENGER: change={change!r}")
    print(f"  {spec.description}")
    print(f"  {n_challenger} challenger vs {n_seats - n_challenger} champion seats | {total} SNGs")
    print(f"  null (equal skill): challenger group wins {100*null:.1f}% of SNGs")
    print("=" * 70)
    print(f"\n  challenger win-rate: {100*p:.1f}%  ({chal_wins}/{total})  95% CI [{100*lo:.1f}, {100*hi:.1f}]")
    if lo > null:
        verdict = "✅ CI-CLEAR ABOVE null — challenger wins more SNGs (real improvement)"
    elif hi < null:
        verdict = "❌ CI-CLEAR BELOW null — challenger wins fewer SNGs (regression)"
    else:
        verdict = "➖ INCONCLUSIVE — CI spans the null (need more SNGs, or no real effect)"
    print(f"  VERDICT: {verdict}")


def _run_pool(worker, work):
    if len(work) > 1:
        with ProcessPoolExecutor(max_workers=len(work)) as ex:
            return list(ex.map(worker, work))
    return [worker(work[0])]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['field', 'champion_challenger'], default='field')
    p.add_argument(
        '--field',
        default='Baseline,TAG,LAG,Rock,Nit,GTO-Lite',
        help='field mode: comma-separated archetypes, one per seat',
    )
    p.add_argument('--change', choices=sorted(CHANGES), help='champion_challenger mode: the change to A/B')
    p.add_argument('--archetype', default='Baseline', help='champion_challenger mode: archetype for all seats')
    p.add_argument('--seats', type=int, default=6, help='champion_challenger mode: table size')
    p.add_argument('--challenger-seats', type=int, default=3, help='champion_challenger mode: seats with change ON')
    p.add_argument('--sngs', type=int, default=400, help='number of SNGs to run')
    p.add_argument('--seed', type=int, default=42, help='base seed')
    p.add_argument('--start-bb', type=int, default=100, help='starting stack in big blinds (bb=100)')
    p.add_argument('--blind-growth', type=float, default=DEFAULT_BLIND['growth'])
    p.add_argument('--hands-per-level', type=int, default=DEFAULT_BLIND['hands_per_level'])
    p.add_argument('--max-blind', type=int, default=DEFAULT_BLIND['max_blind'])
    args = p.parse_args()

    big_blind = 100
    starting_stack = args.start_bb * big_blind
    blind_config = {
        'growth': args.blind_growth,
        'hands_per_level': args.hands_per_level,
        'max_blind': args.max_blind,
    }

    if args.mode == 'field':
        field = [a.strip() for a in args.field.split(',')]
        for a in field:
            if a not in ARCHETYPES:
                print(f"Unknown archetype: {a}")
                sys.exit(1)
        work = [
            (field, blind_config, starting_stack, big_blind, start, count)
            for start, count in _split(args.sngs, args.seed)
        ]
        merged: Counter = Counter()
        for w in _run_pool(_field_worker, work):
            merged.update(w)
        report_field(field, merged, sum(merged.values()))
    else:
        if not args.change:
            print("--change is required for champion_challenger mode")
            sys.exit(1)
        if ARCHETYPES.get(args.archetype, {}).get('kind') == 'rule_bot':
            print(f"{args.archetype!r} is a rule_bot — it ignores tables/flags; the A/B is a no-op.")
            sys.exit(1)
        _challenger_seat_indices(args.seats, args.challenger_seats)  # validates the split
        work = [
            (
                args.change,
                args.seats,
                args.challenger_seats,
                args.archetype,
                blind_config,
                starting_stack,
                big_blind,
                start,
                count,
            )
            for start, count in _split(args.sngs, args.seed)
        ]
        chal_wins = total = 0
        for cw, tot in _run_pool(_cc_worker, work):
            chal_wins += cw
            total += tot
        report_cc(args.change, args.seats, args.challenger_seats, chal_wins, total)


if __name__ == '__main__':
    main()
