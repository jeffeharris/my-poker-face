#!/usr/bin/env python3
"""
Test suite for game persistence functionality.
"""
import json
import sqlite3
from datetime import datetime

import pytest

from poker.persistence import GamePersistence, SavedGame
from poker.poker_game import initialize_game_state, Player
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.utils import get_celebrities
from core.card import Card


# Required for test stats
EMOTIONS = ["confident", "happy", "thinking", "nervous", "angry", "shocked"]


class TestGamePersistence:
    """Test cases for game persistence."""

    def test_save_and_load_game(self, persistence):
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
        persistence.save_game(game_id, state_machine)

        # Load it back
        loaded_machine = persistence.load_game(game_id)

        assert loaded_machine is not None
        assert loaded_machine.current_phase == state_machine.current_phase

        # Check players match
        original_players = [(p.name, p.stack, p.is_human) for p in state_machine.game_state.players]
        loaded_players = [(p.name, p.stack, p.is_human) for p in loaded_machine.game_state.players]
        assert original_players == loaded_players

        # Check game state details
        assert loaded_machine.game_state.pot['total'] == state_machine.game_state.pot['total']
        assert loaded_machine.game_state.current_dealer_idx == state_machine.game_state.current_dealer_idx

    def test_save_and_load_messages(self, persistence):
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
            persistence.save_message(game_id, msg_type, msg_text)

        # Load messages back
        loaded_messages = persistence.load_messages(game_id)

        assert len(loaded_messages) == len(messages)

        for i, (msg_type, msg_text) in enumerate(messages):
            assert loaded_messages[i]['type'] == msg_type
            # load_messages parses "sender: content" format, so check via sender+content
            if ': ' in msg_text:
                sender, content = msg_text.split(': ', 1)
                assert loaded_messages[i]['sender'] == sender
                assert loaded_messages[i]['content'] == content
            else:
                assert loaded_messages[i]['content'] == msg_text

    def test_list_games(self, persistence):
        """Test listing saved games."""
        # Save multiple games
        game_ids = []
        for i in range(5):
            game_id = f"test_game_{i:03d}"
            game_ids.append(game_id)

            player_names = get_celebrities(shuffled=True)[:4]
            game_state = initialize_game_state(player_names=player_names)
            state_machine = PokerStateMachine(game_state=game_state)

            persistence.save_game(game_id, state_machine)

        # List games
        games = persistence.list_games(limit=10)

        assert len(games) == 5

        # Check they're ordered by update time (most recent first)
        for i in range(len(games) - 1):
            assert games[i].updated_at >= games[i + 1].updated_at

        # Check game IDs are present
        listed_ids = [g.game_id for g in games]
        for game_id in game_ids:
            assert game_id in listed_ids

    def test_game_state_with_cards(self, persistence):
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
        persistence.save_game(game_id, state_machine)
        loaded_machine = persistence.load_game(game_id)

        # Check cards were preserved
        loaded_player = loaded_machine.game_state.players[0]
        assert len(loaded_player.hand) == 2
        assert loaded_player.hand[0].rank == 'A'
        assert loaded_player.hand[0].suit == 'Spades'

        assert len(loaded_machine.game_state.community_cards) == 3
        assert loaded_machine.game_state.community_cards[0].rank == 'Q'

    def test_delete_game(self, persistence):
        """Test deleting a game and its messages."""
        game_id = "test_delete"

        # Create and save a game
        game_state = initialize_game_state(player_names=['Jeff', 'AI1'])
        state_machine = PokerStateMachine(game_state=game_state)
        persistence.save_game(game_id, state_machine)

        # Add some messages
        persistence.save_message(game_id, "table", "Test message 1")
        persistence.save_message(game_id, "table", "Test message 2")

        # Verify it exists
        loaded = persistence.load_game(game_id)
        assert loaded is not None

        messages = persistence.load_messages(game_id)
        assert len(messages) == 2

        # Delete it
        persistence.delete_game(game_id)

        # Verify it's gone
        loaded = persistence.load_game(game_id)
        assert loaded is None

        messages = persistence.load_messages(game_id)
        assert len(messages) == 0

    def test_nonexistent_game(self, persistence):
        """Test loading a game that doesn't exist."""
        loaded = persistence.load_game("nonexistent_game_id")
        assert loaded is None

    def test_empty_game_list(self, persistence):
        """Test listing games when none exist."""
        games = persistence.list_games()
        assert len(games) == 0


