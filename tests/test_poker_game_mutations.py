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


class TestPlaceBetPlayerIdx(unittest.TestCase):
    """Test that place_bet correctly handles player_idx=0."""

    def test_place_bet_with_player_idx_zero(self):
        """place_bet(player_idx=0) should bet for player 0, not current_player_idx."""
        from poker.poker_game import place_bet

        game_state = initialize_game_state(['Alice', 'Bob', 'Charlie'])
        # Set current_player_idx to 2 (Charlie), so it differs from 0
        game_state = game_state.update(current_player_idx=2)

        alice_stack_before = game_state.players[0].stack
        charlie_stack_before = game_state.players[2].stack
        bet_amount = 50

        result = place_bet(game_state, amount=bet_amount, player_idx=0)

        # Alice (index 0) should have lost chips
        assert result.players[0].stack == alice_stack_before - bet_amount
        # Charlie (index 2, the current player) should be untouched
        assert result.players[2].stack == charlie_stack_before

    def test_place_bet_default_uses_current_player(self):
        """place_bet without player_idx should use current_player_idx."""
        from poker.poker_game import place_bet

        game_state = initialize_game_state(['Alice', 'Bob', 'Charlie'])
        game_state = game_state.update(current_player_idx=1)

        bob_stack_before = game_state.players[1].stack
        bet_amount = 50

        result = place_bet(game_state, amount=bet_amount)

        # Bob (current player, index 1) should have lost chips
        assert result.players[1].stack == bob_stack_before - bet_amount


if __name__ == '__main__':
    unittest.main()