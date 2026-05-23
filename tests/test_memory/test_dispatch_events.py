"""Tests for `dispatch_events` + dedup (Phase 3 commit 3).

Covers:
  - Detector dedup blocks double-emission of the same hand.
  - `dispatch_events` invokes `record_event` per event.
  - `dispatch_events` skips cash_pair_stats updates when no repo
    is provided (tournament mode).
  - When a repo is provided, BIG_WIN events update both rows of
    `cash_pair_stats` bilaterally; BIG_LOSS is not double-counted.
  - Bilateral cash_pair_stats writes match the chip-flow allocation.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

import pytest

pytestmark = pytest.mark.integration

from poker.memory.hand_history import (
    PlayerHandInfo,
    RecordedAction,
    RecordedHand,
    WinnerInfo,
)
from poker.memory.hand_outcome_detector import (
    DetectedEvent,
    HandOutcomeDetector,
    dispatch_events,
)
from poker.memory.opponent_model import OpponentModelManager
from poker.memory.relationship_events import RelationshipEvent
from poker.repositories.relationship_repository import RelationshipRepository
from poker.repositories.schema_manager import SchemaManager


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "rel.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repo(db_path):
    r = RelationshipRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def manager(repo):
    return OpponentModelManager(relationship_repo=repo)


def _make_hand(
    *,
    pot_size: int,
    winners: List[WinnerInfo],
    players: List[PlayerHandInfo],
    actions: List[RecordedAction],
    hand_number: int = 1,
) -> RecordedHand:
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
        was_showdown=True,
    )


def _player(name: str, stack: int = 1000) -> PlayerHandInfo:
    return PlayerHandInfo(
        name=name, starting_stack=stack, position="BTN", is_human=False,
    )


def _action(name, action, amount, phase="PRE_FLOP", pot_after=0):
    return RecordedAction(
        player_name=name, action=action, amount=amount,
        phase=phase, pot_after=pot_after,
    )


def _heads_up_big_hand(hand_number: int = 1) -> RecordedHand:
    players = [_player("alice"), _player("bob")]
    winners = [WinnerInfo(
        name="alice", amount_won=800, hand_name="Pair", hand_rank=8,
    )]
    actions = [
        _action("alice", "raise", 400, pot_after=400),
        _action("bob", "call", 400, pot_after=800),
    ]
    return _make_hand(
        pot_size=800, winners=winners, players=players, actions=actions,
        hand_number=hand_number,
    )


# ---------------------------------------------------------------------
# Detector dedup
# ---------------------------------------------------------------------


class TestDetectorDedup:
    def test_same_hand_twice_returns_empty_second_time(self):
        detector = HandOutcomeDetector()
        hand = _heads_up_big_hand()
        first = detector.detect_events(hand)
        second = detector.detect_events(hand)
        assert len(first) > 0
        assert second == []

    def test_dedup_keyed_on_hand_number(self):
        # Same actor/target/event but different hand_number =
        # different keys, both should fire.
        detector = HandOutcomeDetector()
        first = detector.detect_events(_heads_up_big_hand(hand_number=1))
        second = detector.detect_events(_heads_up_big_hand(hand_number=2))
        assert len(first) > 0
        assert len(second) > 0
        # Same shape (BIG_WIN + BIG_LOSS each).
        assert {e.event for e in first} == {e.event for e in second}


# ---------------------------------------------------------------------
# dispatch_events — relationship state side
# ---------------------------------------------------------------------


class TestDispatchRecordsRelationship:
    def test_writes_both_sides_via_record_event(self, manager, repo):
        events = HandOutcomeDetector().detect_events(_heads_up_big_hand())
        dispatch_events(
            events, manager,
            hand_id=1, now=datetime(2026, 5, 18, 12, 0),
        )
        # Alice (winner POV) has a row pointing at bob, and bob has
        # a mirror row pointing at alice — both via record_event's
        # bilateral path.
        alice_state = repo.load_raw_relationship_state("alice", "bob")
        bob_state = repo.load_raw_relationship_state("bob", "alice")
        assert alice_state is not None
        assert bob_state is not None

    def test_no_events_no_writes(self, manager, repo):
        dispatch_events(
            [], manager,
            hand_id=1, now=datetime(2026, 5, 18, 12, 0),
        )
        assert repo.load_raw_relationship_state("alice", "bob") is None


# ---------------------------------------------------------------------
# dispatch_events — cash_pair_stats side
# ---------------------------------------------------------------------


class TestDispatchUpdatesCashPairStats:
    def test_no_repo_no_cash_writes(self, manager, repo):
        events = HandOutcomeDetector().detect_events(_heads_up_big_hand())
        dispatch_events(
            events, manager,
            cash_pair_repo=None,  # tournament mode
            hand_id=1, now=datetime(2026, 5, 18, 12, 0),
        )
        # Relationship state was written, but no cash_pair_stats row.
        assert repo.load_cash_pair_stats("alice", "bob") is None
        assert repo.load_cash_pair_stats("bob", "alice") is None

    def test_bilateral_cash_pair_stats(self, manager, repo):
        events = HandOutcomeDetector().detect_events(_heads_up_big_hand())
        dispatch_events(
            events, manager,
            cash_pair_repo=repo,
            hand_id=1, now=datetime(2026, 5, 18, 12, 0),
            sandbox_id="sb-1",
        )
        # alice's POV: +400 (her net win from bob).
        alice_stats = repo.load_cash_pair_stats("alice", "bob", sandbox_id="sb-1")
        bob_stats = repo.load_cash_pair_stats("bob", "alice", sandbox_id="sb-1")
        assert alice_stats.cumulative_pnl == 400
        assert alice_stats.hands_played_cash == 1
        assert bob_stats.cumulative_pnl == -400
        assert bob_stats.hands_played_cash == 1

    def test_pnl_accumulates_across_hands(self, manager, repo):
        # Two hands, same winner/loser pair — PnL should sum, hands
        # should increment to 2.
        detector = HandOutcomeDetector()
        for hand_num in (1, 2):
            events = detector.detect_events(
                _heads_up_big_hand(hand_number=hand_num),
            )
            dispatch_events(
                events, manager,
                cash_pair_repo=repo,
                hand_id=hand_num,
                now=datetime(2026, 5, 18, 12, 0),
                sandbox_id="sb-1",
            )
        alice_stats = repo.load_cash_pair_stats("alice", "bob", sandbox_id="sb-1")
        bob_stats = repo.load_cash_pair_stats("bob", "alice", sandbox_id="sb-1")
        assert alice_stats.cumulative_pnl == 800   # 400 × 2
        assert alice_stats.hands_played_cash == 2
        assert bob_stats.cumulative_pnl == -800
        assert bob_stats.hands_played_cash == 2

    def test_big_loss_not_double_counted(self, manager, repo):
        # The detector emits BIG_WIN + BIG_LOSS for the same pair.
        # cash_pair_stats should only process the BIG_WIN — otherwise
        # the loss event would re-apply the same chip flow with
        # opposite sign and zero out the PnL.
        events = HandOutcomeDetector().detect_events(_heads_up_big_hand())
        # Sanity: both event types present.
        assert {e.event for e in events} == {
            RelationshipEvent.BIG_WIN, RelationshipEvent.BIG_LOSS,
        }
        dispatch_events(
            events, manager,
            cash_pair_repo=repo,
            hand_id=1, now=datetime(2026, 5, 18, 12, 0),
            sandbox_id="sb-1",
        )
        alice_stats = repo.load_cash_pair_stats("alice", "bob", sandbox_id="sb-1")
        # If BIG_LOSS were processed too, alice's PnL would be
        # +400 - (-400) = +800 (or +400 + (-400) = 0, depending on
        # how the sign was handled). Either way, double-processing
        # produces the wrong number; verifying the exact-once result.
        assert alice_stats.cumulative_pnl == 400
        assert alice_stats.hands_played_cash == 1

    def test_multiway_cash_pair_stats(self, manager, repo):
        # 3-way pot — alice wins, bob/carol each lose 300. Each
        # (winner, loser) pair gets its own bilateral row.
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
        events = HandOutcomeDetector().detect_events(hand)
        dispatch_events(
            events, manager,
            cash_pair_repo=repo,
            hand_id=1, now=datetime(2026, 5, 18, 12, 0),
            sandbox_id="sb-1",
        )

        # alice's PnL vs each loser: +300 each.
        assert repo.load_cash_pair_stats("alice", "bob", sandbox_id="sb-1").cumulative_pnl == 300
        assert repo.load_cash_pair_stats("alice", "carol", sandbox_id="sb-1").cumulative_pnl == 300
        # Mirror rows: -300.
        assert repo.load_cash_pair_stats("bob", "alice", sandbox_id="sb-1").cumulative_pnl == -300
        assert repo.load_cash_pair_stats("carol", "alice", sandbox_id="sb-1").cumulative_pnl == -300
        # bob and carol have no row vs each other — they never won
        # or lost to each other directly.
        assert repo.load_cash_pair_stats("bob", "carol", sandbox_id="sb-1") is None
        assert repo.load_cash_pair_stats("carol", "bob", sandbox_id="sb-1") is None

    def test_missing_sandbox_skips_cash_writes(self, manager, repo):
        # Defensive: dispatch_events with cash_pair_repo wired but no
        # sandbox_id is a misconfiguration. Refuse to write rather than
        # silently lump rows into an empty-string bucket the admin
        # filter can't surface.
        events = HandOutcomeDetector().detect_events(_heads_up_big_hand())
        dispatch_events(
            events, manager,
            cash_pair_repo=repo,
            hand_id=1, now=datetime(2026, 5, 18, 12, 0),
            # sandbox_id omitted
        )
        # Relationship axis writes still happen.
        assert repo.load_raw_relationship_state("alice", "bob") is not None
        # Cash pair stats stay empty.
        assert repo.load_cash_pair_stats("alice", "bob") is None
        assert repo.load_cash_pair_stats("bob", "alice") is None
