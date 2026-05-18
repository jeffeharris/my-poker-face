"""Tests for BAD_BEAT detection (Phase 3 commit 7).

Semantic: actor (loser at showdown) had ≥0.70 equity at some pre-
river street and lost. Attributed to the single winner. Multi-
winner pots are skipped because attribution is ambiguous.

Equity data is supplied via `HandEquityHistory` passed to
`detect_events(equity_history=...)`. Without it, BAD_BEAT silently
no-ops — the experiment-runner path supplies it when telemetry /
psychology is enabled; the Flask game path doesn't compute equity
today.
"""

from __future__ import annotations

from datetime import datetime

from poker.equity_snapshot import EquitySnapshot, HandEquityHistory
from poker.memory.hand_history import (
    PlayerHandInfo,
    RecordedAction,
    RecordedHand,
    WinnerInfo,
)
from poker.memory.hand_outcome_detector import HandOutcomeDetector
from poker.memory.relationship_events import RelationshipEvent


def _player(name: str) -> PlayerHandInfo:
    return PlayerHandInfo(
        name=name, starting_stack=1000, position="BTN", is_human=False,
    )


def _action(name, action, amount, phase, pot_after):
    return RecordedAction(
        player_name=name, action=action, amount=amount,
        phase=phase, pot_after=pot_after,
    )


def _eq(player_name, street, equity, *, was_active=True):
    return EquitySnapshot(
        player_name=player_name, street=street, equity=equity,
        hole_cards=(), board_cards=(), was_active=was_active,
    )


def _hand(*, winners, players, actions, hole_cards, pot_size=200):
    return RecordedHand(
        game_id="g1", hand_number=1,
        timestamp=datetime(2026, 5, 18, 12, 0),
        players=tuple(players),
        hole_cards=hole_cards,
        community_cards=("2c", "5h", "8c", "Tc", "Jd"),
        actions=tuple(actions),
        winners=tuple(winners),
        pot_size=pot_size,
        was_showdown=True,
    )


def _history(snapshots):
    return HandEquityHistory(
        hand_history_id=None, game_id="g1", hand_number=1,
        snapshots=tuple(snapshots),
    )


