"""
Tests for OpponentModelManager.aggregate_active_opponents().

Phase 6: validates the multiway 60% rule used to focus exploitation on the
credible threat when one opponent has driven the action.
"""

import os
import sys
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from poker.memory.opponent_model import (
    OpponentModelManager,
    OpponentTendencies,
)
from poker.strategy.exploitation import AggregatedOpponentStats


def _seed_tendencies(
    manager: OpponentModelManager,
    observer: str,
    opponent: str,
    *,
    hands_observed: int = 50,
    vpip: float = 0.5,
    pfr: float = 0.25,
    aggression_factor: float = 1.5,
    all_in_frequency: float = 0.05,
) -> None:
    """Inject specific OpponentTendencies values directly into a model.

    Going through observe_action() works but it's hard to land on exact
    stat values; tests assert exact aggregation math, so we set fields
    explicitly. The aggregation method only reads the public stat fields,
    so the private counters don't need to be consistent.
    """
    model = manager.get_model(observer, opponent)
    t = model.tendencies
    t.hands_observed = hands_observed
    t.vpip = vpip
    t.pfr = pfr
    t.aggression_factor = aggression_factor
    t.all_in_frequency = all_in_frequency


class TestAggregateActiveOpponents(unittest.TestCase):
    """Tests for OpponentModelManager.aggregate_active_opponents()."""

    def test_empty_active_opponents_returns_zero_stats(self):
        manager = OpponentModelManager()
        _seed_tendencies(manager, "Hero", "Bob", hands_observed=30)

        result = manager.aggregate_active_opponents("Hero", [])

        self.assertEqual(result, AggregatedOpponentStats())
        self.assertEqual(result.hands_observed, 0)

    def test_no_models_returns_zero_stats(self):
        """Opponents named, but observer has no models at all."""
        manager = OpponentModelManager()

        result = manager.aggregate_active_opponents("Hero", ["Bob", "Carol"])

        self.assertEqual(result, AggregatedOpponentStats())
        self.assertEqual(result.hands_observed, 0)

    def test_zero_hand_opponents_excluded(self):
        """An opponent with a model but hands_observed=0 should be skipped."""
        manager = OpponentModelManager()
        # Touch model so it exists but leave hands_observed at default (0)
        manager.get_model("Hero", "Bob")
        self.assertEqual(
            manager.models["Hero"]["Bob"].tendencies.hands_observed, 0
        )

        result = manager.aggregate_active_opponents("Hero", ["Bob"])

        self.assertEqual(result, AggregatedOpponentStats())

    def test_single_opponent_with_history_returns_their_stats(self):
        manager = OpponentModelManager()
        _seed_tendencies(
            manager, "Hero", "Bob",
            hands_observed=42,
            vpip=0.33,
            pfr=0.18,
            aggression_factor=2.5,
            all_in_frequency=0.08,
        )

        result = manager.aggregate_active_opponents("Hero", ["Bob"])

        self.assertEqual(result.hands_observed, 42)
        self.assertAlmostEqual(result.vpip, 0.33)
        self.assertAlmostEqual(result.pfr, 0.18)
        self.assertAlmostEqual(result.aggression_factor, 2.5)
        self.assertAlmostEqual(result.all_in_frequency, 0.08)

    def test_multiway_equal_weight_average(self):
        """3 opponents with distinct vpips (0.2, 0.5, 0.8) -> avg = 0.5."""
        manager = OpponentModelManager()
        _seed_tendencies(manager, "Hero", "A", hands_observed=100, vpip=0.2,
                         pfr=0.10, aggression_factor=1.0, all_in_frequency=0.0)
        _seed_tendencies(manager, "Hero", "B", hands_observed=100, vpip=0.5,
                         pfr=0.20, aggression_factor=2.0, all_in_frequency=0.1)
        _seed_tendencies(manager, "Hero", "C", hands_observed=100, vpip=0.8,
                         pfr=0.30, aggression_factor=3.0, all_in_frequency=0.2)

        result = manager.aggregate_active_opponents(
            "Hero", ["A", "B", "C"], money_committed=None
        )

        self.assertAlmostEqual(result.vpip, 0.5)
        self.assertAlmostEqual(result.pfr, 0.20)
        self.assertAlmostEqual(result.aggression_factor, 2.0)
        self.assertAlmostEqual(result.all_in_frequency, 0.1)

    def test_multiway_60_percent_concentrates(self):
        """A puts in 70% of committed money -> result is exactly A's stats."""
        manager = OpponentModelManager()
        _seed_tendencies(manager, "Hero", "A", hands_observed=120, vpip=0.85,
                         pfr=0.55, aggression_factor=4.5, all_in_frequency=0.4)
        _seed_tendencies(manager, "Hero", "B", hands_observed=80, vpip=0.20,
                         pfr=0.10, aggression_factor=1.0, all_in_frequency=0.0)
        _seed_tendencies(manager, "Hero", "C", hands_observed=60, vpip=0.30,
                         pfr=0.15, aggression_factor=1.2, all_in_frequency=0.0)

        result = manager.aggregate_active_opponents(
            "Hero",
            ["A", "B", "C"],
            money_committed={"A": 700, "B": 200, "C": 100},
        )

        # A has 70% of 1000 committed -> dominant
        self.assertEqual(result.hands_observed, 120)
        self.assertAlmostEqual(result.vpip, 0.85)
        self.assertAlmostEqual(result.pfr, 0.55)
        self.assertAlmostEqual(result.aggression_factor, 4.5)
        self.assertAlmostEqual(result.all_in_frequency, 0.4)

    def test_multiway_below_60_percent_uses_average(self):
        """A has 50% of pot committed; below 60% threshold -> equal-weight avg."""
        manager = OpponentModelManager()
        _seed_tendencies(manager, "Hero", "A", hands_observed=100, vpip=0.6,
                         pfr=0.30, aggression_factor=3.0, all_in_frequency=0.2)
        _seed_tendencies(manager, "Hero", "B", hands_observed=100, vpip=0.3,
                         pfr=0.15, aggression_factor=1.5, all_in_frequency=0.05)
        _seed_tendencies(manager, "Hero", "C", hands_observed=100, vpip=0.3,
                         pfr=0.15, aggression_factor=1.5, all_in_frequency=0.05)

        result = manager.aggregate_active_opponents(
            "Hero",
            ["A", "B", "C"],
            money_committed={"A": 500, "B": 300, "C": 200},
        )

        # No one over 60% -> equal-weight average
        self.assertAlmostEqual(result.vpip, (0.6 + 0.3 + 0.3) / 3)
        self.assertAlmostEqual(result.pfr, (0.30 + 0.15 + 0.15) / 3)
        self.assertAlmostEqual(result.aggression_factor, (3.0 + 1.5 + 1.5) / 3)
        self.assertAlmostEqual(result.all_in_frequency, (0.2 + 0.05 + 0.05) / 3)

    def test_hands_observed_is_min_when_weight_averaging(self):
        """When averaging, hands_observed = MIN across opponents."""
        manager = OpponentModelManager()
        _seed_tendencies(manager, "Hero", "A", hands_observed=50,
                         vpip=0.4, pfr=0.2, aggression_factor=1.5, all_in_frequency=0.1)
        _seed_tendencies(manager, "Hero", "B", hands_observed=80,
                         vpip=0.5, pfr=0.25, aggression_factor=2.0, all_in_frequency=0.1)
        _seed_tendencies(manager, "Hero", "C", hands_observed=100,
                         vpip=0.6, pfr=0.30, aggression_factor=2.5, all_in_frequency=0.1)

        result = manager.aggregate_active_opponents(
            "Hero", ["A", "B", "C"], money_committed=None
        )

        self.assertEqual(result.hands_observed, 50)

    def test_hands_observed_is_dominant_when_60_rule_fires(self):
        """When 60% rule fires, hands_observed = the dominant opponent's count."""
        manager = OpponentModelManager()
        _seed_tendencies(manager, "Hero", "A", hands_observed=120,
                         vpip=0.7, pfr=0.4, aggression_factor=3.5, all_in_frequency=0.3)
        _seed_tendencies(manager, "Hero", "B", hands_observed=50,
                         vpip=0.3, pfr=0.15, aggression_factor=1.0, all_in_frequency=0.0)
        _seed_tendencies(manager, "Hero", "C", hands_observed=200,
                         vpip=0.4, pfr=0.20, aggression_factor=1.5, all_in_frequency=0.05)

        result = manager.aggregate_active_opponents(
            "Hero",
            ["A", "B", "C"],
            money_committed={"A": 700, "B": 150, "C": 150},
        )

        # A is dominant (70%) -> use A's hands_observed even though C has more
        self.assertEqual(result.hands_observed, 120)


