"""
LeanBoundedController - Bounded-options bot with a minimal LLM prompt.

Cheaper, faster cousin of HybridAIController. Skips the parent's full
prompt pipeline (no chattiness, mind games, emotional state injection,
tilt narrative, GTO equity verdict, dramatic_sequence, etc.) and sends
only cards + numbered bounded options to the LLM.

Inherits everything from HybridAIController (option generation, style
profile selection, range data, validation, fallback selection, capture
enrichment, decision analysis). Overrides only decide_action and the
prompt-building helpers.

Personality reaches the table through option-profile selection
(STYLE_PROFILES) — TAG / LAG / TP / LP — not through prompt text.
Use this when LLM cost matters and you can accept the personality
muting tradeoff.
"""

import logging
import random
import re
from typing import Dict, List, Optional

from core.card import Card

from .ai_resilience import parse_json_response
from .board_analyzer import build_board_read
from .bounded_options import (
    BoundedOption,
    EmotionalShift,
    OptionProfile,
    apply_emotional_window_shift,
    generate_bounded_options,
    get_emotional_shift,
)
from .controllers import (
    _get_preflop_lines,
    _get_street_lines,
    _parse_game_messages,
    classify_preflop_hand,
)
from .hand_narrator import narrate_hand_breakdown
from .hybrid_ai_controller import HybridAIController
from .minimal_prompt import get_position_abbrev
from .nudge_phrases import apply_composed_nudges

logger = logging.getLogger(__name__)


