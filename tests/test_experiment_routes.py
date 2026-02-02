#!/usr/bin/env python3
"""
Test suite for experiment routes.
"""
import json

import pytest
from unittest.mock import patch, MagicMock

from flask_app import create_app
from poker.persistence import GamePersistence


class TestExperimentRoutes:
    """Test cases for experiment API routes."""

    @pytest.fixture(autouse=True)
    def setup_flask(self, persistence):
        self.persistence = persistence
        self.app = create_app()
        self.app.testing = True
        with patch('flask_app.extensions.persistence', persistence):
            self.client = self.app.test_client()

    def test_list_experiments_empty(self):
        """Test listing experiments when none exist."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.get('/api/experiments')
            data = response.get_json()

            assert response.status_code == 200
            assert data['success'] is True
            assert data['experiments'] == []

    def test_get_quick_prompts(self):
        """Test getting quick prompts."""
        response = self.client.get('/api/experiments/quick-prompts')
        data = response.get_json()

        assert response.status_code == 200
        assert data['success'] is True
        assert 'prompts' in data
        assert len(data['prompts']) > 0

        # Check prompt structure
        prompt = data['prompts'][0]
        assert 'id' in prompt
        assert 'label' in prompt
        assert 'prompt' in prompt

    def test_get_personalities(self):
        """Test getting available personalities."""
        response = self.client.get('/api/experiments/personalities')
        data = response.get_json()

        assert response.status_code == 200
        assert data['success'] is True
        assert 'personalities' in data
        assert isinstance(data['personalities'], list)

    def test_get_prompt_options(self):
        """Test getting prompt config options."""
        response = self.client.get('/api/experiments/prompt-options')
        data = response.get_json()

        assert response.status_code == 200
        assert data['success'] is True
        assert 'fields' in data
        assert 'descriptions' in data

        # Check that key fields are present
        field_names = [f['name'] for f in data['fields']]
        assert 'pot_odds' in field_names
        assert 'hand_strength' in field_names
        assert 'emotional_state' in field_names

    def test_validate_config_missing_name(self):
        """Test validation fails without experiment name."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {'num_tournaments': 5}}
            )
            data = response.get_json()

            assert response.status_code == 200
            assert data['valid'] is False
            assert 'Experiment name is required' in data['errors']

    def test_validate_config_invalid_name_format(self):
        """Test validation fails with invalid name format."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {'name': 'Invalid Name With Spaces'}}
            )
            data = response.get_json()

            assert response.status_code == 200
            assert data['valid'] is False
            assert any('snake_case' in e for e in data['errors'])

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

            assert response.status_code == 200
            assert data['valid'] is True
            assert data['errors'] == []

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

            assert response.status_code == 200
            assert data['valid'] is False
            assert len(data['errors']) > 0

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

            assert response.status_code == 200
            assert len(data['warnings']) > 0
            assert any('10 tournaments' in w for w in data['warnings'])

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

        assert response.status_code == 200
        assert data['success'] is True
        assert 'session_id' in data
        assert data['session_id'] is not None
        assert 'response' in data

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

        assert response.status_code == 200
        assert data['success'] is True
        assert data['config_updates'] is not None
        assert data['config_updates']['name'] == 'model_comparison'
        assert data['config_updates']['num_tournaments'] == 5

        # Response should not include config_updates tags
        assert '<config_updates>' not in data['response']

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

        assert response.status_code == 400
        assert 'error' in data

    def test_get_nonexistent_experiment(self):
        """Test getting a non-existent experiment returns 404."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.get('/api/experiments/99999')
            data = response.get_json()

            assert response.status_code == 404
            assert 'error' in data

    @patch('flask_app.routes.experiment_routes.run_experiment_background')
    def test_create_experiment(self, mock_run):
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

            assert response.status_code == 200
            assert data['success'] is True
            assert 'experiment_id' in data
            assert data['name'] == 'test_experiment'

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

            assert response.status_code == 400
            assert 'error' in data
            assert 'already exists' in data['error']


class TestPersistenceExperimentMethods:
    """Test experiment-specific persistence methods."""

    def test_list_experiments_with_status_filter(self, persistence):
        """Test listing experiments with status filter."""
        # Create experiments with different statuses
        exp1_id = persistence.create_experiment({
            'name': 'running_exp',
            'description': 'A running experiment'
        })
        exp2_id = persistence.create_experiment({
            'name': 'completed_exp',
            'description': 'A completed experiment'
        })

        # Complete one experiment
        persistence.complete_experiment(exp2_id, {'winners': {'Batman': 1}})

        # List all experiments
        all_exps = persistence.list_experiments()
        assert len(all_exps) == 2

        # List only completed
        completed_exps = persistence.list_experiments(status='completed')
        assert len(completed_exps) == 1
        assert completed_exps[0]['name'] == 'completed_exp'

        # List only running (default status)
        running_exps = persistence.list_experiments(status='running')
        assert len(running_exps) == 1
        assert running_exps[0]['name'] == 'running_exp'

    def test_update_experiment_status(self, persistence):
        """Test updating experiment status."""
        exp_id = persistence.create_experiment({
            'name': 'status_test',
        })

        # Initially 'running' (DB schema default)
        exp = persistence.get_experiment(exp_id)
        assert exp['status'] == 'running'

        # Update to completed
        persistence.update_experiment_status(exp_id, 'completed')
        exp = persistence.get_experiment(exp_id)
        assert exp['status'] == 'completed'
        assert exp['completed_at'] is not None

        # Create another experiment and update to failed with error message
        exp2_id = persistence.create_experiment({'name': 'status_test_2'})
        persistence.update_experiment_status(exp2_id, 'failed', 'Something went wrong')
        exp2 = persistence.get_experiment(exp2_id)
        assert exp2['status'] == 'failed'
        assert 'Something went wrong' in (exp2['notes'] or '')

    def test_update_experiment_status_invalid(self, persistence):
        """Test updating experiment status with invalid value."""
        exp_id = persistence.create_experiment({
            'name': 'invalid_status_test',
        })

        with pytest.raises(ValueError):
            persistence.update_experiment_status(exp_id, 'invalid_status')
