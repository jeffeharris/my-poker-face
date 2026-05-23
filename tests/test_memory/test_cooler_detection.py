"""Tests for COOLER detection and DOMINATED_SHOWDOWN mutual exclusion.

COOLER fires when both showdown hands are strong (hand_rank ≤ 7, i.e.,
three-of-a-kind or better) AND the winner's category is strictly
stronger AND the loser was committed postflop. The two events are
mutually exclusive by construction — DOMINATED_SHOWDOWN skips the
both-strong case so the same outcome doesn't double-emit.

Fixtures use real cards + HandEvaluator so the rank-band gate
matches the detector's actual evaluation.
"""

from __future__ import annotations

from datetime import datetime

from poker.memory.hand_history import (
    PlayerHandInfo,
    RecordedAction,
    RecordedHand,
    WinnerInfo,
)
from poker.memory.hand_outcome_detector import HandOutcomeDetector
from poker.memory.relationship_events import RelationshipEvent


def _player(name: str, stack: int = 1000) -> PlayerHandInfo:
    return PlayerHandInfo(
        name=name, starting_stack=stack, position="BTN", is_human=False,
    )


def _action(name, action, amount, phase="RIVER", pot_after=0):
    return RecordedAction(
        player_name=name, action=action, amount=amount,
        phase=phase, pot_after=pot_after,
    )


def _build(
    *,
    hole_cards: dict,
    community: tuple,
    actions: list,
    winners: list,
    pot_size: int = 200,
    was_showdown: bool = True,
    hand_number: int = 1,
) -> RecordedHand:
    players = [_player(name) for name in hole_cards.keys()]
    return RecordedHand(
        game_id="g1",
        hand_number=hand_number,
        timestamp=datetime(2026, 5, 18, 12, 0),
        players=tuple(players),
        hole_cards=hole_cards,
        community_cards=community,
        actions=tuple(actions),
        winners=tuple(winners),
        pot_size=pot_size,
        was_showdown=was_showdown,
    )


# Postflop call pattern shared by every committed-loser test.
COMMITTED_ACTIONS = [
    _action("alice", "bet", 100, phase="FLOP", pot_after=200),
    _action("bob", "call", 100, phase="FLOP", pot_after=300),
    _action("alice", "check", 0, phase="TURN", pot_after=300),
    _action("bob", "check", 0, phase="TURN", pot_after=300),
    _action("alice", "check", 0, phase="RIVER", pot_after=300),
    _action("bob", "check", 0, phase="RIVER", pot_after=300),
]


class TestCoolerFires:
    def test_straight_over_set(self):
        # Board: 6h 7c 8s 2d Kh (no paired board)
        # alice: 9d Th → straight (rank 6)
        # bob:   8d 8h → set of eights (rank 7)
        # Both strong (≤ 7), category jump (6 < 7). COOLER fires.
        hand = _build(
            hole_cards={"alice": ["9d", "Th"], "bob": ["8d", "8h"]},
            community=("6h", "7c", "8s", "2d", "Kh"),
            actions=COMMITTED_ACTIONS,
            winners=[WinnerInfo(
                name="alice", amount_won=300, hand_name="Straight", hand_rank=6,
            )],
        )

        events = HandOutcomeDetector().detect_events(hand)
        coolers = [e for e in events if e.event is RelationshipEvent.COOLER]
        assert len(coolers) == 1
        assert coolers[0].actor_id == "bob"
        assert coolers[0].target_id == "alice"

    def test_full_house_over_flush(self):
        # Board: 7s Ks Qs 5h 5d (board pairs fives; three spades)
        # alice: Kc Kh → full house kings over fives (rank 4)
        # bob:   Ts 9s → K-high spade flush (rank 5)
        # Both strong (≤ 7), category jump (4 < 5). COOLER fires.
        hand = _build(
            hole_cards={"alice": ["Kc", "Kh"], "bob": ["Ts", "9s"]},
            community=("7s", "Ks", "Qs", "5h", "5d"),
            actions=COMMITTED_ACTIONS,
            winners=[WinnerInfo(
                name="alice", amount_won=300, hand_name="Full House", hand_rank=4,
            )],
        )

        events = HandOutcomeDetector().detect_events(hand)
        coolers = [e for e in events if e.event is RelationshipEvent.COOLER]
        assert len(coolers) == 1


