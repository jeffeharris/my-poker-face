#!/usr/bin/env python3
"""Spot-level report for preflop overfolding vs aggression.

This is a diagnostic harness for the hypothesis that loose-aggressive and
maniac archetypes overperform because opponents fold too much to pressure. It
runs the same deterministic 6-max hand loop as simulate_bb100 and records every
preflop decision at the point the actor sees it. Chip-delta columns are whole-
hand deltas grouped by spot, useful for correlation rather than isolated action
EV.

Examples:
    docker compose exec backend python -m experiments.preflop_aggression_report \
        --hands 500 --stack-bb 25
    docker compose exec backend python -m experiments.preflop_aggression_report \
        --heroes TAG,Rock,Defender,Baseline \
        --opponents Maniac,Maniac,Maniac,Maniac,Maniac \
        --stack-bb 100,40,25,15 \
        --csv-dir /app/experiments/results/preflop_aggression
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.simulate_bb100 import (  # noqa: E402
    ARCHETYPES,
    DEFAULT_RULE_OPPONENTS,
    _make_seat_names,
    apply_adaptation_bias_override,
    make_controller,
    make_game_state,
    run_hand,
)
from poker.memory.opponent_model import OpponentModelManager  # noqa: E402
from poker.poker_state_machine import PokerStateMachine  # noqa: E402
from poker.strategy.preflop_classifier import (  # noqa: E402
    classify_preflop_scenario,
    get_6max_position,
)
from poker.strategy.strategy_table import load_strategy_table  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_HEROES = ['TAG', 'Rock', 'Defender', 'Baseline', 'LAG', 'Maniac']
DEFAULT_OPPONENTS = ['Maniac'] * 5
AGGRESSIVE_ARCHETYPES = {
    'LAG',
    'Maniac',
    'ManiacOverBluff',
    'SpewyFish',
    'ManiacBot',
    'TrickyAggro',
}
ACTION_BUCKETS = ('fold', 'check', 'call', 'raise', 'jam')


@dataclass(frozen=True)
class DecisionEvent:
    hand_number: int
    actor: str
    actor_archetype: str
    position: str
    spot: str
    scenario: str
    stack_bucket: str
    stack_bb: float
    action_bucket: str
    action: str
    opponent_name: str
    opponent_archetype: str
    opponent_position: str
    faced_all_in: bool
    cost_to_call_bb: float
    pot_bb: float
    highest_bet_bb: float


@dataclass(frozen=True)
class AggressionEvent:
    hand_number: int
    actor: str
    actor_archetype: str
    position: str
    context: str
    stack_bucket: str
    stack_bb: float
    action_bucket: str
    raise_to_bb: float
    pot_before_bb: float


@dataclass
class ActionSummary:
    opportunities: int = 0
    folds: int = 0
    checks: int = 0
    calls: int = 0
    raises: int = 0
    jams: int = 0
    total_actor_delta_bb: float = 0.0
    total_opponent_delta_bb: float = 0.0
    faced_all_in: int = 0

    def add(self, event: DecisionEvent, actor_delta_bb: float, opponent_delta_bb: float) -> None:
        self.opportunities += 1
        if event.action_bucket == 'fold':
            self.folds += 1
        elif event.action_bucket == 'check':
            self.checks += 1
        elif event.action_bucket == 'call':
            self.calls += 1
        elif event.action_bucket == 'raise':
            self.raises += 1
        elif event.action_bucket == 'jam':
            self.jams += 1
        self.total_actor_delta_bb += actor_delta_bb
        self.total_opponent_delta_bb += opponent_delta_bb
        if event.faced_all_in:
            self.faced_all_in += 1

    @property
    def continues(self) -> int:
        return self.checks + self.calls + self.raises + self.jams


@dataclass
class AggressionSummary:
    attempts: int = 0
    uncontested_wins: int = 0
    total_delta_bb: float = 0.0
    total_raise_to_bb: float = 0.0
    total_pot_before_bb: float = 0.0

    def add(self, event: AggressionEvent, delta_bb: float, won_uncontested: bool) -> None:
        self.attempts += 1
        if won_uncontested:
            self.uncontested_wins += 1
        self.total_delta_bb += delta_bb
        self.total_raise_to_bb += event.raise_to_bb
        self.total_pot_before_bb += event.pot_before_bb


@dataclass
class ArchetypeSummary:
    hands: int = 0
    total_delta_bb: float = 0.0

    def add(self, delta_bb: float) -> None:
        self.hands += 1
        self.total_delta_bb += delta_bb


class PreflopAggressionReport:
    def __init__(self) -> None:
        self.defense: Dict[Tuple[str, ...], ActionSummary] = defaultdict(ActionSummary)
        self.entry: Dict[Tuple[str, ...], ActionSummary] = defaultdict(ActionSummary)
        self.aggression: Dict[Tuple[str, ...], AggressionSummary] = defaultdict(AggressionSummary)
        self.archetypes: Dict[str, ArchetypeSummary] = defaultdict(ArchetypeSummary)

    def record_hand(
        self,
        decisions: List[DecisionEvent],
        aggressions: List[AggressionEvent],
        final_players,
        community_count: int,
        seat_archetypes: Dict[str, str],
        starting_stack: int,
        big_blind: int,
    ) -> None:
        final_stacks = {p.name: p.stack for p in final_players}
        for player in final_players:
            archetype = seat_archetypes.get(player.name, player.name)
            delta_bb = (player.stack - starting_stack) / big_blind
            self.archetypes[archetype].add(delta_bb)

        active = [p.name for p in final_players if not getattr(p, 'is_folded', False)]
        ended_preflop = community_count == 0
        uncontested_winner = active[0] if ended_preflop and len(active) == 1 else None

        for event in decisions:
            actor_delta_bb = (
                final_stacks.get(event.actor, starting_stack) - starting_stack
            ) / big_blind
            opponent_delta_bb = 0.0
            if event.opponent_name:
                opponent_delta_bb = (
                    final_stacks.get(event.opponent_name, starting_stack) - starting_stack
                ) / big_blind
            key = (
                event.actor_archetype,
                event.opponent_archetype,
                event.spot,
                event.position,
                event.opponent_position,
                event.stack_bucket,
            )
            if event.opponent_name:
                self.defense[key].add(event, actor_delta_bb, opponent_delta_bb)
            else:
                self.entry[key].add(event, actor_delta_bb, opponent_delta_bb)

        last_aggression_index = len(aggressions) - 1
        for i, event in enumerate(aggressions):
            delta_bb = (final_stacks.get(event.actor, starting_stack) - starting_stack) / big_blind
            won_uncontested = i == last_aggression_index and event.actor == uncontested_winner
            key = (
                event.actor_archetype,
                event.context,
                event.position,
                event.stack_bucket,
            )
            self.aggression[key].add(event, delta_bb, won_uncontested)

    def decision_rows(self, category: str, summaries: Dict[Tuple[str, ...], ActionSummary]):
        rows = []
        for key, summary in summaries.items():
            actor, opponent, spot, position, opponent_position, stack_bucket = key
            n = summary.opportunities
            rows.append(
                {
                    'category': category,
                    'actor_archetype': actor,
                    'opponent_archetype': opponent,
                    'spot': spot,
                    'position': position,
                    'opponent_position': opponent_position,
                    'stack_bucket': stack_bucket,
                    'opportunities': n,
                    'fold_pct': _pct(summary.folds, n),
                    'check_pct': _pct(summary.checks, n),
                    'call_pct': _pct(summary.calls, n),
                    'raise_pct': _pct(summary.raises, n),
                    'jam_pct': _pct(summary.jams, n),
                    'continue_pct': _pct(summary.continues, n),
                    'faced_all_in_pct': _pct(summary.faced_all_in, n),
                    'avg_actor_delta_bb': _safe_avg(summary.total_actor_delta_bb, n),
                    'avg_opponent_delta_bb': _safe_avg(summary.total_opponent_delta_bb, n),
                    'total_actor_delta_bb': round(summary.total_actor_delta_bb, 2),
                }
            )
        return rows

    def aggression_rows(self):
        rows = []
        for key, summary in self.aggression.items():
            actor, context, position, stack_bucket = key
            n = summary.attempts
            rows.append(
                {
                    'actor_archetype': actor,
                    'context': context,
                    'position': position,
                    'stack_bucket': stack_bucket,
                    'attempts': n,
                    'uncontested_wins': summary.uncontested_wins,
                    'uncontested_win_pct': _pct(summary.uncontested_wins, n),
                    'avg_delta_bb': _safe_avg(summary.total_delta_bb, n),
                    'total_delta_bb': round(summary.total_delta_bb, 2),
                    'avg_raise_to_bb': _safe_avg(summary.total_raise_to_bb, n),
                    'avg_pot_before_bb': _safe_avg(summary.total_pot_before_bb, n),
                }
            )
        return rows

    def archetype_rows(self):
        rows = []
        for archetype, summary in self.archetypes.items():
            rows.append(
                {
                    'archetype': archetype,
                    'hands': summary.hands,
                    'total_delta_bb': round(summary.total_delta_bb, 2),
                    'avg_delta_bb': _safe_avg(summary.total_delta_bb, summary.hands),
                    'bb100': round(100 * _safe_avg(summary.total_delta_bb, summary.hands), 2),
                }
            )
        return rows


def run_report(
    heroes: List[str],
    opponents: List[str],
    stack_bbs: List[float],
    hands: int,
    seed: int,
    big_blind: int,
    hero_adaptation_bias: Optional[float] = None,
    verbose: bool = False,
) -> PreflopAggressionReport:
    strategy_table = load_strategy_table()
    report = PreflopAggressionReport()

    for stack_bb in stack_bbs:
        starting_stack = int(round(stack_bb * big_blind))
        for hero_index, hero in enumerate(heroes):
            hero_name = hero if hero not in opponents else f'{hero}_hero'
            opponent_seats = _make_seat_names(opponents)
            if hero_name in opponent_seats:
                hero_name = f'{hero}_hero'
            all_names = [hero_name] + opponent_seats
            seat_archetypes = {hero_name: hero}
            seat_archetypes.update(dict(zip(opponent_seats, opponents, strict=False)))

            hero_config = apply_adaptation_bias_override(
                ARCHETYPES[hero],
                hero_adaptation_bias,
            )
            opponent_configs = [ARCHETYPES[o] for o in opponents]
            opponent_manager = OpponentModelManager()

            print(
                f'Running {hands} hands: hero={hero}, opponents={opponents}, '
                f'stack={stack_bb:g}bb, seed={seed}'
            )

            for hand_num in range(hands):
                hand_seed = seed + hand_num
                dealer_idx = hand_num % 6
                random.seed(hand_seed)

                gs = make_game_state(
                    player_names=all_names,
                    big_blind=big_blind,
                    starting_stack=starting_stack,
                    dealer_idx=dealer_idx,
                    seed=hand_seed,
                )
                sm = PokerStateMachine(gs)
                sm.current_hand_seed = hand_seed

                controllers = [
                    make_controller(
                        hero_name,
                        hero_config,
                        strategy_table,
                        sm,
                        rng_seed=hand_seed + hero_index * 10_000_000,
                    )
                ]
                for i, (seat, cfg) in enumerate(
                    zip(opponent_seats, opponent_configs, strict=False)
                ):
                    controllers.append(
                        make_controller(
                            seat,
                            cfg,
                            strategy_table,
                            sm,
                            rng_seed=hand_seed + 1_000_000 * (i + 1),
                        )
                    )

                try:
                    controllers[0].opponent_model_manager = opponent_manager
                except Exception:  # noqa: BLE001 - rule bots may not expose this seam.
                    pass
                opponent_manager.record_hand_dealt(
                    observer=hero_name,
                    opponents=opponent_seats,
                    hand_number=hand_num,
                )

                hand_decisions: List[DecisionEvent] = []
                hand_aggressions: List[AggressionEvent] = []

                def observe_decision(
                    current_player,
                    controller,
                    action,
                    raise_to,
                    phase_name,
                    decision_state,
                    sim_current_street,
                    decision,
                ) -> None:
                    event = _build_decision_event(
                        hand_number=hand_num,
                        current_player=current_player,
                        action=action,
                        raise_to=raise_to,
                        phase_name=phase_name,
                        game_state=decision_state,
                        seat_archetypes=seat_archetypes,
                        big_blind=big_blind,
                    )
                    if event is None:
                        return
                    hand_decisions.append(event)
                    aggression = _build_aggression_event(
                        event, current_player, raise_to, decision_state
                    )
                    if aggression is not None:
                        hand_aggressions.append(aggression)

                run_hand(
                    sm,
                    controllers,
                    big_blind,
                    verbose=verbose,
                    opponent_manager=opponent_manager,
                    hero_name=hero_name,
                    hand_number=hand_num,
                    equity_seed=hand_seed * 31 + 7,
                    decision_observer=observe_decision,
                )

                report.record_hand(
                    hand_decisions,
                    hand_aggressions,
                    sm.game_state.players,
                    len(sm.game_state.community_cards),
                    seat_archetypes,
                    starting_stack,
                    big_blind,
                )

    return report


def _build_decision_event(
    hand_number: int,
    current_player,
    action: str,
    raise_to: int,
    phase_name: str,
    game_state,
    seat_archetypes: Dict[str, str],
    big_blind: int,
) -> Optional[DecisionEvent]:
    if phase_name != 'PRE_FLOP':
        return None

    actor_idx = game_state.current_player_idx
    position = get_6max_position(game_state, actor_idx)
    scenario, _, classifier_opener_position = classify_preflop_scenario(game_state)
    action_bucket = _action_bucket(action, raise_to, current_player)
    stack_bb = _effective_stack_bb(game_state, actor_idx, big_blind)
    stack_bucket = _stack_bucket(stack_bb)
    aggressor_idx = _latest_aggressor_idx(game_state, actor_idx)
    faced_all_in = False
    opponent_name = ''
    opponent_archetype = ''
    opponent_position = ''

    if aggressor_idx is not None:
        opponent = game_state.players[aggressor_idx]
        opponent_name = opponent.name
        opponent_archetype = seat_archetypes.get(opponent.name, opponent.name)
        opponent_position = get_6max_position(game_state, aggressor_idx)
        faced_all_in = bool(getattr(opponent, 'is_all_in', False))

    if scenario == 'rfi':
        if _has_limpers(game_state, actor_idx):
            spot = 'limped_pot'
        elif position == 'BB' and game_state.call_amount == 0:
            spot = 'bb_option'
        elif position in ('CO', 'BTN', 'SB'):
            spot = 'steal_rfi'
        else:
            spot = 'rfi'
    elif faced_all_in:
        spot = 'call_vs_jam'
    else:
        spot = scenario

    if opponent_position == '' and classifier_opener_position:
        opponent_position = classifier_opener_position

    return DecisionEvent(
        hand_number=hand_number,
        actor=current_player.name,
        actor_archetype=seat_archetypes.get(current_player.name, current_player.name),
        position=position,
        spot=spot,
        scenario=scenario,
        stack_bucket=stack_bucket,
        stack_bb=stack_bb,
        action_bucket=action_bucket,
        action=action,
        opponent_name=opponent_name,
        opponent_archetype=opponent_archetype,
        opponent_position=opponent_position,
        faced_all_in=faced_all_in,
        cost_to_call_bb=round(game_state.call_amount / big_blind, 3),
        pot_bb=round(game_state.pot.get('total', 0) / big_blind, 3),
        highest_bet_bb=round(game_state.highest_bet / big_blind, 3),
    )


def _build_aggression_event(
    event: DecisionEvent,
    current_player,
    raise_to: int,
    game_state,
) -> Optional[AggressionEvent]:
    if event.action_bucket not in ('raise', 'jam'):
        return None

    prior_raises = game_state.raises_this_round
    if prior_raises == 0:
        base = 'iso' if event.spot == 'limped_pot' else 'open'
    elif prior_raises == 1:
        base = '3bet'
    elif prior_raises == 2:
        base = '4bet'
    else:
        base = '5bet_plus'
    context = f'{base}_jam' if event.action_bucket == 'jam' else base

    total_stack = current_player.stack + current_player.bet
    raise_to_bb = (
        raise_to / game_state.current_ante if raise_to else total_stack / game_state.current_ante
    )
    return AggressionEvent(
        hand_number=event.hand_number,
        actor=event.actor,
        actor_archetype=event.actor_archetype,
        position=event.position,
        context=context,
        stack_bucket=event.stack_bucket,
        stack_bb=event.stack_bb,
        action_bucket=event.action_bucket,
        raise_to_bb=round(raise_to_bb, 3),
        pot_before_bb=event.pot_bb,
    )


def _latest_aggressor_idx(game_state, actor_idx: int) -> Optional[int]:
    if game_state.raises_this_round <= 0:
        return None
    highest = game_state.highest_bet
    players = game_state.players
    candidates = [
        i
        for i, p in enumerate(players)
        if i != actor_idx
        and not getattr(p, 'is_folded', False)
        and p.bet == highest
        and p.last_action in ('raise', 'all_in')
    ]
    if not candidates:
        candidates = [
            i
            for i, p in enumerate(players)
            if i != actor_idx and not getattr(p, 'is_folded', False) and p.bet == highest
        ]
    if not candidates:
        return None
    for offset in range(1, len(players) + 1):
        idx = (actor_idx - offset) % len(players)
        if idx in candidates:
            return idx
    return candidates[0]


def _has_limpers(game_state, actor_idx: int) -> bool:
    if game_state.raises_this_round != 0:
        return False
    for i, player in enumerate(game_state.players):
        if i == actor_idx or getattr(player, 'is_folded', False):
            continue
        if player.bet == game_state.current_ante and player.last_action == 'call':
            return True
    return False


def _action_bucket(action: str, raise_to: int, player) -> str:
    if action == 'all_in':
        return 'jam'
    if action == 'raise' and raise_to >= player.stack + player.bet:
        return 'jam'
    if action in ACTION_BUCKETS:
        return action
    return action


def _effective_stack_bb(game_state, actor_idx: int, big_blind: int) -> float:
    actor = game_state.players[actor_idx]
    actor_total = actor.stack + actor.bet
    opponent_totals = [
        p.stack + p.bet
        for i, p in enumerate(game_state.players)
        if i != actor_idx and not getattr(p, 'is_folded', False) and (p.stack > 0 or p.bet > 0)
    ]
    effective = min(actor_total, max(opponent_totals or [actor_total]))
    return round(effective / big_blind, 3)


def _stack_bucket(stack_bb: float) -> str:
    if stack_bb <= 10:
        return '<=10'
    if stack_bb <= 15:
        return '11-15'
    if stack_bb <= 25:
        return '16-25'
    if stack_bb <= 40:
        return '26-40'
    if stack_bb <= 75:
        return '41-75'
    return '76+'


def _pct(num: int, den: int) -> float:
    return round(100 * num / den, 2) if den else 0.0


def _safe_avg(total: float, den: int) -> float:
    return round(total / den, 3) if den else 0.0


def _parse_csv_list(value: str) -> List[str]:
    return [item.strip() for item in value.split(',') if item.strip()]


def _parse_stack_bbs(value: str) -> List[float]:
    stacks = []
    for raw in _parse_csv_list(value):
        stacks.append(float(raw))
    return stacks


def _expand_heroes(value: str) -> List[str]:
    if value == 'default':
        return list(DEFAULT_HEROES)
    if value == 'all-tiered':
        return [name for name, cfg in ARCHETYPES.items() if cfg.get('kind') != 'rule_bot']
    return _parse_csv_list(value)


def _expand_opponents(value: str) -> List[str]:
    if value == 'maniac-five':
        return list(DEFAULT_OPPONENTS)
    if value == 'rule-mix':
        return list(DEFAULT_RULE_OPPONENTS)
    return _parse_csv_list(value)


def _validate_archetypes(names: Iterable[str], label: str) -> None:
    missing = [name for name in names if name not in ARCHETYPES]
    if missing:
        available = ', '.join(sorted(ARCHETYPES))
        raise SystemExit(f'Unknown {label}: {missing}. Available: {available}')


def print_report(report: PreflopAggressionReport, min_samples: int, limit: int) -> None:
    defense_rows = report.decision_rows('defense', report.defense)
    entry_rows = report.decision_rows('entry', report.entry)
    aggression_rows = report.aggression_rows()
    archetype_rows = report.archetype_rows()

    print('\n=== Archetype Results ===')
    for row in sorted(archetype_rows, key=lambda r: r['bb100'], reverse=True):
        print(
            f"{row['archetype']:<18} hands={row['hands']:>5} "
            f"bb100={row['bb100']:>8.1f} total_bb={row['total_delta_bb']:>9.1f}"
        )

    print('\n=== Highest Fold-To-Aggressive Opponents ===')
    rows = [r for r in defense_rows if r['opportunities'] >= min_samples]
    rows.sort(
        key=lambda r: (
            r['opponent_archetype'] in AGGRESSIVE_ARCHETYPES,
            r['fold_pct'],
            r['opportunities'],
        ),
        reverse=True,
    )
    _print_decision_rows(rows[:limit], include_opponent=True)

    print('\n=== Aggressor Uncontested Wins ===')
    rows = [r for r in aggression_rows if r['attempts'] >= min_samples]
    rows.sort(
        key=lambda r: (
            r['actor_archetype'] in AGGRESSIVE_ARCHETYPES,
            r['uncontested_win_pct'],
            r['total_delta_bb'],
        ),
        reverse=True,
    )
    _print_aggression_rows(rows[:limit])

    print('\n=== Entry / RFI Shape ===')
    rows = [r for r in entry_rows if r['opportunities'] >= min_samples]
    rows.sort(key=lambda r: (r['actor_archetype'], r['spot'], r['position'], r['stack_bucket']))
    _print_decision_rows(rows[:limit], include_opponent=False)


def _print_decision_rows(rows: List[dict], include_opponent: bool) -> None:
    if not rows:
        print('No rows met the sample threshold.')
        return
    if include_opponent:
        print(
            f"{'actor':<14} {'opp':<14} {'spot':<12} {'pos':<3} {'opp_pos':<7} "
            f"{'stack':<6} {'n':>5} {'fold%':>7} {'cont%':>7} {'jam%':>7} {'avgBB':>8}"
        )
        for r in rows:
            print(
                f"{r['actor_archetype']:<14} {r['opponent_archetype']:<14} "
                f"{r['spot']:<12} {r['position']:<3} {r['opponent_position']:<7} "
                f"{r['stack_bucket']:<6} {r['opportunities']:>5} "
                f"{r['fold_pct']:>6.1f}% {r['continue_pct']:>6.1f}% "
                f"{r['jam_pct']:>6.1f}% {r['avg_actor_delta_bb']:>8.2f}"
            )
    else:
        print(
            f"{'actor':<14} {'spot':<12} {'pos':<3} {'stack':<6} {'n':>5} "
            f"{'fold%':>7} {'call%':>7} {'raise%':>7} {'jam%':>7} {'avgBB':>8}"
        )
        for r in rows:
            print(
                f"{r['actor_archetype']:<14} {r['spot']:<12} {r['position']:<3} "
                f"{r['stack_bucket']:<6} {r['opportunities']:>5} "
                f"{r['fold_pct']:>6.1f}% {r['call_pct']:>6.1f}% "
                f"{r['raise_pct']:>6.1f}% {r['jam_pct']:>6.1f}% "
                f"{r['avg_actor_delta_bb']:>8.2f}"
            )


def _print_aggression_rows(rows: List[dict]) -> None:
    if not rows:
        print('No rows met the sample threshold.')
        return
    print(
        f"{'actor':<14} {'context':<10} {'pos':<3} {'stack':<6} {'n':>5} "
        f"{'uncont%':>9} {'avgBB':>8} {'totalBB':>9} {'toBB':>7}"
    )
    for r in rows:
        print(
            f"{r['actor_archetype']:<14} {r['context']:<10} {r['position']:<3} "
            f"{r['stack_bucket']:<6} {r['attempts']:>5} "
            f"{r['uncontested_win_pct']:>8.1f}% {r['avg_delta_bb']:>8.2f} "
            f"{r['total_delta_bb']:>9.1f} {r['avg_raise_to_bb']:>7.1f}"
        )


def write_csvs(report: PreflopAggressionReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    decision_rows = report.decision_rows('defense', report.defense) + report.decision_rows(
        'entry',
        report.entry,
    )
    _write_csv(output_dir / 'preflop_decision_summary.csv', decision_rows)
    _write_csv(output_dir / 'preflop_aggression_summary.csv', report.aggression_rows())
    _write_csv(output_dir / 'preflop_archetype_results.csv', report.archetype_rows())
    print(f'\nCSV written to {output_dir}')


def _write_csv(path: Path, rows: List[dict]) -> None:
    if not rows:
        path.write_text('')
        return
    fieldnames = list(rows[0].keys())
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Validate preflop overfolding vs loose-aggressive pressure.',
    )
    parser.add_argument('--hands', type=int, default=200, help='Hands per hero/depth config.')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--big-blind', type=int, default=100)
    parser.add_argument(
        '--stack-bb',
        default='25',
        help='Comma-separated starting stack depths in BB, e.g. 100,40,25,15.',
    )
    parser.add_argument(
        '--heroes',
        default='default',
        help='Comma-separated ARCHETYPES keys, "default", or "all-tiered".',
    )
    parser.add_argument(
        '--opponents',
        default='maniac-five',
        help='Comma-separated 5 ARCHETYPES keys, "maniac-five", or "rule-mix".',
    )
    parser.add_argument(
        '--adaptation-bias',
        type=float,
        default=None,
        help='Optional adaptation_bias override for the hero seat.',
    )
    parser.add_argument('--min-samples', type=int, default=10)
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--csv-dir', type=Path, default=None)
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()

    heroes = _expand_heroes(args.heroes)
    opponents = _expand_opponents(args.opponents)
    if len(opponents) != 5:
        raise SystemExit(f'--opponents must resolve to exactly 5 entries, got {len(opponents)}')
    _validate_archetypes(heroes, 'heroes')
    _validate_archetypes(opponents, 'opponents')
    stack_bbs = _parse_stack_bbs(args.stack_bb)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format='%(message)s'
    )
    # Matches simulate_bb100: controllers built through the sim factory use a
    # SimpleNamespace psychology object, so bounded_options emits expected noise.
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)
    report = run_report(
        heroes=heroes,
        opponents=opponents,
        stack_bbs=stack_bbs,
        hands=args.hands,
        seed=args.seed,
        big_blind=args.big_blind,
        hero_adaptation_bias=args.adaptation_bias,
        verbose=args.verbose,
    )
    print_report(report, min_samples=args.min_samples, limit=args.limit)
    if args.csv_dir is not None:
        write_csvs(report, args.csv_dir)


if __name__ == '__main__':
    main()
