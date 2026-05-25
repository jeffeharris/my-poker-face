"""
RuleBotController - Rule-based bot with full psychology system.

A subclass of AIPlayerController that:
- Makes decisions via rules (not LLM)
- Has full psychology system (tilt, emotions, axes)
- Can eventually use LLM for communication only

Key insight: AIPlayerController._get_ai_decision() is the ONLY LLM-dependent method.
Everything else (psychology, memory, opponent models) works independently.
"""

import logging
from typing import Dict, List, Optional

from .controllers import (
    AIPlayerController,
    calculate_quick_equity,
    _get_canonical_hand,
    card_to_string,
)
from .rule_strategies import (
    RuleConfig,
    BUILT_IN_STRATEGIES,
    _strategy_custom,
    _strategy_always_fold,
)
from .hand_tiers import PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS
from .stack_utils import effective_stack_chips, effective_stack_bb, spr as compute_spr
from .strategy.hand_classification import (
    RANK_VALUES,
    _classify_straight_draw,
    classify_hand_full,
)

logger = logging.getLogger(__name__)


class RuleBotController(AIPlayerController):
    """Rule-based bot with full psychology infrastructure.

    Inherits all psychology, memory, and opponent modeling from AIPlayerController.
    Overrides only the decision-making method to use rules instead of LLM.
    """

    def __init__(
        self,
        player_name: str,
        state_machine=None,
        strategy: str = 'case_based',
        llm_config=None,
        session_memory=None,
        opponent_model_manager=None,
        game_id: str = None,
        owner_id: str = None,
        capture_label_repo=None,
        decision_analysis_repo=None,
        prompt_config=None,
        fish_leak: Optional[str] = None,
    ):
        """Initialize RuleBotController.

        Args:
            player_name: Name of the bot
            state_machine: The game's state machine
            strategy: Rule strategy to use (e.g., 'case_based', 'abc', 'always_fold')
            llm_config: LLM config (passed to parent but not used for decisions)
            session_memory: Optional session memory
            opponent_model_manager: Optional opponent model manager
            game_id: Game identifier
            owner_id: Owner/user ID
            capture_label_repo: Optional capture label repository
            decision_analysis_repo: Optional decision analysis repository
            prompt_config: Optional prompt configuration
            fish_leak: Optional designated leak for the fish strategy (e.g.
                'calls_down_top_pair'). Threaded into the rule context so
                `_strategy_fish` can apply the leak's deviation. Ignored by
                non-fish strategies.
        """
        # Call parent constructor to get full psychology infrastructure
        super().__init__(
            player_name=player_name,
            state_machine=state_machine,
            llm_config=llm_config,
            session_memory=session_memory,
            opponent_model_manager=opponent_model_manager,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
            prompt_config=prompt_config,
        )

        # Rule-based strategy configuration
        self.strategy = strategy
        self.rule_config = RuleConfig(strategy=strategy, name=player_name)
        self.fish_leak = fish_leak

        # Track decision history for analysis
        self.decision_history: List[Dict] = []
        self._last_decision_context: Optional[Dict] = None

        # Per-hand starting stacks for fish-leak context fields
        # (`committed_fraction_of_stack`, `is_losing_at_table`). Reset on
        # hand-number transition; lazy-captured on each decision.
        self._this_hand_starts: Dict[str, int] = {}
        self._last_hand_number: int = -1

        logger.info(f"[RULE_BOT] Created RuleBotController for {player_name} with strategy '{strategy}'")

    def _get_ai_decision(self, message: str, **context) -> Dict:
        """Override: Use rules instead of LLM for decision making.

        Args:
            message: The decision prompt (unused for rule-based decisions)
            **context: Decision context including valid_actions, call_amount, etc.

        Returns:
            Decision dict with action, raise_to, dramatic_sequence, hand_strategy
        """
        game_state = self.state_machine.game_state
        player = game_state.current_player

        # Build decision context for rule evaluation
        rule_context = self._build_rule_context(game_state, player, context)

        # Get decision from strategy
        if self.strategy == 'custom':
            decision = _strategy_custom(rule_context, self.rule_config.rules)
        elif self.strategy in BUILT_IN_STRATEGIES:
            decision = BUILT_IN_STRATEGIES[self.strategy](rule_context)
        else:
            logger.warning(f"[RULE_BOT] Unknown strategy: {self.strategy}, defaulting to always_fold")
            decision = _strategy_always_fold(rule_context)

        # Validate action is in valid options
        valid_actions = context.get('valid_actions', [])
        if decision['action'] not in valid_actions:
            decision = self._fallback_action(decision, valid_actions, rule_context)

        # Log decision
        self._log_decision(rule_context, decision)

        # Build response in AIPlayerController format
        response = {
            'action': decision['action'],
            'raise_to': decision.get('raise_to', 0),
            'dramatic_sequence': [],  # Future: LLM commentary
            'hand_strategy': f"{self.strategy} rule applied",
            'inner_monologue': '',
            'bluff_likelihood': 0,
        }

        return response

    def _build_rule_context(self, game_state, player, context: Dict) -> Dict:
        """Build context dictionary for rule evaluation.

        Mirrors RuleBasedController._build_context() but uses parent's context.
        """
        big_blind = game_state.current_ante or 100
        pot_total = game_state.pot.get('total', 0)
        cost_to_call = context.get('call_amount', 0)

        # Calculate equity
        hole_cards = [card_to_string(c) for c in player.hand] if player.hand else []
        community_cards = [card_to_string(c) for c in game_state.community_cards] if game_state.community_cards else []

        # Count opponents
        opponents = [p for p in game_state.players if not p.is_folded and p.name != player.name]
        num_opponents = len(opponents)

        effective_stack = effective_stack_chips(game_state, player)
        effective_stack_bb_val = effective_stack_bb(game_state, player, big_blind=big_blind)
        spr = compute_spr(game_state, player, pot_total=pot_total)

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

        # Get raise bounds from context
        min_raise_to = context.get('min_raise', big_blind * 2)
        max_raise_to = context.get('max_raise', player.stack)

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

        # Hand-start stack tracking for fish-leak context fields. Reset
        # the dict on hand-number transition; lazy-capture on first
        # decision of each hand (or mid-hand fallback when this controller
        # was instantiated after the hand began).
        # PokerStateMachine.stats (and the StateMachineAdapter that proxies
        # to it) exposes stats as a dict — {'hand_count': N} — not the raw
        # StateMachineStats dataclass. Read it as a dict, but tolerate the
        # dataclass form too in case a raw state ever reaches here.
        hand_number = 0
        if self.state_machine:
            _stats = self.state_machine.stats
            hand_number = (
                _stats.get('hand_count', 0) if isinstance(_stats, dict)
                else getattr(_stats, 'hand_count', 0)
            )
        if hand_number != self._last_hand_number:
            self._this_hand_starts = {}
            self._last_hand_number = hand_number
        if player.name not in self._this_hand_starts:
            if phase == 'PRE_FLOP':
                self._this_hand_starts[player.name] = player.stack
            else:
                # Mid-hand fallback: approximate by adding the current
                # round's bet back (we miss prior-round chips, but this
                # only fires when the controller was created post-pre-flop)
                self._this_hand_starts[player.name] = player.stack + player.bet

        hand_start_stack = self._this_hand_starts.get(player.name, player.stack)
        if hand_start_stack > 0:
            committed_fraction = max(
                0.0, (hand_start_stack - player.stack) / hand_start_stack,
            )
        else:
            committed_fraction = 0.0
        is_losing_at_table = player.stack < hand_start_stack

        # Hole-card derivations for fish leaks. All cheap; safe to compute
        # on every decision. Card strings are 2 chars (e.g. 'Ah', 'Td')
        # per `card_to_string`.
        has_face_card = any(c[0] in 'JQKA' for c in hole_cards)

        # Postflop-only: made hand strength + draw structure. Skip the
        # eval call preflop where community_cards is empty (those leaks
        # don't apply preflop anyway).
        has_top_pair_or_better = False
        has_flush_draw = False
        has_oesd = False
        if community_cards:
            try:
                classification = classify_hand_full(hole_cards, community_cards)
                has_top_pair_or_better = classification.made_tier in (
                    'nuts', 'strong_made', 'medium_made',
                )
            except Exception:
                # Classifier shouldn't fail on valid game cards, but if it
                # does (malformed card string, edge case), we don't want
                # to take down a decision call. Defaults are safe-for-fish.
                pass
            # Flush draw: same suit on >= 1 hole card AND that suit appears
            # 4+ times across hole+community. Matches the
            # `check_board_connection` definition (suited hole + 2 board
            # cards of suit). Doesn't match board-only flushes.
            suit_counts: Dict[str, int] = {}
            for c in hole_cards + community_cards:
                if len(c) >= 2:
                    suit_counts[c[1]] = suit_counts.get(c[1], 0) + 1
            hole_suits = {c[1] for c in hole_cards if len(c) >= 2}
            has_flush_draw = any(
                cnt >= 4 and s in hole_suits for s, cnt in suit_counts.items()
            )
            # OESD: use the existing helper. Convert cards to int ranks.
            try:
                all_ranks = sorted(
                    {RANK_VALUES[c[0]] for c in hole_cards + community_cards}
                )
                has_oesd = _classify_straight_draw(all_ranks) == 'oesd'
            except (KeyError, ValueError):
                pass

        # Map PRE_FLOP/FLOP/TURN/RIVER → preflop/flop/turn/river for the
        # fish strategy's street-aware leaks.
        street = phase.lower().replace('_', '')

        # is_pair / is_suited derive from the canonical hand string
        # ('AA', 'AKs', 'AKo'). These were missing from the original
        # context — the fish strategy currently sees them as False, which
        # means it under-calls medium bets. Adding them gives the base
        # behavior the smoke-test baseline already assumes.
        canonical_hand = _get_canonical_hand(hole_cards) if hole_cards else ''
        is_pair = bool(canonical_hand) and len(canonical_hand) == 2 and canonical_hand[0] == canonical_hand[1]
        is_suited = canonical_hand.endswith('s')

        return {
            'player_name': player.name,
            'player_stack': player.stack,
            'stack_bb': player.stack / big_blind if big_blind > 0 else 100,
            'pot_total': pot_total,
            'pot_odds': pot_total / cost_to_call if cost_to_call > 0 else None,
            'cost_to_call': cost_to_call,
            'highest_bet': game_state.highest_bet,
            'min_raise': min_raise_to,
            'max_raise': max_raise_to,
            'big_blind': big_blind,
            'equity': equity,
            'canonical_hand': canonical_hand,
            'hole_cards': hole_cards,
            'community_cards': community_cards,
            'phase': phase,
            'street': street,
            'position': position,
            'num_opponents': num_opponents,
            'effective_stack': effective_stack,
            'effective_stack_bb': effective_stack_bb_val,
            'spr': spr,
            'valid_actions': context.get('valid_actions', []),
            # Hand-shape derivations
            'is_pair': is_pair,
            'is_suited': is_suited,
            'has_face_card': has_face_card,
            'has_flush_draw': has_flush_draw,
            'has_oesd': has_oesd,
            'has_top_pair_or_better': has_top_pair_or_better,
            # Hand-investment derivations (fish-leak triggers)
            'committed_fraction_of_stack': committed_fraction,
            'is_losing_at_table': is_losing_at_table,
            # Fish leak (None for non-fish strategies)
            'fish_leak': self.fish_leak,
            # Opponent modeling stats (for adaptive strategies)
            'opp_vpip': opp_stats.get('vpip', 0.5),
            'opp_aggression': opp_stats.get('aggression', 1.0),
            'opp_fold_to_cbet': opp_stats.get('fold_to_cbet', 0.5),
            'opp_hands_observed': opp_stats.get('hands_observed', 0),
        }

    def _get_opponent_stats(self, opponents: List, player_name: str) -> Dict:
        """Get aggregated stats for opponents at the table.

        Uses opponent_model_manager if available, otherwise returns defaults.
        """
        if not self.opponent_model_manager or not opponents:
            return {'vpip': 0.5, 'aggression': 1.0, 'fold_to_cbet': 0.5, 'hands_observed': 0}

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
                logger.debug(f"[RULE_BOT] Falling back from {action} to {fallback}")
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
            'strategy': self.strategy,
            # Opponent modeling stats
            'opp_aggression': context.get('opp_aggression'),
            'opp_fold_to_cbet': context.get('opp_fold_to_cbet'),
            'opp_hands_observed': context.get('opp_hands_observed'),
            'spr': context.get('spr'),
            'pot_total': context.get('pot_total'),
        }
        self.decision_history.append(record)
        self._last_decision_context = record

        logger.info(
            f"[RULE_BOT] {self.player_name} ({self.strategy}): "
            f"{decision['action']} (equity={context['equity']:.2f}, "
            f"pot_odds={context.get('pot_odds') or 0:.1f}, phase={context['phase']})"
        )

    def get_last_decision_context(self) -> Optional[Dict]:
        """Get the context of the last decision made by this RuleBot.

        Returns strategy, equity, pot_odds, opponent modeling stats, etc.
        Useful for decision analysis and telemetry.
        """
        return self._last_decision_context
