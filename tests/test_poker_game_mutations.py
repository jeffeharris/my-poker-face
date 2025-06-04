"""Test mutations in poker_game.py properties that need to be fixed."""
import unittest
from poker.poker_game import PokerGameState, Player, initialize_game_state


class TestPokerGameMutations(unittest.TestCase):
    """Document mutations in PokerGameState properties."""
    
    def test_current_player_options_mutations(self):
        """Test that current_player_options has mutations."""
        # Create a game state
        game_state = initialize_game_state(['Alice', 'Bob'])
        
        # Access the property multiple times
        options1 = game_state.current_player_options
        options2 = game_state.current_player_options
        
        # Currently returns a new list each time (good)
        # But the list is created with mutations (bad)
        self.assertIsInstance(options1, list)
        self.assertIsInstance(options2, list)
        
    def test_table_positions_no_mutations(self):
        """Test that table_positions doesn't mutate."""
        game_state = initialize_game_state(['Alice', 'Bob', 'Charlie'])
        
        # Access property
        positions1 = game_state.table_positions
        positions2 = game_state.table_positions
        
        # Should return new dict each time
        self.assertIsNot(positions1, positions2)
        self.assertEqual(positions1, positions2)
    
    def test_opponent_status_builds_list(self):
        """Test that opponent_status builds a list."""
        game_state = initialize_game_state(['Alice', 'Bob', 'Charlie'])
        
        # This property has a weird signature - it takes a parameter!
        # @property methods shouldn't take parameters
        status = game_state.opponent_status
        self.assertIsInstance(status, list)
    
    def test_create_deck_no_side_effects(self):
        """Test that create_deck has NO side effects."""
        from poker.poker_game import create_deck
        import random
        
        # Save random state
        state = random.getstate()
        
        # Create deck should NOT modify global random state
        deck1 = create_deck(shuffled=True)
        
        # Check state is unchanged
        new_state = random.getstate()
        self.assertEqual(state, new_state, "create_deck should not modify global random state")
        
        # Using same seed should give same deck
        deck2 = create_deck(shuffled=True, random_seed=42)
        deck3 = create_deck(shuffled=True, random_seed=42)
        self.assertEqual(deck2, deck3, "Same seed should produce same deck")


class TestPropertyMutationPatterns(unittest.TestCase):
    """Test the patterns we need to fix."""
    
    def test_list_remove_pattern(self):
        """Document the list.remove() pattern."""
        # Current pattern in current_player_options:
        player_options = ['fold', 'check', 'call', 'raise', 'all_in']
        player_options.remove('fold')  # Mutates the list!
        
        # What we should do instead:
        player_options = ['fold', 'check', 'call', 'raise', 'all_in']
        player_options = [opt for opt in player_options if opt != 'fold']
        
        # Or build the list correctly from the start
        
    def test_list_append_pattern(self):
        """Document the list.append() pattern."""
        # Current pattern in table_positions:
        positions = ["button", "small_blind_player", "big_blind_player"]
        # positions.append("under_the_gun")  # Would mutate!
        
        # What we should do:
        positions = ["button", "small_blind_player", "big_blind_player"]
        positions = positions + ["under_the_gun"]  # Creates new list


if __name__ == '__main__':
    unittest.main()