"""Per-hand outcome breakdown for HU TAG vs CaseBot.

Wraps `run_hand` so we can capture per-hand outcome categories
(fold-preflop, fold-flop, ..., showdown-win, showdown-loss,
uncontested-win, uncontested-loss). For each bucket reports:
  - count
  - mean chip delta
  - total chip delta contribution

The total chip delta contributions sum to the net chips hero lost
or won across the run, so the operator can see exactly which bucket
is leaking bb/100.

Usage:
    docker compose exec backend python -m experiments.casebot_breakdown
        [--hero TAG] [--villain CaseBot] [--hands 500]
        [--seeds 42,142,242] [--adaptation-bias 0.85]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from tqdm import tqdm

from experiments.simulate_bb100 import (
    ARCHETYPES,
    MAX_ACTIONS_PER_HAND,
    TERMINAL_PHASES,
    advance_to_next_active_player,
    apply_adaptation_bias_override,
    load_strategy_table,
    make_controller,
    make_game_state,
)
from poker.card_utils import card_to_string
from poker.memory.cbet_detector import CbetDetector
from poker.memory.opponent_model import OpponentModelManager
from poker.poker_game import play_turn
from poker.poker_state_machine import PokerPhase, PokerStateMachine
from poker.strategy.hand_classification import simplify_hand_class
from poker.strategy.postflop_classifier import build_postflop_node


PHASE_ORDER = (
    PokerPhase.PRE_FLOP,
    PokerPhase.FLOP,
    PokerPhase.TURN,
    PokerPhase.RIVER,
)


def _run_hand_instrumented(
    sm: PokerStateMachine, controllers, big_blind: int,
    hero_name: str, opponent_manager: Optional[OpponentModelManager] = None,
    hand_number: int = 0,
) -> Dict:
    """Run one hand and capture hero's per-phase action history.

    Returns:
        {
          'final_stacks': {name: chips},
          'hero_actions': [(phase_name, action, amount), ...],
          'hero_folded_at': PokerPhase | None,
          'reached_showdown': bool,
          'hero_hand': tuple[str, str],
        }
    """
    controller_map = {c.player_name: c for c in controllers}
    hero_controller = controller_map.get(hero_name)
    if hero_controller is not None:
        hero_controller._sim_last_preflop_aggressor = None
        hero_controller._sim_recent_aggressor = None
    sim_current_street: Optional[str] = None
    cbet_detector = CbetDetector()

    hero_actions: List[Tuple[str, str, int]] = []
    all_actions: List[Tuple[str, str, str, int]] = []  # (phase, name, action, amount)
    community_at_fold: List[str] = []
    hero_folded_at: Optional[PokerPhase] = None
    hero_fold_hand_class: Optional[str] = None
    hero_fold_call_amount: int = 0
    hero_fold_pot_total: int = 0
    # Plan §6: per-fold pipeline-snapshot capture for the multi-axis
    # diagnostic report.
    hero_fold_nut_status: Optional[str] = None
    hero_fold_danger_flags: Tuple[str, ...] = ()
    hero_fold_bet_bucket: Optional[str] = None
    hero_fold_required_equity: float = 0.0
    hero_fold_opponent_archetype: Optional[str] = None
    # Plan §7 follow-up: per-decision capture for value-bet / bluff
    # frequency aggregation. (phase, action, hand_class, archetype)
    # tuples for each postflop hero decision.
    hero_postflop_decisions: List[Tuple[str, str, str, str]] = []

    while sm.phase not in TERMINAL_PHASES:
        sm.run_until(list(TERMINAL_PHASES))
        if sm.phase in TERMINAL_PHASES:
            break

        gs = sm.game_state

        if gs.run_it_out:
            sm.game_state = gs.update(run_it_out=False, awaiting_action=False)
            next_phase = {
                PokerPhase.PRE_FLOP: PokerPhase.DEALING_CARDS,
                PokerPhase.FLOP: PokerPhase.DEALING_CARDS,
                PokerPhase.TURN: PokerPhase.DEALING_CARDS,
                PokerPhase.RIVER: PokerPhase.EVALUATING_HAND,
            }.get(sm.phase, PokerPhase.EVALUATING_HAND)
            sm.phase = next_phase
            continue

        current_player = gs.current_player
        controller = controller_map[current_player.name]
        controller.state_machine = sm
        decision = controller.decide_action()
        action = decision['action']
        raise_to = decision.get('raise_to', 0) or 0
        phase_name = sm.phase.name

        # Always track full action history regardless of player — used
        # to dump example hands of interest at the end.
        all_actions.append((phase_name, current_player.name, action, raise_to))

        if current_player.name == hero_name:
            hero_actions.append((phase_name, action, raise_to))
            # Plan §7 follow-up: capture (phase, action, hand_class,
            # archetype) for postflop hero decisions so the aggregate
            # value-bet / bluff frequency report can compute rates by
            # hand class × opponent archetype.
            if phase_name in ('FLOP', 'TURN', 'RIVER'):
                snap = getattr(
                    controller, '_last_pipeline_snapshot', None,
                ) or {}
                hand_class = snap.get('hand_strength') or 'unknown'
                archetype = snap.get('opponent_archetype') or 'unknown'
                hero_postflop_decisions.append(
                    (phase_name, action, hand_class, archetype),
                )
            if action == 'fold' and hero_folded_at is None:
                hero_folded_at = sm.phase
                community_at_fold = [
                    card_to_string(c) for c in gs.community_cards
                ]
                # Capture hand strength + spot context at fold time so
                # we can later answer "what hand class was TAG folding?"
                # Only meaningful postflop — preflop folds are classified
                # by canonical hand instead and not captured here.
                if sm.phase in (
                    PokerPhase.FLOP, PokerPhase.TURN, PokerPhase.RIVER,
                ):
                    try:
                        hole = [card_to_string(c) for c in current_player.hand]
                        community = [
                            card_to_string(c) for c in gs.community_cards
                        ]
                        node = build_postflop_node(
                            gs,
                            next(
                                i for i, p in enumerate(gs.players)
                                if p.name == hero_name
                            ),
                            hole, community,
                        )
                        hero_fold_hand_class = simplify_hand_class(
                            node.made_tier, node.draw_modifier,
                        )
                    except Exception:
                        hero_fold_hand_class = 'classify_error'
                    pot = getattr(gs, 'pot', None)
                    pot_total = pot.get('total', 0) if isinstance(pot, dict) else (pot or 0)
                    hero_fold_pot_total = pot_total
                    hero_fold_call_amount = getattr(gs, 'call_amount', 0) or 0

                    # Plan §6: read additional snapshot fields the
                    # controller persisted during the fold decision.
                    # These let the multi-axis report group folds by
                    # (hand_class, nut_status, bet_bucket) and surface
                    # the opponent archetype hero faced.
                    snap = getattr(
                        controller, '_last_pipeline_snapshot', None,
                    ) or {}
                    hero_fold_nut_status = snap.get('nut_status')
                    danger_flags = snap.get('danger_flags') or frozenset()
                    hero_fold_danger_flags = tuple(sorted(danger_flags))
                    hero_fold_bet_bucket = snap.get('bet_bucket')
                    hero_fold_required_equity = float(
                        snap.get('required_equity') or 0.0
                    )
                    hero_fold_opponent_archetype = snap.get(
                        'opponent_archetype',
                    )

        active_players_snapshot = [
            p.name for p in gs.players if not getattr(p, 'is_folded', False)
        ]

        if opponent_manager is not None and current_player.name != hero_name:
            opponent_manager.observe_action(
                observer=hero_name, opponent=current_player.name,
                action=action, phase=phase_name,
                is_voluntary=True, hand_number=hand_number,
            )

        cbet_responses = cbet_detector.record_action(
            player_name=current_player.name, action=action, phase=phase_name,
            active_players=active_players_snapshot,
        )
        if opponent_manager is not None:
            for opp_name, folded in cbet_responses:
                model = opponent_manager.get_model(hero_name, opp_name)
                model.tendencies.update_fold_to_cbet(folded)
            for pfr_name, attempted in cbet_detector.consume_pfr_attempt_events():
                if pfr_name != hero_name:
                    model = opponent_manager.get_model(hero_name, pfr_name)
                    model.tendencies.update_cbet_attempt(attempted)

        if hero_controller is not None and phase_name == 'PRE_FLOP' and action in ('raise', 'all_in'):
            hero_controller._sim_last_preflop_aggressor = current_player.name
        if hero_controller is not None and phase_name in ('FLOP', 'TURN', 'RIVER'):
            if action in ('bet', 'raise', 'all_in'):
                if sim_current_street != phase_name:
                    sim_current_street = phase_name
                hero_controller._sim_recent_aggressor = current_player.name
            elif sim_current_street != phase_name:
                hero_controller._sim_recent_aggressor = None
                sim_current_street = phase_name

        if hero_controller is not None and getattr(
            hero_controller, 'opponent_model_manager', None
        ) is None and opponent_manager is not None:
            hero_controller.opponent_model_manager = opponent_manager

        new_gs = play_turn(gs, action, raise_to)
        advanced = advance_to_next_active_player(new_gs)
        sm.game_state = advanced if advanced is not None else new_gs

        # Loop-bound: prevents pathological infinite-action hands from
        # hanging the sim. Mirrors simulate_bb100.run_hand.
        if len(hero_actions) + 1 >= MAX_ACTIONS_PER_HAND:
            break

    # Inspect end state
    final_gs = sm.game_state
    final_stacks = {p.name: p.stack for p in final_gs.players}

    # Did hand reach showdown? Easiest signal: there were at least 5
    # community cards dealt AND hero didn't fold.
    reached_showdown = (
        len(final_gs.community_cards) >= 5 and hero_folded_at is None
    )

    hero_player = next(p for p in final_gs.players if p.name == hero_name)
    hero_hand = tuple(
        c if isinstance(c, str) else f"{c.rank}{c.suit}"
        for c in (hero_player.hand or ())
    )

    return {
        'final_stacks': final_stacks,
        'hero_actions': hero_actions,
        'all_actions': all_actions,
        'community_at_fold': community_at_fold,
        'community_final': [card_to_string(c) for c in final_gs.community_cards],
        'hero_folded_at': hero_folded_at,
        'hero_fold_hand_class': hero_fold_hand_class,
        'hero_fold_call_amount': hero_fold_call_amount,
        'hero_fold_pot_total': hero_fold_pot_total,
        # Plan §6 snapshot fields:
        'hero_fold_nut_status': hero_fold_nut_status,
        'hero_fold_danger_flags': hero_fold_danger_flags,
        'hero_fold_bet_bucket': hero_fold_bet_bucket,
        'hero_fold_required_equity': hero_fold_required_equity,
        'hero_fold_opponent_archetype': hero_fold_opponent_archetype,
        # Plan §7 follow-up: per-decision capture
        'hero_postflop_decisions': hero_postflop_decisions,
        'reached_showdown': reached_showdown,
        'hero_hand': hero_hand,
    }


def _categorize_hand(result: Dict, hero_name: str, starting_stack: int,
                      villain_name: str) -> str:
    folded_at = result['hero_folded_at']
    delta = result['final_stacks'].get(hero_name, starting_stack) - starting_stack
    villain_delta = result['final_stacks'].get(villain_name, starting_stack) - starting_stack

    if folded_at is not None:
        return f"fold_{folded_at.name.lower()}"
    if result['reached_showdown']:
        return 'showdown_win' if delta > 0 else 'showdown_loss' if delta < 0 else 'showdown_tie'
    # No fold by hero, no showdown → opponent folded somewhere.
    if delta > 0:
        return 'uncontested_win'
    if delta < 0:
        return 'uncontested_loss'
    return 'flat'


_INTERESTING_FOLD_CLASSES = frozenset({'nuts', 'strong_made'})
_INTERESTING_FOLD_PHASES = frozenset({PokerPhase.TURN, PokerPhase.RIVER})


def run_breakdown(
    hero_archetype: str, villain_archetype: str, n_hands: int, seed: int,
    hero_adaptation_bias: Optional[float], big_blind: int = 100,
    starting_stack: int = 10000,
    capture_interesting: bool = False,
    max_captured: int = 8,
) -> Tuple[Counter, Dict[str, int], Counter, Dict[Tuple[str, str], int], List[Dict]]:
    """Returns (bucket_counts, bucket_total_chip_delta,
                 fold_class_counts, fold_class_deltas) for n_hands HU.

    fold_class_counts / fold_class_deltas are keyed by
    `(phase_name, hand_class)` for postflop folds — answers "what
    hand class was hero folding on each street."
    """
    strategy_table = load_strategy_table()
    hero_name = hero_archetype if hero_archetype != villain_archetype else f"{hero_archetype}_hero"
    villain_name = villain_archetype
    all_names = [hero_name, villain_name]

    config_hero = apply_adaptation_bias_override(
        ARCHETYPES[hero_archetype], hero_adaptation_bias
    )
    config_villain = ARCHETYPES[villain_archetype]
    opponent_manager = OpponentModelManager()

    bucket_counts: Counter = Counter()
    bucket_deltas: Dict[str, int] = defaultdict(int)
    fold_class_counts: Counter = Counter()
    fold_class_deltas: Dict[Tuple[str, str], int] = defaultdict(int)
    # Plan §6: multi-axis fold breakdown keyed on
    # (phase, hand_class, nut_status, bet_bucket) so operators can see
    # whether folds concentrate on (e.g.) "medium_made + bluff_catcher
    # + small bucket" (where §2 floor should fire) vs "strong_made +
    # actual_nuts + jam bucket" (residual real leaks for §7 work).
    fold_multi_axis_counts: Counter = Counter()
    fold_multi_axis_deltas: Dict[Tuple[str, str, str, str], int] = defaultdict(int)
    # Plan §6: per-archetype fold counter — answers "are we folding
    # more vs sticky_jammer than pure_station? cold_start?"
    fold_archetype_counts: Counter = Counter()
    fold_archetype_deltas: Dict[str, int] = defaultdict(int)
    # Plan §7 follow-up: per-decision aggressive-action counters by
    # (hand_class, archetype). aggressive_counts[k] / decision_counts[k]
    # gives the value-bet (or bluff) frequency for that bucket.
    decision_counts: Counter = Counter()       # keyed by (hand_class, archetype)
    aggressive_counts: Counter = Counter()     # subset where hero bet/raised
    captured_hands: List[Dict] = []

    for hand_num in tqdm(range(n_hands), desc=f"  seed={seed}", leave=False, file=sys.stderr):
        hand_seed = seed + hand_num
        dealer_idx = hand_num % 2

        gs = make_game_state(
            player_names=all_names, big_blind=big_blind,
            starting_stack=starting_stack, dealer_idx=dealer_idx,
            seed=hand_seed,
        )
        sm = PokerStateMachine(gs)
        controllers = [
            make_controller(hero_name, config_hero, strategy_table, sm,
                             rng_seed=hand_seed),
            make_controller(villain_name, config_villain, strategy_table, sm,
                             rng_seed=hand_seed + 1_000_000),
        ]
        controllers[0].opponent_model_manager = opponent_manager
        opponent_manager.record_hand_dealt(
            observer=hero_name, opponents=[villain_name], hand_number=hand_num,
        )

        result = _run_hand_instrumented(
            sm, controllers, big_blind, hero_name=hero_name,
            opponent_manager=opponent_manager, hand_number=hand_num,
        )
        bucket = _categorize_hand(result, hero_name, starting_stack, villain_name)
        delta = result['final_stacks'].get(hero_name, starting_stack) - starting_stack
        bucket_counts[bucket] += 1
        bucket_deltas[bucket] += delta

        folded_at = result['hero_folded_at']
        if folded_at in (
            PokerPhase.FLOP, PokerPhase.TURN, PokerPhase.RIVER,
        ):
            phase_name = folded_at.name.lower()
            hand_class = result['hero_fold_hand_class'] or 'unknown'
            key = (phase_name, hand_class)
            fold_class_counts[key] += 1
            fold_class_deltas[key] += delta

            # Plan §6 multi-axis breakdown bookkeeping.
            nut_status = result.get('hero_fold_nut_status') or 'unknown'
            bet_bucket = result.get('hero_fold_bet_bucket') or 'no_bet'
            multi_key = (phase_name, hand_class, nut_status, bet_bucket)
            fold_multi_axis_counts[multi_key] += 1
            fold_multi_axis_deltas[multi_key] += delta

            archetype = (
                result.get('hero_fold_opponent_archetype') or 'unknown'
            )
            fold_archetype_counts[archetype] += 1
            fold_archetype_deltas[archetype] += delta

            if (
                capture_interesting
                and folded_at in _INTERESTING_FOLD_PHASES
                and hand_class in _INTERESTING_FOLD_CLASSES
                and len(captured_hands) < max_captured
            ):
                captured_hands.append({
                    'seed': hand_seed,
                    'hand_num': hand_num,
                    'phase': phase_name,
                    'hand_class': hand_class,
                    'hero_hole': result['hero_hand'],
                    'community_at_fold': result['community_at_fold'],
                    'community_final': result['community_final'],
                    'pot_at_fold': result['hero_fold_pot_total'],
                    'call_amount_at_fold': result['hero_fold_call_amount'],
                    'delta': delta,
                    'actions': result['all_actions'],
                    # Plan §6 enriched snapshot fields:
                    'nut_status': nut_status,
                    'danger_flags': result.get('hero_fold_danger_flags', ()),
                    'bet_bucket': bet_bucket,
                    'required_equity': result.get(
                        'hero_fold_required_equity', 0.0,
                    ),
                    'opponent_archetype': archetype,
                })

        # Plan §7 follow-up: aggregate per-decision aggressive-rate
        # buckets across the full hand. Counts every postflop hero
        # decision, not just folds — so the rates are computed over
        # the right denominator (all decisions with that hand_class +
        # archetype). 'all_in' counts as aggressive; 'check' / 'call'
        # / 'fold' do not.
        for phase, action, hand_class, archetype in result.get(
            'hero_postflop_decisions', (),
        ):
            decision_key = (hand_class, archetype)
            decision_counts[decision_key] += 1
            is_aggressive = (
                action == 'bet'
                or action.startswith('bet_')
                or action == 'raise'
                or action.startswith('raise_')
                or action == 'all_in'
            )
            if is_aggressive:
                aggressive_counts[decision_key] += 1

    return (
        bucket_counts, bucket_deltas,
        fold_class_counts, fold_class_deltas, captured_hands,
        fold_multi_axis_counts, fold_multi_axis_deltas,
        fold_archetype_counts, fold_archetype_deltas,
        decision_counts, aggressive_counts,
    )


def print_captured_hands(hands: List[Dict], hero_name: str, villain_name: str):
    """Print full action transcripts for hands that triggered the
    interesting-fold capture (nuts/strong_made folded turn/river)."""
    if not hands:
        return
    print("\n" + "=" * 72)
    print(f"EXAMPLE HANDS — {hero_name} folded nuts/strong_made on turn/river")
    print("=" * 72)
    for i, hand in enumerate(hands, 1):
        print(f"\n--- Example {i} (seed={hand['seed']}, hand #{hand['hand_num']}) ---")
        print(f"  Hero hole: {' '.join(hand['hero_hole']) or '?'}")
        print(f"  Community at fold: {' '.join(hand['community_at_fold']) or '(none)'}")
        if hand['community_final'] != hand['community_at_fold']:
            print(f"  Community final:   {' '.join(hand['community_final'])}")
        print(f"  Hand class at fold: {hand['hand_class']}")
        print(f"  Phase: {hand['phase']}")
        print(f"  Pot at fold:    {hand['pot_at_fold']}")
        print(f"  Hero needs to call: {hand['call_amount_at_fold']}  "
              f"(pot odds {hand['call_amount_at_fold'] / max(1, hand['pot_at_fold'] + hand['call_amount_at_fold']):.2%})")
        # Plan §6 enriched context (only present when the controller
        # populated _last_pipeline_snapshot — may be empty on older
        # captured fixtures, hence the .get fallbacks).
        nut = hand.get('nut_status')
        bucket = hand.get('bet_bucket')
        req = hand.get('required_equity')
        flags = hand.get('danger_flags')
        arche = hand.get('opponent_archetype')
        if nut or bucket or arche or flags:
            req_str = f"{req:.3f}" if req else 'n/a'
            flags_str = ', '.join(flags) if flags else '—'
            print(
                f"  Strategic context: nut={nut or 'n/a'}  "
                f"bucket={bucket or 'n/a'}  req_eq={req_str}  "
                f"opp_archetype={arche or 'n/a'}  "
                f"danger=[{flags_str}]"
            )
        print(f"  Hand delta: {hand['delta']:+.0f} chips")
        print(f"  Action sequence:")
        last_phase = None
        for (phase, name, action, amount) in hand['actions']:
            if phase != last_phase:
                print(f"    [{phase}]")
                last_phase = phase
            amount_str = f" -> {amount}" if amount else ""
            tag = " <- HERO" if name == hero_name else ""
            print(f"      {name}: {action}{amount_str}{tag}")


def print_fold_class_breakdown(
    fold_class_counts: Counter, fold_class_deltas: Dict[Tuple[str, str], int],
    n_hands: int, big_blind: int,
):
    """Postflop-fold breakdown grouped by (phase, hand_class)."""
    print(f"\n  Postflop folds by hand class:")
    print(f"  {'phase':<8}  {'hand_class':<16}  {'count':>6}  "
          f"{'mean Δ':>10}  {'total Δ':>12}  {'bb/100':>10}")
    print(f"  {'-' * 8}  {'-' * 16}  {'-' * 6}  {'-' * 10}  "
          f"{'-' * 12}  {'-' * 10}")
    rows = sorted(fold_class_counts.items(), key=lambda kv: (
        ['flop', 'turn', 'river'].index(kv[0][0]) if kv[0][0] in ('flop', 'turn', 'river') else 9,
        -kv[1],
    ))
    for (phase, hand_class), count in rows:
        total = fold_class_deltas[(phase, hand_class)]
        mean = total / count if count else 0
        bb100 = (total / big_blind) / (n_hands / 100) if n_hands else 0
        print(f"  {phase:<8}  {hand_class:<16}  {count:>6}  "
              f"{mean:>+10.0f}  {total:>+12.0f}  {bb100:>+9.1f}")


def print_multi_axis_breakdown(
    counts: Counter,
    deltas: Dict[Tuple[str, str, str, str], int],
    n_hands: int, big_blind: int,
):
    """Plan §6: postflop-fold breakdown grouped by
    (phase, hand_class, nut_status, bet_bucket).

    Helps the operator see whether folds concentrate in spots the
    plan flagged (e.g. medium_made + bluff_catcher + small bucket —
    where §2 defense floor explicitly defers to §7.5 bluff_catch).
    """
    if not counts:
        return
    print("\n" + "=" * 72)
    print("  Postflop folds by (phase, hand_class, nut_status, bet_bucket):")
    print("=" * 72)
    header = (
        f"  {'phase':<6}  {'hand_class':<16}  {'nut_status':<16}  "
        f"{'bucket':<8}  {'count':>5}  {'mean Δ':>9}  "
        f"{'total Δ':>11}  {'bb/100':>8}"
    )
    print(header)
    print('  ' + '-' * (len(header) - 2))
    rows = sorted(counts.items(), key=lambda kv: -counts[kv[0]])
    for key, count in rows:
        phase, hand_class, nut_status, bet_bucket = key
        total = deltas.get(key, 0)
        mean = total / count if count else 0
        bb100 = (total / big_blind) / (n_hands / 100) if n_hands else 0
        print(
            f"  {phase:<6}  {hand_class:<16}  {nut_status:<16}  "
            f"{bet_bucket:<8}  {count:>5}  {mean:>+9.0f}  "
            f"{total:>+11.0f}  {bb100:>+7.1f}"
        )


_VALUE_HAND_CLASSES = frozenset({'strong_made', 'nuts'})
_BLUFF_HAND_CLASSES = frozenset({'air_no_draw', 'air_strong_draw'})


def print_value_and_bluff_freq_breakdown(
    decision_counts: Counter,
    aggressive_counts: Counter,
):
    """Plan §7 follow-up: postflop hero aggressive-action rate by
    (hand_class, opponent_archetype).

    Two sections:
      - Value-bet frequency for `strong_made`/`nuts` hands. Higher is
        better (extract chips from passive opponents).
      - Bluff frequency for `air_*` hands. Lower is generally better
        against low-fold opponents; §5's bluff_reduction rule pushes
        this down vs station archetypes.

    'Aggressive' = bet/raise/all_in. check/call/fold count as non-
    aggressive. The denominator is *every* postflop hero decision in
    the bucket, not just non-fold decisions — so rates measure
    "how often does hero choose the aggressive line."
    """
    if not decision_counts:
        return

    def _print_section(title: str, classes: frozenset):
        rows = [
            ((hc, arche), count)
            for (hc, arche), count in decision_counts.items()
            if hc in classes
        ]
        if not rows:
            return
        rows.sort(key=lambda kv: -kv[1])
        print("\n" + "=" * 72)
        print(f"  {title}")
        print("=" * 72)
        print(
            f"  {'hand_class':<16}  {'archetype':<18}  "
            f"{'decisions':>10}  {'aggressive':>11}  {'rate':>7}"
        )
        print('  ' + '-' * 70)
        for (hc, arche), total in rows:
            agg = aggressive_counts.get((hc, arche), 0)
            rate = (100.0 * agg / total) if total else 0
            print(
                f"  {hc:<16}  {arche:<18}  {total:>10}  "
                f"{agg:>11}  {rate:>6.1f}%"
            )

    _print_section(
        'Value-bet frequency (aggressive %) for strong_made / nuts:',
        _VALUE_HAND_CLASSES,
    )
    _print_section(
        'Bluff frequency (aggressive %) for air_no_draw / air_strong_draw:',
        _BLUFF_HAND_CLASSES,
    )


def print_archetype_breakdown(
    counts: Counter, deltas: Dict[str, int],
    n_hands: int, big_blind: int,
):
    """Plan §6: postflop-fold breakdown by opponent archetype.

    Answers "are we folding more vs sticky_jammer than pure_station?
    are cold-start folds dominating?"
    """
    if not counts:
        return
    print("\n" + "=" * 72)
    print("  Postflop folds by opponent archetype:")
    print("=" * 72)
    print(
        f"  {'archetype':<18}  {'count':>5}  {'pct':>5}  "
        f"{'mean Δ':>9}  {'total Δ':>11}  {'bb/100':>8}"
    )
    print('  ' + '-' * 62)
    total_count = sum(counts.values()) or 1
    rows = sorted(counts.items(), key=lambda kv: -kv[1])
    for archetype, count in rows:
        total = deltas.get(archetype, 0)
        mean = total / count if count else 0
        pct = 100.0 * count / total_count
        bb100 = (total / big_blind) / (n_hands / 100) if n_hands else 0
        print(
            f"  {archetype:<18}  {count:>5}  {pct:>4.1f}%  "
            f"{mean:>+9.0f}  {total:>+11.0f}  {bb100:>+7.1f}"
        )


def print_breakdown(
    hero_archetype: str, villain_archetype: str, n_hands: int, seed: int,
    bucket_counts: Counter, bucket_deltas: Dict[str, int], big_blind: int,
    fold_class_counts: Optional[Counter] = None,
    fold_class_deltas: Optional[Dict[Tuple[str, str], int]] = None,
):
    print("=" * 72)
    print(f"seed={seed} — {hero_archetype} vs {villain_archetype}, {n_hands} hands")
    print("=" * 72)

    total_delta = sum(bucket_deltas.values())
    total_count = sum(bucket_counts.values())
    bb100 = (total_delta / big_blind) / (n_hands / 100) if n_hands else 0
    print(f"\n  Total: {total_delta:+.0f} chips ({bb100:+.1f} bb/100)\n")

    ordering = [
        'fold_pre_flop',
        'fold_flop',
        'fold_turn',
        'fold_river',
        'uncontested_win',
        'uncontested_loss',
        'showdown_win',
        'showdown_loss',
        'showdown_tie',
        'flat',
    ]
    print(f"  {'bucket':<20}  {'count':>6}  {'pct':>6}  "
          f"{'mean Δ':>10}  {'total Δ':>12}  {'bb/100 contrib':>14}")
    print(f"  {'-' * 20}  {'-' * 6}  {'-' * 6}  {'-' * 10}  "
          f"{'-' * 12}  {'-' * 14}")
    for bucket in ordering:
        count = bucket_counts.get(bucket, 0)
        if count == 0:
            continue
        total = bucket_deltas.get(bucket, 0)
        mean = total / count
        pct = 100.0 * count / total_count if total_count else 0
        contrib = (total / big_blind) / (n_hands / 100) if n_hands else 0
        print(f"  {bucket:<20}  {count:>6}  {pct:>5.1f}%  "
              f"{mean:>+10.0f}  {total:>+12.0f}  {contrib:>+13.1f}")

    # Identity check
    print(f"\n  total count = {total_count}, total delta = {total_delta:+.0f}")

    if fold_class_counts:
        print_fold_class_breakdown(
            fold_class_counts, fold_class_deltas or defaultdict(int),
            n_hands, big_blind,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--hero', default='TAG')
    parser.add_argument('--villain', default='CaseBot')
    parser.add_argument('--hands', type=int, default=500)
    parser.add_argument('--seeds', default='42,142,242')
    parser.add_argument('--adaptation-bias', type=float, default=0.85)
    args = parser.parse_args()

    seeds = [int(s) for s in args.seeds.split(',')]

    overall_counts: Counter = Counter()
    overall_deltas: Dict[str, int] = defaultdict(int)
    overall_fold_class_counts: Counter = Counter()
    overall_fold_class_deltas: Dict[Tuple[str, str], int] = defaultdict(int)
    # Plan §6 aggregators
    overall_multi_axis_counts: Counter = Counter()
    overall_multi_axis_deltas: Dict[Tuple[str, str, str, str], int] = defaultdict(int)
    overall_archetype_counts: Counter = Counter()
    overall_archetype_deltas: Dict[str, int] = defaultdict(int)
    # Plan §7 follow-up aggregators
    overall_decision_counts: Counter = Counter()
    overall_aggressive_counts: Counter = Counter()
    overall_captured: List[Dict] = []
    for seed in seeds:
        (
            counts, deltas, fc_counts, fc_deltas, captured,
            ma_counts, ma_deltas, arch_counts, arch_deltas,
            dec_counts, agg_counts,
        ) = run_breakdown(
            args.hero, args.villain, args.hands, seed,
            args.adaptation_bias,
            capture_interesting=True,
            max_captured=3,
        )
        print_breakdown(
            args.hero, args.villain, args.hands, seed,
            counts, deltas, big_blind=100,
            fold_class_counts=fc_counts,
            fold_class_deltas=fc_deltas,
        )
        for k, v in counts.items():
            overall_counts[k] += v
        for k, v in deltas.items():
            overall_deltas[k] += v
        for k, v in fc_counts.items():
            overall_fold_class_counts[k] += v
        for k, v in fc_deltas.items():
            overall_fold_class_deltas[k] += v
        for k, v in ma_counts.items():
            overall_multi_axis_counts[k] += v
        for k, v in ma_deltas.items():
            overall_multi_axis_deltas[k] += v
        for k, v in arch_counts.items():
            overall_archetype_counts[k] += v
        for k, v in arch_deltas.items():
            overall_archetype_deltas[k] += v
        for k, v in dec_counts.items():
            overall_decision_counts[k] += v
        for k, v in agg_counts.items():
            overall_aggressive_counts[k] += v
        overall_captured.extend(captured)

    if len(seeds) > 1:
        print("\n")
        print_breakdown(
            args.hero, args.villain, args.hands * len(seeds),
            seed='ALL',
            bucket_counts=overall_counts,
            bucket_deltas=overall_deltas,
            big_blind=100,
            fold_class_counts=overall_fold_class_counts,
            fold_class_deltas=overall_fold_class_deltas,
        )

    # Plan §6: aggregated multi-axis report (always emit, even for
    # single-seed runs — the table is informative either way).
    if overall_multi_axis_counts:
        print_multi_axis_breakdown(
            overall_multi_axis_counts, overall_multi_axis_deltas,
            args.hands * len(seeds), 100,
        )
    if overall_archetype_counts:
        print_archetype_breakdown(
            overall_archetype_counts, overall_archetype_deltas,
            args.hands * len(seeds), 100,
        )

    # Plan §7 follow-up: aggregate value-bet / bluff frequency report.
    print_value_and_bluff_freq_breakdown(
        overall_decision_counts, overall_aggressive_counts,
    )

    print_captured_hands(overall_captured, args.hero, args.villain)


if __name__ == '__main__':
    main()
