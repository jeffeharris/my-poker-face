#!/usr/bin/env python3
"""Champion-vs-Challenger eval (docs/plans/EVAL_HARNESS_PLAN.md §P0).

The binding constraint on the tiered bot is the *eval*, not the charts: every
postflop win to date was measured vs *exploitable* opponents (the Jeff_clone
station, always-calling rule bots), so a bb/100 gain can mean "the change is
correct" OR "the change extracts more from a station" — indistinguishable.

This harness removes the station. It seats the **current bot (champion, change
OFF)** against the **changed bot (challenger, change ON)** at one table, all the
same archetype, differing *only* by the change under test. The better strategy
wins chips off the worse one, so:

    challenger net bb/100 vs champion  =  the improvement

It is **discriminating by construction** (the opponent is a coherent strategy,
not a station) and **immune to station-inflation** (there is no station). This
is the gate every chart/strategy change should pass before more charts are
authored.

Two flavors, both expressed as a ``--change`` preset:
  - **flag flavor** (trivial): champion/challenger differ by a controller flag
    (e.g. ``enable_multistreet_context``). Same strategy table.
  - **chart flavor** (small build): champion/challenger load *different*
    strategy tables (e.g. with/without the authored low-SPR chart). Same flags.

Seats split champion/challenger interleaved around the ring; the dealer button
rotates every hand, so over a full orbit each seat occupies each position
equally — positional effects cancel. Chips are conserved per hand (no rake), so
challenger_net == -champion_net exactly; the champion line is reported as a
conservation check.

Usage:
    # Re-judge the shipped multistreet layer vs the bot itself (flag flavor):
    docker compose exec backend python -m experiments.champion_challenger \\
        --change multistreet --hands 3000 --seeds 42,142,242

    # Re-judge the authored low-SPR chart vs the bare SPR fallback (chart flavor):
    docker compose exec backend python -m experiments.champion_challenger \\
        --change low_spr --hands 3000 --seeds 42,142,242

    # Heads-up (1 challenger vs 1 champion), lowest-noise discriminator:
    docker compose exec backend python -m experiments.champion_challenger \\
        --change multistreet --seats 2 --challenger-seats 1 --hands 4000
"""

import argparse
import logging
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)

from experiments.simulate_bb100 import (
    ARCHETYPES,
    MAX_ACTIONS_PER_HAND,
    TERMINAL_PHASES,
    compute_stats,
    make_controller,
    make_game_state,
)
from poker.poker_game import advance_to_next_active_player, play_turn
from poker.poker_state_machine import PokerPhase, PokerStateMachine
from poker.strategy.strategy_table import StrategyTable, load_strategy_table

_AGGRESSIVE = frozenset({'bet', 'raise', 'all_in'})
_POSTFLOP_STREETS = ('FLOP', 'TURN', 'RIVER')


# ── Change registry: what champion/challenger differ by ─────────────────────


def _multistreet_flags(h1: bool, h2: bool) -> Dict[str, object]:
    return {
        'enable_multistreet_context': True,
        'multistreet_h1_barrel': h1,
        'multistreet_h2_foldbarrel': h2,
    }


@dataclass(frozen=True)
class ChangeSpec:
    """One A/B: the table + flag deltas between champion (OFF) and challenger (ON).

    Table builders are zero-arg callables (the change name — not the callable —
    is shipped to ProcessPool workers, which look the spec up locally and build
    fresh tables, so nothing unpicklable crosses the process boundary).
    """

    description: str
    champion_table: Callable[[], StrategyTable]
    challenger_table: Callable[[], StrategyTable]
    champion_flags: Dict[str, object] = field(default_factory=dict)
    challenger_flags: Dict[str, object] = field(default_factory=dict)


