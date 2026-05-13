#!/usr/bin/env python3
"""
Measure bb/100 win rates for tiered bot archetypes via full hand simulation.

Runs N complete poker hands using PokerStateMachine + TieredBotController,
tracking stack deltas to compute bb/100 per matchup.

Usage:
    docker compose exec backend python -m experiments.simulate_bb100 --hands 10000
    docker compose exec backend python -m experiments.simulate_bb100 --hands 10000 --round-robin
    docker compose exec backend python -m experiments.simulate_bb100 --hands 1000 --verbose
"""

import argparse
import itertools
import logging
import math
import os
import random
import sys
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Dict, List, Optional
from unittest.mock import patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kwargs):
        return it

from poker.poker_game import (
    PokerGameState, Player, create_deck,
    play_turn, advance_to_next_active_player,
)
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.psychology_model import PersonalityAnchors
from poker.strategy.strategy_table import (
    load_strategy_table,
    load_hu_strategy_table,
    StrategyTable,
)
from poker.strategy.deviation_profiles import DEVIATION_PROFILES
from poker.tiered_bot_controller import TieredBotController, BaselineSolverBot
from poker.rule_based_controller import RuleBasedController, RuleConfig, CHAOS_BOTS
from poker.memory.opponent_model import OpponentModelManager
from poker.memory.cbet_detector import CbetDetector

logger = logging.getLogger(__name__)

# ── Archetype definitions (same as validate_preflop.py) ──────────────────────

ARCHETYPES = {
    'Rock': {
        'profile': 'rock',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.3, baseline_looseness=0.25,
            ego=0.3, poise=0.8, expressiveness=0.3,
            risk_identity=0.3, adaptation_bias=0.3,
            baseline_energy=0.4, recovery_rate=0.2,
        ),
    },
    'TAG': {
        'profile': 'tag',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.7, baseline_looseness=0.35,
            ego=0.5, poise=0.7, expressiveness=0.4,
            risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        ),
    },
    'LAG': {
        'profile': 'lag',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.8, baseline_looseness=0.7,
            ego=0.6, poise=0.5, expressiveness=0.6,
            risk_identity=0.6, adaptation_bias=0.5,
            baseline_energy=0.7, recovery_rate=0.15,
        ),
    },
    'Calling Station': {
        'profile': 'calling_station',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.3, baseline_looseness=0.75,
            ego=0.4, poise=0.5, expressiveness=0.5,
            risk_identity=0.4, adaptation_bias=0.3,
            baseline_energy=0.5, recovery_rate=0.15,
        ),
    },
    'Maniac': {
        'profile': 'maniac',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.9, baseline_looseness=0.85,
            ego=0.7, poise=0.3, expressiveness=0.8,
            risk_identity=0.8, adaptation_bias=0.3,
            baseline_energy=0.8, recovery_rate=0.1,
        ),
    },
    'Nit': {
        'profile': 'nit',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.15, baseline_looseness=0.15,
            ego=0.2, poise=0.9, expressiveness=0.2,
            risk_identity=0.2, adaptation_bias=0.3,
            baseline_energy=0.3, recovery_rate=0.2,
        ),
    },
    # Layer-1-only reference bot. Selectable as opponent for EV-ordering validation.
    'Baseline': {
        'profile': 'baseline',
        'anchors': None,
    },
    # Rule-based opponents (deterministic, no LLM, no strategy table)
    'GTO-Lite': {
        'kind': 'rule_bot',
        'strategy': 'pot_odds_robot',
    },
    'ABCBot': {
        'kind': 'rule_bot',
        'strategy': 'abc',
    },
    # FoldyBot: c-bet exploit validation target. Calls wide preflop,
    # folds tight to flop pressure. See _strategy_foldy in
    # poker/rule_based_controller.py for full semantics.
    'FoldyBot': {
        'kind': 'rule_bot',
        'strategy': 'foldy',
    },
    'CaseBot': {
        'kind': 'rule_bot',
        'strategy': 'case_based',
    },
    'CallStation': {
        'kind': 'rule_bot',
        'strategy': 'always_call',
    },
    'ManiacBot': {
        'kind': 'rule_bot',
        'strategy': 'maniac',
    },
}

TERMINAL_PHASES = {PokerPhase.HAND_OVER, PokerPhase.GAME_OVER}

MAX_ACTIONS_PER_HAND = 100


