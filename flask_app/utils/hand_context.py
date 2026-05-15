"""Pure helpers that turn a RecordedHand into LLM-ready prompt text.

Two related views are exposed:

* :func:`build_hand_context_from_recorded_hand` — structured dict of the
  hand outcome, opponent, hole/board cards, and a per-street action
  timeline. Used by the post-round chat route and the coach hand-review
  route.

* :func:`format_hand_context_for_prompt` — turns that dict into prompt
  text. When a ``RecordedHand`` is provided, it builds the rich
  narrator-driven recap (modeled on the hybrid-bot decision prompts):
  street-by-street action with the player rendered as "You", showdown
  reveal, and a per-card hand breakdown explaining which cards formed
  the player's best hand. Without it, falls back to a simpler block.

These live outside the routes package so tests can import them without
loading every blueprint (and the Flask-Limiter instance that goes with
them).
"""

from collections import defaultdict
from typing import Any, Dict, Optional
import logging

from core.card import Card
from poker.hand_narrator import (
    evaluate_hand_label,
    format_action_phrase,
    narrate_hand_breakdown,
    narrate_hand_recap,
)
from poker.memory.hand_history import RecordedHand

logger = logging.getLogger(__name__)


_OUTCOME_DESCRIPTIONS = {
    'WON_SHOWDOWN': "You WON this hand at showdown",
    'WON_BY_FOLD': "You WON this hand - everyone folded to you",
    'LOST_SHOWDOWN': "You LOST this hand at showdown",
    'FOLDED': "You FOLDED this hand",
}


def build_hand_context_from_recorded_hand(
    hand: RecordedHand,
    player_name: str,
) -> Dict[str, Any]:
    """Build a structured hand-context dict for the LLM prompt builder.

    Returns a dict with:
      - outcome: 'WON_SHOWDOWN' / 'WON_BY_FOLD' / 'LOST_SHOWDOWN' / 'FOLDED'
      - player_cards / player_hand_name
      - opponent_name / opponent_cards / opponent_hand_name
      - timeline: per-street action lines
      - community_cards / pot_size

    Opponent selection:
      * Player won: opponent is whoever committed the largest single amount
        against them — robust to multi-way pots with mixed action types.
      * Player lost or folded: opponent is the first winner that isn't the
        player. (Note: with side pots this can still be wrong if the side
        pot is listed before the main pot in ``hand.winners``; the rich
        recap surfaces all winners by name so the LLM can still ground
        its reaction correctly.)
    """
    result: Dict[str, Any] = {
        'outcome': None,
        'player_cards': None,
        'opponent_name': None,
        'opponent_cards': None,
        'opponent_hand_name': None,
        'player_hand_name': None,
        'timeline': '',
        'community_cards': list(hand.community_cards) if hand.community_cards else [],
        'pot_size': hand.pot_size,
    }

    player_outcome = hand.get_player_outcome(player_name)  # 'won' / 'lost' / 'folded'
    if player_outcome == 'won':
        result['outcome'] = 'WON_SHOWDOWN' if hand.was_showdown else 'WON_BY_FOLD'
    elif player_outcome == 'folded':
        result['outcome'] = 'FOLDED'
    else:
        result['outcome'] = 'LOST_SHOWDOWN'

    if player_name in hand.hole_cards:
        result['player_cards'] = hand.hole_cards[player_name]
    else:
        logger.warning(f"[HandContext] Player '{player_name}' not found in hole_cards!")

    # RecordedAction.amount mixes raise-TO targets with call-cost increments,
    # so the largest single committed amount is a more robust "who pressured
    # us most" proxy than summing.
    opponent_max_amount: Dict[str, int] = defaultdict(int)
    for action in hand.actions:
        if action.player_name == player_name:
            continue
        if action.amount > opponent_max_amount[action.player_name]:
            opponent_max_amount[action.player_name] = action.amount

    if player_outcome == 'won':
        if opponent_max_amount:
            result['opponent_name'] = max(opponent_max_amount, key=opponent_max_amount.get)
    else:
        for w in hand.winners:
            if w.name != player_name:
                result['opponent_name'] = w.name
                result['opponent_hand_name'] = w.hand_name
                break

    if result['opponent_name'] and result['opponent_name'] in hand.hole_cards:
        result['opponent_cards'] = hand.hole_cards[result['opponent_name']]

    if player_outcome == 'won' and hand.was_showdown:
        for w in hand.winners:
            if w.name == player_name:
                result['player_hand_name'] = w.hand_name
                break

    # Winners' hand names come from WinnerInfo; for an opponent who lost
    # the showdown we evaluate live so the field is still populated.
    if (
        not result['opponent_hand_name']
        and result['opponent_cards']
        and hand.was_showdown
    ):
        result['opponent_hand_name'] = evaluate_hand_label(
            result['opponent_cards'], result['community_cards']
        )

    # Per-street action timeline. One action per indented line so the LLM
    # parses each event cleanly. Action wording (raise-TO semantics, "You"
    # substitution) is delegated to the shared poker.hand_narrator helper.
    phases = ['PRE_FLOP', 'FLOP', 'TURN', 'RIVER']
    actions_by_phase: Dict[str, list] = defaultdict(list)
    for action in hand.actions:
        actions_by_phase[action.phase].append(action)

    community = list(hand.community_cards) if hand.community_cards else []
    phase_cards = {
        'FLOP': community[0:3] if len(community) >= 3 else [],
        'TURN': [community[3]] if len(community) >= 4 else [],
        'RIVER': [community[4]] if len(community) >= 5 else [],
    }

    timeline_parts = []
    for phase in phases:
        phase_actions = actions_by_phase.get(phase, [])
        if not phase_actions:
            continue
        cards = phase_cards.get(phase, [])
        phase_header = f"{phase} [{', '.join(cards)}]" if cards else phase
        action_lines = [
            format_action_phrase(a, perspective=player_name)
            for a in phase_actions
        ]
        indented = "\n".join(f"  {line}" for line in action_lines)
        timeline_parts.append(f"{phase_header}:\n{indented}")

    result['timeline'] = '\n'.join(timeline_parts)
    return result


