#!/usr/bin/env python3
"""
Test suite for experiment chat persistence and session management.
"""
import os
import sys
import unittest
import tempfile
import json
from unittest.mock import patch, MagicMock

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from flask_app import create_app
from poker.persistence import GamePersistence


class TestChatSessionPersistence(unittest.TestCase):
    """Test cases for chat session persistence methods."""

    def setUp(self):
        """Create a temporary database for each test."""
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.persistence = GamePersistence(self.test_db.name)

    def tearDown(self):
        """Clean up temporary database."""
        os.unlink(self.test_db.name)

    def test_save_and_get_chat_session(self):
        """Test saving and retrieving a chat session."""
        session_id = 'test_session_123'
        owner_id = 'user_456'
        messages = [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi there!'},
        ]
        config = {'name': 'test_experiment', 'num_tournaments': 5}
        versions = [{'timestamp': '2024-01-01', 'config': config}]

        # Save session
        self.persistence.save_chat_session(
            session_id=session_id,
            owner_id=owner_id,
            messages=messages,
            config_snapshot=config,
            config_versions=versions,
        )

        # Get latest session
        result = self.persistence.get_latest_chat_session(owner_id)

        self.assertIsNotNone(result)
        self.assertEqual(result['session_id'], session_id)
        self.assertEqual(result['messages'], messages)
        self.assertEqual(result['config'], config)
        self.assertEqual(result['config_versions'], versions)

    def test_get_latest_chat_session_returns_most_recent(self):
        """Test that get_latest_chat_session returns the most recently updated session."""
        owner_id = 'user_123'

        # Save first session
        self.persistence.save_chat_session(
            session_id='session_1',
            owner_id=owner_id,
            messages=[{'role': 'user', 'content': 'First'}],
            config_snapshot={'name': 'first'},
        )

        # Save second session
        self.persistence.save_chat_session(
            session_id='session_2',
            owner_id=owner_id,
            messages=[{'role': 'user', 'content': 'Second'}],
            config_snapshot={'name': 'second'},
        )

        # Update session_1 to make it more recent
        self.persistence.save_chat_session(
            session_id='session_1',
            owner_id=owner_id,
            messages=[{'role': 'user', 'content': 'First - updated'}],
            config_snapshot={'name': 'first_updated'},
        )

        # Get latest should return session_1 (most recently updated)
        result = self.persistence.get_latest_chat_session(owner_id)

        self.assertIsNotNone(result)
        self.assertEqual(result['session_id'], 'session_1')
        self.assertEqual(result['config']['name'], 'first_updated')

    def test_get_latest_chat_session_no_session(self):
        """Test get_latest_chat_session returns None when no session exists."""
        result = self.persistence.get_latest_chat_session('nonexistent_user')
        self.assertIsNone(result)

    def test_get_chat_session_by_id(self):
        """Test get_chat_session retrieves a session by its ID."""
        session_id = 'specific_session_123'
        owner_id = 'user_456'
        config_versions = [
            {'timestamp': '2024-01-01T00:00:00', 'config': {'name': 'v1'}, 'message_index': 0},
            {'timestamp': '2024-01-01T00:01:00', 'config': {'name': 'v2'}, 'message_index': 2},
        ]

        # Save a session
        self.persistence.save_chat_session(
            session_id=session_id,
            owner_id=owner_id,
            messages=[{'role': 'user', 'content': 'Hello'}],
            config_snapshot={'name': 'test_config'},
            config_versions=config_versions,
        )

        # Retrieve by ID
        result = self.persistence.get_chat_session(session_id)

        self.assertIsNotNone(result)
        self.assertEqual(result['session_id'], session_id)
        self.assertEqual(result['config']['name'], 'test_config')
        self.assertEqual(len(result['config_versions']), 2)
        self.assertEqual(result['config_versions'][0]['config']['name'], 'v1')
        self.assertEqual(result['config_versions'][1]['config']['name'], 'v2')

    def test_get_chat_session_not_found(self):
        """Test get_chat_session returns None for nonexistent session."""
        result = self.persistence.get_chat_session('nonexistent_session')
        self.assertIsNone(result)

    def test_archive_chat_session(self):
        """Test archiving a chat session hides it from latest."""
        owner_id = 'user_123'

        # Save a session
        self.persistence.save_chat_session(
            session_id='session_to_archive',
            owner_id=owner_id,
            messages=[{'role': 'user', 'content': 'Hello'}],
            config_snapshot={'name': 'test'},
        )

        # Verify it's returned as latest
        result = self.persistence.get_latest_chat_session(owner_id)
        self.assertIsNotNone(result)
        self.assertEqual(result['session_id'], 'session_to_archive')

        # Archive it
        self.persistence.archive_chat_session('session_to_archive')

        # Should no longer be returned as latest
        result = self.persistence.get_latest_chat_session(owner_id)
        self.assertIsNone(result)

    def test_delete_chat_session(self):
        """Test deleting a chat session."""
        owner_id = 'user_123'

        # Save a session
        self.persistence.save_chat_session(
            session_id='session_to_delete',
            owner_id=owner_id,
            messages=[{'role': 'user', 'content': 'Hello'}],
            config_snapshot={'name': 'test'},
        )

        # Verify it exists
        result = self.persistence.get_latest_chat_session(owner_id)
        self.assertIsNotNone(result)

        # Delete it
        self.persistence.delete_chat_session('session_to_delete')

        # Should no longer exist
        result = self.persistence.get_latest_chat_session(owner_id)
        self.assertIsNone(result)

    def test_update_existing_session(self):
        """Test that saving to an existing session_id updates it."""
        session_id = 'session_123'
        owner_id = 'user_456'

        # Save initial session
        self.persistence.save_chat_session(
            session_id=session_id,
            owner_id=owner_id,
            messages=[{'role': 'user', 'content': 'Hello'}],
            config_snapshot={'name': 'initial'},
        )

        # Update the session
        self.persistence.save_chat_session(
            session_id=session_id,
            owner_id=owner_id,
            messages=[
                {'role': 'user', 'content': 'Hello'},
                {'role': 'assistant', 'content': 'Hi!'},
            ],
            config_snapshot={'name': 'updated'},
        )

        # Get session and verify it was updated
        result = self.persistence.get_latest_chat_session(owner_id)
        self.assertEqual(len(result['messages']), 2)
        self.assertEqual(result['config']['name'], 'updated')


