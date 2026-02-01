"""Serialization utilities for game state persistence.

Pure functions for converting game objects to/from dicts suitable for
JSON storage. Extracted from GamePersistence as part of the persistence
refactor (T3-35).
"""
import logging
from typing import Dict, Any, List

from poker.poker_game import PokerGameState, Player
from core.card import Card

logger = logging.getLogger(__name__)


def serialize_card(card) -> Dict[str, Any]:
    """Ensure card is properly serialized to a dict."""
    if hasattr(card, 'to_dict'):
        return card.to_dict()
    elif isinstance(card, dict):
        if 'rank' in card and 'suit' in card:
            return card
        else:
            raise ValueError(f"Invalid card dict: missing rank or suit in {card}")
    else:
        raise ValueError(f"Unknown card format: {type(card)}")


def deserialize_card(card_data) -> Card:
    """Ensure card is properly deserialized to Card object."""
    if isinstance(card_data, dict):
        return Card.from_dict(card_data)
    elif hasattr(card_data, 'rank'):  # Already a Card object
        return card_data
    else:
        raise ValueError(f"Cannot deserialize card: {card_data}")


def serialize_cards(cards) -> List[Dict[str, Any]]:
    """Serialize a collection of cards."""
    if not cards:
        return []
    return [serialize_card(card) for card in cards]


def deserialize_cards(cards_data) -> tuple:
    """Deserialize a collection of cards."""
    if not cards_data:
        return tuple()
    return tuple(deserialize_card(card_data) for card_data in cards_data)


def prepare_state_for_save(game_state: PokerGameState) -> Dict[str, Any]:
    """Prepare game state for JSON serialization."""
    state_dict = game_state.to_dict()
    # The to_dict() method already handles most serialization,
    # but we need to ensure all custom objects are properly converted
    return state_dict


def restore_state_from_dict(state_dict: Dict[str, Any]) -> PokerGameState:
    """Restore game state from dictionary."""
    # Reconstruct players
    players = []
    for player_data in state_dict['players']:
        # Reconstruct hand if present
        hand = None
        if player_data.get('hand'):
            try:
                hand = deserialize_cards(player_data['hand'])
            except Exception as e:
                logger.warning(f"Error deserializing hand for {player_data['name']}: {e}")
                hand = None

        player = Player(
            name=player_data['name'],
            stack=player_data['stack'],
            is_human=player_data['is_human'],
            bet=player_data['bet'],
            hand=hand,
            is_all_in=player_data['is_all_in'],
            is_folded=player_data['is_folded'],
            has_acted=player_data['has_acted'],
            last_action=player_data.get('last_action')
        )
        players.append(player)

    # Reconstruct deck
    try:
        deck = deserialize_cards(state_dict.get('deck', []))
    except Exception as e:
        logger.warning(f"Error deserializing deck: {e}")
        deck = tuple()

    # Reconstruct discard pile
    try:
        discard_pile = deserialize_cards(state_dict.get('discard_pile', []))
    except Exception as e:
        logger.warning(f"Error deserializing discard pile: {e}")
        discard_pile = tuple()

    # Reconstruct community cards
    try:
        community_cards = deserialize_cards(state_dict.get('community_cards', []))
    except Exception as e:
        logger.warning(f"Error deserializing community cards: {e}")
        community_cards = tuple()

    # Create the game state
    return PokerGameState(
        players=tuple(players),
        deck=deck,
        discard_pile=discard_pile,
        pot=state_dict['pot'],
        current_player_idx=state_dict['current_player_idx'],
        current_dealer_idx=state_dict['current_dealer_idx'],
        community_cards=community_cards,
        current_ante=state_dict['current_ante'],
        pre_flop_action_taken=state_dict['pre_flop_action_taken'],
        awaiting_action=state_dict['awaiting_action'],
        run_it_out=state_dict.get('run_it_out', False)
    )
