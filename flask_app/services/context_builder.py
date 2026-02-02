"""Build standardised poker context from coaching data.

Extracts hand-parsing, position classification, and tier logic
into a single place used by SituationClassifier and SkillEvaluator.
"""

from collections import defaultdict
from typing import Dict, Optional

from poker.controllers import PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS


def build_poker_context(coaching_data: Dict) -> Optional[Dict]:
    """Build a standardised context dict from coaching_data.

    Used by both SituationClassifier and SkillEvaluator so the
    hand-parsing / position / tier logic lives in one place.

    Returns None when there is no phase (nothing to evaluate).
    """
    phase = coaching_data.get('phase', '')
    if not phase:
        return None

    # Parse canonical hand from hand_strength string
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

    # Post-flop hand strength (from HandEvaluator via coaching_data)
    # hand_rank: 1=Royal Flush, 2=Straight Flush, 3=Four of a Kind, 4=Full House,
    #            5=Flush, 6=Straight, 7=Three of a Kind, 8=Two Pair, 9=One Pair, 10=High Card
    hand_rank = coaching_data.get('hand_rank')

    # Derived booleans for Gate 2 evaluators
    is_strong_hand = hand_rank is not None and hand_rank <= 8  # Two pair or better
    has_pair = hand_rank is not None and hand_rank <= 9        # One pair or better
    has_draw = (coaching_data.get('outs') or 0) >= 4           # 4+ outs = meaningful draw
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
    player_bet_turn = bool(_aggressive & set(player_actions_by_phase.get('TURN', [])))
    opponent_bet_flop = len(opponent_bets_by_phase.get('FLOP', [])) > 0
    opponent_bet_turn = len(opponent_bets_by_phase.get('TURN', [])) > 0
    opponent_double_barrel = opponent_bet_flop and opponent_bet_turn

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
    }
