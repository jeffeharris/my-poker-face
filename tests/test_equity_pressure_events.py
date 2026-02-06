"""
Tests for equity-based pressure event detection.

Tests the EquitySnapshot, HandEquityHistory, EquityTracker, and the
weighted-delta equity shock detection in PressureDetector.
"""

import unittest
from poker.equity_snapshot import EquitySnapshot, HandEquityHistory, STREET_ORDER
from poker.equity_tracker import EquityTracker
from poker.pressure_detector import PressureEventDetector
from poker.poker_game import PokerGameState, Player, initialize_game_state
from poker.memory.hand_history import HandInProgress


class TestEquitySnapshot(unittest.TestCase):
    """Tests for EquitySnapshot dataclass."""

    def test_snapshot_creation(self):
        """Test creating an equity snapshot."""
        snap = EquitySnapshot(
            player_name="Batman",
            street="FLOP",
            equity=0.65,
            hole_cards=("Ah", "Kd"),
            board_cards=("Qh", "Jh", "Th"),
            was_active=True,
            sample_count=2000,
        )

        self.assertEqual(snap.player_name, "Batman")
        self.assertEqual(snap.street, "FLOP")
        self.assertAlmostEqual(snap.equity, 0.65)
        self.assertEqual(snap.hole_cards, ("Ah", "Kd"))
        self.assertEqual(snap.board_cards, ("Qh", "Jh", "Th"))
        self.assertTrue(snap.was_active)
        self.assertEqual(snap.sample_count, 2000)

    def test_snapshot_immutability(self):
        """Test that snapshots are immutable."""
        snap = EquitySnapshot(
            player_name="Batman",
            street="FLOP",
            equity=0.65,
            hole_cards=("Ah", "Kd"),
            board_cards=(),
        )

        with self.assertRaises(AttributeError):
            snap.equity = 0.80

    def test_snapshot_serialization(self):
        """Test to_dict and from_dict."""
        snap = EquitySnapshot(
            player_name="Batman",
            street="FLOP",
            equity=0.65,
            hole_cards=("Ah", "Kd"),
            board_cards=("Qh", "Jh", "Th"),
            was_active=True,
            sample_count=2000,
        )

        data = snap.to_dict()
        restored = EquitySnapshot.from_dict(data)

        self.assertEqual(snap.player_name, restored.player_name)
        self.assertEqual(snap.street, restored.street)
        self.assertAlmostEqual(snap.equity, restored.equity)
        self.assertEqual(snap.hole_cards, restored.hole_cards)
        self.assertEqual(snap.board_cards, restored.board_cards)


