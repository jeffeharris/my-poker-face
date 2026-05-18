"""Tests for HandOutcomeDetector (Phase 3 commit 1).

Covers the BIG_WIN / BIG_LOSS emission path for the single-winner /
single-loser case. Multiway chip-flow allocation, dispatch, and
MemoryManager integration land in subsequent commits and have their
own tests.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from poker.memory.hand_history import (
    PlayerHandInfo,
    RecordedAction,
    RecordedHand,
    WinnerInfo,
)
from poker.memory.hand_outcome_detector import (
    DetectedEvent,
    HandOutcomeDetector,
)
from poker.memory.relationship_events import RelationshipEvent


def _make_hand(
    *,
    pot_size: int,
    winners: List[WinnerInfo],
    players: List[PlayerHandInfo],
    actions: List[RecordedAction],
    was_showdown: bool = True,
    hand_number: int = 1,
) -> RecordedHand:
    """Compact constructor for `RecordedHand` test fixtures."""
    return RecordedHand(
        game_id="g1",
        hand_number=hand_number,
        timestamp=datetime(2026, 5, 18, 12, 0),
        players=tuple(players),
        hole_cards={p.name: ["Ah", "Ks"] for p in players},
        community_cards=("2c", "7d", "9s", "Th", "Jc"),
        actions=tuple(actions),
        winners=tuple(winners),
        pot_size=pot_size,
        was_showdown=was_showdown,
    )


def _player(name: str, stack: int = 1000, position: str = "BTN") -> PlayerHandInfo:
    return PlayerHandInfo(
        name=name, starting_stack=stack, position=position, is_human=False,
    )


def _action(name: str, action: str, amount: int, phase: str = "PRE_FLOP",
            pot_after: int = 0) -> RecordedAction:
    return RecordedAction(
        player_name=name, action=action, amount=amount,
        phase=phase, pot_after=pot_after,
    )


# ---------------------------------------------------------------------
# Big-pot heads-up emission
# ---------------------------------------------------------------------


class TestHeadsUpBigPotEmission:
    """Single winner, single loser, pot over the big-pot threshold:
    emit BIG_WIN(winner→loser) and BIG_LOSS(loser→winner) together.
    """

    def test_big_pot_heads_up_emits_paired_events(self):
        # Avg starting stack 1000; big-pot threshold via
        # MomentAnalyzer.is_big_pot is pot > 0.75 * avg_stack when
        # no per-player stack is supplied. 800 chips > 750 → big.
        players = [_player("alice"), _player("bob")]
        winners = [WinnerInfo(
            name="alice", amount_won=800, hand_name="Pair", hand_rank=8,
        )]
        actions = [
            _action("alice", "raise", 100, pot_after=100),
            _action("bob", "call", 100, pot_after=200),
            _action("alice", "bet", 300, phase="FLOP", pot_after=500),
            _action("bob", "call", 300, phase="FLOP", pot_after=800),
        ]
        hand = _make_hand(
            pot_size=800, winners=winners, players=players, actions=actions,
        )

        detector = HandOutcomeDetector()
        events = detector.detect_events(hand)

        # One BIG_WIN, one BIG_LOSS — symmetric pair.
        kinds = {e.event for e in events}
        assert kinds == {RelationshipEvent.BIG_WIN, RelationshipEvent.BIG_LOSS}

        big_win = next(e for e in events if e.event is RelationshipEvent.BIG_WIN)
        big_loss = next(e for e in events if e.event is RelationshipEvent.BIG_LOSS)

        # Actor / target orientation matches design adapter table:
        # BIG_WIN actor = winner; BIG_LOSS actor = loser.
        assert big_win.actor_id == "alice"
        assert big_win.target_id == "bob"
        assert big_loss.actor_id == "bob"
        assert big_loss.target_id == "alice"

        # Chip flow: winner +400 (their contribution), loser -400.
        # In the heads-up single-pair case both contributions are
        # 400, and min(800 collected, 400 contributed) = 400.
        assert big_win.chips_won == 400
        assert big_loss.chips_won == -400

    def test_big_pot_by_fold_emits_paired_events(self):
        # Heads-up by fold: alice folds postflop after a big pot built.
        # Still one winner, one loser — same emission shape.
        # Avg stack 1000 → big-pot threshold is pot > 750.
        players = [_player("alice"), _player("bob")]
        actions = [
            _action("alice", "raise", 300, pot_after=300),
            _action("bob", "call", 300, pot_after=600),
            _action("alice", "bet", 300, phase="FLOP", pot_after=900),
            _action("bob", "raise", 600, phase="FLOP", pot_after=1200),
            _action("alice", "fold", 0, phase="FLOP", pot_after=1200),
        ]
        winners = [WinnerInfo(
            name="bob", amount_won=900, hand_name=None, hand_rank=None,
        )]
        hand = _make_hand(
            pot_size=900, winners=winners, players=players, actions=actions,
            was_showdown=False,
        )

        events = HandOutcomeDetector().detect_events(hand)

        kinds = {e.event for e in events}
        assert kinds == {RelationshipEvent.BIG_WIN, RelationshipEvent.BIG_LOSS}
        big_win = next(e for e in events if e.event is RelationshipEvent.BIG_WIN)
        assert big_win.actor_id == "bob"
        assert big_win.target_id == "alice"


# ---------------------------------------------------------------------
# Non-trigger cases
# ---------------------------------------------------------------------


class TestNoEmission:
    """Cases where the detector returns no events."""

    def test_small_pot_emits_nothing(self):
        # 50-chip pot with 1000-chip avg stack: 50 < 750 (0.75 * avg).
        players = [_player("alice"), _player("bob")]
        winners = [WinnerInfo(
            name="alice", amount_won=50, hand_name="High Card", hand_rank=10,
        )]
        actions = [
            _action("alice", "raise", 25, pot_after=25),
            _action("bob", "call", 25, pot_after=50),
            _action("alice", "check", 0, phase="FLOP", pot_after=50),
            _action("bob", "check", 0, phase="FLOP", pot_after=50),
        ]
        hand = _make_hand(
            pot_size=50, winners=winners, players=players, actions=actions,
        )
        assert HandOutcomeDetector().detect_events(hand) == []

    def test_zero_pot_emits_nothing(self):
        players = [_player("alice"), _player("bob")]
        hand = _make_hand(
            pot_size=0, winners=[], players=players, actions=[],
            was_showdown=False,
        )
        assert HandOutcomeDetector().detect_events(hand) == []

    def test_no_winners_emits_nothing(self):
        players = [_player("alice"), _player("bob")]
        hand = _make_hand(
            pot_size=1000, winners=[], players=players, actions=[
                _action("alice", "raise", 500, pot_after=500),
                _action("bob", "raise", 1000, pot_after=1500),
            ],
        )
        assert HandOutcomeDetector().detect_events(hand) == []

    def test_split_pot_skipped_in_commit_1(self):
        # Multiple winners (chopped pot) — commit 2 handles allocation.
        players = [_player("alice"), _player("bob"), _player("carol")]
        winners = [
            WinnerInfo(
                name="alice", amount_won=400, hand_name="Straight",
                hand_rank=5,
            ),
            WinnerInfo(
                name="bob", amount_won=400, hand_name="Straight",
                hand_rank=5,
            ),
        ]
        actions = [
            _action("alice", "raise", 250, pot_after=250),
            _action("bob", "call", 250, pot_after=500),
            _action("carol", "call", 250, pot_after=750),
            _action("alice", "check", 0, phase="FLOP", pot_after=750),
            _action("bob", "check", 0, phase="FLOP", pot_after=750),
            _action("carol", "fold", 0, phase="FLOP", pot_after=750),
        ]
        hand = _make_hand(
            pot_size=800, winners=winners, players=players, actions=actions,
        )
        # Multiple winners → commit 1 skips, commit 2 will handle.
        assert HandOutcomeDetector().detect_events(hand) == []

    def test_multiway_losers_skipped_in_commit_1(self):
        # One winner, multiple losers — commit 2 ships the allocation.
        players = [_player("alice"), _player("bob"), _player("carol")]
        winners = [WinnerInfo(
            name="alice", amount_won=900, hand_name="Flush", hand_rank=4,
        )]
        actions = [
            _action("alice", "raise", 300, pot_after=300),
            _action("bob", "call", 300, pot_after=600),
            _action("carol", "call", 300, pot_after=900),
        ]
        hand = _make_hand(
            pot_size=900, winners=winners, players=players, actions=actions,
        )
        # Multiple losers → commit 1 skips, commit 2 will allocate.
        assert HandOutcomeDetector().detect_events(hand) == []


# ---------------------------------------------------------------------
# ID registry
# ---------------------------------------------------------------------


class TestIdResolution:
    """`name_to_id` registry rewrites display names to personality_ids."""

    def test_registered_ids_used_when_present(self):
        players = [_player("alice"), _player("bob")]
        winners = [WinnerInfo(
            name="alice", amount_won=800, hand_name="Pair", hand_rank=8,
        )]
        actions = [
            _action("alice", "raise", 400, pot_after=400),
            _action("bob", "call", 400, pot_after=800),
        ]
        hand = _make_hand(
            pot_size=800, winners=winners, players=players, actions=actions,
        )

        registry = {"alice": "alice_v1", "bob": "bob_v1"}
        events = HandOutcomeDetector(registry).detect_events(hand)

        big_win = next(e for e in events if e.event is RelationshipEvent.BIG_WIN)
        big_loss = next(e for e in events if e.event is RelationshipEvent.BIG_LOSS)
        assert big_win.actor_id == "alice_v1"
        assert big_win.target_id == "bob_v1"
        assert big_loss.actor_id == "bob_v1"
        assert big_loss.target_id == "alice_v1"

    def test_unregistered_name_falls_back_to_display_name(self):
        # Only alice has an id; bob falls through to display name.
        players = [_player("alice"), _player("bob")]
        winners = [WinnerInfo(
            name="alice", amount_won=800, hand_name="Pair", hand_rank=8,
        )]
        actions = [
            _action("alice", "raise", 400, pot_after=400),
            _action("bob", "call", 400, pot_after=800),
        ]
        hand = _make_hand(
            pot_size=800, winners=winners, players=players, actions=actions,
        )

        events = HandOutcomeDetector({"alice": "alice_v1"}).detect_events(hand)

        big_win = next(e for e in events if e.event is RelationshipEvent.BIG_WIN)
        assert big_win.actor_id == "alice_v1"
        # bob isn't registered → falls back to display name.
        assert big_win.target_id == "bob"

    def test_none_registered_id_uses_display_name(self):
        # Human players are explicitly registered with id=None — the
        # detector falls back to their display name as the key.
        players = [_player("alice"), _player("bob")]
        winners = [WinnerInfo(
            name="alice", amount_won=800, hand_name="Pair", hand_rank=8,
        )]
        actions = [
            _action("alice", "raise", 400, pot_after=400),
            _action("bob", "call", 400, pot_after=800),
        ]
        hand = _make_hand(
            pot_size=800, winners=winners, players=players, actions=actions,
        )

        registry = {"alice": None, "bob": None}
        events = HandOutcomeDetector(registry).detect_events(hand)

        big_win = next(e for e in events if e.event is RelationshipEvent.BIG_WIN)
        assert big_win.actor_id == "alice"
        assert big_win.target_id == "bob"


# ---------------------------------------------------------------------
# Narrative + impact_score defaults
# ---------------------------------------------------------------------


class TestDetectedEventShape:
    def test_default_impact_score_is_one(self):
        players = [_player("alice"), _player("bob")]
        winners = [WinnerInfo(
            name="alice", amount_won=800, hand_name="Pair", hand_rank=8,
        )]
        actions = [
            _action("alice", "raise", 400, pot_after=400),
            _action("bob", "call", 400, pot_after=800),
        ]
        hand = _make_hand(
            pot_size=800, winners=winners, players=players, actions=actions,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert all(e.impact_score == 1.0 for e in events)

    def test_narrative_includes_both_names(self):
        players = [_player("alice"), _player("bob")]
        winners = [WinnerInfo(
            name="alice", amount_won=800, hand_name="Pair", hand_rank=8,
        )]
        actions = [
            _action("alice", "raise", 400, pot_after=400),
            _action("bob", "call", 400, pot_after=800),
        ]
        hand = _make_hand(
            pot_size=800, winners=winners, players=players, actions=actions,
        )
        events = HandOutcomeDetector().detect_events(hand)
        for e in events:
            assert "alice" in e.narrative
            assert "bob" in e.narrative
            assert e.hand_summary  # non-empty
