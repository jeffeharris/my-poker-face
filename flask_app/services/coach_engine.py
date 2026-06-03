"""Poker coaching engine.

Pre-computes all coaching statistics from the current game state
for the human player: equity, pot odds, hand strength, outs,
opponent stats, and an optimal action recommendation.

Also provides `compute_coaching_data_with_progression()` which
enriches coaching data with skill-aware progression context.
"""

import logging
from typing import Any, Dict, List, Optional

from poker.card_utils import card_to_string
from poker.controllers import (
    _get_preflop_lines,
    _get_street_lines,
    _parse_game_messages,
    _process_preflop_lines,
    classify_preflop_hand,
)
from poker.decision_analyzer import DecisionAnalyzer
from poker.hand_evaluator import HandEvaluator
from poker.hand_ranges import OpponentInfo

from ..extensions import game_repo
from ..services import game_state_service
from .coach_progression import CoachProgressionService
from .skill_definitions import ALL_SKILLS

logger = logging.getLogger(__name__)

_decision_analyzer = DecisionAnalyzer(iterations=2000)


def _get_position_label(game_state, player_idx: int) -> str:
    """Get position label for a player.

    Drops the trailing '_player' on the blind keys so the blinds read as
    'Small Blind' / 'Big Blind' (matching _position_label_to_key's mapping)
    rather than 'Small Blind Player' / 'Big Blind Player'.
    """
    positions = game_state.table_positions
    player_name = game_state.players[player_idx].name
    for position, name in positions.items():
        if name == player_name:
            return position.replace('_player', '').replace('_', ' ').title()
    return "Unknown"


def _compute_equity(
    player_hand: List[str], community: List[str], opponent_infos: Optional[List] = None
) -> Optional[float]:
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
        logger.error(f"Equity calculation failed: {e}", exc_info=True)
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
                current_score = eval7.evaluate(
                    hero_cards + board_cards + deck[: 5 - len(board_cards)]
                )

            outs = []
            for card in deck:
                test_board = board_cards + [card]
                if len(test_board) < 5:
                    remaining = [c for c in deck if c != card]
                    test_score = eval7.evaluate(
                        hero_cards + test_board + remaining[: 5 - len(test_board)]
                    )
                else:
                    test_score = eval7.evaluate(hero_cards + test_board[:5])

                if test_score > current_score:
                    outs.append(str(card))

            return {
                'count': len(outs),
                'cards': outs[:15],  # Cap display at 15
            }
    except Exception as e:
        logger.warning(f"Outs calculation failed: {e}", exc_info=True)

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
        logger.warning(f"Hand strength evaluation failed: {e}", exc_info=True)
        return None


def _get_raw_position(game_state, player_idx: int) -> str:
    """Get the raw position key (e.g. 'small_blind_player') for hand_ranges lookup."""
    positions = game_state.table_positions
    player_name = game_state.players[player_idx].name
    for position, name in positions.items():
        if name == player_name:
            return position
    return "unknown"


def _position_label_to_key(position_label: str) -> str:
    """Convert display label to position key for range lookup.

    Examples:
        'Button' -> 'button'
        'Under The Gun' -> 'under_the_gun'
        'Small Blind' -> 'small_blind_player'

    Args:
        position_label: Human-readable position label from _get_position_label()

    Returns:
        Position key for use with hand_ranges module
    """
    mapping = {
        'Button': 'button',
        'Cutoff': 'cutoff',
        'Under The Gun': 'under_the_gun',
        'Small Blind': 'small_blind_player',
        'Big Blind': 'big_blind_player',
        'Middle Position 1': 'middle_position_1',
        'Middle Position 2': 'middle_position_2',
        'Middle Position 3': 'middle_position_3',
    }
    result = mapping.get(position_label)
    if result is None:
        logger.warning(
            f"Unknown position label '{position_label}', defaulting to 'button'", exc_info=True
        )
        return 'button'
    return result