CHANGES: Dict[str, ChangeSpec] = {
    # ── Flag flavor: the only genuinely flag-gated shipped change ──
    'multistreet': ChangeSpec(
        description='enable_multistreet_context = H1 barrel-continuation + H2 '
        'fold-to-double-barrel (flag flavor)',
        champion_table=load_strategy_table,
        challenger_table=load_strategy_table,
        champion_flags={'enable_multistreet_context': False},
        challenger_flags=_multistreet_flags(h1=True, h2=True),
    ),
    'multistreet_h1': ChangeSpec(
        description='multistreet H1 only (barrel continuation, HU) (flag flavor)',
        champion_table=load_strategy_table,
        challenger_table=load_strategy_table,
        champion_flags={'enable_multistreet_context': False},
        challenger_flags=_multistreet_flags(h1=True, h2=False),
    ),
    'multistreet_h2': ChangeSpec(
        description='multistreet H2 only (fold to double barrels) (flag flavor)',
        champion_table=load_strategy_table,
        challenger_table=load_strategy_table,
        champion_flags={'enable_multistreet_context': False},
        challenger_flags=_multistreet_flags(h1=False, h2=True),
    ),
    # ── Chart flavor: champion loads a table WITHOUT the authored low-SPR
    # slice (relies on the always-on SPR fallback); challenger loads it WITH. ──
    'low_spr': ChangeSpec(
        description='authored low-SPR postflop chart merged on top of the '
        'always-on SPR fallback (chart flavor; re-judges c5aa0d07)',
        champion_table=lambda: load_strategy_table(include_low_spr=False),
        challenger_table=lambda: load_strategy_table(include_low_spr=True),
    ),
    'three_bp': ChangeSpec(
        description='authored 3-bet-pot (3BP) postflop chart vs the bare '
        'pot_type fallback (3BP→SRP) (chart flavor; re-judges 4be11e93)',
        champion_table=lambda: load_strategy_table(include_3bp=False),
        challenger_table=lambda: load_strategy_table(include_3bp=True),
    ),
    'slices': ChangeSpec(
        description='BOTH authored precision slices (low-SPR + 3BP) on top of '
        'the SPR/pot_type fallbacks — the "is the precision worth keeping?" '
        'bloat question (chart flavor; champion = neither slice)',
        champion_table=lambda: load_strategy_table(include_low_spr=False, include_3bp=False),
        challenger_table=lambda: load_strategy_table(include_low_spr=True, include_3bp=True),
    ),
    # ── The core postflop fix vs the PRE-fix passive default. Champion =
    # pre-760d89e5 behavior (no SPR degrade ladder, postflop_commit off, no
    # authored slices) → low/med-SPR & 3BP misses hit the hand-blind default
    # (fold-the-nuts ~70%). Challenger = the core fix alone (SPR fallback +
    # postflop_commit, still no precision slices). Isolates whether the big
    # +32/+48-vs-Jeff fold-the-nuts fix is real vs the bot itself, separate from
    # the (known neutral/negative) low_spr / three_bp precision slices. ──
    'core_fix': ChangeSpec(
        description='core postflop fix (SPR fallback + postflop_commit, no '
        'precision slices) vs the pre-fix passive default (re-judges 760d89e5)',
        champion_table=lambda: load_strategy_table(
            spr_fallback=False, include_low_spr=False, include_3bp=False),
        challenger_table=lambda: load_strategy_table(
            spr_fallback=True, include_low_spr=False, include_3bp=False),
        champion_flags={'disable_rules': frozenset({('postflop_commit', 'default')})},
        challenger_flags={'disable_rules': frozenset()},
    ),
    # ── Depth-chart flavor: champion gets NO shallow depth charts (flat 100bb
    # preflop table at every effective-stack depth — the original
    # depth-agnostic behavior); challenger keeps the 50/25bb shallow charts
    # make_controller loads by default. Toggled via the controller's
    # `depth_strategy_tables` attribute (see _select_preflop_table). The SNG's
    # escalating blinds walk effective stacks down through 50→25bb, so the
    # charts are genuinely exercised — verifies the 50/25bb depth charts that
    # measured +13.8/+4.8 vs the Jeff station but were never self-play tested. ──
    'depth_charts': ChangeSpec(
        description='shallow 6-max depth charts (50/25bb preflop) vs the flat '
        '100bb table at every depth (flag flavor; verifies the depth charts)',
        champion_table=load_strategy_table,
        challenger_table=load_strategy_table,
        champion_flags={'depth_strategy_tables': {}},
    ),
    # ── Calibration changes (EVAL_HARNESS_PLAN §P3/§P4): not real product
    # changes — they validate the *gate itself*. `null` proves the harness is
    # unbiased (A == A → win-rate covers the null); the cripple pair proves it
    # has the sign + sensitivity to catch an engineered disaster, symmetrically.
    'null': ChangeSpec(
        description='A-A null calibration: champion == challenger (same table, '
        'no flag delta). Challenger-group win-rate must COVER the null (§P3).',
        champion_table=load_strategy_table,
        challenger_table=load_strategy_table,
    ),
    'cripple_challenger': ChangeSpec(
        description='known-extreme: challenger folds to any bet / never bets '
        '(deliberately broken) — must lose CI-clear BELOW null (§P4 sign check)',
        champion_table=load_strategy_table,
        challenger_table=load_strategy_table,
        challenger_flags={'_cripple': 'fold'},
    ),
    'cripple_champion': ChangeSpec(
        description='known-extreme mirror: champion is the broken folder — '
        'challenger must win CI-clear ABOVE null (§P4 sign check)',
        champion_table=load_strategy_table,
        challenger_table=load_strategy_table,
        champion_flags={'_cripple': 'fold'},
    ),
}


