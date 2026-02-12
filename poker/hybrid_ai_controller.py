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
    OptionProfile,
    STYLE_PROFILES,
    STYLE_HINTS,
    generate_bounded_options,
    format_options_for_prompt,
    calculate_required_equity,
    apply_emotional_window_shift,
    get_emotional_shift,
)
from .hand_tiers import PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS
from .hand_ranges import (
    calculate_equity_vs_ranges,
    build_opponent_info,
    EquityConfig,
)
from .ai_resilience import parse_json_response
from .playstyle_selector import (
    _build_stat_lines,
    build_exploit_tips,
    _select_biggest_threat,
    MINDSET_FRAMES,
)

logger = logging.getLogger(__name__)


class HybridAIController(AIPlayerController):
    """AI that picks from rule-bounded options.

    Inherits full psychology, memory, and personality from AIPlayerController.
    Overrides _get_ai_decision to present bounded options to the LLM.
    """

    LEAN_SYSTEM_PROMPT = "You are a poker player. Pick one option. Respond with JSON."

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

        # Phase 0: track hand transitions for per-hand memory clearing
        # -1 ensures first hand is always detected as new
        self._last_hand_number: int = -1

        lean = getattr(self.prompt_config, 'lean_bounded', False)
        logger.info(f"[HYBRID] Created HybridAIController for {player_name} (lean={lean})")

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
            if looseness < 0.45:
                key = 'tight_passive' if aggression < 0.5 else 'tight_aggressive'
            elif looseness > 0.65:
                key = 'loose_passive' if aggression < 0.5 else 'loose_aggressive'
            else:
                key = 'default'
        else:
            key = 'default'
        return key, STYLE_PROFILES.get(key, OptionProfile())

    def decide_action(self, game_messages) -> Dict:
        """Override: dispatch to lean path when lean_bounded is enabled."""
        if getattr(self.prompt_config, 'lean_bounded', False):
            return self._decide_action_lean(game_messages)
        return super().decide_action(game_messages)

    def _decide_action_lean(self, game_messages) -> Dict:
        """Lean bounded path: minimal prompt, no parent pipeline.

        Bypasses all parent prompt building (psychology, memory, chattiness,
        tilt effects, etc.) and sends only cards + options to the LLM.

        When hand_plan is enabled, fires Phase 0 at the start of each hand
        to generate a strategy plan. The plan stays in the decision thread
        so Phase 1 decisions see it as prior context.
        """
        from core.llm.tracking import update_prompt_capture

        game_state = self.state_machine.game_state
        player = game_state.current_player

        # Store messages for compatibility
        self._current_game_messages = game_messages

        hand_plan_enabled = getattr(self.prompt_config, 'hand_plan', False)
        current_hand = self.current_hand_number or 0
        is_new_hand = current_hand != self._last_hand_number

        # Manage conversation memory
        if hasattr(self, 'assistant') and self.assistant and self.assistant.memory:
            if hand_plan_enabled:
                # Clear memory once at hand start, preserve within hand
                if is_new_hand:
                    self.assistant.memory.clear()
            else:
                # Original behavior: clear every decision
                keep_exchanges = getattr(self.prompt_config, 'memory_keep_exchanges', 0)
                if keep_exchanges > 0:
                    self.assistant.memory.trim_to_exchanges(keep_exchanges)
                else:
                    self.assistant.memory.clear()

        # Update playstyle at hand start (lean path bypasses parent's update_playstyle call)
        if hand_plan_enabled and is_new_hand and self.psychology:
            opponent_models = (
                self.opponent_model_manager.get_all_models_for_observer(self.player_name)
                if self.opponent_model_manager else None
            )
            self.psychology.update_playstyle(
                opponent_models=opponent_models,
                hand_number=current_hand,
            )

        # Build context for option generation
        player_options = game_state.current_player_options
        big_blind = game_state.current_ante or 100
        raw_cost_to_call = game_state.highest_bet - player.bet
        cost_to_call = min(raw_cost_to_call, player.stack)

        # Calculate raise bounds (same logic as parent decide_action)
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

        context = {
            'valid_actions': player_options,
            'call_amount': cost_to_call,
            'min_raise': min_raise_to,
            'max_raise': max_raise_to,
        }

        # Build rule context and generate options with style profile
        rule_context = self._build_rule_context(game_state, player, context)
        profile_key, profile = self._get_option_profile()
        options = generate_bounded_options(rule_context, profile)

        if not options:
            logger.warning(f"[HYBRID-LEAN] No options for {self.player_name}, fallback")
            return self._create_fallback_response(
                'check' if 'check' in player_options else 'fold'
            )

        # Layer 6: Emotional window shift
        emotional_shift = get_emotional_shift(self.psychology)
        if emotional_shift.severity != 'none':
            options = apply_emotional_window_shift(
                options, emotional_shift, rule_context, profile,
            )

        # Swap system prompt to minimal (covers both Phase 0 and Phase 1)
        original_system_message = self.assistant.system_message
        self.assistant.system_message = self.LEAN_SYSTEM_PROMPT

        capture_id = [None]
        try:
            # Phase 0: Generate hand plan at hand start
            if hand_plan_enabled and is_new_hand:
                self._execute_hand_plan(rule_context, profile_key)
                self._last_hand_number = current_hand

            # Phase 1: Build minimal prompt and get decision
            lean_prompt = self._build_lean_prompt(options, rule_context, profile_key)

            llm_response = self.assistant.chat_full(
                lean_prompt,
                json_format=True,
                hand_number=self.current_hand_number,
                prompt_template='decision_lean_bounded',
                capture_enricher=self._make_hybrid_enricher(
                    options, rule_context, capture_id, profile_key=profile_key
                ),
            )
            response_dict = parse_json_response(llm_response.content)
        except Exception as e:
            logger.warning(
                f"[HYBRID-LEAN] LLM/parse failed for {self.player_name}: "
                f"{type(e).__name__}: {e}",
                exc_info=True
            )
            response_dict = None
        finally:
            self.assistant.system_message = original_system_message

        # Validate and select
        chosen = self._validate_and_select(response_dict, options)

        # Update capture
        if capture_id[0]:
            action = chosen.get('action')
            raise_amount = chosen.get('raise_to') if action == 'raise' else None
            update_prompt_capture(capture_id[0], action_taken=action, raise_amount=raise_amount)

        # Post-decision bookkeeping: energy events for psychology
        action = chosen.get('action', '')
        self.last_energy_events = []
        if action and self.psychology:
            self.last_energy_events = self.psychology.on_action_taken(action)

        return chosen

    def _build_lean_prompt(self, options: List[BoundedOption], context: Dict, profile_key: str = 'default') -> str:
        """Build minimal prompt: just cards, situation, and numbered options.

        When hand_plan is enabled, style hints are omitted — the hand plan
        in the decision thread provides personality-driven context instead.
        """
        hole_cards = context.get('hole_cards', [])
        community_cards = context.get('community_cards', [])
        big_blind = context.get('big_blind', 100)

        # Cards
        parts = [f"Cards: {' '.join(hole_cards)}"]
        if community_cards:
            parts[0] += f" | Board: {' '.join(community_cards)}"

        # Situation in BB
        stack_bb = context.get('stack_bb', 0)
        pot_bb = context.get('pot_total', 0) / big_blind if big_blind > 0 else 0
        parts.append(f"Stack: {stack_bb:.0f} BB | Pot: {pot_bb:.1f} BB")

        # Style hint (omitted when hand_plan provides context via decision thread)
        if not getattr(self.prompt_config, 'hand_plan', False):
            style_hint = STYLE_HINTS.get(profile_key, '')
            if style_hint:
                parts.append(style_hint)

        parts.append("")

        # Numbered options with EV labels
        for i, opt in enumerate(options, 1):
            action_str = opt.action.upper()
            if opt.action == 'raise' and opt.raise_to > 0:
                raise_bb = opt.raise_to / big_blind if big_blind > 0 else opt.raise_to
                action_str += f" {raise_bb:.0f}BB"
            parts.append(f"{i}. {action_str}  [{opt.ev_estimate}]  {opt.rationale}")

        parts.append("")
        parts.append(f'Pick 1-{len(options)}: {{"choice": N}}')

        return "\n".join(parts)

    # === Phase 0: Hand Plan ===

    def _execute_hand_plan(self, rule_context: Dict, profile_key: str) -> None:
        """Execute Phase 0: generate a hand plan at the start of a new hand.

        The plan response stays in the decision thread so Phase 1 decisions
        see it as prior context (the LLM's own earlier output).
        """
        plan_prompt = self._build_hand_plan_prompt(rule_context, profile_key)

        plan_capture_id = [None]
        try:
            plan_response = self.assistant.chat_full(
                plan_prompt,
                json_format=True,
                hand_number=self.current_hand_number,
                prompt_template='hand_plan',
                capture_enricher=self._make_hand_plan_enricher(
                    rule_context, profile_key, plan_capture_id
                ),
            )
            plan_dict = parse_json_response(plan_response.content)
            plan_text = plan_dict.get('plan', '') if plan_dict else ''
            logger.info(
                f"[HYBRID-PLAN] {self.player_name} hand {self.current_hand_number} "
                f"({profile_key}): {plan_text[:80]}"
            )
        except Exception as e:
            logger.warning(f"[HYBRID-PLAN] Phase 0 failed for {self.player_name}: {e}")

    # Playstyle cues for Phase 0 hand plans (short, directive)
    PLAYSTYLE_PLAN_CUES = {
        'commanding': "You play aggressively for maximum value.",
        'aggro': "You play aggressively and attack weakness.",
        'poker_face': "You play a balanced, math-driven game.",
        'guarded': "You play cautiously and control the pot.",
    }

    def _build_hand_plan_prompt(self, rule_context: Dict, profile_key: str) -> str:
        """Build Phase 0 prompt with playstyle-specific strategic context.

        Always includes a mindset frame and playstyle cue so plans are
        personality-differentiated even at basic engagement. Higher tiers
        add more context (stats, exploit tips).
        """
        hole_cards = rule_context.get('hole_cards', [])
        position = rule_context.get('position', '?')
        stack_bb = rule_context.get('stack_bb', 0)
        num_opponents = rule_context.get('num_opponents', 0)

        parts = [f"Cards: {' '.join(hole_cards)}"]
        parts.append(f"Position: {position}")
        parts.append(f"Stack: {stack_bb:.0f} BB | Opponents: {num_opponents}")

        # Get playstyle and engagement from psychology (if available)
        active_playstyle = 'poker_face'
        engagement = 'basic'
        if self.psychology and hasattr(self.psychology, 'playstyle_state'):
            ps = self.psychology.playstyle_state
            active_playstyle = ps.active_playstyle
            engagement = ps.engagement

        # Always include mindset frame for personality differentiation
        mindset = MINDSET_FRAMES.get(active_playstyle, '')
        if mindset:
            parts.append(mindset)

        # Medium+ engagement: add richer strategic context
        if engagement in ('medium', 'full'):
            context_lines = self._build_plan_strategic_context(
                active_playstyle, engagement, rule_context
            )
            if context_lines:
                parts.append(context_lines)

        # Playstyle-aware plan instruction
        style_cue = self.PLAYSTYLE_PLAN_CUES.get(active_playstyle, '')
        parts.append("")
        if style_cue:
            parts.append(f'{style_cue} Plan this hand in 1 sentence.')
        else:
            parts.append('Plan this hand in 1 sentence.')
        parts.append('{"plan": "..."}')

        return "\n".join(parts)

    def _build_plan_strategic_context(
        self, active_playstyle: str, engagement: str, rule_context: Dict
    ) -> str:
        """Build playstyle-specific strategic context for the hand plan.

        Reuses existing briefing functions from playstyle_selector.py.
        """
        lines = []
        big_blind = rule_context.get('big_blind', 100)

        # Full engagement: curated stats per playstyle
        if engagement == 'full':
            # Compute avg stack from game state
            game_state = self.state_machine.game_state
            active_players = [p for p in game_state.players if not p.is_folded]
            avg_stack = (
                sum(p.stack for p in active_players) / len(active_players)
                if active_players else 0
            )

            # Get threat info for aggro style
            threat_name = None
            threat_summary = None
            if active_playstyle == 'aggro' and self.opponent_model_manager:
                models = self.opponent_model_manager.get_all_models_for_observer(
                    self.player_name
                )
                threat = _select_biggest_threat(models)
                if threat:
                    threat_name = threat.opponent
                    threat_summary = f"AF {threat.tendencies.aggression_factor:.1f}, VPIP {threat.tendencies.vpip:.0%}"

            stat_lines = _build_stat_lines(
                active_playstyle,
                player_stack=rule_context.get('player_stack', 0),
                avg_stack=avg_stack,
                pot_total=rule_context.get('pot_total', 0),
                big_blind=big_blind,
                threat_name=threat_name,
                threat_summary=threat_summary,
            )
            if stat_lines:
                lines.append(stat_lines)

        # Medium+ engagement: exploit tips from opponent models
        threat_model = None
        if self.opponent_model_manager:
            models = self.opponent_model_manager.get_all_models_for_observer(
                self.player_name
            )
            threat_model = _select_biggest_threat(models)

        exploit_tips = build_exploit_tips(
            active_playstyle, engagement, threat_model=threat_model
        )
        if exploit_tips:
            lines.append(exploit_tips)

        # Note: mindset frame is now always added in _build_hand_plan_prompt
        # (not gated on engagement) so it's not repeated here.

        return "\n".join(lines)

    def _make_hand_plan_enricher(
        self, rule_context: Dict, profile_key: str, capture_id_holder: List
    ):
        """Create enricher for Phase 0 hand plan captures."""
        active_playstyle = 'poker_face'
        engagement = 'basic'
        if self.psychology and hasattr(self.psychology, 'playstyle_state'):
            ps = self.psychology.playstyle_state
            active_playstyle = ps.active_playstyle
            engagement = ps.engagement

        lean = getattr(self.prompt_config, 'lean_bounded', False)

        def enrich_capture(capture_data: Dict) -> Dict:
            capture_data.update({
                'hybrid_mode': True,
                'lean_bounded': lean,
                'hand_plan': True,
                'style_profile': profile_key,
                'active_playstyle': active_playstyle,
                'engagement': engagement,
                'phase': rule_context.get('phase'),
                'stack_bb': rule_context.get('stack_bb'),
                'player_hand': rule_context.get('hole_cards', []),
                'position': rule_context.get('position'),
                'num_opponents': rule_context.get('num_opponents'),
                '_on_captured': lambda cid: capture_id_holder.__setitem__(0, cid),
            })
            return capture_data
        return enrich_capture

    def _get_ai_decision(self, message: str, **context) -> Dict:
        """Override: Use bounded options for decision making.

        Used when lean_bounded is False (regular hybrid mode).
        """
        from core.llm.tracking import update_prompt_capture

        game_state = self.state_machine.game_state
        player = game_state.current_player

        # Step 1: Build rule context for option generation
        rule_context = self._build_rule_context(game_state, player, context)

        # Step 2: Generate bounded options with style profile
        profile_key, profile = self._get_option_profile()
        options = generate_bounded_options(rule_context, profile)

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
                capture_enricher=self._make_hybrid_enricher(options, rule_context, capture_id, profile_key=profile_key),
            )

            response_dict = parse_json_response(llm_response.content)

        except Exception as e:
            logger.warning(f"[HYBRID] LLM call failed for {self.player_name}: {e}")
            response_dict = None

        # Step 5: Validate choice and extract action
        chosen = self._validate_and_select(response_dict, options)

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
                logger.warning(
                    f"[HYBRID] Equity calculation returned None for {player.name}. "
                    f"Falling back to 0.5. hole_cards={hole_cards}, "
                    f"community_cards={community_cards}"
                )
                equity = 0.5
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

    def _make_hybrid_enricher(self, options: List[BoundedOption], context: Dict, capture_id_holder: List, profile_key: str = 'default'):
        """Create an enricher callback for prompt captures with hybrid context.

        Args:
            options: List of bounded options presented to LLM
            context: Rule context with equity, pot odds, etc.
            capture_id_holder: Single-element list to store capture ID for post-update
            profile_key: Style profile name used for option generation
        """
        game_state = self.state_machine.game_state
        player = game_state.current_player
        big_blind = game_state.current_ante or 100

        lean = getattr(self.prompt_config, 'lean_bounded', False)

        # Capture emotional shift state at enricher creation time
        emotional_shift = get_emotional_shift(self.psychology)

        def enrich_capture(capture_data: Dict) -> Dict:
            # Core hybrid data
            capture_data.update({
                'hybrid_mode': True,
                'lean_bounded': lean,
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
                # Emotional window shift tracking
                'emotional_shift': emotional_shift.to_dict(),
                # Capture ID callback for post-update
                '_on_captured': lambda cid: capture_id_holder.__setitem__(0, cid),
            })
            return capture_data
        return enrich_capture
