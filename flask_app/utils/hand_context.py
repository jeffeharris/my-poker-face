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
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple
import logging

from core.card import Card
from poker.hand_evaluator import HandEvaluator
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
      * Player won: opponent is whoever contributed the most chips to the
        pot, computed via ``hand.get_player_contributions()`` so raise-TO
        snapshots and call-cost increments are normalized to the same
        unit before being compared.
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

    # Use the per-action contribution normalizer so raise-TO snapshots
    # and call-cost increments are summed in the same unit. Without this
    # multi-way pots with mixed action types misrank the opponent.
    contributions = hand.get_player_contributions()
    opponent_contributions = {
        name: chips for name, chips in contributions.items()
        if name != player_name
    }

    if player_outcome == 'won':
        if opponent_contributions:
            result['opponent_name'] = max(
                opponent_contributions, key=opponent_contributions.get
            )
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
      2. YOUR CARDS / BOARD / WINNER — explicit always-present facts so the
         LLM always sees what cards the player held, the community cards,
         and who won with what. These are surfaced up front because the
         narrator recap can drop them on sparse hands (e.g. preflop folds
         or hands with incomplete action records).
      3. Recap from ``narrate_hand_recap`` — player list with positions,
         street-by-street action with "You" substitution, RESULT, SHOWDOWN.
      4. YOUR HAND BREAKDOWN — per-card explanation of which cards formed
         the player's best 5-card hand (when at least 3 board cards exist).
    """
    parts = [f"OUTCOME: {_OUTCOME_DESCRIPTIONS.get(context['outcome'], context['outcome'])}"]

    facts = _build_explicit_card_facts(context, recorded_hand, player_name, big_blind)
    if facts:
        parts.append("")
        parts.extend(facts)

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


def _build_explicit_card_facts(
    context: Dict[str, Any],
    recorded_hand: RecordedHand,
    player_name: str,
    big_blind: Optional[int],
) -> list:
    """Explicit cards block the LLM can't misread.

    Lists separately:
      - YOUR HOLE CARDS — the two cards dealt to the player.
      - COMMUNITY CARDS — the board.
      - YOUR BEST 5 CARDS — the actual 5 cards forming the player's best
        hand, tagged ``(yours)`` / ``(board)`` so a small model can't claim
        the player "won with their cards" when the pair came off the board.
      - HOLE CARDS USED — which of the player's hole cards are active.
      - RIVER IMPACT — whether the river card actually changed the player's
        best 5. Hard guard against the "lucky river!" trope when the river
        was a blank.
      - OPPONENT AT SHOWDOWN — best-5 for every non-folded opponent.
    """
    lines = []

    hole_strs = recorded_hand.hole_cards.get(player_name) or context.get('player_cards') or []
    community_strs = (
        list(recorded_hand.community_cards) if recorded_hand.community_cards
        else (context.get('community_cards') or [])
    )

    if hole_strs:
        lines.append(f"YOUR HOLE CARDS: {' '.join(hole_strs)}")

    if community_strs:
        lines.append(f"COMMUNITY CARDS: {' '.join(community_strs)}")
    elif hole_strs:
        lines.append("COMMUNITY CARDS: (none — hand ended preflop)")

    best5 = _compute_best5_info(hole_strs, community_strs)
    if best5:
        provenance, hand_name, used_hole_strs, river_impact = best5
        if hand_name:
            lines.append(f"YOUR BEST 5 CARDS: {provenance}  →  {hand_name}")
        else:
            lines.append(f"YOUR BEST 5 CARDS: {provenance}")

        unused = [c for c in hole_strs if c not in used_hole_strs]
        if used_hole_strs and unused:
            lines.append(
                f"HOLE CARDS USED: {', '.join(used_hole_strs)} — "
                f"your {', '.join(unused)} played no role."
            )
        elif used_hole_strs:
            lines.append(f"HOLE CARDS USED: {', '.join(used_hole_strs)}.")
        else:
            lines.append("HOLE CARDS USED: none — the board played your hand.")

        if river_impact is not None:
            lines.append(f"RIVER IMPACT: {river_impact}")

    winner_lines = _build_winner_summary(recorded_hand, player_name, big_blind)
    lines.extend(winner_lines)

    opponent_lines = _build_opponent_showdowns(recorded_hand, player_name)
    if opponent_lines:
        lines.append("")
        lines.append("OPPONENTS AT SHOWDOWN:")
        lines.extend(opponent_lines)

    return lines


def _build_winner_summary(
    recorded_hand: RecordedHand,
    player_name: str,
    big_blind: Optional[int],
) -> list:
    """One line per winner: name, amount won, cards (if shown), made hand."""
    from poker.hand_narrator import _fmt_amount  # local import to avoid cycle

    if not recorded_hand.winners:
        return []

    lines = []
    for w in recorded_hand.winners:
        name = "You" if w.name == player_name else w.name
        amount = _fmt_amount(w.amount_won, big_blind)
        cards = recorded_hand.hole_cards.get(w.name) if recorded_hand.was_showdown else None
        cards_part = f" with [{', '.join(cards)}]" if cards else ""
        hand_part = f" — {w.hand_name}" if w.hand_name else ""
        lines.append(f"WINNER: {name} won {amount}{cards_part}{hand_part}")
    return lines


def _build_opponent_showdowns(
    recorded_hand: RecordedHand, player_name: str,
) -> list:
    """One indented line per non-folded showdown opponent.

    Format::

        - Lady Macbeth: hole 6♠ 10♦  →  best 5: 2♠ 2♣ 10♦(hers) J♥ 7♣  (One Pair, 2's)
    """
    if not recorded_hand.was_showdown:
        return []

    folded = {a.player_name for a in recorded_hand.actions if a.action == 'fold'}
    acted = {a.player_name for a in recorded_hand.actions}
    opponents = [
        p.name for p in recorded_hand.players
        if p.name != player_name
        and p.name in acted
        and p.name not in folded
        and p.name in recorded_hand.hole_cards
    ]

    community_strs = list(recorded_hand.community_cards) if recorded_hand.community_cards else []
    if not community_strs or not opponents:
        return []

    winner_hand_names = {w.name: w.hand_name for w in recorded_hand.winners if w.hand_name}

    lines = []
    for name in opponents:
        opp_hole = recorded_hand.hole_cards.get(name) or []
        if not opp_hole:
            continue
        best5 = _compute_best5_info(opp_hole, community_strs, hole_label="hers")
        if best5:
            provenance, hand_name, _, _ = best5
            label = winner_hand_names.get(name) or hand_name
            label_part = f"  ({label})" if label else ""
            lines.append(
                f"  - {name}: hole {' '.join(opp_hole)}  →  best 5: {provenance}{label_part}"
            )
        else:
            label = winner_hand_names.get(name)
            label_part = f"  ({label})" if label else ""
            lines.append(f"  - {name}: hole {' '.join(opp_hole)}{label_part}")

    return lines


def _compute_best5_info(
    hole_strs: list,
    community_strs: list,
    hole_label: str = "yours",
) -> Optional[Tuple[str, str, List[str], Optional[str]]]:
    """Return (provenance_str, hand_name, used_hole_strs, river_impact) or None.

    ``provenance_str`` is the 5 cards tagged with ``(yours)`` / ``(board)``
    in best-hand display order (made-hand cards first, then kickers, high
    to low). ``used_hole_strs`` is the subset of hole cards that appear in
    the best 5. ``river_impact`` is ``None`` when there's no river card to
    reason about, otherwise a human-readable sentence stating whether the
    river card changed the player's made hand.
    """
    if len(hole_strs) < 2 or len(community_strs) < 3:
        return None
    try:
        hole_cards = [Card.from_short(c) for c in hole_strs]
        community_cards = [Card.from_short(c) for c in community_strs]
    except (KeyError, ValueError, IndexError) as e:
        logger.debug(f"[HandContext] Card parsing failed for best-5: {e}")
        return None

    best5_cards, hand_name = _select_best_5(hole_cards + community_cards)
    if not best5_cards:
        return None

    hole_set = {(c.value, c.suit) for c in hole_cards}
    ordered = _order_for_display(best5_cards)
    provenance_tokens = [
        f"{str(c)}({hole_label})" if (c.value, c.suit) in hole_set else f"{str(c)}(board)"
        for c in ordered
    ]
    used_hole_strs = [
        str(c) for c in hole_cards if (c.value, c.suit) in {(b.value, b.suit) for b in best5_cards}
    ]
    # Map back to the original strings so unicode-suit display is preserved.
    used_hole_input_strs = [
        hole_strs[i] for i, c in enumerate(hole_cards)
        if (c.value, c.suit) in {(b.value, b.suit) for b in best5_cards}
    ]

    river_impact = _compute_river_impact(hole_cards, community_cards, hand_name)

    return " ".join(provenance_tokens), hand_name, used_hole_input_strs, river_impact


def _select_best_5(seven_cards: List[Card]) -> Tuple[List[Card], str]:
    """Brute-force the best 5-card combo out of 7 (or however many).

    Returns the 5 Card objects forming the best hand plus the hand_name.
    HandEvaluator's hand_rank is 1=Royal Flush, 10=High Card (lower better),
    so we sort by (-hand_rank, hand_values, kicker_values) descending.
    """
    if len(seven_cards) < 5:
        return [], ""

    best_key = None
    best_combo: List[Card] = []
    best_name = ""
    for combo in combinations(seven_cards, 5):
        result = HandEvaluator(list(combo)).evaluate_hand()
        key = (
            -result.get("hand_rank", 10),
            tuple(result.get("hand_values", []) or []),
            tuple(result.get("kicker_values", []) or []),
        )
        if best_key is None or key > best_key:
            best_key = key
            best_combo = list(combo)
            best_name = result.get("hand_name", "")
    return best_combo, best_name


def _order_for_display(cards: List[Card]) -> List[Card]:
    """Sort the best-5 cards into a stable display order.

    Repeated ranks (pair, trips, quads) cluster together. Within a rank,
    sort by suit alphabetically so output is deterministic. Across ranks,
    sort by frequency (more = more important) then by rank descending.
    """
    from collections import Counter
    rank_counts = Counter(c.value for c in cards)
    return sorted(
        cards,
        key=lambda c: (-rank_counts[c.value], -c.value, c.suit),
    )


def _compute_river_impact(
    hole_cards: List[Card],
    community_cards: List[Card],
    river_hand_name: str,
) -> Optional[str]:
    """Did the river card change the player's best hand?

    Returns ``None`` if there's no river card (fewer than 5 board cards).
    Otherwise, returns a short sentence the LLM can quote so it doesn't
    fabricate a "river save" when the river was a blank.
    """
    if len(community_cards) < 5:
        return None

    pre_river_board = community_cards[:4]
    _, pre_river_name = _select_best_5(hole_cards + pre_river_board)
    river_card = community_cards[4]

    if not pre_river_name:
        return f"River {river_card} completed your hand ({river_hand_name})."
    if pre_river_name == river_hand_name:
        return f"River {river_card} was a blank — your hand was already {river_hand_name} on the turn."
    return (
        f"River {river_card} changed your hand from {pre_river_name} to {river_hand_name}."
    )


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
