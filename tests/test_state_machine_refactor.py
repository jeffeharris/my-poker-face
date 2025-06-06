"""
Comprehensive tests for state machine refactoring.
These tests capture the current behavior before we make breaking changes.
"""
import unittest
from poker.poker_game import initialize_game_state, player_fold, player_call, player_raise
from poker.poker_state_machine import PokerStateMachine, PokerPhase, ImmutableStateMachine
import copy


class TestStateMachineCurrentBehavior(unittest.TestCase):
    """Test current state machine behavior before refactoring."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.game_state = initialize_game_state(['Alice', 'Bob', 'Charlie'])
        self.sm = PokerStateMachine(self.game_state)
    
    def test_mutation_patterns(self):
        """Document how Flask currently mutates the state machine."""
        # Flask pattern 1: Direct phase mutation
        self.sm.phase = PokerPhase.DEALING_CARDS
        self.assertEqual(self.sm.phase, PokerPhase.DEALING_CARDS)
        
        # Flask pattern 2: Direct game state mutation
        new_game_state = self.game_state.update(current_player_idx=2)
        self.sm.game_state = new_game_state
        self.assertEqual(self.sm.game_state.current_player_idx, 2)
        
        # Flask pattern 3: update_phase method
        self.sm.update_phase(PokerPhase.FLOP)
        self.assertEqual(self.sm.phase, PokerPhase.FLOP)
    
    def test_advance_state_behavior(self):
        """Test how advance_state currently works."""
        initial_phase = self.sm.phase
        self.sm.advance_state()
        
        # State should change
        self.assertNotEqual(self.sm.phase, initial_phase)
        # Should have a snapshot
        self.assertEqual(len(self.sm.snapshots), 1)
    
    def test_state_machine_in_games_dict(self):
        """Test how Flask stores state machines."""
        # Flask pattern: games[game_id] = {'state_machine': sm, ...}
        games = {}
        games['test_game'] = {
            'state_machine': self.sm,
            'game_state': self.game_state
        }
        
        # Mutations affect the stored reference
        games['test_game']['state_machine'].phase = PokerPhase.RIVER
        self.assertEqual(self.sm.phase, PokerPhase.RIVER)
    
    def test_run_until_player_action(self):
        """Test run_until_player_action behavior."""
        # Should run until awaiting_action is True
        self.sm.run_until_player_action()
        self.assertTrue(self.sm.game_state.awaiting_action)
        
        # Should have advanced at least once
        self.assertTrue(len(self.sm.snapshots) > 0)
    
    def test_property_access_patterns(self):
        """Test all property access patterns."""
        # Test getters
        phase = self.sm.phase
        game_state = self.sm.game_state
        next_phase = self.sm.next_phase
        current_phase = self.sm.current_phase
        stats = self.sm.stats
        snapshots = self.sm.snapshots
        
        # Test types
        self.assertIsInstance(phase, PokerPhase)
        self.assertIsInstance(stats, dict)
        self.assertIsInstance(snapshots, list)


class TestImmutablePatterns(unittest.TestCase):
    """Test patterns we want to migrate to."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.game_state = initialize_game_state(['Alice', 'Bob', 'Charlie'])
        self.sm = PokerStateMachine(self.game_state)
    
    def test_immutable_advance_pattern(self):
        """Test what immutable advance would look like."""
        # Current mutable pattern
        old_phase = self.sm.phase
        self.sm.advance_state()  # Mutates in place
        
        # What we want: immutable pattern
        # new_sm = self.sm.advance()  # Returns new instance
        # self.assertIsNot(new_sm, self.sm)
        # self.assertEqual(self.sm.phase, old_phase)  # Original unchanged
    
    def test_immutable_update_pattern(self):
        """Test what immutable updates would look like."""
        # What we want:
        # new_sm = self.sm.with_phase(PokerPhase.FLOP)
        # self.assertEqual(new_sm.phase, PokerPhase.FLOP)
        # self.assertNotEqual(self.sm.phase, PokerPhase.FLOP)
        pass
    
    def test_api_endpoints_needed(self):
        """Document what APIs need to keep working."""
        # The React app calls these endpoints:
        # POST /api/new-game
        # GET  /api/game-state/{game_id}
        # POST /api/game/{game_id}/action
        
        # These need to continue working regardless of state machine changes
        pass


class TestPersistenceRequirements(unittest.TestCase):
    """Test what persistence needs from state machine."""
    
    def test_serialization_requirements(self):
        """Test what needs to be serializable."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm = PokerStateMachine(game_state)
        
        # Advance a bit
        for _ in range(3):
            sm.advance_state()
            if sm.game_state.awaiting_action:
                break
        
        # What persistence needs to save
        data_to_persist = {
            'phase': sm.phase.name,
            'game_state': sm.game_state.to_dict(),
            'stats': sm.stats,
            # Note: snapshots are NOT persisted
        }
        
        # All of these should be serializable
        import json
        json_str = json.dumps(data_to_persist)
        self.assertIsInstance(json_str, str)


class TestCriticalGameFlows(unittest.TestCase):
    """Test critical game flows that must continue working."""
    
    def test_full_betting_round(self):
        """Test a complete betting round."""
        game_state = initialize_game_state(['Alice', 'Bob', 'Charlie'])
        sm = PokerStateMachine(game_state)
        
        # Run to first action
        sm.run_until_player_action()
        
        # Simulate player actions
        if sm.game_state.awaiting_action:
            # Player makes a move
            new_game_state = player_call(sm.game_state)
            sm.game_state = new_game_state
            
            # Continue
            sm.advance_state()
        
        # Game should progress
        self.assertIsNotNone(sm.phase)
    
    def test_hand_completion(self):
        """Test that hands can progress through phases."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm = PokerStateMachine(game_state)
        
        # Just verify we can progress through some phases
        initial_phase = sm.phase
        phases_seen = set()
        
        for _ in range(10):
            phases_seen.add(sm.phase)
            sm.advance_state()
            
            if sm.game_state.awaiting_action:
                break
        
        # Should have progressed through multiple phases
        self.assertTrue(len(phases_seen) > 1)
        self.assertIn(PokerPhase.PRE_FLOP, phases_seen)


if __name__ == '__main__':
    unittest.main()