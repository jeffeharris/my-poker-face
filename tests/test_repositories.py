"""Test repository pattern implementations."""
import unittest
import tempfile
import os
from datetime import datetime

from poker.repositories import (
    Game, GameMessage, AIPlayerState,
    InMemoryGameRepository, InMemoryMessageRepository, InMemoryAIStateRepository,
    SQLiteGameRepository, SQLiteMessageRepository, SQLiteAIStateRepository
)
from poker.services import GameService
from poker.poker_game import initialize_game_state
from poker.poker_state_machine import PokerStateMachine


class RepositoryTestMixin:
    """Mixin with common repository tests."""
    
    def test_game_save_and_find(self):
        """Test saving and finding games."""
        # Create a game
        game_state = initialize_game_state(['Alice', 'Bob'])
        state_machine = PokerStateMachine(game_state)
        
        game = Game(
            id="test123",
            state_machine=state_machine,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        # Save it
        self.game_repo.save(game)
        
        # Find it
        found_game = self.game_repo.find_by_id("test123")
        self.assertIsNotNone(found_game)
        self.assertEqual(found_game.id, "test123")
        self.assertEqual(found_game.num_players, 3)  # Alice, Bob + Jeff
    
    def test_game_exists(self):
        """Test checking if game exists."""
        self.assertFalse(self.game_repo.exists("nonexistent"))
        
        # Create and save a game
        game_state = initialize_game_state(['Alice'])
        game = Game(
            id="exists123",
            state_machine=PokerStateMachine(game_state),
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        self.game_repo.save(game)
        
        self.assertTrue(self.game_repo.exists("exists123"))
    
    def test_find_recent_games(self):
        """Test finding recent games."""
        # Create multiple games with different timestamps
        base_time = datetime.now()
        
        for i in range(5):
            game_state = initialize_game_state([f'Player{i}'])
            game = Game(
                id=f"game{i}",
                state_machine=PokerStateMachine(game_state),
                created_at=base_time,
                updated_at=datetime.fromtimestamp(base_time.timestamp() - i * 60)
            )
            self.game_repo.save(game)
        
        # Get recent games
        recent = self.game_repo.find_recent(limit=3)
        self.assertEqual(len(recent), 3)
        
        # Should be ordered by updated_at descending
        self.assertEqual(recent[0].id, "game0")
        self.assertEqual(recent[1].id, "game1")
        self.assertEqual(recent[2].id, "game2")
    
    def test_message_save_and_find(self):
        """Test saving and finding messages."""
        msg = GameMessage(
            id=None,
            game_id="test123",
            sender="Alice",
            message="Hello!",
            message_type="player",
            timestamp=datetime.now()
        )
        
        # Save returns message with ID
        saved_msg = self.message_repo.save(msg)
        self.assertIsNotNone(saved_msg.id)
        
        # Find by game ID
        messages = self.message_repo.find_by_game_id("test123")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message, "Hello!")
    
    def test_ai_state_save_and_find(self):
        """Test saving and finding AI states."""
        ai_state = AIPlayerState(
            game_id="test123",
            player_name="Bot1",
            conversation_history=[{"role": "system", "content": "You are Bot1"}],
            personality_state={"aggression": 0.7},
            last_updated=datetime.now()
        )
        
        # Save
        self.ai_state_repo.save(ai_state)
        
        # Find by game and player
        found = self.ai_state_repo.find_by_game_and_player("test123", "Bot1")
        self.assertIsNotNone(found)
        self.assertEqual(found.personality_state["aggression"], 0.7)
        
        # Find all for game
        all_states = self.ai_state_repo.find_by_game_id("test123")
        self.assertEqual(len(all_states), 1)
    
    def test_delete_cascade(self):
        """Test deleting game and related data."""
        game_id = "delete_test"
        
        # Create game with messages and AI state
        game_state = initialize_game_state(['Human', 'Bot'])
        game = Game(
            id=game_id,
            state_machine=PokerStateMachine(game_state),
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        self.game_repo.save(game)
        
        # Add message
        self.message_repo.save(GameMessage(
            id=None,
            game_id=game_id,
            sender="System",
            message="Game started",
            message_type="system",
            timestamp=datetime.now()
        ))
        
        # Add AI state
        self.ai_state_repo.save(AIPlayerState(
            game_id=game_id,
            player_name="Bot",
            conversation_history=[],
            personality_state={},
            last_updated=datetime.now()
        ))
        
        # Verify data exists
        self.assertTrue(self.game_repo.exists(game_id))
        self.assertEqual(len(self.message_repo.find_by_game_id(game_id)), 1)
        self.assertEqual(len(self.ai_state_repo.find_by_game_id(game_id)), 1)
        
        # Delete via service (proper cascade)
        service = GameService(self.game_repo, self.message_repo, self.ai_state_repo)
        service.delete_game(game_id)
        
        # Verify all deleted
        self.assertFalse(self.game_repo.exists(game_id))
        self.assertEqual(len(self.message_repo.find_by_game_id(game_id)), 0)
        self.assertEqual(len(self.ai_state_repo.find_by_game_id(game_id)), 0)


class TestInMemoryRepositories(unittest.TestCase, RepositoryTestMixin):
    """Test in-memory repository implementations."""
    
    def setUp(self):
        """Set up in-memory repositories."""
        self.game_repo = InMemoryGameRepository()
        self.message_repo = InMemoryMessageRepository()
        self.ai_state_repo = InMemoryAIStateRepository()


class TestSQLiteRepositories(unittest.TestCase, RepositoryTestMixin):
    """Test SQLite repository implementations."""
    
    def setUp(self):
        """Set up SQLite repositories with temp database."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.temp_db.name
        
        self.game_repo = SQLiteGameRepository(self.db_path)
        self.message_repo = SQLiteMessageRepository(self.db_path)
        self.ai_state_repo = SQLiteAIStateRepository(self.db_path)
    
    def tearDown(self):
        """Clean up temp database."""
        self.temp_db.close()
        os.unlink(self.db_path)


class TestGameService(unittest.TestCase):
    """Test GameService with repositories."""
    
    def setUp(self):
        """Set up service with in-memory repositories."""
        self.game_repo = InMemoryGameRepository()
        self.message_repo = InMemoryMessageRepository()
        self.ai_state_repo = InMemoryAIStateRepository()
        self.service = GameService(
            self.game_repo,
            self.message_repo,
            self.ai_state_repo
        )
    
    def test_create_game(self):
        """Test creating a game through service."""
        game = self.service.create_game(['Alice', 'Bob'])
        
        self.assertIsNotNone(game.id)
        self.assertEqual(game.num_players, 3)  # Alice, Bob + Jeff
        
        # Should have initial message
        messages = self.service.get_game_messages(game.id)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].sender, "System")
    
    def test_game_workflow(self):
        """Test complete game workflow."""
        # Create game
        game = self.service.create_game(['Alice', 'Bob'])
        
        # Update game
        game.state_machine = game.state_machine.advance()
        self.service.update_game(game)
        
        # Add player message
        self.service.add_message(
            game.id,
            "Alice",
            "I fold",
            "player"
        )
        
        # Save AI state
        self.service.save_ai_state(
            game.id,
            "Bob",
            [{"role": "assistant", "content": "I'm thinking..."}],
            {"bluff_tendency": 0.8}
        )
        
        # Retrieve everything
        loaded_game = self.service.get_game(game.id)
        self.assertIsNotNone(loaded_game)
        
        messages = self.service.get_game_messages(game.id)
        self.assertEqual(len(messages), 2)  # System + Alice
        
        ai_state = self.service.get_ai_state(game.id, "Bob")
        self.assertIsNotNone(ai_state)
        self.assertEqual(ai_state.personality_state["bluff_tendency"], 0.8)


if __name__ == '__main__':
    unittest.main()