"""
Minimal Poker Prompt System.

Provides a stripped-down prompt format for AI poker decisions that contains
only essential game state information, normalized to big blinds (BB).

This is designed to:
1. Test pure model poker ability without personality/psychology overhead
2. Provide a baseline for A/B testing prompt additions
3. Be easily parseable with a simple JSON response format
"""
import json
import re
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

from poker.card_utils import card_to_string

logger = logging.getLogger(__name__)


# Position abbreviation mapping (internal name -> standard poker abbreviation)
POSITION_ABBREV = {
    "button": "BTN",
    "small_blind_player": "SB",
    "big_blind_player": "BB",
    "under_the_gun": "UTG",
    "under_the_gun_1": "UTG+1",
    "under_the_gun_2": "UTG+2",
    "middle_position": "MP",
    "middle_position_1": "MP",
    "middle_position_2": "MP+1",
    "middle_position_3": "MP+2",
    "hijack": "HJ",
    "cutoff": "CO",
}

# Street name mapping
STREET_NAMES = {
    "PRE_FLOP": "Pre-flop",
    "FLOP": "Flop",
    "TURN": "Turn",
    "RIVER": "River",
}


def get_position_abbrev(position_name: str) -> str:
    """Convert internal position name to standard poker abbreviation."""
    return POSITION_ABBREV.get(position_name, position_name.upper())


def to_bb(amount: int, big_blind: int) -> float:
    """Convert chip amount to big blinds."""
    if big_blind <= 0:
        return float(amount)
    return round(amount / big_blind, 1)


def format_cards(cards) -> str:
    """Format a list of cards to standard notation."""
    if not cards:
        return "none"
    return " ".join(card_to_string(c) for c in cards)


@dataclass
class MinimalGameState:
    """Extracted game state for minimal prompt generation."""
    # Player info
    hole_cards: List[str]
    position: str
    stack_bb: float

    # Board state
    community_cards: List[str]
    street: str

    # Pot and betting
    pot_bb: float
    to_call_bb: float
    min_raise_to_bb: float
    max_raise_to_bb: float

    # Action history this round
    actions_this_round: List[Dict[str, Any]]

    # Players behind (position, stack)
    players_behind: List[Tuple[str, float]]

    # Valid actions
    valid_actions: List[str]


def extract_minimal_state(game_state, player, phase: str) -> MinimalGameState:
    """
    Extract minimal game state from full game state.

    Args:
        game_state: PokerGameState object
        player: Current Player object
        phase: Current betting phase (PRE_FLOP, FLOP, TURN, RIVER)

    Returns:
        MinimalGameState with BB-normalized values
    """
    big_blind = game_state.current_ante

    # Get player's position
    table_positions = game_state.table_positions
    player_position = "?"
    for pos_name, pos_player in table_positions.items():
        if pos_player == player.name:
            player_position = get_position_abbrev(pos_name)
            break

    # Calculate betting info
    highest_bet = game_state.highest_bet if hasattr(game_state, 'highest_bet') else 0
    cost_to_call = min(highest_bet - player.bet, player.stack)

    # Min raise calculation: must be at least the size of the last raise
    last_raise = getattr(game_state, 'last_raise_amount', big_blind)
    min_raise_total = highest_bet + last_raise
    min_raise_to = max(min_raise_total, big_blind * 2)  # At least 2 BB

    # Max is all-in
    max_raise_to = player.stack + player.bet  # Total player could have in pot

    # Get valid actions
    valid_actions = game_state.current_player_options if hasattr(game_state, 'current_player_options') else []

    # Build action history for this round
    # Note: This needs to be passed in or derived from game messages
    actions_this_round = []  # Will be populated by caller

    # Find players who act after this player
    players_behind = []
    player_idx = game_state.current_player_idx
    num_players = len(game_state.players)

    # Walk through players after current player in betting order
    for i in range(1, num_players):
        next_idx = (player_idx + i) % num_players
        next_player = game_state.players[next_idx]

        # Skip folded or all-in players
        if next_player.is_folded or next_player.is_all_in:
            continue

        # Find their position
        next_pos = "?"
        for pos_name, pos_player in table_positions.items():
            if pos_player == next_player.name:
                next_pos = get_position_abbrev(pos_name)
                break

        players_behind.append((next_pos, to_bb(next_player.stack, big_blind)))

    return MinimalGameState(
        hole_cards=[card_to_string(c) for c in player.hand],
        position=player_position,
        stack_bb=to_bb(player.stack, big_blind),
        community_cards=[card_to_string(c) for c in game_state.community_cards],
        street=STREET_NAMES.get(phase, phase),
        pot_bb=to_bb(game_state.pot.get('total', 0), big_blind),
        to_call_bb=to_bb(cost_to_call, big_blind),
        min_raise_to_bb=to_bb(min_raise_to, big_blind),
        max_raise_to_bb=to_bb(max_raise_to, big_blind),
        actions_this_round=actions_this_round,
        players_behind=players_behind,
        valid_actions=valid_actions,
    )


def build_action_history_line(position: str, action: str, amount_bb: Optional[float] = None,
                               stack_bb: Optional[float] = None) -> str:
    """Build a single action history line."""
    line = f"- {position}: {action}"
    if amount_bb is not None and action in ('raise', 'bet', 'call', 'all-in'):
        if action == 'raise':
            line += f" to {amount_bb} BB"
        elif action == 'call':
            line += f" {amount_bb} BB"
        elif action == 'bet':
            line += f" {amount_bb} BB"
        elif action == 'all-in':
            line += f" ({amount_bb} BB)"
    if stack_bb is not None:
        line += f" ({stack_bb} BB behind)"
    return line