class TestExperimentDesignChatPersistence(unittest.TestCase):
    """Test cases for experiment design chat storage."""

    def setUp(self):
        """Create a temporary database for each test."""
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.persistence = GamePersistence(self.test_db.name)

    def tearDown(self):
        """Clean up temporary database."""
        os.unlink(self.test_db.name)

    def test_save_and_get_design_chat(self):
        """Test saving and retrieving design chat history."""
        # Create an experiment
        exp_id = self.persistence.create_experiment({
            'name': 'test_with_design_chat',
            'description': 'Test experiment',
        })

        # Save design chat
        design_chat = [
            {'role': 'user', 'content': 'I want to test model performance'},
            {'role': 'assistant', 'content': 'Great! Let me help you design that experiment.'},
            {'role': 'user', 'content': 'Make it 5 tournaments'},
            {'role': 'assistant', 'content': 'Done, I set num_tournaments to 5.'},
        ]
        self.persistence.save_experiment_design_chat(exp_id, design_chat)

        # Retrieve design chat
        result = self.persistence.get_experiment_design_chat(exp_id)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]['role'], 'user')
        self.assertEqual(result[1]['role'], 'assistant')

    def test_get_design_chat_no_chat(self):
        """Test get_experiment_design_chat returns None when no chat stored."""
        exp_id = self.persistence.create_experiment({
            'name': 'test_no_chat',
        })

        result = self.persistence.get_experiment_design_chat(exp_id)
        self.assertIsNone(result)

    def test_save_and_get_assistant_chat(self):
        """Test saving and retrieving assistant chat history."""
        # Create an experiment
        exp_id = self.persistence.create_experiment({
            'name': 'test_with_assistant_chat',
        })

        # Save assistant chat
        assistant_chat = [
            {'role': 'user', 'content': 'Why did we choose 5 tournaments?'},
            {'role': 'assistant', 'content': 'Based on our design conversation...'},
        ]
        self.persistence.save_experiment_assistant_chat(exp_id, assistant_chat)

        # Retrieve assistant chat
        result = self.persistence.get_experiment_assistant_chat(exp_id)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)

    def test_design_and_assistant_chat_independent(self):
        """Test that design chat and assistant chat are stored independently."""
        exp_id = self.persistence.create_experiment({
            'name': 'test_independent_chats',
        })

        # Save different chats
        design_chat = [{'role': 'user', 'content': 'Design message'}]
        assistant_chat = [{'role': 'user', 'content': 'Assistant message'}]

        self.persistence.save_experiment_design_chat(exp_id, design_chat)
        self.persistence.save_experiment_assistant_chat(exp_id, assistant_chat)

        # Verify they're independent
        design_result = self.persistence.get_experiment_design_chat(exp_id)
        assistant_result = self.persistence.get_experiment_assistant_chat(exp_id)

        self.assertEqual(design_result[0]['content'], 'Design message')
        self.assertEqual(assistant_result[0]['content'], 'Assistant message')