class LeanBoundedController(HybridAIController):
    """Bounded-options controller with a minimal LLM prompt.

    Always uses the lean decision path. Bypasses parent prompt
    pipeline; sends only cards + numbered options to the LLM.
    """

    LEAN_BOUNDED: bool = True

    # Lean path sends only cards + options — never the emotional narration. So
    # like the solver/rule bots, only narrate in heads-up (for the panel).
    USES_EMOTIONAL_NARRATION = False

    LEAN_SYSTEM_PROMPT = (
        'You are a poker player. Pick one option. '
        'Return JSON: {"reasoning": "1-2 sentences explaining your choice", "choice": <number>}'
    )

    def decide_action(self, game_messages) -> Dict:
        """Always dispatch to lean path."""
        return self._decide_action_lean(game_messages)

    def _decide_action_lean(self, game_messages) -> Dict:
        """Lean bounded path: minimal prompt, no parent pipeline.

        Bypasses all parent prompt building (psychology, memory, chattiness,
        tilt effects, etc.) and sends only cards + options to the LLM.
        """
        from core.llm.tracking import update_prompt_capture

        game_state = self.state_machine.game_state
        player = game_state.current_player

        # Store messages for compatibility
        self._current_game_messages = game_messages

        # Manage conversation memory
        if hasattr(self, 'assistant') and self.assistant and self.assistant.memory:
            keep_exchanges = getattr(self.prompt_config, 'memory_keep_exchanges', 0)
            if keep_exchanges > 0:
                self.assistant.memory.trim_to_exchanges(keep_exchanges)
            else:
                self.assistant.memory.clear()

        # Build context for option generation
        player_options = game_state.current_player_options
        raw_cost_to_call = game_state.highest_bet - player.bet
        cost_to_call = min(raw_cost_to_call, player.stack)

        # Calculate raise bounds (same logic as parent decide_action)
        highest_bet = game_state.highest_bet
        max_opponent_stack = max(
            (
                p.stack
                for p in game_state.players
                if not p.is_folded and not p.is_all_in and p.name != player.name
            ),
            default=0,
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
            apply_hu_equity_offset=getattr(self.prompt_config, 'hu_equity_offset', False),
        )

        if not options:
            logger.warning(f"[LEAN] No options for {self.player_name}, fallback")
            return self._create_fallback_response('check' if 'check' in player_options else 'fold')

        # Layer 5.5: Composed nudges (replace raw rationale with playstyle phrases)
        is_heads_up = rule_context.get('num_opponents', 2) <= 1
        if getattr(self.prompt_config, 'composed_nudges', False):
            options = apply_composed_nudges(options, profile_key, is_heads_up=is_heads_up)

        # Layer 6: Emotional window shift (may override nudge rationale at moderate+)
        emotional_shift = get_emotional_shift(self.psychology)
        if emotional_shift.severity != 'none':
            options = apply_emotional_window_shift(
                options,
                emotional_shift,
                rule_context,
                profile,
            )

        # Option ordering (local RNG per project convention)
        option_order = getattr(self.prompt_config, 'option_order', 'default')
        if option_order == 'shuffle':
            rng = random.Random()
            rng.shuffle(options)
        elif option_order == 'ev_descending':
            ev_rank = {'+EV': 0, 'neutral': 1, 'marginal': 2, '-EV': 3}
            options.sort(key=lambda o: ev_rank.get(o.ev_estimate, 4))

        # Swap system prompt to minimal
        original_system_message = self.assistant.system_message
        self.assistant.system_message = self.LEAN_SYSTEM_PROMPT

        capture_id = [None]
        try:
            # Build minimal prompt and get decision
            lean_prompt = self._build_lean_prompt(
                options,
                rule_context,
                profile_key,
                profile=profile,
                emotional_shift=emotional_shift,
            )

            llm_response = self.assistant.chat_full(
                lean_prompt,
                json_format=True,
                hand_number=self.current_hand_number,
                prompt_template='decision_lean_bounded',
                capture_enricher=self._make_hybrid_enricher(
                    options,
                    rule_context,
                    capture_id,
                    profile_key=profile_key,
                    range_data=range_data,
                ),
            )
            response_dict = parse_json_response(llm_response.content)
        except Exception as e:
            logger.warning(
                f"[LEAN] LLM/parse failed for {self.player_name}: " f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            response_dict = None
        finally:
            self.assistant.system_message = original_system_message

        # Validate and select
        chosen = self._validate_and_select(response_dict, options)

        # Analyze decision quality (populates player_decision_analysis table)
        self._analyze_decision(
            chosen,
            rule_context,
            capture_id[0],
            player_bet=player.bet,
            all_players_bets=[(p.bet, p.is_folded) for p in game_state.players],
            bounded_options=[o.to_dict() for o in options],
        )

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

    def _build_street_action_summary(
        self, phase: str, big_blind: int, position_map: Optional[Dict[str, str]] = None
    ) -> str:
        """Build a compact summary of betting actions on the current street.

        Parses game_messages to extract actions for the current phase and
        formats them as a compact line like:
          "UTG raises 3BB → You raise 4BB → BB raises 6BB"

        When position_map is provided, opponents are labeled by their table
        position (UTG, CO, BTN, etc.) instead of OppA/OppB.

        Works with both web handler messages ("Name raises to $300.") and
        experiment runner messages ("Name raises to $300").
        """
        game_messages = getattr(self, '_current_game_messages', None)
        if not game_messages:
            return ''

        lines = _parse_game_messages(game_messages)
        if not lines:
            return ''

        if phase == 'PRE_FLOP':
            street_lines = _get_preflop_lines(lines)
        else:
            street_lines = _get_street_lines(lines, phase)

        if not street_lines:
            return ''

        actions = []
        player_name = self.player_name

        # Build opponent labels using position names when available,
        # falling back to Opp/OppA/OppB for non-lean contexts.
        opponent_names = {}
        for line in street_lines:
            m = re.match(
                r'(.+?)\s+(?:checks|calls|raises|folds|goes all-in|bets)',
                line.strip(),
                re.IGNORECASE,
            )
            if m:
                name = m.group(1).strip()
                if name != player_name and name not in opponent_names:
                    opponent_names[name] = None  # placeholder

        if position_map:
            for name in opponent_names:
                opponent_names[name] = position_map.get(name, 'Opp')
        elif len(opponent_names) == 1:
            for name in opponent_names:
                opponent_names[name] = 'Opp'
        else:
            for i, name in enumerate(opponent_names):
                opponent_names[name] = f"Opp{chr(65 + i)}"

        for line in street_lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue
            # Match: "Name raises to $300", "Name checks", "Name calls",
            #        "Name folds", "Name goes all-in"
            match = re.match(
                r'(.+?)\s+(checks|calls|raises|folds|goes all-in|bets)'
                r'(?:\s+(?:to\s+)?\$(\d+))?',
                line_stripped,
                re.IGNORECASE,
            )
            if not match:
                continue
            name = match.group(1).strip()
            action = match.group(2).lower()
            amount = match.group(3)

            who = 'You' if name == player_name else opponent_names.get(name, 'Opp')

            if amount and big_blind > 0:
                bb_val = int(amount) / big_blind
                actions.append(f"{who} {action} {bb_val:.0f}BB")
            else:
                actions.append(f"{who} {action}")

        if not actions:
            return ''
        return ' → '.join(actions)

    def _build_lean_prompt(
        self,
        options: List[BoundedOption],
        context: Dict,
        profile_key: str = 'default',
        profile: OptionProfile = None,
        emotional_shift: Optional[EmotionalShift] = None,
    ) -> str:
        """Build minimal prompt: just cards, situation, and numbered options.

        Board read is injected postflop for analytical profiles (board_read=True)
        unless the player is in an extreme tilted/shaken/dissociated state.
        """
        hole_cards = context.get('hole_cards', [])
        community_cards = context.get('community_cards', [])
        big_blind = context.get('big_blind', 100)
        phase = context.get('phase', 'PRE_FLOP')

        # Cards
        parts = [f"Cards: {' '.join(hole_cards)}"]
        if community_cards:
            parts[0] += f" | Board: {' '.join(community_cards)}"

        # Hand breakdown (postflop) or preflop classification
        if community_cards and hole_cards:
            try:
                hole_objs = [Card.from_short(c) for c in hole_cards]
                comm_objs = [Card.from_short(c) for c in community_cards]
                breakdown = narrate_hand_breakdown(hole_objs, comm_objs)
                if breakdown:
                    parts.append(breakdown)
            except Exception:
                pass
        elif hole_cards:
            preflop_str = classify_preflop_hand(hole_cards)
            if preflop_str:
                parts.append(f"Hand: {preflop_str}")

        # Build position map from table_positions (name → short label)
        position_map = {}
        position_short_label = ''
        try:
            game_state = self.state_machine.game_state
            if game_state and hasattr(game_state, 'table_positions'):
                for pos, name in game_state.table_positions.items():
                    short = get_position_abbrev(pos)
                    position_map[name] = short
                    if name == self.player_name:
                        position_short_label = short
        except (AttributeError, TypeError):
            pass

        # Street and situation in BB
        street_name = phase.replace('_', ' ').title() if phase else ''
        stack_bb = context.get('stack_bb', 0)
        pot_bb = context.get('pot_total', 0) / big_blind if big_blind > 0 else 0
        pos_segment = f" | Position: {position_short_label}" if position_short_label else ''
        parts.append(
            f"Street: {street_name}{pos_segment} | Stack: {stack_bb:.0f} BB | Pot: {pot_bb:.1f} BB"
        )

        # Betting action this street
        action_summary = self._build_street_action_summary(
            phase, big_blind, position_map=position_map
        )
        if action_summary:
            parts.append(f"Action: {action_summary}")

        # Facing context (raise escalation awareness)
        raises_this_round = context.get('raises_this_round', 0)
        if raises_this_round >= 1 and context.get('cost_to_call', 0) > 0:
            if raises_this_round == 1:
                parts.append("Facing: a raise")
            elif raises_this_round == 2:
                parts.append("Facing: a 3-bet (re-raise)")
            else:
                parts.append("Facing: a 4-bet+ (heavy action)")

        # Heads-up context
        num_opponents = context.get('num_opponents', 2)
        if num_opponents <= 1:
            parts.append("Heads-up (1v1). Widen your ranges and apply pressure.")

        # Board read injection (postflop, analytical profiles only)
        if profile and profile.board_read and community_cards:
            # Suppress in extreme tilted/shaken/dissociated states
            suppress = False
            if emotional_shift and emotional_shift.severity == 'extreme':
                if emotional_shift.state in ('tilted', 'shaken', 'dissociated'):
                    suppress = True
            if not suppress:
                board_read_line = build_board_read(community_cards)
                if board_read_line:
                    parts.append(board_read_line)

        # Style hint
        style_hint = profile.style_hint if profile else ''
        if style_hint:
            parts.append(style_hint)

        parts.append("")

        # Numbered options
        # Resolve EV visibility: PromptConfig override > profile default
        show_ev_override = getattr(self.prompt_config, 'show_ev_labels', None)
        show_ev = (
            show_ev_override
            if show_ev_override is not None
            else (profile.show_ev_labels if profile else True)
        )
        use_nudges = getattr(self.prompt_config, 'composed_nudges', False)

        for i, opt in enumerate(options, 1):
            action_str = opt.action.upper()
            if opt.action == 'raise' and opt.raise_to > 0:
                raise_bb = opt.raise_to / big_blind if big_blind > 0 else opt.raise_to
                action_str += f" to {raise_bb:.1f}BB"
            ev_part = f"  [{opt.ev_estimate}]" if show_ev else ""
            if use_nudges:
                parts.append(f"{i}. {action_str}{ev_part} — {opt.rationale}")
            else:
                parts.append(f"{i}. {action_str}{ev_part}  {opt.rationale}")

        parts.append("")
        parts.append(f'Respond with JSON: {{"reasoning": "...", "choice": N}} (1-{len(options)})')

        return "\n".join(parts)