class TestCardSerialization:
    """Test card serialization and deserialization."""

    def test_serialize_card_object(self, persistence):
        """Test serializing a Card object."""
        card = Card('A', 'Spades')
        result = persistence._serialize_card(card)
        # Card.to_dict() includes suit_symbol
        assert result['rank'] == 'A'
        assert result['suit'] == 'Spades'
        assert 'suit_symbol' in result

    def test_serialize_card_dict(self, persistence):
        """Test serializing a card dict."""
        card_dict = {'rank': 'K', 'suit': 'Hearts'}
        result = persistence._serialize_card(card_dict)
        assert result == card_dict

    def test_serialize_invalid_card(self, persistence):
        """Test serializing invalid card raises error."""
        with pytest.raises(ValueError):
            persistence._serialize_card("not a card")

    def test_deserialize_card_dict(self, persistence):
        """Test deserializing a card dict."""
        card_dict = {'rank': 'Q', 'suit': 'Diamonds'}
        result = persistence._deserialize_card(card_dict)
        assert isinstance(result, Card)
        assert result.rank == 'Q'
        assert result.suit == 'Diamonds'

    def test_deserialize_card_object(self, persistence):
        """Test deserializing an already Card object."""
        card = Card('J', 'Clubs')
        result = persistence._deserialize_card(card)
        assert result == card

    def test_serialize_cards_collection(self, persistence):
        """Test serializing a collection of cards."""
        cards = [
            Card('A', 'Spades'),
            {'rank': 'K', 'suit': 'Hearts'},
            Card('Q', 'Diamonds')
        ]
        result = persistence._serialize_cards(cards)
        assert len(result) == 3
        assert all(isinstance(c, dict) for c in result)

    def test_deserialize_cards_collection(self, persistence):
        """Test deserializing a collection of cards."""
        cards_data = [
            {'rank': 'A', 'suit': 'Spades'},
            {'rank': 'K', 'suit': 'Hearts'},
            {'rank': 'Q', 'suit': 'Diamonds'}
        ]
        result = persistence._deserialize_cards(cards_data)
        assert len(result) == 3
        assert all(isinstance(c, Card) for c in result)


class TestAIStatePersistence:
    """Test AI state saving and loading."""

    def test_save_ai_player_state(self, persistence):
        """Test saving AI player state."""
        game_id = "test_game_123"
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
        persistence.save_ai_player_state(
            game_id,
            "Eeyore",
            messages,
            personality_state
        )

        # Load and verify
        ai_states = persistence.load_ai_player_states(game_id)
        assert "Eeyore" in ai_states

        eeyore_state = ai_states["Eeyore"]
        assert eeyore_state["messages"] == messages
        assert eeyore_state["personality_state"] == personality_state

    def test_save_multiple_ai_states(self, persistence):
        """Test saving states for multiple AI players."""
        game_id = "test_game_123"
        # Save states for multiple players
        players = ["Eeyore", "Kanye West", "Sherlock Holmes"]

        for player in players:
            messages = [{"role": "system", "content": f"You are {player}"}]
            personality = {"traits": {"aggression": 0.5}}
            persistence.save_ai_player_state(
                game_id, player, messages, personality
            )

        # Load all states
        ai_states = persistence.load_ai_player_states(game_id)
        assert len(ai_states) == 3
        for player in players:
            assert player in ai_states

    def test_update_ai_state(self, persistence):
        """Test updating existing AI state."""
        game_id = "test_game_123"
        # Initial save
        initial_messages = [{"role": "system", "content": "You are Eeyore"}]
        initial_personality = {"traits": {"aggression": 0.3}}

        persistence.save_ai_player_state(
            game_id, "Eeyore", initial_messages, initial_personality
        )

        # Update with more messages
        updated_messages = initial_messages + [
            {"role": "user", "content": "Nice hand!"},
            {"role": "assistant", "content": "Thanks... I guess."}
        ]
        updated_personality = {"traits": {"aggression": 0.25}}

        persistence.save_ai_player_state(
            game_id, "Eeyore", updated_messages, updated_personality
        )

        # Verify update
        ai_states = persistence.load_ai_player_states(game_id)
        eeyore_state = ai_states["Eeyore"]
        assert len(eeyore_state["messages"]) == 3
        assert eeyore_state["personality_state"]["traits"]["aggression"] == 0.25

    def test_load_nonexistent_ai_states(self, persistence):
        """Test loading AI states for non-existent game."""
        ai_states = persistence.load_ai_player_states("nonexistent_game")
        assert ai_states == {}