class TestChatEndpoints(unittest.TestCase):
    """Test cases for chat-related API endpoints."""

    def setUp(self):
        """Create a test Flask app and temporary database."""
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

        self.persistence = GamePersistence(self.test_db.name)
        self.app = create_app()
        self.app.testing = True

        with patch('flask_app.extensions.persistence', self.persistence):
            self.client = self.app.test_client()

    def tearDown(self):
        """Clean up temporary database."""
        os.unlink(self.test_db.name)

    def test_get_latest_chat_session_empty(self):
        """Test getting latest chat session when none exists."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.get('/api/experiments/chat/latest')
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data['success'])
            self.assertIsNone(data['session'])

    def test_archive_chat_session_endpoint(self):
        """Test archiving a chat session via API."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            # First save a session directly
            self.persistence.save_chat_session(
                session_id='test_archive_session',
                owner_id='anonymous',
                messages=[{'role': 'user', 'content': 'Test'}],
                config_snapshot={'name': 'test'},
            )

            # Archive via API
            response = self.client.post(
                '/api/experiments/chat/archive',
                json={'session_id': 'test_archive_session'}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data['success'])
            self.assertTrue(data['archived'])

    def test_archive_chat_session_requires_session_id(self):
        """Test that archive endpoint requires session_id."""
        response = self.client.post(
            '/api/experiments/chat/archive',
            json={}
        )
        data = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertIn('error', data)

    @patch('flask_app.routes.experiment_routes.LLMClient')
    def test_chat_returns_config_diff(self, mock_llm_client):
        """Test that chat endpoint returns config_diff when AI updates config."""
        mock_response = MagicMock()
        mock_response.content = '''I've set up your experiment.
<config_updates>{"name": "test_exp", "num_tournaments": 5}</config_updates>'''
        mock_response.reasoning_content = None
        mock_llm_client.return_value.complete.return_value = mock_response

        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/chat',
                json={
                    'message': 'Create a 5 tournament experiment',
                    'session_id': None,
                    'current_config': {'name': '', 'num_tournaments': 1}
                }
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data['success'])
            self.assertIn('config_diff', data)
            # Should have a diff showing the changes
            if data['config_diff']:
                self.assertIn('num_tournaments', data['config_diff'])

    @patch('flask_app.routes.experiment_routes.LLMClient')
    def test_chat_persists_session(self, mock_llm_client):
        """Test that chat endpoint persists session to database."""
        mock_response = MagicMock()
        mock_response.content = 'Here is my response.'
        mock_response.reasoning_content = None
        mock_llm_client.return_value.complete.return_value = mock_response

        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            # Send a chat message
            response = self.client.post(
                '/api/experiments/chat',
                json={
                    'message': 'Hello',
                    'session_id': None,
                    'current_config': {}
                }
            )
            data = response.get_json()
            session_id = data['session_id']

            # Verify session was persisted
            session = self.persistence.get_latest_chat_session('anonymous')
            self.assertIsNotNone(session)
            self.assertEqual(session['session_id'], session_id)


