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


class TestCardSerialization(unittest.TestCase):
    """Test card serialization and deserialization."""
    
    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.persistence = GamePersistence(self.test_db.name)
    
    def tearDown(self):
        os.unlink(self.test_db.name)
    
    def test_serialize_card_object(self):
        """Test serializing a Card object."""
        card = Card('A', 'Spades')
        result = self.persistence._serialize_card(card)
        # Card.to_dict() includes suit_symbol
        self.assertEqual(result['rank'], 'A')
        self.assertEqual(result['suit'], 'Spades')
        self.assertIn('suit_symbol', result)
    
    def test_serialize_card_dict(self):
        """Test serializing a card dict."""
        card_dict = {'rank': 'K', 'suit': 'Hearts'}
        result = self.persistence._serialize_card(card_dict)
        self.assertEqual(result, card_dict)
    
    def test_serialize_invalid_card(self):
        """Test serializing invalid card raises error."""
        with self.assertRaises(ValueError):
            self.persistence._serialize_card("not a card")
    
    def test_deserialize_card_dict(self):
        """Test deserializing a card dict."""
        card_dict = {'rank': 'Q', 'suit': 'Diamonds'}
        result = self.persistence._deserialize_card(card_dict)
        self.assertIsInstance(result, Card)
        self.assertEqual(result.rank, 'Q')
        self.assertEqual(result.suit, 'Diamonds')
    
    def test_deserialize_card_object(self):
        """Test deserializing an already Card object."""
        card = Card('J', 'Clubs')
        result = self.persistence._deserialize_card(card)
        self.assertEqual(result, card)
    
    def test_serialize_cards_collection(self):
        """Test serializing a collection of cards."""
        cards = [
            Card('A', 'Spades'),
            {'rank': 'K', 'suit': 'Hearts'},
            Card('Q', 'Diamonds')
        ]
        result = self.persistence._serialize_cards(cards)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(isinstance(c, dict) for c in result))
    
    def test_deserialize_cards_collection(self):
        """Test deserializing a collection of cards."""
        cards_data = [
            {'rank': 'A', 'suit': 'Spades'},
            {'rank': 'K', 'suit': 'Hearts'},
            {'rank': 'Q', 'suit': 'Diamonds'}
        ]
        result = self.persistence._deserialize_cards(cards_data)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(isinstance(c, Card) for c in result))


class TestAIStatePersistence(unittest.TestCase):
    """Test AI state saving and loading."""
    
    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.persistence = GamePersistence(self.test_db.name)
        self.game_id = "test_game_123"
    
    def tearDown(self):
        os.unlink(self.test_db.name)
    
    def test_save_ai_player_state(self):
        """Test saving AI player state."""
        messages = [
            {"role": "system", "content": "You are Eeyore"},
            {"role": "user", "content": "What's your move?"},
            {"role": "assistant", "content": "I'll call... I suppose."}
        ]
        
        personality_state = {
            "traits": {
                "bluff_tendency": 0.2,
                "aggression": 0.3,
                "chattiness": 0.5
            },
            "confidence": "Low",
            "attitude": "Pessimistic"
        }
        
        # Save AI state
        self.persistence.save_ai_player_state(
            self.game_id,
            "Eeyore",
            messages,
            personality_state
        )
        
        # Load and verify
        ai_states = self.persistence.load_ai_player_states(self.game_id)
        self.assertIn("Eeyore", ai_states)
        
        eeyore_state = ai_states["Eeyore"]
        self.assertEqual(eeyore_state["messages"], messages)
        self.assertEqual(eeyore_state["personality_state"], personality_state)
    
    def test_save_multiple_ai_states(self):
        """Test saving states for multiple AI players."""
        # Save states for multiple players
        players = ["Eeyore", "Kanye West", "Sherlock Holmes"]
        
        for player in players:
            messages = [{"role": "system", "content": f"You are {player}"}]
            personality = {"traits": {"aggression": 0.5}}
            self.persistence.save_ai_player_state(
                self.game_id, player, messages, personality
            )
        
        # Load all states
        ai_states = self.persistence.load_ai_player_states(self.game_id)
        self.assertEqual(len(ai_states), 3)
        for player in players:
            self.assertIn(player, ai_states)
    
    def test_update_ai_state(self):
        """Test updating existing AI state."""
        # Initial save
        initial_messages = [{"role": "system", "content": "You are Eeyore"}]
        initial_personality = {"traits": {"aggression": 0.3}}
        
        self.persistence.save_ai_player_state(
            self.game_id, "Eeyore", initial_messages, initial_personality
        )
        
        # Update with more messages
        updated_messages = initial_messages + [
            {"role": "user", "content": "Nice hand!"},
            {"role": "assistant", "content": "Thanks... I guess."}
        ]
        updated_personality = {"traits": {"aggression": 0.25}}
        
        self.persistence.save_ai_player_state(
            self.game_id, "Eeyore", updated_messages, updated_personality
        )
        
        # Verify update
        ai_states = self.persistence.load_ai_player_states(self.game_id)
        eeyore_state = ai_states["Eeyore"]
        self.assertEqual(len(eeyore_state["messages"]), 3)
        self.assertEqual(eeyore_state["personality_state"]["traits"]["aggression"], 0.25)
    
    def test_load_nonexistent_ai_states(self):
        """Test loading AI states for non-existent game."""
        ai_states = self.persistence.load_ai_player_states("nonexistent_game")
        self.assertEqual(ai_states, {})


