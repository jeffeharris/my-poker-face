"""Tests for STACK_DOMINANCE detection in HandOutcomeDetector.

STACK_DOMINANCE fires once per hand per (observer, deep_stack) pair
when a seated peer's starting_stack >= 1.5× table max buy-in AND the
observer has negative cumulative_pnl against them. Each emission
carries a context_multiplier equal to `stack/max - 1.5` so the
dispatch-side AxisShift scales with how deep the stack is.

Detector is cash-mode-only — gated on `max_buy_in` being supplied to
`detect_events()`. Tournament callers pass None and the detector is
silent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Tuple

import pytest

from poker.memory.hand_history import (
    PlayerHandInfo,
    RecordedHand,
)
from poker.memory.hand_outcome_detector import (
    STACK_DOMINANCE_EXCESS_CAP,
    STACK_DOMINANCE_THRESHOLD,
    HandOutcomeDetector,
)
from poker.memory.relationship_events import RelationshipEvent

# At $50 stake the max buy-in is $50 * 100 = 5000 chips. Threshold for
# STACK_DOMINANCE is 1.5× that = 7500 chips. Constants picked to match
# a real cash-mode tier so the test reads like a realistic table.
MAX_BUY_IN = 5000
THRESHOLD_CHIPS = int(STACK_DOMINANCE_THRESHOLD * MAX_BUY_IN)


def _player(name: str, starting_stack: int) -> PlayerHandInfo:
    return PlayerHandInfo(
        name=name,
        starting_stack=starting_stack,
        position="BTN",
        is_human=False,
    )


def _build_hand(
    *,
    stacks: Dict[str, int],
    hand_number: int = 1,
) -> RecordedHand:
    """Minimal RecordedHand with just the seat list — STACK_DOMINANCE
    reads only `players[i].starting_stack`, so the other fields can be
    skeletal. Set as `was_showdown=False` and no winners so the other
    detectors stay silent and don't muddy the assertion."""
    players = tuple(_player(name, stack) for name, stack in stacks.items())
    return RecordedHand(
        game_id="g1",
        hand_number=hand_number,
        timestamp=datetime(2026, 5, 23, 12, 0),
        players=players,
        hole_cards={},
        community_cards=(),
        actions=(),
        winners=(),
        pot_size=0,
        was_showdown=False,
    )


def _build_lookup(pnls: Dict[Tuple[str, str], int]):
    """Return a cash_pnl_lookup closure backed by an in-memory dict.

    `pnls[(observer, deep)] = chips` — positive when the observer is
    up against deep, negative when down. Missing pairs return 0 to
    match the production behavior of a brand-new pair with no PnL row.
    """

    def lookup(observer_id: str, deep_id: str) -> int:
        return pnls.get((observer_id, deep_id), 0)

    return lookup


class TestNoEmission:
    def test_max_buy_in_none_skips_detector(self):
        # Tournament callers leave max_buy_in unset.
        hand = _build_hand(stacks={"alice": 50_000, "bob": 1000})
        det = HandOutcomeDetector()
        events = det.detect_events(hand, max_buy_in=None)
        assert [e for e in events if e.event is RelationshipEvent.STACK_DOMINANCE] == []

    def test_max_buy_in_zero_skips_detector(self):
        hand = _build_hand(stacks={"alice": 50_000, "bob": 1000})
        det = HandOutcomeDetector()
        events = det.detect_events(hand, max_buy_in=0)
        assert [e for e in events if e.event is RelationshipEvent.STACK_DOMINANCE] == []

    def test_single_seat_emits_nothing(self):
        # Need at least 2 players for the pair to make sense.
        hand = _build_hand(stacks={"alice": 50_000})
        det = HandOutcomeDetector()
        events = det.detect_events(hand, max_buy_in=MAX_BUY_IN)
        assert events == []

    def test_no_deep_stacks_emits_nothing(self):
        # Everyone below 1.5× cap — no resentment.
        hand = _build_hand(
            stacks={
                "alice": 4000,  # 0.8× cap
                "bob": 6000,  # 1.2× cap
                "carol": 5500,  # 1.1× cap
            }
        )
        det = HandOutcomeDetector()
        events = det.detect_events(hand, max_buy_in=MAX_BUY_IN)
        assert events == []

    def test_exactly_at_threshold_emits_nothing(self):
        # 1.5× cap = excess of 0 → multiplier 0 → no shift. The
        # detector filters this out rather than emit a no-op event.
        hand = _build_hand(
            stacks={
                "deep": THRESHOLD_CHIPS,
                "shorty": 2000,
            }
        )
        det = HandOutcomeDetector()
        events = det.detect_events(hand, max_buy_in=MAX_BUY_IN)
        assert events == []


