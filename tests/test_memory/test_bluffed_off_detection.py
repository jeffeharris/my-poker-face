"""Tests for BLUFFED_OFF detection (Phase 3 commit 6).

Semantic: actor folded postflop to a bettor who reached showdown
with a weaker hand than the actor would have had. Requires both
sides' card visibility and a completed board.

Fixtures use real cards + HandEvaluator so the comparison matches
the detector's actual evaluation.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

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


def _action(name, action, amount, phase="FLOP", pot_after=0):
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
#   board: 2c 5h 8c Tc Jd  (no obvious draws hit, no board pair)
#   alice: Ah Ad  → pair of aces (rank 9)
#   bob:   7s 6s  → high card jack (rank 10)
#   carol: Ks Kd  → pair of kings (rank 9 too, but with worse kickers
#                   if vs alice — both rank 9; depends on full
#                   evaluator output)
# For BLUFFED_OFF tests we need clear rank gaps:
#   strong: Ah Ad  → rank 9 (pair of aces) on the dry board
#   bluff:  7s 6s  → rank 10 (high card jack)
COMMUNITY = ("2c", "5h", "8c", "Tc", "Jd")
HOLE_STRONG = ["Ah", "Ad"]
HOLE_BLUFF = ["7s", "6s"]
HOLE_SHOWDOWN_WINNER = ["Kh", "Kd"]  # pair of kings, beats both


class TestBluffedOffFires:
    def test_postflop_fold_to_bluffer_who_loses_showdown(self):
        # 3-way: carol folds flop to bob's bet. alice and bob continue
        # to showdown; alice wins, bob's hand is revealed as a bluff.
        # carol had pocket aces — would have crushed both.
        hand = _build(
            hole_cards={
                "alice": HOLE_SHOWDOWN_WINNER,
                "bob": HOLE_BLUFF,
                "carol": HOLE_STRONG,
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
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=200,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            pot_size=200,
        )
        events = HandOutcomeDetector().detect_events(hand)
        bluffed_off = [e for e in events if e.event is RelationshipEvent.BLUFFED_OFF]
        assert len(bluffed_off) == 1
        e = bluffed_off[0]
        assert e.actor_id == "carol"
        assert e.target_id == "bob"

    def test_turn_fold_to_bluffer(self):
        # carol calls flop, folds turn to bob's bluff. Both alice and
        # bob still reach showdown; carol's strong hand would have won.
        hand = _build(
            hole_cards={
                "alice": HOLE_SHOWDOWN_WINNER,
                "bob": HOLE_BLUFF,
                "carol": HOLE_STRONG,
            },
            community=COMMUNITY,
            actions=[
                _action("bob", "bet", 50, phase="FLOP", pot_after=50),
                _action("carol", "call", 50, phase="FLOP", pot_after=100),
                _action("alice", "call", 50, phase="FLOP", pot_after=150),
                _action("bob", "bet", 100, phase="TURN", pot_after=250),
                _action("carol", "fold", 0, phase="TURN", pot_after=250),
                _action("alice", "call", 100, phase="TURN", pot_after=350),
                _action("alice", "check", 0, phase="RIVER", pot_after=350),
                _action("bob", "check", 0, phase="RIVER", pot_after=350),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=350,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            pot_size=350,
        )
        events = HandOutcomeDetector().detect_events(hand)
        bluffed_off = [e for e in events if e.event is RelationshipEvent.BLUFFED_OFF]
        assert len(bluffed_off) == 1
        assert bluffed_off[0].actor_id == "carol"
        assert bluffed_off[0].target_id == "bob"

    def test_attribution_to_most_recent_aggressor(self):
        # alice bets flop, bob raises, carol folds. bob is the more
        # recent aggressor → BLUFFED_OFF attributed to bob, not alice.
        hand = _build(
            hole_cards={
                "alice": HOLE_SHOWDOWN_WINNER,
                "bob": HOLE_BLUFF,
                "carol": HOLE_STRONG,
            },
            community=COMMUNITY,
            actions=[
                _action("alice", "bet", 50, phase="FLOP", pot_after=50),
                _action("bob", "raise", 200, phase="FLOP", pot_after=250),
                _action("carol", "fold", 0, phase="FLOP", pot_after=250),
                _action("alice", "call", 150, phase="FLOP", pot_after=400),
                _action("alice", "check", 0, phase="TURN", pot_after=400),
                _action("bob", "check", 0, phase="TURN", pot_after=400),
                _action("alice", "check", 0, phase="RIVER", pot_after=400),
                _action("bob", "check", 0, phase="RIVER", pot_after=400),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=400,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            pot_size=400,
        )
        events = HandOutcomeDetector().detect_events(hand)
        bluffed_off = [e for e in events if e.event is RelationshipEvent.BLUFFED_OFF]
        assert len(bluffed_off) == 1
        # Attribution to most recent aggressor (bob), not original
        # bettor (alice).
        assert bluffed_off[0].target_id == "bob"


class TestBluffedOffDoesNotFire:
    def test_non_showdown_no_emit(self):
        # Both opponents folded — no card visibility on bluffer.
        hand = _build(
            hole_cards={"carol": HOLE_STRONG},
            community=COMMUNITY,
            actions=[
                _action("bob", "bet", 100, phase="FLOP", pot_after=100),
                _action("carol", "fold", 0, phase="FLOP", pot_after=100),
                _action("alice", "fold", 0, phase="FLOP", pot_after=100),
            ],
            winners=[
                WinnerInfo(
                    name="bob",
                    amount_won=100,
                    hand_name=None,
                    hand_rank=None,
                )
            ],
            pot_size=100,
            was_showdown=False,
        )
        events = HandOutcomeDetector().detect_events(hand)
        bluffed_off = [e for e in events if e.event is RelationshipEvent.BLUFFED_OFF]
        assert bluffed_off == []

    def test_folder_actually_behind_no_emit(self):
        # carol folds with weak cards to bob's bet; carol would have
        # lost anyway. No BLUFFED_OFF (folder wasn't ahead).
        hand = _build(
            hole_cards={
                "alice": HOLE_SHOWDOWN_WINNER,
                "bob": HOLE_STRONG,  # bob's "bet" was a value bet
                "carol": HOLE_BLUFF,  # carol's cards: weak
            },
            community=COMMUNITY,
            actions=[
                _action("bob", "bet", 100, phase="FLOP", pot_after=100),
                _action("carol", "fold", 0, phase="FLOP", pot_after=100),
                _action("alice", "call", 100, phase="FLOP", pot_after=200),
                _action("alice", "check", 0, phase="RIVER", pot_after=200),
                _action("bob", "check", 0, phase="RIVER", pot_after=200),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=200,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            pot_size=200,
        )
        events = HandOutcomeDetector().detect_events(hand)
        bluffed_off = [e for e in events if e.event is RelationshipEvent.BLUFFED_OFF]
        assert bluffed_off == []

    def test_preflop_fold_skipped(self):
        # carol folds preflop — no community-cards comparison possible.
        hand = _build(
            hole_cards={
                "alice": HOLE_SHOWDOWN_WINNER,
                "bob": HOLE_BLUFF,
                "carol": HOLE_STRONG,
            },
            community=COMMUNITY,
            actions=[
                _action("bob", "raise", 100, phase="PRE_FLOP", pot_after=100),
                _action("carol", "fold", 0, phase="PRE_FLOP", pot_after=100),
                _action("alice", "call", 100, phase="PRE_FLOP", pot_after=200),
                _action("alice", "check", 0, phase="FLOP", pot_after=200),
                _action("bob", "check", 0, phase="FLOP", pot_after=200),
                _action("alice", "check", 0, phase="TURN", pot_after=200),
                _action("bob", "check", 0, phase="TURN", pot_after=200),
                _action("alice", "check", 0, phase="RIVER", pot_after=200),
                _action("bob", "check", 0, phase="RIVER", pot_after=200),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=200,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            pot_size=200,
        )
        events = HandOutcomeDetector().detect_events(hand)
        bluffed_off = [e for e in events if e.event is RelationshipEvent.BLUFFED_OFF]
        assert bluffed_off == []

    def test_folder_cards_missing_skipped(self):
        # Folder's hole_cards stripped (tournament-path case). Detector
        # skips silently rather than crashing on KeyError.
        hand = _build(
            hole_cards={
                "alice": HOLE_SHOWDOWN_WINNER,
                "bob": HOLE_BLUFF,
                # carol's cards intentionally absent
            },
            community=COMMUNITY,
            actions=[
                _action("bob", "bet", 100, phase="FLOP", pot_after=100),
                _action("carol", "fold", 0, phase="FLOP", pot_after=100),
                _action("alice", "call", 100, phase="FLOP", pot_after=200),
                _action("alice", "check", 0, phase="RIVER", pot_after=200),
                _action("bob", "check", 0, phase="RIVER", pot_after=200),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=200,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            pot_size=200,
        )
        events = HandOutcomeDetector().detect_events(hand)
        bluffed_off = [e for e in events if e.event is RelationshipEvent.BLUFFED_OFF]
        assert bluffed_off == []

    def test_bettor_also_folded_no_visibility(self):
        # carol folds to bob's flop bet. bob then folds turn to
        # alice's bet. bob never reaches showdown → no visibility
        # on bob's cards → no BLUFFED_OFF for carol.
        hand = _build(
            hole_cards={
                # alice and the third showdown player visible only
                "alice": HOLE_SHOWDOWN_WINNER,
                "dave": HOLE_STRONG,  # 4th player who stays to showdown
                "carol": HOLE_STRONG,
            },
            community=COMMUNITY,
            actions=[
                _action("bob", "bet", 100, phase="FLOP", pot_after=100),
                _action("carol", "fold", 0, phase="FLOP", pot_after=100),
                _action("alice", "call", 100, phase="FLOP", pot_after=200),
                _action("dave", "call", 100, phase="FLOP", pot_after=300),
                _action("alice", "bet", 100, phase="TURN", pot_after=400),
                _action("bob", "fold", 0, phase="TURN", pot_after=400),
                _action("dave", "call", 100, phase="TURN", pot_after=500),
                _action("alice", "check", 0, phase="RIVER", pot_after=500),
                _action("dave", "check", 0, phase="RIVER", pot_after=500),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=500,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            pot_size=500,
        )
        events = HandOutcomeDetector().detect_events(hand)
        bluffed_off = [e for e in events if e.event is RelationshipEvent.BLUFFED_OFF]
        assert bluffed_off == []

    def test_fold_to_check_no_emit(self):
        # No prior aggressor on the fold's street — folder gave up
        # unforced. This is a pathological case (you don't usually
        # "fold" to a check) but the detector should handle it.
        hand = _build(
            hole_cards={
                "alice": HOLE_SHOWDOWN_WINNER,
                "bob": HOLE_BLUFF,
                "carol": HOLE_STRONG,
            },
            community=COMMUNITY,
            actions=[
                _action("alice", "check", 0, phase="FLOP", pot_after=50),
                _action("bob", "check", 0, phase="FLOP", pot_after=50),
                _action("carol", "fold", 0, phase="FLOP", pot_after=50),
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
            pot_size=50,
        )
        events = HandOutcomeDetector().detect_events(hand)
        bluffed_off = [e for e in events if e.event is RelationshipEvent.BLUFFED_OFF]
        assert bluffed_off == []


class TestBluffedOffDedupAndIds:
    def test_dedup_blocks_double_emit(self):
        hand = _build(
            hole_cards={
                "alice": HOLE_SHOWDOWN_WINNER,
                "bob": HOLE_BLUFF,
                "carol": HOLE_STRONG,
            },
            community=COMMUNITY,
            actions=[
                _action("bob", "bet", 100, phase="FLOP", pot_after=100),
                _action("carol", "fold", 0, phase="FLOP", pot_after=100),
                _action("alice", "call", 100, phase="FLOP", pot_after=200),
                _action("alice", "check", 0, phase="RIVER", pot_after=200),
                _action("bob", "check", 0, phase="RIVER", pot_after=200),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=200,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            pot_size=200,
        )
        detector = HandOutcomeDetector()
        first = detector.detect_events(hand)
        second = detector.detect_events(hand)
        assert any(e.event is RelationshipEvent.BLUFFED_OFF for e in first)
        assert second == []

    def test_ids_resolved_from_registry(self):
        hand = _build(
            hole_cards={
                "alice": HOLE_SHOWDOWN_WINNER,
                "bob": HOLE_BLUFF,
                "carol": HOLE_STRONG,
            },
            community=COMMUNITY,
            actions=[
                _action("bob", "bet", 100, phase="FLOP", pot_after=100),
                _action("carol", "fold", 0, phase="FLOP", pot_after=100),
                _action("alice", "call", 100, phase="FLOP", pot_after=200),
                _action("alice", "check", 0, phase="RIVER", pot_after=200),
                _action("bob", "check", 0, phase="RIVER", pot_after=200),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=200,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            pot_size=200,
        )
        registry = {
            "alice": "alice_v1",
            "bob": "bob_v1",
            "carol": "carol_v1",
        }
        events = HandOutcomeDetector(registry).detect_events(hand)
        bo = next(e for e in events if e.event is RelationshipEvent.BLUFFED_OFF)
        assert bo.actor_id == "carol_v1"
        assert bo.target_id == "bob_v1"
