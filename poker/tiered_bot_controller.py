"""
TieredBotController - Solver baselines + personality distortion, no LLM decisions.

A subclass of AIPlayerController that:
- Makes decisions via solver-derived strategy tables + personality distortion
- Has full psychology system (tilt, emotions, axes)
- Never uses LLM for decisions (LLM is expression layer only, Phase 4)

Phases:
- Preflop: full strategy table lookup + personality modifier
- Postflop: hand-crafted flop strategies + turn/river heuristics + personality
"""

import logging
import random
from typing import Dict, List, Optional

from .controllers import AIPlayerController, _get_canonical_hand
from .card_utils import card_to_string
from .bounded_options import get_emotional_shift
from .strategy.nodes import PreflopNode
from .strategy.strategy_table import StrategyTable
from .strategy.preflop_classifier import build_preflop_node, get_6max_position
from .strategy.postflop_classifier import build_postflop_node
from .strategy.personality_modifier import modify_strategy, apply_river_bluff_guardrail
from .strategy.deviation_profiles import select_deviation_profile, DeviationProfile
from .strategy.action_mapper import resolve_preflop_sizing, resolve_postflop_sizing
from .strategy.hand_classification import simplify_hand_class
from .strategy.multiway import apply_multiway_adjustment
from .strategy.math_floor import apply_pot_odds_floor
from .strategy.exploitation import (
    compute_exploitation_offsets,
    apply_exploitation_offsets,
    classify_detected_patterns,
    DecisionContext,
    AggregatedOpponentStats,
)
from .strategy.value_override import (
    HandStrengthClass,
    should_apply_value_override,
    compute_value_override_strategy,
)
from .hand_tiers import is_hand_in_range
from .strategy.expression_context import ExpressionContext
from .strategy.expression_generator import ExpressionGenerator
from .archetypes import classify_from_anchors

logger = logging.getLogger(__name__)