class TestPersonalitySnapshots:
    """Test personality snapshot functionality."""

    def test_save_personality_snapshot(self, persistence):
        """Test saving personality snapshot."""
        game_id = "test_game_123"
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
        persistence.save_personality_snapshot(
            game_id,
            "Kanye West",
            hand_number=5,
            traits=traits,
            pressure_levels=pressure_levels
        )

        # TODO: Add load method when needed for elasticity
        # For now, just verify it doesn't crash

    def test_save_snapshot_without_pressure(self, persistence):
        """Test saving snapshot without pressure levels."""
        game_id = "test_game_123"
        traits = {
            "bluff_tendency": 0.5,
            "aggression": 0.5
        }

        # Should not crash when pressure_levels is None
        persistence.save_personality_snapshot(
            game_id,
            "Test Player",
            hand_number=1,
            traits=traits
        )


class TestDatabaseSchema:
    """Test database schema creation and indices."""

    def test_ai_tables_created(self, db_path, persistence):
        """Test that AI persistence tables are created."""
        with sqlite3.connect(db_path) as conn:
            # Check ai_player_state table
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='ai_player_state'
            """)
            assert cursor.fetchone() is not None

            # Check personality_snapshots table
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='personality_snapshots'
            """)
            assert cursor.fetchone() is not None

    def test_indices_created(self, db_path, persistence):
        """Test that indices are created."""
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='index' AND name='idx_ai_player_game'
            """)
            assert cursor.fetchone() is not None

            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='index' AND name='idx_personality_snapshots'
            """)
            assert cursor.fetchone() is not None


