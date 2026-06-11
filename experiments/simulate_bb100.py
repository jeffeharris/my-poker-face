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
from typing import Callable, Dict, List, Optional, Tuple
from unittest.mock import patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tqdm import tqdm
except ImportError:

    def tqdm(it, **kwargs):
        return it


from experiments._hand_loop import drive_hand
from poker.memory.cbet_detector import CbetDetector
from poker.memory.opponent_model import OpponentModelManager
from poker.poker_game import (
    Player,
    PokerGameState,
    advance_to_next_active_player,  # noqa: F401 — re-exported for casebot_breakdown
    create_deck,
    play_turn,  # noqa: F401 — re-exported for sibling sim scripts
)
from poker.poker_state_machine import PokerPhase, PokerStateMachine
from poker.psychology_model import PersonalityAnchors
from poker.rule_based_controller import CHAOS_BOTS, RuleBasedController, RuleConfig
from poker.strategy.deviation_profiles import DEVIATION_PROFILES
from poker.strategy.strategy_table import (
    StrategyTable,
    load_archetype_preflop_tables,
    load_depth_strategy_tables,
    load_hu_strategy_table,
    load_strategy_table,
)
from poker.tiered_bot_controller import BaselineSolverBot, TieredBotController

logger = logging.getLogger(__name__)

# ── Archetype definitions (same as validate_preflop.py) ──────────────────────

ARCHETYPES = {
    'Rock': {
        'profile': 'rock',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.3,
            baseline_looseness=0.25,
            ego=0.3,
            poise=0.8,
            expressiveness=0.3,
            risk_identity=0.3,
            adaptation_bias=0.3,
            baseline_energy=0.4,
            recovery_rate=0.2,
        ),
    },
    'TAG': {
        'profile': 'tag',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.7,
            baseline_looseness=0.35,
            ego=0.5,
            poise=0.7,
            expressiveness=0.4,
            risk_identity=0.5,
            adaptation_bias=0.5,
            baseline_energy=0.5,
            recovery_rate=0.15,
        ),
    },
    'LAG': {
        'profile': 'lag',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.8,
            baseline_looseness=0.7,
            ego=0.6,
            poise=0.5,
            expressiveness=0.6,
            risk_identity=0.6,
            adaptation_bias=0.5,
            baseline_energy=0.7,
            recovery_rate=0.15,
        ),
    },
    'Calling Station': {
        'profile': 'calling_station',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.3,
            baseline_looseness=0.75,
            ego=0.4,
            poise=0.5,
            expressiveness=0.5,
            risk_identity=0.4,
            adaptation_bias=0.3,
            baseline_energy=0.5,
            recovery_rate=0.15,
        ),
    },
    'Maniac': {
        'profile': 'maniac',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.9,
            baseline_looseness=0.85,
            ego=0.7,
            poise=0.3,
            expressiveness=0.8,
            risk_identity=0.8,
            adaptation_bias=0.3,
            baseline_energy=0.8,
            recovery_rate=0.1,
        ),
    },
    # Validation twin of Maniac WITH over_bluff (maniac_overbluff profile), to
    # confirm over_bluff fires + shifts EV on an aggressive base (the control for
    # its inertness on the passive station base). Compare vs 'Maniac'.
    'ManiacOverBluff': {
        'profile': 'maniac_overbluff',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.9,
            baseline_looseness=0.85,
            ego=0.7,
            poise=0.3,
            expressiveness=0.8,
            risk_identity=0.8,
            adaptation_bias=0.3,
            baseline_energy=0.8,
            recovery_rate=0.1,
        ),
    },
    # Weakest realistic fish (the $2-tier trickle): loose-passive anchors + the
    # weak_fish profile (weak_station table + can't-fold + sticky/over_bluff).
    'WeakFish': {
        'profile': 'weak_fish',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.2,
            baseline_looseness=0.9,
            ego=0.4,
            poise=0.5,
            expressiveness=0.5,
            risk_identity=0.3,
            adaptation_bias=0.3,
            baseline_energy=0.5,
            recovery_rate=0.15,
        ),
    },
    # Balanced defender: the apex anti-aggression reg (balanced_defender profile —
    # calls down to catch bluffs + traps + 3-bets back, without over-folding).
    # Disciplined anchors (high poise, high adaptation_bias). The control for
    # "does competent defense neutralize the maniac, or is aggression structurally
    # +EV in this engine?"
    'Defender': {
        'profile': 'balanced_defender',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.6,
            baseline_looseness=0.35,
            ego=0.5,
            poise=0.85,
            expressiveness=0.3,
            risk_identity=0.45,
            adaptation_bias=0.6,
            baseline_energy=0.5,
            recovery_rate=0.2,
        ),
    },
    # Spewy aggressive fish: the loose-aggressive donator who bluffs off his
    # stack (spewy_fish profile — loose table + over_bluff + sticky). Loose +
    # tilty anchors (low poise, high ego/risk) so psychology amplifies the spew.
    'SpewyFish': {
        'profile': 'spewy_fish',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.85,
            baseline_looseness=0.85,
            ego=0.75,
            poise=0.25,
            expressiveness=0.7,
            risk_identity=0.8,
            adaptation_bias=0.2,
            baseline_energy=0.7,
            recovery_rate=0.1,
        ),
    },
    # Isolation: calling_station + position_blind only (station table), to price
    # position-blindness alone vs plain Calling Station + test depth-independence.
    'StationPBlind': {
        'profile': 'calling_station_pblind',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.3,
            baseline_looseness=0.75,
            ego=0.4,
            poise=0.5,
            expressiveness=0.5,
            risk_identity=0.4,
            adaptation_bias=0.3,
            baseline_energy=0.5,
            recovery_rate=0.15,
        ),
    },
    # Isolation: calling_station + over_bluff only (station table), to price the
    # over-bluff (spew) lever ALONE vs the punisher clone (the honest cost of
    # bluffing into a competent folder-and-barreler). Mirrors StationPBlind.
    'StationOverBluff': {
        'profile': 'calling_station_overbluff',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.3,
            baseline_looseness=0.75,
            ego=0.4,
            poise=0.5,
            expressiveness=0.5,
            risk_identity=0.4,
            adaptation_bias=0.3,
            baseline_energy=0.5,
            recovery_rate=0.15,
        ),
    },
    'Nit': {
        'profile': 'nit',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.15,
            baseline_looseness=0.15,
            ego=0.2,
            poise=0.9,
            expressiveness=0.2,
            risk_identity=0.2,
            adaptation_bias=0.3,
            baseline_energy=0.3,
            recovery_rate=0.2,
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
    'CaseBotV2': {
        'kind': 'rule_bot',
        'strategy': 'case_based_v2',
    },
    # Range-AWARE CaseBotV2 (adaptive prototype): same value strategy, but its
    # postflop equity is computed vs the opponents' estimated RANGE instead of
    # vs-random. `use_range_equity` is honored by the harness, which feeds
    # perfect-read field stats (ARCHETYPE_STATS) so we can test the concept
    # ceiling before building real opponent modeling.
    'CaseBotRange': {
        'kind': 'rule_bot',
        'strategy': 'case_based_v2',
        'use_range_equity': True,
    },
    'Reg': {
        'kind': 'rule_bot',
        'strategy': 'reg',
    },
    # Reg+ — the competent yardstick (keystone). Value-extracts like CaseBotV2 but
    # FOLDS to polarized big bets instead of paying them off, and never bluffs a
    # caller. Built to beat/neutralize CaseBotV2 so robustness becomes measurable.
    'RegPlus': {
        'kind': 'rule_bot',
        'strategy': 'reg_plus',
    },
    # PolarValueBot — maximally face-up value bettor (sizing-aware §B leak probe).
    'PolarValue': {
        'kind': 'rule_bot',
        'strategy': 'polar_value',
    },
    # LooseFaceUp — loose recreational face-up bettor: value-bets big with a WIDE
    # range (medium+), OFTEN, never bluffs. The "loose human" §B regime where
    # folding-to-big-bets actually pays (vs PolarValue's rare nuts-only big bet).
    'LooseFaceUp': {
        'kind': 'rule_bot',
        'strategy': 'loose_value',
    },
    # TrickyReg — eval instrument that overbet-BLUFFS to punish RegPlus's residual
    # over-fold-to-overbets leak (the §3 yardstick). Not a production bot.
    'TrickyReg': {
        'kind': 'rule_bot',
        'strategy': 'tricky_reg',
    },
    # TrickyAggro — sharper attacker: seizes initiative (wide 3-bets) + overbet-
    # barrels polarized, to stress-test RegPlus's fold-to-overbet rule. Eval-only.
    'TrickyAggro': {
        'kind': 'rule_bot',
        'strategy': 'tricky_aggro',
    },
    # Exploiter — best-responder that punishes a face-up/never-bluff bot's tells.
    # bb/100 of this vs a candidate = the candidate's human-exploitability proxy.
    'Exploiter': {
        'kind': 'rule_bot',
        'strategy': 'exploiter',
    },
    'RegVsManiac': {
        'kind': 'rule_bot',
        'strategy': 'reg_vs_maniac',
    },
    'CallStation': {
        'kind': 'rule_bot',
        'strategy': 'always_call',
    },
    'ManiacBot': {
        'kind': 'rule_bot',
        'strategy': 'maniac',
    },
    # Fish (casino tourist): loose-passive calling station that now value-bets
    # its strong hands with honest, size=strength sizing. 'Fish' is the
    # baseline; the rest layer one readable aggression leak. See
    # poker/rule_strategies.py::_strategy_fish / FishLeak.
    'Fish': {
        'kind': 'rule_bot',
        'strategy': 'fish',
    },
    'Fish-Transparent': {
        'kind': 'rule_bot',
        'strategy': 'fish',
        'fish_leak': 'bets_strong_transparently',
    },
    'Fish-Spew': {
        'kind': 'rule_bot',
        'strategy': 'fish',
        'fish_leak': 'spews_bluffs',
    },
    'Fish-Sticky': {
        'kind': 'rule_bot',
        'strategy': 'fish',
        'fish_leak': 'sticky_then_pops',
    },
}

