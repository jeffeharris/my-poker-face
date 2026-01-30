"""Poker coaching engine.

Pre-computes all coaching statistics from the current game state
for the human player: equity, pot odds, hand strength, outs,
opponent stats, and an optimal action recommendation.
"""

import logging
from typing import Any, Dict, List, Optional

from poker.equity_calculator import EquityCalculator
from poker.hand_evaluator import HandEvaluator
from poker.decision_analyzer import DecisionAnalyzer
from poker.controllers import classify_preflop_hand
from poker.card_utils import normalize_card_string

from ..services import game_state_service

logger = logging.getLogger(__name__)

_equity_calc = EquityCalculator(monte_carlo_iterations=5000)
_decision_analyzer = DecisionAnalyzer(iterations=2000)


def _card_dict_to_str(card: Any) -> str:
    """Convert a card dict/object to a short string like 'As'."""
    if isinstance(card, str):
        return normalize_card_string(card)
    if isinstance(card, dict):
        rank = card.get('rank', '')
        suit = card.get('suit', '')
        if rank == '10':
            rank = 'T'
        suit_letter = suit[0].lower() if suit else ''
        return f"{rank}{suit_letter}"
    # Card object with rank/suit attributes
    rank = getattr(card, 'rank', '') or ''
    suit = getattr(card, 'suit', '') or ''
    if rank == '10':
        rank = 'T'
    suit_letter = suit[0].lower() if suit else ''
    return f"{rank}{suit_letter}"


def _get_phase_name(game_state) -> str:
    """Get the current phase name from the game state."""
    community = list(game_state.community_cards) if game_state.community_cards else []
    n = len(community)
    if n == 0:
        return "PRE_FLOP"
    elif n == 3:
        return "FLOP"
    elif n == 4:
        return "TURN"
    elif n >= 5:
        return "RIVER"
    return "UNKNOWN"


def _get_position_label(game_state, player_idx: int) -> str:
    """Get position label for a player."""
    positions = game_state.table_positions
    player_name = game_state.players[player_idx].name
    for position, name in positions.items():
        if name == player_name:
            return position.replace('_', ' ').title()
    return "Unknown"


def _compute_equity(player_hand: List[str], community: List[str]) -> Optional[float]:
    """Compute player equity via Monte Carlo simulation."""
    try:
        # We need at least one opponent range to calculate against.
        # Use a generic "random hand" opponent.
        result = _equity_calc.calculate_equity(
            players_hands={'hero': player_hand, 'villain': []},
            board=community,
        )
        # The above won't work with empty villain hand. Use single-player
        # equity against a random hand via Monte Carlo directly.
        if result and 'hero' in result.equities:
            return result.equities['hero']
    except Exception:
        pass

    # Fallback: calculate equity with hero hand vs random opponent
    try:
        import eval7
        import random

        hero_cards = [eval7.Card(c) for c in player_hand]
        board_cards = [eval7.Card(c) for c in community]
        known = set(hero_cards + board_cards)
        deck = [c for c in eval7.Deck().cards if c not in known]
        cards_needed = 5 - len(board_cards)

        wins = 0
        iterations = 5000
        for _ in range(iterations):
            sampled = random.sample(deck, cards_needed + 2)  # board fill + 2 villain cards
            full_board = board_cards + sampled[:cards_needed]
            villain_hand = sampled[cards_needed:cards_needed + 2]

            hero_score = eval7.evaluate(hero_cards + full_board)
            villain_score = eval7.evaluate(villain_hand + full_board)

            if hero_score > villain_score:
                wins += 1
            elif hero_score == villain_score:
                wins += 0.5

        return wins / iterations
    except Exception as e:
        logger.warning(f"Equity calculation failed: {e}")
        return None


def _compute_outs(player_hand: List[str], community: List[str]) -> Optional[Dict]:
    """Count cards that improve the player's hand rank."""
    if not community:
        return None

    try:
        import eval7

        hero_cards = [eval7.Card(c) for c in player_hand]
        board_cards = [eval7.Card(c) for c in community]
        known = set(hero_cards + board_cards)
        deck = [c for c in eval7.Deck().cards if c not in known]

        current_score = eval7.evaluate(hero_cards + board_cards + [eval7.Card('2c')] * (5 - len(board_cards)))
        # Actually evaluate current best hand with available cards
        if len(board_cards) >= 3:
            # Pad board to 5 for evaluation if needed - but we want outs that improve
            # Evaluate current hand properly
            if len(board_cards) == 5:
                current_score = eval7.evaluate(hero_cards + board_cards)
            else:
                # For incomplete boards, evaluate current made hand
                # by considering the best among random completions
                current_score = eval7.evaluate(hero_cards + board_cards + deck[:5 - len(board_cards)])

            outs = []
            for card in deck:
                test_board = board_cards + [card]
                if len(test_board) < 5:
                    # Still need more cards - check if this card improves hand category
                    test_score = eval7.evaluate(hero_cards + test_board + deck[:5 - len(test_board)])
                else:
                    test_score = eval7.evaluate(hero_cards + test_board[:5])

                if test_score > current_score:
                    outs.append(str(card))

            return {
                'count': len(outs),
                'cards': outs[:15],  # Cap display at 15
            }
    except Exception as e:
        logger.warning(f"Outs calculation failed: {e}")

    return None


