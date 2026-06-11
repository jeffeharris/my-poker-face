"""Serialization utilities for game state persistence.

Pure functions for converting game objects to/from dicts suitable for
JSON storage.
"""

import logging
from typing import Any, Dict, List

from core.card import Card
from poker.poker_game import Player, PokerGameState
from poker.table.seat import seat_id_from_dict

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
            # Tournament seats key `name` on the raw field id and carry the
            # friendly label here; without this, cold-load reverts it to None
            # and the felt shows the raw id. None for cash (name is friendly).
            nickname=player_data.get('nickname'),
            # Stable persona identity (T3-80). Round-trips like nickname; .get so
            # pre-migration saved games (no key) restore as None.
            personality_id=player_data.get('personality_id'),
            # Canonical typed seat identity (T3-80). Round-trips via SeatId; .get
            # so pre-migration saves (no key) restore as None.
            seat_id=seat_id_from_dict(player_data.get('seat_id')),
            stack=player_data['stack'],
            is_human=player_data['is_human'],
            bet=player_data['bet'],
            hand=hand,
            is_all_in=player_data['is_all_in'],
            is_folded=player_data['is_folded'],
            has_acted=player_data['has_acted'],
            last_action=player_data.get('last_action'),
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

    # Older saves predate these fields; fall back to safe defaults so loads
    # don't crash and don't silently revert to dataclass defaults that don't
    # match the table's blinds (e.g. last_raise_amount → ANTE=50 at a $2 table).
    current_ante = state_dict['current_ante']
    last_raise_amount = state_dict.get('last_raise_amount', current_ante)

    return PokerGameState(
        players=tuple(players),
        deck=deck,
        discard_pile=discard_pile,
        pot=state_dict['pot'],
        current_player_idx=state_dict['current_player_idx'],
        current_dealer_idx=state_dict['current_dealer_idx'],
        community_cards=community_cards,
        current_ante=current_ante,
        last_raise_amount=last_raise_amount,
        raises_this_round=state_dict.get('raises_this_round', 0),
        preflop_raise_count=state_dict.get('preflop_raise_count', 0),
        preflop_opener_idx=state_dict.get('preflop_opener_idx', -1),
        pre_flop_action_taken=state_dict['pre_flop_action_taken'],
        awaiting_action=state_dict['awaiting_action'],
        run_it_out=state_dict.get('run_it_out', False),
        has_revealed_cards=state_dict.get('has_revealed_cards', False),
        newly_dealt_count=state_dict.get('newly_dealt_count', 0),
    )