# Perfect-read opponent stats per archetype (measured VPIP/PFR + estimated AF),
# for the range-aware prototype (CaseBotRange). Lets the bot's range estimator
# "know" the field's looseness without waiting for opponent modeling — the
# concept-test ceiling. Production would feed real observed stats instead.
ARCHETYPE_STATS = {
    'Maniac': {'vpip': 0.56, 'pfr': 0.48, 'aggression_factor': 3.0, 'hands_observed': 100},
    'LAG': {'vpip': 0.37, 'pfr': 0.30, 'aggression_factor': 2.2, 'hands_observed': 100},
    'TAG': {'vpip': 0.24, 'pfr': 0.20, 'aggression_factor': 2.0, 'hands_observed': 100},
    'Rock': {'vpip': 0.19, 'pfr': 0.12, 'aggression_factor': 1.5, 'hands_observed': 100},
    'Nit': {'vpip': 0.15, 'pfr': 0.10, 'aggression_factor': 1.2, 'hands_observed': 100},
    'Calling Station': {'vpip': 0.45, 'pfr': 0.15, 'aggression_factor': 0.3, 'hands_observed': 100},
    'WeakFish': {'vpip': 0.50, 'pfr': 0.10, 'aggression_factor': 0.4, 'hands_observed': 100},
    'Jeff_clone': {'vpip': 0.39, 'pfr': 0.15, 'aggression_factor': 0.6, 'hands_observed': 100},
    'Punisher_clone': {'vpip': 0.25, 'pfr': 0.22, 'aggression_factor': 2.5, 'hands_observed': 100},
    'CaseBot': {'vpip': 0.95, 'pfr': 0.05, 'aggression_factor': 0.8, 'hands_observed': 100},
    'CaseBotV2': {'vpip': 0.95, 'pfr': 0.20, 'aggression_factor': 1.5, 'hands_observed': 100},
}

TERMINAL_PHASES = {PokerPhase.HAND_OVER, PokerPhase.GAME_OVER}

MAX_ACTIONS_PER_HAND = 100


def apply_adaptation_bias_override(config: dict, bias: Optional[float]) -> dict:
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

# Phase B sizing-defense eval config, set by --sizing-defense in main(). None →
# layer off (byte-identical). When set: {'polar': forced read, 'mult': call mult}.
_SIZING_DEFENSE_CFG: Optional[dict] = None


def _get_hu_table() -> Optional[StrategyTable]:
    """Lazy-load + cache the HU preflop table. Returns None if file missing."""
    global _HU_TABLE_CACHE, _HU_TABLE_CACHED
    if not _HU_TABLE_CACHED:
        _HU_TABLE_CACHE = load_hu_strategy_table()
        _HU_TABLE_CACHED = True
    return _HU_TABLE_CACHE


_DEPTH_TABLES_CACHE: Optional[dict] = None


def _get_depth_tables() -> dict:
    """Lazy-load + cache the shallow 6-max depth charts ({50:.., 25:..}).

    Empty dict if no shallow charts are present (→ no depth adjustment).
    """
    global _DEPTH_TABLES_CACHE
    if _DEPTH_TABLES_CACHE is None:
        _DEPTH_TABLES_CACHE = load_depth_strategy_tables()
    return _DEPTH_TABLES_CACHE


_ARCHETYPE_TABLES_CACHE: Optional[dict] = None


def _get_archetype_tables() -> dict:
    """Lazy-load + cache the width-tier preflop charts keyed by archetype
    ({'loose':.., 'station':.., 'nit':..}). Empty dict if files are missing
    (→ every archetype uses the base table). Mirrors production
    (tiered_factory.build_tiered_controller) so sims and live agree.
    """
    global _ARCHETYPE_TABLES_CACHE
    if _ARCHETYPE_TABLES_CACHE is None:
        _ARCHETYPE_TABLES_CACHE = load_archetype_preflop_tables()
    return _ARCHETYPE_TABLES_CACHE


