"""Test the game adapter functionality"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fresh_ui.utils.game_adapter import GameAdapter
from poker import PokerGameState, Player, PokerPhase


class TestGameAdapter(unittest.TestCase):
    """Test GameAdapter class"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.player_name = "TestPlayer"
        self.ai_names = ["AI1", "AI2"]
    
    @patch('fresh_ui.utils.game_adapter.AIPlayerController')
    @patch('fresh_ui.utils.game_adapter.initialize_game_state')
    def test_create_new_game(self, mock_init_game, mock_ai_controller):
        """Test creating a new game"""
        # Mock the game state
        mock_players = [
            Player(name="Jeff", stack=10000, is_human=True),
            Player(name="AI1", stack=10000, is_human=False),
            Player(name="AI2", stack=10000, is_human=False)
        ]
        mock_game_state = MagicMock(spec=PokerGameState)
        mock_game_state.players = mock_players
        mock_game_state.current_player_index = 0
        mock_init_game.return_value = mock_game_state
        
        # Create game
        adapter = GameAdapter.create_new_game(
            self.player_name,
            self.ai_names
        )
        
        # Verify
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.game_state, mock_game_state)
        self.assertIsNotNone(adapter.state_machine)
        self.assertEqual(len(adapter.ai_controllers), 2)
        mock_init_game.assert_called_once_with(self.ai_names)
    
    def test_get_current_player(self):
        """Test getting current player"""
        # Create mock game state
        mock_players = [
            Player(name="Jeff", stack=10000, is_human=True),
            Player(name="AI1", stack=10000, is_human=False)
        ]
        mock_game_state = MagicMock(spec=PokerGameState)
        mock_game_state.players = mock_players
        mock_game_state.current_player_index = 0
        
        # Create adapter
        adapter = GameAdapter(
            game_state=mock_game_state,
            state_machine=MagicMock(),
            ai_controllers={}
        )
        
        # Test
        current = adapter.get_current_player()
        self.assertEqual(current.name, "Jeff")
        
        # Test with no current player
        mock_game_state.current_player_index = None
        current = adapter.get_current_player()
        self.assertIsNone(current)
    
    def test_get_available_actions(self):
        """Test getting available actions"""
        # Create mock game state with current player
        current_player = Player(
            name="Jeff", 
            stack=1000, 
            is_human=True,
            bet=100,
            is_folded=False
        )
        mock_game_state = MagicMock(spec=PokerGameState)
        mock_game_state.players = [current_player]
        mock_game_state.current_player_index = 0
        mock_game_state.current_bet = 200
        
        # Create adapter
        adapter = GameAdapter(
            game_state=mock_game_state,
            state_machine=MagicMock(),
            ai_controllers={}
        )
        
        # Test actions
        actions = adapter.get_available_actions()
        self.assertIn('fold', actions)
        self.assertIn('call', actions)  # Can call since bet < current_bet
        self.assertIn('raise', actions)  # Has enough for min raise
        self.assertIn('all_in', actions)
        self.assertNotIn('check', actions)  # Can't check when behind
    
    def test_process_action(self):
        """Test processing player actions"""
        # Create mock state machine
        mock_state_machine = MagicMock()
        mock_state_machine.process_action.return_value = (MagicMock(), None)
        
        # Create current player
        current_player = Player(
            name="Jeff",
            stack=1000,
            is_human=True,
            bet=100
        )
        
        # Create mock game state
        mock_game_state = MagicMock(spec=PokerGameState)
        mock_game_state.players = [current_player]
        mock_game_state.current_player_index = 0
        
        # Create adapter
        adapter = GameAdapter(
            game_state=mock_game_state,
            state_machine=mock_state_machine,
            ai_controllers={}
        )
        
        # Test fold action
        new_state, error = adapter.process_action('fold')
        self.assertIsNone(error)
        mock_state_machine.process_action.assert_called()
        
        # Verify the action was created correctly
        call_args = mock_state_machine.process_action.call_args[0]
        action = call_args[1]
        self.assertTrue(action.is_fold)
    
    def test_is_hand_complete(self):
        """Test checking if hand is complete"""
        # Test with multiple active players
        players = [
            Player(name="P1", stack=1000, is_human=True, is_folded=False),
            Player(name="P2", stack=1000, is_human=False, is_folded=False)
        ]
        mock_game_state = MagicMock(spec=PokerGameState)
        mock_game_state.players = players
        mock_game_state.phase = PokerPhase.FLOP
        
        adapter = GameAdapter(
            game_state=mock_game_state,
            state_machine=MagicMock(),
            ai_controllers={}
        )
        
        # Should not be complete
        self.assertFalse(adapter.is_hand_complete())
        
        # Test with showdown
        mock_game_state.phase = PokerPhase.SHOWDOWN
        self.assertTrue(adapter.is_hand_complete())
        
        # Test with only one active player
        mock_game_state.phase = PokerPhase.FLOP
        players[1] = Player(name="P2", stack=1000, is_human=False, is_folded=True)
        self.assertTrue(adapter.is_hand_complete())


if __name__ == '__main__':
    unittest.main()