class TestAvatarPersistence:
    """Test avatar image persistence functionality."""

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

    def test_save_and_load_avatar_image(self, persistence):
        """Test saving and loading avatar image bytes."""
        image_data = self._create_test_image_bytes()

        # Save
        persistence.save_avatar_image(
            personality_name="Bob Ross",
            emotion="confident",
            image_data=image_data,
            width=256,
            height=256
        )

        # Load
        loaded_data = persistence.load_avatar_image("Bob Ross", "confident")

        assert loaded_data is not None
        assert loaded_data == image_data

    def test_has_avatar_image(self, persistence):
        """Test checking if avatar exists."""
        image_data = self._create_test_image_bytes()

        # Should not exist initially
        assert not persistence.has_avatar_image("Bob Ross", "happy")

        # Save it
        persistence.save_avatar_image("Bob Ross", "happy", image_data)

        # Should exist now
        assert persistence.has_avatar_image("Bob Ross", "happy")

        # Other emotions should not exist
        assert not persistence.has_avatar_image("Bob Ross", "angry")

    def test_get_available_emotions(self, persistence):
        """Test listing available emotions for personality."""
        image_data = self._create_test_image_bytes()

        # Save multiple emotions
        persistence.save_avatar_image("Batman", "confident", image_data)
        persistence.save_avatar_image("Batman", "angry", image_data)
        persistence.save_avatar_image("Batman", "thinking", image_data)

        # Get available
        emotions = persistence.get_available_avatar_emotions("Batman")

        assert len(emotions) == 3
        assert "confident" in emotions
        assert "angry" in emotions
        assert "thinking" in emotions

    def test_has_all_avatar_emotions(self, persistence):
        """Test checking if personality has all 6 emotions."""
        image_data = self._create_test_image_bytes()

        # Add only 3 emotions
        for emotion in ["confident", "happy", "thinking"]:
            persistence.save_avatar_image("Joker", emotion, image_data)

        assert not persistence.has_all_avatar_emotions("Joker")

        # Add remaining 3 emotions
        for emotion in ["nervous", "angry", "shocked"]:
            persistence.save_avatar_image("Joker", emotion, image_data)

        assert persistence.has_all_avatar_emotions("Joker")

    def test_delete_avatar_images(self, persistence):
        """Test deleting all avatars for a personality."""
        image_data = self._create_test_image_bytes()

        # Save multiple emotions
        for emotion in ["confident", "happy", "angry"]:
            persistence.save_avatar_image("Villain", emotion, image_data)

        # Verify they exist
        assert len(persistence.get_available_avatar_emotions("Villain")) == 3

        # Delete
        count = persistence.delete_avatar_images("Villain")

        assert count == 3
        assert len(persistence.get_available_avatar_emotions("Villain")) == 0

    def test_load_avatar_with_metadata(self, persistence):
        """Test loading avatar image with metadata."""
        image_data = self._create_test_image_bytes()

        persistence.save_avatar_image(
            personality_name="Hero",
            emotion="confident",
            image_data=image_data,
            width=256,
            height=256
        )

        result = persistence.load_avatar_image_with_metadata("Hero", "confident")

        assert result is not None
        assert result['image_data'] == image_data
        assert result['content_type'] == 'image/png'
        assert result['width'] == 256
        assert result['height'] == 256
        assert result['file_size'] == len(image_data)

    def test_get_avatar_stats(self, persistence):
        """Test getting avatar statistics."""
        image_data = self._create_test_image_bytes()

        # Add some avatars
        for emotion in EMOTIONS:
            persistence.save_avatar_image("Complete Player", emotion, image_data)

        persistence.save_avatar_image("Incomplete Player", "confident", image_data)
        persistence.save_avatar_image("Incomplete Player", "happy", image_data)

        stats = persistence.get_avatar_stats()

        assert stats['total_images'] == 8  # 6 + 2
        assert stats['personality_count'] == 2
        assert stats['complete_personality_count'] == 1
        assert stats['total_size_bytes'] > 0

    def test_list_personalities_with_avatars(self, persistence):
        """Test listing personalities that have avatars."""
        image_data = self._create_test_image_bytes()

        persistence.save_avatar_image("Alice", "confident", image_data)
        persistence.save_avatar_image("Alice", "happy", image_data)
        persistence.save_avatar_image("Bob", "confident", image_data)

        result = persistence.list_personalities_with_avatars()

        assert len(result) == 2
        names = [p['personality_name'] for p in result]
        assert "Alice" in names
        assert "Bob" in names

        # Check counts
        alice = next(p for p in result if p['personality_name'] == "Alice")
        assert alice['emotion_count'] == 2

    def test_avatar_table_created(self, db_path, persistence):
        """Test that avatar_images table is created."""
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='avatar_images'
            """)
            assert cursor.fetchone() is not None


class TestPersonalitySeed:
    """Test personality seeding functionality."""

    def test_seed_from_nonexistent_file(self, persistence):
        """Test seeding from non-existent file returns error."""
        result = persistence.seed_personalities_from_json("/nonexistent/path.json")

        assert result['added'] == 0
        assert 'error' in result

    def test_save_and_load_personality(self, persistence):
        """Test saving and loading a personality."""
        config = {
            "play_style": "aggressive",
            "default_confidence": "high",
            "personality_traits": {
                "bluff_tendency": 0.8,
                "aggression": 0.9
            }
        }

        persistence.save_personality("Test Player", config, source='test')

        loaded = persistence.load_personality("Test Player")

        assert loaded is not None
        assert loaded['play_style'] == "aggressive"
        assert loaded['personality_traits']['bluff_tendency'] == 0.8