def apply_adaptation_bias_override(
    config: dict, bias: Optional[float]
) -> dict:
    """Return a config with adaptation_bias overridden in its anchors.

    Returns the original config unchanged if bias is None or the config
    has no anchors (rule_bots, Baseline). Used by Phase 6 validation gates
    to inject high/low adaptation_bias into a test archetype without
    mutating the global ARCHETYPES dict.
    """
    if bias is None:
        return config
    anchors = config.get('anchors')
    if anchors is None:
        return config
    return {**config, 'anchors': replace(anchors, adaptation_bias=bias)}


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class MatchupStats:
    """Statistics for one archetype matchup."""
    bb100: float
    ci_lo: float
    ci_hi: float
    n: int
    mean_delta: float


# ── Controller factory ───────────────────────────────────────────────────────

_HU_TABLE_CACHE: Optional[StrategyTable] = None
_HU_TABLE_CACHED: bool = False


def _get_hu_table() -> Optional[StrategyTable]:
    """Lazy-load + cache the HU preflop table. Returns None if file missing."""
    global _HU_TABLE_CACHE, _HU_TABLE_CACHED
    if not _HU_TABLE_CACHED:
        _HU_TABLE_CACHE = load_hu_strategy_table()
        _HU_TABLE_CACHED = True
    return _HU_TABLE_CACHE


def make_controller(
    name: str,
    archetype_config: dict,
    strategy_table: StrategyTable,
    sm: PokerStateMachine,
    rng_seed: Optional[int] = None,
    hu_strategy_table: Optional[StrategyTable] = None,
):
    """Build a controller without LLM/persistence dependencies.

    Dispatches based on archetype_config['kind']:
      - 'rule_bot' (CHAOS_BOTS strategy)  -> RuleBasedController
      - default                            -> TieredBotController / BaselineSolverBot

    Uses the same mock pattern as test_tiered_bot_controller.py for tiered:
    bypass AIPlayerController.__init__ and manually set required attributes.
    """
    # Rule-based controller path: no strategy table, no LLM, no psychology
    if archetype_config.get('kind') == 'rule_bot':
        strategy = archetype_config['strategy']
        rule_config = CHAOS_BOTS.get(strategy) or RuleConfig(
            strategy=strategy, name=name,
        )
        return RuleBasedController(
            player_name=name, state_machine=sm, config=rule_config,
        )

    # Tiered path: solver baselines + personality distortion
    profile_key = archetype_config['profile']
    is_baseline = profile_key == 'baseline'
    cls = BaselineSolverBot if is_baseline else TieredBotController

    with patch(
        'poker.tiered_bot_controller.AIPlayerController.__init__',
        return_value=None,
    ):
        controller = cls.__new__(cls)

    anchors = archetype_config['anchors']

    controller.player_name = name
    controller.state_machine = sm
    controller.strategy_table = strategy_table
    # Lazy-load HU table from disk if not explicitly passed. Cached
    # module-wide so the lookup only happens once per process.
    controller.hu_strategy_table = (
        hu_strategy_table if hu_strategy_table is not None else _get_hu_table()
    )
    controller.debug_logging = False
    controller.rng = random.Random(rng_seed)
    controller.skip_personality_distortion = is_baseline
    controller._deviation_profile = (
        None if is_baseline else DEVIATION_PROFILES[profile_key]
    )
    # SimpleNamespace with anchors is sufficient — get_emotional_shift()
    # gracefully returns 'composed' when zone_effects is unavailable.
    controller.psychology = SimpleNamespace(anchors=anchors)
    controller.prompt_config = SimpleNamespace(strategic_reflection=False)
    controller._current_hand_plans = []
    controller._hand_max_bluff_likelihood = 0

    return controller


# ── Game state factory ───────────────────────────────────────────────────────

def make_game_state(
    player_names: List[str],
    big_blind: int = 100,
    starting_stack: int = 10000,
    dealer_idx: int = 0,
    seed: Optional[int] = None,
) -> PokerGameState:
    """Create a fresh heads-up game state for one hand."""
    players = tuple(
        Player(name=n, stack=starting_stack, is_human=False)
        for n in player_names
    )
    return PokerGameState(
        players=players,
        deck=create_deck(shuffled=True, random_seed=seed),
        current_ante=big_blind,
        last_raise_amount=big_blind,
        current_dealer_idx=dealer_idx,
    )


# ── Hand runner ──────────────────────────────────────────────────────────────

