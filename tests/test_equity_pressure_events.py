"""
Tests for equity-based pressure event detection.

Tests the EquitySnapshot, HandEquityHistory, EquityTracker, and equity-based
event detection in PressureDetector.
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


class TestEquityBasedPressureEvents(unittest.TestCase):
    """Tests for equity-based pressure event detection in PressureDetector."""

    def setUp(self):
        """Set up test components."""
        self.detector = PressureEventDetector()

    def _create_game_state(self, player_names, pot_total=1000, folded=None):
        """Helper to create a game state."""
        folded = folded or []
        game_state = initialize_game_state(
            player_names=player_names,
            starting_stack=1000
        )
        # Set pot and folded status
        game_state = game_state.update(pot={'total': pot_total})
        # Update folded status
        updated_players = []
        for p in game_state.players:
            if p.name in folded:
                updated_players.append(p.update(is_folded=True))
            else:
                updated_players.append(p)
        game_state = game_state.update(players=tuple(updated_players))
        return game_state

    def _create_winner_info(self, winner_name, amount, hand_name="Pair"):
        """Helper to create winner info."""
        return {
            'pot_breakdown': [
                {'winners': [{'name': winner_name, 'amount': amount}], 'hand_name': hand_name}
            ],
            'winnings': {winner_name: amount},
            'hand_name': hand_name,
        }

    def test_detect_cooler(self):
        """Test cooler detection: both players had strong flop equity."""
        game_state = self._create_game_state(["Batman", "Joker"], pot_total=2000)
        winner_info = self._create_winner_info("Joker", 2000, "Full House")

        # Both had 40%+ equity on flop (cooler scenario)
        snapshots = (
            EquitySnapshot("Batman", "FLOP", 0.45, ("As", "Ks"), ("Qs", "Js", "Ts"), True),
            EquitySnapshot("Joker", "FLOP", 0.55, ("Qh", "Qc"), ("Qs", "Js", "Ts"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("As", "Ks"), ("Qs", "Js", "Ts", "Qd", "2c"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("Qh", "Qc"), ("Qs", "Js", "Ts", "Qd", "2c"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        events = self.detector.detect_equity_events(game_state, winner_info, history)

        event_types = [e[0] for e in events]
        self.assertIn("cooler", event_types)

        # Batman should be the cooler victim
        cooler_event = next(e for e in events if e[0] == "cooler")
        self.assertEqual(cooler_event[1], ["Batman"])

    def test_detect_suckout(self):
        """Test suckout detection: winner was behind on turn."""
        game_state = self._create_game_state(["Batman", "Joker"], pot_total=2000)
        winner_info = self._create_winner_info("Joker", 2000, "Two Pair")

        # Joker was way behind on turn (20%) but won
        snapshots = (
            EquitySnapshot("Batman", "TURN", 0.80, ("As", "Ks"), ("2s", "3d", "4h", "5c"), True),
            EquitySnapshot("Joker", "TURN", 0.20, ("6h", "7d"), ("2s", "3d", "4h", "5c"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("As", "Ks"), ("2s", "3d", "4h", "5c", "8s"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("6h", "7d"), ("2s", "3d", "4h", "5c", "8s"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        events = self.detector.detect_equity_events(game_state, winner_info, history)

        event_types = [e[0] for e in events]
        self.assertIn("suckout", event_types)

        suckout_event = next(e for e in events if e[0] == "suckout")
        self.assertEqual(suckout_event[1], ["Joker"])

    def test_detect_got_sucked_out(self):
        """Test got_sucked_out detection: loser was ahead on turn."""
        game_state = self._create_game_state(["Batman", "Joker"], pot_total=2000)
        winner_info = self._create_winner_info("Joker", 2000, "Flush")

        # Batman was way ahead on turn (75%) but lost
        snapshots = (
            EquitySnapshot("Batman", "TURN", 0.75, ("As", "Ks"), ("Kd", "Qd", "2h", "3c"), True),
            EquitySnapshot("Joker", "TURN", 0.25, ("Jd", "Td"), ("Kd", "Qd", "2h", "3c"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("As", "Ks"), ("Kd", "Qd", "2h", "3c", "4d"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("Jd", "Td"), ("Kd", "Qd", "2h", "3c", "4d"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        events = self.detector.detect_equity_events(game_state, winner_info, history)

        event_types = [e[0] for e in events]
        self.assertIn("got_sucked_out", event_types)

        got_sucked_out_event = next(e for e in events if e[0] == "got_sucked_out")
        self.assertEqual(got_sucked_out_event[1], ["Batman"])

    def test_detect_bad_beat_equity(self):
        """Test equity-based bad beat: loser had >70% equity on flop."""
        game_state = self._create_game_state(["Batman", "Joker"], pot_total=1500)
        winner_info = self._create_winner_info("Joker", 1500, "Straight")

        # Batman was a big favorite on flop (85%) but lost
        snapshots = (
            EquitySnapshot("Batman", "FLOP", 0.85, ("As", "Ah"), ("Ac", "Kd", "2h"), True),
            EquitySnapshot("Joker", "FLOP", 0.15, ("Qh", "Jc"), ("Ac", "Kd", "2h"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("As", "Ah"), ("Ac", "Kd", "2h", "Td", "9s"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("Qh", "Jc"), ("Ac", "Kd", "2h", "Td", "9s"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        events = self.detector.detect_equity_events(game_state, winner_info, history)

        event_types = [e[0] for e in events]
        self.assertIn("bad_beat", event_types)

        bad_beat_event = next(e for e in events if e[0] == "bad_beat")
        self.assertEqual(bad_beat_event[1], ["Batman"])

    def test_no_events_for_small_pot(self):
        """Test that suckout/got_sucked_out don't fire for small pots."""
        # Small pot (100 chips with 1000 average stack)
        game_state = self._create_game_state(["Batman", "Joker"], pot_total=100)
        winner_info = self._create_winner_info("Joker", 100, "Pair")

        # Would be a suckout in a big pot
        snapshots = (
            EquitySnapshot("Batman", "TURN", 0.80, ("As", "Ks"), ("2s", "3d", "4h", "5c"), True),
            EquitySnapshot("Joker", "TURN", 0.20, ("6h", "7d"), ("2s", "3d", "4h", "5c"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("As", "Ks"), ("2s", "3d", "4h", "5c", "8s"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("6h", "7d"), ("2s", "3d", "4h", "5c", "8s"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        events = self.detector.detect_equity_events(game_state, winner_info, history)

        event_types = [e[0] for e in events]
        # Suckout and got_sucked_out should NOT fire for small pots
        self.assertNotIn("suckout", event_types)
        self.assertNotIn("got_sucked_out", event_types)

    def test_no_events_without_equity_history(self):
        """Test that no events fire if equity history is empty."""
        game_state = self._create_game_state(["Batman", "Joker"], pot_total=2000)
        winner_info = self._create_winner_info("Joker", 2000, "Pair")

        empty_history = HandEquityHistory.empty("test", 1)

        events = self.detector.detect_equity_events(game_state, winner_info, empty_history)

        self.assertEqual(len(events), 0)

    def test_multiple_events_can_fire(self):
        """Test that multiple equity events can fire for the same hand."""
        game_state = self._create_game_state(["Batman", "Joker"], pot_total=2000)
        winner_info = self._create_winner_info("Joker", 2000, "Flush")

        # Both cooler (both strong on flop) AND suckout (Joker was behind on turn)
        snapshots = (
            EquitySnapshot("Batman", "FLOP", 0.55, ("As", "Ks"), ("Qd", "Jd", "2h"), True),
            EquitySnapshot("Joker", "FLOP", 0.45, ("Kd", "Td"), ("Qd", "Jd", "2h"), True),
            EquitySnapshot("Batman", "TURN", 0.70, ("As", "Ks"), ("Qd", "Jd", "2h", "3c"), True),
            EquitySnapshot("Joker", "TURN", 0.30, ("Kd", "Td"), ("Qd", "Jd", "2h", "3c"), True),
            EquitySnapshot("Batman", "RIVER", 0.0, ("As", "Ks"), ("Qd", "Jd", "2h", "3c", "9d"), True),
            EquitySnapshot("Joker", "RIVER", 1.0, ("Kd", "Td"), ("Qd", "Jd", "2h", "3c", "9d"), True),
        )
        history = HandEquityHistory(None, "test", 1, snapshots)

        events = self.detector.detect_equity_events(game_state, winner_info, history)

        event_types = [e[0] for e in events]
        # Should have cooler, suckout, and got_sucked_out
        self.assertIn("cooler", event_types)
        self.assertIn("suckout", event_types)
        self.assertIn("got_sucked_out", event_types)


if __name__ == "__main__":
    unittest.main()
