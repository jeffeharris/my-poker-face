"""Integration tests for the refactored state machine."""
import unittest
import pytest
from poker.poker_game import initialize_game_state, player_fold, player_call
from poker.poker_state_machine import PokerStateMachine, PokerPhase

pytestmark = pytest.mark.integration


class TestStateMachineIntegration(unittest.TestCase):
    """Test that the refactored state machine integrates correctly."""
    
    def test_full_hand_simulation(self):
        """Test playing through a full hand."""
        # Initialize game
        game_state = initialize_game_state(['Alice', 'Bob', 'Charlie'])
        sm = PokerStateMachine(game_state)
        
        # Track phases we see
        phases_seen = set()
        
        # Run until we need player action
        while not sm.game_state.awaiting_action:
            sm.advance_state()
            phases_seen.add(sm.phase)
            
            # Safety limit
            if len(phases_seen) > 20:
                break
        
        # Should have gone through initialization and reached pre-flop
        self.assertIn(PokerPhase.INITIALIZING_HAND, phases_seen)
        self.assertIn(PokerPhase.PRE_FLOP, phases_seen)
        self.assertTrue(sm.game_state.awaiting_action)
    
    def test_phase_setter_compatibility(self):
        """Test that phase setter still works for compatibility."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm = PokerStateMachine(game_state)
        
        # Test setting phase directly (Flask does this)
        sm.phase = PokerPhase.DEALING_CARDS
        self.assertEqual(sm.phase, PokerPhase.DEALING_CARDS)
        
        # Test update_phase method
        sm.update_phase(PokerPhase.FLOP)
        self.assertEqual(sm.phase, PokerPhase.FLOP)
    
    def test_game_state_setter_compatibility(self):
        """Test that game_state setter still works."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm = PokerStateMachine(game_state)
        
        # Modify game state
        new_game_state = game_state.update(current_player_idx=1)
        sm.game_state = new_game_state
        
        self.assertEqual(sm.game_state.current_player_idx, 1)
    
    def test_snapshots_as_list(self):
        """Test that snapshots property returns a list."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm = PokerStateMachine(game_state)
        
        # Advance a few times
        for _ in range(3):
            sm.advance_state()
            if sm.game_state.awaiting_action:
                break
        
        # Check snapshots is a list (not tuple)
        self.assertIsInstance(sm.snapshots, list)
        self.assertTrue(len(sm.snapshots) > 0)
    
    def test_stats_dict_compatibility(self):
        """Test that stats returns a dict."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm = PokerStateMachine(game_state)
        
        # Check stats is a dict
        self.assertIsInstance(sm.stats, dict)
        self.assertIn('hand_count', sm.stats)
        self.assertEqual(sm.stats['hand_count'], 0)
    
    def test_immutability_under_the_hood(self):
        """Test that internal state is truly immutable."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm = PokerStateMachine(game_state)
        
        # Capture internal state
        state1 = sm._state
        
        # Advance
        sm.advance_state()
        state2 = sm._state
        
        # Internal states should be different objects
        self.assertIsNot(state1, state2)
        # But the wrapped object maintains the illusion of mutation
        self.assertEqual(sm.phase, state2.phase)


if __name__ == '__main__':
    unittest.main()
