#!/usr/bin/env python3
"""
Test suite for experiment routes.
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


class TestExperimentRoutes(unittest.TestCase):
    """Test cases for experiment API routes."""

    def setUp(self):
        """Create a test Flask app and temporary database."""
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

        # Patch the persistence to use our test database
        self.persistence = GamePersistence(self.test_db.name)

        # Create the Flask app
        self.app = create_app()
        self.app.testing = True

        # Patch the persistence in extensions
        with patch('flask_app.extensions.persistence', self.persistence):
            self.client = self.app.test_client()

    def tearDown(self):
        """Clean up temporary database."""
        os.unlink(self.test_db.name)

    def test_list_experiments_empty(self):
        """Test listing experiments when none exist."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.get('/api/experiments')
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data['success'])
            self.assertEqual(data['experiments'], [])

    def test_get_quick_prompts(self):
        """Test getting quick prompts."""
        response = self.client.get('/api/experiments/quick-prompts')
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data['success'])
        self.assertIn('prompts', data)
        self.assertGreater(len(data['prompts']), 0)

        # Check prompt structure
        prompt = data['prompts'][0]
        self.assertIn('id', prompt)
        self.assertIn('label', prompt)
        self.assertIn('prompt', prompt)

    def test_get_personalities(self):
        """Test getting available personalities."""
        response = self.client.get('/api/experiments/personalities')
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data['success'])
        self.assertIn('personalities', data)
        self.assertIsInstance(data['personalities'], list)

    def test_get_prompt_options(self):
        """Test getting prompt config options."""
        response = self.client.get('/api/experiments/prompt-options')
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data['success'])
        self.assertIn('fields', data)
        self.assertIn('descriptions', data)

        # Check that key fields are present
        field_names = [f['name'] for f in data['fields']]
        self.assertIn('pot_odds', field_names)
        self.assertIn('hand_strength', field_names)
        self.assertIn('emotional_state', field_names)

    def test_validate_config_missing_name(self):
        """Test validation fails without experiment name."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {'num_tournaments': 5}}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertFalse(data['valid'])
            self.assertIn('Experiment name is required', data['errors'])

    def test_validate_config_invalid_name_format(self):
        """Test validation fails with invalid name format."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {'name': 'Invalid Name With Spaces'}}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertFalse(data['valid'])
            self.assertTrue(any('snake_case' in e for e in data['errors']))

    def test_validate_config_valid(self):
        """Test validation passes with valid config."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {
                    'name': 'valid_experiment_name',
                    'num_tournaments': 3,
                    'hands_per_tournament': 50,
                    'num_players': 4,
                }}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data['valid'])
            self.assertEqual(data['errors'], [])

    def test_validate_config_invalid_range(self):
        """Test validation fails with out-of-range values."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {
                    'name': 'test_experiment',
                    'num_tournaments': 100,  # Too high
                    'num_players': 1,  # Too low
                }}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertFalse(data['valid'])
            self.assertGreater(len(data['errors']), 0)

    def test_validate_config_warning_for_large_experiment(self):
        """Test validation warns for large experiments."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {
                    'name': 'large_experiment',
                    'num_tournaments': 15,
                }}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertGreater(len(data['warnings']), 0)
            self.assertTrue(any('10 tournaments' in w for w in data['warnings']))

    @patch('flask_app.routes.experiment_routes.LLMClient')
    def test_chat_creates_session(self, mock_llm_client):
        """Test chat endpoint creates a session."""
        # Mock LLM response
        mock_response = MagicMock()
        mock_response.content = "I can help you design an experiment. What would you like to test?"
        mock_response.reasoning_content = None
        mock_llm_client.return_value.complete.return_value = mock_response

        response = self.client.post(
            '/api/experiments/chat',
            json={
                'message': 'I want to test model performance',
                'session_id': None,
                'current_config': {}
            }
        )
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data['success'])
        self.assertIn('session_id', data)
        self.assertIsNotNone(data['session_id'])
        self.assertIn('response', data)

    @patch('flask_app.routes.experiment_routes.LLMClient')
    def test_chat_extracts_config_updates(self, mock_llm_client):
        """Test chat endpoint extracts config updates from AI response."""
        # Mock LLM response with config updates
        mock_response = MagicMock()
        mock_response.content = '''Here's a suggested config for your experiment.
<config_updates>{"name": "model_comparison", "num_tournaments": 5}</config_updates>
This will compare model performance over 5 tournaments.'''
        mock_response.reasoning_content = None
        mock_llm_client.return_value.complete.return_value = mock_response

        response = self.client.post(
            '/api/experiments/chat',
            json={
                'message': 'Compare GPT vs Claude',
                'session_id': None,
                'current_config': {}
            }
        )
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data['success'])
        self.assertIsNotNone(data['config_updates'])
        self.assertEqual(data['config_updates']['name'], 'model_comparison')
        self.assertEqual(data['config_updates']['num_tournaments'], 5)

        # Response should not include config_updates tags
        self.assertNotIn('<config_updates>', data['response'])

    def test_chat_requires_message(self):
        """Test chat endpoint requires a message."""
        response = self.client.post(
            '/api/experiments/chat',
            json={
                'message': '',
                'session_id': None,
                'current_config': {}
            }
        )
        data = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertIn('error', data)

    def test_get_nonexistent_experiment(self):
        """Test getting a non-existent experiment returns 404."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.get('/api/experiments/99999')
            data = response.get_json()

            self.assertEqual(response.status_code, 404)
            self.assertIn('error', data)

    @patch('flask_app.routes.experiment_routes.threading.Thread')
    def test_create_experiment(self, mock_thread):
        """Test creating and launching an experiment."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments',
                json={'config': {
                    'name': 'test_experiment',
                    'description': 'A test experiment',
                    'num_tournaments': 1,
                }}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data['success'])
            self.assertIn('experiment_id', data)
            self.assertEqual(data['name'], 'test_experiment')

            # Verify thread was started
            mock_thread.return_value.start.assert_called_once()

    def test_create_experiment_duplicate_name(self):
        """Test creating experiment with duplicate name fails."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            # Create first experiment directly in DB
            self.persistence.create_experiment({
                'name': 'duplicate_name',
                'description': 'First experiment'
            })

            # Try to create another with same name
            response = self.client.post(
                '/api/experiments',
                json={'config': {
                    'name': 'duplicate_name',
                    'description': 'Second experiment',
                }}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 400)
            self.assertIn('error', data)
            self.assertIn('already exists', data['error'])