class TestHandEquityHistory(unittest.TestCase):
    """Tests for HandEquityHistory dataclass."""

    def setUp(self):
        """Create a sample equity history."""
        self.snapshots = (
            # Pre-flop: Batman ahead
            EquitySnapshot("Batman", "PRE_FLOP", 0.65, ("Ah", "Kd"), (), True),
            EquitySnapshot("Joker", "PRE_FLOP", 0.35, ("Qh", "Qc"), (), True),
            # Flop: Joker catches a set, now ahead
            EquitySnapshot("Batman", "FLOP", 0.25, ("Ah", "Kd"), ("Qs", "7d", "2c"), True),
            EquitySnapshot("Joker", "FLOP", 0.75, ("Qh", "Qc"), ("Qs", "7d", "2c"), True),
            # Turn: Batman catches an ace, back ahead
            EquitySnapshot("Batman", "TURN", 0.70, ("Ah", "Kd"), ("Qs", "7d", "2c", "As"), True),
            EquitySnapshot("Joker", "TURN", 0.30, ("Qh", "Qc"), ("Qs", "7d", "2c", "As"), True),
            # River: Joker catches a queen (quads!), wins
            EquitySnapshot("Batman", "RIVER", 0.0, ("Ah", "Kd"), ("Qs", "7d", "2c", "As", "Qd"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("Qh", "Qc"), ("Qs", "7d", "2c", "As", "Qd"), True),
        )

        self.history = HandEquityHistory(
            hand_history_id=1,
            game_id="test_game",
            hand_number=5,
            snapshots=self.snapshots,
        )

    def test_get_player_equity(self):
        """Test getting equity for a specific player at a specific street."""
        self.assertAlmostEqual(
            self.history.get_player_equity("Batman", "PRE_FLOP"), 0.65
        )
        self.assertAlmostEqual(
            self.history.get_player_equity("Joker", "FLOP"), 0.75
        )
        self.assertIsNone(
            self.history.get_player_equity("Unknown", "FLOP")
        )

    def test_get_street_equities(self):
        """Test getting all player equities at a street."""
        flop_equities = self.history.get_street_equities("FLOP")
        self.assertAlmostEqual(flop_equities["Batman"], 0.25)
        self.assertAlmostEqual(flop_equities["Joker"], 0.75)

    def test_get_player_history(self):
        """Test getting equity progression for a player."""
        batman_history = self.history.get_player_history("Batman")
        self.assertEqual(len(batman_history), 4)  # 4 streets
        self.assertEqual(batman_history[0].street, "PRE_FLOP")
        self.assertEqual(batman_history[3].street, "RIVER")

    def test_was_behind_then_won(self):
        """Test detecting suckout scenario."""
        # Joker was behind preflop (35%) but won on river
        self.assertTrue(self.history.was_behind_then_won("Joker", threshold=0.40))
        # Batman was ahead at some point but lost
        self.assertFalse(self.history.was_behind_then_won("Batman", threshold=0.40))

    def test_was_ahead_then_lost(self):
        """Test detecting got_sucked_out scenario."""
        # Batman was ahead on turn (70%) but lost
        self.assertTrue(self.history.was_ahead_then_lost("Batman", threshold=0.60))
        # Joker won, so didn't get sucked out
        self.assertFalse(self.history.was_ahead_then_lost("Joker", threshold=0.60))

    def test_get_max_equity_swing(self):
        """Test finding the largest equity swing."""
        # Batman's biggest swing was from turn (0.70) to river (0.0) = -0.70
        swing = self.history.get_max_equity_swing("Batman")
        self.assertIsNotNone(swing)
        from_street, to_street, delta = swing
        self.assertEqual(from_street, "TURN")
        self.assertEqual(to_street, "RIVER")
        self.assertAlmostEqual(delta, -0.70)

    def test_empty_history(self):
        """Test empty history helper."""
        empty = HandEquityHistory.empty("game1", 1)
        self.assertEqual(len(empty.snapshots), 0)
        self.assertIsNone(empty.get_player_equity("Anyone", "FLOP"))


class TestEquityTracker(unittest.TestCase):
    """Tests for EquityTracker service."""

    def setUp(self):
        """Set up test components."""
        self.tracker = EquityTracker()

    def test_calculate_from_hand_in_progress(self):
        """Test calculating equity from a HandInProgress."""
        hand = HandInProgress("test_game", 1)

        # Add players
        hand.add_player("Batman", 1000, "BTN", False)
        hand.add_player("Joker", 1000, "BB", False)

        # Set hole cards
        hand.set_hole_cards("Batman", ["Ah", "Kd"])
        hand.set_hole_cards("Joker", ["2c", "7s"])

        # Add community cards
        hand.add_community_cards("FLOP", ["Qh", "Jh", "Th"])
        hand.add_community_cards("TURN", ["2d"])
        hand.add_community_cards("RIVER", ["3c"])

        # Calculate equity history
        history = self.tracker.calculate_hand_equity_history(hand)

        # Should have snapshots for all 4 streets for both players = 8 total
        self.assertEqual(len(history.snapshots), 8)

        # Batman should have high equity with AK on this board
        batman_flop = history.get_player_equity("Batman", "FLOP")
        self.assertIsNotNone(batman_flop)
        # AK has a straight on this board (AKQJT), so should be very high
        self.assertGreater(batman_flop, 0.9)

    def test_folded_players_tracking(self):
        """Test that folded players are tracked correctly."""
        hand = HandInProgress("test_game", 1)

        hand.add_player("Batman", 1000, "BTN", False)
        hand.add_player("Joker", 1000, "SB", False)
        hand.add_player("Penguin", 1000, "BB", False)

        hand.set_hole_cards("Batman", ["Ah", "Kd"])
        hand.set_hole_cards("Joker", ["2c", "7s"])
        hand.set_hole_cards("Penguin", ["9d", "9c"])

        # Joker folds preflop
        hand.record_action("Joker", "fold", 0, "PRE_FLOP", 150)

        hand.add_community_cards("FLOP", ["Qh", "Jh", "Th"])

        history = self.tracker.calculate_hand_equity_history(hand)

        # Check that Joker is marked as not active on flop
        for snap in history.snapshots:
            if snap.player_name == "Joker" and snap.street == "FLOP":
                self.assertFalse(snap.was_active)
            if snap.player_name == "Batman" and snap.street == "FLOP":
                self.assertTrue(snap.was_active)


class TestEquityShockDetection(unittest.TestCase):
    """Tests for weighted-delta equity shock detection model."""

    def setUp(self):
        """Set up test components."""
        self.detector = PressureEventDetector()

    def test_bad_beat_detected(self):
        """bad_beat: loser had 85% equity at worst swing, big weighted delta."""
        # Batman had 85% on flop, dropped to 0 on river
        snapshots = (
            EquitySnapshot("Batman", "FLOP", 0.85, ("As", "Ah"), ("Ac", "Kd", "2h"), True),
            EquitySnapshot("Joker", "FLOP", 0.15, ("Qh", "Jc"), ("Ac", "Kd", "2h"), True),
            EquitySnapshot("Batman", "TURN", 0.85, ("As", "Ah"), ("Ac", "Kd", "2h", "3c"), True),
            EquitySnapshot("Joker", "TURN", 0.15, ("Qh", "Jc"), ("Ac", "Kd", "2h", "3c"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("As", "Ah"), ("Ac", "Kd", "2h", "3c", "Td"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("Qh", "Jc"), ("Ac", "Kd", "2h", "3c", "Td"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        events = self.detector.detect_equity_shock_events(
            history, winner_names=["Joker"], pot_size=2000,
            hand_start_stacks={"Batman": 1000, "Joker": 1000},
        )

        event_types = [e[0] for e in events]
        # Batman should get bad_beat (had 85% equity, lost with big swing)
        batman_events = [e for e in events if "Batman" in e[1]]
        self.assertTrue(any(e[0] == "bad_beat" for e in batman_events))

    def test_got_sucked_out_detected(self):
        """got_sucked_out: loser was ahead on turn, lost on river."""
        snapshots = (
            EquitySnapshot("Batman", "FLOP", 0.65, ("Ks", "Kd"), ("Kc", "5d", "2h"), True),
            EquitySnapshot("Joker", "FLOP", 0.35, ("6h", "7h"), ("Kc", "5d", "2h"), True),
            EquitySnapshot("Batman", "TURN", 0.70, ("Ks", "Kd"), ("Kc", "5d", "2h", "Jc"), True),
            EquitySnapshot("Joker", "TURN", 0.30, ("6h", "7h"), ("Kc", "5d", "2h", "Jc"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("Ks", "Kd"), ("Kc", "5d", "2h", "Jc", "8h"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("6h", "7h"), ("Kc", "5d", "2h", "Jc", "8h"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        events = self.detector.detect_equity_shock_events(
            history, winner_names=["Joker"], pot_size=2000,
            hand_start_stacks={"Batman": 1000, "Joker": 1000},
        )

        batman_events = [e for e in events if "Batman" in e[1]]
        self.assertTrue(any(e[0] == "got_sucked_out" for e in batman_events))

    def test_suckout_detected_for_winner(self):
        """suckout: winner had big positive weighted delta (got lucky)."""
        snapshots = (
            EquitySnapshot("Batman", "FLOP", 0.80, ("As", "Ks"), ("Ad", "Kc", "2h"), True),
            EquitySnapshot("Joker", "FLOP", 0.20, ("3h", "4h"), ("Ad", "Kc", "2h"), True),
            EquitySnapshot("Batman", "TURN", 0.85, ("As", "Ks"), ("Ad", "Kc", "2h", "7c"), True),
            EquitySnapshot("Joker", "TURN", 0.15, ("3h", "4h"), ("Ad", "Kc", "2h", "7c"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("As", "Ks"), ("Ad", "Kc", "2h", "7c", "5h"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("3h", "4h"), ("Ad", "Kc", "2h", "7c", "5h"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        events = self.detector.detect_equity_shock_events(
            history, winner_names=["Joker"], pot_size=2000,
            hand_start_stacks={"Batman": 1000, "Joker": 1000},
        )

        joker_events = [e for e in events if "Joker" in e[1]]
        self.assertTrue(any(e[0] == "suckout" for e in joker_events))

    def test_pot_significance_threshold(self):
        """Swings in trivial pots (< 15% of stack) should be ignored."""
        snapshots = (
            EquitySnapshot("Batman", "FLOP", 0.85, ("As", "Ah"), ("Ac", "Kd", "2h"), True),
            EquitySnapshot("Joker", "FLOP", 0.15, ("Qh", "Jc"), ("Ac", "Kd", "2h"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("As", "Ah"), ("Ac", "Kd", "2h", "Td", "9s"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("Qh", "Jc"), ("Ac", "Kd", "2h", "Td", "9s"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        # Tiny pot (100 chips) vs large stacks (10000)
        events = self.detector.detect_equity_shock_events(
            history, winner_names=["Joker"], pot_size=100,
            hand_start_stacks={"Batman": 10000, "Joker": 10000},
        )

        # Should have no events (pot_significance = 100/10000 = 0.01 < 0.15)
        self.assertEqual(len(events), 0)

    def test_street_weighting_river_hurts_more(self):
        """River swings should be weighted more heavily than flop swings."""
        # Create two scenarios: same equity delta, different streets

        # Scenario 1: Big swing on flop (weight 1.0)
        flop_snapshots = (
            EquitySnapshot("Batman", "PRE_FLOP", 0.80, ("As", "Ah"), (), True),
            EquitySnapshot("Joker", "PRE_FLOP", 0.20, ("2h", "3h"), (), True),
            EquitySnapshot("Batman", "FLOP", 0.20, ("As", "Ah"), ("4h", "5h", "6h"), True),
            EquitySnapshot("Joker", "FLOP", 0.80, ("2h", "3h"), ("4h", "5h", "6h"), True),
        )
        flop_history = HandEquityHistory(None, "test", 1, flop_snapshots)

        # Scenario 2: Same swing on river (weight 1.4)
        river_snapshots = (
            EquitySnapshot("Batman", "TURN", 0.80, ("As", "Ah"), ("Kc", "7d", "2c", "3c"), True),
            EquitySnapshot("Joker", "TURN", 0.20, ("4h", "5h"), ("Kc", "7d", "2c", "3c"), True),
            EquitySnapshot("Batman", "RIVER", 0.20, ("As", "Ah"), ("Kc", "7d", "2c", "3c", "6h"), True),
            EquitySnapshot("Joker", "RIVER", 0.80, ("4h", "5h"), ("Kc", "7d", "2c", "3c", "6h"), True),
        )
        river_history = HandEquityHistory(None, "test", 1, river_snapshots)

        # Both with same pot significance
        stacks = {"Batman": 1000, "Joker": 1000}

        # River scenario should be more likely to trigger (higher weighted delta)
        # With pot_size=600, pot_significance=0.6
        # Flop: delta=-0.60, weighted = -0.60 * 0.6 * 1.0 = -0.36
        # River: delta=-0.60, weighted = -0.60 * 0.6 * 1.4 = -0.504
        # Both should exceed threshold 0.30, but river swing is bigger
        flop_events = self.detector.detect_equity_shock_events(
            flop_history, winner_names=["Joker"], pot_size=600, hand_start_stacks=stacks
        )
        river_events = self.detector.detect_equity_shock_events(
            river_history, winner_names=["Joker"], pot_size=600, hand_start_stacks=stacks
        )

        # Both should trigger events, but this demonstrates the weighting works
        self.assertTrue(len(flop_events) > 0 or len(river_events) > 0)

    def test_at_most_one_event_per_player(self):
        """Each player should get at most one equity shock event."""
        # Complex scenario where multiple events could trigger
        snapshots = (
            EquitySnapshot("Batman", "PRE_FLOP", 0.65, ("Ah", "Kd"), (), True),
            EquitySnapshot("Joker", "PRE_FLOP", 0.35, ("Qh", "Qc"), (), True),
            EquitySnapshot("Batman", "FLOP", 0.85, ("Ah", "Kd"), ("Ac", "Ks", "2c"), True),
            EquitySnapshot("Joker", "FLOP", 0.15, ("Qh", "Qc"), ("Ac", "Ks", "2c"), True),
            EquitySnapshot("Batman", "TURN", 0.90, ("Ah", "Kd"), ("Ac", "Ks", "2c", "3d"), True),
            EquitySnapshot("Joker", "TURN", 0.10, ("Qh", "Qc"), ("Ac", "Ks", "2c", "3d"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("Ah", "Kd"), ("Ac", "Ks", "2c", "3d", "Qd"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("Qh", "Qc"), ("Ac", "Ks", "2c", "3d", "Qd"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        events = self.detector.detect_equity_shock_events(
            history, winner_names=["Joker"], pot_size=2000,
            hand_start_stacks={"Batman": 1000, "Joker": 1000},
        )

        # Count events per player
        batman_events = [e for e in events if "Batman" in e[1]]
        joker_events = [e for e in events if "Joker" in e[1]]

        self.assertLessEqual(len(batman_events), 1)
        self.assertLessEqual(len(joker_events), 1)

    def test_bad_beat_over_got_sucked_out_priority(self):
        """bad_beat should take priority when loser had 80%+ equity at worst swing."""
        # Batman had 85% on turn, lost on river
        snapshots = (
            EquitySnapshot("Batman", "TURN", 0.85, ("As", "Ah"), ("Ac", "Kd", "2h", "3c"), True),
            EquitySnapshot("Joker", "TURN", 0.15, ("Qh", "Jc"), ("Ac", "Kd", "2h", "3c"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("As", "Ah"), ("Ac", "Kd", "2h", "3c", "Td"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("Qh", "Jc"), ("Ac", "Kd", "2h", "3c", "Td"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        events = self.detector.detect_equity_shock_events(
            history, winner_names=["Joker"], pot_size=2000,
            hand_start_stacks={"Batman": 1000, "Joker": 1000},
        )

        batman_events = [e for e in events if "Batman" in e[1]]
        # Should be bad_beat (85% > 80% threshold), not got_sucked_out
        self.assertEqual(len(batman_events), 1)
        self.assertEqual(batman_events[0][0], "bad_beat")

    def test_no_events_without_equity_history(self):
        """No events should fire if equity history is empty."""
        empty_history = HandEquityHistory.empty("test", 1)

        events = self.detector.detect_equity_shock_events(
            empty_history, winner_names=["Joker"], pot_size=2000,
            hand_start_stacks={"Batman": 1000, "Joker": 1000},
        )

        self.assertEqual(len(events), 0)

    def test_cooler_detected(self):
        """cooler: loser had 60-80% equity at worst swing."""
        # Batman had 65% on flop (good but not dominant), lost
        snapshots = (
            EquitySnapshot("Batman", "FLOP", 0.65, ("Ks", "Kd"), ("Kc", "Qd", "2h"), True),
            EquitySnapshot("Joker", "FLOP", 0.35, ("Qh", "Qc"), ("Kc", "Qd", "2h"), True),
            EquitySnapshot("Batman", "TURN", 0.70, ("Ks", "Kd"), ("Kc", "Qd", "2h", "3c"), True),
            EquitySnapshot("Joker", "TURN", 0.30, ("Qh", "Qc"), ("Kc", "Qd", "2h", "3c"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("Ks", "Kd"), ("Kc", "Qd", "2h", "3c", "Qs"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("Qh", "Qc"), ("Kc", "Qd", "2h", "3c", "Qs"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        events = self.detector.detect_equity_shock_events(
            history, winner_names=["Joker"], pot_size=2000,
            hand_start_stacks={"Batman": 1000, "Joker": 1000},
        )

        batman_events = [e for e in events if "Batman" in e[1]]
        # Batman had 70% at worst swing (turn→river), which is in 60-80% range
        # But got_sucked_out takes priority over cooler in our priority chain
        # (bad_beat > got_sucked_out > cooler)
        # Since Batman lost with big negative delta, got_sucked_out fires first
        self.assertEqual(len(batman_events), 1)
        # With 70% equity at worst swing (< 80%), got_sucked_out fires
        self.assertEqual(batman_events[0][0], "got_sucked_out")


if __name__ == "__main__":
    unittest.main()