# ── Controller construction ─────────────────────────────────────────────────


def _install_cripple(controller, mode: str):
    """Replace a controller's decide_action with a deliberately broken strategy
    (EVAL_HARNESS_PLAN §P4 known-extreme calibration).

    ``mode='fold'``: fold to any bet, check when checking is free, never put
    money in voluntarily — a strictly-dominated player that bleeds blinds and
    busts fast. Used to confirm the gate has the *sign* and *sensitivity* right:
    a crippled side must lose CI-clear. The wrapper reads the live legal-option
    set at decision time so it never emits an illegal action (e.g. the BB with
    no raise to fold to checks instead). This is harness-only — it shadows the
    instance's bound method, the production controller is untouched."""
    if mode != 'fold':
        raise ValueError(f"unknown cripple mode: {mode!r}")

    def _broken_decide(*_args, **_kwargs):
        options = controller.state_machine.game_state.current_player_options
        return {'action': 'fold' if 'fold' in options else 'check'}

    controller.decide_action = _broken_decide


def _apply_flags(controller, flags: Dict[str, object]):
    """Set A/B flags on a controller (make_controller bypasses __init__, so the
    controller reads every flag via getattr(..., default) — setattr is the
    supported override path, mirroring measure_passivity._apply_mode).

    The ``_cripple`` sentinel is not a controller attribute — it installs a
    broken decide_action for known-extreme calibration (see _install_cripple)."""
    for attr, value in flags.items():
        if attr == '_cripple':
            _install_cripple(controller, value)
            continue
        setattr(controller, attr, value)


def _build_seat(name, archetype_config, table, flags, sm, rng_seed):
    controller = make_controller(name, archetype_config, table, sm, rng_seed=rng_seed)
    # Every seat is a tiered/baseline bot here; the postflop pipeline touches
    # opponent_model_manager (no-op at anchors=None, but the attribute must
    # exist since the bypassed __init__ never set it).
    controller.opponent_model_manager = None
    _apply_flags(controller, flags)
    return controller


def _challenger_seat_indices(n_seats: int, n_challenger: int) -> List[int]:
    """Interleave challenger seats evenly around the ring (e.g. 6 seats / 3
    challengers → [0,2,4]). Combined with per-hand button rotation, every seat
    sees every position equally, so neither role gets a positional edge."""
    if not 0 < n_challenger < n_seats:
        raise ValueError(f"challenger seats must be in (0, {n_seats}), got {n_challenger}")
    step = n_seats / n_challenger
    indices = sorted({int(k * step) for k in range(n_challenger)})
    # The set dedups, so a non-divisible ratio whose truncated indices collide
    # would silently seat fewer challengers than asked — fail loudly instead.
    if len(indices) != n_challenger:
        raise ValueError(
            f"{n_challenger} challengers don't interleave cleanly into {n_seats} "
            f"seats (got {indices}); pick a divisor-friendly --challenger-seats"
        )
    return indices


