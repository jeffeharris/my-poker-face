"""Poker coaching engine.

Pre-computes all coaching statistics from the current game state
for the human player: equity, pot odds, hand strength, outs,
opponent stats, and an optimal action recommendation.

Also provides `compute_coaching_data_with_progression()` which
enriches coaching data with skill-aware progression context.
"""

import logging
from typing import Any, Dict, List, Optional

from poker.hand_evaluator import HandEvaluator
from poker.hand_ranges import OpponentInfo
from poker.decision_analyzer import DecisionAnalyzer
from poker.controllers import classify_preflop_hand
from poker.card_utils import card_to_string

from ..services import game_state_service
from .skill_definitions import ALL_SKILLS

logger = logging.getLogger(__name__)

_decision_analyzer = DecisionAnalyzer(iterations=2000)


def _get_position_label(game_state, player_idx: int) -> str:
    """Get position label for a player."""
    positions = game_state.table_positions
    player_name = game_state.players[player_idx].name
    for position, name in positions.items():
        if name == player_name:
            return position.replace('_', ' ').title()
    return "Unknown"


def _compute_equity(player_hand: List[str], community: List[str],
                    opponent_infos: Optional[List] = None) -> Optional[float]:
    """Compute player equity against opponent ranges via DecisionAnalyzer.

    Uses opponent stats/ranges when available, falls back to vs-random.
    Returns equity as a float in [0, 1], or None on failure.
    """
    if not player_hand:
        return None

    try:
        if opponent_infos:
            equity = _decision_analyzer.calculate_equity_vs_ranges(
                player_hand, community, opponent_infos
            )
            if equity is not None:
                return equity
            logger.warning("Equity vs ranges failed, falling back to vs random")

        # Fallback: vs random hands
        num_opponents = len(opponent_infos) if opponent_infos else 1
        equity = _decision_analyzer.calculate_equity_vs_random(
            player_hand, community, num_opponents
        )
        if equity is not None:
            return equity

        logger.error("Both equity calculations (ranges + random) failed")
        return None
    except Exception as e:
        logger.error(f"Equity calculation failed: {e}")
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

        if len(board_cards) >= 3:
            # Evaluate current hand with available cards
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
                    remaining = [c for c in deck if c != card]
                    test_score = eval7.evaluate(hero_cards + test_board + remaining[:5 - len(test_board)])
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
            hand_strs = [card_to_string(c) for c in player_hand_cards]
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


def _get_raw_position(game_state, player_idx: int) -> str:
    """Get the raw position key (e.g. 'small_blind_player') for hand_ranges lookup."""
    positions = game_state.table_positions
    player_name = game_state.players[player_idx].name
    for position, name in positions.items():
        if name == player_name:
            return position
    return "unknown"


def _build_opponent_infos(game_data: dict, game_state, human_name: str) -> List[OpponentInfo]:
    """Build OpponentInfo objects for active opponents (for range-based equity)."""
    infos = []
    memory_manager = game_data.get('memory_manager')
    omm = getattr(memory_manager, 'opponent_model_manager', None) if memory_manager else None

    for i, player in enumerate(game_state.players):
        if player.name == human_name or player.is_folded:
            continue

        position = _get_raw_position(game_state, i)
        info = OpponentInfo(name=player.name, position=position)

        if omm and human_name in omm.models and player.name in omm.models[human_name]:
            model = omm.models[human_name][player.name]
            t = model.tendencies
            info.hands_observed = t.hands_observed
            info.vpip = t.vpip
            info.pfr = t.pfr
            info.aggression = t.aggression_factor

        infos.append(info)
    return infos


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


def _get_current_hand_actions(game_data: dict) -> List[Dict]:
    """Extract actions from the current in-progress hand."""
    memory_manager = game_data.get('memory_manager')
    if not memory_manager:
        return []
    recorder = getattr(memory_manager, 'hand_recorder', None)
    if not recorder or not recorder.current_hand:
        return []
    return [a.to_dict() for a in recorder.current_hand.actions]


