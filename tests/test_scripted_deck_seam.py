"""The one-shot provided-deck seam on the state machine.

Scripted scenes (career Scene-0) need to deal FIXED cards. `provide_hand_deck`
sets a pre-stacked deck that replaces the random shuffle for exactly one hand,
then clears. Deal order is sequential pairs by player index (verified in
`poker_game.deal_hole_cards`), so a stacked deck pins every hole card + board.
"""

from __future__ import annotations

import pytest

from core.card import Card
from poker.poker_game import create_deck, initialize_game_state
from poker.poker_state_machine import PokerStateMachine


def _k(card) -> tuple:
    return (card.rank, card.suit)


def _stacked_deck(hole_pairs, board, filler_seed=7):
    """A 52-card deck where players[i] gets hole_pairs[i] and the board follows.

    hole_pairs: list of 2-card short-string lists, in player order.
    board: 5 short-string cards (flop+turn+river).
    Remaining slots filled from a shuffled standard deck minus the placed cards.
    """
    placed = [Card.from_short(s) for pair in hole_pairs for s in pair]
    placed += [Card.from_short(s) for s in board]
    placed_keys = {_k(c) for c in placed}
    filler = [
        c for c in create_deck(shuffled=True, random_seed=filler_seed) if _k(c) not in placed_keys
    ]
    # Order: all hole pairs (seat 0,1,2...), then board, then filler.
    ordered = []
    for pair in hole_pairs:
        ordered += [Card.from_short(s) for s in pair]
    ordered += [Card.from_short(s) for s in board]
    ordered += filler
    return tuple(ordered)


def _deal_first_hand(sm: PokerStateMachine, max_iter=60) -> PokerStateMachine:
    """Advance until hole cards are dealt (someone has a non-empty hand)."""
    for _ in range(max_iter):
        if any(p.hand for p in sm.game_state.players):
            return sm
        sm = sm.advance()
    return sm


def test_provided_deck_deals_scripted_hole_cards_on_first_hand():
    gs = initialize_game_state(player_names=["Sal", "Larry"], human_name="You")
    # players order = [You, Sal, Larry] (human first in initialize_game_state).
    deck = _stacked_deck(
        hole_pairs=[["Ah", "Kc"], ["7d", "2s"], ["Qd", "Jd"]],
        board=["Kd", "9s", "4c", "6d", "2h"],
    )
    sm = PokerStateMachine(game_state=gs)
    sm.provide_hand_deck(deck)
    sm = _deal_first_hand(sm)

    by_name = {p.name: p for p in sm.game_state.players}
    assert {_k(c) for c in by_name["You"].hand} == {_k(Card.from_short(s)) for s in ["Ah", "Kc"]}
    assert {_k(c) for c in by_name["Sal"].hand} == {_k(Card.from_short(s)) for s in ["7d", "2s"]}
    assert {_k(c) for c in by_name["Larry"].hand} == {_k(Card.from_short(s)) for s in ["Qd", "Jd"]}


def test_provided_deck_is_consumed_after_one_hand():
    gs = initialize_game_state(player_names=["Sal", "Larry"], human_name="You")
    sm = PokerStateMachine(game_state=gs)
    deck = _stacked_deck(
        hole_pairs=[["Ah", "Kc"], ["7d", "2s"], ["Qd", "Jd"]],
        board=["Kd", "9s", "4c", "6d", "2h"],
    )
    sm.provide_hand_deck(deck)
    # After providing + the wrapper sets it; once consumed the flag clears.
    assert sm._state.hand_deck_provided is True
    sm = _deal_first_hand(sm)
    assert sm._state.hand_deck_provided is False  # consumed by the deal
    assert sm._state.current_hand_deck is None


def test_no_provided_deck_is_normal_random_deal():
    gs = initialize_game_state(player_names=["Sal", "Larry"], human_name="You")
    sm = PokerStateMachine(game_state=gs)
    sm = _deal_first_hand(sm)
    # Everyone got 2 cards; nothing asserted about which (random) — just that
    # the seam is inert when no deck is provided.
    assert all(len(p.hand) == 2 for p in sm.game_state.players)
    assert sm._state.hand_deck_provided is False