# ── Hand driver ─────────────────────────────────────────────────────────────


def run_cc_hand(sm: PokerStateMachine, controllers: List, big_blind: int) -> Dict[str, int]:
    """Drive one hand to completion; return {player_name: final_stack}.

    Mirrors simulate_bb100.run_hand's action driving, but maintains the
    multi-street sim-shadow state (`_sim_hero_bet_by_street` /
    `_sim_opp_bet_by_street` / `_sim_last_preflop_aggressor`) for *every* seat —
    run_hand only drives one hero, but here any seat may run the multistreet
    layer, and each derives its own line (self == hero, others == opp).
    """
    controller_map = {c.player_name: c for c in controllers}

    for c in controllers:
        c._sim_last_preflop_aggressor = None
        c._sim_recent_aggressor = None
        c._sim_hero_bet_by_street = {}
        c._sim_opp_bet_by_street = {}
    sim_current_street: Optional[str] = None
    action_count = 0

    while sm.phase not in TERMINAL_PHASES:
        sm.run_until(list(TERMINAL_PHASES))
        if sm.phase in TERMINAL_PHASES:
            break
        gs = sm.game_state

        if gs.run_it_out:
            sm.game_state = gs.update(run_it_out=False, awaiting_action=False)
            sm.phase = {
                PokerPhase.PRE_FLOP: PokerPhase.DEALING_CARDS,
                PokerPhase.FLOP: PokerPhase.DEALING_CARDS,
                PokerPhase.TURN: PokerPhase.DEALING_CARDS,
                PokerPhase.RIVER: PokerPhase.EVALUATING_HAND,
            }.get(sm.phase, PokerPhase.EVALUATING_HAND)
            continue

        current_player = gs.current_player
        controller = controller_map[current_player.name]
        controller.state_machine = sm

        decision = controller.decide_action()
        action = decision['action']
        raise_to = decision.get('raise_to', 0) or 0
        phase_name = sm.phase.name

        new_gs = play_turn(gs, action, raise_to)

        # ── Drive multi-street sim-shadow state for ALL seats ──
        if phase_name == 'PRE_FLOP' and action in ('raise', 'all_in'):
            for c in controllers:
                c._sim_last_preflop_aggressor = current_player.name
        if sim_current_street != phase_name:
            for c in controllers:
                c._sim_recent_aggressor = None
            sim_current_street = phase_name
        if phase_name in _POSTFLOP_STREETS and action in _AGGRESSIVE:
            for c in controllers:
                c._sim_recent_aggressor = current_player.name
                if c.player_name == current_player.name:
                    c._sim_hero_bet_by_street[phase_name] = True
                else:
                    c._sim_opp_bet_by_street[phase_name] = True

        advanced = advance_to_next_active_player(new_gs)
        sm.game_state = advanced if advanced is not None else new_gs

        action_count += 1
        if action_count >= MAX_ACTIONS_PER_HAND:
            break

    return {p.name: p.stack for p in sm.game_state.players}


# ── Matchup ─────────────────────────────────────────────────────────────────


@dataclass
class CCMatchupResult:
    """Per-seat chip deltas for one seed's run."""

    seat_deltas: Dict[str, List[float]]  # name -> per-hand delta
    challenger_names: Tuple[str, ...]
    champion_names: Tuple[str, ...]