class TieredBotController(AIPlayerController):
    """AI player using 3-layer tiered architecture.

    Layer 1: Solver-derived baselines (strategy table lookup)
    Layer 2: Personality distortion (logit-space modification)
    Layer 3: Expression (LLM narrates - not implemented in Phase 1)
    """

    def __init__(
        self,
        player_name: str,
        strategy_table: StrategyTable,
        state_machine=None,
        llm_config=None,
        debug_logging: bool = False,
        rng_seed=None,
        skip_personality_distortion: bool = False,
        expression_generator: Optional[ExpressionGenerator] = None,
        **kwargs,
    ):
        super().__init__(
            player_name=player_name,
            state_machine=state_machine,
            llm_config=llm_config,
            **kwargs,
        )
        self.strategy_table = strategy_table
        self.debug_logging = debug_logging
        self.rng = random.Random(rng_seed)
        self._deviation_profile: Optional[DeviationProfile] = None
        self.skip_personality_distortion = skip_personality_distortion
        self.expression_generator = expression_generator

    @property
    def deviation_profile(self) -> DeviationProfile:
        """Lazy-init deviation profile from personality anchors."""
        if self._deviation_profile is None:
            if self.psychology and self.psychology.anchors:
                self._deviation_profile = select_deviation_profile(self.psychology.anchors)
            else:
                # Fallback to TAG if no psychology loaded yet
                from .strategy.deviation_profiles import DEVIATION_PROFILES
                self._deviation_profile = DEVIATION_PROFILES['tag']
        return self._deviation_profile

    @property
    def archetype_name(self) -> str:
        """Get personality archetype name from anchors."""
        if self.skip_personality_distortion:
            return 'baseline'
        anchors = self.psychology.anchors if self.psychology else None
        if not anchors:
            return 'tag'
        if anchors.baseline_looseness < 0.25 and anchors.baseline_aggression < 0.25:
            return 'nit'
        if anchors.baseline_looseness > 0.80 and anchors.baseline_aggression > 0.80:
            return 'maniac'
        base = classify_from_anchors(
            anchors.baseline_looseness, anchors.baseline_aggression
        )
        return {
            'tight_passive': 'rock', 'tight_aggressive': 'tag',
            'loose_passive': 'calling_station', 'loose_aggressive': 'lag',
            'default': 'tag',
        }.get(base, 'tag')

    def decide_action(self, game_messages=None) -> Dict:
        """Tiered decision: bypass the LLM-coupled parent pipeline.

        The parent AIPlayerController.decide_action runs LLM bookkeeping
        (conversation memory, chattiness checks, message summarization) that
        TieredBotController doesn't need — decisions come from strategy
        tables, not the LLM. We go straight to _get_ai_decision.

        The optional expression layer (Layer 3) is invoked inside
        _get_ai_decision via _attach_expression after the action commits.
        """
        game_state = self.state_machine.game_state
        try:
            valid_actions = game_state.current_player_options
        except Exception:
            valid_actions = ['fold', 'check', 'call', 'raise']
        return self._get_ai_decision(
            message='',
            valid_actions=valid_actions,
            call_amount=getattr(game_state, 'call_amount', 0) or 0,
        )

    def _get_ai_decision(self, message: str, **context) -> Dict:
        """Override: Use strategy tables + personality distortion instead of LLM.

        Routes to preflop or postflop decision logic based on game phase.
        """
        game_state = self.state_machine.game_state
        player = game_state.current_player
        player_idx = game_state.current_player_idx
        valid_actions = context.get('valid_actions', [])
        phase = self.state_machine.current_phase

        is_preflop = phase and phase.name == 'PRE_FLOP'
        if not is_preflop:
            return self._get_postflop_decision(
                game_state, player_idx, valid_actions, context
            )

        # ── Preflop decision ──
        hole_cards = [card_to_string(c) for c in player.hand] if player.hand else []
        canonical_hand = _get_canonical_hand(hole_cards) if hole_cards else ''

        if not canonical_hand:
            logger.warning(f"[TIERED_BOT] {self.player_name}: No canonical hand, using fallback")
            return self._postflop_fallback(valid_actions)

        node = build_preflop_node(game_state, player_idx, canonical_hand)

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"hand={canonical_hand} node_key={node.key}"
            )

        # Layer 1: Lookup base strategy
        base_strategy = self.strategy_table.lookup_with_fallback(node, valid_actions)

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"base_strategy={base_strategy.action_probabilities}"
            )

        # Layer 2: Personality distortion (skipped for BaselineSolverBot)
        emotional_state = get_emotional_shift(self.psychology)
        anchors = self.psychology.anchors if self.psychology else None

        if anchors and not self.skip_personality_distortion:
            modified_strategy = modify_strategy(
                base=base_strategy,
                legal_actions=valid_actions,
                anchors=anchors,
                emotional_state=emotional_state,
                deviation_profile=self.deviation_profile,
            )
        else:
            modified_strategy = base_strategy

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"modified_strategy={modified_strategy.action_probabilities}"
            )

        # Phase 6: opponent exploitation (between personality and math floor)
        modified_strategy = self._apply_exploitation(
            modified_strategy, game_state, player_idx, valid_actions,
            anchors, emotional_state,
        )

        # Phase 6.5: strong-hand value override.
        # Replaces strategy entirely when hero has a top-tier hand vs a
        # detected hyper-aggressive opponent — offsets can't shift
        # probability mass enough for these spots.
        modified_strategy = self._apply_value_override(
            modified_strategy, game_state, player_idx, valid_actions,
            anchors, emotional_state,
            hand_strength=self._classify_preflop_hand_strength(canonical_hand, anchors),
        )

        # Math floor: override when pot odds / pot-committed / short stack
        # make personality-driven folds clearly -EV.
        modified_strategy = self._apply_math_floor(
            modified_strategy, game_state, player_idx, valid_actions
        )

        abstract_action = modified_strategy.sample_action(self.rng)

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"sampled={abstract_action} emotional={emotional_state.state}"
            )

        game_action, raise_to = resolve_preflop_sizing(
            abstract_action, game_state, player_idx
        )

        if game_action not in valid_actions:
            game_action, raise_to = self._validate_action(
                game_action, raise_to, valid_actions
            )

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"final_action={game_action} raise_to={raise_to}"
            )

        decision = {
            'action': game_action,
            'raise_to': raise_to,
            'dramatic_sequence': [],
            'hand_strategy': (
                f"Tiered bot: {node.scenario} {node.position} "
                f"with {canonical_hand} -> {abstract_action}"
            ),
            'inner_monologue': '',
            'bluff_likelihood': 0,
        }
        self._attach_expression(decision, game_state, player_idx, phase='pre_flop')
        return decision

    def _get_postflop_decision(
        self, game_state, player_idx: int,
        valid_actions: List[str], context: dict,
    ) -> Dict:
        """Postflop decision: strategy table + personality + multiway + guardrails."""
        player = game_state.players[player_idx]

        # 1. Convert cards to string format
        hole_cards = [card_to_string(c) for c in player.hand] if player.hand else []
        community_cards = [
            card_to_string(c) for c in game_state.community_cards
        ] if game_state.community_cards else []

        if not hole_cards or len(community_cards) < 3:
            return self._postflop_fallback(valid_actions)

        # 2. Build PostflopNode
        try:
            node = build_postflop_node(
                game_state, player_idx, hole_cards, community_cards
            )
        except Exception as e:
            logger.warning(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop_classifier error: {e}, using fallback"
            )
            return self._postflop_fallback(valid_actions)

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop node_key={node.key}"
            )

        # 3. Lookup base strategy (with texture fallback)
        base_strategy = self.strategy_table.lookup_postflop_with_fallback(
            node, valid_actions
        )

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop base_strategy={base_strategy.action_probabilities}"
            )

        # 4. Multiway adjustment (if > 2 active players)
        active_count = sum(
            1 for p in game_state.players
            if not p.is_folded
        )
        if active_count > 2:
            base_strategy = apply_multiway_adjustment(
                base_strategy, active_count, node.position
            )
            if self.debug_logging:
                logger.info(
                    f"[TIERED_BOT] {self.player_name}: "
                    f"multiway_adjusted ({active_count} players)="
                    f"{base_strategy.action_probabilities}"
                )

        # 5. Personality distortion (skipped for BaselineSolverBot)
        emotional_state = get_emotional_shift(self.psychology)
        anchors = self.psychology.anchors if self.psychology else None

        if anchors and not self.skip_personality_distortion:
            modified_strategy = modify_strategy(
                base=base_strategy,
                legal_actions=valid_actions,
                anchors=anchors,
                emotional_state=emotional_state,
                deviation_profile=self.deviation_profile,
            )
        else:
            modified_strategy = base_strategy

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop modified={modified_strategy.action_probabilities}"
            )

        # 6. River bluff guardrail
        if node.street == 'river':
            simplified_class = simplify_hand_class(
                node.made_tier, node.draw_modifier
            )
            modified_strategy = apply_river_bluff_guardrail(
                modified_strategy, simplified_class, self.archetype_name
            )
            if self.debug_logging:
                logger.info(
                    f"[TIERED_BOT] {self.player_name}: "
                    f"river_guardrail class={simplified_class} "
                    f"arch={self.archetype_name} "
                    f"result={modified_strategy.action_probabilities}"
                )

        # 6a. Phase 6: opponent exploitation (between personality and math floor)
        modified_strategy = self._apply_exploitation(
            modified_strategy, game_state, player_idx, valid_actions,
            anchors, emotional_state,
        )

        # 6a.5 Phase 6.5: strong-hand value override.
        # Replaces strategy when hero has a strong made hand vs a detected
        # hyper-aggressive opponent. Sits after exploitation so it takes
        # precedence on the few decisions where it fires.
        modified_strategy = self._apply_value_override(
            modified_strategy, game_state, player_idx, valid_actions,
            anchors, emotional_state,
            hand_strength=self._classify_postflop_hand_strength(node),
        )

        # 6b. Math floor — override when arithmetic mandates a call/jam.
        # Runs AFTER personality + river guardrail so it has final say.
        modified_strategy = self._apply_math_floor(
            modified_strategy, game_state, player_idx, valid_actions
        )

        # 7. Sample action
        abstract_action = modified_strategy.sample_action(self.rng)

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop sampled={abstract_action} "
                f"emotional={emotional_state.state}"
            )

        # 8. Resolve sizing
        game_action, raise_to = resolve_postflop_sizing(
            abstract_action, game_state, player_idx
        )

        # 9. Validate action is legal
        if game_action not in valid_actions:
            game_action, raise_to = self._validate_action(
                game_action, raise_to, valid_actions
            )

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop final={game_action} raise_to={raise_to}"
            )

        decision = {
            'action': game_action,
            'raise_to': raise_to,
            'dramatic_sequence': [],
            'hand_strategy': (
                f"Tiered bot: {node.street} {node.position} "
                f"{node.board_texture} {node.made_tier}/{node.draw_modifier} "
                f"-> {abstract_action}"
            ),
            'inner_monologue': '',
            'bluff_likelihood': 0,
        }
        self._attach_expression(decision, game_state, player_idx, phase=node.street)
        return decision

    def _apply_exploitation(
        self, strategy, game_state, player_idx, valid_actions,
        anchors, emotional_state,
    ):
        """Phase 6 opponent exploitation step.

        Inserts between personality distortion and math floor. No-ops when:
        - opponent_model_manager is not attached (sim or test without manager)
        - anchors is None (BaselineSolverBot)
        - aggregated stats produce no offsets (cold start, low adaptation_bias,
          heavy tilt, or no opponent matches an exploitation rule)
        """
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None or anchors is None:
            return strategy

        decision_context = self._build_decision_context(game_state, player_idx)
        tilt_factor = self._zone_to_tilt_factor(emotional_state)

        hero_name = self.player_name
        active_opponents = [
            p.name for p in game_state.players
            if not getattr(p, 'is_folded', False) and p.name != hero_name
        ]
        money_committed = self._get_money_committed(game_state)

        # Prefer per-aggressor stats when facing a bet. Aggregated stats
        # across 5 mixed opponents wash out individual signals — a maniac
        # in a 5-rule mix produces avg AF ~2 (below the trigger). Looking
        # at the specific aggressor's stats correctly identifies them.
        stats = self._select_exploitation_stats(
            game_state, manager, hero_name, active_opponents, money_committed,
        )

        exploitation_strength = getattr(self, 'exploitation_strength', 1.0)
        offsets = compute_exploitation_offsets(
            stats=stats,
            adaptation_bias=anchors.adaptation_bias,
            decision_context=decision_context,
            available_actions=list(strategy.action_probabilities.keys()),
            tilt_factor=tilt_factor,
            exploitation_strength=exploitation_strength,
        )

        # Diagnostic counters: track detection vs firing per rule. Useful
        # for sim runs to see if exploitation is actually engaging.
        self._tally_exploitation_event(stats, offsets, decision_context)

        if not offsets:
            return strategy

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"exploitation offsets={offsets}"
            )

        return apply_exploitation_offsets(
            strategy=strategy,
            offsets=offsets,
            legal_actions=valid_actions,
            max_total_shift=self._pick_max_total_shift(stats, decision_context),
        )

    def _pick_max_total_shift(self, stats, decision_context):
        """Choose the L1 clamp based on context.

        The 0.4 default preserves table-baseline-as-dominant signal — a
        reasonable invariant for typical decisions. But when we're facing
        aggression from a detected hyper-aggressive opponent (wide shove
        range), the table baseline is *known wrong* — its preflop chart
        assumes a neutral opener, not a maniac shoving junk. In that
        narrow case we accept more deviation, raising to 0.6 so the
        exploitation offsets can actually flip marginal calls.

        Returns 0.4 in all other contexts.
        """
        is_extreme_spot = (
            (decision_context.facing_all_in or decision_context.facing_big_bet)
            and 'hyper_aggressive' in classify_detected_patterns(stats)
        )
        return 0.6 if is_extreme_spot else 0.4

    def _apply_value_override(
        self, strategy, game_state, player_idx, valid_actions,
        anchors, emotional_state, hand_strength,
    ):
        """Phase 6.5: strong-hand value override.

        Replaces the strategy distribution (not nudges it) when hero has
        a top-tier hand against a detected hyper-aggressive opponent.
        Bypasses offset-based shaping which can't shift probability mass
        far enough for these high-conviction spots.

        Same gating as exploitation: no-ops when manager not attached,
        anchors None, opponent not aggressive, hand not strong enough,
        or psychology gates suppress.
        """
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None or anchors is None:
            return strategy

        decision_context = self._build_decision_context(game_state, player_idx)
        tilt_factor = self._zone_to_tilt_factor(emotional_state)

        hero_name = self.player_name
        active_opponents = [
            p.name for p in game_state.players
            if not getattr(p, 'is_folded', False) and p.name != hero_name
        ]
        money_committed = self._get_money_committed(game_state)
        stats = self._select_exploitation_stats(
            game_state, manager, hero_name, active_opponents, money_committed,
        )

        should_fire = should_apply_value_override(
            stats=stats,
            hand_strength=hand_strength,
            decision_context=decision_context,
            adaptation_bias=anchors.adaptation_bias,
            tilt_factor=tilt_factor,
        )

        self._tally_value_override_event(stats, hand_strength, should_fire)

        if not should_fire:
            return strategy

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"value_override fired hand={hand_strength}"
            )

        return compute_value_override_strategy(
            strategy=strategy,
            decision_context=decision_context,
            hand_strength=hand_strength,
        )

    def _classify_preflop_hand_strength(self, canonical_hand, anchors):
        """'strong' if hand in archetype-adjusted top-N% range, else 'not_strong'.

        Threshold scales with anchors.baseline_looseness so a nit's value
        range is narrower than a maniac's. Capped at top 25% even for
        loose archetypes — dominated hands shouldn't override.
        """
        if not canonical_hand:
            return HandStrengthClass.NOT_STRONG.value
        looseness = getattr(anchors, 'baseline_looseness', 0.4) if anchors else 0.4
        if looseness < 0.30:
            threshold = 0.10   # Nit / Rock
        elif looseness < 0.50:
            threshold = 0.15   # TAG / Calling Station
        elif looseness < 0.70:
            threshold = 0.20   # LAG-ish
        else:
            threshold = 0.25   # Maniac (capped — codex feedback)
        if is_hand_in_range(canonical_hand, threshold):
            return HandStrengthClass.STRONG.value
        return HandStrengthClass.NOT_STRONG.value

    def _classify_postflop_hand_strength(self, node):
        """Map PostflopNode → simplified hand class string ('nuts',
        'strong_made', 'medium_made', etc.). Reuses the same classifier
        used by the river bluff guardrail.
        """
        return simplify_hand_class(node.made_tier, node.draw_modifier)

    def _tally_value_override_event(self, stats, hand_strength, fired):
        """Diagnostic counters for value override (parallel to exploitation tally).

        Tracked keys (under manager._exploitation_counters for unified output):
          value_override_eligible_strong   — strong hand observed
          value_override_eligible_aggro    — aggressor detected
          value_override_fired             — override actually replaced strategy
        """
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return
        if not hasattr(manager, '_exploitation_counters'):
            from collections import Counter
            manager._exploitation_counters = Counter()
        c = manager._exploitation_counters
        is_strong = hand_strength in {
            HandStrengthClass.NUTS.value,
            HandStrengthClass.STRONG_MADE.value,
            HandStrengthClass.STRONG.value,
        }
        if is_strong:
            c['value_override_eligible_strong'] += 1
        if 'hyper_aggressive' in classify_detected_patterns(stats):
            c['value_override_eligible_aggro'] += 1
        if fired:
            c['value_override_fired'] += 1

    def _tally_exploitation_event(self, stats, offsets, decision_context):
        """Increment diagnostic counters for this decision.

        Counters live on opponent_model_manager (persists across hands)
        rather than the controller (rebuilt per hand in sims).

        Tracked keys:
          decisions             — total decisions that reached this step
          cold_start            — gated off (hands_observed below min)
          detected_<pattern>    — pattern was detected (regardless of firing)
          fired                 — offsets came back non-empty
          detected_but_no_fire  — patterns detected but rule didn't fire
                                  (e.g. tight_nit detected outside open spot,
                                   or gated by adaptation_bias × tilt floor)
          no_pattern_matched    — past cold-start, no pattern matched stats
        """
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return
        if not hasattr(manager, '_exploitation_counters'):
            from collections import Counter
            manager._exploitation_counters = Counter()
        c = manager._exploitation_counters
        c['decisions'] += 1

        # Cold-start gating is internal to compute_exploitation_offsets;
        # we mirror its checks here for diagnostic visibility.
        if stats.hands_observed < 15:
            c['cold_start'] += 1
            return

        patterns_this_decision = classify_detected_patterns(stats)
        for pattern in patterns_this_decision:
            c[f'detected_{pattern}'] += 1

        if offsets:
            c['fired'] += 1
        elif patterns_this_decision:
            # Detected but didn't fire — likely tight_nit-only in a non-open spot,
            # or gated by the (bias × tilt) floor.
            c['detected_but_no_fire'] += 1
        else:
            c['no_pattern_matched'] += 1

    def _select_exploitation_stats(
        self, game_state, manager, hero_name,
        active_opponents, money_committed,
    ):
        """Choose stats source: per-aggressor when facing a bet, aggregated otherwise.

        Aggregated stats wash out individual opponent signals in mixed
        fields. When one opponent is driving the action (their current
        bet > everyone else's), we exploit *their* stats directly. When
        no single aggressor exists (open spots, limped pots), fall back
        to aggregated stats with the multiway 60% rule.
        """
        call_amount = getattr(game_state, 'call_amount', 0) or 0
        if call_amount > 0:
            aggressor = self._identify_recent_aggressor(game_state)
            if aggressor:
                model = manager.get_model(hero_name, aggressor)
                t = model.tendencies
                if t.hands_observed > 0:
                    return AggregatedOpponentStats(
                        hands_observed=t.hands_observed,
                        vpip=t.vpip,
                        pfr=t.pfr,
                        aggression_factor=t.aggression_factor,
                        all_in_frequency=t.all_in_frequency,
                    )
        return manager.aggregate_active_opponents(
            observer=hero_name,
            active_opponents=active_opponents,
            money_committed=money_committed,
        )

    def _identify_recent_aggressor(self, game_state):
        """Return the single non-hero opponent with the strictly highest
        current-street bet, or None if no clear aggressor.

        "Strictly highest" matters: in a limped pot everyone has the
        same bet (one BB) and there's no aggressor. When one player has
        raised and others have just called, the raiser is the aggressor.
        """
        hero_name = self.player_name
        candidates = []
        max_bet = 0
        for p in game_state.players:
            if p.name == hero_name or getattr(p, 'is_folded', False):
                continue
            opp_bet = getattr(p, 'bet', 0) or 0
            if opp_bet > max_bet:
                max_bet = opp_bet
                candidates = [p.name]
            elif opp_bet == max_bet and opp_bet > 0:
                candidates.append(p.name)
        if max_bet == 0 or len(candidates) != 1:
            return None
        return candidates[0]

    def _build_decision_context(self, game_state, player_idx):
        """Build DecisionContext from game state.

        - is_preflop: phase.name == 'PRE_FLOP'
        - facing_all_in: there's a call_amount > 0 AND some active non-hero
          opponent has stack 0 (they're all-in)
        - facing_big_bet: call_amount > 10 BB AND call_amount > pot/2,
          AND NOT facing_all_in
        """
        phase = self.state_machine.current_phase
        is_preflop = phase is not None and phase.name == 'PRE_FLOP'

        big_blind = getattr(game_state, 'big_blind', 100) or 100
        call_amount = getattr(game_state, 'call_amount', 0) or 0

        pot = getattr(game_state, 'pot', None)
        if isinstance(pot, dict):
            pot_total = pot.get('total', 0)
        else:
            pot_total = pot or 0

        facing_all_in = False
        hero_name = self.player_name
        if call_amount > 0:
            for p in game_state.players:
                if p.name == hero_name:
                    continue
                if getattr(p, 'is_folded', False):
                    continue
                if getattr(p, 'stack', 1) <= 0:
                    facing_all_in = True
                    break

        facing_big_bet = (
            not facing_all_in
            and call_amount > 10 * big_blind
            and call_amount > pot_total / 2
        )

        return DecisionContext(
            is_preflop=is_preflop,
            facing_all_in=facing_all_in,
            facing_big_bet=facing_big_bet,
        )

    def _zone_to_tilt_factor(self, emotional_state) -> float:
        """Map emotional_state.state -> 3-phase tilt_factor.

        composed -> 1.0 (full exploitation)
        overconfident/tilted -> 0.5 (slight tilt, half-strength)
        shaken/dissociated -> 0.0 (heavy tilt, no exploitation)
        """
        if emotional_state is None:
            return 1.0
        state = getattr(emotional_state, 'state', 'composed')
        if state in ('shaken', 'dissociated'):
            return 0.0
        if state in ('tilted', 'overconfident'):
            return 0.5
        return 1.0

    def _get_money_committed(self, game_state):
        """Per-opponent chips committed this hand.

        Tries player.total_bet first (preferred if available), then falls
        back to player.bet (current street only). Returns empty dict if
        neither attribute is available.
        """
        money = {}
        hero_name = self.player_name
        for p in game_state.players:
            if p.name == hero_name:
                continue
            total = getattr(p, 'total_bet', None)
            if total is None:
                total = getattr(p, 'bet', 0) or 0
            money[p.name] = float(total)
        return money

    def _apply_math_floor(
        self, strategy, game_state, player_idx: int, valid_actions: List[str],
    ):
        """Run apply_pot_odds_floor with the right context pulled from game state.

        Returns the (possibly overridden) strategy. Any unexpected error here
        returns the strategy unchanged — the floor is a safety net, not a
        critical path.
        """
        try:
            player = game_state.players[player_idx]
            big_blind = getattr(game_state, 'current_ante', 0) or 0
            pot_total = (
                game_state.pot.get('total', 0)
                if isinstance(getattr(game_state, 'pot', None), dict) else 0
            )
            cost_to_call = getattr(game_state, 'call_amount', 0) or 0
            override, rule = apply_pot_odds_floor(
                strategy=strategy,
                cost_to_call=cost_to_call,
                pot_total=pot_total,
                player_stack=getattr(player, 'stack', 0) or 0,
                player_bet=getattr(player, 'bet', 0) or 0,
                big_blind=big_blind,
                legal_actions=valid_actions,
            )
            if rule is not None and self.debug_logging:
                logger.info(
                    f"[TIERED_BOT] {self.player_name}: "
                    f"math_floor={rule} -> {override.action_probabilities}"
                )
            return override
        except Exception as e:
            logger.warning(
                f"[TIERED_BOT] {self.player_name}: "
                f"math_floor failed safely: {e}"
            )
            return strategy

    def _attach_expression(
        self, decision: Dict, game_state, player_idx: int, phase: str,
    ) -> None:
        """Populate narration fields on a committed decision dict.

        No-op when no expression_generator is configured. All failures are
        contained: the decision dict is unchanged and the game proceeds.
        """
        if getattr(self, 'expression_generator', None) is None:
            return

        try:
            from .moment_analyzer import MomentAnalyzer
            from .card_utils import card_to_string

            player = game_state.players[player_idx]
            personality_config = getattr(
                getattr(self, 'ai_player', None), 'personality_config', {}
            ) or {}

            hand_cards = (
                [card_to_string(c) for c in player.hand] if player.hand else []
            )
            community_cards = (
                [card_to_string(c) for c in game_state.community_cards]
                if game_state.community_cards else []
            )

            try:
                moment = MomentAnalyzer.analyze(
                    game_state=game_state,
                    player=player,
                    cost_to_call=getattr(game_state, 'call_amount', 0) or 0,
                    big_blind=getattr(game_state, 'current_ante', 0) or 0,
                )
                drama_level = moment.level
                drama_tone = moment.tone
            except Exception:
                drama_level, drama_tone = 'routine', 'neutral'

            emotional = get_emotional_shift(self.psychology)
            active_count = sum(1 for p in game_state.players if not p.is_folded)

            context = ExpressionContext(
                action_taken=decision['action'],
                raise_to=decision.get('raise_to', 0) or 0,
                hand_cards=hand_cards,
                community_cards=community_cards,
                phase=phase,
                pot_size=getattr(game_state, 'pot_total', 0) or 0,
                opponent_count=max(0, active_count - 1),
                personality_name=personality_config.get(
                    'name', self.player_name
                ),
                play_style=personality_config.get('play_style', ''),
                default_attitude=personality_config.get(
                    'default_attitude', 'neutral'
                ),
                verbal_tics=personality_config.get('verbal_tics', []) or [],
                physical_tics=personality_config.get('physical_tics', []) or [],
                drama_level=drama_level,
                drama_tone=drama_tone,
                emotional_state=emotional.state,
                emotional_severity=emotional.severity,
            )

            capture_id_holder = [None]
            narration = self.expression_generator.generate(
                context,
                call_type=getattr(self, '_expression_call_type', None),
                game_id=getattr(self, 'game_id', None),
                capture_id_holder=capture_id_holder,
            )
            for key in ('dramatic_sequence', 'inner_monologue', 'bluff_likelihood'):
                if key in narration:
                    decision[key] = narration[key]
            # Only overwrite hand_strategy if LLM produced one (preserves Layer 1+2 debug string otherwise)
            if narration.get('hand_strategy'):
                decision['hand_strategy'] = narration['hand_strategy']
        except Exception as e:
            logger.warning(
                f"[TIERED_BOT] {self.player_name}: "
                f"expression failed safely: {e}"
            )
            return

        # Link the decision-analysis row to the narration capture so the
        # analyzer pipeline can join them (matches the hybrid path's behavior).
        if capture_id_holder[0] is not None and getattr(
            self, '_decision_analysis_repo', None
        ) is not None:
            try:
                cost_to_call = getattr(game_state, 'call_amount', 0) or 0
                player_obj = game_state.players[player_idx]
                self._analyze_decision(
                    decision,
                    {'call_amount': cost_to_call},
                    capture_id=capture_id_holder[0],
                    player_bet=getattr(player_obj, 'bet', 0),
                    all_players_bets=[
                        (p.bet, p.is_folded) for p in game_state.players
                    ],
                )
            except Exception as e:
                logger.warning(
                    f"[TIERED_BOT] {self.player_name}: "
                    f"capture_id linkage failed: {e}"
                )

    def _postflop_fallback(self, valid_actions: List[str]) -> Dict:
        """Emergency fallback: check if possible, otherwise fold."""
        if 'check' in valid_actions:
            action = 'check'
        elif 'fold' in valid_actions:
            action = 'fold'
        elif 'call' in valid_actions:
            action = 'call'
        else:
            action = valid_actions[0] if valid_actions else 'fold'

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop_fallback={action}"
            )

        return {
            'action': action,
            'raise_to': 0,
            'dramatic_sequence': [],
            'hand_strategy': 'Postflop emergency fallback',
            'inner_monologue': '',
            'bluff_likelihood': 0,
        }

    def _validate_action(
        self, action: str, raise_to: int, valid_actions: List[str]
    ) -> tuple:
        """Ensure the resolved action is legal, with fallback priority."""
        fallback_order = {
            'raise': ['call', 'check', 'fold'],
            'all_in': ['raise', 'call', 'check', 'fold'],
            'call': ['check', 'fold'],
            'check': ['fold'],
            'fold': ['check'],
        }

        fallbacks = fallback_order.get(action, ['fold'])
        for fb in fallbacks:
            if fb in valid_actions:
                logger.debug(
                    f"[TIERED_BOT] {self.player_name}: "
                    f"Falling back from {action} to {fb}"
                )
                return (fb, 0)

        # Ultimate fallback
        if valid_actions:
            return (valid_actions[0], 0)
        return ('fold', 0)


class BaselineSolverBot(TieredBotController):
    """Layer-1-only reference bot for EV-ordering validation.

    System-only test entity per the tiered bot spec. Uses strategy tables,
    multiway adjustments, and the river bluff guardrail, but skips Layer 2
    (personality distortion) and Layer 3 (LLM expression). Not selectable
    in normal games — used to verify that personality deviations cost EV.
    """

    def __init__(
        self,
        player_name: str,
        strategy_table: StrategyTable,
        state_machine=None,
        llm_config=None,
        debug_logging: bool = False,
        rng_seed=None,
        **kwargs,
    ):
        kwargs.pop('skip_personality_distortion', None)
        super().__init__(
            player_name=player_name,
            strategy_table=strategy_table,
            state_machine=state_machine,
            llm_config=llm_config,
            debug_logging=debug_logging,
            rng_seed=rng_seed,
            skip_personality_distortion=True,
            **kwargs,
        )
