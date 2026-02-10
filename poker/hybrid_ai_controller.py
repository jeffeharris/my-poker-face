"""
HybridAIController - LLM picks from rule-bounded options.

Combines the mathematical rigor of rule-based decisions with the personality
expression of LLM-driven AI players.

Flow:
    Game State → Rule Engine → Bounded Options → LLM Choice + Narrative → Action

Benefits:
- No catastrophic folds (rules block them)
- AI personality shines through option selection + narrative
- Exploitability hidden from humans (varied LLM behavior)
- Graceful degradation (if LLM fails, use rule engine's top pick)
"""

import json
import logging
from typing import Dict, List, Optional

from .controllers import AIPlayerController, _get_canonical_hand, card_to_string
from .bounded_options import (
    BoundedOption,
    generate_bounded_options,
    format_options_for_prompt,
    calculate_required_equity,
)
from .hand_tiers import PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS
from .hand_ranges import (
    calculate_equity_vs_ranges,
    build_opponent_info,
    EquityConfig,
)
from .ai_resilience import parse_json_response

logger = logging.getLogger(__name__)


class HybridAIController(AIPlayerController):
    """AI that picks from rule-bounded options.

    Inherits full psychology, memory, and personality from AIPlayerController.
    Overrides _get_ai_decision to present bounded options to the LLM.
    """

    def __init__(
        self,
        player_name: str,
        state_machine=None,
        llm_config=None,
        session_memory=None,
        opponent_model_manager=None,
        game_id: str = None,
        owner_id: str = None,
        capture_label_repo=None,
        decision_analysis_repo=None,
        prompt_config=None,
    ):
        """Initialize HybridAIController.

        Args:
            player_name: Name of the AI player
            state_machine: The game's state machine
            llm_config: LLM configuration for personality and narrative
            session_memory: Optional session memory
            opponent_model_manager: Optional opponent model manager
            game_id: Game identifier
            owner_id: Owner/user ID
            capture_label_repo: Optional capture label repository
            decision_analysis_repo: Optional decision analysis repository
            prompt_config: Optional prompt configuration
        """
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

        logger.info(f"[HYBRID] Created HybridAIController for {player_name}")

    def _get_ai_decision(self, message: str, **context) -> Dict:
        """Override: Use bounded options for decision making.

        Args:
            message: The base game state message (from parent's prompt building)
            **context: Decision context including valid_actions, call_amount, etc.

        Returns:
            Decision dict with action, raise_to, dramatic_sequence, hand_strategy
        """
        game_state = self.state_machine.game_state
        player = game_state.current_player

        # Step 1: Build rule context for option generation
        rule_context = self._build_rule_context(game_state, player, context)

        # Step 2: Generate bounded options
        options = generate_bounded_options(rule_context)

        if not options:
            logger.warning(f"[HYBRID] No options generated for {self.player_name}, using fallback")
            return self._create_fallback_response('check' if 'check' in context.get('valid_actions', []) else 'fold')

        # Step 3: Build choice prompt for LLM
        choice_prompt = self._build_choice_prompt(message, options, rule_context)

        # Step 4: Get LLM choice + narrative
        try:
            llm_response = self.assistant.chat_full(
                choice_prompt,
                json_format=True,
                capture_enricher=self._make_hybrid_enricher(options, rule_context),
            )

            response_dict = parse_json_response(llm_response.content)

        except Exception as e:
            logger.warning(f"[HYBRID] LLM call failed for {self.player_name}: {e}")
            response_dict = None

        # Step 5: Validate choice and extract action
        chosen = self._validate_and_select(response_dict, options)

        return chosen

    def _build_rule_context(self, game_state, player, context: Dict) -> Dict:
        """Build context dictionary for rule evaluation.

        Similar to RuleBotController but using parent's context values.
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

        # Effective stack (min of ours and largest opponent)
        opponent_stacks = [p.stack for p in opponents]
        effective_stack = min(player.stack, max(opponent_stacks)) if opponent_stacks else player.stack
        effective_stack_bb = effective_stack / big_blind if big_blind > 0 else 100

        # Stack-to-pot ratio
        spr = effective_stack / pot_total if pot_total > 0 else float('inf')

        # Get equity using range-based calculation (accounts for opponent actions/positions)
        if community_cards:
            # Build opponent info for range-based equity
            opponent_infos = []
            table_positions = game_state.table_positions
            position_by_name = {name: pos for pos, name in table_positions.items()}

            for opp in opponents:
                opp_position = position_by_name.get(opp.name, "button")

                # Get observed stats from opponent model manager if available
                opp_model_data = None
                if self.opponent_model_manager:
                    opp_model = self.opponent_model_manager.get_model(self.player_name, opp.name)
                    if opp_model and opp_model.tendencies:
                        opp_model_data = opp_model.tendencies.to_dict()

                opponent_infos.append(build_opponent_info(
                    name=opp.name,
                    position=opp_position,
                    opponent_model=opp_model_data,
                ))

            # Use range-based equity calculation
            equity_config = EquityConfig(use_enhanced_ranges=True)
            equity = calculate_equity_vs_ranges(
                hole_cards, community_cards, opponent_infos,
                iterations=300, config=equity_config
            )
            if equity is None:
                equity = 0.5  # Fallback if calculation fails
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

        # Calculate required equity for pot odds
        required_equity = calculate_required_equity(pot_total, cost_to_call)

        return {
            'player_name': player.name,
            'player_stack': player.stack,
            'stack_bb': player.stack / big_blind if big_blind > 0 else 100,
            'pot_total': pot_total,
            'pot_odds': pot_total / cost_to_call if cost_to_call > 0 else float('inf'),
            'cost_to_call': cost_to_call,
            'already_bet': player.bet,
            'highest_bet': game_state.highest_bet,
            'min_raise': min_raise_to,
            'max_raise': max_raise_to,
            'big_blind': big_blind,
            'equity': equity,
            'required_equity': required_equity,
            'canonical_hand': _get_canonical_hand(hole_cards) if hole_cards else '',
            'hole_cards': hole_cards,
            'community_cards': community_cards,
            'phase': phase,
            'position': position,
            'num_opponents': num_opponents,
            'effective_stack': effective_stack,
            'effective_stack_bb': effective_stack_bb,
            'spr': spr,
            'valid_actions': context.get('valid_actions', []),
        }

    def _build_choice_prompt(self, base_message: str, options: List[BoundedOption], context: Dict) -> str:
        """Build the choice prompt for the LLM.

        Presents the bounded options and asks the LLM to pick one with narrative.
        """
        equity = context.get('equity', 0.5)
        pot_odds = context.get('pot_odds', 0)

        options_text = format_options_for_prompt(options, equity, pot_odds)

        choice_template = """
{base_message}