def run_cc_matchup(
    change_name: str,
    archetype: str,
    n_seats: int,
    n_challenger: int,
    n_hands: int,
    champion_table: StrategyTable,
    challenger_table: StrategyTable,
    big_blind: int = 100,
    starting_stack: int = 10000,
    base_seed: int = 42,
) -> CCMatchupResult:
    """Run n_hands at one table; return per-seat deltas tagged champion/challenger."""
    spec = CHANGES[change_name]
    arch_config = ARCHETYPES[archetype]

    challenger_idx = set(_challenger_seat_indices(n_seats, n_challenger))
    names = [f"{'CHAL' if i in challenger_idx else 'CHMP'}_{i}" for i in range(n_seats)]
    challenger_names = tuple(names[i] for i in range(n_seats) if i in challenger_idx)
    champion_names = tuple(names[i] for i in range(n_seats) if i not in challenger_idx)

    seat_deltas: Dict[str, List[float]] = {n: [] for n in names}

    for hand_num in range(n_hands):
        hand_seed = base_seed + hand_num
        dealer_idx = hand_num % n_seats
        random.seed(hand_seed)  # per-hand global-random reset (mirrors the harness)

        gs = make_game_state(
            player_names=names,
            big_blind=big_blind,
            starting_stack=starting_stack,
            dealer_idx=dealer_idx,
            seed=hand_seed,
        )
        sm = PokerStateMachine(gs)
        sm.current_hand_seed = hand_seed

        controllers = []
        for i, name in enumerate(names):
            is_challenger = i in challenger_idx
            controllers.append(
                _build_seat(
                    name,
                    arch_config,
                    challenger_table if is_challenger else champion_table,
                    spec.challenger_flags if is_challenger else spec.champion_flags,
                    sm,
                    rng_seed=hand_seed + 1_000_000 * i,
                )
            )

        final_stacks = run_cc_hand(sm, controllers, big_blind)
        for name in names:
            seat_deltas[name].append(final_stacks.get(name, starting_stack) - starting_stack)

    return CCMatchupResult(seat_deltas, challenger_names, champion_names)


def _run_seed_worker(args: Tuple) -> Tuple[int, CCMatchupResult]:
    """ProcessPool worker: run one seed. Builds its own tables from the change
    spec (avoids shipping unpicklable StrategyTable / lambdas across processes)."""
    change_name, archetype, n_seats, n_challenger, n_hands, seed, stack_bb = args
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)
    spec = CHANGES[change_name]
    champion_table = spec.champion_table()
    challenger_table = spec.challenger_table()
    result = run_cc_matchup(
        change_name,
        archetype,
        n_seats,
        n_challenger,
        n_hands,
        champion_table,
        challenger_table,
        base_seed=seed,
        starting_stack=stack_bb * 100,
    )
    return seed, result


# ── Reporting ───────────────────────────────────────────────────────────────


def _pool(result: CCMatchupResult, names: Tuple[str, ...]) -> List[float]:
    out: List[float] = []
    for n in names:
        out.extend(result.seat_deltas[n])
    return out