def format_hand_context_for_prompt(
    context: Dict[str, Any],
    player_name: str,
    recorded_hand: Optional[RecordedHand] = None,
    big_blind: Optional[int] = None,
) -> str:
    """Format hand context as LLM-ready text.

    When ``recorded_hand`` is provided, builds the rich narrator-driven
    recap (modeled on the hybrid-bot decision prompts). Otherwise falls
    back to the legacy block — kept for callers that built their own
    context dict without a RecordedHand handy.
    """
    if recorded_hand is not None:
        return _format_hand_context_rich(context, player_name, recorded_hand, big_blind)

    parts = [f"OUTCOME: {_OUTCOME_DESCRIPTIONS.get(context['outcome'], context['outcome'])}"]

    if context['player_cards']:
        cards_str = ', '.join(context['player_cards'])
        if context.get('player_hand_name'):
            parts.append(f"YOUR CARDS: {cards_str} ({context['player_hand_name']})")
        else:
            parts.append(f"YOUR CARDS: {cards_str}")

    if context['opponent_name']:
        opp_str = f"OPPONENT: {context['opponent_name']}"
        if context['opponent_cards']:
            opp_str += f" - {', '.join(context['opponent_cards'])}"
        if context['opponent_hand_name']:
            opp_str += f" ({context['opponent_hand_name']})"
        parts.append(opp_str)

    if context['community_cards']:
        parts.append(f"BOARD: {', '.join(context['community_cards'])}")

    if context['timeline']:
        parts.append(f"\nHAND TIMELINE:\n{context['timeline']}")

    parts.append(f"\nFinal pot: ${context['pot_size']}")

    if context.get('drama_note'):
        parts.append(f"\n{context['drama_note']}")

    return '\n'.join(parts)


def _format_hand_context_rich(
    context: Dict[str, Any],
    player_name: str,
    recorded_hand: RecordedHand,
    big_blind: Optional[int] = None,
) -> str:
    """Narrator-driven recap with per-card hand breakdown.

    Output sections:
      1. OUTCOME — single-line "You WON/LOST/FOLDED" prefix the LLM can lean on.
      2. Recap from ``narrate_hand_recap`` — player list with positions,
         street-by-street action with "You" substitution, RESULT, SHOWDOWN.
      3. YOUR HAND BREAKDOWN — per-card explanation of which cards formed
         the player's best 5-card hand (when at least 3 board cards exist).
    """
    parts = [f"OUTCOME: {_OUTCOME_DESCRIPTIONS.get(context['outcome'], context['outcome'])}"]

    recap = narrate_hand_recap(
        recorded_hand, big_blind=big_blind, perspective=player_name,
    )
    if recap:
        parts.append("")
        parts.append(recap)

    breakdown = _build_player_hand_breakdown(recorded_hand, player_name)
    if breakdown:
        parts.append("")
        parts.append(f"YOUR HAND BREAKDOWN:\n{breakdown}")

    if context.get('drama_note'):
        parts.append(f"\n{context['drama_note']}")

    return '\n'.join(parts)


def _build_player_hand_breakdown(
    recorded_hand: RecordedHand, player_name: str,
) -> str:
    """Return narrate_hand_breakdown output for the player's hand, or ''.

    Requires hole cards + at least 3 community cards to produce a 5-card
    hand. Card strings from the recorder use short form (e.g. ``"Ah"``),
    which ``Card.from_short`` parses.
    """
    hole_strs = recorded_hand.hole_cards.get(player_name) or []
    community_strs = list(recorded_hand.community_cards) if recorded_hand.community_cards else []
    if len(hole_strs) < 2 or len(community_strs) < 3:
        return ""
    try:
        hole_cards = [Card.from_short(c) for c in hole_strs]
        community_cards = [Card.from_short(c) for c in community_strs]
    except (KeyError, ValueError, IndexError) as e:
        logger.debug(f"[HandContext] Card parsing failed for breakdown: {e}")
        return ""
    return narrate_hand_breakdown(hole_cards, community_cards)
