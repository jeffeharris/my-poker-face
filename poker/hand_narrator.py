"""
Hand Narrator — Deterministic narration of poker hands for LLM consumption.

Generates structured, factual text descriptions of:
1. Hand breakdowns (which cards form the hand, for decision prompts)
2. Hand recaps (street-by-street play-by-play, for commentary/memory)
3. Key moments (concise pivotal events, for session memory)

All functions are pure and deterministic — no LLM calls, no side effects.
"""

from collections import Counter
from typing import Dict, List, Optional, Tuple

from core.card import Card
from .hand_evaluator import HandEvaluator, rank_to_display


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def narrate_hand_breakdown(
    hole_cards: List[Card],
    community_cards: List[Card],
) -> str:
    """Build a factual explanation of which cards form the player's best hand.

    Meant for injection into the decision prompt so the LLM doesn't have to
    figure out its own hand composition.

    Args:
        hole_cards: The player's 2 hole cards (Card objects).
        community_cards: The community cards on the board (Card objects).

    Returns:
        Multi-line string explaining the hand, e.g.::

            HAND BREAKDOWN: Two Pair, A's and K's
            Your Ah pairs with the Ac on the board (top pair).
            Your Kd pairs with the Kc on the board (second pair).
            Kicker: 7s from the board.
            Both of your hole cards are active in your hand.
    """
    if not hole_cards or not community_cards:
        return ""

    all_cards = list(hole_cards) + list(community_cards)
    if len(all_cards) < 5:
        return ""

    evaluator = HandEvaluator(all_cards)
    result = evaluator.evaluate_hand()

    hand_name = result.get("hand_name", "High Card")
    hand_rank = result.get("hand_rank", 10)
    hand_values = result.get("hand_values", [])
    kicker_values = result.get("kicker_values", [])
    flush_suit = result.get("suit")

    lines: List[str] = [f"HAND BREAKDOWN: {hand_name}"]

    # Describe based on hand type
    descriptor = _HAND_DESCRIBERS.get(hand_rank, _describe_high_card)
    detail_lines = descriptor(
        hole_cards=hole_cards,
        community_cards=community_cards,
        hand_values=hand_values,
        kicker_values=kicker_values,
        flush_suit=flush_suit,
        hand_name=hand_name,
    )
    lines.extend(detail_lines)

    # Summarize hole card usage
    hole_usage = _summarize_hole_card_usage(
        hole_cards, hand_values, kicker_values, hand_rank, flush_suit
    )
    if hole_usage:
        lines.append(hole_usage)

    return "\n".join(lines)


