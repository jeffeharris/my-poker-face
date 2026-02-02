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


class TestResetPlayerActionFlags(unittest.TestCase):
    """T1-10: Verify reset_player_action_flags uses index comparison, not name."""

    def test_reset_flags_uses_index_not_name(self):
        """reset_player_action_flags with exclude_current_player=True should exclude by index."""
        from poker.poker_game import reset_player_action_flags

        game_state = initialize_game_state(['Alice', 'Bob', 'Charlie'])
        # Set current player to index 1 (Bob)
        game_state = game_state.update(current_player_idx=1)
        # Mark all players as having acted
        for idx in range(len(game_state.players)):
            game_state = game_state.update_player(player_idx=idx, has_acted=True)

        result = reset_player_action_flags(game_state, exclude_current_player=True)

        # Player 0 (human) and Player 2 (Charlie) should be reset to has_acted=False
        assert result.players[0].has_acted is False, "Player 0 should be reset"
        assert result.players[2].has_acted is False, "Player 2 should be reset"
        # Player 1 (Bob, current player) should remain has_acted=True
        assert result.players[1].has_acted is True, "Current player should be excluded"

    def test_reset_flags_resets_all_when_not_excluding(self):
        """reset_player_action_flags without exclude resets all players."""
        from poker.poker_game import reset_player_action_flags

        game_state = initialize_game_state(['Alice', 'Bob'])
        game_state = game_state.update(current_player_idx=0)
        for idx in range(len(game_state.players)):
            game_state = game_state.update_player(player_idx=idx, has_acted=True)

        result = reset_player_action_flags(game_state, exclude_current_player=False)

        for idx, player in enumerate(result.players):
            assert player.has_acted is False, f"Player {idx} should be reset"

    def test_initialize_game_state_rejects_duplicate_ai_names(self):
        """initialize_game_state should raise ValueError for duplicate AI names."""
        import pytest
        with pytest.raises(ValueError, match="Duplicate player names"):
            initialize_game_state(['Alice', 'Alice', 'Bob'])

    def test_initialize_game_state_rejects_ai_name_matching_human(self):
        """initialize_game_state should raise ValueError when AI name matches human name."""
        import pytest
        with pytest.raises(ValueError, match="Duplicate player names"):
            initialize_game_state(['Player', 'Bob'], human_name='Player')


if __name__ == '__main__':
    unittest.main()