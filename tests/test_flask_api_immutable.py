"""Test that Flask API works with immutable state machine."""
import unittest
import json
from flask_app.ui_web_immutable import app, games


class TestFlaskAPIImmutable(unittest.TestCase):
    """Test Flask API with immutable state machine."""
    
    def setUp(self):
        """Set up test client."""
        self.app = app.test_client()
        self.app.testing = True
        # Clear games between tests
        games.clear()
    
    def test_new_game(self):
        """Test creating a new game."""
        response = self.app.post('/api/new-game')
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.data)
        self.assertIn('game_id', data)
        
        # Verify game was created
        game_id = data['game_id']
        self.assertIn(game_id, games)
    
    def test_get_game_state(self):
        """Test getting game state."""
        # Create game
        response = self.app.post('/api/new-game')
        game_id = json.loads(response.data)['game_id']
        
        # Get game state
        response = self.app.get(f'/api/game-state/{game_id}')
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.data)
        self.assertIn('players', data)
        self.assertEqual(len(data['players']), 4)
        self.assertIn('phase', data)
        self.assertIn('pot', data)
    
    def test_player_action(self):
        """Test player action."""
        # Create game
        response = self.app.post('/api/new-game')
        game_id = json.loads(response.data)['game_id']
        
        # Get initial state
        response = self.app.get(f'/api/game-state/{game_id}')
        initial_data = json.loads(response.data)
        
        # Check if it's human's turn
        current_idx = initial_data['current_player_idx']
        current_player = initial_data['players'][current_idx]
        
        if current_player['is_human']:
            # Make an action
            response = self.app.post(f'/api/game/{game_id}/action',
                                   json={'action': 'call', 'amount': 0})
            self.assertEqual(response.status_code, 200)
            
            # Get updated state
            response = self.app.get(f'/api/game-state/{game_id}')
            updated_data = json.loads(response.data)
            
            # Should have messages
            self.assertTrue(len(updated_data['messages']) > 1)
    
    def test_immutable_internals(self):
        """Test that state machine is truly immutable."""
        # Create game
        response = self.app.post('/api/new-game')
        game_id = json.loads(response.data)['game_id']
        
        # Get state machine
        sm1 = games[game_id]['state_machine']
        
        # Make an action to trigger state change
        response = self.app.get(f'/api/game-state/{game_id}')
        data = json.loads(response.data)
        
        if data['players'][data['current_player_idx']]['is_human']:
            self.app.post(f'/api/game/{game_id}/action',
                         json={'action': 'fold', 'amount': 0})
            
            # State machine should be a different instance
            sm2 = games[game_id]['state_machine']
            self.assertIsNot(sm1, sm2)


if __name__ == '__main__':
    unittest.main()