def make_controller(
    name: str,
    archetype_config: dict,
    strategy_table: StrategyTable,
    sm: PokerStateMachine,
    rng_seed: Optional[int] = None,
    hu_strategy_table: Optional[StrategyTable] = None,
    decision_analysis_repo=None,
    disable_rules: Optional[frozenset] = None,
    game_id: Optional[str] = None,
):
    """Build a controller without LLM/persistence dependencies.

    Dispatches based on archetype_config['kind']:
      - 'rule_bot' (CHAOS_BOTS strategy)  -> RuleBasedController
      - default                            -> TieredBotController / BaselineSolverBot

    Uses the same mock pattern as test_tiered_bot_controller.py for tiered:
    bypass AIPlayerController.__init__ and manually set required attributes.

    Phase 7.6 Step 7: when `decision_analysis_repo` is provided, attaches
    it to the tiered controller so intervention traces + pipeline
    snapshots get persisted via the normal capture path. `disable_rules`
    sets the ablation hook; `game_id` is the row id for persistence.
    These are no-ops for rule_bot / baseline paths.
    """
    # Rule-based controller path: no strategy table, no LLM, no psychology
    if archetype_config.get('kind') == 'rule_bot':
        strategy = archetype_config['strategy']
        fish_leak = archetype_config.get('fish_leak')
        # Use the CHAOS_BOTS preset unless this archetype designates a fish
        # leak, in which case build a fresh config carrying it.
        rule_config = CHAOS_BOTS.get(strategy)
        if rule_config is None or fish_leak is not None:
            rule_config = RuleConfig(strategy=strategy, name=name, fish_leak=fish_leak)
        return RuleBasedController(
            player_name=name,
            state_machine=sm,
            config=rule_config,
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
    # Depth-aware shallow 6-max charts ({50:.., 25:..}); empty → no
    # depth adjustment. Cached module-wide like the HU table.
    controller.depth_strategy_tables = _get_depth_tables()
    # Width-tier preflop charts keyed by archetype (loose/station/tight); empty
    # → every archetype uses the base table. Mirrors production. The hero's
    # explicit --preflop-chart (measure_passivity) clears this so the forced
    # chart wins; Baseline classifies as 'baseline' (not in the map) regardless.
    controller.archetype_preflop_tables = _get_archetype_tables()
    controller.debug_logging = False
    controller.rng = random.Random(rng_seed)
    controller.skip_personality_distortion = is_baseline
    controller._deviation_profile = None if is_baseline else DEVIATION_PROFILES[profile_key]
    # Track B Phase 2 seam: sims don't populate relationship_states,
    # so the modifier seam has nothing to read. Default `False` here
    # (rather than mirroring TieredBotController.__init__'s `True`)
    # because the bypassed-`__init__` factory above doesn't run the
    # constructor that would set up the relationship-state hooks the
    # seam expects. Leaves the offset path identical to pre-Phase-2
    # sim behavior.
    controller.apply_relationship_modifier = False
    controller._last_relationship_modifier = None
    controller._last_relationship_target_id = None
    # SimpleNamespace with anchors is sufficient — get_emotional_shift()
    # gracefully returns 'composed' when zone_effects is unavailable.
    controller.psychology = SimpleNamespace(anchors=anchors)
    controller.prompt_config = SimpleNamespace(strategic_reflection=False)
    controller._current_hand_plans = []
    controller._hand_max_bluff_likelihood = 0

    # Phase 7.6 Step 7: persistence + ablation wiring. Attached only
    # to non-baseline tiered controllers; baselines/rule_bots don't
    # produce traces so persistence is irrelevant.
    if not is_baseline:
        if decision_analysis_repo is not None:
            controller._decision_analysis_repo = decision_analysis_repo
        if disable_rules:
            controller.disable_rules = disable_rules
        if game_id is not None:
            controller.game_id = game_id
        # Phase B sizing defense (SIZING_AWARE_OPPONENT_MODELING.md §B) eval hook.
        # __init__ is bypassed here, so the flags default to absent → off. When the
        # CLI sets --sizing-defense, enable the layer on the tiered hero + force the
        # face-up read via the override (the sim has no matured opponent model);
        # this measures the EV ceiling of folding to a known face-up big bettor.
        if _SIZING_DEFENSE_CFG is not None:
            controller.sizing_defense_enabled = True
            controller.sizing_defense_min_polar = 0.15
            controller.sizing_defense_call_multiplier = _SIZING_DEFENSE_CFG['mult']
            controller.sizing_defense_min_bet_ratio = 0.75
            controller.sizing_defense_polar_override = _SIZING_DEFENSE_CFG['polar']

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
    players = tuple(Player(name=n, stack=starting_stack, is_human=False) for n in player_names)
    return PokerGameState(
        players=players,
        deck=create_deck(shuffled=True, random_seed=seed),
        current_ante=big_blind,
        last_raise_amount=big_blind,
        current_dealer_idx=dealer_idx,
    )


# ── Hand runner ──────────────────────────────────────────────────────────────


def _ensure_sim_game_row(decision_analysis_repo, game_id: str) -> None:
    """Phase 7.6 Step 7: insert a games table row for the sim matchup.

    Idempotent via INSERT OR IGNORE. Needed because
    `player_decision_analysis.game_id` has a FOREIGN KEY → games(game_id)
    constraint; persisting decisions without a parent games row would
    fail. Sets minimum required columns; the rest stay NULL or defaults.

    Marks the row's `phase` field as 'SIM' so analysis tools can
    distinguish sim-produced games from production ones if needed.
    """
    try:
        with decision_analysis_repo._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO games "
                "(game_id, phase, num_players, pot_size, game_state_json, owner_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (game_id, 'SIM', 2, 0.0, '{}', 'simulate_bb100'),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001 — observability degradation
        logger.warning(f"[SIM_PERSIST] Failed to insert games row for {game_id}: {e}")


def _persist_hero_decision(
    hero_controller,
    decision: Dict,
    hand_number: Optional[int],
    phase_name: str,
) -> None:
    """Phase 7.6 Step 7: persist a hero decision's trace + snapshot.

    Bypasses the LLM-coupled `_analyze_decision` path (which requires
    an expression_generator + capture_id). Writes the minimal row Mode
    1/3/4 need: game_id, player_name, hand_number, phase, action_taken,
    intervention_trace_json, strategy_pipeline_snapshot_json.

    No-op when the controller lacks `_decision_analysis_repo` or
    `game_id`. Failures log a WARN and proceed — gameplay never
    blocked by persistence.
    """
    repo = getattr(hero_controller, '_decision_analysis_repo', None)
    game_id = getattr(hero_controller, 'game_id', None)
    if repo is None or game_id is None:
        return
    try:
        from poker.controllers import (
            _serialize_intervention_trace,
            _serialize_pipeline_snapshot,
        )
        from poker.decision_analyzer import DecisionAnalysis

        analysis = DecisionAnalysis(
            game_id=game_id,
            player_name=hero_controller.player_name,
            hand_number=hand_number,
            phase=phase_name,
            action_taken=decision.get('action'),
            raise_amount=decision.get('raise_to'),
            intervention_trace_json=_serialize_intervention_trace(
                getattr(hero_controller, '_last_intervention_trace', None),
                player_name=hero_controller.player_name,
            ),
            strategy_pipeline_snapshot_json=_serialize_pipeline_snapshot(
                getattr(hero_controller, '_last_pipeline_snapshot', None),
                player_name=hero_controller.player_name,
            ),
        )
        repo.save_decision_analysis(analysis)
    except Exception as e:  # noqa: BLE001 — observability degradation
        logger.warning(
            f"[SIM_PERSIST] {hero_controller.player_name}: failed to " f"persist decision: {e}"
        )


def _record_sim_equity_at_actions(
    game_state,
    action_log: List[Tuple[str, str, str, Tuple[str, ...]]],
    opponent_manager: OpponentModelManager,
    hero_name: str,
    equity_seed: Optional[int] = None,
) -> None:
    """Sim-side equivalent of MemoryManager._record_showdown_equity_at_actions.

    `simulate_bb100.run_hand` bypasses `MemoryManager.complete_hand`, so the
    Phase A equity-at-action recorder never fires in sims. Without this
    helper the Phase B polarization signal stays at neutral 0.0 for every
    decision and the gate stays in 'insufficient_sample' mode — Phase B
    becomes a behavioral no-op in sims and the measurement gate (Rock
    recovery vs CaseBot) can't be honestly evaluated.

    Walks the per-action log built during `run_hand`. For each postflop
    bet/raise/call action taken by a non-folded player at hand end (the
    showdown set), computes equity-vs-random using the cards visible at
    action time and credits it into hero's `OpponentModel` of that
    player via `update_equity_at_action`.

    Best-effort: any per-action equity computation failure (cards
    unparseable, board snapshot missing, eval7 unavailable) silently
    skips that action without affecting the rest of the recording or
    the surrounding sim.

    `equity_seed`, when provided, is mixed with the action index to
    seed each per-action Monte Carlo equity calculation. This makes
    the recorded equity values reproducible across sim runs — required
    for deterministic bb/100 measurement at fixed seeds. Without a
    seed, `calculate_equity_vs_random` uses system entropy and the
    sim becomes non-reproducible (the equity values feed the opponent
    model, which feeds tiered-bot exploitation logic, which affects
    hero decisions, which affects game outcomes).
    """
    from poker.card_utils import card_to_string
    from poker.decision_analyzer import DecisionAnalyzer

    revealed = [p for p in game_state.players if not getattr(p, 'is_folded', False)]
    if not revealed:
        return

    # Hero never observes themselves — but the showdown set may include
    # hero. The credit-loop below skips hero as the "opponent" target,
    # so observer-as-target is naturally a no-op.
    n_revealed = len(revealed)

    analyzer = DecisionAnalyzer(iterations=400)

    # Per-action seed counter — paired with `equity_seed` to give each
    # equity calculation a stable, distinct seed. Stays at None when the
    # caller didn't supply a seed (preserving the pre-reproducibility
    # behavior for any future caller that explicitly wants entropy).
    action_idx = 0

    for p in revealed:
        if p.name == hero_name:
            continue
        hand_cards = getattr(p, 'hand', None) or ()
        if len(hand_cards) != 2:
            continue
        try:
            hole_strs = [card_to_string(c) for c in hand_cards]
        except Exception:
            continue

        model = opponent_manager.get_model(hero_name, p.name)

        for actor, action, phase, board_strs in action_log:
            if actor != p.name:
                continue
            if phase not in ('FLOP', 'TURN', 'RIVER'):
                continue
            if action not in ('bet', 'raise', 'call'):
                continue
            if not board_strs:
                continue
            # Equity-vs-random with one opponent slot per other
            # non-folded showdown player matches the production
            # _record_showdown_equity_at_actions convention.
            num_opp = max(1, n_revealed - 1)
            per_action_seed = (
                None
                if equity_seed is None
                else equity_seed + action_idx * 1_000_003  # arbitrary prime stride
            )
            action_idx += 1
            try:
                equity = analyzer.calculate_equity_vs_random(
                    player_hand=hole_strs,
                    community_cards=list(board_strs),
                    num_opponents=num_opp,
                    seed=per_action_seed,
                )
            except Exception:
                continue
            if equity is None:
                continue
            model.tendencies.update_equity_at_action(action, equity)


def run_hand(
    sm: PokerStateMachine,
    controllers: List[TieredBotController],
    big_blind: int,
    verbose: bool = False,
    opponent_manager: Optional[OpponentModelManager] = None,
    hero_name: Optional[str] = None,
    hand_number: Optional[int] = None,
    equity_seed: Optional[int] = None,
    decision_observer: Optional[Callable] = None,
) -> Dict[str, int]:
    """Drive one complete hand to completion.

    Returns dict mapping player name to final stack.

    When ``opponent_manager`` and ``hero_name`` are provided, every non-hero
    action observed during the hand is fed into the manager so the hero
    controller can adapt to opponent tendencies across hands. Default is
    no observation (behavior unchanged).

    ``equity_seed`` is forwarded to `_record_sim_equity_at_actions` so
    the per-action Monte Carlo equity calculations are reproducible.
    Caller typically derives this from `hand_seed` for hand-level
    determinism.

    ``decision_observer`` is an optional diagnostics hook called after the
    controller chooses an action but before ``play_turn`` mutates state. It
    must not alter gameplay state.
    """
    controller_map = {c.player_name: c for c in controllers}

    # Phase 6.6/6.7a: reset of sim-path aggressor state on hero's controller
    # is handled by drive_hand (production paths get it via
    # MemoryManager.on_hand_start; the sim bypasses MM).
    hero_controller = controller_map.get(hero_name) if hero_name else None
    # C-bet detector drives the production state machine. Without this
    # the sim never feeds fold_to_cbet observations into opponent
    # models, leaving the c-bet exploit silently inert.
    cbet_detector = CbetDetector()

    # Polarization Phase A: per-action log for end-of-hand equity-at-action
    # recording. Each entry is (player_name, action, phase, board_snapshot).
    # Board snapshot is captured at action time so the equity computation
    # sees the same board the actor saw. See `_record_sim_equity_at_actions`.
    action_log: List[Tuple[str, str, str, Tuple[str, ...]]] = []

    def _on_decision(
        current_player,
        controller,
        action,
        raise_to,
        phase_name,
        gs,
        sim_current_street,
        decision,
    ):
        # Phase 7.6 Step 7: persist hero's decision trace + snapshot when
        # the controller is configured for it. Tiered controllers only —
        # rule_bots don't produce traces. No-op when not configured.
        if (
            hero_name is not None
            and current_player.name == hero_name
            and getattr(controller, '_decision_analysis_repo', None) is not None
        ):
            _persist_hero_decision(
                hero_controller=controller,
                decision=decision,
                hand_number=hand_number,
                phase_name=phase_name,
            )

        if verbose:
            logger.info(
                f"  {current_player.name}: {action}" f"{f' to {raise_to}' if raise_to else ''}"
            )

        if decision_observer is not None:
            decision_observer(
                current_player,
                controller,
                action,
                raise_to,
                phase_name,
                gs,
                sim_current_street,
                decision,
            )

        # Polarization Phase A: capture board snapshot at action time
        # for end-of-hand equity-at-action recording. Skip preflop
        # actions (the recorder is postflop-only); skip if community
        # cards can't be stringified.
        if (
            opponent_manager is not None
            and hero_name is not None
            and phase_name in ('FLOP', 'TURN', 'RIVER')
        ):
            try:
                from poker.card_utils import card_to_string

                board_snapshot = tuple(card_to_string(c) for c in gs.community_cards)
            except Exception:
                board_snapshot = ()
            action_log.append((current_player.name, action, phase_name, board_snapshot))

        # Snapshot active players BEFORE play_turn — CbetDetector needs
        # the pre-fold view to seed its facing-set on flop c-bets. Stashed on
        # the closure so the post_action hook (which drives the detector after
        # play_turn) sees the same pre-fold view the original inline code did.
        _on_decision.active_players_snapshot = [
            p.name for p in gs.players if not getattr(p, 'is_folded', False)
        ]

        # Compute was_facing_bet BEFORE cbet_detector updates (mirror
        # AIMemoryManager.on_action). Required for opportunity-normalized
        # VPIP/PFR counters preflop and AF-postflop axes postflop.
        if phase_name in ('FLOP', 'TURN', 'RIVER'):
            recent_postflop = (
                getattr(
                    hero_controller,
                    '_sim_recent_aggressor',
                    None,
                )
                if hero_controller is not None
                else None
            )
            was_facing_bet_snapshot = (
                recent_postflop is not None
                and recent_postflop != current_player.name
                and sim_current_street == phase_name
            )
        elif phase_name == 'PRE_FLOP':
            prior_raiser = cbet_detector.preflop_aggressor
            was_facing_bet_snapshot = (
                prior_raiser is not None and prior_raiser != current_player.name
            )
        else:
            was_facing_bet_snapshot = None

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
                was_facing_bet=was_facing_bet_snapshot,
            )

    def _post_action(current_player, action, raise_to, phase_name, gs, new_gs):
        # Drive the c-bet detector and apply any fold_to_cbet
        # observations to the hero's opponent model. Hero's own actions
        # still feed the state machine (e.g. hero's preflop raise sets
        # them as c-bet aggressor) but produce no self-observation.
        if opponent_manager is not None and hero_name is not None:
            cbet_responses = cbet_detector.record_action(
                player_name=current_player.name,
                action=action,
                phase=phase_name,
                active_players=_on_decision.active_players_snapshot,
            )
            for opp_name, folded in cbet_responses:
                if opp_name == hero_name:
                    continue  # hero observing hero is not useful
                model = opponent_manager.get_model(hero_name, opp_name)
                model.tendencies.update_fold_to_cbet(folded)

    drive_hand(
        sm,
        controllers,
        hero_name=hero_name,
        hero_controller=hero_controller,
        on_decision=_on_decision,
        post_action=_post_action,
        on_max_actions=lambda: logger.warning("Max actions reached — terminating hand"),
    )

    # Polarization Phase A: end-of-hand equity-at-action recording.
    # Walks the per-action log and credits equity into hero's models of
    # the non-folded (showdown) opponents. Wrapped broadly — equity
    # recording is enrichment, not a hard requirement, and a failure
    # mid-recording shouldn't affect the rest of the sim.
    if opponent_manager is not None and hero_name is not None and action_log:
        try:
            _record_sim_equity_at_actions(
                sm.game_state,
                action_log,
                opponent_manager,
                hero_name,
                equity_seed=equity_seed,
            )
        except Exception as e:
            logger.warning(f"Phase A sim equity recording failed: {e}")

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
    decision_analysis_repo=None,
    disable_rules: Optional[frozenset] = None,
    game_id: Optional[str] = None,
    enable_session_drift: bool = False,
) -> List[float]:
    """Run n_hands between two archetypes.

    Returns list of per-hand chip deltas for player A.
    Uses unique player names (P1/P2) to avoid collision in mirror matchups.

    Phase 7.6 Step 7: when `decision_analysis_repo` + `game_id` are
    provided, hero's per-decision trace + pipeline snapshot are
    persisted for downstream Mode 1/4 analysis. `disable_rules`
    suppresses the named (layer, rule_id) pairs in the hero pipeline.

    `enable_session_drift` (default False): when True, perturb each
    archetype's anchors once per matchup via `apply_session_drift`
    (drift_strength derived from poise + recovery_rate). The drifted
    anchors then drive every hand in this matchup — drift is per-
    session, not per-hand. Default off so the post-patch baselines
    measured for the Phase B gate stay reproducible.
    """
    config_a = apply_adaptation_bias_override(ARCHETYPES[archetype_a], hero_adaptation_bias)
    config_b = ARCHETYPES[archetype_b]

    if enable_session_drift:
        from poker.psychology_model import apply_session_drift

        # Stable per-matchup seeds for drift — derived from base_seed so
        # the same matchup with the same base_seed produces identical
        # drift, even across separate invocations. Mixed with two
        # distinct multipliers so the two archetypes draw independent
        # noise. The multipliers are arbitrary primes; their job is
        # decorrelation, not cryptographic quality.
        drift_seed_a = base_seed * 7919 + 11
        drift_seed_b = base_seed * 7919 + 17
        # Shallow-copy config_a/_b before mutating — config_b is a
        # direct reference into the global ARCHETYPES dict and must
        # not be mutated in place (would corrupt subsequent matchups).
        # config_a came from apply_adaptation_bias_override and is
        # already a shallow copy, but the symmetry is cheap.
        config_a = dict(config_a)
        config_b = dict(config_b)
        for cfg, drift_seed in ((config_a, drift_seed_a), (config_b, drift_seed_b)):
            anchors = cfg.get('anchors')
            if anchors is not None:
                cfg['anchors'] = apply_session_drift(
                    anchors,
                    random.Random(drift_seed),
                )

    name_a = 'P1'
    name_b = 'P2'
    deltas_a: List[float] = []

    # Phase 6: one manager per matchup so observations accumulate across
    # hands. Hero is name_a (the first archetype). Manager is attached to
    # ctrl_a below in the hand loop.
    opponent_manager = OpponentModelManager()

    # Phase 7.6 Step 7: insert a games-table row so the FK on
    # player_decision_analysis is satisfied. Idempotent — uses
    # INSERT OR IGNORE.
    if decision_analysis_repo is not None and game_id is not None:
        _ensure_sim_game_row(decision_analysis_repo, game_id)

    for hand_num in tqdm(
        range(n_hands), desc=f"  {archetype_a} vs {archetype_b}", leave=False, file=sys.stderr
    ):
        hand_seed = base_seed + hand_num
        dealer_idx = hand_num % 2  # alternate button for fairness

        # Re-seed the global `random` module per hand. Several downstream
        # consumers (eval7.Deck().shuffle() inside calculate_quick_equity,
        # rule strategies' fallback paths, etc.) read from the global
        # random state. Without per-hand re-seeding, cross-matchup
        # state varies based on prior matchups' consumption — including
        # any drift-induced changes to tiered controllers' decision
        # schedule, which advances global random differently and breaks
        # reproducibility for downstream rule_bot matchups. Per-hand
        # seeding isolates each hand to its own deterministic prefix.
        random.seed(hand_seed)

        gs = make_game_state(
            player_names=[name_a, name_b],
            big_blind=big_blind,
            starting_stack=starting_stack,
            dealer_idx=dealer_idx,
            seed=hand_seed,
        )
        sm = PokerStateMachine(gs)
        # Tell the state machine the deck seed was provided. Otherwise
        # `_resolve_hand_seed` falls back to `random.getrandbits(32)`
        # (global random state) and reshuffles the deck on the first
        # initialize_hand_transition — making the sim non-deterministic
        # despite `make_game_state(seed=hand_seed)` setting up a
        # deterministic initial deck.
        sm.current_hand_seed = hand_seed

        ctrl_a = make_controller(
            name_a,
            config_a,
            strategy_table,
            sm,
            rng_seed=hand_seed,
            decision_analysis_repo=decision_analysis_repo,
            disable_rules=disable_rules,
            game_id=game_id,
        )
        ctrl_b = make_controller(
            name_b,
            config_b,
            strategy_table,
            sm,
            rng_seed=hand_seed + 1_000_000,
        )

        # Attach the shared manager to the hero controller for this hand.
        ctrl_a.opponent_model_manager = opponent_manager
        opponent_manager.record_hand_dealt(
            observer=name_a,
            opponents=[name_b],
            hand_number=hand_num,
        )

        final_stacks = run_hand(
            sm,
            [ctrl_a, ctrl_b],
            big_blind,
            verbose=verbose,
            opponent_manager=opponent_manager,
            hero_name=name_a,
            hand_number=hand_num,
            # Stable per-hand seed for the Phase A equity Monte Carlo.
            # Without this, calculate_equity_vs_random uses an unseeded
            # Random and recorded equity values vary across runs — which
            # feeds the opponent model and breaks sim reproducibility.
            equity_seed=hand_seed * 31 + 7,
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
    disable_rules: Optional[frozenset] = None,
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
        raise ValueError(f"opponents must have 5 entries, got {len(opponents)}")

    # Disambiguate hero from any opponent that happens to share the archetype name
    hero_name = archetype if archetype not in opponents else f"{archetype}_hero"
    opponent_seats = _make_seat_names(opponents)
    if hero_name in opponent_seats:
        hero_name = f"{archetype}_hero"
    archetype_seat = hero_name
    all_names = [archetype_seat] + opponent_seats

    config_arch = apply_adaptation_bias_override(ARCHETYPES[archetype], hero_adaptation_bias)
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
        leave=False,
        file=sys.stderr,
    ):
        hand_seed = base_seed + hand_num
        dealer_idx = hand_num % 6  # rotate button through all 6 seats

        random.seed(hand_seed)  # see HU branch — per-hand global-random reset

        gs = make_game_state(
            player_names=all_names,
            big_blind=big_blind,
            starting_stack=starting_stack,
            dealer_idx=dealer_idx,
            seed=hand_seed,
        )
        sm = PokerStateMachine(gs)
        sm.current_hand_seed = hand_seed  # see HU branch above

        controllers = [
            make_controller(
                archetype_seat,
                config_arch,
                strategy_table,
                sm,
                rng_seed=hand_seed,
                disable_rules=disable_rules,  # hero only — ablation target
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

        # Attach the shared manager to the hero controller for this hand.
        controllers[0].opponent_model_manager = opponent_manager
        opponent_manager.record_hand_dealt(
            observer=archetype_seat,
            opponents=opponent_seats,
            hand_number=hand_num,
        )

        final_stacks = run_hand(
            sm,
            controllers,
            big_blind,
            verbose=verbose,
            opponent_manager=opponent_manager,
            hero_name=archetype_seat,
            hand_number=hand_num,
            equity_seed=hand_seed * 31 + 7,
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
    print("Format: 1 archetype + 5 Baselines, dealer rotates")
    print(f"Stack: {starting_stack}, BB: {big_blind}")
    print("=" * 67)

    results: Dict[str, MatchupStats] = {}

    # Skip 'Baseline' as test subject AND as opponent — only test personality archetypes
    test_archetypes = [n for n in ARCHETYPES if n != 'Baseline']

    # Include a Baseline-as-subject run for mirror sanity check
    test_archetypes.append('Baseline')

    for name in test_archetypes:
        deltas = run_6max_matchup(
            name,
            n_hands,
            strategy_table,
            big_blind=big_blind,
            starting_stack=starting_stack,
            base_seed=seed,
            verbose=verbose,
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
    disable_rules: Optional[frozenset] = None,
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
    if disable_rules:
        rules_str = ', '.join(f'{l}.{r}' for (l, r) in sorted(disable_rules))
        print(f"Disabled (hero only): {rules_str}")
    print("=" * 67)

    # Test all tiered archetypes (not the rule_bots themselves)
    test_archetypes = [
        n for n, cfg in ARCHETYPES.items() if cfg.get('kind') != 'rule_bot' and n != 'Baseline'
    ]
    # Include Baseline as a sanity reference
    test_archetypes.append('Baseline')

    results: Dict[str, MatchupStats] = {}
    for name in test_archetypes:
        deltas = run_6max_matchup(
            name,
            n_hands,
            strategy_table,
            big_blind=big_blind,
            starting_stack=starting_stack,
            base_seed=seed,
            verbose=verbose,
            opponents=opponents,
            hero_adaptation_bias=hero_adaptation_bias,
            disable_rules=disable_rules,
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
    decision_analysis_repo=None,
    disable_rules: Optional[frozenset] = None,
    game_id_prefix: Optional[str] = None,
    enable_session_drift: bool = False,
):
    """Run each archetype heads-up vs TAG (or specified opponent)."""
    print(f"\nBB/100 Simulation: {n_hands} hands per matchup, seed={seed}")
    print(f"Opponent: {opponent}, Stack: {starting_stack}, BB: {big_blind}")
    if hero_adaptation_bias is not None:
        print(f"Hero adaptation_bias overridden to: {hero_adaptation_bias}")
    if disable_rules:
        rules_str = ', '.join(f'{l}.{r}' for (l, r) in sorted(disable_rules))
        print(f"Disabled rules: {rules_str}")
    if enable_session_drift:
        print("Session drift: ENABLED (anchors perturbed once per matchup)")
    print("=" * 67)

    results: Dict[str, MatchupStats] = {}

    for name in ARCHETYPES:
        # Phase 7.6 Step 7: per-matchup game_id for trace persistence.
        matchup_game_id = (
            f'{game_id_prefix}_{name}_vs_{opponent}' if game_id_prefix is not None else None
        )
        deltas = run_matchup(
            name,
            opponent,
            n_hands,
            strategy_table,
            big_blind=big_blind,
            starting_stack=starting_stack,
            base_seed=seed,
            verbose=verbose,
            hero_adaptation_bias=hero_adaptation_bias,
            decision_analysis_repo=decision_analysis_repo,
            disable_rules=disable_rules,
            game_id=matchup_game_id,
            enable_session_drift=enable_session_drift,
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
            a,
            b,
            n_hands,
            strategy_table,
            big_blind=big_blind,
            starting_stack=starting_stack,
            base_seed=seed,
            verbose=verbose,
        )
        # A's deltas
        all_deltas[a].extend(deltas_a)
        # B's deltas are the inverse
        all_deltas[b].extend([-d for d in deltas_a])

    # Compute aggregate stats per archetype
    results = {name: compute_stats(deltas, big_blind) for name, deltas in all_deltas.items()}

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
        '--hands',
        type=int,
        default=1000,
        help='Hands per matchup (default: 1000)',
    )
    parser.add_argument(
        '--seed',
        type=int,
        default=42,
        help='RNG seed (default: 42)',
    )
    parser.add_argument(
        '--big-blind',
        type=int,
        default=100,
        help='Big blind size (default: 100)',
    )
    parser.add_argument(
        '--stack',
        type=int,
        default=10000,
        help='Starting stack (default: 10000)',
    )
    parser.add_argument(
        '--round-robin',
        action='store_true',
        help='Run all 15 pairings instead of just vs TAG',
    )
    parser.add_argument(
        '--six-max',
        action='store_true',
        help='Run 6-max: 1 archetype + 5 BaselineSolverBots per matchup',
    )
    parser.add_argument(
        '--six-max-vs-rules',
        action='store_true',
        help='Run 6-max vs a mix of 5 rule_bots (GTO-Lite, ABCBot, CaseBot, CallStation, ManiacBot)',
    )
    parser.add_argument(
        '--opponents',
        type=str,
        default=None,
        help='Comma-separated list of 5 ARCHETYPES keys to override the default '
        'rule_bot mix when using --six-max-vs-rules. Example: '
        '"CaseBot,CaseBot,CaseBot,GTO-Lite,ABCBot"',
    )
    parser.add_argument(
        '--opponent',
        type=str,
        default='TAG',
        help='Baseline opponent for vs-all mode (default: TAG)',
    )
    parser.add_argument(
        '--adaptation-bias',
        type=float,
        default=None,
        help='Override adaptation_bias on the hero archetype anchors '
        '(Phase 6 validation: use 0.05 for no-exploit floor, 0.85 '
        'for full exploitation). Applies only to 6-max modes.',
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Per-hand action logging',
    )
    # Phase 7.6 Step 7: persistence + ablation hooks. When --db is
    # provided, hero decisions persist intervention traces +
    # strategy_pipeline_snapshot JSON to player_decision_analysis,
    # making the data consumable by analyze_intervention_traces.py
    # (Modes 1/3/4 in particular).
    parser.add_argument(
        '--db',
        type=str,
        default=None,
        help='Phase 7.6: path to poker_games.db. When provided, hero '
        'decisions persist intervention_trace_json + '
        'strategy_pipeline_snapshot_json for analyze_intervention_'
        'traces.py consumption. Default: no persistence.',
    )
    parser.add_argument(
        '--game-id-prefix',
        type=str,
        default=None,
        help='Phase 7.6: game_id prefix for persisted matchups. Each '
        'matchup gets f"{prefix}_{hero}_vs_{opponent}". Defaults '
        'to f"sim_seed{seed}".',
    )
    parser.add_argument(
        '--disable-rule',
        type=str,
        action='append',
        default=None,
        help='Phase 7.6: ablate a rule in the hero strategy pipeline. '
        'Format: "layer.rule_id" (e.g. "bluff_catch_override.default"). '
        'Repeatable for multi-rule ablation. Has no effect without '
        '--db (the persisted traces are the only way to see the '
        'effect downstream).',
    )
    parser.add_argument(
        '--enable-session-drift',
        action='store_true',
        help='Perturb each archetype\'s anchors once per matchup via '
        'apply_session_drift (drift_strength derived from poise + '
        'recovery_rate). Off by default so post-patch baseline '
        'measurements stay reproducible. Stoic archetypes (high '
        'poise + recovery_rate, e.g. Nit) drift very little; '
        'volatile ones (Maniac, LAG) drift visibly.',
    )
    parser.add_argument(
        '--clone-opponent',
        type=str,
        action='append',
        default=None,
        metavar='PLAYER_NAME',
        help='Derive a CloneProfile from opponent_models for the named '
        'human/AI player and register it as an opponent archetype '
        '"<PLAYER_NAME>_clone". Repeatable. Reference the clone via '
        '--opponents (e.g. --clone-opponent Jeff --opponents '
        '"Jeff_clone,CaseBot,CaseBot,CaseBot,CaseBot"). Requires the '
        'player to have ≥20 hands_observed in opponent_models.',
    )
    parser.add_argument(
        '--clone-profile',
        type=str,
        action='append',
        default=None,
        metavar='PROFILE_JSON',
        help='Load a frozen CloneProfile from a JSON file (produced by '
        'human_clone.dump_profile_to_file, e.g. '
        'experiments/clone_profiles/jeff.json) and register it as '
        'archetype "<source_player>_clone". Repeatable. Unlike '
        '--clone-opponent this needs no DB — the snapshot is fully '
        'portable across checkouts/machines. Reference via --opponents '
        '(e.g. --clone-profile experiments/clone_profiles/jeff.json '
        '--opponents "Jeff_clone,CaseBot,CaseBot,CaseBot,CaseBot").',
    )
    parser.add_argument(
        '--sizing-defense',
        action='store_true',
        help='Phase B (SIZING_AWARE_OPPONENT_MODELING.md §B): enable the tiered '
        'hero\'s fold-more-vs-face-up-big-bettor layer + force the face-up read '
        '(via the polar override) so its EV ceiling is measurable vs a face-up '
        'opponent like LooseFaceUp. Off → byte-identical. A/B by running once '
        'without and once with this flag vs the same opponents/seed.',
    )
    parser.add_argument(
        '--sizing-defense-polar',
        type=float,
        default=0.3,
        help='Forced sizing_polarization_score for the hero\'s read when '
        '--sizing-defense is set (default 0.3, clearly face-up). Lower it to '
        'probe the threshold sensitivity.',
    )
    parser.add_argument(
        '--sizing-defense-mult',
        type=float,
        default=0.55,
        help='Call-retention multiplier for the sizing-defense layer (default '
        '0.55 — retain ~55%% of baseline calls vs a face-up big bet).',
    )
    args = parser.parse_args()

    if args.sizing_defense:
        global _SIZING_DEFENSE_CFG
        _SIZING_DEFENSE_CFG = {
            'polar': args.sizing_defense_polar,
            'mult': args.sizing_defense_mult,
        }
        print(
            f"Phase B sizing-defense ON (hero): forced polar="
            f"{args.sizing_defense_polar:+.2f}, call_mult={args.sizing_defense_mult:.2f}"
        )

    # Register any clone opponents the user requested. Do this before
    # ARCHETYPES is read by the matchup runners so the new entries are
    # visible. Fails loud rather than silently swallowing — bad
    # --clone-opponent name should stop the run, not produce a misleading
    # "opponent not found" downstream.
    if args.clone_opponent:
        import os as _os

        from poker.human_clone import derive_profile_from_db, register_clone_strategy

        clone_db = args.db or (
            '/app/data/poker_games.db'
            if _os.path.exists('/app/data/poker_games.db')
            else 'poker_games.db'
        )
        for player_name in args.clone_opponent:
            profile = derive_profile_from_db(clone_db, player_name)
            strategy_key = f"clone_{player_name.replace(' ', '_').lower()}"
            register_clone_strategy(strategy_key, profile)
            archetype_key = f"{player_name}_clone"
            ARCHETYPES[archetype_key] = {
                'kind': 'rule_bot',
                'strategy': strategy_key,
            }
            print(
                f"[CLONE] Registered {archetype_key!r} from {profile.hands_observed} "
                f"observed hand(s) — vpip={profile.vpip:.2f} pfr={profile.pfr:.2f} "
                f"af={profile.aggression_factor:.2f} ftc={profile.fold_to_cbet:.2f}"
            )

    # Register any clone opponents loaded from frozen JSON snapshots. Same
    # archetype wiring as --clone-opponent, but the profile comes from a
    # portable file instead of the local DB, so this works on any checkout.
    if args.clone_profile:
        from poker.human_clone import load_profile_from_file, register_clone_strategy

        for profile_path in args.clone_profile:
            profile = load_profile_from_file(profile_path)
            player_name = profile.source_player
            strategy_key = f"clone_{player_name.replace(' ', '_').lower()}"
            register_clone_strategy(strategy_key, profile)
            archetype_key = f"{player_name}_clone"
            ARCHETYPES[archetype_key] = {
                'kind': 'rule_bot',
                'strategy': strategy_key,
            }
            print(
                f"[CLONE] Loaded {archetype_key!r} from {profile_path} — "
                f"{profile.hands_observed} observed hand(s), "
                f"vpip={profile.vpip:.2f} pfr={profile.pfr:.2f} "
                f"af={profile.aggression_factor:.2f} ftc={profile.fold_to_cbet:.2f}"
            )

    # Seed the global `random` module so any code path that calls
    # `random.random()` / `random.choice()` / `random.getrandbits()` etc.
    # (rather than an instance method on a seeded Random) becomes
    # reproducible across CLI invocations. Without this, the sim's
    # per-CLI-run output varies because the global random module is
    # auto-seeded from os.urandom per process. Specific known consumers
    # we depend on being deterministic: state-machine fallback path
    # (`_resolve_hand_seed`), AI fallback strategies, chat/commentary
    # randomness. All instance-level RNGs (controller `self.rng`,
    # `create_deck(random_seed=...)`, equity Monte Carlo seed-aware
    # path) are seeded separately and don't depend on this.
    random.seed(args.seed)

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format='%(message)s')
    else:
        logging.basicConfig(level=logging.WARNING, format='%(message)s')

    # Suppress noisy emotional shift warnings from bounded_options
    # (SimpleNamespace psychology doesn't have zone_effects — expected)
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)

    strategy_table = load_strategy_table()

    # Phase 7.6 Step 7: parse persistence + ablation args.
    decision_analysis_repo = None
    disable_rules: Optional[frozenset] = None
    game_id_prefix = args.game_id_prefix
    if args.db:
        from poker.repositories.decision_analysis_repository import (
            DecisionAnalysisRepository,
        )
        from poker.repositories.schema_manager import SchemaManager

        # Ensure schema is up to date (creates DB if missing).
        SchemaManager(args.db).ensure_schema()
        decision_analysis_repo = DecisionAnalysisRepository(args.db)
        if game_id_prefix is None:
            game_id_prefix = f'sim_seed{args.seed}'
        print(
            f"Phase 7.6: persisting hero decision traces + snapshots "
            f"to {args.db} with game_id prefix '{game_id_prefix}'"
        )
    if args.disable_rule:
        parsed: List[Tuple[str, str]] = []
        for entry in args.disable_rule:
            if '.' not in entry:
                print(f"--disable-rule {entry!r} must be in 'layer.rule_id' format")
                sys.exit(2)
            layer, rule_id = entry.split('.', 1)
            parsed.append((layer, rule_id))
        disable_rules = frozenset(parsed)
        if not args.db:
            print(
                "--disable-rule has no observable effect without --db "
                "(traces aren't persisted otherwise); proceeding anyway"
            )

    if args.six_max_vs_rules:
        custom_opp = None
        if args.opponents:
            custom_opp = [o.strip() for o in args.opponents.split(',')]
            if len(custom_opp) != 5:
                print(f"--opponents must have 5 entries, got {len(custom_opp)}")
                sys.exit(1)
        run_all_6max_vs_rules(
            args.hands,
            strategy_table,
            args.big_blind,
            args.stack,
            args.seed,
            verbose=args.verbose,
            opponents=custom_opp,
            hero_adaptation_bias=args.adaptation_bias,
            disable_rules=disable_rules,
        )
    elif args.six_max:
        run_all_6max_vs_baseline(
            args.hands,
            strategy_table,
            args.big_blind,
            args.stack,
            args.seed,
            verbose=args.verbose,
            hero_adaptation_bias=args.adaptation_bias,
        )
    elif args.round_robin:
        run_round_robin(
            args.hands,
            strategy_table,
            args.big_blind,
            args.stack,
            args.seed,
            verbose=args.verbose,
        )
    else:
        run_all_vs_tag(
            args.hands,
            strategy_table,
            args.big_blind,
            args.stack,
            args.seed,
            verbose=args.verbose,
            opponent=args.opponent,
            hero_adaptation_bias=args.adaptation_bias,
            decision_analysis_repo=decision_analysis_repo,
            disable_rules=disable_rules,
            game_id_prefix=game_id_prefix,
            enable_session_drift=args.enable_session_drift,
        )


if __name__ == '__main__':
    main()