def run_hand(
    sm: PokerStateMachine,
    controllers: List[TieredBotController],
    big_blind: int,
    verbose: bool = False,
    opponent_manager: Optional[OpponentModelManager] = None,
    hero_name: Optional[str] = None,
    hand_number: Optional[int] = None,
) -> Dict[str, int]:
    """Drive one complete hand to completion.

    Returns dict mapping player name to final stack.

    When ``opponent_manager`` and ``hero_name`` are provided, every non-hero
    action observed during the hand is fed into the manager so the hero
    controller can adapt to opponent tendencies across hands. Default is
    no observation (behavior unchanged).
    """
    controller_map = {c.player_name: c for c in controllers}
    action_count = 0

    # Phase 6.6/6.7a: reset sim-path aggressor state on hero's controller
    # at hand start. Production paths get this via MemoryManager.on_hand_start;
    # the sim bypasses MM, so we drive it directly here.
    hero_controller = controller_map.get(hero_name) if hero_name else None
    if hero_controller is not None:
        hero_controller._sim_last_preflop_aggressor = None
        hero_controller._sim_recent_aggressor = None
    # Phase 6.7a: track current street so we can reset _sim_recent_aggressor
    # on each street transition.
    sim_current_street: Optional[str] = None
    # C-bet detector drives the production state machine. Without this
    # the sim never feeds fold_to_cbet observations into opponent
    # models, leaving the c-bet exploit silently inert.
    cbet_detector = CbetDetector()

    while sm.phase not in TERMINAL_PHASES:
        sm.run_until(list(TERMINAL_PHASES))

        if sm.phase in TERMINAL_PHASES:
            break

        gs = sm.game_state

        # Handle all-in runout: advance past the betting round so the SM
        # deals remaining community cards. Simply clearing the flags would
        # cause run_betting_round_transition to re-set them (infinite loop).
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

        # Normal action required
        current_player = gs.current_player
        controller = controller_map[current_player.name]
        controller.state_machine = sm

        # Both TieredBotController and RuleBasedController expose decide_action()
        # as their public interface — uniform call across controller types.
        decision = controller.decide_action()

        action = decision['action']
        raise_to = decision.get('raise_to', 0) or 0
        phase_name = sm.phase.name

        if verbose:
            logger.info(
                f"  {current_player.name}: {action}"
                f"{f' to {raise_to}' if raise_to else ''}"
            )

        # Snapshot active players BEFORE play_turn — CbetDetector needs
        # the pre-fold view to seed its facing-set on flop c-bets.
        active_players_snapshot = [
            p.name for p in gs.players if not getattr(p, 'is_folded', False)
        ]

        # Phase 6: feed non-hero actions into hero's opponent model so the
        # tiered bot can detect tendencies (VPIP/PFR/AF/all-in freq) and
        # adapt across hands. Hero's own actions are skipped.
        if (
            opponent_manager is not None
            and hero_name is not None
            and current_player.name != hero_name
        ):
            opponent_manager.observe_action(
                observer=hero_name,
                opponent=current_player.name,
                action=action,
                phase=phase_name,
                is_voluntary=True,
                hand_number=hand_number,
            )

        # play_turn expects raise_to as absolute amount for 'raise' action
        new_gs = play_turn(gs, action, raise_to)

        # Drive the c-bet detector and apply any fold_to_cbet
        # observations to the hero's opponent model. Hero's own actions
        # still feed the state machine (e.g. hero's preflop raise sets
        # them as c-bet aggressor) but produce no self-observation.
        if opponent_manager is not None and hero_name is not None:
            cbet_responses = cbet_detector.record_action(
                player_name=current_player.name, action=action,
                phase=phase_name, active_players=active_players_snapshot,
            )
            for opp_name, folded in cbet_responses:
                if opp_name == hero_name:
                    continue  # hero observing hero is not useful
                model = opponent_manager.get_model(hero_name, opp_name)
                model.tendencies.update_fold_to_cbet(folded)

        # Phase 6.6: track last accepted preflop aggressor on hero's
        # controller. Set after play_turn() so we mirror MemoryManager
        # .on_action's "accepted action" semantics — controller intent
        # that the engine rejects should not change the c-bet aggressor.
        if (
            phase_name == 'PRE_FLOP'
            and action in ('raise', 'all_in')
            and hero_controller is not None
        ):
            hero_controller._sim_last_preflop_aggressor = current_player.name

        # Phase 6.7a: per-street postflop live aggressor. Reset on street
        # change; update on accepted postflop bet/raise/all_in.
        if hero_controller is not None:
            if sim_current_street != phase_name:
                hero_controller._sim_recent_aggressor = None
                sim_current_street = phase_name
            if (
                phase_name in ('FLOP', 'TURN', 'RIVER')
                and action in ('bet', 'raise', 'all_in')
            ):
                hero_controller._sim_recent_aggressor = current_player.name
        advanced = advance_to_next_active_player(new_gs)
        sm.game_state = advanced if advanced is not None else new_gs

        action_count += 1
        if action_count >= MAX_ACTIONS_PER_HAND:
            logger.warning("Max actions reached — terminating hand")
            break

    return {p.name: p.stack for p in sm.game_state.players}