def narrate_hand_recap(
    recorded_hand,
    big_blind: Optional[int] = None,
) -> str:
    """Build a street-by-street play-by-play of a completed hand.

    Args:
        recorded_hand: A RecordedHand (from poker.memory.hand_history).
        big_blind: If provided, amounts are shown in BB. Otherwise dollars.

    Returns:
        Multi-line string, e.g.::

            HAND #12 RECAP
            Players: Batman (BTN), Joker (SB), Superman (BB)

            PRE-FLOP:
              Batman raised to 6 BB. Joker called. Superman folded.

            FLOP [Ah Kd 7s]:
              Batman bet 10 BB. Joker called.

            TURN [3c]:
              Both checked.

            RIVER [2h]:
              Joker bet 20 BB. Batman folded.

            RESULT: Joker won 52 BB.
    """
    hand = recorded_hand
    lines: List[str] = [f"HAND #{hand.hand_number} RECAP"]

    # Player list with positions
    player_strs = []
    for p in hand.players:
        pos = p.position if p.position != "Unknown" else ""
        player_strs.append(f"{p.name} ({pos})" if pos else p.name)
    lines.append(f"Players: {', '.join(player_strs)}")
    lines.append("")

    # Group actions by phase
    phases = ["PRE_FLOP", "FLOP", "TURN", "RIVER"]
    actions_by_phase: Dict[str, list] = {p: [] for p in phases}
    for action in hand.actions:
        if action.phase in actions_by_phase:
            actions_by_phase[action.phase].append(action)

    # Track community cards revealed per phase
    community = list(hand.community_cards)
    phase_cards = _split_community_by_phase(community)

    for phase in phases:
        phase_actions = actions_by_phase[phase]
        if not phase_actions:
            continue

        # Phase header with cards
        header = _format_phase_header(phase, phase_cards.get(phase, []))
        lines.append(f"{header}:")

        # Format actions
        action_strs = _format_actions(phase_actions, big_blind)
        lines.append(f"  {' '.join(action_strs)}")
        lines.append("")

    # Result — handle split pots and ties clearly
    if hand.winners:
        is_split = len(hand.winners) > 1
        if is_split:
            winner_names = [w.name for w in hand.winners]
            total_pot = _fmt_amount(hand.pot_size, big_blind)
            per_player = _fmt_amount(hand.winners[0].amount_won, big_blind)
            hand_desc = hand.winners[0].hand_name
            if hand_desc:
                lines.append(
                    f"RESULT: SPLIT POT — {' and '.join(winner_names)} "
                    f"tied with {hand_desc} and split the {total_pot} pot "
                    f"({per_player} each)."
                )
            else:
                lines.append(
                    f"RESULT: SPLIT POT — {' and '.join(winner_names)} "
                    f"split the {total_pot} pot ({per_player} each)."
                )
        else:
            w = hand.winners[0]
            amount = _fmt_amount(w.amount_won, big_blind)
            if w.hand_name:
                lines.append(f"RESULT: {w.name} won {amount} with {w.hand_name}.")
            else:
                lines.append(f"RESULT: {w.name} won {amount}.")

        # Show showdown cards if available
        if hand.was_showdown and hand.hole_cards:
            shown = []
            for w in hand.winners:
                if w.name in hand.hole_cards:
                    cards_str = ", ".join(hand.hole_cards[w.name])
                    shown.append(f"{w.name} showed [{cards_str}]")
            if shown:
                lines.append(f"SHOWDOWN: {'; '.join(shown)}")

    return "\n".join(lines)


def narrate_key_moments(
    recorded_hand,
    player_name: str,
    big_blind: Optional[int] = None,
) -> Optional[str]:
    """Extract a concise one-liner describing the pivotal moment of a hand.

    Returns None if the hand had no notable moments (routine fold, small pot).

    Args:
        recorded_hand: A RecordedHand.
        player_name: The player's perspective.
        big_blind: For BB formatting.

    Returns:
        A string like "You went all-in and won 50 BB with Two Pair" or None.
    """
    hand = recorded_hand
    player_actions = [a for a in hand.actions if a.player_name == player_name]
    outcome = hand.get_player_outcome(player_name)
    pot = _fmt_amount(hand.pot_size, big_blind)
    is_split = len(hand.winners) > 1
    player_won = outcome == "won"

    # Split pot detection — player is one of multiple winners
    is_player_in_split = is_split and player_won
    if is_player_in_split:
        other_winners = [w.name for w in hand.winners if w.name != player_name]
        partner = other_winners[0] if other_winners else "opponent"
        my_share = _find_winner(hand, player_name)
        share_amt = _fmt_amount(my_share.amount_won, big_blind) if my_share else pot
        hand_desc = f" with {my_share.hand_name}" if my_share and my_share.hand_name else ""

    # All-in is always notable
    all_in_action = next(
        (a for a in hand.actions if a.action == "all_in"), None
    )
    if all_in_action:
        if is_player_in_split:
            return (
                f"All-in showdown — you and {partner} split the {pot} pot{hand_desc} "
                f"({share_amt} each)"
            )
        if all_in_action.player_name == player_name:
            if player_won:
                winner = _find_winner(hand, player_name)
                hand_desc = f" with {winner.hand_name}" if winner and winner.hand_name else ""
                return f"You went all-in and won {pot}{hand_desc}"
            elif outcome == "folded":
                return f"You went all-in but folded later (side pot scenario)"
            else:
                return f"You went all-in and lost ({pot} pot)"
        else:
            if player_won:
                winner = _find_winner(hand, player_name)
                hand_desc = f" with {winner.hand_name}" if winner and winner.hand_name else ""
                return f"{all_in_action.player_name} went all-in — you called and won {pot}{hand_desc}"
            elif outcome == "lost":
                return f"{all_in_action.player_name} went all-in — you called and lost ({pot} pot)"

    # Showdown is notable
    if hand.was_showdown:
        if is_player_in_split:
            hand_label = my_share.hand_name if my_share and my_share.hand_name else None
            if hand_label:
                return (
                    f"Split pot at showdown — you and {partner} both had {hand_label} "
                    f"and split {pot} ({share_amt} each)"
                )
            return f"Split pot at showdown — you and {partner} tied and split {pot} ({share_amt} each)"
        if player_won:
            winner = _find_winner(hand, player_name)
            hand_desc = f" with {winner.hand_name}" if winner and winner.hand_name else ""
            return f"You won {pot} at showdown{hand_desc}"
        elif outcome == "lost":
            # Who beat us?
            winner_names = [w.name for w in hand.winners if w.name != player_name]
            beater = winner_names[0] if winner_names else "opponent"
            winner_info = hand.winners[0] if hand.winners else None
            hand_desc = f" with {winner_info.hand_name}" if winner_info and winner_info.hand_name else ""
            return f"You lost {pot} at showdown — {beater} won{hand_desc}"

    # Big pot without showdown (bluff or steal)
    if big_blind and hand.pot_size >= big_blind * 10:
        if outcome == "won":
            return f"You took down a {pot} pot without showdown"
        elif outcome == "lost":
            return f"You folded in a {pot} pot"

    # Simple preflop fold — not notable
    if (len(player_actions) == 1 and player_actions[0].action == "fold"
            and player_actions[0].phase == "PRE_FLOP"):
        return None

    # Won a modest pot
    if outcome == "won":
        return f"You won {pot}"

    return None