def print_report(
    change_name: str,
    archetype: str,
    n_seats: int,
    n_challenger: int,
    n_hands: int,
    seeds: List[int],
    results: List[Tuple[int, CCMatchupResult]],
    big_blind: int,
    stack_bb: int,
):
    spec = CHANGES[change_name]
    n_champion = n_seats - n_challenger
    print("\n" + "=" * 74)
    print(f"CHAMPION vs CHALLENGER: change={change_name!r}")
    print(f"  {spec.description}")
    print(
        f"  {archetype} | {n_challenger} challenger vs {n_champion} champion seats "
        f"| {n_seats}-max | stack={stack_bb}bb"
    )
    print(f"  {n_hands * len(seeds)} hands ({n_hands} x seeds {sorted(seeds)})")
    print("=" * 74)

    # Per-seed challenger bb/100 (sign-disagreement = noise, per the push/fold A/B).
    print("\n── challenger net bb/100 vs champion, per seed ──")
    per_seed_bb: List[float] = []
    for seed, result in sorted(results, key=lambda r: r[0]):
        chal = _pool(result, result.challenger_names)
        bb = compute_stats(chal, big_blind).bb100
        per_seed_bb.append(bb)
        print(f"  seed {seed}: {bb:+8.1f} bb/100")
    sign_disagree = len({b > 0 for b in per_seed_bb}) > 1
    mean_bb = sum(per_seed_bb) / len(per_seed_bb) if per_seed_bb else 0.0
    print(
        f"  MEAN:    {mean_bb:+8.1f} bb/100"
        + ("   ⚠ per-seed SIGN DISAGREEMENT (noise)" if sign_disagree else "")
    )

    # Pooled CI (the gate: CI-clear positive = a real improvement).
    pooled_chal = [d for _, result in results for d in _pool(result, result.challenger_names)]
    pooled_chmp = [d for _, result in results for d in _pool(result, result.champion_names)]
    cs = compute_stats(pooled_chal, big_blind)
    champ_bb = compute_stats(pooled_chmp, big_blind).bb100
    lo, hi = cs.ci_lo, cs.ci_hi
    print("\n── pooled (all seeds) ──")
    print(f"  challenger: {cs.bb100:+8.1f} bb/100   95% CI [{lo:+.1f}, {hi:+.1f}]")
    print(f"  champion:   {champ_bb:+8.1f} bb/100   (conservation check: ≈ −challenger)")

    if lo > 0:
        verdict = "✅ CI-CLEAR POSITIVE — challenger is a real improvement vs the bot itself"
    elif hi < 0:
        verdict = "❌ CI-CLEAR NEGATIVE — challenger REGRESSES vs the bot itself"
    else:
        verdict = "➖ INCONCLUSIVE — CI spans 0 (need more hands/seeds, or no real effect)"
    print(f"  VERDICT: {verdict}")

    # Per-seat bb/100 (catch per-seat sign disagreement = noise, not signal).
    print("\n── per-seat bb/100 (pooled across seeds) ──")
    agg: Dict[str, List[float]] = {}
    for _, result in results:
        for name, deltas in result.seat_deltas.items():
            agg.setdefault(name, []).extend(deltas)
    for name in sorted(agg, key=lambda n: (not n.startswith('CHAL'), n)):
        role = 'challenger' if name.startswith('CHAL') else 'champion'
        print(f"  {name:<8} ({role:<10}): {compute_stats(agg[name], big_blind).bb100:+8.1f} bb/100")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        '--change',
        required=True,
        choices=sorted(CHANGES),
        help='which shipped change to A/B vs the bot itself',
    )
    p.add_argument(
        '--archetype',
        default='Baseline',
        help='archetype for ALL seats (default Baseline — pure charts, no '
        'psychology noise). Must be a tiered bot, not a rule_bot.',
    )
    p.add_argument('--seats', type=int, default=6, help='players at the table (default 6)')
    p.add_argument(
        '--challenger-seats',
        type=int,
        default=3,
        help='how many seats run the change ON (default 3; the rest are champion)',
    )
    p.add_argument('--hands', type=int, default=3000, help='hands per seed')
    p.add_argument('--seeds', default='42,142,242', help='comma-separated base seeds')
    p.add_argument(
        '--stack-bb', type=int, default=100, help='effective starting stack in BB (default 100)'
    )
    args = p.parse_args()

    if args.archetype not in ARCHETYPES:
        print(f"Unknown archetype: {args.archetype}")
        sys.exit(1)
    if ARCHETYPES[args.archetype].get('kind') == 'rule_bot':
        print(f"{args.archetype!r} is a rule_bot — it ignores tables/flags, so the A/B is a no-op.")
        sys.exit(1)
    try:
        _challenger_seat_indices(args.seats, args.challenger_seats)
    except ValueError as e:
        print(e)
        sys.exit(1)

    seeds = [int(s) for s in args.seeds.split(',')]
    work = [
        (args.change, args.archetype, args.seats, args.challenger_seats, args.hands, s, args.stack_bb)
        for s in seeds
    ]
    if len(seeds) > 1:
        with ProcessPoolExecutor(max_workers=min(len(seeds), os.cpu_count() or 1)) as ex:
            results = list(ex.map(_run_seed_worker, work))
    else:
        results = [_run_seed_worker(work[0])]

    print_report(
        args.change,
        args.archetype,
        args.seats,
        args.challenger_seats,
        args.hands,
        seeds,
        results,
        big_blind=100,
        stack_bb=args.stack_bb,
    )


if __name__ == '__main__':
    main()