class TestCoolerDoesNotFire:
    def test_equal_strong_categories_does_not_fire(self):
        # Board: 8h 5d 2s Jc 4h (no paired board)
        # alice: 8d 8s → set of eights (rank 7)
        # bob:   5s 5c → set of fives (rank 7)
        # Both strong but SAME hand_rank — no category jump.
        # The hand is decided on kickers, which the detector doesn't
        # gate on. No COOLER (strict inequality required).
        hand = _build(
            hole_cards={"alice": ["8d", "8s"], "bob": ["5s", "5c"]},
            community=("8h", "5d", "2s", "Jc", "4h"),
            actions=COMMITTED_ACTIONS,
            winners=[WinnerInfo(
                name="alice", amount_won=300, hand_name="Three of a kind", hand_rank=7,
            )],
        )

        events = HandOutcomeDetector().detect_events(hand)
        coolers = [e for e in events if e.event is RelationshipEvent.COOLER]
        assert coolers == []

    def test_uncommitted_loser_does_not_fire(self):
        # Same straight-over-set matchup as the fires-test, but bob
        # never calls a postflop bet. Pure check-down → COOLER skips.
        hand = _build(
            hole_cards={"alice": ["9d", "Th"], "bob": ["8d", "8h"]},
            community=("6h", "7c", "8s", "2d", "Kh"),
            actions=[
                _action("alice", "check", 0, phase="FLOP", pot_after=50),
                _action("bob", "check", 0, phase="FLOP", pot_after=50),
                _action("alice", "check", 0, phase="TURN", pot_after=50),
                _action("bob", "check", 0, phase="TURN", pot_after=50),
                _action("alice", "check", 0, phase="RIVER", pot_after=50),
                _action("bob", "check", 0, phase="RIVER", pot_after=50),
            ],
            winners=[WinnerInfo(
                name="alice", amount_won=50, hand_name="Straight", hand_rank=6,
            )],
        )

        events = HandOutcomeDetector().detect_events(hand)
        coolers = [e for e in events if e.event is RelationshipEvent.COOLER]
        assert coolers == []


class TestDominatedShowdownMutualExclusion:
    """COOLER and DOMINATED_SHOWDOWN are mutually exclusive — they
    target different emotional shapes of the "loser was outclassed"
    outcome and must not both fire on the same hand.
    """

    def test_strong_v_strong_fires_cooler_not_dominated(self):
        # Same straight-over-set matchup. Verify DOMINATED does NOT
        # fire (the both-strong exclusion in _detect_dominated_showdown
        # should defer to COOLER).
        hand = _build(
            hole_cards={"alice": ["9d", "Th"], "bob": ["8d", "8h"]},
            community=("6h", "7c", "8s", "2d", "Kh"),
            actions=COMMITTED_ACTIONS,
            winners=[WinnerInfo(
                name="alice", amount_won=300, hand_name="Straight", hand_rank=6,
            )],
        )

        events = HandOutcomeDetector().detect_events(hand)
        dom = [e for e in events if e.event is RelationshipEvent.DOMINATED_SHOWDOWN]
        coolers = [e for e in events if e.event is RelationshipEvent.COOLER]
        assert dom == []
        assert len(coolers) == 1

    def test_set_over_two_pair_fires_dominated_not_cooler(self):
        # Board: 8h 5d 2s Jc 4h (no paired board)
        # alice: 8d 8s → set of eights (rank 7 — strong)
        # bob:   Js Jd → two pair (J's and 5's? no — bob has JsJd so
        #                board J + bob JJ = trips. Different hand.)
        # Try bob: Jh 5c → wait need bob to have two pair.
        # Use board with one pair: 8h 5d 2s 8c Jc.
        #   alice: Kd Kh → wait one pair (KK + board 8-8) → two pair K's + 8's, rank 8
        #   bob:   Jh Js → wait need set or better for "strong" but
        #                  Jh Js + board 8-5-2-8-J = pair of J's plus
        #                  board pair of 8's = two pair J's + 8's, rank 8.
        # Need ALICE to have a SET (rank 7) and BOB to have TWO PAIR (rank 8).
        # Board: 8h 5d 2s Jc 4h (unpaired)
        # alice: 8d 8s → set of eights (rank 7, strong)
        # bob:   Jh 5h → two pair (Js + 5s from board + pair of 5s with 5h) = wait, that's 5-5 + J on board not paired
        # Hmm. Try this board: 8h 5d Js 5c 4h
        # alice: 8d 8s → board has 8h-5d-Js-5c-4h, alice with 8d8s = full house? alice 8-8 + board 5-5 + 8h-Js-4h = 8-8-8-5-5 = full house rank 4. Too strong!
        # Try board where alice can have set without board pair:
        # Board: 8h 5d 2s Jc 4h (no pairs)
        # alice: 8d 8s → set of 8s, rank 7
        # bob: Jh Jd → wait Js on board, so JJ with board J = trips. Not two pair.
        # Try bob: Jc 5c — wait Jc is on board.
        # Try bob: Th 5c → board has 8-5-2-J-4, bob has T-5 → pair of 5s + J kicker = pair, rank 9. NOT two pair.
        # bob: Jd Tc → pair of Js (board J + Jd), rank 9. Not two pair.
        # bob: 5s 4c → wait 4h on board. 5-5 + 4-4? bob has 5s 4c, board 8-5-2-J-4, total 8-5-2-J-4-5-4. Two pair 5s and 4s with J kicker. RANK 8.
        # So alice (88) vs bob (5s 4c) on 8-5-2-J-4 board: alice set rank 7, bob two-pair rank 8.
        hand = _build(
            hole_cards={"alice": ["8d", "8s"], "bob": ["5s", "4c"]},
            community=("8h", "5d", "2c", "Jc", "4h"),
            actions=COMMITTED_ACTIONS,
            winners=[WinnerInfo(
                name="alice", amount_won=300, hand_name="Three of a kind", hand_rank=7,
            )],
        )

        events = HandOutcomeDetector().detect_events(hand)
        dom = [e for e in events if e.event is RelationshipEvent.DOMINATED_SHOWDOWN]
        coolers = [e for e in events if e.event is RelationshipEvent.COOLER]
        # Loser (rank 8) is not in the strong band → COOLER skips,
        # DOMINATED fires.
        assert len(dom) == 1
        assert coolers == []
