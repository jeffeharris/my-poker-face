"""Tests for HERO_CALL detection (Phase 3 commit 5).

v1 simple semantic: winner's last RIVER action was `call` against a
loser's bet/raise, and at showdown the winner's hand_rank beat the
caller's. Approximation — doesn't gate on decision-time equity.
That refinement waits for polarization Phase B's equity infra.

Fixtures use real cards + `HandEvaluator` so hand_rank comparisons
match what the detector computes (rather than mocked values that
might drift from the evaluator's actual ranking semantics).
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


def _player(name: str, stack: int = 1000, position: str = "BTN") -> PlayerHandInfo:
    return PlayerHandInfo(
        name=name,
        starting_stack=stack,
        position=position,
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


def _showdown_hand(
    *,
    hole_cards: dict,
    community: tuple,
    actions: list,
    winners: list,
    pot_size: int = 200,
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
        was_showdown=True,
    )


# Reference hand-strength setup used across tests:
#   board: 2c 5h 8c Tc Jd  (no pair, no flush, no straight on the board itself)
#   alice: Ah Ad           → pocket aces, rank 9 (one pair)
#   bob:   7s 6s           → high card jack, rank 10 (high card)
# alice wins; alice rank (9) < bob rank (10).
COMMUNITY_DRY = ("2c", "5h", "8c", "Tc", "Jd")
HOLE_ALICE_AA = ["Ah", "Ad"]
HOLE_BOB_HIGH = ["7s", "6s"]


class TestHeroCallFiresOnRiverCall:
    def test_bet_call_winner_emits_hero_call(self):
        # bob bets river, alice calls, alice wins at showdown.
        hand = _showdown_hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                # Preflop / postflop streets summarized to set up the
                # river spot. Only RIVER actions drive HERO_CALL.
                _action("alice", "check", 0, phase="PRE_FLOP", pot_after=50),
                _action("bob", "check", 0, phase="PRE_FLOP", pot_after=50),
                _action("alice", "check", 0, phase="FLOP", pot_after=50),
                _action("bob", "check", 0, phase="FLOP", pot_after=50),
                _action("alice", "check", 0, phase="TURN", pot_after=50),
                _action("bob", "check", 0, phase="TURN", pot_after=50),
                # River — bob fires a bluff, alice calls.
                _action("bob", "bet", 100, phase="RIVER", pot_after=150),
                _action("alice", "call", 100, phase="RIVER", pot_after=250),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=250,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            pot_size=250,
        )

        events = HandOutcomeDetector().detect_events(hand)
        hero_calls = [e for e in events if e.event is RelationshipEvent.HERO_CALL]
        assert len(hero_calls) == 1
        hc = hero_calls[0]
        assert hc.actor_id == "alice"
        assert hc.target_id == "bob"

    def test_raise_call_winner_emits_hero_call(self):
        # alice bets, bob raises, alice calls — alice still hero-calls.
        hand = _showdown_hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("alice", "bet", 100, phase="RIVER", pot_after=100),
                _action("bob", "raise", 300, phase="RIVER", pot_after=300),
                _action("alice", "call", 200, phase="RIVER", pot_after=500),
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
        hero_calls = [e for e in events if e.event is RelationshipEvent.HERO_CALL]
        assert len(hero_calls) == 1
        assert hero_calls[0].actor_id == "alice"
        assert hero_calls[0].target_id == "bob"


class TestHeroCallDoesNotFire:
    def test_non_showdown_no_hero_call(self):
        # bob folded — no showdown, even if alice's call pattern matches.
        hand = _showdown_hand(
            hole_cards={"alice": HOLE_ALICE_AA},  # bob's cards stripped
            community=COMMUNITY_DRY,
            actions=[
                _action("alice", "bet", 100, phase="RIVER", pot_after=100),
                _action("bob", "fold", 0, phase="RIVER", pot_after=100),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=100,
                    hand_name=None,
                    hand_rank=None,
                )
            ],
            pot_size=100,
        )
        # Override was_showdown via constructor for clarity.
        hand_no_showdown = RecordedHand(
            game_id=hand.game_id,
            hand_number=hand.hand_number,
            timestamp=hand.timestamp,
            players=hand.players,
            hole_cards=hand.hole_cards,
            community_cards=hand.community_cards,
            actions=hand.actions,
            winners=hand.winners,
            pot_size=hand.pot_size,
            was_showdown=False,
        )
        events = HandOutcomeDetector().detect_events(hand_no_showdown)
        hero_calls = [e for e in events if e.event is RelationshipEvent.HERO_CALL]
        assert hero_calls == []

    def test_winner_raised_river_not_a_hero_call(self):
        # alice raised the river instead of calling — not a hero call.
        hand = _showdown_hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "raise", 300, phase="RIVER", pot_after=400),
                _action("bob", "call", 200, phase="RIVER", pot_after=600),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=600,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            pot_size=600,
        )
        events = HandOutcomeDetector().detect_events(hand)
        hero_calls = [e for e in events if e.event is RelationshipEvent.HERO_CALL]
        assert hero_calls == []

    def test_check_check_river_no_hero_call(self):
        # No river bet → no hero call (even if alice wins at showdown).
        hand = _showdown_hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
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
        hero_calls = [e for e in events if e.event is RelationshipEvent.HERO_CALL]
        assert hero_calls == []

    def test_winner_called_but_had_worse_hand_no_hero_call(self):
        # Suckout from alice's POV: alice called with worse hand-rank
        # but somehow appears as the winner. The detector compares
        # computed ranks, not just the winners list, so a "winner
        # by misregistration" shouldn't trigger HERO_CALL. Swap roles
        # so the recorded winner has the WORSE rank.
        # alice: 7s 6s (high card), bob: Ah Ad (pair).
        # If the recorded winner was bob (correctly), no winner-call
        # pattern. If somehow alice were the recorded winner, bob's
        # rank is BETTER → no hero call from alice's perspective.
        hand = _showdown_hand(
            hole_cards={"alice": HOLE_BOB_HIGH, "bob": HOLE_ALICE_AA},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "call", 100, phase="RIVER", pot_after=200),
            ],
            # Pathological: alice recorded as winner despite worse hand.
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=200,
                    hand_name="High",
                    hand_rank=10,
                )
            ],
            pot_size=200,
        )
        events = HandOutcomeDetector().detect_events(hand)
        hero_calls = [e for e in events if e.event is RelationshipEvent.HERO_CALL]
        assert hero_calls == []

    def test_turn_call_then_river_check_no_hero_call(self):
        # Call was on TURN; v1 detector restricts to RIVER calls.
        hand = _showdown_hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="TURN", pot_after=100),
                _action("alice", "call", 100, phase="TURN", pot_after=200),
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
        hero_calls = [e for e in events if e.event is RelationshipEvent.HERO_CALL]
        # v1 restriction documented in detector docstring.
        assert hero_calls == []


class TestHeroCallEmittedAlongsideBigPot:
    def test_big_pot_hero_call_both_events_present(self):
        # River bet/call pot large enough to also trigger BIG_WIN/LOSS.
        # Both event types should appear in the same emission.
        hand = _showdown_hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("alice", "raise", 200, phase="PRE_FLOP", pot_after=200),
                _action("bob", "call", 200, phase="PRE_FLOP", pot_after=400),
                _action("bob", "bet", 200, phase="RIVER", pot_after=600),
                _action("alice", "call", 200, phase="RIVER", pot_after=800),
            ],
            winners=[
                WinnerInfo(
                    name="alice",
                    amount_won=800,
                    hand_name="Pair",
                    hand_rank=9,
                )
            ],
            pot_size=800,
        )
        events = HandOutcomeDetector().detect_events(hand)
        kinds = {e.event for e in events}
        assert RelationshipEvent.BIG_WIN in kinds
        assert RelationshipEvent.BIG_LOSS in kinds
        assert RelationshipEvent.HERO_CALL in kinds


class TestHeroCallDedupAndIdResolution:
    def test_dedup_blocks_double_emit(self):
        hand = _showdown_hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "call", 100, phase="RIVER", pot_after=200),
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
        assert any(e.event is RelationshipEvent.HERO_CALL for e in first)
        assert second == []

    def test_ids_resolved_from_registry(self):
        hand = _showdown_hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "call", 100, phase="RIVER", pot_after=200),
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
        registry = {"alice": "alice_v1", "bob": "bob_v1"}
        events = HandOutcomeDetector(registry).detect_events(hand)
        hc = next(e for e in events if e.event is RelationshipEvent.HERO_CALL)
        assert hc.actor_id == "alice_v1"
        assert hc.target_id == "bob_v1"
