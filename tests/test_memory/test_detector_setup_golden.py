"""Golden gate for the HandOutcomeDetector setup-block refactor.

Behaviour-preserving: this file pins the EXACT events emitted by the five
detectors that share the ``revealed_ranks`` / fold setup block
(``_detect_hero_calls``, ``_detect_bluffed_off``, ``_detect_dominated_showdown``,
``_detect_coolers``, ``_detect_strong_fold_shown``) so an internal extraction
of the common precompute cannot change observable output.

Expected values below are HARD-CODED from the CURRENT code (captured before
any refactor) — not recomputed from the implementation under test. Each case
asserts the full tuple of ``(event, actor_id, target_id, narrative)`` for the
detector-of-interest's events, plus regimes that must emit nothing.

Regimes covered per detector:
  - showdown vs non-showdown (was_showdown=False short-circuit)
  - missing / empty / malformed community cards
  - winners present / fewer than two revealed ranks
  - the positive trigger condition for each event
  - the negative (no-trigger) condition for each event

Hand-strength reference (board 2c 5h 8c Tc Jd, "DRY"):
  AA  -> one pair, rank 9
  76s -> jack high, rank 10
For coolers we use a board that gives both sides a strong (rank<=7) hand.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Tuple

from poker.memory.hand_history import (
    PlayerHandInfo,
    RecordedAction,
    RecordedHand,
    WinnerInfo,
)
from poker.memory.hand_outcome_detector import HandOutcomeDetector
from poker.memory.relationship_events import RelationshipEvent


# --------------------------------------------------------------------------
# Builders (mirror the existing detector test fixtures).
# --------------------------------------------------------------------------
def _player(name: str, stack: int = 1000, position: str = "BTN") -> PlayerHandInfo:
    return PlayerHandInfo(name=name, starting_stack=stack, position=position, is_human=False)


def _action(name, action, amount, phase="RIVER", pot_after=0):
    return RecordedAction(
        player_name=name, action=action, amount=amount, phase=phase, pot_after=pot_after
    )


def _hand(
    *,
    hole_cards: dict,
    community: tuple,
    actions: list,
    winners: list,
    pot_size: int = 200,
    hand_number: int = 1,
    was_showdown: bool = True,
    players: list = None,
) -> RecordedHand:
    if players is None:
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


def _events_of(events, kind) -> List[Tuple]:
    """Return a sorted list of (actor_id, target_id, narrative) for `kind`."""
    return sorted(
        (e.actor_id, e.target_id, e.narrative) for e in events if e.event is kind
    )


COMMUNITY_DRY = ("2c", "5h", "8c", "Tc", "Jd")
HOLE_ALICE_AA = ["Ah", "Ad"]
HOLE_BOB_HIGH = ["7s", "6s"]


# ==========================================================================
# HERO_CALL  (_detect_hero_calls)
# ==========================================================================
class TestHeroCallGolden:
    def test_river_bet_call_fires(self):
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "call", 100, phase="RIVER", pot_after=200),
            ],
            winners=[WinnerInfo("alice", 200, "Pair", 9)],
            pot_size=200,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.HERO_CALL) == [
            (
                "alice",
                "bob",
                "alice called bob's river bet and showed down the winner",
            )
        ]

    def test_non_showdown_no_hero_call(self):
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "call", 100, phase="RIVER", pot_after=200),
            ],
            winners=[WinnerInfo("alice", 200, "Pair", 9)],
            pot_size=200,
            was_showdown=False,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.HERO_CALL) == []

    def test_no_community_cards_no_hero_call(self):
        # Empty board -> only hole cards evaluated; <2 revealed ranks may
        # still be 2 here, but the rank comparison flips. Capture actual.
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=(),
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "call", 100, phase="RIVER", pot_after=200),
            ],
            winners=[WinnerInfo("alice", 200, "Pair", 9)],
            pot_size=200,
        )
        events = HandOutcomeDetector().detect_events(hand)
        # alice AA (rank 9 one pair) still beats bob 76 high (rank 10) with
        # no board, so HERO_CALL still fires on a 2-card eval.
        assert _events_of(events, RelationshipEvent.HERO_CALL) == [
            (
                "alice",
                "bob",
                "alice called bob's river bet and showed down the winner",
            )
        ]

    def test_only_one_revealed_rank_no_hero_call(self):
        # bob's cards stripped -> <2 revealed ranks -> short-circuit.
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "call", 100, phase="RIVER", pot_after=200),
            ],
            winners=[WinnerInfo("alice", 200, "Pair", 9)],
            pot_size=200,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.HERO_CALL) == []

    def test_no_river_action_no_hero_call(self):
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="TURN", pot_after=100),
                _action("alice", "call", 100, phase="TURN", pot_after=200),
            ],
            winners=[WinnerInfo("alice", 200, "Pair", 9)],
            pot_size=200,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.HERO_CALL) == []


# ==========================================================================
# BLUFFED_OFF  (_detect_bluffed_off)
# ==========================================================================
# board where the FOLDER would have beaten the bettor.
# Use the dry board; folder=AA (rank 9), bettor=76 high (rank 10).
class TestBluffedOffGolden:
    def test_postflop_fold_of_winner_fires(self):
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                # bob (bettor, weaker) bets the river; alice (AA) folds.
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "fold", 0, phase="RIVER", pot_after=100),
            ],
            winners=[WinnerInfo("bob", 100, "High", 10)],
            pot_size=100,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.BLUFFED_OFF) == [
            (
                "alice",
                "bob",
                "alice folded a winner to bob's river bet",
            )
        ]

    def test_non_showdown_no_bluffed_off(self):
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "fold", 0, phase="RIVER", pot_after=100),
            ],
            winners=[WinnerInfo("bob", 100, "High", 10)],
            pot_size=100,
            was_showdown=False,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.BLUFFED_OFF) == []

    def test_incomplete_board_no_bluffed_off(self):
        # <5 community cards -> defensive guard returns [].
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=("2c", "5h", "8c"),
            actions=[
                _action("bob", "bet", 100, phase="FLOP", pot_after=100),
                _action("alice", "fold", 0, phase="FLOP", pot_after=100),
            ],
            winners=[WinnerInfo("bob", 100, "High", 10)],
            pot_size=100,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.BLUFFED_OFF) == []

    def test_preflop_fold_no_bluffed_off(self):
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="PRE_FLOP", pot_after=100),
                _action("alice", "fold", 0, phase="PRE_FLOP", pot_after=100),
            ],
            winners=[WinnerInfo("bob", 100, "High", 10)],
            pot_size=100,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.BLUFFED_OFF) == []

    def test_folder_was_behind_no_bluffed_off(self):
        # Folder (76 high) folds to bettor (AA) -> folder was behind, no event.
        hand = _hand(
            hole_cards={"alice": HOLE_BOB_HIGH, "bob": HOLE_ALICE_AA},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "fold", 0, phase="RIVER", pot_after=100),
            ],
            winners=[WinnerInfo("bob", 100, "Pair", 9)],
            pot_size=100,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.BLUFFED_OFF) == []


# ==========================================================================
# DOMINATED_SHOWDOWN  (_detect_dominated_showdown)
# ==========================================================================
# Need a category jump where NOT both sides are strong (so cooler excluded).
# winner AA -> one pair (rank 9, weak band); loser 76 -> high card (rank 10).
class TestDominatedShowdownGolden:
    def test_committed_loser_outclassed_fires(self):
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("alice", "bet", 100, phase="FLOP", pot_after=100),
                _action("bob", "call", 100, phase="FLOP", pot_after=200),
            ],
            winners=[WinnerInfo("alice", 200, "Pair", 9)],
            pot_size=200,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.DOMINATED_SHOWDOWN) == [
            (
                "bob",
                "alice",
                "bob called postflop and showed down a weaker hand than alice",
            )
        ]

    def test_no_postflop_commitment_no_dominated(self):
        # bob never called postflop -> not committed.
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("alice", "check", 0, phase="RIVER", pot_after=50),
                _action("bob", "check", 0, phase="RIVER", pot_after=50),
            ],
            winners=[WinnerInfo("alice", 50, "Pair", 9)],
            pot_size=50,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.DOMINATED_SHOWDOWN) == []

    def test_non_showdown_no_dominated(self):
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("alice", "bet", 100, phase="FLOP", pot_after=100),
                _action("bob", "call", 100, phase="FLOP", pot_after=200),
            ],
            winners=[WinnerInfo("alice", 200, "Pair", 9)],
            pot_size=200,
            was_showdown=False,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.DOMINATED_SHOWDOWN) == []


# ==========================================================================
# COOLER  (_detect_coolers)
# ==========================================================================
# Need BOTH hands strong (rank <= 7) with a STRICT category gap.
# Board: 7c 8c 9d Tc Jh  (3 clubs + straight texture)
#   alice Ac 2c -> club flush      (rank 5)
#   bob   Qd 8d -> Q-high straight  (rank 6)
# alice wins; 5 < 6, both <= 7 -> strict cooler.
COMMUNITY_COOLER = ("7c", "8c", "9d", "Tc", "Jh")
HOLE_TT = ["Ac", "2c"]   # flush (winner)
HOLE_99 = ["Qd", "8d"]   # straight (loser)


class TestCoolerGolden:
    def test_both_strong_fires_cooler(self):
        hand = _hand(
            hole_cards={"alice": HOLE_TT, "bob": HOLE_99},
            community=COMMUNITY_COOLER,
            actions=[
                _action("alice", "bet", 100, phase="FLOP", pot_after=100),
                _action("bob", "call", 100, phase="FLOP", pot_after=200),
            ],
            winners=[WinnerInfo("alice", 200, "Full House", 4)],
            pot_size=200,
        )
        events = HandOutcomeDetector().detect_events(hand)
        cooler = _events_of(events, RelationshipEvent.COOLER)
        dominated = _events_of(events, RelationshipEvent.DOMINATED_SHOWDOWN)
        assert cooler == [
            (
                "bob",
                "alice",
                "bob had a strong hand but ran into alice's stronger one",
            )
        ]
        # Mutually exclusive: dominated must NOT also fire on this matchup.
        assert dominated == []

    def test_weak_loser_no_cooler(self):
        # Dominated case from above (AA vs 76 high) must NOT emit cooler.
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("alice", "bet", 100, phase="FLOP", pot_after=100),
                _action("bob", "call", 100, phase="FLOP", pot_after=200),
            ],
            winners=[WinnerInfo("alice", 200, "Pair", 9)],
            pot_size=200,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.COOLER) == []

    def test_non_showdown_no_cooler(self):
        hand = _hand(
            hole_cards={"alice": HOLE_TT, "bob": HOLE_99},
            community=COMMUNITY_COOLER,
            actions=[
                _action("alice", "bet", 100, phase="FLOP", pot_after=100),
                _action("bob", "call", 100, phase="FLOP", pot_after=200),
            ],
            winners=[WinnerInfo("alice", 200, "Full House", 4)],
            pot_size=200,
            was_showdown=False,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.COOLER) == []


# ==========================================================================
# STRONG_FOLD_SHOWN  (_detect_strong_fold_shown)
# ==========================================================================
# Mirror of bluffed_off: folder was BEHIND the bettor.
# folder=76 high (rank 10) folds to bettor=AA (rank 9, ahead).
class TestStrongFoldShownGolden:
    def test_correct_postflop_fold_fires(self):
        hand = _hand(
            hole_cards={"alice": HOLE_BOB_HIGH, "bob": HOLE_ALICE_AA},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "fold", 0, phase="RIVER", pot_after=100),
            ],
            winners=[WinnerInfo("bob", 100, "Pair", 9)],
            pot_size=100,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.STRONG_FOLD_SHOWN) == [
            (
                "alice",
                "bob",
                "alice folded to bob's river bet and would have lost",
            )
        ]

    def test_folder_ahead_no_strong_fold(self):
        # folder AA (ahead) folds to bettor 76 high -> bluffed_off, not strong fold.
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "fold", 0, phase="RIVER", pot_after=100),
            ],
            winners=[WinnerInfo("bob", 100, "High", 10)],
            pot_size=100,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.STRONG_FOLD_SHOWN) == []

    def test_non_showdown_no_strong_fold(self):
        hand = _hand(
            hole_cards={"alice": HOLE_BOB_HIGH, "bob": HOLE_ALICE_AA},
            community=COMMUNITY_DRY,
            actions=[
                _action("bob", "bet", 100, phase="RIVER", pot_after=100),
                _action("alice", "fold", 0, phase="RIVER", pot_after=100),
            ],
            winners=[WinnerInfo("bob", 100, "Pair", 9)],
            pot_size=100,
            was_showdown=False,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.STRONG_FOLD_SHOWN) == []

    def test_incomplete_board_no_strong_fold(self):
        hand = _hand(
            hole_cards={"alice": HOLE_BOB_HIGH, "bob": HOLE_ALICE_AA},
            community=("2c", "5h", "8c"),
            actions=[
                _action("bob", "bet", 100, phase="FLOP", pot_after=100),
                _action("alice", "fold", 0, phase="FLOP", pot_after=100),
            ],
            winners=[WinnerInfo("bob", 100, "Pair", 9)],
            pot_size=100,
        )
        events = HandOutcomeDetector().detect_events(hand)
        assert _events_of(events, RelationshipEvent.STRONG_FOLD_SHOWN) == []


# ==========================================================================
# Full-emission golden: one hand, the complete set of events from all
# detectors (locks cross-detector interaction too).
# ==========================================================================
class TestFullEmissionGolden:
    def test_dominated_full_event_set(self):
        # AA vs 76, bob commits postflop -> DOMINATED_SHOWDOWN only
        # (small pot, no big-pot; no hero call; no fold).
        hand = _hand(
            hole_cards={"alice": HOLE_ALICE_AA, "bob": HOLE_BOB_HIGH},
            community=COMMUNITY_DRY,
            actions=[
                _action("alice", "bet", 50, phase="FLOP", pot_after=50),
                _action("bob", "call", 50, phase="FLOP", pot_after=100),
            ],
            winners=[WinnerInfo("alice", 100, "Pair", 9)],
            pot_size=100,
        )
        events = HandOutcomeDetector().detect_events(hand)
        got = sorted(
            (e.event.name, e.actor_id, e.target_id, e.narrative) for e in events
        )
        assert got == [
            (
                "DOMINATED_SHOWDOWN",
                "bob",
                "alice",
                "bob called postflop and showed down a weaker hand than alice",
            ),
        ]