class TestPersonalitySnapshots(unittest.TestCase):
    """Test personality snapshot functionality."""
    
    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.persistence = GamePersistence(self.test_db.name)
        self.game_id = "test_game_123"
    
    def tearDown(self):
        os.unlink(self.test_db.name)
    
    def test_save_personality_snapshot(self):
        """Test saving personality snapshot."""
        traits = {
            "bluff_tendency": 0.8,
            "aggression": 0.7,
            "chattiness": 0.9,
            "emoji_usage": 0.6
        }
        
        pressure_levels = {
            "bluff_tendency": 0.2,
            "aggression": 0.1,
            "chattiness": 0.0,
            "emoji_usage": 0.0
        }
        
        # Save snapshot
        self.persistence.save_personality_snapshot(
            self.game_id,
            "Kanye West",
            hand_number=5,
            traits=traits,
            pressure_levels=pressure_levels
        )
        
        # TODO: Add load method when needed for elasticity
        # For now, just verify it doesn't crash
    
    def test_save_snapshot_without_pressure(self):
        """Test saving snapshot without pressure levels."""
        traits = {
            "bluff_tendency": 0.5,
            "aggression": 0.5
        }
        
        # Should not crash when pressure_levels is None
        self.persistence.save_personality_snapshot(
            self.game_id,
            "Test Player",
            hand_number=1,
            traits=traits
        )


class TestDatabaseSchema(unittest.TestCase):
    """Test database schema creation and indices."""
    
    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.persistence = GamePersistence(self.test_db.name)
    
    def tearDown(self):
        os.unlink(self.test_db.name)
    
    def test_ai_tables_created(self):
        """Test that AI persistence tables are created."""
        import sqlite3
        
        with sqlite3.connect(self.test_db.name) as conn:
            # Check ai_player_state table
            cursor = conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='ai_player_state'
            """)
            self.assertIsNotNone(cursor.fetchone())
            
            # Check personality_snapshots table
            cursor = conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='personality_snapshots'
            """)
            self.assertIsNotNone(cursor.fetchone())
    
    def test_indices_created(self):
        """Test that indices are created."""
        import sqlite3
        
        with sqlite3.connect(self.test_db.name) as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='index' AND name='idx_ai_player_game'
            """)
            self.assertIsNotNone(cursor.fetchone())
            
            cursor = conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='index' AND name='idx_personality_snapshots'
            """)
            self.assertIsNotNone(cursor.fetchone())


if __name__ == '__main__':
    unittest.main()