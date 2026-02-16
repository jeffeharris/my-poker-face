"""
TieredBotController - Solver baselines + personality distortion, no LLM decisions.

A subclass of AIPlayerController that:
- Makes decisions via solver-derived strategy tables + personality distortion
- Has full psychology system (tilt, emotions, axes)
- Never uses LLM for decisions (LLM is expression layer only, Phase 4)

Phase 1 scope:
- Preflop: full strategy table lookup + personality modifier
- Postflop: check/fold fallback (Phase 2 adds postflop tables)
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
from .strategy.personality_modifier import modify_strategy
from .strategy.deviation_profiles import select_deviation_profile, DeviationProfile
from .strategy.action_mapper import resolve_preflop_sizing

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

    def _get_ai_decision(self, message: str, **context) -> Dict:
        """Override: Use strategy tables + personality distortion instead of LLM.

        Args:
            message: The decision prompt (unused for tiered bot decisions)
            **context: Decision context including valid_actions, call_amount, etc.

        Returns:
            Decision dict with action, raise_to, dramatic_sequence, hand_strategy
        """
        game_state = self.state_machine.game_state
        player = game_state.current_player
        player_idx = game_state.current_player_idx
        valid_actions = context.get('valid_actions', [])
        phase = self.state_machine.current_phase

        # Phase 1: Postflop → check/fold fallback
        is_preflop = phase and phase.name == 'PRE_FLOP'
        if not is_preflop:
            return self._postflop_fallback(valid_actions)

        # Get canonical hand
        hole_cards = [card_to_string(c) for c in player.hand] if player.hand else []
        canonical_hand = _get_canonical_hand(hole_cards) if hole_cards else ''

        if not canonical_hand:
            logger.warning(f"[TIERED_BOT] {self.player_name}: No canonical hand, using fallback")
            return self._postflop_fallback(valid_actions)

        # Build preflop node
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

        # Layer 2: Personality distortion
        emotional_state = get_emotional_shift(self.psychology)
        anchors = self.psychology.anchors if self.psychology else None

        if anchors:
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

        # Sample action from modified distribution
        abstract_action = modified_strategy.sample_action(self.rng)

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"sampled={abstract_action} emotional={emotional_state.state}"
            )

        # Resolve abstract action to concrete game action + sizing
        game_action, raise_to = resolve_preflop_sizing(
            abstract_action, game_state, player_idx
        )

        # Validate action is in valid options
        if game_action not in valid_actions:
            game_action, raise_to = self._validate_action(
                game_action, raise_to, valid_actions
            )

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"final_action={game_action} raise_to={raise_to}"
            )

        return {
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

    def _postflop_fallback(self, valid_actions: List[str]) -> Dict:
        """Phase 1 postflop: check if possible, otherwise fold."""
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
            'hand_strategy': 'Postflop check/fold fallback (Phase 1)',
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