# ---------------------------------------------------------------------------
# Hand description helpers (one per hand type)
# ---------------------------------------------------------------------------

def _describe_pair(
    hole_cards: List[Card],
    community_cards: List[Card],
    hand_values: List[int],
    kicker_values: List[int],
    **_,
) -> List[str]:
    """Describe One Pair."""
    lines = []
    if not hand_values:
        return lines

    pair_rank = hand_values[0]
    pair_name = rank_to_display(pair_rank)

    hole_match = [c for c in hole_cards if c.value == pair_rank]
    board_match = [c for c in community_cards if c.value == pair_rank]

    if len(hole_match) == 2:
        lines.append(f"You have a pocket pair of {pair_name}'s ({_cs(hole_match[0])}, {_cs(hole_match[1])}).")
    elif len(hole_match) == 1 and len(board_match) >= 1:
        lines.append(
            f"Your {_cs(hole_match[0])} pairs with {_cs(board_match[0])} on the board."
        )
    elif len(board_match) >= 2:
        lines.append(f"The board has a pair of {pair_name}'s ({_cs(board_match[0])}, {_cs(board_match[1])}).")

    _add_kickers(lines, kicker_values, hole_cards, community_cards)
    return lines


def _describe_two_pair(
    hole_cards: List[Card],
    community_cards: List[Card],
    hand_values: List[int],
    kicker_values: List[int],
    **_,
) -> List[str]:
    """Describe Two Pair."""
    lines = []
    if len(hand_values) < 4:
        return lines

    # hand_values for two pair from HandEvaluator is [h, l, h, l] (interleaved)
    # Extract the two distinct pair ranks
    pairs = sorted(set(hand_values), reverse=True)

    for pair_rank in pairs:
        pair_name = rank_to_display(pair_rank)
        hole_match = [c for c in hole_cards if c.value == pair_rank]
        board_match = [c for c in community_cards if c.value == pair_rank]

        if len(hole_match) == 2:
            lines.append(f"Pocket {pair_name}'s ({_cs(hole_match[0])}, {_cs(hole_match[1])}).")
        elif len(hole_match) == 1 and len(board_match) >= 1:
            lines.append(
                f"Your {_cs(hole_match[0])} pairs with {_cs(board_match[0])} on the board."
            )
        elif len(board_match) >= 2:
            lines.append(f"Board pair of {pair_name}'s.")

    _add_kickers(lines, kicker_values, hole_cards, community_cards)
    return lines


