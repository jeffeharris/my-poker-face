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
        # Close any open connections before unlinking (T3-07)
        try:
            import sqlite3
            conn = sqlite3.connect(self.test_db.name)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
        except Exception:
            pass
        os.unlink(self.test_db.name)
        # Clean up WAL/SHM files if they exist
        for suffix in ('-wal', '-shm'):
            path = self.test_db.name + suffix
            if os.path.exists(path):
                os.unlink(path)
    
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
            # load_messages parses "sender: content" format, so check via sender+content
            if ': ' in msg_text:
                sender, content = msg_text.split(': ', 1)
                self.assertEqual(loaded_messages[i]['sender'], sender)
                self.assertEqual(loaded_messages[i]['content'], content)
            else:
                self.assertEqual(loaded_messages[i]['content'], msg_text)
    
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


class TestAIStatePersistence(unittest.TestCase):
    """Test AI state saving and loading."""
    
    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.persistence = GamePersistence(self.test_db.name)
        self.game_id = "test_game_123"
    
    def tearDown(self):
        # Close any open connections before unlinking (T3-07)
        try:
            import sqlite3
            conn = sqlite3.connect(self.test_db.name)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
        except Exception:
            pass
        os.unlink(self.test_db.name)
        # Clean up WAL/SHM files if they exist
        for suffix in ('-wal', '-shm'):
            path = self.test_db.name + suffix
            if os.path.exists(path):
                os.unlink(path)
    
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
        # Close any open connections before unlinking (T3-07)
        try:
            import sqlite3
            conn = sqlite3.connect(self.test_db.name)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
        except Exception:
            pass
        os.unlink(self.test_db.name)
        # Clean up WAL/SHM files if they exist
        for suffix in ('-wal', '-shm'):
            path = self.test_db.name + suffix
            if os.path.exists(path):
                os.unlink(path)
    
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
        # Close any open connections before unlinking (T3-07)
        try:
            import sqlite3
            conn = sqlite3.connect(self.test_db.name)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
        except Exception:
            pass
        os.unlink(self.test_db.name)
        # Clean up WAL/SHM files if they exist
        for suffix in ('-wal', '-shm'):
            path = self.test_db.name + suffix
            if os.path.exists(path):
                os.unlink(path)
    
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