# ── Matchup runner ───────────────────────────────────────────────────────────

def run_matchup(
    archetype_a: str,
    archetype_b: str,
    n_hands: int,
    strategy_table: StrategyTable,
    big_blind: int = 100,
    starting_stack: int = 10000,
    base_seed: int = 42,
    verbose: bool = False,
    hero_adaptation_bias: Optional[float] = None,
) -> List[float]:
    """Run n_hands between two archetypes.

    Returns list of per-hand chip deltas for player A.
    Uses unique player names (P1/P2) to avoid collision in mirror matchups.
    """
    config_a = apply_adaptation_bias_override(
        ARCHETYPES[archetype_a], hero_adaptation_bias
    )
    config_b = ARCHETYPES[archetype_b]
    name_a = 'P1'
    name_b = 'P2'
    deltas_a: List[float] = []

    # Phase 6: one manager per matchup so observations accumulate across
    # hands. Hero is name_a (the first archetype). Manager is attached to
    # ctrl_a below in the hand loop.
    opponent_manager = OpponentModelManager()

    for hand_num in tqdm(range(n_hands), desc=f"  {archetype_a} vs {archetype_b}", leave=False, file=sys.stderr):
        hand_seed = base_seed + hand_num
        dealer_idx = hand_num % 2  # alternate button for fairness

        gs = make_game_state(
            player_names=[name_a, name_b],
            big_blind=big_blind,
            starting_stack=starting_stack,
            dealer_idx=dealer_idx,
            seed=hand_seed,
        )
        sm = PokerStateMachine(gs)

        ctrl_a = make_controller(
            name_a, config_a, strategy_table, sm, rng_seed=hand_seed,
        )
        ctrl_b = make_controller(
            name_b, config_b, strategy_table, sm, rng_seed=hand_seed + 1_000_000,
        )

        # Attach the shared manager to the hero controller for this hand.
        ctrl_a.opponent_model_manager = opponent_manager
        opponent_manager.record_hand_dealt(
            observer=name_a, opponents=[name_b], hand_number=hand_num,
        )

        final_stacks = run_hand(
            sm, [ctrl_a, ctrl_b], big_blind, verbose=verbose,
            opponent_manager=opponent_manager,
            hero_name=name_a,
            hand_number=hand_num,
        )
        delta_a = final_stacks.get(name_a, starting_stack) - starting_stack
        deltas_a.append(delta_a)

    return deltas_a


# ── 6-max matchup runner ─────────────────────────────────────────────────────

def _make_seat_names(opponents: List[str]) -> List[str]:
    """Build readable, unique seat names for opponents.

    Duplicates get numeric suffixes (CaseBot, CaseBot, CaseBot
    → CaseBot01, CaseBot02, CaseBot03). Singletons keep their archetype name
    (GTO-Lite → GTO-Lite). The simulator uses these as player names for the
    duration of the hand; the diagnostic tool uses them as keys in its
    per-opponent chip-transfer table.
    """
    counts = {name: opponents.count(name) for name in set(opponents)}
    indexes: Dict[str, int] = {name: 0 for name in counts}
    seats: List[str] = []
    for name in opponents:
        if counts[name] == 1:
            seats.append(name)
        else:
            indexes[name] += 1
            seats.append(f"{name}{indexes[name]:02d}")
    return seats


