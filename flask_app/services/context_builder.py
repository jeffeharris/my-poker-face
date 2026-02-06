"""Build standardised poker context from coaching data.

Extracts hand-parsing, position classification, and tier logic
into a single place used by SituationClassifier and SkillEvaluator.
"""

from collections import defaultdict
from typing import Dict, Optional

from poker.hand_tiers import (
    PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS,
    is_hand_in_range,
)


RANK_VALUES = {
    '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8,
    '9': 9, 'T': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14,
}


def _has_real_draw(coaching_data: Dict) -> bool:
    """Check for flush draw or straight draw using actual cards.

    Raw outs count is unreliable because it includes pair-outs (any card
    that pairs a hole card), which makes every high-card hand look like
    it has a draw.  Instead, check suit and rank patterns directly.
    """
    hole = coaching_data.get('hand_hole_cards') or []
    community = coaching_data.get('hand_community_cards') or []
    if not hole or not community:
        # Pre-flop or missing data — fall back to outs count
        return (coaching_data.get('outs') or 0) >= 8

    all_cards = hole + community

    # --- Flush draw: 4 cards of the same suit ---
    suits: Dict[str, int] = defaultdict(int)
    for card in all_cards:
        if len(card) >= 2:
            suits[card[-1]] += 1
    has_flush_draw = any(count == 4 for count in suits.values())

    # --- Straight draw: 4 unique ranks within a 5-rank window ---
    ranks = sorted({RANK_VALUES.get(card[:-1], 0) for card in all_cards if len(card) >= 2})
    has_straight_draw = False
    # Also handle ace-low (A-2-3-4-5) by adding rank 1 if ace present
    if 14 in ranks:
        ranks = [1] + ranks
    for i in range(len(ranks)):
        # Count unique ranks in a 5-wide window starting at ranks[i]
        window_start = ranks[i]
        window_end = window_start + 4
        count = sum(1 for r in ranks if window_start <= r <= window_end)
        if count >= 4:
            has_straight_draw = True
            break

    return has_flush_draw or has_straight_draw