def _describe_three_of_a_kind(
    hole_cards: List[Card],
    community_cards: List[Card],
    hand_values: List[int],
    kicker_values: List[int],
    **_,
) -> List[str]:
    """Describe Three of a Kind (trips vs set)."""
    lines = []
    if not hand_values:
        return lines

    trip_rank = hand_values[0]
    trip_name = rank_to_display(trip_rank)
    hole_match = [c for c in hole_cards if c.value == trip_rank]
    board_match = [c for c in community_cards if c.value == trip_rank]

    if len(hole_match) == 2:
        lines.append(
            f"You have a SET of {trip_name}'s — pocket pair ({_cs(hole_match[0])}, {_cs(hole_match[1])}) "
            f"plus {_cs(board_match[0])} on the board. Sets are well-disguised."
        )
    elif len(hole_match) == 1 and len(board_match) >= 2:
        lines.append(
            f"You have TRIPS — your {_cs(hole_match[0])} with the pair of {trip_name}'s on the board "
            f"({_cs(board_match[0])}, {_cs(board_match[1])}). "
            f"Note: opponents with a {trip_name} also have trips."
        )
    elif len(board_match) >= 3:
        lines.append(f"Three {trip_name}'s are on the board — all players share this.")

    _add_kickers(lines, kicker_values, hole_cards, community_cards)
    return lines


def _describe_straight(
    hole_cards: List[Card],
    community_cards: List[Card],
    hand_values: List[int],
    **_,
) -> List[str]:
    """Describe a Straight."""
    lines = []
    if not hand_values:
        return lines

    straight_ranks = hand_values
    # Handle wheel (A-2-3-4-5): Ace is value 1 in hand_values but 14 in Card.value
    ranks_for_check = _ace_low_to_high(straight_ranks)

    hole_in_straight = [c for c in hole_cards if c.value in ranks_for_check]
    board_in_straight = [c for c in community_cards if c.value in ranks_for_check]

    rank_strs = [rank_to_display(v) for v in sorted(straight_ranks)]
    lines.append(f"Straight: {'-'.join(rank_strs)}")

    if hole_in_straight:
        hole_strs = [_cs(c) for c in hole_in_straight]
        lines.append(f"Your {', '.join(hole_strs)} contribute to the straight.")
    else:
        lines.append("The straight is entirely on the board — all players share it.")

    # Check if it's the nut straight (Ace-high, not wheel)
    if straight_ranks and max(straight_ranks) == 14:
        lines.append("This is the nut straight (Ace-high).")

    return lines


def _describe_flush(
    hole_cards: List[Card],
    community_cards: List[Card],
    hand_values: List[int],
    flush_suit: Optional[str] = None,
    **_,
) -> List[str]:
    """Describe a Flush."""
    lines = []
    if not flush_suit:
        return lines

    hole_suited = [c for c in hole_cards if c.suit == flush_suit]
    board_suited = [c for c in community_cards if c.suit == flush_suit]

    lines.append(f"Flush in {flush_suit}.")
    if hole_suited:
        hole_strs = [_cs(c) for c in hole_suited]
        lines.append(f"Your {', '.join(hole_strs)} contribute to the flush.")
        if len(board_suited) >= 4:
            lines.append("Four suited cards on the board — opponents may also have a flush.")
    else:
        lines.append("The flush is entirely on the board — all players share it.")

    # Nut flush check
    if hole_suited and any(c.value == 14 for c in hole_suited):
        lines.append("You have the nut flush (Ace-high).")

    return lines


def _describe_full_house(
    hole_cards: List[Card],
    community_cards: List[Card],
    hand_values: List[int],
    **_,
) -> List[str]:
    """Describe a Full House."""
    lines = []
    if len(hand_values) < 5:
        return lines

    trip_rank = hand_values[0]
    pair_rank = hand_values[3]
    trip_name = rank_to_display(trip_rank)
    pair_name = rank_to_display(pair_rank)

    lines.append(f"Full House: {trip_name}'s full of {pair_name}'s.")

    hole_in_trips = [c for c in hole_cards if c.value == trip_rank]
    hole_in_pair = [c for c in hole_cards if c.value == pair_rank]

    contributions = []
    if hole_in_trips:
        contributions.append(f"{', '.join(_cs(c) for c in hole_in_trips)} in the trips")
    if hole_in_pair:
        contributions.append(f"{', '.join(_cs(c) for c in hole_in_pair)} in the pair")

    if contributions:
        lines.append(f"Your hole cards: {'; '.join(contributions)}.")
    else:
        lines.append("The full house is entirely on the board.")

    return lines


