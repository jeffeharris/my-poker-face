"""
Postflop classifier -- maps game state to a PostflopNode for strategy lookup.

Reads the live PokerGameState to determine street, position (IP/OOP),
board texture, hand classification, facing action, and SPR bucket.
"""

from typing import List

from poker.board_analyzer import classify_texture_bucket
from poker.card_utils import card_to_string
from poker.strategy.hand_classification import classify_hand_full
from poker.strategy.nodes import PostflopNode
from poker.strategy.preflop_classifier import get_6max_position

# Postflop position ordering: lower index = more in-position (acts later).
# SB vs BB flips with table size: in 6-max the SB acts FIRST postflop (most
# OOP, after BB), but heads-up the button IS the SB and acts LAST (in position).
# get_6max_position labels the HU button 'SB', so we must rank SB ahead of BB
# only when heads-up — otherwise a BvB pot mis-selects the solver chart.
_POSITION_ORDER_6MAX = ['BTN', 'CO', 'HJ', 'UTG', 'BB', 'SB']
_POSITION_ORDER_HU = ['BTN', 'CO', 'HJ', 'UTG', 'SB', 'BB']


def _position_rank(pos: str, heads_up: bool = False) -> int:
    """Lower rank = more in-position. `heads_up` flips SB/BB (see above)."""
    order = _POSITION_ORDER_HU if heads_up else _POSITION_ORDER_6MAX
    try:
        return order.index(pos)
    except ValueError:
        return len(order)


def _find_preflop_raiser_idx(game_state) -> int:
    """Find the index of the preflop raiser (highest bet above BB).

    Returns -1 if no raiser found.
    """
    big_blind = game_state.current_ante
    best_idx = -1
    best_bet = big_blind

    for i, player in enumerate(game_state.players):
        if player.bet > best_bet or (player.bet == best_bet and best_idx == -1):
            # In postflop the bets are reset, so we use a heuristic:
            # during preflop the raiser had the highest bet.
            # By the time we're postflop, bets are reset. So we fall back
            # to position-based heuristic if we can't detect a raiser.
            pass
        if player.bet > best_bet:
            best_bet = player.bet
            best_idx = i

    return best_idx


def _determine_position(game_state, player_idx: int) -> str:
    """Determine if the player is IP (in-position) or OOP (out-of-position).

    The player closer to the button acts last and is IP.
    """
    player_pos = get_6max_position(game_state, player_idx)

    # Find the other active (non-folded) players
    active_positions = []
    for i, p in enumerate(game_state.players):
        if i != player_idx and not p.is_folded:
            active_positions.append(get_6max_position(game_state, i))

    if not active_positions:
        return 'IP'

    # Heads-up if the button is also the small blind (get_6max_position labels
    # that player 'SB'). Flips the SB/BB postflop ordering.
    positions = getattr(game_state, 'table_positions', {}) or {}
    heads_up = positions.get('button') is not None and positions.get('button') == positions.get(
        'small_blind_player'
    )

    player_rank = _position_rank(player_pos, heads_up)

    # IP if this player has a lower rank (closer to BTN) than all opponents
    if all(player_rank < _position_rank(opp, heads_up) for opp in active_positions):
        return 'IP'
    return 'OOP'


def _determine_facing_action(game_state) -> str:
    """Determine what action the player is facing."""
    if game_state.raises_this_round == 0:
        return 'unopened'
    if game_state.raises_this_round == 1:
        return 'facing_bet'
    return 'facing_raise'


def _determine_pot_type(game_state) -> str:
    """Classify the pot as single-raised (SRP) or 3-bet+ (3BP) from the
    hand-scoped preflop raise count (survives street resets; see
    PokerGameState.preflop_raise_count). 0-1 raises = SRP (limp/open), 2+ =
    3BP. 4-bet+ collapses into 3BP — the node model is two-valued."""
    raises = getattr(game_state, 'preflop_raise_count', 0)
    return '3BP' if raises >= 2 else 'SRP'


def _determine_spr_bucket(game_state, player_idx: int) -> str:
    """Classify the stack-to-pot ratio into a bucket."""
    from poker.stack_utils import effective_stack_chips

    player = game_state.players[player_idx]
    # SPR must use the EFFECTIVE stack (min of hero / largest active opp) — the
    # most that can go in. Using hero's own stack overstates SPR when hero covers
    # a shorter opp and wrongly suppresses the low-SPR postflop_commit chart.
    effective_stack = effective_stack_chips(game_state, player)
    pot_total = game_state.pot.get('total', 0)

    if pot_total <= 0:
        return 'high'

    spr = effective_stack / pot_total
    if spr > 6:
        return 'high'
    if spr >= 2:
        return 'medium'
    return 'low'


def _cards_to_strings(cards) -> List[str]:
    """Convert game state card objects/dicts to card strings."""
    return [card_to_string(c) for c in cards]


def build_postflop_node(
    game_state,
    player_idx: int,
    hole_cards: List[str],
    community_cards: List[str],
) -> PostflopNode:
    """Build a PostflopNode from the live game state.

    Parameters
    ----------
    game_state : PokerGameState
    player_idx : index into game_state.players
    hole_cards : ['Ah', 'Kd'] — already converted to card strings
    community_cards : ['Ks', '7d', '2c'] — already converted to card strings
    """
    num_community = len(community_cards)
    street = {3: 'flop', 4: 'turn', 5: 'river'}.get(num_community, 'flop')

    position = _determine_position(game_state, player_idx)
    board_texture = classify_texture_bucket(community_cards)
    classification = classify_hand_full(hole_cards, community_cards)
    facing_action = _determine_facing_action(game_state)
    spr_bucket = _determine_spr_bucket(game_state, player_idx)

    return PostflopNode(
        street=street,
        position=position,
        pot_type=_determine_pot_type(game_state),
        board_texture=board_texture,
        made_tier=classification.made_tier,
        draw_modifier=classification.draw_modifier,
        facing_action=facing_action,
        spr_bucket=spr_bucket,
        nut_status=classification.nut_status,
        danger_flags=classification.danger_flags,
    )
