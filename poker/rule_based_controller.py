"""
Rule-Based Controller for Exploitation Testing

A deterministic, config-driven bot that makes decisions based on simple rules.
No LLM calls - pure rule evaluation. Useful for:
- Testing if extreme strategies (always-raise, always-fold) are exploitable
- Baseline comparison for AI player decisions
- Stress-testing game logic with predictable behavior

NOTE: The strategy library (RuleConfig, _strategy_*, BUILT_IN_STRATEGIES,
CHAOS_BOTS, _evaluate_condition, _calculate_raise_size, _strategy_custom,
_position_category, _stack_category, _equity_category) lives in
poker/rule_strategies.py. It is re-exported below for backward compatibility
with existing imports.

Example configs:

    # Always fold (except when free to check)
    {"strategy": "always_fold"}

    # Maximum aggression - raise whenever possible
    {"strategy": "always_raise"}

    # Simple ABC poker - raise strong, fold weak
    {"strategy": "abc"}

    # Custom rules with priority
    {
        "strategy": "custom",
        "rules": [
            {"condition": "equity >= 0.80", "action": "raise", "raise_size": "pot"},
            {"condition": "pot_odds >= 3 and equity >= 0.30", "action": "call"},
            {"condition": "cost_to_call == 0", "action": "check"},
            {"condition": "default", "action": "fold"}
        ]
    }
"""

import logging
from typing import Dict, List, Optional

from .poker_state_machine import PokerStateMachine
from .controllers import (
    calculate_quick_equity,
    _get_canonical_hand,
    card_to_string,
)
from .hand_tiers import PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS
from .rule_strategies import (
    RuleConfig,
    BUILT_IN_STRATEGIES,
    CHAOS_BOTS,
    _strategy_always_fold,
    _strategy_always_call,
    _strategy_always_raise,
    _strategy_always_all_in,
    _strategy_abc,
    _strategy_foldy,
    _strategy_position_aware,
    _strategy_pot_odds_robot,
    _strategy_maniac,
    _strategy_bluffbot,
    _strategy_case_based,
    _strategy_custom,
    _position_category,
    _stack_category,
    _equity_category,
    _evaluate_condition,
    _calculate_raise_size,
)

logger = logging.getLogger(__name__)


