#!/usr/bin/env python3
"""
Test suite for game persistence functionality.
"""
import os
import sys
import unittest
import tempfile
import json
from datetime import datetime

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from poker.persistence import GamePersistence, SavedGame
from poker.poker_game import initialize_game_state, Player
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.utils import get_celebrities
from core.card import Card


class TestGamePersistence(unittest.TestCase):
    """Test cases for game persistence."""
    
    def setUp(self):
        """Create a temporary database for each test."""
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.persistence = GamePersistence(self.test_db.name)
        
    def tearDown(self):
        """Clean up temporary database."""
        os.unlink(self.test_db.name)
    
    def test_save_and_load_game(self):
        """Test saving and loading a game preserves all state."""
        # Create a game
        player_names = ['Jeff', 'AI1', 'AI2', 'AI3']
        game_state = initialize_game_state(player_names=player_names)
        state_machine = PokerStateMachine(game_state=game_state)
        game_id = "test_game_001"
        
        # Advance the game a bit
        state_machine.advance_state()  # Move past INITIALIZING_GAME
        state_machine.advance_state()  # Move past INITIALIZING_HAND
        
        # Save the game
        self.persistence.save_game(game_id, state_machine)
        
        # Load it back
        loaded_machine = self.persistence.load_game(game_id)
        
        self.assertIsNotNone(loaded_machine)
        self.assertEqual(loaded_machine.current_phase, state_machine.current_phase)
        
        # Check players match
        original_players = [(p.name, p.stack, p.is_human) for p in state_machine.game_state.players]
        loaded_players = [(p.name, p.stack, p.is_human) for p in loaded_machine.game_state.players]
        self.assertEqual(original_players, loaded_players)
        
        # Check game state details
        self.assertEqual(loaded_machine.game_state.pot['total'], 
                        state_machine.game_state.pot['total'])
        self.assertEqual(loaded_machine.game_state.current_dealer_idx,
                        state_machine.game_state.current_dealer_idx)
    
    def test_save_and_load_messages(self):
        """Test message persistence."""
        game_id = "test_game_002"
        
        # Save some messages
        messages = [
            ("table", "Game started!"),
            ("user", "Jeff: Hello everyone"),
            ("ai", "AI1: Let's play!"),
            ("table", "Dealing cards...")
        ]
        
        for msg_type, msg_text in messages:
            self.persistence.save_message(game_id, msg_type, msg_text)
        
        # Load messages back
        loaded_messages = self.persistence.load_messages(game_id)
        
        self.assertEqual(len(loaded_messages), len(messages))
        
        for i, (msg_type, msg_text) in enumerate(messages):
            self.assertEqual(loaded_messages[i]['type'], msg_type)
            self.assertEqual(loaded_messages[i]['text'], msg_text)
    
    def test_list_games(self):
        """Test listing saved games."""
        # Save multiple games
        game_ids = []
        for i in range(5):
            game_id = f"test_game_{i:03d}"
            game_ids.append(game_id)
            
            player_names = get_celebrities(shuffled=True)[:4]
            game_state = initialize_game_state(player_names=player_names)
            state_machine = PokerStateMachine(game_state=game_state)
            
            self.persistence.save_game(game_id, state_machine)
        
        # List games
        games = self.persistence.list_games(limit=10)
        
        self.assertEqual(len(games), 5)
        
        # Check they're ordered by update time (most recent first)
        for i in range(len(games) - 1):
            self.assertGreaterEqual(games[i].updated_at, games[i + 1].updated_at)
        
        # Check game IDs are present
        listed_ids = [g.game_id for g in games]
        for game_id in game_ids:
            self.assertIn(game_id, listed_ids)
    
    def test_game_state_with_cards(self):
        """Test serialization of game state with dealt cards."""
        player_names = ['Jeff', 'AI1']
        game_state = initialize_game_state(player_names=player_names)
        state_machine = PokerStateMachine(game_state=game_state)
        
        # Deal some cards manually for testing
        from dataclasses import replace
        test_cards = [
            Card(rank='A', suit='Spades'),
            Card(rank='K', suit='Hearts')
        ]
        
        # Update players with cards
        new_players = []
        for i, p in enumerate(game_state.players):
            if i == 0:
                new_players.append(replace(p, hand=tuple(test_cards)))
            else:
                new_players.append(p)
        
        game_state = replace(game_state,
            players=tuple(new_players),
            community_cards=[
                Card(rank='Q', suit='Diamonds'),
                Card(rank='J', suit='Clubs'),
                Card(rank='10', suit='Spades')
            ]
        )
        state_machine.game_state = game_state
        
        # Save and load
        game_id = "test_cards"
        self.persistence.save_game(game_id, state_machine)
        loaded_machine = self.persistence.load_game(game_id)
        
        # Check cards were preserved
        loaded_player = loaded_machine.game_state.players[0]
        self.assertEqual(len(loaded_player.hand), 2)
        self.assertEqual(loaded_player.hand[0].rank, 'A')
        self.assertEqual(loaded_player.hand[0].suit, 'Spades')
        
        self.assertEqual(len(loaded_machine.game_state.community_cards), 3)
        self.assertEqual(loaded_machine.game_state.community_cards[0].rank, 'Q')
    
    def test_delete_game(self):
        """Test deleting a game and its messages."""
        game_id = "test_delete"
        
        # Create and save a game
        game_state = initialize_game_state(player_names=['Jeff', 'AI1'])
        state_machine = PokerStateMachine(game_state=game_state)
        self.persistence.save_game(game_id, state_machine)
        
        # Add some messages
        self.persistence.save_message(game_id, "table", "Test message 1")
        self.persistence.save_message(game_id, "table", "Test message 2")
        
        # Verify it exists
        loaded = self.persistence.load_game(game_id)
        self.assertIsNotNone(loaded)
        
        messages = self.persistence.load_messages(game_id)
        self.assertEqual(len(messages), 2)
        
        # Delete it
        self.persistence.delete_game(game_id)
        
        # Verify it's gone
        loaded = self.persistence.load_game(game_id)
        self.assertIsNone(loaded)
        
        messages = self.persistence.load_messages(game_id)
        self.assertEqual(len(messages), 0)
    
    def test_nonexistent_game(self):
        """Test loading a game that doesn't exist."""
        loaded = self.persistence.load_game("nonexistent_game_id")
        self.assertIsNone(loaded)
    
    def test_empty_game_list(self):
        """Test listing games when none exist."""
        games = self.persistence.list_games()
        self.assertEqual(len(games), 0)


if __name__ == '__main__':
    unittest.main()