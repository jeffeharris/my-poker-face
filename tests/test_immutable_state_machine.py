"""Test the new immutable PokerStateMachine."""
import unittest
from poker.poker_game import initialize_game_state, player_fold, player_call
from poker.poker_state_machine import PokerStateMachine, PokerPhase


class TestImmutableStateMachine(unittest.TestCase):
    """Test the new immutable state machine interface."""
    
    def test_basic_immutability(self):
        """Test that operations return new instances."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm1 = PokerStateMachine(game_state)
        
        # Advance should return new instance
        sm2 = sm1.advance()
        self.assertIsNot(sm1, sm2)
        self.assertNotEqual(sm1.phase, sm2.phase)
    
    def test_setters_for_compatibility(self):
        """Test that setters work for Flask compatibility."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm = PokerStateMachine(game_state)

        # Setters now work for Flask compatibility
        sm.phase = PokerPhase.FLOP
        self.assertEqual(sm.phase, PokerPhase.FLOP)

        # game_state setter also works
        new_game_state = game_state.update(current_player_idx=1)
        sm.game_state = new_game_state
        self.assertEqual(sm.game_state.current_player_idx, 1)
    
    def test_with_methods(self):
        """Test the with_* methods."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm1 = PokerStateMachine(game_state)
        
        # Test with_phase
        sm2 = sm1.with_phase(PokerPhase.FLOP)
        self.assertEqual(sm1.phase, PokerPhase.INITIALIZING_GAME)
        self.assertEqual(sm2.phase, PokerPhase.FLOP)
        
        # Test with_game_state
        new_game_state = game_state.update(current_player_idx=1)
        sm3 = sm1.with_game_state(new_game_state)
        self.assertEqual(sm1.game_state.current_player_idx, 0)
        self.assertEqual(sm3.game_state.current_player_idx, 1)
    
    def test_run_until_player_action(self):
        """Test run_until_player_action works (mutable-style for compatibility)."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm = PokerStateMachine(game_state)

        # Now mutates and returns self for chaining
        result = sm.run_until_player_action()
        self.assertIs(result, sm)  # Returns same instance
        self.assertTrue(sm.awaiting_action)
    
    def test_chaining_operations(self):
        """Test that we can chain operations."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm = PokerStateMachine(game_state)
        
        # Chain multiple operations
        sm_final = (sm
                    .advance()
                    .advance()
                    .with_phase(PokerPhase.FLOP))
        
        self.assertEqual(sm_final.phase, PokerPhase.FLOP)
        self.assertEqual(sm.phase, PokerPhase.INITIALIZING_GAME)  # Original unchanged
    
    def test_game_flow_immutable(self):
        """Test a game flow with immutable pattern."""
        game_state = initialize_game_state(['Alice', 'Bob', 'Charlie'])
        sm = PokerStateMachine(game_state)
        
        # Track phases
        phases = [sm.phase]
        
        # Run a few advances
        for _ in range(5):
            sm = sm.advance()
            phases.append(sm.phase)
            if sm.awaiting_action:
                break
        
        # Should have progressed through multiple phases
        self.assertTrue(len(set(phases)) > 1)
        self.assertIn(PokerPhase.PRE_FLOP, phases)


if __name__ == '__main__':
    unittest.main()