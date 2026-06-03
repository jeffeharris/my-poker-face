"""Unit tests for the KNOCKOUT / NEMESIS / REGULAR relationship events and the
`final_stack` plumbing they rely on. Detector-level (pure) — no DB needed.
"""

from __future__ import annotations

from datetime import datetime

from poker.memory.hand_history import (
    HandInProgress,
    PlayerHandInfo,
    RecordedAction,
    RecordedHand,
    WinnerInfo,
)
from poker.memory.hand_outcome_detector import (
    NEMESIS_BIG_LOSS_COUNT,
    NEMESIS_MIN_HANDS,
    REGULAR_COOLDOWN_HANDS,
    REGULAR_MAX_FIRES,
    RIVAL_MIN_CLASHES,
    RIVAL_MIN_HANDS,
    HandOutcomeDetector,
)

# A hands_played_lookup that reports plenty of shared history (volume gate
# satisfied) so tests can focus on the clash-count logic.
_PLENTY_HANDS = lambda a, b: 10_000  # noqa: E731
from poker.memory.relationship_events import RelationshipEvent


def _hand(
    hand_number,
    players,
    *,
    winners=(),
    actions=(),
    pot_size=0,
    was_showdown=False,
    hole_cards=None,
):
    return RecordedHand(
        game_id="g",
        hand_number=hand_number,
        timestamp=datetime(2026, 6, 2, 12, 0),
        players=tuple(players),
        hole_cards=hole_cards or {},
        community_cards=(),
        actions=tuple(actions),
        winners=tuple(winners),
        pot_size=pot_size,
        was_showdown=was_showdown,
    )


class TestFinalStackPlumbing:
    def test_player_hand_info_round_trips_final_stack(self):
        p = PlayerHandInfo(
            name="a", starting_stack=100, position="BTN", is_human=False, final_stack=0
        )
        assert PlayerHandInfo.from_dict(p.to_dict()).final_stack == 0

    def test_final_stack_defaults_none_for_legacy_rows(self):
        legacy = {
            "name": "a",
            "starting_stack": 100,
            "position": "BTN",
            "is_human": False,
        }
        assert PlayerHandInfo.from_dict(legacy).final_stack is None

    def test_complete_stamps_final_stacks(self):
        h = HandInProgress("g", 1)
        h.add_player("a", 100, "BTN", False)
        h.add_player("b", 100, "BB", False)
        rec = h.complete(
            winners=[], pot_size=0, was_showdown=False, final_stacks={"a": 250, "b": 0}
        )
        assert {p.name: p.final_stack for p in rec.players} == {"a": 250, "b": 0}


def _bust_hand(hand_number, *, bob_final):
    """Heads-up all-in: alice calls bob's shove. bob ends on `bob_final`."""
    players = [
        PlayerHandInfo("alice", 4000, "BTN", False, final_stack=8000),
        PlayerHandInfo("bob", 2000, "BB", False, final_stack=bob_final),
    ]
    actions = [
        RecordedAction("bob", "all_in", 2000, "PRE_FLOP", 2000),
        RecordedAction("alice", "call", 2000, "PRE_FLOP", 4000),
    ]
    winners = [WinnerInfo("alice", 4000, "Pair", 2)]
    return _hand(
        hand_number,
        players,
        winners=winners,
        actions=actions,
        pot_size=4000,
        was_showdown=True,
        hole_cards={"alice": ["Ah", "Ad"], "bob": ["Kh", "Kd"]},
    )


class TestKnockout:
    def test_bust_triggers_knockout_attributed_to_buster(self):
        det = HandOutcomeDetector()
        events = det.detect_events(_bust_hand(1, bob_final=0))
        ko = [e for e in events if e.event == RelationshipEvent.KNOCKOUT]
        assert len(ko) == 1
        assert ko[0].actor_id == "alice"  # the buster
        assert ko[0].target_id == "bob"  # busted

    def test_loss_without_bust_does_not_knock_out(self):
        det = HandOutcomeDetector()
        events = det.detect_events(_bust_hand(1, bob_final=1500))  # lost but survived
        assert not [e for e in events if e.event == RelationshipEvent.KNOCKOUT]

    def test_no_knockout_when_final_stack_unknown(self):
        det = HandOutcomeDetector()
        events = det.detect_events(_bust_hand(1, bob_final=None))
        assert not [e for e in events if e.event == RelationshipEvent.KNOCKOUT]