class TestPersistenceExperimentMethods(unittest.TestCase):
    """Test experiment-specific persistence methods."""

    def setUp(self):
        """Create a temporary database for each test."""
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.persistence = GamePersistence(self.test_db.name)

    def tearDown(self):
        """Clean up temporary database."""
        os.unlink(self.test_db.name)

    def test_list_experiments_with_status_filter(self):
        """Test listing experiments with status filter."""
        # Create experiments with different statuses
        # Note: Default status in DB schema is 'running'
        exp1_id = self.persistence.create_experiment({
            'name': 'running_exp',
            'description': 'A running experiment'
        })
        exp2_id = self.persistence.create_experiment({
            'name': 'completed_exp',
            'description': 'A completed experiment'
        })

        # Complete one experiment
        self.persistence.complete_experiment(exp2_id, {'winners': {'Batman': 1}})

        # List all experiments
        all_exps = self.persistence.list_experiments()
        self.assertEqual(len(all_exps), 2)

        # List only completed
        completed_exps = self.persistence.list_experiments(status='completed')
        self.assertEqual(len(completed_exps), 1)
        self.assertEqual(completed_exps[0]['name'], 'completed_exp')

        # List only running (default status)
        running_exps = self.persistence.list_experiments(status='running')
        self.assertEqual(len(running_exps), 1)
        self.assertEqual(running_exps[0]['name'], 'running_exp')

    def test_update_experiment_status(self):
        """Test updating experiment status."""
        exp_id = self.persistence.create_experiment({
            'name': 'status_test',
        })

        # Initially 'running' (DB schema default)
        exp = self.persistence.get_experiment(exp_id)
        self.assertEqual(exp['status'], 'running')

        # Update to completed
        self.persistence.update_experiment_status(exp_id, 'completed')
        exp = self.persistence.get_experiment(exp_id)
        self.assertEqual(exp['status'], 'completed')
        self.assertIsNotNone(exp['completed_at'])

        # Create another experiment and update to failed with error message
        exp2_id = self.persistence.create_experiment({'name': 'status_test_2'})
        self.persistence.update_experiment_status(exp2_id, 'failed', 'Something went wrong')
        exp2 = self.persistence.get_experiment(exp2_id)
        self.assertEqual(exp2['status'], 'failed')
        self.assertIn('Something went wrong', exp2['notes'] or '')

    def test_update_experiment_status_invalid(self):
        """Test updating experiment status with invalid value."""
        exp_id = self.persistence.create_experiment({
            'name': 'invalid_status_test',
        })

        with self.assertRaises(ValueError):
            self.persistence.update_experiment_status(exp_id, 'invalid_status')


if __name__ == '__main__':
    unittest.main()