def _extract_preflop_action(opponent_name: str, game_messages: list, game_state) -> Optional[str]:
    """
    Extract what preflop action an opponent took this hand.

    Args:
        opponent_name: Name of opponent to check
        game_messages: List of game message strings
        game_state: Current game state for position info

    Returns:
        Action string ('open_raise', 'call', '3bet', '4bet+', 'limp') or None
    """
    lines = _parse_game_messages(game_messages)
    if lines is None:
        return None

    # Get BB player name to ignore forced BB
    bb_player = game_state.table_positions.get('big_blind_player')
    opponent_lower = opponent_name.lower()

    # Filter to preflop lines only
    preflop_lines = _get_preflop_lines(lines)

    return _process_preflop_lines(preflop_lines, opponent_lower, opponent_name, bb_player)


def _extract_postflop_aggression(
    opponent_name: str, game_messages: list, current_phase: str
) -> Optional[str]:
    """
    Extract what postflop aggression an opponent showed this hand.

    Checks for betting/raising actions on the current street (flop/turn/river).
    Used to weight opponent hand sampling toward hands that connect with the board.

    Args:
        opponent_name: Name of opponent to check
        game_messages: List of game message strings
        current_phase: Current game phase ('FLOP', 'TURN', 'RIVER')

    Returns:
        Aggression type: 'bet', 'raise', 'check_call', 'check', or None
    """
    if current_phase == 'PRE_FLOP':
        return None

    lines = _parse_game_messages(game_messages)
    if lines is None:
        return None

    opponent_lower = opponent_name.lower()

    # Get lines from current street only
    street_lines = _get_street_lines(lines, current_phase)

    # Track opponent's most aggressive action on this street
    most_aggressive_action = None

    for line in street_lines:
        line_lower = line.lower()

        # Check if this line is about our opponent
        if opponent_lower not in line_lower:
            continue

        # Determine their action (most aggressive wins)
        if 'raise' in line_lower or ('all' in line_lower and 'in' in line_lower):
            most_aggressive_action = 'raise'  # Raise is most aggressive
        elif 'bet' in line_lower and most_aggressive_action != 'raise':
            most_aggressive_action = 'bet'
        elif 'call' in line_lower and most_aggressive_action not in ('raise', 'bet'):
            most_aggressive_action = 'check_call'
        elif 'check' in line_lower and most_aggressive_action is None:
            most_aggressive_action = 'check'

    return most_aggressive_action


def _load_cross_session_historical(human_name: str, user_id: Optional[str]) -> dict:
    """Cross-session opponent stats for `human_name`, or {} (no user / on error).

    Pulled out so `compute_coaching_data` can load it ONCE and pass it to both
    `_build_opponent_infos` and `_get_opponent_stats` instead of each hitting
    the DB independently per coaching tick.
    """
    if not user_id:
        return {}
    try:
        return game_repo.load_cross_session_opponent_models(human_name, user_id)
    except Exception as e:
        logger.warning(f"Failed to load cross-session opponent stats: {e}")
        return {}