def _describe_four_of_a_kind(
    hole_cards: List[Card],
    community_cards: List[Card],
    hand_values: List[int],
    kicker_values: List[int],
    **_,
) -> List[str]:
    """Describe Four of a Kind."""
    lines = []
    if not hand_values:
        return lines

    quad_rank = hand_values[0]
    quad_name = rank_to_display(quad_rank)
    hole_match = [c for c in hole_cards if c.value == quad_rank]

    lines.append(f"Four of a Kind: {quad_name}'s.")
    if len(hole_match) == 2:
        lines.append("You hold a pocket pair — both your cards are in the quads.")
    elif len(hole_match) == 1:
        lines.append(f"Your {_cs(hole_match[0])} completes the quads with three on the board.")
    else:
        lines.append("The quads are entirely on the board.")

    _add_kickers(lines, kicker_values, hole_cards, community_cards)
    return lines


def _describe_straight_flush(
    hole_cards: List[Card],
    community_cards: List[Card],
    hand_values: List[int],
    flush_suit: Optional[str] = None,
    **_,
) -> List[str]:
    """Describe a Straight Flush."""
    lines = []
    rank_strs = [rank_to_display(v) for v in sorted(hand_values)]
    suit_label = flush_suit or "unknown suit"
    lines.append(f"Straight Flush: {'-'.join(rank_strs)} of {suit_label}.")

    # Handle wheel (A-2-3-4-5): Ace is value 1 in hand_values but 14 in Card.value
    values_for_check = _ace_low_to_high(hand_values)
    hole_in = [c for c in hole_cards if c.value in values_for_check and c.suit == flush_suit]
    if hole_in:
        lines.append(f"Your {', '.join(_cs(c) for c in hole_in)} contribute.")
    return lines


def _describe_royal_flush(
    hole_cards: List[Card],
    hand_values: List[int],
    flush_suit: Optional[str] = None,
    **_,
) -> List[str]:
    """Describe a Royal Flush."""
    lines = [f"Royal Flush in {flush_suit or 'unknown suit'}! The best possible hand."]
    hole_in = [c for c in hole_cards if c.value in hand_values and c.suit == flush_suit]
    if hole_in:
        lines.append(f"Your {', '.join(_cs(c) for c in hole_in)} are part of the royal flush.")
    return lines


def _describe_high_card(
    hole_cards: List[Card],
    community_cards: List[Card],
    kicker_values: List[int],
    **_,
) -> List[str]:
    """Describe High Card (no made hand)."""
    lines = []
    if not hole_cards:
        return lines

    high_hole = max(hole_cards, key=lambda c: c.value)
    lines.append(f"No made hand. Your highest card is {_cs(high_hole)}.")

    # Check for draws
    all_cards = list(hole_cards) + list(community_cards)
    draws = _detect_draws(hole_cards, community_cards)
    if draws:
        lines.extend(draws)

    return lines


# Map hand_rank (1-10) to descriptor functions
_HAND_DESCRIBERS = {
    1: _describe_royal_flush,
    2: _describe_straight_flush,
    3: _describe_four_of_a_kind,
    4: _describe_full_house,
    5: _describe_flush,
    6: _describe_straight,
    7: _describe_three_of_a_kind,
    8: _describe_two_pair,
    9: _describe_pair,
    10: _describe_high_card,
}


# ---------------------------------------------------------------------------
# Draw detection (for high-card / marginal hands)
# ---------------------------------------------------------------------------

def _detect_draws(hole_cards: List[Card], community_cards: List[Card]) -> List[str]:
    """Detect flush and straight draws."""
    draws = []
    all_cards = list(hole_cards) + list(community_cards)

    # Flush draw: 4 cards of same suit
    suit_counts = Counter(c.suit for c in all_cards)
    for suit, count in suit_counts.items():
        if count == 4:
            hole_suited = [c for c in hole_cards if c.suit == suit]
            if hole_suited:
                draws.append(
                    f"Flush draw in {suit} — need one more {suit} card. "
                    f"Your {', '.join(_cs(c) for c in hole_suited)} contribute."
                )

    # Open-ended straight draw: 4 consecutive ranks
    all_values = sorted(set(c.value for c in all_cards))
    for i in range(len(all_values) - 3):
        window = all_values[i:i + 4]
        if window[-1] - window[0] == 3 and len(window) == 4:
            hole_in_run = [c for c in hole_cards if c.value in window]
            if hole_in_run:
                rank_strs = [rank_to_display(v) for v in window]
                draws.append(
                    f"Straight draw ({'-'.join(rank_strs)}) — need one more card on either end."
                )
                break  # Only report the best straight draw

    return draws


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _ace_low_to_high(values: List[int]) -> List[int]:
    """Convert Ace-low values (1) to Ace-high (14) for Card.value matching.

    HandEvaluator represents Aces as 1 in wheel straights (A-2-3-4-5),
    but Card objects always use value=14 for Aces.
    """
    if 1 in values:
        return [14 if v == 1 else v for v in values]
    return values


