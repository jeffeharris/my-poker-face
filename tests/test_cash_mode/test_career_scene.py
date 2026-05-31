"""Tests for the Scene-0 in-table rig builder (`cash_mode/career_scene.py`)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

from core.card import Card
from cash_mode import career_scene as cs
from cash_mode.career_scene import ROLE_FISH, ROLE_HERO, ROLE_MENTOR, build_hand_deck
from poker.poker_game import deal_hole_cards, initialize_game_state
from poker.poker_state_machine import PokerStateMachine


def _k(c):
    return (c.rank, c.suit)


def _keys(shorts):
    return {_k(Card.from_short(s)) for s in shorts}


def test_build_hand_deck_places_holes_and_board_by_seat():
    hand = cs._BLUFF_CATCH
    # Pretend the live seating is: seat0=fish, seat1=hero, seat2=mentor.
    role_seats = {ROLE_FISH: 0, ROLE_HERO: 1, ROLE_MENTOR: 2}
    deck = build_hand_deck(hand, num_players=3, role_seats=role_seats)
    assert len(deck) == 52
    # Sequential pairs by seat. Bluff-catch = Moneymaker vs Farha: hero is Farha
    # (Q♠9♥ top pair), the fish barrels Moneymaker's king-high air (K♠7♥).
    assert {_k(deck[0]), _k(deck[1])} == _keys(["Ks", "7h"])  # fish at seat 0
    assert {_k(deck[2]), _k(deck[3])} == _keys(["Qs", "9h"])  # hero at seat 1
    assert {_k(deck[4]), _k(deck[5])} == _keys(["7s", "2c"])  # mentor at seat 2
    # Board follows all hole cards (3 players → positions 6..10).
    assert [_k(c) for c in deck[6:11]] == [_k(Card.from_short(s)) for s in hand.board]
    # No duplicates.
    assert len({_k(c) for c in deck}) == 52


def test_build_hand_deck_round_trips_through_the_dealer():
    """The whole point: dealing from the stacked deck yields the scripted hands."""
    gs = initialize_game_state(player_names=["Sal", "Larry"], human_name="You")
    # initialize_game_state seats the human first: [You, Sal, Larry].
    names = [p.name for p in gs.players]
    role_seats = {
        ROLE_HERO: names.index("You"),
        ROLE_MENTOR: names.index("Sal"),
        ROLE_FISH: names.index("Larry"),
    }
    deck = build_hand_deck(cs._BLUFF_CATCH, num_players=3, role_seats=role_seats)
    dealt = deal_hole_cards(gs.update(deck=deck))
    by_name = {p.name: p for p in dealt.players}
    assert {_k(c) for c in by_name["You"].hand} == _keys(["Qs", "9h"])
    assert {_k(c) for c in by_name["Larry"].hand} == _keys(["Ks", "7h"])
    assert {_k(c) for c in by_name["Sal"].hand} == _keys(["7s", "2c"])


def test_build_hand_deck_seam_end_to_end():
    """Provided to the state machine, the rigged deck deals at the table."""
    gs = initialize_game_state(player_names=["Sal", "Larry"], human_name="You")
    names = [p.name for p in gs.players]
    role_seats = {
        ROLE_HERO: names.index("You"),
        ROLE_MENTOR: names.index("Sal"),
        ROLE_FISH: names.index("Larry"),
    }
    deck = build_hand_deck(cs._BLUFF_CATCH, num_players=3, role_seats=role_seats)
    sm = PokerStateMachine(game_state=gs)
    sm.provide_hand_deck(deck)
    for _ in range(60):
        if any(p.hand for p in sm.game_state.players):
            break
        sm = sm.advance()
    by_name = {p.name: p for p in sm.game_state.players}
    assert {_k(c) for c in by_name["You"].hand} == _keys(["Qs", "9h"])


def test_script_shape():
    assert cs.script_length() >= 7
    assert cs.hand_for_index(0).rigged is False  # hand 1 is just poker
    lessons = {h.lesson for h in cs.SCENE0_SCRIPT if h.lesson}
    assert lessons == {"value", "bluff_catch", "discipline"}
    assert cs.hand_for_index(999) is None


def test_every_rigged_hand_builds_without_card_collisions():
    """Authoring guard: each rigged hand's cards (incl. Sal's junk) are distinct,
    so the stacked deck is a legal 52-card deck. build_hand_deck raises on dupes."""
    role_seats = {ROLE_HERO: 0, ROLE_MENTOR: 1, ROLE_FISH: 2}
    for i, hand in enumerate(cs.SCENE0_SCRIPT):
        if not hand.rigged:
            continue
        deck = build_hand_deck(hand, num_players=3, role_seats=role_seats)
        assert len(deck) == 52, f"hand {i}"
        assert len({_k(c) for c in deck}) == 52, f"hand {i} has duplicate cards"


def test_discipline_hand_judged_on_fold():
    d = cs.hand_for_index(6)
    assert d.lesson == "discipline"
    assert d.pass_when == "folded"
    # Discipline = Chan vs Seidel: the fish (Chan) flopped the nut straight with
    # J9; the hero has top pair (Seidel's Q7) and should fold.
    assert d.holes[ROLE_FISH] == ["Jh", "9s"]
    assert d.holes[ROLE_HERO] == ["Qc", "7c"]


# --- scripted-action resolver ------------------------------------------------


def test_fish_bluffs_when_checked_to():
    r = cs.resolve_scripted_action(
        intent="bluff", valid_actions=["check", "raise", "all_in"],
        cost_to_call=0, pot_total=40, stack=110, big_blind=2,
    )
    assert r["action"] == "raise"
    assert r["amount"] > 0


def test_fish_bluff_gives_up_when_bet_into():
    r = cs.resolve_scripted_action(
        intent="bluff", valid_actions=["fold", "call", "raise"],
        cost_to_call=20, pot_total=60, stack=110, big_blind=2,
    )
    assert r["action"] == "fold"


def test_mentor_folds_to_a_bet_but_checks_when_free():
    facing = cs.resolve_scripted_action(
        intent="fold", valid_actions=["fold", "call", "raise"],
        cost_to_call=4, pot_total=6, stack=100, big_blind=2,
    )
    assert facing["action"] == "fold"
    free = cs.resolve_scripted_action(
        intent="fold", valid_actions=["check", "raise"],
        cost_to_call=0, pot_total=6, stack=100, big_blind=2,
    )
    assert free["action"] == "check"


def test_fish_stays_cheap_preflop():
    facing = cs.resolve_scripted_action(
        intent="limp", valid_actions=["fold", "call", "raise"],
        cost_to_call=2, pot_total=3, stack=100, big_blind=2,
    )
    assert facing["action"] == "call"
    free = cs.resolve_scripted_action(
        intent="limp", valid_actions=["check", "raise"],
        cost_to_call=0, pot_total=4, stack=100, big_blind=2,
    )
    assert free["action"] == "check"


def test_shove_jams_full_stack_when_checked_to():
    r = cs.resolve_scripted_action(
        intent="shove", valid_actions=["check", "raise", "all_in"],
        cost_to_call=0, pot_total=40, stack=150, big_blind=2, allow_bust=True,
    )
    assert r["action"] == "raise"
    assert r["amount"] == 150  # raise-to the whole stack (all-in)


def test_passive_folds_to_all_in_normally_but_calls_it_off_on_a_bust_hand():
    # Default (no-bust guard): a sticky station folds rather than bust.
    folds = cs.resolve_scripted_action(
        intent="passive", valid_actions=["fold", "call"],
        cost_to_call=200, pot_total=300, stack=150, big_blind=2,
    )
    assert folds["action"] == "fold"
    # Finale (allow_bust): Larry calls the all-in off and busts.
    calls = cs.resolve_scripted_action(
        intent="passive", valid_actions=["fold", "call"],
        cost_to_call=200, pot_total=300, stack=150, big_blind=2, allow_bust=True,
    )
    assert calls["action"] == "call"


def test_finale_is_bust_ok_sal_set_vs_larry_top_pair():
    """The closing hand: Sal flops a set, Larry has top pair, Sal shoves the river."""
    f = cs.SCENE0_SCRIPT[-1]
    assert f.bust_ok is True
    assert f.lesson is None  # a showcase, not a judged lesson
    assert f.holes[ROLE_MENTOR] == ["7s", "7d"]  # Sal's hidden set
    assert f.holes[ROLE_FISH] == ["Kh", "Qd"]    # Larry's sticky top pair
    assert f.mentor_plan["RIVER"][0] == "shove"
    assert f.fish_plan["RIVER"] == "passive"     # + allow_bust → calls it off


def test_fish_passive_checks_then_calls():
    assert cs.resolve_scripted_action(
        intent="passive", valid_actions=["check", "raise"],
        cost_to_call=0, pot_total=10, stack=100, big_blind=2,
    )["action"] == "check"
    assert cs.resolve_scripted_action(
        intent="passive", valid_actions=["fold", "call", "raise"],
        cost_to_call=8, pot_total=20, stack=100, big_blind=2,
    )["action"] == "call"
