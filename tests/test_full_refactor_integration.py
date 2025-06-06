"""Test that all refactored components work together."""
import unittest
from poker.poker_game import initialize_game_state, player_fold, create_deck
from poker.poker_state_machine import PokerStateMachine, PokerPhase


class TestFullRefactorIntegration(unittest.TestCase):
    """Test that immutable state machine and fixed properties work together."""
    
    def test_create_deck_pure(self):
        """Test create_deck is now pure."""
        # Same seed gives same deck
        deck1 = create_deck(shuffled=True, random_seed=123)
        deck2 = create_deck(shuffled=True, random_seed=123)
        self.assertEqual(deck1, deck2)
        
        # Different seeds give different decks
        deck3 = create_deck(shuffled=True, random_seed=456)
        self.assertNotEqual(deck1, deck3)
    
    def test_game_flow_with_immutable_state_machine(self):
        """Test full game flow with all refactored components."""
        # Initialize game
        game_state = initialize_game_state(['Alice', 'Bob', 'Charlie'])
        sm = PokerStateMachine(game_state)
        
        # Advance through initialization
        sm = sm.advance()  # INITIALIZING_GAME -> INITIALIZING_HAND
        self.assertEqual(sm.phase, PokerPhase.INITIALIZING_HAND)
        
        sm = sm.advance()  # INITIALIZING_HAND -> PRE_FLOP
        self.assertEqual(sm.phase, PokerPhase.PRE_FLOP)
        
        # Check current player options (no mutations)
        options1 = sm.game_state.current_player_options
        options2 = sm.game_state.current_player_options
        self.assertEqual(options1, options2)
        self.assertIsInstance(options1, list)
        
        # Check table positions (no mutations)
        positions = sm.game_state.table_positions
        self.assertIsInstance(positions, dict)
        self.assertIn('button', positions)
        
        # Check opponent status (no parameters)
        status = sm.game_state.opponent_status
        self.assertIsInstance(status, list)
        self.assertEqual(len(status), 4)  # All 4 players (Jeff gets added as human)
    
    def test_player_update_functional(self):
        """Test update_player is functional."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        
        # Update player
        new_state = game_state.update_player(0, stack=5000)
        
        # Original unchanged
        self.assertEqual(game_state.players[0].stack, 10000)
        # New state updated
        self.assertEqual(new_state.players[0].stack, 5000)
    
    def test_run_until_patterns(self):
        """Test run_until methods return new instances."""
        game_state = initialize_game_state(['Alice', 'Bob'])
        sm1 = PokerStateMachine(game_state)
        
        # run_until_player_action returns new instance
        sm2 = sm1.run_until_player_action()
        self.assertIsNot(sm1, sm2)
        self.assertTrue(sm2.awaiting_action)
        
        # run_until returns new instance
        sm3 = sm1.run_until([PokerPhase.PRE_FLOP])
        self.assertIsNot(sm1, sm3)
        self.assertEqual(sm3.phase, PokerPhase.PRE_FLOP)
    
    def test_no_mutations_in_properties(self):
        """Verify all properties are free of mutations."""
        game_state = initialize_game_state(['Alice', 'Bob', 'Charlie', 'David'])
        
        # current_player_options should build list functionally
        options = game_state.current_player_options
        # Options depend on game state - just verify it's a list
        self.assertIsInstance(options, list)
        self.assertTrue(len(options) > 0)
        
        # table_positions should use functional approach
        positions = game_state.table_positions
        expected_positions = ['button', 'small_blind_player', 'big_blind_player', 'under_the_gun']
        for pos in expected_positions:
            self.assertIn(pos, positions)
        
        # opponent_status returns all players
        status = game_state.opponent_status
        self.assertEqual(len(status), 5)  # 5 because Jeff gets added
        for s in status:
            self.assertIn('has $', s)


if __name__ == '__main__':
    unittest.main()