def run_6max_matchup(
    archetype: str,
    n_hands: int,
    strategy_table: StrategyTable,
    big_blind: int = 100,
    starting_stack: int = 10000,
    base_seed: int = 42,
    verbose: bool = False,
    opponents: Optional[List[str]] = None,
    hero_adaptation_bias: Optional[float] = None,
) -> List[float]:
    """Run n_hands of 6-max poker: 1 archetype + 5 opponents.

    Rotates dealer position through all 6 seats for positional fairness.
    Returns per-hand stack deltas for the archetype player.

    Args:
        archetype: Name of the archetype occupying the hero seat (test subject)
        opponents: List of 5 ARCHETYPES keys for the other seats. Defaults
            to ['Baseline'] * 5 if not supplied.

    Duplicate opponents get suffixed seat names (CaseBot01, CaseBot02, ...).
    """
    if opponents is None:
        opponents = ['Baseline'] * 5
    elif len(opponents) != 5:
        raise ValueError(
            f"opponents must have 5 entries, got {len(opponents)}"
        )

    # Disambiguate hero from any opponent that happens to share the archetype name
    hero_name = archetype if archetype not in opponents else f"{archetype}_hero"
    opponent_seats = _make_seat_names(opponents)
    if hero_name in opponent_seats:
        hero_name = f"{archetype}_hero"
    archetype_seat = hero_name
    all_names = [archetype_seat] + opponent_seats

    config_arch = apply_adaptation_bias_override(
        ARCHETYPES[archetype], hero_adaptation_bias
    )
    opp_configs = [ARCHETYPES[o] for o in opponents]
    opp_desc = '+'.join(opponents) if len(set(opponents)) > 1 else f'5x {opponents[0]}'
    deltas: List[float] = []

    # Phase 6: one manager per matchup so observations accumulate across
    # hands. Hero is archetype_seat (the first controller). Manager is
    # attached to controllers[0] below in the hand loop.
    opponent_manager = OpponentModelManager()

    for hand_num in tqdm(
        range(n_hands),
        desc=f"  {archetype} vs {opp_desc} (6-max)",
        leave=False, file=sys.stderr,
    ):
        hand_seed = base_seed + hand_num
        dealer_idx = hand_num % 6  # rotate button through all 6 seats

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
                archetype_seat, config_arch, strategy_table, sm,
                rng_seed=hand_seed,
            )
        ]
        for i, (seat, cfg) in enumerate(zip(opponent_seats, opp_configs)):
            controllers.append(
                make_controller(
                    seat, cfg, strategy_table, sm,
                    rng_seed=hand_seed + 1_000_000 * (i + 1),
                )
            )

        # Attach the shared manager to the hero controller for this hand.
        controllers[0].opponent_model_manager = opponent_manager
        opponent_manager.record_hand_dealt(
            observer=archetype_seat,
            opponents=opponent_seats,
            hand_number=hand_num,
        )

        final_stacks = run_hand(
            sm, controllers, big_blind, verbose=verbose,
            opponent_manager=opponent_manager,
            hero_name=archetype_seat,
            hand_number=hand_num,
        )
        delta = final_stacks.get(archetype_seat, starting_stack) - starting_stack
        deltas.append(delta)

    return deltas


def run_all_6max_vs_baseline(
    n_hands: int,
    strategy_table: StrategyTable,
    big_blind: int,
    starting_stack: int,
    seed: int,
    verbose: bool = False,
    hero_adaptation_bias: Optional[float] = None,
):
    """Run each archetype vs 5 baselines at 6-max."""
    print(f"\nBB/100 Simulation: 6-MAX, {n_hands} hands per archetype, seed={seed}")
    print(f"Format: 1 archetype + 5 Baselines, dealer rotates")
    print(f"Stack: {starting_stack}, BB: {big_blind}")
    print("=" * 67)

    results: Dict[str, MatchupStats] = {}

    # Skip 'Baseline' as test subject AND as opponent — only test personality archetypes
    test_archetypes = [n for n in ARCHETYPES if n != 'Baseline']

    # Include a Baseline-as-subject run for mirror sanity check
    test_archetypes.append('Baseline')

    for name in test_archetypes:
        deltas = run_6max_matchup(
            name, n_hands, strategy_table,
            big_blind=big_blind, starting_stack=starting_stack,
            base_seed=seed, verbose=verbose,
            hero_adaptation_bias=hero_adaptation_bias,
        )
        results[name] = compute_stats(deltas, big_blind)

    print_results(results, opponent_label='5x Baseline')
    print_baseline_hypothesis_check(results)

    return results


# Default rule_bot mix for vs-rules 6-max runs.
# Picked to span the strategic spectrum: GTO-Lite (pot-odds discipline),
# ABCBot (tight rule-based), CaseBot (adaptive), CallStation (passive
# leak), ManiacBot (relentless aggression). Five seats, one of each.
DEFAULT_RULE_OPPONENTS = ['GTO-Lite', 'ABCBot', 'CaseBot', 'CallStation', 'ManiacBot']