class TestEmissionWithoutPnLGate:
    """No lookup → every seated peer resents the deep stack. This is
    the test-only / early-sandbox path."""

    def test_one_deep_stack_emits_to_each_peer(self):
        hand = _build_hand(
            stacks={
                "deep": 10_000,  # 2.0× cap → excess 0.5
                "alice": 2000,
                "bob": 3000,
            }
        )
        det = HandOutcomeDetector()
        events = [
            e
            for e in det.detect_events(hand, max_buy_in=MAX_BUY_IN)
            if e.event is RelationshipEvent.STACK_DOMINANCE
        ]
        assert len(events) == 2
        actors = {e.actor_id for e in events}
        assert actors == {"alice", "bob"}
        assert all(e.target_id == "deep" for e in events)
        # 10_000 / 5000 - 1.5 = 0.5 — same multiplier for both pairs.
        assert all(e.context_multiplier == pytest.approx(0.5) for e in events)

    def test_two_deep_stacks_pair_with_every_other_seat(self):
        # 4 seats: 2 deep, 2 short. Each deep stack is observed by
        # every other seat (including the other deep stack):
        #   deep_a → seen by {deep_b, alice, bob} = 3 events
        #   deep_b → seen by {deep_a, alice, bob} = 3 events
        # Total 6 events.
        hand = _build_hand(
            stacks={
                "deep_a": 12_000,
                "deep_b": 10_000,
                "alice": 3000,
                "bob": 2500,
            }
        )
        det = HandOutcomeDetector()
        events = [
            e
            for e in det.detect_events(hand, max_buy_in=MAX_BUY_IN)
            if e.event is RelationshipEvent.STACK_DOMINANCE
        ]
        assert len(events) == 6
        targets = sorted(e.target_id for e in events)
        # Each deep stack should appear as target 3 times.
        assert targets.count("deep_a") == 3
        assert targets.count("deep_b") == 3

    def test_context_multiplier_scales_with_excess(self):
        # 3× cap → excess 1.5
        hand = _build_hand(
            stacks={
                "whale": 15_000,
                "fish": 2000,
            }
        )
        det = HandOutcomeDetector()
        events = [
            e
            for e in det.detect_events(hand, max_buy_in=MAX_BUY_IN)
            if e.event is RelationshipEvent.STACK_DOMINANCE
        ]
        assert len(events) == 1
        assert events[0].context_multiplier == pytest.approx(1.5)

    def test_context_multiplier_saturates_at_cap(self):
        # 10× cap would naively yield excess 8.5 — capped at 2.0 so a
        # single session against a whale can't tank a pair's axes.
        hand = _build_hand(
            stacks={
                "whale": 10 * MAX_BUY_IN,
                "fish": 2000,
            }
        )
        det = HandOutcomeDetector()
        events = [
            e
            for e in det.detect_events(hand, max_buy_in=MAX_BUY_IN)
            if e.event is RelationshipEvent.STACK_DOMINANCE
        ]
        assert len(events) == 1
        assert events[0].context_multiplier == pytest.approx(STACK_DOMINANCE_EXCESS_CAP)


class TestPnLGate:
    """With a lookup wired, only observers who have lost chips to the
    deep stack emit. Strangers and net-up observers stay neutral."""

    def test_only_net_down_observers_emit(self):
        hand = _build_hand(
            stacks={
                "deep": 10_000,
                "loser": 2000,
                "winner": 3000,
                "neutral": 2500,
            }
        )
        lookup = _build_lookup(
            {
                ("loser", "deep"): -500,  # loser is down 500 to deep
                ("winner", "deep"): +200,  # winner is up on deep
                ("neutral", "deep"): 0,  # neutral has no history
            }
        )
        det = HandOutcomeDetector()
        events = [
            e
            for e in det.detect_events(
                hand,
                max_buy_in=MAX_BUY_IN,
                cash_pnl_lookup=lookup,
            )
            if e.event is RelationshipEvent.STACK_DOMINANCE
        ]
        assert len(events) == 1
        assert events[0].actor_id == "loser"
        assert events[0].target_id == "deep"

    def test_zero_pnl_is_treated_as_no_resentment(self):
        # Brand-new pair with no prior chip flow → 0 PnL → no event.
        # This is the "early sandbox" behavior: resentment only kicks
        # in once the observer has actually lost to the deep stack.
        hand = _build_hand(
            stacks={
                "deep": 10_000,
                "stranger": 2000,
            }
        )
        lookup = _build_lookup({})  # all pairs default to 0
        det = HandOutcomeDetector()
        events = [
            e
            for e in det.detect_events(
                hand,
                max_buy_in=MAX_BUY_IN,
                cash_pnl_lookup=lookup,
            )
            if e.event is RelationshipEvent.STACK_DOMINANCE
        ]
        assert events == []

    def test_lookup_exception_skips_pair_silently(self):
        # A pnl lookup that raises shouldn't crash the detector. The
        # affected pair is treated as "no data" and skipped, but
        # other pairs in the same hand still get evaluated.
        hand = _build_hand(
            stacks={
                "deep": 10_000,
                "broken": 2000,
                "loser": 3000,
            }
        )

        def lookup(observer_id, deep_id):
            if observer_id == "broken":
                raise RuntimeError("repo blew up")
            return -100  # loser is down

        det = HandOutcomeDetector()
        events = [
            e
            for e in det.detect_events(
                hand,
                max_buy_in=MAX_BUY_IN,
                cash_pnl_lookup=lookup,
            )
            if e.event is RelationshipEvent.STACK_DOMINANCE
        ]
        assert len(events) == 1
        assert events[0].actor_id == "loser"


class TestDedup:
    def test_same_hand_emits_once(self):
        # Detector dedup is keyed on (hand_number, actor, target, event).
        # Calling detect_events twice on the same hand should yield
        # events the first time and nothing the second time.
        hand = _build_hand(
            stacks={
                "deep": 10_000,
                "alice": 2000,
            }
        )
        det = HandOutcomeDetector()
        first = [
            e
            for e in det.detect_events(hand, max_buy_in=MAX_BUY_IN)
            if e.event is RelationshipEvent.STACK_DOMINANCE
        ]
        second = [
            e
            for e in det.detect_events(hand, max_buy_in=MAX_BUY_IN)
            if e.event is RelationshipEvent.STACK_DOMINANCE
        ]
        assert len(first) == 1
        assert second == []
