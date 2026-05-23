"""Tests for STRONG_FOLD_SHOWN detection.

Semantic: postflop fold where the folder's hand at the final board
would have lost to the bettor's revealed showdown hand. Mirror of
BLUFFED_OFF: same scan, opposite outcome.

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
        name=name, starting_stack=stack, position="BTN", is_human=False,
    )


def _action(name, action, amount, phase="FLOP", pot_after=0):
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


# Reference setup:
#   board: 2c 5h 8c Tc Jd  (dry, no obvious draws)
#   aces:   Ah Ad → pair of aces (hand_rank 9, beats kings on kicker)
#   kings:  Kd Kh → pair of kings (hand_rank 9)
#   high:   7s 6s → high card jack (hand_rank 10)
COMMUNITY = ("2c", "5h", "8c", "Tc", "Jd")
HOLE_ACES = ["Ah", "Ad"]
HOLE_KINGS = ["Kd", "Kh"]
HOLE_HIGH = ["7s", "6s"]


class TestStrongFoldShownFires:
    def test_postflop_fold_to_bettor_who_had_it(self):
        # 3-way: carol folds flop to bob's bet. alice and bob check down;
        # at showdown alice wins (aces) over bob (kings). carol had
        # high-card-jack — would have been crushed. STRONG_FOLD_SHOWN
        # fires for carol's view of bob (the bettor she correctly folded to).
        hand = _build(
            hole_cards={
                "alice": HOLE_ACES,
                "bob": HOLE_KINGS,
                "carol": HOLE_HIGH,
            },
            community=COMMUNITY,
            actions=[
                _action("bob", "bet", 100, phase="FLOP", pot_after=100),
                _action("carol", "fold", 0, phase="FLOP", pot_after=100),
                _action("alice", "call", 100, phase="FLOP", pot_after=200),
                _action("alice", "check", 0, phase="TURN", pot_after=200),
                _action("bob", "check", 0, phase="TURN", pot_after=200),
                _action("alice", "check", 0, phase="RIVER", pot_after=200),
                _action("bob", "check", 0, phase="RIVER", pot_after=200),
            ],
            winners=[WinnerInfo(
                name="alice", amount_won=200, hand_name="Pair", hand_rank=9,
            )],
        )

        events = HandOutcomeDetector().detect_events(hand)
        sfs = [e for e in events if e.event is RelationshipEvent.STRONG_FOLD_SHOWN]
        assert len(sfs) == 1
        assert sfs[0].actor_id == "carol"
        assert sfs[0].target_id == "bob"


class TestStrongFoldShownDoesNotFire:
    def test_folder_would_have_won_does_not_fire(self):
        # Inverse setup: carol (the folder) had aces. bob bet with junk.
        # carol's would-have-been hand BEATS bob — that's BLUFFED_OFF,
        # not STRONG_FOLD_SHOWN.
        hand = _build(
            hole_cards={
                "alice": HOLE_KINGS,
                "bob": HOLE_HIGH,
                "carol": HOLE_ACES,
            },
            community=COMMUNITY,
            actions=[
                _action("bob", "bet", 100, phase="FLOP", pot_after=100),
                _action("carol", "fold", 0, phase="FLOP", pot_after=100),
                _action("alice", "call", 100, phase="FLOP", pot_after=200),
                _action("alice", "check", 0, phase="TURN", pot_after=200),
                _action("bob", "check", 0, phase="TURN", pot_after=200),
                _action("alice", "check", 0, phase="RIVER", pot_after=200),
                _action("bob", "check", 0, phase="RIVER", pot_after=200),
            ],
            winners=[WinnerInfo(
                name="alice", amount_won=200, hand_name="Pair", hand_rank=9,
            )],
        )

        events = HandOutcomeDetector().detect_events(hand)
        sfs = [e for e in events if e.event is RelationshipEvent.STRONG_FOLD_SHOWN]
        assert sfs == []

    def test_preflop_fold_does_not_fire(self):
        # carol folds preflop — postflop fold requirement not satisfied.
        hand = _build(
            hole_cards={
                "alice": HOLE_ACES,
                "bob": HOLE_KINGS,
                "carol": HOLE_HIGH,
            },
            community=COMMUNITY,
            actions=[
                _action("bob", "raise", 50, phase="PRE_FLOP", pot_after=50),
                _action("carol", "fold", 0, phase="PRE_FLOP", pot_after=50),
                _action("alice", "call", 50, phase="PRE_FLOP", pot_after=100),
                _action("alice", "check", 0, phase="FLOP", pot_after=100),
                _action("bob", "check", 0, phase="FLOP", pot_after=100),
                _action("alice", "check", 0, phase="TURN", pot_after=100),
                _action("bob", "check", 0, phase="TURN", pot_after=100),
                _action("alice", "check", 0, phase="RIVER", pot_after=100),
                _action("bob", "check", 0, phase="RIVER", pot_after=100),
            ],
            winners=[WinnerInfo(
                name="alice", amount_won=100, hand_name="Pair", hand_rank=9,
            )],
        )

        events = HandOutcomeDetector().detect_events(hand)
        sfs = [e for e in events if e.event is RelationshipEvent.STRONG_FOLD_SHOWN]
        assert sfs == []

    def test_bettor_not_at_showdown_does_not_fire(self):
        # bob bets the flop, carol folds, bob then folds to alice's raise.
        # The bettor (bob) didn't reach showdown — can't verify his hand
        # so STRONG_FOLD_SHOWN can't fire on the carol → bob edge.
        hand = _build(
            hole_cards={
                "alice": HOLE_ACES,
                # bob folded — cards stripped in normal flow but the
                # detector also checks `showdown_with_cards`, which
                # excludes fold_actors regardless. Keep cards out to
                # match the canonical fold-strip behavior.
                "carol": HOLE_HIGH,
            },
            community=COMMUNITY,
            actions=[
                _action("bob", "bet", 100, phase="FLOP", pot_after=100),
                _action("carol", "fold", 0, phase="FLOP", pot_after=100),
                _action("alice", "raise", 300, phase="FLOP", pot_after=400),
                _action("bob", "fold", 0, phase="FLOP", pot_after=400),
            ],
            winners=[WinnerInfo(
                name="alice", amount_won=400, hand_name="Pair", hand_rank=9,
            )],
            was_showdown=False,
        )

        events = HandOutcomeDetector().detect_events(hand)
        sfs = [e for e in events if e.event is RelationshipEvent.STRONG_FOLD_SHOWN]
        assert sfs == []