def _build_opponent_infos(
    game_data: dict,
    game_state,
    human_name: str,
    user_id: Optional[str] = None,
    historical_data: Optional[dict] = None,
) -> List[OpponentInfo]:
    """Build OpponentInfo objects for active opponents (for range-based equity).

    Includes preflop action context for more accurate range narrowing.
    Uses cross-session historic stats when current session data is insufficient.

    Args:
        game_data: Current game data dict
        game_state: Current poker game state
        human_name: The human player's name (observer)
        user_id: Optional user ID for fetching cross-session historical data
    """
    from poker.hand_ranges import EquityConfig

    infos = []
    memory_manager = game_data.get('memory_manager')
    omm = getattr(memory_manager, 'opponent_model_manager', None) if memory_manager else None

    # Get game messages for action extraction
    game_messages = game_data.get('messages', [])

    # Get current phase for postflop aggression detection
    state_machine = game_data.get('state_machine')
    current_phase = state_machine.phase.name if state_machine else 'PRE_FLOP'

    # Cross-session historic stats (AI personalities are deterministic, so
    # historic stats are reliable). Caller may pass it pre-loaded; else fetch.
    if historical_data is None:
        historical_data = _load_cross_session_historical(human_name, user_id)

    min_hands = EquityConfig().min_hands_for_stats

    for i, player in enumerate(game_state.players):
        if player.name == human_name or player.is_folded:
            continue

        position = _get_raw_position(game_state, i)
        info = OpponentInfo(name=player.name, position=position)

        # Extract preflop action for range narrowing
        if game_messages:
            info.preflop_action = _extract_preflop_action(player.name, game_messages, game_state)

        # Extract postflop aggression for board-connection weighted sampling
        if game_messages and current_phase != 'PRE_FLOP':
            info.postflop_aggression_this_hand = _extract_postflop_aggression(
                player.name, game_messages, current_phase
            )

        # Add observed stats from opponent model (current session)
        current_hands = 0
        if omm and human_name in omm.models and player.name in omm.models[human_name]:
            model = omm.models[human_name][player.name]
            t = model.tendencies
            current_hands = t.hands_observed
            info.hands_observed = t.hands_observed
            info.vpip = t.vpip
            info.pfr = t.pfr
            info.aggression = t.aggression_factor

        # Use historic stats as fallback when current session has insufficient data
        # AI personalities have consistent behavior, so historic data is reliable
        if current_hands < min_hands and player.name in historical_data:
            hist = historical_data[player.name]
            if hist['total_hands'] >= min_hands:
                info.hands_observed = hist['total_hands']
                info.vpip = hist['vpip']
                info.pfr = hist['pfr']
                info.aggression = hist['aggression_factor']
                logger.debug(
                    f"Using historic stats for {player.name}: "
                    f"{hist['total_hands']} hands across {hist['session_count']} sessions"
                )

        infos.append(info)
    return infos


def _get_style_label(vpip: float, aggression: float) -> str:
    """Play style label for the cross-session `historical` block.

    The cross-session loader returns raw rate floats (not an
    `OpponentTendencies`), and the caller has already gated on a hand floor —
    so this is the shared quadrant mapping with no sample gate of its own.
    """
    from poker.archetypes import play_style_label

    return play_style_label(vpip, aggression)


# Detection-layer archetype labels → coach-friendly phrasing. The classifier
# only returns a label past its sample-size gate (≥15 hands), so a fresh table
# stays silent rather than guessing.
_ARCHETYPE_COACH_LABELS = {
    'pure_station': 'calling station (calls too much, rarely raises)',
    'hyper_aggressive': 'maniac (over-aggressive — bets/raises relentlessly)',
    'sticky_jammer': 'sticky jammer (calls light, then jams)',
}


def _classify_opp_archetype(tendencies) -> Optional[str]:
    """Coach-friendly opponent archetype from the tiered bots' detection layer.

    Reuses the exact classifier the AI uses to exploit opponents, so the coach's
    read matches the table's reality. Best-effort: returns None on any issue or
    below the classifier's sample gate.
    """
    try:
        from poker.memory.opponent_model import _build_aggregate_from_single
        from poker.strategy.exploitation import classify_opponent_archetype

        label = classify_opponent_archetype(_build_aggregate_from_single(tendencies))
        return _ARCHETYPE_COACH_LABELS.get(label) if label else None
    except Exception as e:
        logger.debug(f"_classify_opp_archetype failed: {e}")
        return None


def _opponent_deep_reads(tendencies, model, memory_manager, user_id):
    """Tier-2 postflop deep reads for one opponent, for the coaching prompt.

    Defaults to the live per-game tendency (always populated, and the only
    source in training mode, which runs without a sandbox). When this IS a
    sandbox game and a durable lifetime row has accumulated at least as many
    hands, prefer the cross-game reconstruction — more samples, same canonical
    rate definitions the dossier uses. Best-effort: any failure falls back to
    the per-game read.
    """
    from flask_app.services.opponent_reads import (
        deep_reads_from_tendencies,
        reconstruct_tendencies_from_lifetime,
    )

    read_tendencies = tendencies
    sandbox_id = getattr(memory_manager, 'sandbox_id', None)
    opponent_id = getattr(model, 'opponent_id', None)
    if sandbox_id and opponent_id and user_id:
        try:
            life = game_repo.load_observation_lifetime(sandbox_id, user_id, opponent_id)
            life_t = reconstruct_tendencies_from_lifetime(life)
            if life_t and life_t.hands_observed >= tendencies.hands_observed:
                read_tendencies = life_t
        except Exception as e:
            logger.debug(f"_opponent_deep_reads: lifetime load failed: {e}")

    return deep_reads_from_tendencies(read_tendencies)