class TestBadBeatFires:
    def test_river_runout_kills_favorite(self):
        # alice was 85% on the turn, lost on the river to bob's draw.
        hand = _hand(
            winners=[WinnerInfo(
                name="bob", amount_won=200, hand_name="Flush",
                hand_rank=4,
            )],
            players=[_player("alice"), _player("bob")],
            hole_cards={"alice": ["Ah", "Ad"], "bob": ["7s", "6s"]},
            actions=[
                _action("alice", "raise", 100, "PRE_FLOP", 100),
                _action("bob", "call", 100, "PRE_FLOP", 200),
            ],
        )
        history = _history([
            _eq("alice", "PRE_FLOP", 0.85),
            _eq("bob", "PRE_FLOP", 0.15),
            _eq("alice", "FLOP", 0.80),
            _eq("bob", "FLOP", 0.20),
            _eq("alice", "TURN", 0.85),
            _eq("bob", "TURN", 0.15),
            _eq("alice", "RIVER", 0.0),
            _eq("bob", "RIVER", 1.0),
        ])
        events = HandOutcomeDetector().detect_events(
            hand, equity_history=history,
        )
        bad_beats = [
            e for e in events if e.event is RelationshipEvent.BAD_BEAT
        ]
        assert len(bad_beats) == 1
        bb = bad_beats[0]
        assert bb.actor_id == "alice"
        assert bb.target_id == "bob"
        assert "85%" in bb.narrative

    def test_all_in_preflop_favorite_loses(self):
        # AA vs KK all-in preflop: AA 82%, KK 18%. Three-king board.
        hand = _hand(
            winners=[WinnerInfo(
                name="bob", amount_won=2000, hand_name="Trips",
                hand_rank=7,
            )],
            players=[_player("alice"), _player("bob")],
            hole_cards={"alice": ["Ah", "Ad"], "bob": ["Ks", "Kd"]},
            actions=[
                _action("alice", "all_in", 1000, "PRE_FLOP", 1000),
                _action("bob", "call", 1000, "PRE_FLOP", 2000),
            ],
            pot_size=2000,
        )
        history = _history([
            _eq("alice", "PRE_FLOP", 0.82),
            _eq("bob", "PRE_FLOP", 0.18),
            _eq("alice", "RIVER", 0.0),
            _eq("bob", "RIVER", 1.0),
        ])
        events = HandOutcomeDetector().detect_events(
            hand, equity_history=history,
        )
        bad_beats = [
            e for e in events if e.event is RelationshipEvent.BAD_BEAT
        ]
        assert len(bad_beats) == 1
        assert bad_beats[0].actor_id == "alice"

    def test_threshold_exactly_70_fires(self):
        # Right at the threshold — fires (>= 0.70 condition).
        hand = _hand(
            winners=[WinnerInfo(
                name="bob", amount_won=200, hand_name="Pair",
                hand_rank=9,
            )],
            players=[_player("alice"), _player("bob")],
            hole_cards={"alice": ["Ah", "Kd"], "bob": ["7s", "6s"]},
            actions=[
                _action("alice", "raise", 100, "PRE_FLOP", 100),
                _action("bob", "call", 100, "PRE_FLOP", 200),
            ],
        )
        history = _history([
            _eq("alice", "TURN", 0.70),
            _eq("bob", "TURN", 0.30),
            _eq("alice", "RIVER", 0.0),
            _eq("bob", "RIVER", 1.0),
        ])
        events = HandOutcomeDetector().detect_events(
            hand, equity_history=history,
        )
        bad_beats = [
            e for e in events if e.event is RelationshipEvent.BAD_BEAT
        ]
        assert len(bad_beats) == 1