class TestHandsDealt(unittest.TestCase):
    """VPIP/PFR/all_in_frequency must use hands_dealt as denominator,
    not hands_observed. hands_observed only counts hands where the
    opponent took at least one action; hands_dealt counts hands the
    opponent was at the table — the correct denominator since folding
    before action is a real "opted out" outcome.
    """

    def test_record_hand_dealt_increments_counter(self):
        from poker.memory.opponent_model import OpponentModel
        model = OpponentModel(observer='Hero', opponent='Villain')
        self.assertEqual(model.tendencies.hands_dealt, 0)
        model.record_hand_dealt(hand_number=1)
        self.assertEqual(model.tendencies.hands_dealt, 1)
        model.record_hand_dealt(hand_number=2)
        self.assertEqual(model.tendencies.hands_dealt, 2)

    def test_record_hand_dealt_idempotent_within_hand(self):
        """Calling twice with same hand_number must only increment once."""
        from poker.memory.opponent_model import OpponentModel
        model = OpponentModel(observer='Hero', opponent='Villain')
        model.record_hand_dealt(hand_number=1)
        model.record_hand_dealt(hand_number=1)
        model.record_hand_dealt(hand_number=1)
        self.assertEqual(model.tendencies.hands_dealt, 1)

    def test_vpip_uses_hands_dealt_when_set(self):
        """VPIP denominator must be hands_dealt, not hands_observed.

        Reproduces the original bug: opponent who folds half their
        hands gets observe_action called only when they enter the pot,
        so hands_observed=their_entries. With the fix, hands_dealt
        captures all dealt hands.
        """
        from poker.memory.opponent_model import OpponentModel
        model = OpponentModel(observer='Hero', opponent='Villain')

        # Villain is dealt 10 hands, voluntarily entered 3 of them.
        for h in range(10):
            model.record_hand_dealt(hand_number=h)
        # Simulate 3 voluntary entries (call/raise); folds-before-action
        # wouldn't trigger observe_action.
        for h in range(3):
            model.observe_action(action='call', phase='PRE_FLOP',
                                 hand_number=h + 100)

        # VPIP should be 3 / 10 = 0.30, not 3 / 3 = 1.0
        self.assertAlmostEqual(model.tendencies.vpip, 0.30, places=3)
        self.assertEqual(model.tendencies.hands_dealt, 10)
        self.assertEqual(model.tendencies.hands_observed, 3)

    def test_vpip_falls_back_to_hands_observed_when_no_record(self):
        """When record_hand_dealt isn't called, behavior must match old
        code path (hands_observed denominator). Backwards compatibility.
        """
        from poker.memory.opponent_model import OpponentModel
        model = OpponentModel(observer='Hero', opponent='Villain')
        # No record_hand_dealt calls — only observed actions
        for h in range(3):
            model.observe_action(action='call', phase='PRE_FLOP',
                                 hand_number=h)

        # hands_dealt=0, so falls back to hands_observed=3 → VPIP = 3/3 = 1.0
        self.assertEqual(model.tendencies.hands_dealt, 0)
        self.assertAlmostEqual(model.tendencies.vpip, 1.0, places=3)

    def test_manager_record_hand_dealt_covers_all_opponents(self):
        """Manager-level method records the hand for every opponent in
        the active list at once.
        """
        manager = OpponentModelManager()
        manager.record_hand_dealt(
            observer='Hero',
            opponents=['A', 'B', 'C'],
            hand_number=1,
        )
        for opp in ('A', 'B', 'C'):
            self.assertEqual(
                manager.get_model('Hero', opp).tendencies.hands_dealt, 1
            )

    def test_to_dict_includes_hands_dealt(self):
        t = OpponentTendencies(hands_observed=5, hands_dealt=10)
        d = t.to_dict()
        self.assertEqual(d['hands_dealt'], 10)
        restored = OpponentTendencies.from_dict(d)
        self.assertEqual(restored.hands_dealt, 10)


if __name__ == '__main__':
    unittest.main()