def render_minimal_prompt(state: MinimalGameState,
                          action_history_lines: Optional[List[str]] = None) -> str:
    """
    Render the minimal prompt from extracted game state.

    Args:
        state: MinimalGameState object
        action_history_lines: Pre-formatted action history lines (optional)

    Returns:
        Formatted prompt string
    """
    lines = ["You are playing No-Limit Texas Hold'em.", ""]

    # Hand and board
    lines.append(f"Hand: {' '.join(state.hole_cards)}")
    lines.append(f"Board: {' '.join(state.community_cards) if state.community_cards else 'none'}")
    lines.append(f"Street: {state.street}")
    lines.append("")

    # Position and stack
    lines.append(f"Position: {state.position}")
    lines.append(f"Stack: {state.stack_bb} BB")
    lines.append("")

    # Pot and betting
    lines.append(f"Pot: {state.pot_bb} BB")
    lines.append(f"To call: {state.to_call_bb} BB")
    lines.append(f"Min raise to: {state.min_raise_to_bb} BB")
    lines.append("")

    # Action history
    if action_history_lines:
        lines.append("Action this round:")
        lines.extend(action_history_lines)
        lines.append("")

    # Players behind
    if state.players_behind:
        behind_str = ", ".join(f"{pos} ({stack} BB)" for pos, stack in state.players_behind)
        lines.append(f"Players behind: {behind_str}")
    else:
        lines.append("Players behind: none")
    lines.append("")

    # Valid actions and response format
    lines.append("Respond in JSON. Valid actions:")

    if state.to_call_bb == 0:
        # Can check
        lines.append('{"action": "check"}')
        if 'raise' in state.valid_actions or 'all_in' in state.valid_actions:
            lines.append(f'{{"action": "raise", "raise_to": <{state.min_raise_to_bb}-{state.stack_bb}>}}')
    else:
        # Must call or fold
        lines.append('{"action": "fold"}')
        if 'call' in state.valid_actions:
            lines.append('{"action": "call"}')
        if 'raise' in state.valid_actions:
            lines.append(f'{{"action": "raise", "raise_to": <{state.min_raise_to_bb}-{state.max_raise_to_bb}>}}')
        if 'all_in' in state.valid_actions and 'raise' not in state.valid_actions:
            # All-in when can't raise (short stack)
            lines.append('{"action": "all-in"}')

    return "\n".join(lines)


def convert_game_to_minimal_prompt(game_state, player, phase: str,
                                    action_history: Optional[List[Dict]] = None) -> str:
    """
    Convert full game state to minimal prompt.

    This is the main entry point for the minimal prompt system.

    Args:
        game_state: PokerGameState object
        player: Current Player object
        phase: Current betting phase
        action_history: List of action dicts with keys: position, action, amount_bb, stack_bb

    Returns:
        Formatted minimal prompt string
    """
    state = extract_minimal_state(game_state, player, phase)

    # Format action history lines
    history_lines = None
    if action_history:
        history_lines = [
            build_action_history_line(
                a.get('position', '?'),
                a.get('action', '?'),
                a.get('amount_bb'),
                a.get('stack_bb')
            )
            for a in action_history
        ]

    return render_minimal_prompt(state, history_lines)


# Minimal response parsing

@dataclass
class MinimalResponse:
    """Parsed response from minimal prompt."""
    action: str
    raise_to: Optional[float] = None
    raw_response: Optional[str] = None
    parse_error: Optional[str] = None


def parse_minimal_response(response: str) -> MinimalResponse:
    """
    Parse AI response from minimal prompt format.

    Expects JSON like:
        {"action": "fold"}
        {"action": "call"}
        {"action": "check"}
        {"action": "raise", "raise_to": 9}
        {"action": "all-in"}

    Returns:
        MinimalResponse with parsed action and optional raise_to
    """
    # Strip markdown code blocks if present
    cleaned = re.sub(r'```json?\s*|\s*```', '', response).strip()

    # Try to extract JSON from response
    # Handle case where there's text before/after JSON
    json_match = re.search(r'\{[^{}]*\}', cleaned)
    if json_match:
        cleaned = json_match.group()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return MinimalResponse(
            action="fold",  # Safe fallback
            raw_response=response,
            parse_error=f"JSON parse error: {e}"
        )

    action = data.get('action', '').lower().strip()

    # Normalize action names
    action_map = {
        'all-in': 'all_in',
        'allin': 'all_in',
        'all in': 'all_in',
    }
    action = action_map.get(action, action)

    # Extract raise amount
    raise_to = data.get('raise_to')
    if raise_to is not None:
        try:
            raise_to = float(raise_to)
        except (ValueError, TypeError):
            raise_to = None

    return MinimalResponse(
        action=action,
        raise_to=raise_to,
        raw_response=response
    )


def convert_minimal_response_to_game_action(response: MinimalResponse,
                                             big_blind: int,
                                             current_bet: int) -> Dict[str, Any]:
    """
    Convert minimal response to game action format.

    The game expects:
        {"action": "fold|check|call|raise|all_in", "adding_to_pot": <amount>}

    Args:
        response: MinimalResponse from parse_minimal_response
        big_blind: Current big blind amount
        current_bet: Player's current bet this round

    Returns:
        Dict compatible with game action processing
    """
    result = {"action": response.action}

    if response.action == "raise" and response.raise_to is not None:
        # Convert raise_to (in BB) to adding_to_pot (in chips)
        # raise_to is the total bet amount, adding_to_pot is how much more to add
        total_bet_chips = int(response.raise_to * big_blind)
        adding = total_bet_chips - current_bet
        result["adding_to_pot"] = max(0, adding)

    return result