def _cs(card: Card) -> str:
    """Card to short string: 'Ah', 'Kd', etc."""
    return str(card)


def _add_kickers(
    lines: List[str],
    kicker_values: List[int],
    hole_cards: List[Card],
    community_cards: List[Card],
) -> None:
    """Add kicker information if relevant."""
    if not kicker_values:
        return

    kicker_strs = []
    for kv in kicker_values[:2]:  # Show top 2 kickers max
        # Find which card is the kicker
        hole_kicker = next((c for c in hole_cards if c.value == kv), None)
        board_kicker = next((c for c in community_cards if c.value == kv), None)
        if hole_kicker:
            kicker_strs.append(f"{_cs(hole_kicker)} (yours)")
        elif board_kicker:
            kicker_strs.append(f"{_cs(board_kicker)} (board)")
        else:
            kicker_strs.append(rank_to_display(kv))

    if kicker_strs:
        lines.append(f"Kicker{'s' if len(kicker_strs) > 1 else ''}: {', '.join(kicker_strs)}.")


def _summarize_hole_card_usage(
    hole_cards: List[Card],
    hand_values: List[int],
    kicker_values: List[int],
    hand_rank: int,
    flush_suit: Optional[str],
) -> Optional[str]:
    """Summarize how many hole cards are active in the best hand."""
    active_count = 0
    for card in hole_cards:
        if card.value in hand_values:
            active_count += 1
        elif card.value in kicker_values:
            active_count += 1
        elif flush_suit and card.suit == flush_suit and hand_rank == 5:
            active_count += 1

    if active_count == 2:
        return "Both of your hole cards are active in your hand."
    elif active_count == 1:
        return "One of your hole cards is active in your hand."
    else:
        return "Your hand is entirely on the board — opponents share it."


def _fmt_amount(amount: int, big_blind: Optional[int]) -> str:
    """Format an amount as BB or dollars."""
    if big_blind and big_blind > 0:
        bb = amount / big_blind
        if bb == int(bb):
            return f"{int(bb)} BB"
        return f"{bb:.1f} BB"
    return f"${amount}"


def _format_phase_header(phase: str, cards: List[str]) -> str:
    """Format a phase header with community cards."""
    display = phase.replace("_", "-")
    if cards:
        return f"{display} [{' '.join(cards)}]"
    return display


def _split_community_by_phase(community: List[str]) -> Dict[str, List[str]]:
    """Split community cards into which phase they appeared."""
    result: Dict[str, List[str]] = {}
    if len(community) >= 3:
        result["FLOP"] = list(community[:3])
    if len(community) >= 4:
        result["TURN"] = [community[3]]
    if len(community) >= 5:
        result["RIVER"] = [community[4]]
    return result


def _format_actions(actions: list, big_blind: Optional[int]) -> List[str]:
    """Format a list of RecordedAction into readable strings."""
    parts = []
    for a in actions:
        name = a.player_name
        if a.action == "fold":
            parts.append(f"{name} folded.")
        elif a.action == "check":
            parts.append(f"{name} checked.")
        elif a.action == "call":
            amt = _fmt_amount(a.amount, big_blind)
            parts.append(f"{name} called {amt}.")
        elif a.action == "raise":
            amt = _fmt_amount(a.amount, big_blind)
            parts.append(f"{name} raised to {amt}.")
        elif a.action == "all_in":
            amt = _fmt_amount(a.amount, big_blind)
            parts.append(f"{name} went all-in for {amt}.")
        else:
            parts.append(f"{name} {a.action}.")
    return parts


def _find_winner(hand, player_name: str):
    """Find WinnerInfo for a specific player, or None."""
    for w in hand.winners:
        if w.name == player_name:
            return w
    return None
