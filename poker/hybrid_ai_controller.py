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

from .archetypes import classify_from_anchors
from .controllers import (
    AIPlayerController, _get_canonical_hand, card_to_string,
)
from .bounded_options import (
    BoundedOption,
    OptionProfile,
    STYLE_PROFILES,
    generate_bounded_options,
    format_options_for_prompt,
    calculate_required_equity,
    apply_emotional_window_shift,
    get_emotional_shift,
)
from .hand_ranges import (
    calculate_equity_vs_ranges,
    build_opponent_info,
    EquityConfig,
)
from .ai_resilience import parse_json_response
from .range_guidance import (
    looseness_to_range_pct,
    _game_position_to_range_key,
    _position_display_name,
)
from .hand_tiers import is_hand_in_range
from .stack_utils import effective_stack_chips, effective_stack_bb, spr as compute_spr

logger = logging.getLogger(__name__)


class HybridAIController(AIPlayerController):
    """AI that picks from rule-bounded options.

    Inherits full psychology, memory, and personality from AIPlayerController.
    Overrides _get_ai_decision to present bounded options to the LLM. The
    parent's full prompt pipeline runs first (persona, chattiness, emotional
    state, tilt effects, mind games, GTO equity, etc.); bounded options are
    appended to constrain the action choice.

    For the cheaper minimal-prompt path, see LeanBoundedController.
    """

    # LeanBoundedController overrides this so captures still tag lean decisions.
    LEAN_BOUNDED: bool = False

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

    def _compute_range_data(self, rule_context: Dict) -> Dict:
        """Compute preflop range data for range-biased option generation.

        Returns dict with:
            in_range: bool - whether hand is in player's range
            range_pct: float - player's range percentage
            effective_looseness: float - current looseness value
            position_display: str - human-readable position
        """
        canonical = rule_context.get('canonical_hand', '')
        game_position = rule_context.get('position') or 'middle'

        # Get effective looseness from psychology
        if self.psychology:
            effective_looseness = self.psychology.effective_looseness
        else:
            effective_looseness = 0.5

        num_opponents = rule_context.get('num_opponents')
        range_key = _game_position_to_range_key(game_position)
        range_pct = looseness_to_range_pct(effective_looseness, range_key, num_opponents=num_opponents)
        in_range = is_hand_in_range(canonical, range_pct) if canonical else True
        position_display = _position_display_name(range_key)

        return {
            'in_range': in_range,
            'range_pct': range_pct,
            'effective_looseness': effective_looseness,
            'position_display': position_display,
        }

    def _get_option_profile(self) -> tuple:
        """Map psychology looseness + aggression to an OptionProfile.

        Uses effective_looseness and effective_aggression from psychology axes,
        which incorporate personality anchors and emotional modifiers.

        Returns:
            (profile_key, OptionProfile) tuple. Falls back to ('default', OptionProfile()).
            Returns default profile when style_aware_options is disabled.
        """
        if not getattr(self.prompt_config, 'style_aware_options', True):
            return 'default', STYLE_PROFILES['default']

        if self.psychology:
            looseness = self.psychology.effective_looseness
            aggression = self.psychology.effective_aggression
            key = classify_from_anchors(looseness, aggression)
        else:
            key = 'default'
        return key, STYLE_PROFILES.get(key, OptionProfile())

    def _get_ai_decision(self, message: str, **context) -> Dict:
        """Override: Use bounded options for decision making.

        Runs the parent's full prompt pipeline (persona, chattiness,
        emotional state, tilt effects, GTO equity, etc.) and appends
        a choice prompt containing bounded options. The LLM picks one
        and provides narrative; rule-bounded options guarantee no
        catastrophic action selection.
        """
        from core.llm.tracking import update_prompt_capture

        game_state = self.state_machine.game_state
        player = game_state.current_player

        # Step 1: Build rule context for option generation
        rule_context = self._build_rule_context(game_state, player, context)

        # Step 2: Generate bounded options with style profile
        profile_key, profile = self._get_option_profile()

        # Compute range data for preflop range biasing
        range_gate_enabled = getattr(self.prompt_config, 'preflop_range_gate', False)
        range_data = self._compute_range_data(rule_context) if range_gate_enabled else {}

        options = generate_bounded_options(
            rule_context,
            profile,
            phase=rule_context.get('phase'),
            in_range=range_data.get('in_range', True),
            range_pct=range_data.get('range_pct'),
            position_display=range_data.get('position_display'),
        )

        if not options:
            logger.warning(f"[HYBRID] No options generated for {self.player_name}, using fallback")
            return self._create_fallback_response('check' if 'check' in context.get('valid_actions', []) else 'fold')

        # Step 2b: Emotional window shift
        emotional_shift = get_emotional_shift(self.psychology)
        if emotional_shift.severity != 'none':
            options = apply_emotional_window_shift(
                options, emotional_shift, rule_context, profile,
            )

        # Step 3: Build choice prompt for LLM
        choice_prompt = self._build_choice_prompt(message, options, rule_context)

        # Track capture ID for post-decision update
        capture_id = [None]

        # Step 4: Get LLM choice + narrative
        try:
            llm_response = self.assistant.chat_full(
                choice_prompt,
                json_format=True,
                hand_number=self.current_hand_number,
                prompt_template='decision_bounded',
                capture_enricher=self._make_hybrid_enricher(
                    options, rule_context, capture_id, profile_key=profile_key,
                    range_data=range_data,
                ),
            )

            response_dict = parse_json_response(llm_response.content)

        except Exception as e:
            logger.warning(f"[HYBRID] LLM call failed for {self.player_name}: {e}")
            response_dict = None

        # Step 5: Validate choice and extract action
        chosen = self._validate_and_select(response_dict, options)

        # Step 5b: Analyze decision quality (populates player_decision_analysis table)
        self._analyze_decision(
            chosen,
            rule_context,
            capture_id[0],
            player_bet=player.bet,
            all_players_bets=[(p.bet, p.is_folded) for p in game_state.players],
            bounded_options=[o.to_dict() for o in options],
        )

        # Step 6: Update capture with final action (like parent class does)
        if capture_id[0]:
            action = chosen.get('action')
            raise_amount = chosen.get('raise_to') if action == 'raise' else None
            update_prompt_capture(capture_id[0], action_taken=action, raise_amount=raise_amount)

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

        effective_stack = effective_stack_chips(game_state, player)
        effective_stack_bb_val = effective_stack_bb(game_state, player, big_blind=big_blind)
        spr = compute_spr(game_state, player, pot_total=pot_total)

        # Get equity using range-based calculation (works for both preflop and postflop)
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

        equity_config = EquityConfig(use_enhanced_ranges=True)
        equity = calculate_equity_vs_ranges(
            hole_cards, community_cards, opponent_infos,
            iterations=300, config=equity_config
        )
        if equity is None:
            logger.warning(
                f"[HYBRID] Equity calculation returned None for {player.name}. "
                f"Falling back to 0.5. hole_cards={hole_cards}, "
                f"community_cards={community_cards}"
            )
            equity = 0.5

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
            'pot_odds': pot_total / cost_to_call if cost_to_call > 0 else None,
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
            'effective_stack_bb': effective_stack_bb_val,
            'spr': spr,
            'valid_actions': context.get('valid_actions', []),
            'raises_this_round': game_state.raises_this_round,
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
        Tries multiple extraction strategies before giving up:
        1. Direct int conversion (handles "2" and 2)
        2. First digit extraction from fuzzy text ("option 2", "I pick 1")
        """
        import re

        default_option = self._get_best_fallback_option(options)

        if response is None:
            logger.warning(f"[HYBRID] No response from LLM, using fallback")
            return self._option_to_response(default_option, {})

        # Extract and validate choice
        choice = response.get('choice')
        if choice is None:
            logger.warning(f"[HYBRID] No choice in response, using fallback")
            return self._option_to_response(default_option, response)

        # Strategy 1: direct int conversion (handles int and string digits)
        choice_idx = None
        try:
            choice_idx = int(choice) - 1
        except (ValueError, TypeError):
            pass

        # Strategy 2: extract first digit from fuzzy text ("option 2", "I pick 1", "N")
        if choice_idx is None and isinstance(choice, str):
            match = re.search(r'\d+', choice)
            if match:
                try:
                    choice_idx = int(match.group()) - 1
                except (ValueError, TypeError):
                    pass

        # Validate range
        if choice_idx is None or choice_idx < 0 or choice_idx >= len(options):
            logger.warning(f"[HYBRID] Invalid/out-of-range choice: {choice!r} (1-{len(options)}), using fallback")
            return self._option_to_response(default_option, response)

        selected = options[choice_idx]
        logger.info(f"[HYBRID] {self.player_name} chose option {choice}: {selected.action}")

        return self._option_to_response(selected, response)

    def _get_best_fallback_option(self, options: List[BoundedOption]) -> BoundedOption:
        """Get the best fallback option based on EV estimate.

        Priority: +EV > neutral > -EV, then by style (standard > conservative > aggressive)
        """
        if not options:
            logger.error("[HYBRID] _get_best_fallback_option called with empty options list")
            return BoundedOption(
                action='check', raise_to=0,
                rationale="Fallback (no options available)",
                ev_estimate="neutral", style_tag="conservative",
            )

        ev_priority = {'+EV': 0, 'neutral': 1, 'marginal': 2, '-EV': 3}
        style_priority = {'standard': 0, 'conservative': 1, 'aggressive': 2, 'trappy': 3}

        sorted_options = sorted(
            options,
            key=lambda o: (
                ev_priority.get(o.ev_estimate, 3),
                style_priority.get(o.style_tag, 4)
            )
        )

        return sorted_options[0]

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

    def _make_hybrid_enricher(
        self,
        options: List[BoundedOption],
        context: Dict,
        capture_id_holder: List,
        profile_key: str = 'default',
        range_data: Dict = None,
    ):
        """Create an enricher callback for prompt captures with hybrid context.

        Args:
            options: List of bounded options presented to LLM
            context: Rule context with equity, pot odds, etc.
            capture_id_holder: Single-element list to store capture ID for post-update
            profile_key: Style profile name used for option generation
            range_data: Range check results (in_range, range_pct, effective_looseness)
        """
        game_state = self.state_machine.game_state
        player = game_state.current_player
        big_blind = game_state.current_ante or 100

        rd = range_data or {}

        # Capture emotional shift state at enricher creation time
        emotional_shift = get_emotional_shift(self.psychology)
        rd = range_data or {}

        def enrich_capture(capture_data: Dict) -> Dict:
            # Core hybrid data
            capture_data.update({
                'hybrid_mode': True,
                'lean_bounded': self.LEAN_BOUNDED,
                'style_profile': profile_key,
                'bounded_options': [o.to_dict() for o in options],
                'equity': context.get('equity'),
                'pot_odds': context.get('pot_odds'),
                'required_equity': context.get('required_equity'),
                'phase': context.get('phase'),
                'stack_bb': context.get('stack_bb'),
                # Match parent enricher fields for consistency
                'pot_total': context.get('pot_total'),
                'cost_to_call': context.get('cost_to_call'),
                'player_stack': context.get('player_stack'),
                'already_bet_bb': player.bet / big_blind if big_blind > 0 else None,
                'community_cards': context.get('community_cards', []),
                'player_hand': context.get('hole_cards', []),
                'valid_actions': context.get('valid_actions', []),
                # Rule context fields for replay experiments
                'position': context.get('position'),
                'min_raise': context.get('min_raise'),
                'max_raise': context.get('max_raise'),
                'big_blind': big_blind,
                'canonical_hand': context.get('canonical_hand'),
                'num_opponents': context.get('num_opponents'),
                'effective_stack': context.get('effective_stack'),
                'spr': context.get('spr'),
                # Emotional window shift tracking
                'emotional_shift': emotional_shift.to_dict(),
                # Capture ID callback for post-update
                '_on_captured': lambda cid: capture_id_holder.__setitem__(0, cid),
            })
            # Range gate tracking
            if rd:
                capture_data.update({
                    'in_range': rd.get('in_range'),
                    'range_pct': rd.get('range_pct'),
                    'effective_looseness': rd.get('effective_looseness'),
                })
            return capture_data
        return enrich_capture