class RuleBasedController:
    """
    Deterministic rule-based controller for exploitation testing.

    Same interface as AIPlayerController, but decisions are made by
    evaluating rules from config instead of calling an LLM.
    """

    def __init__(
        self,
        player_name: str,
        state_machine: PokerStateMachine = None,
        config: RuleConfig = None,
        game_id: str = None,
    ):
        self.player_name = player_name
        self.state_machine = state_machine
        self.config = config or RuleConfig()
        self.game_id = game_id

        # Track decision history for analysis
        self.decision_history: List[Dict] = []

        # Last decision context for analysis integration
        self._last_decision_context: Optional[Dict] = None

        # Opponent model manager - set externally by experiment runner
        self.opponent_model_manager = None

    def decide_action(self, game_messages=None) -> Dict:
        """
        Make a decision based on rules.

        Returns dict with:
            - action: "fold", "check", "call", "raise", "all_in"
            - raise_to: raise amount (0 if not raising)
        """
        game_state = self.state_machine.game_state
        player = game_state.current_player

        # Build decision context
        context = self._build_context(game_state, player)

        # Get decision from strategy
        if self.config.strategy == 'custom':
            decision = _strategy_custom(context, self.config.rules)
        elif self.config.strategy in BUILT_IN_STRATEGIES:
            decision = BUILT_IN_STRATEGIES[self.config.strategy](context)
        else:
            logger.warning(f"Unknown strategy: {self.config.strategy}, defaulting to always_fold")
            decision = _strategy_always_fold(context)

        # Validate action is in valid options
        valid_actions = context['valid_actions']
        if decision['action'] not in valid_actions:
            decision = self._fallback_action(decision, valid_actions, context)

        # Log decision
        self._log_decision(context, decision)

        return decision

    def _get_opponent_stats(self, opponents: List, player_name: str) -> Dict:
        """Get aggregated stats for opponents at the table.

        Returns weighted averages of opponent tendencies based on hands observed.
        Used by adaptive strategies like case_based to adjust play.
        """
        if not self.opponent_model_manager or not opponents:
            return {}

        # Aggregate stats across all opponents
        total_hands = 0
        weighted_vpip = 0.0
        weighted_aggression = 0.0
        weighted_fold_to_cbet = 0.0

        for opp in opponents:
            model = self.opponent_model_manager.get_model(player_name, opp.name)
            hands = model.tendencies.hands_observed
            if hands > 0:
                total_hands += hands
                weighted_vpip += model.tendencies.vpip * hands
                weighted_aggression += model.tendencies.aggression_factor * hands
                weighted_fold_to_cbet += model.tendencies.fold_to_cbet * hands

        if total_hands == 0:
            return {'vpip': 0.5, 'aggression': 1.0, 'fold_to_cbet': 0.5, 'hands_observed': 0}

        return {
            'vpip': weighted_vpip / total_hands,
            'aggression': weighted_aggression / total_hands,
            'fold_to_cbet': weighted_fold_to_cbet / total_hands,
            'hands_observed': total_hands,
        }

    def _build_context(self, game_state, player) -> Dict:
        """Build context dictionary for rule evaluation."""
        big_blind = game_state.current_ante or 100
        pot_total = game_state.pot.get('total', 0)
        cost_to_call = min(
            game_state.highest_bet - player.bet,
            player.stack
        )

        # Calculate equity
        hole_cards = [card_to_string(c) for c in player.hand] if player.hand else []
        community_cards = [card_to_string(c) for c in game_state.community_cards] if game_state.community_cards else []

        # Count opponents
        opponents = [p for p in game_state.players if not p.is_folded and p.name != player.name]
        num_opponents = len(opponents)

        # Effective stack (min of ours and largest opponent)
        opponent_stacks = [p.stack for p in opponents]
        effective_stack = min(player.stack, max(opponent_stacks)) if opponent_stacks else player.stack
        effective_stack_bb = effective_stack / big_blind if big_blind > 0 else 100

        # Stack-to-pot ratio
        spr = effective_stack / pot_total if pot_total > 0 else float('inf')

        # Get equity (post-flop) or estimate (pre-flop)
        if community_cards:
            equity = calculate_quick_equity(hole_cards, community_cards, num_opponents=num_opponents) or 0.5
        else:
            # Pre-flop equity estimate based on hand ranking
            canonical = _get_canonical_hand(hole_cards) if hole_cards else ''
            if canonical in PREMIUM_HANDS:
                equity = 0.75
            elif canonical in TOP_10_HANDS:
                equity = 0.65
            elif canonical in TOP_20_HANDS:
                equity = 0.55
            elif canonical in TOP_35_HANDS:
                equity = 0.48
            else:
                equity = 0.40

        # Calculate raise bounds
        highest_bet = game_state.highest_bet
        max_opponent_stack = max(
            (p.stack for p in game_state.players
             if not p.is_folded and not p.is_all_in and p.name != player.name),
            default=0
        )
        max_raise_by = min(player.stack, max_opponent_stack)
        max_raise_to = highest_bet + max_raise_by
        min_raise_by = min(game_state.min_raise_amount, max_raise_by) if max_raise_by > 0 else 0
        min_raise_to = highest_bet + min_raise_by

        # Get position
        position = None
        for pos, name in game_state.table_positions.items():
            if name == player.name:
                position = pos
                break

        # Get phase
        phase = self.state_machine.current_phase.name if self.state_machine.current_phase else 'PRE_FLOP'

        # Get opponent stats if available (for adaptive strategies)
        opp_stats = self._get_opponent_stats(opponents, player.name)

        return {
            'player_name': player.name,
            'player_stack': player.stack,
            'stack_bb': player.stack / big_blind if big_blind > 0 else 100,
            'pot_total': pot_total,
            'pot_odds': pot_total / cost_to_call if cost_to_call > 0 else None,
            'cost_to_call': cost_to_call,
            'highest_bet': highest_bet,
            'min_raise': min_raise_to,
            'max_raise': max_raise_to,
            'big_blind': big_blind,
            'equity': equity,
            'canonical_hand': _get_canonical_hand(hole_cards) if hole_cards else '',
            'hole_cards': hole_cards,
            'community_cards': community_cards,
            'phase': phase,
            'position': position,
            'num_opponents': num_opponents,
            'effective_stack': effective_stack,
            'effective_stack_bb': effective_stack_bb,
            'spr': spr,
            'valid_actions': game_state.current_player_options,
            # Opponent modeling stats (for adaptive strategies)
            'opp_vpip': opp_stats.get('vpip', 0.5),
            'opp_aggression': opp_stats.get('aggression', 1.0),
            'opp_fold_to_cbet': opp_stats.get('fold_to_cbet', 0.5),
            'opp_hands_observed': opp_stats.get('hands_observed', 0),
        }

    def _fallback_action(self, decision: Dict, valid_actions: List[str], context: Dict) -> Dict:
        """Find a valid fallback action when desired action isn't available."""
        action = decision['action']

        # Map to fallback priority
        fallback_order = {
            'raise': ['call', 'check', 'fold'],
            'call': ['check', 'fold'],
            'check': ['fold'],
            'all_in': ['raise', 'call', 'check', 'fold'],
        }

        fallbacks = fallback_order.get(action, ['fold'])
        for fallback in fallbacks:
            if fallback in valid_actions:
                logger.debug(f"Falling back from {action} to {fallback}")
                return {'action': fallback, 'raise_to': 0}

        # Ultimate fallback
        if 'fold' in valid_actions:
            return {'action': 'fold', 'raise_to': 0}
        return {'action': valid_actions[0], 'raise_to': 0}

    def _log_decision(self, context: Dict, decision: Dict) -> None:
        """Log decision for later analysis."""
        record = {
            'phase': context['phase'],
            'position': context['position'],
            'equity': context['equity'],
            'pot_odds': context['pot_odds'],
            'cost_to_call': context['cost_to_call'],
            'stack_bb': context['stack_bb'],
            'canonical_hand': context['canonical_hand'],
            'action': decision['action'],
            'raise_to': decision.get('raise_to', 0),
            'strategy': self.config.strategy,
            # Opponent modeling stats (for adaptive strategies)
            'opp_aggression': context.get('opp_aggression'),
            'opp_fold_to_cbet': context.get('opp_fold_to_cbet'),
            'opp_hands_observed': context.get('opp_hands_observed'),
            'spr': context.get('spr'),
            'pot_total': context.get('pot_total'),
        }
        self.decision_history.append(record)

        # Store last decision context for analysis integration
        self._last_decision_context = record

        # Log at INFO level in human games (game_id set), DEBUG otherwise
        log_level = logging.INFO if self.game_id else logging.DEBUG
        logger.log(
            log_level,
            f"[RULE_BOT] {self.player_name} ({self.config.strategy}): "
            f"{decision['action']} (equity={context['equity']:.2f}, "
            f"pot_odds={context.get('pot_odds') or 0:.1f}, phase={context['phase']})"
        )

    # ========================================================================
    # Compatibility stubs for game handler (matches AIPlayerController interface)
    # ========================================================================

    def get_last_decision_context(self) -> Optional[Dict]:
        """Get the context of the last decision made by this RuleBot.

        Returns strategy, equity, pot_odds, opponent modeling stats, etc.
        Useful for decision analysis and telemetry.
        """
        return self._last_decision_context

    def clear_decision_plans(self) -> List[Dict]:
        """Stub: RuleBots don't track decision plans."""
        return []

    def clear_hand_bluff_likelihood(self) -> None:
        """Stub: RuleBots don't track bluff likelihood."""
        pass

    @property
    def psychology(self):
        """Stub: RuleBots don't have psychology (no emotional state)."""
        return None

    @property
    def emotional_state(self):
        """Stub: RuleBots don't have emotional state."""
        return None

    # Attribute stub for game handler compatibility
    current_hand_number = 0

    @property
    def ai_player(self):
        """Stub: RuleBots don't have an ai_player object.

        Returns a minimal object that satisfies ai_player attribute access patterns
        in the game handler (personality_config.get('nickname'), confidence, attitude).
        """
        return _RuleBotAIPlayerStub(self.config.name)

    @property
    def assistant(self):
        """Stub: RuleBots don't have an LLM assistant."""
        return None

    @property
    def session_memory(self):
        """Stub: RuleBots don't use session memory."""
        return None

    @session_memory.setter
    def session_memory(self, value):
        """Stub: Ignore session memory assignment."""
        pass

    @property
    def prompt_config(self):
        """Stub: RuleBots don't have prompt config."""
        return None


class _RuleBotAIPlayerStub:
    """Minimal stub that satisfies ai_player attribute access patterns."""

    def __init__(self, name: str):
        self.personality_config = {
            'nickname': name,
            'name': name,
            'personality_traits': {
                'table_talk': 0.0,  # RuleBots don't chat
                'chattiness': 0.0,
            },
        }
        self.confidence = 'Normal'
        self.attitude = 'Neutral'
        self.assistant = None  # RuleBots don't have LLM assistants
        self.is_rule_based = True  # Flag to skip LLM-based commentary


# ============================================================================
# Factory Functions
# ============================================================================

def create_rule_bot(
    name: str,
    strategy: str = 'always_fold',
    state_machine: PokerStateMachine = None,
    **kwargs
) -> RuleBasedController:
    """Quick factory for creating rule-based bots."""
    config = RuleConfig(
        strategy=strategy,
        name=name,
        **{k: v for k, v in kwargs.items() if k in ('rules', 'raise_size')}
    )
    return RuleBasedController(
        player_name=name,
        state_machine=state_machine,
        config=config,
    )