def run_all_6max_vs_rules(
    n_hands: int,
    strategy_table: StrategyTable,
    big_blind: int,
    starting_stack: int,
    seed: int,
    verbose: bool = False,
    opponents: Optional[List[str]] = None,
    hero_adaptation_bias: Optional[float] = None,
):
    """Run each tiered archetype vs a fixed mix of 5 rule_bots at 6-max.

    Cleaner read on real-world cash-game performance than HU vs a single
    rule_bot, because the archetype faces a spectrum of styles
    (pot-odds disciplined, passive, aggressive, adaptive) and can't be
    counter-exploited by any single one.
    """
    opponents = opponents or DEFAULT_RULE_OPPONENTS
    print(f"\nBB/100 Simulation: 6-MAX vs RULE BOTS, {n_hands} hands per archetype, seed={seed}")
    print(f"Opponents: {', '.join(opponents)}")
    print(f"Stack: {starting_stack}, BB: {big_blind}")
    print("=" * 67)

    # Test all tiered archetypes (not the rule_bots themselves)
    test_archetypes = [
        n for n, cfg in ARCHETYPES.items()
        if cfg.get('kind') != 'rule_bot' and n != 'Baseline'
    ]
    # Include Baseline as a sanity reference
    test_archetypes.append('Baseline')

    results: Dict[str, MatchupStats] = {}
    for name in test_archetypes:
        deltas = run_6max_matchup(
            name, n_hands, strategy_table,
            big_blind=big_blind, starting_stack=starting_stack,
            base_seed=seed, verbose=verbose,
            opponents=opponents,
            hero_adaptation_bias=hero_adaptation_bias,
        )
        results[name] = compute_stats(deltas, big_blind)

    print_results(results, opponent_label='5x rule_bots')

    print("\n--- Summary ---")
    sorted_results = sorted(results.items(), key=lambda x: -x[1].bb100)
    winners = [n for n, s in sorted_results if s.ci_lo > 0]
    losers = [n for n, s in sorted_results if s.ci_hi < 0]
    print(f"  Profitable archetypes (CI > 0): {winners or 'none'}")
    print(f"  Losing archetypes    (CI < 0): {losers or 'none'}")

    return results


# ── Statistics ───────────────────────────────────────────────────────────────

def compute_stats(deltas: List[float], big_blind: int) -> MatchupStats:
    """Compute bb/100 with 95% confidence interval."""
    n = len(deltas)
    if n == 0:
        return MatchupStats(bb100=0, ci_lo=0, ci_hi=0, n=0, mean_delta=0)

    mean = sum(deltas) / n
    variance = sum((d - mean) ** 2 for d in deltas) / max(n - 1, 1)
    stderr = math.sqrt(variance / n)

    # bb/100: convert mean chip delta to big blinds, scale to per-100 hands
    bb100 = (mean / big_blind) * 100
    ci_margin = 1.96 * (stderr / big_blind) * 100

    return MatchupStats(
        bb100=bb100,
        ci_lo=bb100 - ci_margin,
        ci_hi=bb100 + ci_margin,
        n=n,
        mean_delta=mean,
    )


# ── Reporting ────────────────────────────────────────────────────────────────

def print_results(results: Dict[str, MatchupStats], opponent_label: str = 'TAG'):
    """Print formatted results table."""
    print(f"\n{'Archetype vs ' + opponent_label:<25} {'bb/100':>8} {'95% CI':>22} {'Hands':>8}")
    print("-" * 67)

    for name, stats in sorted(results.items(), key=lambda x: -x[1].bb100):
        ci_str = f"[{stats.ci_lo:+.1f}, {stats.ci_hi:+.1f}]"
        mirror_tag = "  (mirror)" if name == opponent_label else ""
        print(f"{name:<25} {stats.bb100:>+8.1f} {ci_str:>22} {stats.n:>8}{mirror_tag}")


def print_baseline_hypothesis_check(results: Dict[str, MatchupStats]):
    """Verify the BaselineSolverBot hypothesis (spec line 1271).

    Checks:
    1. Every personality archetype has negative bb/100 vs baseline (deviations cost EV)
    2. All losses within -20 bb/100 hard guardrail (spec line 1281)
    3. Loss ordering roughly tracks deviation magnitude
       (TAG closest to baseline → smallest loss; Maniac/Nit furthest → largest loss)
    """
    print("\n--- Baseline hypothesis verification ---")
    arch_results = {n: s for n, s in results.items() if n != 'Baseline'}
    baseline_mirror = results.get('Baseline')

    all_pass = True

    # 1. Every archetype negative
    print("\n  Check 1: Every archetype loses bb/100 vs baseline")
    for name, stats in sorted(arch_results.items(), key=lambda x: x[1].bb100):
        passed = stats.ci_hi < 0  # entire CI below zero = strongly negative
        weak_pass = stats.bb100 < 0 and not passed  # mean negative but CI crosses zero
        if passed:
            status = 'PASS'
        elif weak_pass:
            status = 'WEAK'
        else:
            status = 'FAIL'
            all_pass = False
        print(
            f"    [{status}] {name:<18} {stats.bb100:>+7.1f} "
            f"[{stats.ci_lo:+.1f}, {stats.ci_hi:+.1f}]"
        )

    # 2. -20 bb/100 hard guardrail
    print("\n  Check 2: All losses within -20 bb/100 guardrail")
    for name, stats in arch_results.items():
        passed = stats.bb100 >= -20.0
        status = 'PASS' if passed else 'FAIL'
        if not passed:
            all_pass = False
        print(f"    [{status}] {name:<18} {stats.bb100:>+7.1f}")

    # 3. Baseline mirror should be near zero (sanity check)
    if baseline_mirror:
        print("\n  Sanity: Baseline mirror CI should include 0")
        passed = baseline_mirror.ci_lo <= 0 <= baseline_mirror.ci_hi
        status = 'PASS' if passed else 'FAIL'
        if not passed:
            all_pass = False
        print(
            f"    [{status}] Baseline vs Baseline "
            f"{baseline_mirror.bb100:>+7.1f} "
            f"[{baseline_mirror.ci_lo:+.1f}, {baseline_mirror.ci_hi:+.1f}]"
        )

    # 4. Deviation-magnitude ordering (informational only)
    ordered = sorted(arch_results.items(), key=lambda x: x[1].bb100)
    actual = [name for name, _ in ordered]
    print(f"\n  Loss ordering (worst → best): {' < '.join(actual)}")
    print("  Expected approx ordering: Maniac/Nit < LAG/Station < Rock < TAG")

    print(f"\n  Overall hypothesis: {'PASS' if all_pass else 'FAIL'}")
    return all_pass