class TestBadBeatDoesNotFire:
    def test_below_threshold_no_emit(self):
        # alice was 65% on the turn — favored but not bad-beat-worthy.
        hand = _hand(
            winners=[WinnerInfo(
                name="bob", amount_won=200, hand_name="Pair",
                hand_rank=9,
            )],
            players=[_player("alice"), _player("bob")],
            hole_cards={"alice": ["Ah", "Kd"], "bob": ["7s", "6s"]},
            actions=[
                _action("alice", "raise", 100, "PRE_FLOP", 100),
                _action("bob", "call", 100, "PRE_FLOP", 200),
            ],
        )
        history = _history([
            _eq("alice", "TURN", 0.65),
            _eq("bob", "TURN", 0.35),
        ])
        events = HandOutcomeDetector().detect_events(
            hand, equity_history=history,
        )
        bad_beats = [
            e for e in events if e.event is RelationshipEvent.BAD_BEAT
        ]
        assert bad_beats == []

    def test_no_equity_history_silent_no_op(self):
        hand = _hand(
            winners=[WinnerInfo(
                name="bob", amount_won=200, hand_name="Pair",
                hand_rank=9,
            )],
            players=[_player("alice"), _player("bob")],
            hole_cards={"alice": ["Ah", "Ad"], "bob": ["7s", "6s"]},
            actions=[
                _action("alice", "raise", 100, "PRE_FLOP", 100),
                _action("bob", "call", 100, "PRE_FLOP", 200),
            ],
        )
        # No equity_history passed
        events = HandOutcomeDetector().detect_events(hand)
        bad_beats = [
            e for e in events if e.event is RelationshipEvent.BAD_BEAT
        ]
        assert bad_beats == []

    def test_river_only_equity_ignored(self):
        # alice had 0% at RIVER (outcome state) and 0% at every
        # pre-river street. No bad beat — she was never a favorite.
        hand = _hand(
            winners=[WinnerInfo(
                name="bob", amount_won=200, hand_name="Pair",
                hand_rank=9,
            )],
            players=[_player("alice"), _player("bob")],
            hole_cards={"alice": ["7s", "6s"], "bob": ["Ah", "Ad"]},
            actions=[
                _action("alice", "raise", 100, "PRE_FLOP", 100),
                _action("bob", "call", 100, "PRE_FLOP", 200),
            ],
        )
        history = _history([
            _eq("alice", "PRE_FLOP", 0.18),
            _eq("bob", "PRE_FLOP", 0.82),
            _eq("alice", "RIVER", 0.0),
            _eq("bob", "RIVER", 1.0),
        ])
        events = HandOutcomeDetector().detect_events(
            hand, equity_history=history,
        )
        bad_beats = [
            e for e in events if e.event is RelationshipEvent.BAD_BEAT
        ]
        assert bad_beats == []

    def test_river_equity_alone_not_a_bad_beat(self):
        # Defensive: even if RIVER equity is 1.0 (loser's snapshot
        # before the showdown reveal), the detector should NOT count
        # RIVER as a "pre-river street." Without pre-river data,
        # no bad beat. (This validates the RIVER-exclusion rule.)
        hand = _hand(
            winners=[WinnerInfo(
                name="bob", amount_won=200, hand_name="Pair",
                hand_rank=9,
            )],
            players=[_player("alice"), _player("bob")],
            hole_cards={"alice": ["7s", "6s"], "bob": ["Ah", "Ad"]},
            actions=[
                _action("alice", "raise", 100, "PRE_FLOP", 100),
                _action("bob", "call", 100, "PRE_FLOP", 200),
            ],
        )
        # alice has 0 pre-river equity (no pre-river snapshots);
        # only RIVER snapshot showing 1.0 (this shouldn't happen in
        # real data but defends the detector against weird inputs).
        history = _history([
            _eq("alice", "RIVER", 1.0),
            _eq("bob", "RIVER", 0.0),
        ])
        events = HandOutcomeDetector().detect_events(
            hand, equity_history=history,
        )
        bad_beats = [
            e for e in events if e.event is RelationshipEvent.BAD_BEAT
        ]
        assert bad_beats == []

    def test_split_pot_skipped(self):
        # Multiple winners (chopped pot). Attribution ambiguous — skip.
        hand = _hand(
            winners=[
                WinnerInfo(
                    name="bob", amount_won=100, hand_name="Pair",
                    hand_rank=9,
                ),
                WinnerInfo(
                    name="carol", amount_won=100, hand_name="Pair",
                    hand_rank=9,
                ),
            ],
            players=[_player("alice"), _player("bob"), _player("carol")],
            hole_cards={
                "alice": ["Ah", "Ad"],
                "bob": ["7s", "6s"],
                "carol": ["Ks", "Kd"],
            },
            actions=[
                _action("alice", "raise", 100, "PRE_FLOP", 100),
                _action("bob", "call", 100, "PRE_FLOP", 200),
                _action("carol", "call", 100, "PRE_FLOP", 300),
            ],
        )
        history = _history([
            _eq("alice", "TURN", 0.85),
        ])
        events = HandOutcomeDetector().detect_events(
            hand, equity_history=history,
        )
        bad_beats = [
            e for e in events if e.event is RelationshipEvent.BAD_BEAT
        ]
        assert bad_beats == []

    def test_non_showdown_skipped(self):
        # Folded hand — no equity data to compare, even if we have it.
        players = [_player("alice"), _player("bob")]
        hand = RecordedHand(
            game_id="g1", hand_number=1,
            timestamp=datetime(2026, 5, 18, 12, 0),
            players=tuple(players),
            hole_cards={"alice": ["Ah", "Ad"]},  # bob folded
            community_cards=(),
            actions=(
                _action("alice", "bet", 100, "FLOP", 100),
                _action("bob", "fold", 0, "FLOP", 100),
            ),
            winners=(WinnerInfo(
                name="alice", amount_won=100, hand_name=None,
                hand_rank=None,
            ),),
            pot_size=100,
            was_showdown=False,
        )
        history = _history([_eq("alice", "FLOP", 0.85)])
        events = HandOutcomeDetector().detect_events(
            hand, equity_history=history,
        )
        bad_beats = [
            e for e in events if e.event is RelationshipEvent.BAD_BEAT
        ]
        assert bad_beats == []