class TestExperimentAssistantChat(unittest.TestCase):
    """Test cases for experiment-scoped assistant chat."""

    def setUp(self):
        """Create a test Flask app and temporary database."""
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

        self.persistence = GamePersistence(self.test_db.name)
        self.app = create_app()
        self.app.testing = True

        with patch('flask_app.extensions.persistence', self.persistence):
            self.client = self.app.test_client()

        # Create a test experiment
        self.exp_id = self.persistence.create_experiment({
            'name': 'test_assistant_experiment',
            'description': 'Test experiment for assistant chat',
        })

    def tearDown(self):
        """Clean up temporary database."""
        os.unlink(self.test_db.name)

    def test_get_empty_chat_history(self):
        """Test getting chat history when none exists."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.get(f'/api/experiments/{self.exp_id}/chat/history')
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data['success'])
            self.assertEqual(data['history'], [])

    @patch('flask_app.routes.experiment_routes.LLMClient')
    def test_experiment_assistant_chat(self, mock_llm_client):
        """Test chatting with experiment assistant."""
        mock_response = MagicMock()
        mock_response.content = 'Based on the experiment results...'
        mock_llm_client.return_value.complete.return_value = mock_response

        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                f'/api/experiments/{self.exp_id}/chat',
                json={'message': 'What were the results?'}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data['success'])
            self.assertIn('response', data)

    @patch('flask_app.routes.experiment_routes.LLMClient')
    @patch('flask_app.routes.experiment_routes._experiment_assistant_sessions', {})
    def test_experiment_assistant_chat_persists_history(self, mock_llm_client):
        """Test that experiment assistant chat persists history."""
        mock_response = MagicMock()
        mock_response.content = 'Here is my analysis.'
        mock_llm_client.return_value.complete.return_value = mock_response

        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            # Send a message
            self.client.post(
                f'/api/experiments/{self.exp_id}/chat',
                json={'message': 'Analyze the results'}
            )

            # Check history was saved
            history = self.persistence.get_experiment_assistant_chat(self.exp_id)
            self.assertIsNotNone(history)
            self.assertEqual(len(history), 2)  # User + assistant message

    def test_clear_experiment_chat_history(self):
        """Test clearing experiment assistant chat history."""
        # Save some chat history
        self.persistence.save_experiment_assistant_chat(self.exp_id, [
            {'role': 'user', 'content': 'Question'},
            {'role': 'assistant', 'content': 'Answer'},
        ])

        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            # Clear history
            response = self.client.post(f'/api/experiments/{self.exp_id}/chat/clear')
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data['success'])
            self.assertTrue(data['cleared'])

            # Verify history is empty
            history = self.persistence.get_experiment_assistant_chat(self.exp_id)
            self.assertEqual(history, [])

    def test_experiment_assistant_chat_requires_message(self):
        """Test that experiment assistant chat requires a message."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                f'/api/experiments/{self.exp_id}/chat',
                json={'message': ''}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 400)
            self.assertIn('error', data)

    def test_experiment_assistant_chat_nonexistent_experiment(self):
        """Test assistant chat with non-existent experiment."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/99999/chat',
                json={'message': 'Hello'}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 404)
            self.assertIn('error', data)


if __name__ == '__main__':
    unittest.main()