def print_ordering_check(results: Dict[str, MatchupStats]):
    """Print expected vs actual win-rate ordering."""
    ordered = sorted(results.items(), key=lambda x: -x[1].bb100)
    actual = [name for name, _ in ordered]

    print(f"\nACTUAL ORDERING: {' > '.join(actual)}")

    # Directional checks that should hold in HU
    tag_stats = results.get('TAG', MatchupStats(0, 0, 0, 0, 0))
    lag_stats = results.get('LAG')
    nit_stats = results.get('Nit')
    rock_stats = results.get('Rock')

    checks = [
        # TAG mirror should include 0 in CI
        ('TAG mirror CI includes 0', tag_stats.ci_lo <= 0 <= tag_stats.ci_hi),
        # Nit should lose (too tight for HU)
        ('Nit negative or near-zero', nit_stats.bb100 < 10 if nit_stats else True),
    ]
    # LAG should beat TAG in HU (aggression rewarded)
    if lag_stats:
        checks.append(('LAG positive bb/100', lag_stats.bb100 > 0))
    # Rock should underperform LAG (too passive)
    if lag_stats and rock_stats:
        checks.append(('LAG > Rock', lag_stats.bb100 > rock_stats.bb100))

    all_pass = True
    for label, passed in checks:
        status = 'PASS' if passed else 'FAIL'
        if not passed:
            all_pass = False
        print(f"  [{status}] {label}")

    return all_pass


# ── Top-level runners ────────────────────────────────────────────────────────

def run_all_vs_tag(
    n_hands: int,
    strategy_table: StrategyTable,
    big_blind: int,
    starting_stack: int,
    seed: int,
    verbose: bool = False,
    opponent: str = 'TAG',
    hero_adaptation_bias: Optional[float] = None,
):
    """Run each archetype heads-up vs TAG (or specified opponent)."""
    print(f"\nBB/100 Simulation: {n_hands} hands per matchup, seed={seed}")
    print(f"Opponent: {opponent}, Stack: {starting_stack}, BB: {big_blind}")
    if hero_adaptation_bias is not None:
        print(f"Hero adaptation_bias overridden to: {hero_adaptation_bias}")
    print("=" * 67)

    results: Dict[str, MatchupStats] = {}

    for name in ARCHETYPES:
        deltas = run_matchup(
            name, opponent, n_hands, strategy_table,
            big_blind=big_blind, starting_stack=starting_stack,
            base_seed=seed, verbose=verbose,
            hero_adaptation_bias=hero_adaptation_bias,
        )
        results[name] = compute_stats(deltas, big_blind)

    print_results(results, opponent_label=opponent)
    if opponent == 'Baseline':
        print_baseline_hypothesis_check(results)
    else:
        print_ordering_check(results)

    return results