def _big_loss_hand(hand_number, *, loser, winner, stack=5000):
    """A big pot the `loser` ships to the `winner`. pot=2*stack vs avg=1.5*stack
    clears the big-pot gate at any scale, so the same shape works for high and
    low stakes by varying `stack`.
    """
    players = [
        PlayerHandInfo(winner, stack, "BTN", False, final_stack=2 * stack),
        PlayerHandInfo(loser, stack // 2, "BB", False, final_stack=0),
    ]
    actions = [
        RecordedAction(loser, "all_in", stack // 2, "PRE_FLOP", stack // 2),
        RecordedAction(winner, "call", stack // 2, "PRE_FLOP", stack),
    ]
    return _hand(
        hand_number,
        players,
        winners=[WinnerInfo(winner, stack, "Pair", 2)],
        actions=actions,
        pot_size=stack,
        was_showdown=True,
        hole_cards={winner: ["Ah", "Ad"], loser: ["Kh", "Kd"]},
    )


class TestNemesis:
    def _run_losses(self, det, n, *, loser, winner, start=1, stack=5000, hands=_PLENTY_HANDS):
        fired = []
        for i in range(n):
            evs = det.detect_events(
                _big_loss_hand(start + i, loser=loser, winner=winner, stack=stack),
                max_buy_in=stack,
                hands_played_lookup=hands,
            )
            fired += [e for e in evs if e.event == RelationshipEvent.NEMESIS]
        return fired

    def test_fires_after_threshold_big_losses(self):
        det = HandOutcomeDetector()
        fired = self._run_losses(det, NEMESIS_BIG_LOSS_COUNT, loser="bob", winner="alice")
        assert len(fired) == 1
        assert fired[0].actor_id == "bob" and fired[0].target_id == "alice"
        # Latched — further losses don't re-fire.
        more = self._run_losses(det, 2, loser="bob", winner="alice", start=100)
        assert not more

    def test_no_fire_below_threshold(self):
        det = HandOutcomeDetector()
        fired = self._run_losses(det, NEMESIS_BIG_LOSS_COUNT - 1, loser="bob", winner="alice")
        assert not fired

    def test_reachable_at_low_stakes(self):
        # Tiny stacks, but "big pot" is stack-relative — so a low-stakes victim
        # still reaches nemesis in the same number of beats. A chip threshold
        # could never do this.
        det = HandOutcomeDetector()
        fired = self._run_losses(
            det, NEMESIS_BIG_LOSS_COUNT, loser="bob", winner="alice", stack=200
        )
        assert len(fired) == 1

    def test_mutual_nemesis_in_even_war(self):
        # alice and bob trade big-pot beats. Both cross the threshold, so BOTH
        # consider the other a nemesis — impossible under a pure net metric.
        det = HandOutcomeDetector()
        fired = []
        hn = 0
        for _ in range(NEMESIS_BIG_LOSS_COUNT):
            hn += 1
            evs = det.detect_events(
                _big_loss_hand(hn, loser="bob", winner="alice"),
                max_buy_in=5000,
                hands_played_lookup=_PLENTY_HANDS,
            )
            fired += [e for e in evs if e.event == RelationshipEvent.NEMESIS]
            hn += 1
            evs = det.detect_events(
                _big_loss_hand(hn, loser="alice", winner="bob"),
                max_buy_in=5000,
                hands_played_lookup=_PLENTY_HANDS,
            )
            fired += [e for e in evs if e.event == RelationshipEvent.NEMESIS]
        directed = {(e.actor_id, e.target_id) for e in fired}
        assert ("bob", "alice") in directed
        assert ("alice", "bob") in directed  # mutual

    def test_one_directional_when_lopsided(self):
        # bob loses repeatedly to alice; alice never loses to bob. Only bob
        # gets the nemesis — alice, who's crushing bob, does not.
        det = HandOutcomeDetector()
        fired = self._run_losses(det, NEMESIS_BIG_LOSS_COUNT + 2, loser="bob", winner="alice")
        directed = {(e.actor_id, e.target_id) for e in fired}
        assert directed == {("bob", "alice")}

    def test_hand_count_gate_blocks_nemesis(self):
        # Enough big-pot losses, but too little shared history → no nemesis yet.
        det = HandOutcomeDetector()
        fired = self._run_losses(
            det,
            NEMESIS_BIG_LOSS_COUNT + 1,
            loser="bob",
            winner="alice",
            hands=lambda a, b: NEMESIS_MIN_HANDS - 1,
        )
        assert not fired


class TestRival:
    def _big_loss_hand_pair(self, hn, loser, winner):
        return _big_loss_hand(hn, loser=loser, winner=winner, stack=5000)

    def test_rival_fires_mutually_after_clashes_and_hands(self):
        det = HandOutcomeDetector()
        fired = []
        # RIVAL_MIN_CLASHES clashes (split direction), plenty of shared hands.
        for i in range(RIVAL_MIN_CLASHES):
            loser, winner = ("bob", "alice") if i % 2 == 0 else ("alice", "bob")
            evs = det.detect_events(
                self._big_loss_hand_pair(i + 1, loser, winner),
                max_buy_in=5000,
                hands_played_lookup=_PLENTY_HANDS,
            )
            fired += [e for e in evs if e.event == RelationshipEvent.RIVAL]
        # RIVAL fires once for the (unordered) pair.
        assert len(fired) == 1
        assert {fired[0].actor_id, fired[0].target_id} == {"alice", "bob"}

    def test_rival_blocked_by_hand_count(self):
        det = HandOutcomeDetector()
        fired = []
        for i in range(RIVAL_MIN_CLASHES + 1):
            loser, winner = ("bob", "alice") if i % 2 == 0 else ("alice", "bob")
            evs = det.detect_events(
                self._big_loss_hand_pair(i + 1, loser, winner),
                max_buy_in=5000,
                hands_played_lookup=lambda a, b: RIVAL_MIN_HANDS - 1,
            )
            fired += [e for e in evs if e.event == RelationshipEvent.RIVAL]
        assert not fired

    def test_no_rivalries_without_hands_lookup(self):
        # No cash-mode hands lookup wired → tiers don't run at all.
        det = HandOutcomeDetector()
        evs = det.detect_events(
            _big_loss_hand(1, loser="bob", winner="alice", stack=5000),
            max_buy_in=5000,
        )
        assert not [
            e for e in evs if e.event in (RelationshipEvent.RIVAL, RelationshipEvent.NEMESIS)
        ]


class TestRegularCap:
    def test_regular_plateaus_at_max_fires(self):
        det = HandOutcomeDetector()
        players = [
            PlayerHandInfo("alice", 1000, "BTN", False),
            PlayerHandInfo("bob", 1000, "BB", False),
        ]
        fires = 0
        # Run well past MAX_FIRES * COOLDOWN peaceful hands.
        for hn in range(1, REGULAR_COOLDOWN_HANDS * (REGULAR_MAX_FIRES + 4) + 1):
            evs = det.detect_events(_hand(hn, players))
            fires += sum(1 for e in evs if e.event == RelationshipEvent.REGULAR)
        assert fires == REGULAR_MAX_FIRES

    def test_first_shared_hand_only_starts_clock(self):
        det = HandOutcomeDetector()
        players = [
            PlayerHandInfo("alice", 1000, "BTN", False),
            PlayerHandInfo("bob", 1000, "BB", False),
        ]
        evs = det.detect_events(_hand(1, players))
        # Familiarity is earned — no bump on the very first shared hand.
        assert not [e for e in evs if e.event == RelationshipEvent.REGULAR]
