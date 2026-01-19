"""Message handling functions for the poker game."""

import uuid
import logging
from datetime import datetime
from typing import Optional

from ..extensions import socketio, persistence
from ..services import game_state_service

logger = logging.getLogger(__name__)


def format_action_message(player_name: str, action: str, amount: int = 0,
                          highest_bet: int = 0) -> str:
    """Format a player action into a human-readable message.

    Args:
        player_name: The name of the player taking the action
        action: The action type (raise, bet, call, check, fold, all_in)
        amount: The "raise BY" amount (increment over the call)
        highest_bet: The current highest bet before this action

    Returns:
        Formatted message string
    """
    if action == 'raise':
        # amount is "raise BY", so total bet = highest_bet + amount
        raise_to_amount = highest_bet + amount
        return f"{player_name} raises to ${raise_to_amount}."
    elif action == 'bet':
        return f"{player_name} bets ${amount}."
    elif action == 'call':
        return f"{player_name} calls."
    elif action == 'check':
        return f"{player_name} checks."
    elif action == 'fold':
        return f"{player_name} folds."
    elif action == 'all_in':
        return f"{player_name} goes all-in!"
    else:
        return f"{player_name} chose to {action}."


def record_action_in_memory(game_data: dict, player_name: str, action: str,
                            amount: int, game_state, state_machine) -> None:
    """Record a player action in the memory manager if available.

    Args:
        game_data: The game data dictionary containing the memory_manager
        player_name: Name of the player who acted
        action: The action taken ('fold', 'check', 'call', 'raise', 'bet', 'all_in')
        amount: Amount added to pot
        game_state: Current game state (for pot total)
        state_machine: State machine (for current phase)
    """
    if 'memory_manager' not in game_data:
        return

    memory_manager = game_data['memory_manager']
    pot_total = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
    phase = (state_machine.current_phase.name
             if hasattr(state_machine.current_phase, 'name')
             else str(state_machine.current_phase))

    # Get active players for c-bet tracking
    active_players = [
        p.name for p in game_state.players
        if not p.is_folded
    ] if hasattr(game_state, 'players') else None

    memory_manager.on_action(
        player_name=player_name,
        action=action,
        amount=amount,
        phase=phase,
        pot_total=pot_total,
        active_players=active_players
    )


def send_message(game_id: str, sender: str, content: str, message_type: str,
                 sleep: Optional[int] = None, action: Optional[str] = None) -> None:
    """Send a message to the specified game chat.

    Args:
        game_id: The unique identifier for the game.
        sender: The sender's username or identifier.
        content: The message content.
        message_type: The type of the message ['ai', 'table', 'user'].
        sleep: Optional time to sleep after sending the message, in seconds.
        action: Optional action text to include with AI messages (e.g., "raised to $50").
    """
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return

    game_messages = game_data.get('messages', [])

    new_message = {
        "id": str(uuid.uuid4()),
        "sender": sender,
        "content": content,
        "timestamp": datetime.now().strftime("%H:%M %b %d %Y"),
        "message_type": message_type
    }

    # Include action for AI messages (shown in floating bubble)
    if action:
        new_message["action"] = action

    game_messages.append(new_message)

    # Update the messages in game data
    game_data['messages'] = game_messages
    game_state_service.set_game(game_id, game_data)

    # Save message to database
    persistence.save_message(game_id, message_type, f"{sender}: {content}")

    # Emit only the new message to reduce payload size
    socketio.emit('new_message', {'message': new_message}, to=game_id)

    if sleep:
        socketio.sleep(sleep)


def format_messages_for_api(messages: list) -> list:
    """Format messages for API response.

    Args:
        messages: List of message dictionaries from game data

    Returns:
        List of formatted message dictionaries for frontend
    """
    formatted = []
    for msg in messages:
        formatted.append({
            'id': str(msg.get('id', len(formatted))),
            'sender': msg.get('sender', 'System'),
            'message': msg.get('content', msg.get('message', '')),
            'timestamp': msg.get('timestamp', datetime.now().isoformat()),
            'type': msg.get('message_type', msg.get('type', 'system'))
        })
    return formatted