def _get_opponent_stats(
    game_data: dict, human_name: str, user_id: str = None, historical_data: Optional[dict] = None
) -> List[Dict]:
    """Extract opponent stats from memory manager, including stack and all-in status.

    Args:
        game_data: The current game data dict
        human_name: The human player's name (observer)
        user_id: Optional user ID for fetching cross-session historical data

    Returns:
        List of opponent stat dicts, each containing current game stats
        (including stack, bet, is_all_in) and optionally a nested 'historical'
        block with cross-session data.
    """
    stats = []

    # Validate required game data
    state_machine = game_data.get('state_machine')
    if not state_machine:
        logger.error("_get_opponent_stats: state_machine missing from game_data")
        return stats

    try:
        game_state = state_machine.game_state
    except AttributeError as e:
        logger.error(f"_get_opponent_stats: cannot access game_state: {e}")
        return stats

    # Cross-session historical data — caller may pass it pre-loaded; else fetch.
    if historical_data is None:
        historical_data = _load_cross_session_historical(human_name, user_id)

    from poker.hand_ranges import EquityConfig

    min_hands = EquityConfig().min_hands_for_stats

    memory_manager = game_data.get('memory_manager')
    omm = None
    if memory_manager:
        omm = getattr(memory_manager, 'opponent_model_manager', None)

    # Reverse map: player name -> raw table position string. Used to
    # annotate each opponent with their seat so the coach can reason
    # about relative position (UTG opens vs BTN opens read very
    # differently).
    table_positions = getattr(game_state, 'table_positions', {}) or {}
    position_by_name = {name: pos for pos, name in table_positions.items()}

    try:
        for player in game_state.players:
            if player.name == human_name or player.is_folded:
                continue

            # Determine if player is all-in (stack is 0)
            is_all_in = player.stack == 0

            opp_data = {
                'name': player.name,
                'position': position_by_name.get(player.name),
                'stack': player.stack,
                'bet': player.bet,
                'is_all_in': is_all_in,
                'vpip': None,
                'pfr': None,
                'aggression': None,
                'style': 'unknown',
                'hands_observed': 0,
            }

            # Get model from human's perspective if available
            if omm and human_name in omm.models and player.name in omm.models[human_name]:
                try:
                    model = omm.models[human_name][player.name]
                    tendencies = model.tendencies
                    opp_data.update(
                        {
                            'vpip': round(tendencies.vpip, 2),
                            'pfr': round(tendencies.pfr, 2),
                            'aggression': round(tendencies.aggression_factor, 1),
                            'style': tendencies.get_play_style_label(),
                            'hands_observed': tendencies.hands_observed,
                            # Detection-layer archetype — the same read the
                            # tiered bots exploit. A diagnosis ("calling
                            # station") is far more actionable for the coach
                            # than raw VPIP/PFR/AF. None below the sample gate.
                            'archetype': _classify_opp_archetype(tendencies),
                            # Tier-2 postflop tells (fold-to-cbet, barreling,
                            # polarization, limp rate, …) — the same deep reads
                            # the dossier surfaces, so the coach can give
                            # exploit advice ("he folds to c-bets 70% — barrel
                            # him"). Each rate is None until its spot is seen.
                            'deep_reads': _opponent_deep_reads(
                                tendencies, model, memory_manager, user_id
                            ),
                        }
                    )
                except (AttributeError, KeyError) as e:
                    logger.warning(
                        f"_get_opponent_stats: failed to get tendencies for {player.name}: {e}"
                    )

            # Add historical data when available AND has enough samples
            # to render meaningful stats — empty placeholders coach prompts
            # 0%/0%/0.0 numbers that look like real reads.
            if player.name in historical_data:
                hist = historical_data[player.name]
                if hist.get('total_hands', 0) >= min_hands:
                    opp_data['historical'] = {
                        'session_count': hist['session_count'],
                        'total_hands': hist['total_hands'],
                        'vpip': hist['vpip'],
                        'pfr': hist['pfr'],
                        'aggression': hist['aggression_factor'],
                        'style': _get_style_label(hist['vpip'], hist['aggression_factor']),
                        'notes': hist['notes'][-5:],  # Most recent 5 notes
                    }

            stats.append(opp_data)
    except (AttributeError, TypeError) as e:
        logger.error(f"_get_opponent_stats: error iterating players: {e}")

    return stats