class TestAvatarPersistence(unittest.TestCase):
    """Test avatar image persistence functionality."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.persistence = GamePersistence(self.test_db.name)

    def tearDown(self):
        # Close any open connections before unlinking (T3-07)
        try:
            import sqlite3
            conn = sqlite3.connect(self.test_db.name)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
        except Exception:
            pass
        os.unlink(self.test_db.name)
        # Clean up WAL/SHM files if they exist
        for suffix in ('-wal', '-shm'):
            path = self.test_db.name + suffix
            if os.path.exists(path):
                os.unlink(path)

    def _create_test_image_bytes(self) -> bytes:
        """Create minimal PNG bytes for testing."""
        # Minimal valid 1x1 transparent PNG
        return bytes([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
            0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  # IHDR chunk
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
            0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
            0x89, 0x00, 0x00, 0x00, 0x0A, 0x49, 0x44, 0x41,  # IDAT chunk
            0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00,
            0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00,
            0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,  # IEND chunk
            0x42, 0x60, 0x82
        ])

    def test_save_and_load_avatar_image(self):
        """Test saving and loading avatar image bytes."""
        image_data = self._create_test_image_bytes()

        # Save
        self.persistence.save_avatar_image(
            personality_name="Bob Ross",
            emotion="confident",
            image_data=image_data,
            width=256,
            height=256
        )

        # Load
        loaded_data = self.persistence.load_avatar_image("Bob Ross", "confident")

        self.assertIsNotNone(loaded_data)
        self.assertEqual(loaded_data, image_data)

    def test_has_avatar_image(self):
        """Test checking if avatar exists."""
        image_data = self._create_test_image_bytes()

        # Should not exist initially
        self.assertFalse(self.persistence.has_avatar_image("Bob Ross", "happy"))

        # Save it
        self.persistence.save_avatar_image("Bob Ross", "happy", image_data)

        # Should exist now
        self.assertTrue(self.persistence.has_avatar_image("Bob Ross", "happy"))

        # Other emotions should not exist
        self.assertFalse(self.persistence.has_avatar_image("Bob Ross", "angry"))

    def test_get_available_emotions(self):
        """Test listing available emotions for personality."""
        image_data = self._create_test_image_bytes()

        # Save multiple emotions
        self.persistence.save_avatar_image("Batman", "confident", image_data)
        self.persistence.save_avatar_image("Batman", "angry", image_data)
        self.persistence.save_avatar_image("Batman", "thinking", image_data)

        # Get available
        emotions = self.persistence.get_available_avatar_emotions("Batman")

        self.assertEqual(len(emotions), 3)
        self.assertIn("confident", emotions)
        self.assertIn("angry", emotions)
        self.assertIn("thinking", emotions)

    def test_has_all_avatar_emotions(self):
        """Test checking if personality has all 6 emotions."""
        image_data = self._create_test_image_bytes()

        # Add only 3 emotions
        for emotion in ["confident", "happy", "thinking"]:
            self.persistence.save_avatar_image("Joker", emotion, image_data)

        self.assertFalse(self.persistence.has_all_avatar_emotions("Joker"))

        # Add remaining 3 emotions
        for emotion in ["nervous", "angry", "shocked"]:
            self.persistence.save_avatar_image("Joker", emotion, image_data)

        self.assertTrue(self.persistence.has_all_avatar_emotions("Joker"))

    def test_delete_avatar_images(self):
        """Test deleting all avatars for a personality."""
        image_data = self._create_test_image_bytes()

        # Save multiple emotions
        for emotion in ["confident", "happy", "angry"]:
            self.persistence.save_avatar_image("Villain", emotion, image_data)

        # Verify they exist
        self.assertEqual(len(self.persistence.get_available_avatar_emotions("Villain")), 3)

        # Delete
        count = self.persistence.delete_avatar_images("Villain")

        self.assertEqual(count, 3)
        self.assertEqual(len(self.persistence.get_available_avatar_emotions("Villain")), 0)

    def test_load_avatar_with_metadata(self):
        """Test loading avatar image with metadata."""
        image_data = self._create_test_image_bytes()

        self.persistence.save_avatar_image(
            personality_name="Hero",
            emotion="confident",
            image_data=image_data,
            width=256,
            height=256
        )

        result = self.persistence.load_avatar_image_with_metadata("Hero", "confident")

        self.assertIsNotNone(result)
        self.assertEqual(result['image_data'], image_data)
        self.assertEqual(result['content_type'], 'image/png')
        self.assertEqual(result['width'], 256)
        self.assertEqual(result['height'], 256)
        self.assertEqual(result['file_size'], len(image_data))

    def test_get_avatar_stats(self):
        """Test getting avatar statistics."""
        image_data = self._create_test_image_bytes()

        # Add some avatars
        for emotion in EMOTIONS:
            self.persistence.save_avatar_image("Complete Player", emotion, image_data)

        self.persistence.save_avatar_image("Incomplete Player", "confident", image_data)
        self.persistence.save_avatar_image("Incomplete Player", "happy", image_data)

        stats = self.persistence.get_avatar_stats()

        self.assertEqual(stats['total_images'], 8)  # 6 + 2
        self.assertEqual(stats['personality_count'], 2)
        self.assertEqual(stats['complete_personality_count'], 1)
        self.assertGreater(stats['total_size_bytes'], 0)

    def test_list_personalities_with_avatars(self):
        """Test listing personalities that have avatars."""
        image_data = self._create_test_image_bytes()

        self.persistence.save_avatar_image("Alice", "confident", image_data)
        self.persistence.save_avatar_image("Alice", "happy", image_data)
        self.persistence.save_avatar_image("Bob", "confident", image_data)

        result = self.persistence.list_personalities_with_avatars()

        self.assertEqual(len(result), 2)
        names = [p['personality_name'] for p in result]
        self.assertIn("Alice", names)
        self.assertIn("Bob", names)

        # Check counts
        alice = next(p for p in result if p['personality_name'] == "Alice")
        self.assertEqual(alice['emotion_count'], 2)

    def test_avatar_table_created(self):
        """Test that avatar_images table is created."""
        import sqlite3

        with sqlite3.connect(self.test_db.name) as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='avatar_images'
            """)
            self.assertIsNotNone(cursor.fetchone())


class TestPersonalitySeed(unittest.TestCase):
    """Test personality seeding functionality."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.persistence = GamePersistence(self.test_db.name)

    def tearDown(self):
        # Close any open connections before unlinking (T3-07)
        try:
            import sqlite3
            conn = sqlite3.connect(self.test_db.name)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
        except Exception:
            pass
        os.unlink(self.test_db.name)
        # Clean up WAL/SHM files if they exist
        for suffix in ('-wal', '-shm'):
            path = self.test_db.name + suffix
            if os.path.exists(path):
                os.unlink(path)

    def test_seed_from_nonexistent_file(self):
        """Test seeding from non-existent file returns error."""
        result = self.persistence.seed_personalities_from_json("/nonexistent/path.json")

        self.assertEqual(result['added'], 0)
        self.assertIn('error', result)

    def test_save_and_load_personality(self):
        """Test saving and loading a personality."""
        config = {
            "play_style": "aggressive",
            "default_confidence": "high",
            "personality_traits": {
                "bluff_tendency": 0.8,
                "aggression": 0.9
            }
        }

        self.persistence.save_personality("Test Player", config, source='test')

        loaded = self.persistence.load_personality("Test Player")

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['play_style'], "aggressive")
        self.assertEqual(loaded['personality_traits']['bluff_tendency'], 0.8)


# Required for test stats
EMOTIONS = ["confident", "happy", "thinking", "nervous", "angry", "shocked"]


if __name__ == '__main__':
    unittest.main()