def compute_coaching_data(game_id: str, player_name: str,
                          game_data: Optional[Dict] = None,
                          game_state_override=None) -> Optional[Dict]:
    """Compute all coaching statistics for the given player.

    Returns a dict with equity, pot odds, hand strength, outs,
    recommendation, opponent stats, etc. Returns None if game not found.
    """
    if game_data is None:
        game_data = game_state_service.get_game(game_id)
    if not game_data:
        return None

    state_machine = game_data['state_machine']
    game_state = game_state_override if game_state_override is not None else state_machine.game_state

    # Find the human player
    player_info = game_state.get_player_by_name(player_name)
    if not player_info:
        return None

    player, player_idx = player_info

    # Basic game info
    pot_total = game_state.pot.get('total', 0)
    cost_to_call = max(0, game_state.highest_bet - player.bet)
    phase = state_machine.phase.name
    position = _get_position_label(game_state, player_idx)

    community_cards = list(game_state.community_cards) if game_state.community_cards else []
    player_hand = list(player.hand) if player.hand else []

    # Convert cards to string format for calculations
    hand_strs = [card_to_string(c) for c in player_hand]
    community_strs = [card_to_string(c) for c in community_cards]

    result: Dict[str, Any] = {
        'phase': phase,
        'position': position,
        'pot_total': pot_total,
        'cost_to_call': cost_to_call,
        'big_blind': game_state.current_ante,
        'stack': player.stack,
        'equity': None,
        'equity_vs_random': None,
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

    # Equity calculations
    opponent_infos = _build_opponent_infos(game_data, game_state, player_name)
    num_opponents = len(opponent_infos) or 1

    # Primary: equity vs opponent ranges (used for coaching guidance)
    equity = _compute_equity(hand_strs, community_strs, opponent_infos=opponent_infos)
    result['equity'] = round(equity, 3) if equity is not None else None

    # Secondary: equity vs random hands (baseline reference)
    # Only calculate separately when primary equity used opponent ranges;
    # if no ranges were available, _compute_equity already fell back to vs-random.
    if opponent_infos and equity is not None:
        equity_random = _decision_analyzer.calculate_equity_vs_random(
            hand_strs, community_strs, num_opponents
        )
        result['equity_vs_random'] = round(equity_random, 3) if equity_random is not None else None
    elif equity is not None:
        # Primary equity was already vs-random — reuse it
        result['equity_vs_random'] = result['equity']

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
            recommendation = _decision_analyzer.determine_optimal_action(
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

    # Current hand action timeline
    result['hand_actions'] = _get_current_hand_actions(game_data)
    result['hand_community_cards'] = community_strs

    # Player name for multi-street context filtering
    result['player_name'] = player_name

    return result


def compute_coaching_data_with_progression(
    game_id: str,
    player_name: str,
    user_id: str,
    game_data: Optional[Dict] = None,
    coach_repo=None,
) -> Optional[Dict]:
    """Compute coaching data enriched with skill progression context.

    Wraps compute_coaching_data() and adds classification, coaching
    decision, and skill states from the progression system.
    """
    data = compute_coaching_data(game_id, player_name, game_data=game_data)
    if data is None:
        return None

    if not user_id or not coach_repo:
        return data

    try:
        from .coach_progression import CoachProgressionService

        service = CoachProgressionService(coach_repo)
        player_state = service.get_or_initialize_player(user_id)

        skill_states = player_state['skill_states']
        gate_progress = player_state['gate_progress']

        # Get coaching decision
        decision = service.get_coaching_decision(
            user_id, data, skill_states, gate_progress
        )

        # Attach progression context to coaching data
        data['progression'] = {
            'coaching_mode': decision.mode.value,
            'primary_skill': decision.primary_skill_id,
            'relevant_skills': decision.relevant_skill_ids,
            'coaching_prompt': decision.coaching_prompt,
            'situation_tags': decision.situation_tags,
            'skill_states': {
                sid: {
                    'state': ss.state.value,
                    'window_accuracy': round(ss.window_accuracy, 2),
                    'total_opportunities': ss.total_opportunities,
                    'name': ALL_SKILLS[sid].name if sid in ALL_SKILLS else sid,
                    'description': ALL_SKILLS[sid].description if sid in ALL_SKILLS else '',
                    'gate': ALL_SKILLS[sid].gate if sid in ALL_SKILLS else 0,
                }
                for sid, ss in skill_states.items()
            },
        }
    except Exception as e:
        logger.error(f"Coach progression enrichment failed: {e}", exc_info=True)

    return data