def _get_player_self_stats(game_data: dict, human_name: str) -> Optional[Dict]:
    """Get the human player's own stats from the AI observer with the most data."""
    try:
        memory_manager = game_data.get('memory_manager')
        if not memory_manager:
            return None

        omm = getattr(memory_manager, 'opponent_model_manager', None)
        if not omm:
            return None

        # Pick the observer with the most hands observed for the most accurate stats
        best = None
        best_hands = 0
        for observer_name, opponents in omm.models.items():
            if observer_name == human_name:
                continue
            if human_name in opponents:
                t = opponents[human_name].tendencies
                if t.hands_observed > best_hands:
                    best = t
                    best_hands = t.hands_observed

        if best and best_hands >= 1:
            return {
                'vpip': round(best.vpip, 2),
                'pfr': round(best.pfr, 2),
                'aggression': round(best.aggression_factor, 1),
                'style': best.get_play_style_label(),
                'hands_observed': best.hands_observed,
            }
    except Exception as e:
        logger.warning(f"Player self-stats extraction failed: {e}")

    return None


def _get_current_hand_actions(game_data: dict) -> List[Dict]:
    """Extract actions from the current in-progress hand."""
    memory_manager = game_data.get('memory_manager')
    if not memory_manager:
        return []
    recorder = getattr(memory_manager, 'hand_recorder', None)
    if not recorder or not recorder.current_hand:
        return []
    return [a.to_dict() for a in recorder.current_hand.actions]


def _get_available_actions(game_state, player, cost_to_call: int) -> List[str]:
    """Determine which actions are available to the player."""
    actions = []

    # Count active opponents (not folded, not the player)
    active_opponents = [p for p in game_state.players if not p.is_folded and p.name != player.name]

    # Check if all opponents are all-in
    all_opponents_all_in = all(p.stack == 0 for p in active_opponents)

    if cost_to_call == 0:
        actions.append('check')
        # Can only bet if there are opponents with chips left
        if not all_opponents_all_in and player.stack > 0:
            actions.append('bet')
    else:
        actions.append('fold')
        if player.stack > 0:
            if player.stack <= cost_to_call:
                actions.append('all-in')
            else:
                actions.append('call')
                # Can only raise if there are opponents with chips left
                if not all_opponents_all_in:
                    actions.append('raise')

    return actions


def _get_position_context(position: str, phase: str) -> str:
    """Get actionable context for the player's position.

    Position names from game: button, small_blind_player, big_blind_player,
    under_the_gun, cutoff, middle_position_1/2/3
    After _get_position_label: "Button", "Small Blind", "Big Blind", etc.
    """
    pos_lower = position.lower()

    if phase == 'PRE_FLOP':
        if 'button' in pos_lower:
            return "Best position - act last post-flop, can open wide"
        elif 'cutoff' in pos_lower:
            return "Late position - can open fairly wide"
        elif 'under' in pos_lower:
            return "Early position - play tight, premium hands only"
        elif 'small' in pos_lower and 'blind' in pos_lower:
            return "Small blind - worst position, act first post-flop"
        elif 'big' in pos_lower and 'blind' in pos_lower:
            return "Big blind - defend wider since you have money invested"
        elif 'middle' in pos_lower:
            return "Middle position - moderate opening range"
    else:
        # Post-flop position matters for acting order
        if 'button' in pos_lower:
            return "In position - act last, big advantage"
        elif 'cutoff' in pos_lower:
            return "In position vs blinds"
        elif ('small' in pos_lower or 'big' in pos_lower) and 'blind' in pos_lower:
            return "Out of position - act early, disadvantage"
        elif 'under' in pos_lower:
            return "Out of position vs later seats"
        elif 'middle' in pos_lower:
            return "Middle position - depends on remaining players"

    return ""