def run_round_robin(
    n_hands: int,
    strategy_table: StrategyTable,
    big_blind: int,
    starting_stack: int,
    seed: int,
    verbose: bool = False,
):
    """Run all 15 unique pairings."""
    names = list(ARCHETYPES.keys())
    pairings = list(itertools.combinations(names, 2))

    print(f"\nBB/100 Round Robin: {n_hands} hands per matchup, seed={seed}")
    print(f"{len(pairings)} matchups, {len(pairings) * n_hands} total hands")
    print(f"Stack: {starting_stack}, BB: {big_blind}")
    print("=" * 67)

    # Accumulate total bb/100 per archetype across all matchups
    all_deltas: Dict[str, List[float]] = {name: [] for name in names}

    for a, b in tqdm(pairings, desc="Matchups", file=sys.stderr):
        deltas_a = run_matchup(
            a, b, n_hands, strategy_table,
            big_blind=big_blind, starting_stack=starting_stack,
            base_seed=seed, verbose=verbose,
        )
        # A's deltas
        all_deltas[a].extend(deltas_a)
        # B's deltas are the inverse
        all_deltas[b].extend([-d for d in deltas_a])

    # Compute aggregate stats per archetype
    results = {
        name: compute_stats(deltas, big_blind)
        for name, deltas in all_deltas.items()
    }

    print(f"\n{'Archetype':<25} {'bb/100':>8} {'95% CI':>22} {'Hands':>8}")
    print("-" * 67)
    for name, stats in sorted(results.items(), key=lambda x: -x[1].bb100):
        ci_str = f"[{stats.ci_lo:+.1f}, {stats.ci_hi:+.1f}]"
        print(f"{name:<25} {stats.bb100:>+8.1f} {ci_str:>22} {stats.n:>8}")

    return results


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Measure bb/100 win rates for tiered bot archetypes',
    )
    parser.add_argument(
        '--hands', type=int, default=1000,
        help='Hands per matchup (default: 1000)',
    )
    parser.add_argument(
        '--seed', type=int, default=42,
        help='RNG seed (default: 42)',
    )
    parser.add_argument(
        '--big-blind', type=int, default=100,
        help='Big blind size (default: 100)',
    )
    parser.add_argument(
        '--stack', type=int, default=10000,
        help='Starting stack (default: 10000)',
    )
    parser.add_argument(
        '--round-robin', action='store_true',
        help='Run all 15 pairings instead of just vs TAG',
    )
    parser.add_argument(
        '--six-max', action='store_true',
        help='Run 6-max: 1 archetype + 5 BaselineSolverBots per matchup',
    )
    parser.add_argument(
        '--six-max-vs-rules', action='store_true',
        help='Run 6-max vs a mix of 5 rule_bots (GTO-Lite, ABCBot, CaseBot, CallStation, ManiacBot)',
    )
    parser.add_argument(
        '--opponents', type=str, default=None,
        help='Comma-separated list of 5 ARCHETYPES keys to override the default '
             'rule_bot mix when using --six-max-vs-rules. Example: '
             '"CaseBot,CaseBot,CaseBot,GTO-Lite,ABCBot"',
    )
    parser.add_argument(
        '--opponent', type=str, default='TAG',
        help='Baseline opponent for vs-all mode (default: TAG)',
    )
    parser.add_argument(
        '--adaptation-bias', type=float, default=None,
        help='Override adaptation_bias on the hero archetype anchors '
             '(Phase 6 validation: use 0.05 for no-exploit floor, 0.85 '
             'for full exploitation). Applies only to 6-max modes.',
    )
    parser.add_argument(
        '--verbose', action='store_true',
        help='Per-hand action logging',
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format='%(message)s')
    else:
        logging.basicConfig(level=logging.WARNING, format='%(message)s')

    # Suppress noisy emotional shift warnings from bounded_options
    # (SimpleNamespace psychology doesn't have zone_effects — expected)
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)

    strategy_table = load_strategy_table()

    if args.six_max_vs_rules:
        custom_opp = None
        if args.opponents:
            custom_opp = [o.strip() for o in args.opponents.split(',')]
            if len(custom_opp) != 5:
                print(f"--opponents must have 5 entries, got {len(custom_opp)}")
                sys.exit(1)
        run_all_6max_vs_rules(
            args.hands, strategy_table, args.big_blind,
            args.stack, args.seed, verbose=args.verbose,
            opponents=custom_opp,
            hero_adaptation_bias=args.adaptation_bias,
        )
    elif args.six_max:
        run_all_6max_vs_baseline(
            args.hands, strategy_table, args.big_blind,
            args.stack, args.seed, verbose=args.verbose,
            hero_adaptation_bias=args.adaptation_bias,
        )
    elif args.round_robin:
        run_round_robin(
            args.hands, strategy_table, args.big_blind,
            args.stack, args.seed, verbose=args.verbose,
        )
    else:
        run_all_vs_tag(
            args.hands, strategy_table, args.big_blind,
            args.stack, args.seed, verbose=args.verbose,
            opponent=args.opponent,
            hero_adaptation_bias=args.adaptation_bias,
        )


if __name__ == '__main__':
    main()
