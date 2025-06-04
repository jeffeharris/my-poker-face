"""Test the pure state machine functions."""
import unittest
from poker.poker_game import initialize_game_state
from poker.poker_state_machine import (
    ImmutableStateMachine, StateMachineStats, PokerPhase,
    advance_state_pure, get_next_phase
)


class TestPureStateMachine(unittest.TestCase):
    """Test pure state machine functions."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.game_state = initialize_game_state(['Alice', 'Bob', 'Charlie'])
        self.initial_state = ImmutableStateMachine(
            game_state=self.game_state,
            phase=PokerPhase.INITIALIZING_GAME
        )
    
    def test_immutable_state_creation(self):
        """Test that we can create immutable state."""
        self.assertEqual(self.initial_state.phase, PokerPhase.INITIALIZING_GAME)
        self.assertEqual(self.initial_state.stats.hand_count, 0)
        self.assertEqual(len(self.initial_state.snapshots), 0)
    
    def test_get_next_phase(self):
        """Test phase transition logic."""
        # Test basic transitions
        self.assertEqual(
            get_next_phase(self.initial_state),
            PokerPhase.INITIALIZING_HAND
        )
        
        # Test PRE_FLOP -> DEALING_CARDS
        pre_flop_state = self.initial_state.with_phase(PokerPhase.PRE_FLOP)
        self.assertEqual(
            get_next_phase(pre_flop_state),
            PokerPhase.DEALING_CARDS
        )
    
    def test_advance_state_pure_initializing(self):
        """Test advancing from INITIALIZING_GAME."""
        new_state = advance_state_pure(self.initial_state)
        
        # Should advance to INITIALIZING_HAND
        self.assertEqual(new_state.phase, PokerPhase.INITIALIZING_HAND)
        # Should have added a snapshot
        self.assertEqual(len(new_state.snapshots), 1)
        # Original state should be unchanged
        self.assertEqual(self.initial_state.phase, PokerPhase.INITIALIZING_GAME)
    
    def test_advance_state_pure_full_sequence(self):
        """Test advancing through multiple states."""
        state = self.initial_state
        
        # Track phase progression
        phases_seen = []
        
        # Advance through several states
        for _ in range(5):
            state = advance_state_pure(state)
            phases_seen.append(state.phase)
            
            # Stop if we hit a state that needs player action
            if state.game_state.awaiting_action:
                break
        
        # Verify we progressed through states
        self.assertIn(PokerPhase.INITIALIZING_HAND, phases_seen)
        self.assertTrue(len(state.snapshots) >= len(phases_seen))
    
    def test_state_immutability(self):
        """Test that states are truly immutable."""
        state1 = self.initial_state
        state2 = advance_state_pure(state1)
        state3 = advance_state_pure(state2)
        
        # All states should be different
        self.assertIsNot(state1, state2)
        self.assertIsNot(state2, state3)
        
        # Original states should be unchanged
        self.assertEqual(state1.phase, PokerPhase.INITIALIZING_GAME)
        self.assertEqual(len(state1.snapshots), 0)
    
    def test_stats_increment(self):
        """Test stats immutability."""
        stats1 = StateMachineStats(hand_count=5)
        stats2 = stats1.increment_hand_count()
        
        self.assertEqual(stats1.hand_count, 5)  # Original unchanged
        self.assertEqual(stats2.hand_count, 6)  # New value
        self.assertIsNot(stats1, stats2)  # Different objects


if __name__ == '__main__':
    unittest.main()