class TestBadBeatDedupAndIds:
    def test_dedup_blocks_double_emit(self):
        hand = _hand(
            winners=[WinnerInfo(
                name="bob", amount_won=200, hand_name="Pair",
                hand_rank=9,
            )],
            players=[_player("alice"), _player("bob")],
            hole_cards={"alice": ["Ah", "Ad"], "bob": ["7s", "6s"]},
            actions=[
                _action("alice", "raise", 100, "PRE_FLOP", 100),
                _action("bob", "call", 100, "PRE_FLOP", 200),
            ],
        )
        history = _history([
            _eq("alice", "TURN", 0.85),
            _eq("bob", "TURN", 0.15),
        ])
        detector = HandOutcomeDetector()
        first = detector.detect_events(hand, equity_history=history)
        second = detector.detect_events(hand, equity_history=history)
        assert any(
            e.event is RelationshipEvent.BAD_BEAT for e in first
        )
        assert second == []

    def test_ids_resolved_from_registry(self):
        hand = _hand(
            winners=[WinnerInfo(
                name="bob", amount_won=200, hand_name="Pair",
                hand_rank=9,
            )],
            players=[_player("alice"), _player("bob")],
            hole_cards={"alice": ["Ah", "Ad"], "bob": ["7s", "6s"]},
            actions=[
                _action("alice", "raise", 100, "PRE_FLOP", 100),
                _action("bob", "call", 100, "PRE_FLOP", 200),
            ],
        )
        history = _history([
            _eq("alice", "TURN", 0.85),
            _eq("bob", "TURN", 0.15),
        ])
        registry = {"alice": "alice_v1", "bob": "bob_v1"}
        events = HandOutcomeDetector(registry).detect_events(
            hand, equity_history=history,
        )
        bb = next(
            e for e in events if e.event is RelationshipEvent.BAD_BEAT
        )
        assert bb.actor_id == "alice_v1"
        assert bb.target_id == "bob_v1"


class TestBadBeatMultiway:
    def test_emits_for_each_qualifying_loser(self):
        # 3-way: alice 70% favorite, bob/carol each ~15%. carol wins
        # somehow. alice gets BAD_BEAT, bob doesn't (not a favorite).
        hand = _hand(
            winners=[WinnerInfo(
                name="carol", amount_won=300, hand_name="Flush",
                hand_rank=4,
            )],
            players=[_player("alice"), _player("bob"), _player("carol")],
            hole_cards={
                "alice": ["Ah", "Ad"],
                "bob": ["Ks", "Qs"],
                "carol": ["7d", "6d"],
            },
            actions=[
                _action("alice", "raise", 100, "PRE_FLOP", 100),
                _action("bob", "call", 100, "PRE_FLOP", 200),
                _action("carol", "call", 100, "PRE_FLOP", 300),
            ],
            pot_size=300,
        )
        history = _history([
            _eq("alice", "TURN", 0.70),
            _eq("bob", "TURN", 0.15),
            _eq("carol", "TURN", 0.15),
        ])
        events = HandOutcomeDetector().detect_events(
            hand, equity_history=history,
        )
        bad_beats = [
            e for e in events if e.event is RelationshipEvent.BAD_BEAT
        ]
        assert len(bad_beats) == 1
        assert bad_beats[0].actor_id == "alice"
        assert bad_beats[0].target_id == "carol"
