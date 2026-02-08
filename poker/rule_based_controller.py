"""
Rule-Based Controller for Exploitation Testing

A deterministic, config-driven bot that makes decisions based on simple rules.
No LLM calls - pure rule evaluation. Useful for:
- Testing if extreme strategies (always-raise, always-fold) are exploitable
- Baseline comparison for AI player decisions
- Stress-testing game logic with predictable behavior

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
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import json

from .poker_state_machine import PokerStateMachine
from .controllers import (
    calculate_quick_equity,
    _get_canonical_hand,
    card_to_string,
)
from .hand_tiers import PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuleConfig:
    """Configuration for rule-based decision making."""
    strategy: str = "always_fold"  # Built-in strategy name
    rules: tuple = field(default_factory=tuple)  # Custom rules for "custom" strategy
    raise_size: str = "min"  # Default raise sizing: "min", "pot", "half_pot", "all_in"
    name: str = "RuleBot"  # Display name for the bot

    @classmethod
    def from_dict(cls, d: Dict) -> 'RuleConfig':
        rules = tuple(d.get('rules', []))
        return cls(
            strategy=d.get('strategy', 'always_fold'),
            rules=rules,
            raise_size=d.get('raise_size', 'min'),
            name=d.get('name', 'RuleBot'),
        )

    @classmethod
    def from_json_file(cls, path: str) -> 'RuleConfig':
        with open(path) as f:
            return cls.from_dict(json.load(f))


# ============================================================================
# Built-in Strategies
# ============================================================================

def _strategy_always_fold(context: Dict) -> Dict:
    """Fold everything except free checks."""
    if context['cost_to_call'] == 0:
        return {'action': 'check', 'raise_to': 0}
    return {'action': 'fold', 'raise_to': 0}


def _strategy_always_call(context: Dict) -> Dict:
    """Call any bet, check when free."""
    if context['cost_to_call'] == 0:
        return {'action': 'check', 'raise_to': 0}
    if 'call' in context['valid_actions']:
        return {'action': 'call', 'raise_to': 0}
    # Can't call (maybe all-in situation) - fold as fallback
    return {'action': 'fold', 'raise_to': 0}


def _strategy_always_raise(context: Dict) -> Dict:
    """Raise whenever possible, otherwise call."""
    if 'raise' in context['valid_actions']:
        return {'action': 'raise', 'raise_to': context['max_raise']}
    if 'call' in context['valid_actions']:
        return {'action': 'call', 'raise_to': 0}
    if context['cost_to_call'] == 0:
        return {'action': 'check', 'raise_to': 0}
    return {'action': 'fold', 'raise_to': 0}


def _strategy_always_all_in(context: Dict) -> Dict:
    """Go all-in every hand."""
    if 'all_in' in context['valid_actions']:
        return {'action': 'all_in', 'raise_to': 0}
    if 'raise' in context['valid_actions']:
        return {'action': 'raise', 'raise_to': context['player_stack']}
    if 'call' in context['valid_actions']:
        return {'action': 'call', 'raise_to': 0}
    return {'action': 'check', 'raise_to': 0}


def _strategy_abc(context: Dict) -> Dict:
    """
    Simple ABC poker:
    - Raise with premium hands
    - Call with decent hands
    - Fold weak hands
    """
    canonical = context.get('canonical_hand', '')
    equity = context.get('equity', 0.5)
    cost_to_call = context['cost_to_call']

    # Free check always
    if cost_to_call == 0:
        # Bet with good hands
        if equity >= 0.65 and 'raise' in context['valid_actions']:
            return {'action': 'raise', 'raise_to': context['min_raise']}
        return {'action': 'check', 'raise_to': 0}

    # Premium hands - raise
    if canonical in PREMIUM_HANDS or equity >= 0.75:
        if 'raise' in context['valid_actions']:
            return {'action': 'raise', 'raise_to': context['min_raise']}
        return {'action': 'call', 'raise_to': 0}

    # Good hands - call with odds
    pot_odds = context.get('pot_odds', 1)
    required_equity = 1 / (pot_odds + 1)

    if canonical in TOP_20_HANDS or equity >= required_equity:
        if 'call' in context['valid_actions']:
            return {'action': 'call', 'raise_to': 0}

    # Default fold
    return {'action': 'fold', 'raise_to': 0}


def _strategy_position_aware(context: Dict) -> Dict:
    """
    Position-based strategy:
    - Late position (button, cutoff): wider range, more aggressive
    - Early position: tight, premium hands only
    """
    position = context.get('position', 'button')
    canonical = context.get('canonical_hand', '')
    equity = context.get('equity', 0.5)
    cost_to_call = context['cost_to_call']

    # Determine position type
    late_positions = {'button', 'cutoff', 'btn', 'co'}
    is_late_position = position.lower() in late_positions

    # Free check
    if cost_to_call == 0:
        if equity >= 0.55 and 'raise' in context['valid_actions']:
            return {'action': 'raise', 'raise_to': context['min_raise']}
        return {'action': 'check', 'raise_to': 0}

    # Late position - play wider
    if is_late_position:
        if canonical in TOP_35_HANDS or equity >= 0.50:
            if 'raise' in context['valid_actions'] and equity >= 0.60:
                return {'action': 'raise', 'raise_to': context['min_raise']}
            if 'call' in context['valid_actions']:
                return {'action': 'call', 'raise_to': 0}

    # Early position - play tight
    else:
        if canonical in TOP_10_HANDS or equity >= 0.70:
            if 'raise' in context['valid_actions']:
                return {'action': 'raise', 'raise_to': context['min_raise']}
            if 'call' in context['valid_actions']:
                return {'action': 'call', 'raise_to': 0}

    return {'action': 'fold', 'raise_to': 0}


def _strategy_pot_odds_robot(context: Dict) -> Dict:
    """
    Pure GTO-ish: only call/raise when pot odds justify it.
    No personality, no bluffing - just math.
    """
    equity = context.get('equity', 0.5)
    cost_to_call = context['cost_to_call']
    pot = context['pot_total']

    if cost_to_call == 0:
        # Bet for value with strong hands
        if equity >= 0.65 and 'raise' in context['valid_actions']:
            # Bet 2/3 pot
            bet_size = int(pot * 0.67)
            bet_size = max(context['min_raise'], min(bet_size, context['max_raise']))
            return {'action': 'raise', 'raise_to': bet_size}
        return {'action': 'check', 'raise_to': 0}

    # Calculate required equity
    pot_odds = pot / cost_to_call if cost_to_call > 0 else float('inf')
    required_equity = 1 / (pot_odds + 1)

    # Pure EV calculation
    if equity >= required_equity:
        # +EV to call - but should we raise?
        if equity >= 0.70 and 'raise' in context['valid_actions']:
            # Value raise
            raise_size = int(pot * 0.75)
            raise_size = max(context['min_raise'], min(raise_size, context['max_raise']))
            return {'action': 'raise', 'raise_to': raise_size}
        if 'call' in context['valid_actions']:
            return {'action': 'call', 'raise_to': 0}

    return {'action': 'fold', 'raise_to': 0}


def _strategy_maniac(context: Dict) -> Dict:
    """
    Hyper-aggressive: raises most hands, barrels all streets.

    Tests if AI can call down light against constant aggression.
    - Raise 80% of hands preflop
    - Triple barrel (bet flop, turn, river) with 75% pot sizing
    - Only slow down with absolute air (< 20% equity)
    """
    equity = context.get('equity', 0.5)
    pot = context['pot_total']
    cost_to_call = context['cost_to_call']

    # Always try to raise/bet
    if 'raise' in context['valid_actions']:
        # 75% pot sizing
        bet_size = int(pot * 0.75)
        bet_size = max(context['min_raise'], min(bet_size, context['max_raise']))

        # Only check with total air when it's free
        if cost_to_call == 0 and equity < 0.20:
            return {'action': 'check', 'raise_to': 0}

        return {'action': 'raise', 'raise_to': bet_size}

    # Can't raise - call if we have anything
    if 'call' in context['valid_actions'] and equity >= 0.25:
        return {'action': 'call', 'raise_to': 0}

    if cost_to_call == 0:
        return {'action': 'check', 'raise_to': 0}

    return {'action': 'fold', 'raise_to': 0}


def _strategy_bluffbot(context: Dict) -> Dict:
    """
    Bluffs missed draws, especially on river.

    Tests if AI can detect bluffs and make hero calls.
    - On river with low equity but checked to us, bluff pot-sized
    - Value bet strong hands normally
    - Uses pot odds for calling decisions
    """
    equity = context.get('equity', 0.5)
    pot = context['pot_total']
    cost_to_call = context['cost_to_call']
    phase = context.get('phase', 'PRE_FLOP')

    if cost_to_call == 0:  # Can bet
        # River bluff with weak hands (representing missed draws)
        if phase == 'RIVER' and equity < 0.35 and 'raise' in context['valid_actions']:
            # Big bluff - pot-sized bet
            bluff_size = int(pot * 1.0)
            bluff_size = max(context['min_raise'], min(bluff_size, context['max_raise']))
            return {'action': 'raise', 'raise_to': bluff_size}

        # Value bet strong hands
        if equity >= 0.60 and 'raise' in context['valid_actions']:
            bet_size = int(pot * 0.66)
            bet_size = max(context['min_raise'], min(bet_size, context['max_raise']))
            return {'action': 'raise', 'raise_to': bet_size}

        return {'action': 'check', 'raise_to': 0}

    # Facing bet - only continue with decent equity (pot odds)
    pot_odds = pot / cost_to_call if cost_to_call > 0 else float('inf')
    required_equity = 1 / (pot_odds + 1)

    if equity >= required_equity and 'call' in context['valid_actions']:
        return {'action': 'call', 'raise_to': 0}

    return {'action': 'fold', 'raise_to': 0}


def _position_category(position: str) -> str:
    """Categorize position for case matching."""
    pos = position.lower() if position else ''
    if pos in ['button', 'cutoff']:
        return 'late'
    elif pos in ['under_the_gun', 'middle_position_1']:
        return 'early'
    elif pos in ['small_blind_player', 'big_blind_player']:
        return 'blind'
    return 'middle'


def _stack_category(stack_bb: float) -> str:
    """Categorize stack depth."""
    if stack_bb <= 15:
        return 'short'
    elif stack_bb <= 40:
        return 'mid'
    return 'deep'


def _equity_category(equity: float) -> str:
    """Categorize hand strength."""
    if equity >= 0.75:
        return 'premium'
    elif equity >= 0.60:
        return 'strong'
    elif equity >= 0.45:
        return 'medium'
    elif equity >= 0.25:
        return 'weak'
    return 'air'


def _strategy_case_based(context: Dict) -> Dict:
    """
    Case-based strategy using pattern matching on game state.
    Balances value betting, bluffing, and pot odds by situation.

    Adaptive features (v2):
    - Bluffs more vs high-fold opponents (fold_to_cbet > 60%)
    - Bluffs less vs calling stations (fold_to_cbet < 30%)
    - Calls lighter vs aggressive opponents (aggression > 2.0)
    - Calls tighter vs passive opponents (aggression < 0.5)
    """
    equity = context['equity']
    cost = context['cost_to_call']
    pot = context['pot_total']
    phase = context['phase']
    position = context.get('position', '')
    stack_bb = context.get('stack_bb', 100)
    spr = context.get('spr', 10)
    valid = context['valid_actions']

    # Opponent modeling stats
    opp_fold_rate = context.get('opp_fold_to_cbet', 0.5)
    opp_aggression = context.get('opp_aggression', 1.0)
    opp_hands = context.get('opp_hands_observed', 0)

    # Calculate adjustments based on opponent tendencies
    # Only adapt if we have enough observations (5+ hands)
    bluff_adjust = 1.0  # Multiplier for bluff frequency
    call_adjust = 0.0   # Additive adjustment to equity threshold

    if opp_hands >= 5:
        # Adjust bluff threshold based on opponent fold rate
        # If they fold > 60%, bluffs are more profitable
        if opp_fold_rate > 0.6:
            bluff_adjust = 1.5  # Bluff more
        elif opp_fold_rate < 0.3:
            bluff_adjust = 0.5  # Bluff less (calling station)

        # Adjust calling threshold based on aggression
        # High aggression = they bluff more = call lighter
        if opp_aggression > 2.0:
            call_adjust = -0.08  # Need 8% less equity to call
        elif opp_aggression < 0.5:
            call_adjust = 0.05  # Need more equity (they're not bluffing)

    # Categorize inputs
    pos = _position_category(position)
    stack = _stack_category(stack_bb)
    hand = _equity_category(equity)
    facing = 'bet' if cost > 0 else 'check'

    # Helpers
    def bet(fraction):
        size = int(pot * fraction)
        size = max(context['min_raise'], min(size, context['max_raise']))
        if 'raise' in valid:
            return {'action': 'raise', 'raise_to': size}
        return {'action': 'check', 'raise_to': 0}

    def call():
        if 'call' in valid:
            return {'action': 'call', 'raise_to': 0}
        return {'action': 'check', 'raise_to': 0}

    def check():
        if 'check' in valid:
            return {'action': 'check', 'raise_to': 0}
        return {'action': 'fold', 'raise_to': 0}

    def fold():
        return {'action': 'fold', 'raise_to': 0}

    def shove():
        if 'all_in' in valid:
            return {'action': 'all_in', 'raise_to': 0}
        if 'raise' in valid:
            return {'action': 'raise', 'raise_to': context['max_raise']}
        return call()

    # Pot odds calculation with opponent adjustment
    pot_odds_needed = cost / (pot + cost) if cost > 0 else 0
    adjusted_pot_odds = pot_odds_needed + call_adjust

    # === LOW SPR: Commit or fold ===
    if spr < 3:
        if hand in ['premium', 'strong']:
            return shove()
        if facing == 'bet' and hand == 'medium' and equity >= adjusted_pot_odds:
            return call()
        if facing == 'check':
            return check()
        return fold()

    # === SHORT STACK: Push/fold ===
    if stack == 'short':
        if hand in ['premium', 'strong']:
            return shove()
        if facing == 'bet' and equity >= adjusted_pot_odds:
            return call()
        if facing == 'check':
            return check()
        return fold()

    # === FACING BET ===
    if facing == 'bet':
        # Premium: raise for value
        if hand == 'premium':
            return bet(0.75)

        # Strong: call (raise sometimes in position)
        if hand == 'strong':
            if pos == 'late' and phase == 'FLOP':
                return bet(0.67)  # Raise flop IP
            return call()

        # Medium: call if pot odds are right (adjusted for opponent tendencies)
        if hand == 'medium' and equity >= adjusted_pot_odds:
            return call()

        # Weak with odds: call (adjusted for opponent tendencies)
        if hand == 'weak' and equity >= adjusted_pot_odds * 0.9:
            return call()

        return fold()

    # === CAN BET (checked to us) ===

    # Premium: bet big for value
    if hand == 'premium':
        return bet(0.75)

    # Strong: bet for value, size by position
    if hand == 'strong':
        if pos == 'late':
            return bet(0.67)
        return bet(0.5)

    # Medium: bet in position, check OOP
    if hand == 'medium':
        if pos == 'late':
            return bet(0.5)
        return check()

    # Weak: check (no showdown value but not pure air)
    if hand == 'weak':
        return check()

    # Air: bluff in position on river (adjusted by opponent fold rate)
    if hand == 'air':
        # Bluff more vs folders, less vs calling stations
        should_bluff_river = pos == 'late' and phase == 'RIVER' and bluff_adjust >= 1.0
        should_bluff_earlier = (
            pos == 'late' and
            phase in ['FLOP', 'TURN'] and
            equity > 0.15 and
            bluff_adjust >= 0.75
        )

        if should_bluff_river:
            return bet(0.67)
        if should_bluff_earlier:
            return bet(0.5)
        return check()

    return check()


BUILT_IN_STRATEGIES = {
    'always_fold': _strategy_always_fold,
    'always_call': _strategy_always_call,
    'always_raise': _strategy_always_raise,
    'always_all_in': _strategy_always_all_in,
    'abc': _strategy_abc,
    'position_aware': _strategy_position_aware,
    'pot_odds_robot': _strategy_pot_odds_robot,
    'maniac': _strategy_maniac,
    'bluffbot': _strategy_bluffbot,
    'case_based': _strategy_case_based,
}


# ============================================================================
# Custom Rule Evaluation
# ============================================================================

def _evaluate_condition(condition: str, context: Dict) -> bool:
    """
    Evaluate a condition string against the context.

    Supported variables:
        equity, pot_odds, cost_to_call, pot_total, player_stack,
        stack_bb, position, phase, canonical_hand, is_premium,
        is_top_10, is_top_20, is_suited, is_pair

    Supported operators:
        ==, !=, >=, <=, >, <, and, or, in

    Examples:
        "equity >= 0.65"
        "pot_odds >= 3 and equity >= 0.30"
        "canonical_hand in ['AA', 'KK', 'QQ']"
        "is_premium"
        "default"
    """
    if condition == 'default':
        return True

    # Build evaluation namespace
    canonical = context.get('canonical_hand', '')
    namespace = {
        'equity': context.get('equity', 0.5),
        'pot_odds': context.get('pot_odds', 1.0),
        'cost_to_call': context.get('cost_to_call', 0),
        'pot_total': context.get('pot_total', 0),
        'player_stack': context.get('player_stack', 0),
        'stack_bb': context.get('stack_bb', 100),
        'position': context.get('position', 'button'),
        'phase': context.get('phase', 'PRE_FLOP'),
        'canonical_hand': canonical,
        'is_premium': canonical in PREMIUM_HANDS,
        'is_top_10': canonical in TOP_10_HANDS,
        'is_top_20': canonical in TOP_20_HANDS,
        'is_top_35': canonical in TOP_35_HANDS,
        'is_suited': canonical.endswith('s') if canonical else False,
        'is_pair': len(canonical) == 2 and canonical[0] == canonical[1] if canonical else False,
        'num_opponents': context.get('num_opponents', 1),
        'is_heads_up': context.get('num_opponents', 1) == 1,
    }

    try:
        # Safe eval with restricted namespace
        result = eval(condition, {"__builtins__": {}}, namespace)
        return bool(result)
    except Exception as e:
        logger.warning(f"Rule condition evaluation failed: {condition} - {e}")
        return False


def _calculate_raise_size(size_spec: str, context: Dict) -> int:
    """Calculate raise amount based on size specification."""
    pot = context.get('pot_total', 0)
    min_raise = context.get('min_raise', 100)
    max_raise = context.get('max_raise', 1000)

    if size_spec == 'min':
        return min_raise
    elif size_spec == 'pot':
        return max(min_raise, min(pot, max_raise))
    elif size_spec == 'half_pot':
        return max(min_raise, min(pot // 2, max_raise))
    elif size_spec == 'all_in':
        return max_raise
    elif size_spec.endswith('x'):
        # Multiplier: "3x" means 3x the big blind
        try:
            multiplier = float(size_spec[:-1])
            bb = context.get('big_blind', 100)
            return max(min_raise, min(int(bb * multiplier), max_raise))
        except ValueError:
            return min_raise
    else:
        # Try to parse as integer
        try:
            return max(min_raise, min(int(size_spec), max_raise))
        except ValueError:
            return min_raise


def _strategy_custom(context: Dict, rules: tuple) -> Dict:
    """
    Evaluate custom rules in priority order.
    First matching rule wins.
    """
    for rule in rules:
        condition = rule.get('condition', 'default')
        if _evaluate_condition(condition, context):
            action = rule.get('action', 'fold')

            # Handle raise sizing
            if action == 'raise':
                size_spec = rule.get('raise_size', 'min')
                raise_to = _calculate_raise_size(size_spec, context)
                return {'action': 'raise', 'raise_to': raise_to}

            return {'action': action, 'raise_to': 0}

    # No rules matched - fold as ultimate fallback
    return {'action': 'fold', 'raise_to': 0}


# ============================================================================
# Controller Class
# ============================================================================

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
            'pot_odds': pot_total / cost_to_call if cost_to_call > 0 else float('inf'),
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
        }
        self.decision_history.append(record)

        logger.debug(
            f"[RULE_BOT] {self.player_name} ({self.config.strategy}): "
            f"{decision['action']} (equity={context['equity']:.2f}, "
            f"pot_odds={context['pot_odds']:.1f})"
        )

    # ========================================================================
    # Compatibility stubs for game handler (matches AIPlayerController interface)
    # ========================================================================

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


# Pre-defined bot configurations for common experiments
CHAOS_BOTS = {
    'always_fold': RuleConfig(strategy='always_fold', name='FoldBot'),
    'always_call': RuleConfig(strategy='always_call', name='CallStation'),
    'always_raise': RuleConfig(strategy='always_raise', name='AggBot'),
    'always_all_in': RuleConfig(strategy='always_all_in', name='YOLOBot'),
    'abc': RuleConfig(strategy='abc', name='ABCBot'),
    'position_aware': RuleConfig(strategy='position_aware', name='PositionBot'),
    'pot_odds_robot': RuleConfig(strategy='pot_odds_robot', name='GTO-Lite'),
    'maniac': RuleConfig(strategy='maniac', name='ManiacBot'),
    'bluffbot': RuleConfig(strategy='bluffbot', name='BluffBot'),
    'case_based': RuleConfig(strategy='case_based', name='CaseBot'),
}