def _compute_hand_strength(player_hand_cards, community_cards) -> Optional[Dict]:
    """Evaluate current hand strength."""
    try:
        if not community_cards:
            # Pre-flop: use classify_preflop_hand
            hand_strs = [_card_dict_to_str(c) for c in player_hand_cards]
            classification = classify_preflop_hand(hand_strs)
            return {
                'description': classification or 'Unknown',
                'rank': None,
            }

        # Post-flop: use HandEvaluator
        all_cards = list(player_hand_cards) + list(community_cards)
        result = HandEvaluator(all_cards).evaluate_hand()
        return {
            'description': result.get('hand_name', 'Unknown'),
            'rank': result.get('hand_rank'),
        }
    except Exception as e:
        logger.warning(f"Hand strength evaluation failed: {e}")
        return None


def _get_opponent_stats(game_data: dict, human_name: str) -> List[Dict]:
    """Extract opponent stats from memory manager."""
    stats = []
    try:
        memory_manager = game_data.get('memory_manager')
        if not memory_manager:
            return stats

        omm = getattr(memory_manager, 'opponent_model_manager', None)
        if not omm:
            return stats

        game_state = game_data['state_machine'].game_state
        for player in game_state.players:
            if player.name == human_name or player.is_folded:
                continue

            # Get model from human's perspective
            if human_name in omm.models and player.name in omm.models[human_name]:
                model = omm.models[human_name][player.name]
                tendencies = model.tendencies
                stats.append({
                    'name': player.name,
                    'vpip': round(tendencies.vpip, 2),
                    'pfr': round(tendencies.pfr, 2),
                    'aggression': round(tendencies.aggression_factor, 1),
                    'style': tendencies.get_play_style_label(),
                    'hands_observed': tendencies.hands_observed,
                })
            else:
                stats.append({
                    'name': player.name,
                    'vpip': None,
                    'pfr': None,
                    'aggression': None,
                    'style': 'unknown',
                    'hands_observed': 0,
                })
    except Exception as e:
        logger.warning(f"Opponent stats extraction failed: {e}")

    return stats


def compute_coaching_data(game_id: str, player_name: str) -> Optional[Dict]:
    """Compute all coaching statistics for the given player.

    Returns a dict with equity, pot odds, hand strength, outs,
    recommendation, opponent stats, etc. Returns None if game not found.
    """
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return None

    game_state = game_data['state_machine'].game_state

    # Find the human player
    player_info = game_state.get_player_by_name(player_name)
    if not player_info:
        return None

    player, player_idx = player_info

    # Basic game info
    pot_total = game_state.pot.get('total', 0)
    cost_to_call = max(0, game_state.highest_bet - player.bet)
    phase = _get_phase_name(game_state)
    position = _get_position_label(game_state, player_idx)

    community_cards = list(game_state.community_cards) if game_state.community_cards else []
    player_hand = list(player.hand) if player.hand else []

    # Convert cards to string format for calculations
    hand_strs = [_card_dict_to_str(c) for c in player_hand]
    community_strs = [_card_dict_to_str(c) for c in community_cards]

    result: Dict[str, Any] = {
        'phase': phase,
        'position': position,
        'pot_total': pot_total,
        'cost_to_call': cost_to_call,
        'stack': player.stack,
        'equity': None,
        'pot_odds': None,
        'required_equity': None,
        'is_positive_ev': None,
        'ev_call': None,
        'hand_strength': None,
        'hand_rank': None,
        'outs': None,
        'outs_cards': None,
        'recommendation': None,
        'opponent_stats': [],
    }

    # Equity calculation
    equity = _compute_equity(hand_strs, community_strs)
    result['equity'] = round(equity, 3) if equity is not None else None

    # Pot odds
    if cost_to_call > 0:
        result['pot_odds'] = round(pot_total / cost_to_call, 1)
        result['required_equity'] = round(cost_to_call / (pot_total + cost_to_call), 3)
    else:
        result['pot_odds'] = None
        result['required_equity'] = 0.0

    # EV calculation
    if equity is not None and cost_to_call > 0:
        # max winnable = pot + cost_to_call (simplified)
        max_winnable = pot_total + cost_to_call
        ev_call = (equity * max_winnable) - ((1 - equity) * cost_to_call)
        result['ev_call'] = round(ev_call, 1)
        result['is_positive_ev'] = ev_call > 0
    elif equity is not None and cost_to_call == 0:
        result['is_positive_ev'] = True
        result['ev_call'] = 0.0

    # Hand strength
    hand_info = _compute_hand_strength(player_hand, community_cards)
    if hand_info:
        result['hand_strength'] = hand_info['description']
        result['hand_rank'] = hand_info['rank']

    # Outs (only post-flop, pre-river)
    if community_strs and len(community_strs) < 5:
        outs_info = _compute_outs(hand_strs, community_strs)
        if outs_info:
            result['outs'] = outs_info['count']
            result['outs_cards'] = outs_info['cards']

    # Optimal action recommendation
    if equity is not None:
        num_opponents = len([p for p in game_state.players if not p.is_folded and p.name != player_name])
        required_equity = result['required_equity'] or 0.0
        ev_call = result['ev_call'] or 0.0

        try:
            recommendation = _decision_analyzer._determine_optimal_action(
                equity=equity,
                ev_call=ev_call,
                required_equity=required_equity,
                num_opponents=num_opponents,
                phase=phase,
                pot_total=pot_total,
                cost_to_call=cost_to_call,
                player_stack=player.stack,
            )
            result['recommendation'] = recommendation
        except Exception as e:
            logger.warning(f"Recommendation calculation failed: {e}")

    # Opponent stats
    result['opponent_stats'] = _get_opponent_stats(game_data, player_name)

    return result