def build_poker_context(
    coaching_data: Dict,
    range_targets: Optional[Dict[str, float]] = None
) -> Optional[Dict]:
    """Build a standardised context dict from coaching_data.

    Used by both SituationClassifier and SkillEvaluator so the
    hand-parsing / position / tier logic lives in one place.

    Args:
        coaching_data: Dict from compute_coaching_data() with game state
        range_targets: Optional personal range targets dict (position -> percentage)
                       If provided, adds is_in_personal_range to context

    Returns None when there is no phase (nothing to evaluate).
    """
    phase = coaching_data.get('phase', '')
    if not phase:
        return None

    # Format: "AKs - Suited broadway, Top 10% of starting hands"
    canonical = ''
    hand_strength = coaching_data.get('hand_strength', '')
    if hand_strength and ' - ' in hand_strength:
        canonical = hand_strength.split(' - ')[0].strip()

    position = coaching_data.get('position', '').lower()
    cost_to_call = coaching_data.get('cost_to_call', 0)
    pot_total = coaching_data.get('pot_total', 0)
    big_blind = coaching_data.get('big_blind', 0)

    # Position categories
    early_positions = {'under the gun', 'utg', 'utg+1', 'early position'}
    late_positions = {'button', 'cutoff', 'btn', 'co', 'dealer'}
    is_early = any(ep in position for ep in early_positions)
    is_late = any(lp in position for lp in late_positions)
    is_blind = 'blind' in position

    # Hand tiers
    is_trash = canonical and canonical not in TOP_35_HANDS
    is_premium = canonical and canonical in PREMIUM_HANDS
    is_top10 = canonical and canonical in TOP_10_HANDS
    is_top20 = canonical and canonical in TOP_20_HANDS
    is_playable = canonical and canonical in TOP_35_HANDS

    # Personal range check (if range_targets provided)
    is_in_personal_range = False
    personal_range_target = None
    if range_targets and canonical:
        from .range_targets import get_range_target
        personal_range_target = get_range_target(range_targets, position)
        is_in_personal_range = is_hand_in_range(canonical, personal_range_target)

    # Post-flop hand strength (from HandEvaluator via coaching_data)
    # hand_rank: 1=Royal Flush, 2=Straight Flush, 3=Four of a Kind, 4=Full House,
    #            5=Flush, 6=Straight, 7=Three of a Kind, 8=Two Pair, 9=One Pair, 10=High Card
    hand_rank = coaching_data.get('hand_rank')

    is_strong_hand = hand_rank is not None and hand_rank <= 8  # Two pair or better
    has_pair = hand_rank is not None and hand_rank <= 9        # One pair or better
    has_draw = _has_real_draw(coaching_data)                     # Flush draw or straight draw (not just pair-outs)
    is_air = hand_rank is not None and hand_rank >= 10 and not has_draw  # High card, no draw
    can_check = cost_to_call == 0

    # --- Multi-street context ---
    hand_actions = coaching_data.get('hand_actions', [])
    player_name = coaching_data.get('player_name', '')

    # Player's actions by phase
    player_actions_by_phase = defaultdict(list)
    for a in hand_actions:
        if a.get('player_name') == player_name:
            player_actions_by_phase[a['phase']].append(a['action'])

    # Opponent aggressive actions by phase
    opponent_bets_by_phase = defaultdict(list)
    for a in hand_actions:
        if a.get('player_name') != player_name and a['action'] in ('raise', 'bet', 'all_in'):
            opponent_bets_by_phase[a['phase']].append(a)

    _aggressive = {'raise', 'bet', 'all_in'}
    player_bet_flop = bool(_aggressive & set(player_actions_by_phase.get('FLOP', [])))
    opponent_bet_turn = len(opponent_bets_by_phase.get('TURN', [])) > 0
    opponent_double_barrel = (
        len(opponent_bets_by_phase.get('FLOP', [])) > 0 and opponent_bet_turn
    )

    # --- Equity fields ---
    equity = coaching_data.get('equity')
    required_equity = coaching_data.get('required_equity')

    # --- Bet sizing context ---
    bet_to_pot_ratio = coaching_data.get('bet_to_pot_ratio', 0)

    # Situation tags
    tag_conditions = [
        ('trash_hand', is_trash),
        ('premium_hand', is_premium),
        ('early_position', is_early),
        ('late_position', is_late),
        ('strong_hand', is_strong_hand),
        ('air', is_air),
    ]
    tags = tuple(tag for tag, cond in tag_conditions if cond)

    return {
        'phase': phase,
        'canonical': canonical,
        'position': position,
        'is_early': is_early,
        'is_late': is_late,
        'is_blind': is_blind,
        'is_trash': is_trash,
        'is_premium': is_premium,
        'is_top10': is_top10,
        'is_top20': is_top20,
        'is_playable': is_playable,
        'cost_to_call': cost_to_call,
        'pot_total': pot_total,
        'big_blind': big_blind,
        'hand_rank': hand_rank,
        'is_strong_hand': is_strong_hand,
        'has_pair': has_pair,
        'has_draw': has_draw,
        'is_air': is_air,
        'can_check': can_check,
        'is_marginal_hand': has_pair and not is_strong_hand,
        'is_big_bet': (
            (pot_total - cost_to_call > 0 and cost_to_call >= (pot_total - cost_to_call) * 0.5)
            or cost_to_call > pot_total  # overbets always count as big
        ),
        'tags': tags,
        # Multi-street context
        'player_bet_flop': player_bet_flop,
        'opponent_bet_turn': opponent_bet_turn,
        'opponent_double_barrel': opponent_double_barrel,
        # Equity fields
        'equity': equity,
        'required_equity': required_equity,
        # Bet sizing
        'bet_to_pot_ratio': bet_to_pot_ratio,
        # Personal range (if range_targets provided)
        'is_in_personal_range': is_in_personal_range,
        'personal_range_target': personal_range_target,
    }
