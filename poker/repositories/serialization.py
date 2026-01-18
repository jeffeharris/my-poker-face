"""
Serialization helpers for cards and game state.
"""
import json
from typing import Dict, Any, List, Optional, Tuple
from core.card import Card
from poker.poker_game import PokerGameState, Player
from poker.poker_state_machine import PokerStateMachine, PokerPhase


def serialize_card(card) -> Dict[str, Any]:
    """Serialize a card to a dictionary."""
    if hasattr(card, 'to_dict'):
        return card.to_dict()
    elif isinstance(card, dict):
        if 'rank' in card and 'suit' in card:
            return card
        raise ValueError(f"Invalid card dict: missing rank or suit in {card}")
    raise ValueError(f"Unknown card format: {type(card)}")


def deserialize_card(card_data) -> Card:
    """Deserialize a card from a dictionary."""
    if isinstance(card_data, dict):
        return Card.from_dict(card_data)
    elif hasattr(card_data, 'rank'):
        return card_data
    raise ValueError(f"Cannot deserialize card: {card_data}")


def serialize_cards(cards) -> List[Dict[str, Any]]:
    """Serialize a collection of cards."""
    if not cards:
        return []
    return [serialize_card(card) for card in cards]


def deserialize_cards(cards_data) -> Tuple[Card, ...]:
    """Deserialize a collection of cards to a tuple."""
    if not cards_data:
        return tuple()
    return tuple(deserialize_card(card_data) for card_data in cards_data)


def serialize_player(player: Player) -> Dict[str, Any]:
    """Serialize a player to a dictionary."""
    return {
        'name': player.name,
        'chips': player.chips,
        'hole_cards': serialize_cards(player.hole_cards),
        'current_bet': player.current_bet,
        'is_all_in': player.is_all_in,
        'has_folded': player.has_folded,
        'is_human': player.is_human,
        'seat_position': player.seat_position,
    }


def deserialize_player(player_data: Dict[str, Any]) -> Player:
    """Deserialize a player from a dictionary."""
    return Player(
        name=player_data['name'],
        chips=player_data['chips'],
        hole_cards=deserialize_cards(player_data.get('hole_cards', [])),
        current_bet=player_data.get('current_bet', 0),
        is_all_in=player_data.get('is_all_in', False),
        has_folded=player_data.get('has_folded', False),
        is_human=player_data.get('is_human', False),
        seat_position=player_data.get('seat_position'),
    )


def serialize_game_state(game_state: PokerGameState) -> Dict[str, Any]:
    """Serialize a PokerGameState to a dictionary."""
    return {
        'players': [serialize_player(p) for p in game_state.players],
        'community_cards': serialize_cards(game_state.community_cards),
        'pot': dict(game_state.pot),
        'current_bet': game_state.current_bet,
        'dealer_index': game_state.dealer_index,
        'current_player_index': game_state.current_player_index,
        'small_blind': game_state.small_blind,
        'big_blind': game_state.big_blind,
        'hand_number': game_state.hand_number,
        'deck': serialize_cards(game_state.deck),
        'last_action': game_state.last_action,
        'round_start_index': game_state.round_start_index,
        'starting_stacks': dict(game_state.starting_stacks) if game_state.starting_stacks else {},
        'last_aggressor': game_state.last_aggressor,
        'last_raise_amount': game_state.last_raise_amount,
    }


def deserialize_game_state(state_dict: Dict[str, Any]) -> PokerGameState:
    """Deserialize a PokerGameState from a dictionary."""
    players = tuple(deserialize_player(p) for p in state_dict.get('players', []))
    community_cards = deserialize_cards(state_dict.get('community_cards', []))
    deck = deserialize_cards(state_dict.get('deck', []))

    pot_data = state_dict.get('pot', {})
    if isinstance(pot_data, dict):
        pot = dict(pot_data)
    else:
        pot = {'total': 0, 'main': 0}

    starting_stacks = state_dict.get('starting_stacks', {})
    if starting_stacks is None:
        starting_stacks = {}

    return PokerGameState(
        players=players,
        community_cards=community_cards,
        pot=pot,
        current_bet=state_dict.get('current_bet', 0),
        dealer_index=state_dict.get('dealer_index', 0),
        current_player_index=state_dict.get('current_player_index', 0),
        small_blind=state_dict.get('small_blind', 50),
        big_blind=state_dict.get('big_blind', 100),
        hand_number=state_dict.get('hand_number', 1),
        deck=deck,
        last_action=state_dict.get('last_action'),
        round_start_index=state_dict.get('round_start_index', 0),
        starting_stacks=starting_stacks,
        last_aggressor=state_dict.get('last_aggressor'),
        last_raise_amount=state_dict.get('last_raise_amount', 0),
    )


def serialize_state_machine(
    state_machine: PokerStateMachine, phase: Optional[PokerPhase] = None
) -> Dict[str, Any]:
    """Serialize a PokerStateMachine to a dictionary."""
    result = serialize_game_state(state_machine.game_state)
    result['current_phase'] = (phase or state_machine.phase).value
    return result


def deserialize_state_machine(state_dict: Dict[str, Any]) -> PokerStateMachine:
    """Deserialize a PokerStateMachine from a dictionary."""
    game_state = deserialize_game_state(state_dict)
    state_machine = PokerStateMachine(game_state)

    # Restore phase
    phase_value = state_dict.get('current_phase', 0)
    if isinstance(phase_value, str):
        phase_value = int(phase_value)
    phase = PokerPhase(phase_value)
    if phase != PokerPhase.INITIALIZING_GAME:
        state_machine = state_machine.with_phase(phase)

    return state_machine


def to_json(data: Any) -> str:
    """Convert data to JSON string."""
    return json.dumps(data, default=str)


def from_json(json_str: str) -> Any:
    """Parse JSON string to data."""
    if not json_str:
        return None
    return json.loads(json_str)
