"""Tests for DOMINATED_SHOWDOWN detection.

Semantic: at showdown, a non-winner who was committed postflop
(called a bet/raise on FLOP/TURN/RIVER) shows down with a hand
whose category is strictly weaker than a winner's. "Materially
worse" = different hand_rank category, not just kicker difference.

Fixtures use real cards + HandEvaluator so the comparison matches
the detector's actual evaluation.
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
        name=name,
        starting_stack=stack,
        position="BTN",
        is_human=False,
    )


def _action(name, action, amount, phase="RIVER", pot_after=0):
    return RecordedAction(
        player_name=name,
        action=action,
        amount=amount,
        phase=phase,
        pot_after=pot_after,
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


# Reference setup:
#   board: 2c 5h 8c Tc Jd
#   strong: Ah Ad → pair of aces (hand_rank 9)
#   high:   7s 6s → high card jack (hand_rank 10)
#   kings:  Kd Kh → pair of kings (hand_rank 9; same category as aces)
COMMUNITY = ("2c", "5h", "8c", "Tc", "Jd")
HOLE_ACES = ["Ah", "Ad"]
HOLE_HIGH = ["7s", "6s"]
HOLE_KINGS = ["Kd", "Kh"]


class TestDominatedShowdownFires:
    def test_category_jump_with_postflop_call(self):
        # alice (pair) crushes bob (high card). bob called the flop bet
        # — committed. Expect DOMINATED_SHOWDOWN(bob → alice).
        hand = _build(
            hole_cards={"alice": HOLE_ACES, "bob": HOLE_HIGH},
            community=COMMUNITY,
            actions=[
                _action("alice", "bet", 50, phase="FLOP", pot_after=100),
                _action("bob", "call", 50, phase="FLOP", pot_after=150),
                _action("alice", "check", 0, phase="TURN", pot_after=150),
                _action("bob", "check", 0, phase="TURN", pot_after=150),
                _action("alice", "check", 0, phase="RIVER", pot_after=150),
                _action("bob", "check", 0, phase="RIVER", pot_after=150),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=150,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
        )

        events = HandOutcomeDetector().detect_events(hand)
        dom = [e for e in events if e.event is RelationshipEvent.DOMINATED_SHOWDOWN]
        assert len(dom) == 1
        assert dom[0].actor_id == "bob"
        assert dom[0].target_id == "alice"


class TestDominatedShowdownDoesNotFire:
    def test_same_category_does_not_fire(self):
        # alice (aces) vs bob (kings) — both pair (rank 9). Kicker-level
        # domination, not categorical. Should NOT emit.
        hand = _build(
            hole_cards={"alice": HOLE_ACES, "bob": HOLE_KINGS},
            community=COMMUNITY,
            actions=[
                _action("alice", "bet", 50, phase="FLOP", pot_after=100),
                _action("bob", "call", 50, phase="FLOP", pot_after=150),
                _action("alice", "check", 0, phase="TURN", pot_after=150),
                _action("bob", "check", 0, phase="TURN", pot_after=150),
                _action("alice", "check", 0, phase="RIVER", pot_after=150),
                _action("bob", "check", 0, phase="RIVER", pot_after=150),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=150,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
        )

        events = HandOutcomeDetector().detect_events(hand)
        dom = [e for e in events if e.event is RelationshipEvent.DOMINATED_SHOWDOWN]
        assert dom == []

    def test_passive_checkdown_does_not_fire(self):
        # bob never called a postflop bet — pure check-down to showdown.
        # The categorical gap is there but commitment isn't.
        hand = _build(
            hole_cards={"alice": HOLE_ACES, "bob": HOLE_HIGH},
            community=COMMUNITY,
            actions=[
                _action("alice", "check", 0, phase="FLOP", pot_after=50),
                _action("bob", "check", 0, phase="FLOP", pot_after=50),
                _action("alice", "check", 0, phase="TURN", pot_after=50),
                _action("bob", "check", 0, phase="TURN", pot_after=50),
                _action("alice", "check", 0, phase="RIVER", pot_after=50),
                _action("bob", "check", 0, phase="RIVER", pot_after=50),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=50,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
        )

        events = HandOutcomeDetector().detect_events(hand)
        dom = [e for e in events if e.event is RelationshipEvent.DOMINATED_SHOWDOWN]
        assert dom == []

    def test_no_showdown_does_not_fire(self):
        # bob folds preflop — no showdown, no detection.
        hand = _build(
            hole_cards={"alice": HOLE_ACES, "bob": HOLE_HIGH},
            community=COMMUNITY,
            actions=[
                _action("alice", "raise", 50, phase="PRE_FLOP", pot_after=50),
                _action("bob", "fold", 0, phase="PRE_FLOP", pot_after=50),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=50,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            was_showdown=False,
        )

        events = HandOutcomeDetector().detect_events(hand)
        dom = [e for e in events if e.event is RelationshipEvent.DOMINATED_SHOWDOWN]
        assert dom == []