{options_text}

Pick the option that fits your personality and the moment.

Respond with JSON:
{{
  "choice": <option number 1-{num_options}>,
  "inner_monologue": "your brief reasoning for this choice",
  "dramatic_sequence": ["*action*", "speech", ...],
  "hand_strategy": "one sentence summary of your approach"
}}

CRITICAL RULES:
- You MUST pick one of the numbered options above
- "choice" must be an integer from 1 to {num_options}
- Keep dramatic_sequence to 1-3 beats for normal hands, more for dramatic moments
- Each beat is EITHER an action (*in asterisks*) OR speech (plain text)
- Stay in character with your personality
"""

        return choice_template.format(
            base_message=base_message,
            options_text=options_text,
            num_options=len(options),
        )

    def _validate_and_select(self, response: Optional[Dict], options: List[BoundedOption]) -> Dict:
        """Validate LLM choice and build response dict.

        Falls back to the highest +EV option if LLM response is invalid.
        """
        default_option = self._get_best_fallback_option(options)

        if response is None:
            logger.warning(f"[HYBRID] No response from LLM, using fallback")
            return self._option_to_response(default_option, {})

        # Extract and validate choice
        choice = response.get('choice')
        if choice is None:
            logger.warning(f"[HYBRID] No choice in response, using fallback")
            return self._option_to_response(default_option, response)

        try:
            choice_idx = int(choice) - 1  # Convert to 0-indexed
            if choice_idx < 0 or choice_idx >= len(options):
                logger.warning(f"[HYBRID] Choice {choice} out of range [1-{len(options)}], using fallback")
                return self._option_to_response(default_option, response)
        except (ValueError, TypeError):
            logger.warning(f"[HYBRID] Invalid choice value: {choice}, using fallback")
            return self._option_to_response(default_option, response)

        selected = options[choice_idx]
        logger.info(f"[HYBRID] {self.player_name} chose option {choice}: {selected.action}")

        return self._option_to_response(selected, response)

    def _get_best_fallback_option(self, options: List[BoundedOption]) -> BoundedOption:
        """Get the best fallback option based on EV estimate.

        Priority: +EV > neutral > -EV, then by style (standard > conservative > aggressive)
        """
        ev_priority = {'+EV': 0, 'neutral': 1, '-EV': 2}
        style_priority = {'standard': 0, 'conservative': 1, 'aggressive': 2, 'trappy': 3}

        sorted_options = sorted(
            options,
            key=lambda o: (
                ev_priority.get(o.ev_estimate, 3),
                style_priority.get(o.style_tag, 4)
            )
        )

        return sorted_options[0] if sorted_options else options[0]

    def _option_to_response(self, option: BoundedOption, llm_response: Dict) -> Dict:
        """Convert a BoundedOption to a decision response dict."""
        return {
            'action': option.action,
            'raise_to': option.raise_to,
            'dramatic_sequence': llm_response.get('dramatic_sequence', []),
            'hand_strategy': llm_response.get('hand_strategy', f'{option.style_tag} {option.action}'),
            'inner_monologue': llm_response.get('inner_monologue', ''),
            'bluff_likelihood': 0,  # Could be inferred from option.ev_estimate in future
        }

    def _create_fallback_response(self, action: str) -> Dict:
        """Create a minimal fallback response."""
        return {
            'action': action,
            'raise_to': 0,
            'dramatic_sequence': [],
            'hand_strategy': 'fallback action',
            'inner_monologue': '',
            'bluff_likelihood': 0,
        }

    def _make_hybrid_enricher(self, options: List[BoundedOption], context: Dict):
        """Create an enricher callback for prompt captures with hybrid context."""
        def enrich_capture(capture_data: Dict) -> Dict:
            capture_data.update({
                'hybrid_mode': True,
                'bounded_options': [o.to_dict() for o in options],
                'equity': context.get('equity'),
                'pot_odds': context.get('pot_odds'),
                'required_equity': context.get('required_equity'),
                'phase': context.get('phase'),
                'stack_bb': context.get('stack_bb'),
            })
            return capture_data
        return enrich_capture