def compute_coaching_data(
    game_id: str,
    player_name: str,
    game_data: Optional[Dict] = None,
    game_state_override=None,
    user_id: str = None,
) -> Optional[Dict]:
    """Compute all coaching statistics for the given player.

    Args:
        game_id: The game identifier
        player_name: The human player's name
        game_data: Optional pre-loaded game data dict
        game_state_override: Optional game state to use instead of current
        user_id: Optional user ID for cross-session opponent history

    Returns a dict with equity, pot odds, hand strength, outs,
    recommendation, opponent stats, etc. Returns None if game not found.
    """
    if game_data is None:
        game_data = game_state_service.get_game(game_id)
    if not game_data:
        return None

    state_machine = game_data['state_machine']
    game_state = (
        game_state_override if game_state_override is not None else state_machine.game_state
    )

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

    # Stack-depth signals — effective stack and SPR are what bots and
    # serious players actually reason from postflop. The coach was
    # missing them; surface them so advice can reference depth properly.
    from poker.stack_utils import effective_stack_bb, effective_stack_chips, spr as compute_spr

    bb = game_state.current_ante or 0
    eff_stack = effective_stack_chips(game_state, player)
    eff_stack_bb = effective_stack_bb(game_state, player, big_blind=bb if bb > 0 else None)
    spr_value = compute_spr(game_state, player, pot_total=pot_total)

    result: Dict[str, Any] = {
        'phase': phase,
        'position': position,
        'pot_total': pot_total,
        'cost_to_call': cost_to_call,
        'big_blind': game_state.current_ante,
        'stack': player.stack,
        'effective_stack': eff_stack,
        'effective_stack_bb': eff_stack_bb,
        'spr': spr_value,
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

    # Load cross-session opponent history ONCE and share it between the
    # equity build and the opponent-stats block (both keyed on player_name +
    # user_id) instead of each hitting the DB independently.
    historical_data = _load_cross_session_historical(player_name, user_id)

    # Equity calculations (pass user_id for cross-session historic stats)
    opponent_infos = _build_opponent_infos(
        game_data, game_state, player_name, user_id, historical_data=historical_data
    )
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
        num_opponents = len(
            [p for p in game_state.players if not p.is_folded and p.name != player_name]
        )
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

    # Coach raise amount - populated by coach_routes when coach provides specific amount
    result['raise_to'] = None

    # Opponent stats (with stack, all-in info, and historical data if user_id provided)
    result['opponent_stats'] = _get_opponent_stats(
        game_data, player_name, user_id=user_id, historical_data=historical_data
    )

    # Available actions (what the player can actually do)
    result['available_actions'] = _get_available_actions(game_state, player, cost_to_call)

    # Position context (actionable guidance)
    result['position_context'] = _get_position_context(position, phase)

    # Player's own stats (from any AI observer's model)
    result['player_stats'] = _get_player_self_stats(game_data, player_name)

    # Current hand action timeline
    result['hand_actions'] = _get_current_hand_actions(game_data)
    result['hand_community_cards'] = community_strs
    result['hand_hole_cards'] = hand_strs

    # Player name for multi-street context filtering
    result['player_name'] = player_name

    # Board texture analysis (for coach to comment on wet/dry boards)
    if community_strs:
        try:
            from poker.board_analyzer import analyze_board_texture

            board_texture = analyze_board_texture(community_strs)
            result['board_texture'] = board_texture
        except Exception as e:
            logger.warning(f"Board texture analysis failed: {e}")

    # Opponent ranges summary (for coach to explain equity vs ranges)
    if opponent_infos:
        try:
            from poker.hand_ranges import EquityConfig, get_opponent_range

            config = EquityConfig()
            opponent_ranges = {}
            for opp_info in opponent_infos:
                opp_range = get_opponent_range(opp_info, config)
                opponent_ranges[opp_info.name] = {
                    'range_size': len(opp_range),
                    'range_pct': round(len(opp_range) / 169 * 100, 1),
                    'sample_hands': sorted(list(opp_range))[:10],
                }
            result['opponent_ranges'] = opponent_ranges
        except Exception as e:
            logger.warning(f"Opponent range calculation failed: {e}")

    # Player hand range analysis (is player playing outside standard range?)
    if hand_strs and len(hand_strs) == 2:
        try:
            from poker.hand_ranges import is_hand_in_standard_range

            position_key = _position_label_to_key(position)
            range_analysis = is_hand_in_standard_range(hand_strs[0], hand_strs[1], position_key)
            result['player_range_analysis'] = range_analysis

            # Live leak recall: if THIS hand+position is one of the player's
            # recurring preflop leaks (from their own history), flag it so the
            # proactive coach can give a Socratic reminder in the moment. Only
            # preflop; the leak set is loaded once per session and cached.
            if result.get('phase') == 'PRE_FLOP':
                _annotate_known_preflop_leak(
                    result, game_data, game_state, player_idx, range_analysis
                )
        except Exception as e:
            logger.warning(f"Player range analysis failed: {e}")

    return result


def _annotate_known_preflop_leak(result, game_data, game_state, player_idx, range_analysis) -> None:
    """Set result['known_preflop_leak'] when the CURRENT preflop spot matches one
    of the player's recurring chart leaks (graded vs the bots' solver charts).

    Two tiers: prefer a specific (scenario, position, hand) match, fall back to
    the (scenario, position) tendency (e.g. open-limping from the SB). Live
    nudges are confirmed-only and throttled to once per matched key per session,
    so the coach reminds without nagging. Best-effort; never raises.
    """
    try:
        from flask_app import extensions
        from poker.strategy.preflop_classifier import build_preflop_node

        from .coach_chart_data import get_owner_chart_leak_set

        owner_id = game_data.get('owner_id')
        canon = (range_analysis or {}).get('canonical_hand')
        if not (owner_id and canon):
            return

        node = build_preflop_node(game_state, player_idx, canon)
        scenario, position = node.scenario, node.position

        if '_chart_leak_set' not in game_data:
            game_data['_chart_leak_set'] = get_owner_chart_leak_set(
                extensions.persistence_db_path, owner_id
            )
        leak_set = game_data['_chart_leak_set']

        # Specific-hand match wins over the spot-tendency match.
        info = leak_set['by_hand'].get((scenario, position, canon))
        granularity, hand = ('hand', canon) if info else ('spot', '')
        if info is None:
            info = leak_set['by_spot'].get((scenario, position))
        if info is None:
            return

        # Throttle: nudge each matched key at most once per session.
        nudged = game_data.setdefault('_chart_leak_nudged', set())
        key = (granularity, scenario, position, hand)
        if key in nudged:
            return
        nudged.add(key)

        result['known_preflop_leak'] = {
            'scenario': scenario,
            'position': position,
            'hand': hand,
            'kind': info['kind'],
            'status': info['status'],  # confirmed (live is confirmed-only)
            'your_freq': info['your_freq'],
            'chart_freq': info['chart_freq'],
            'granularity': granularity,
        }
    except Exception as e:
        logger.debug(f"known-leak annotation failed: {e}")


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
    data = compute_coaching_data(game_id, player_name, game_data=game_data, user_id=user_id)
    if data is None:
        return None

    if not user_id or not coach_repo:
        return data

    try:
        service = CoachProgressionService(coach_repo)
        player_state = service.get_or_initialize_player(user_id)

        skill_states = player_state['skill_states']
        gate_progress = player_state['gate_progress']
        profile = player_state.get('profile', {})
        range_targets = profile.get('range_targets') if profile else None

        # Get coaching decision
        decision = service.get_coaching_decision(
            user_id, data, skill_states, gate_progress, range_targets=range_targets
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
            'range_targets': range_targets,
        }
    except Exception as e:
        logger.error(f"Coach progression enrichment failed: {e}", exc_info=True)

    return data
