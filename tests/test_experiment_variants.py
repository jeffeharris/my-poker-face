#!/usr/bin/env python3
"""
Test suite for experiment variant (A/B testing) functionality.
"""
import os
import sys
import unittest
import tempfile
from unittest.mock import patch, MagicMock

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from experiments.run_ai_tournament import ExperimentConfig, TournamentResult
from experiments.variant_config import build_effective_variant_config, VariantConfig, ControlConfig


class TestExperimentConfigVariants(unittest.TestCase):
    """Test cases for ExperimentConfig variant methods."""

    def test_get_variant_configs_legacy_mode(self):
        """Test get_variant_configs returns single entry in legacy mode (no control)."""
        config = ExperimentConfig(
            name='test_experiment',
            model='gpt-5-nano',
            provider='openai',
        )

        variants = config.get_variant_configs()

        self.assertEqual(len(variants), 1)
        label, effective_config = variants[0]
        self.assertIsNone(label)  # No label in legacy mode
        self.assertEqual(effective_config['model'], 'gpt-5-nano')
        self.assertEqual(effective_config['provider'], 'openai')

    def test_get_variant_configs_control_only(self):
        """Test get_variant_configs with control but no variants.

        Note: Control always uses experiment-level model/provider, even if
        model/provider are specified in control config (they're ignored).
        """
        config = ExperimentConfig(
            name='test_experiment',
            model='gpt-5-nano',
            provider='openai',
            control={
                'label': 'GPT Control',
                # model/provider on control are now ignored - uses experiment-level
            },
            variants=[],
        )

        variants = config.get_variant_configs()

        self.assertEqual(len(variants), 1)
        label, effective_config = variants[0]
        self.assertEqual(label, 'GPT Control')
        # Control always uses experiment-level model/provider
        self.assertEqual(effective_config['model'], 'gpt-5-nano')
        self.assertEqual(effective_config['provider'], 'openai')

    def test_get_variant_configs_control_with_variants(self):
        """Test get_variant_configs with control and variants.

        Control uses experiment-level model/provider. Variants can override.
        """
        config = ExperimentConfig(
            name='model_comparison',
            model='gpt-5-nano',
            provider='openai',
            control={
                'label': 'GPT Baseline',
                # model/provider not set - uses experiment-level
            },
            variants=[
                {
                    'label': 'Claude Sonnet',
                    'model': 'claude-sonnet-4-20250514',
                    'provider': 'anthropic',
                },
                {
                    'label': 'Claude Haiku',
                    'model': 'claude-haiku-4-5-20251001',
                    'provider': 'anthropic',
                },
            ],
        )

        variants = config.get_variant_configs()

        self.assertEqual(len(variants), 3)

        # First should be control (uses experiment-level model/provider)
        label, effective_config = variants[0]
        self.assertEqual(label, 'GPT Baseline')
        self.assertEqual(effective_config['model'], 'gpt-5-nano')  # From experiment
        self.assertEqual(effective_config['provider'], 'openai')   # From experiment

        # Second should be first variant (overrides model/provider)
        label, effective_config = variants[1]
        self.assertEqual(label, 'Claude Sonnet')
        self.assertEqual(effective_config['model'], 'claude-sonnet-4-20250514')
        self.assertEqual(effective_config['provider'], 'anthropic')

        # Third should be second variant (overrides model/provider)
        label, effective_config = variants[2]
        self.assertEqual(label, 'Claude Haiku')
        self.assertEqual(effective_config['model'], 'claude-haiku-4-5-20251001')
        self.assertEqual(effective_config['provider'], 'anthropic')

    def test_get_variant_configs_variant_inherits_from_experiment(self):
        """Test that variants inherit unspecified model/provider from experiment.

        This is the new behavior: variants skip control and inherit directly
        from experiment-level settings for model/provider.
        """
        config = ExperimentConfig(
            name='prompt_ablation',
            model='gpt-5-nano',
            provider='openai',
            control={
                'label': 'Full Prompts',
                # model/provider on control are ignored
            },
            variants=[
                {
                    'label': 'No Pot Odds',
                    # model and provider not specified - should inherit from experiment
                },
            ],
        )

        variants = config.get_variant_configs()

        self.assertEqual(len(variants), 2)

        # Control uses experiment-level
        label, control_config = variants[0]
        self.assertEqual(control_config['model'], 'gpt-5-nano')
        self.assertEqual(control_config['provider'], 'openai')

        # Variant inherits model and provider from experiment (not control)
        label, variant_config = variants[1]
        self.assertEqual(label, 'No Pot Odds')
        self.assertEqual(variant_config['model'], 'gpt-5-nano')  # From experiment
        self.assertEqual(variant_config['provider'], 'openai')   # From experiment

    def test_get_variant_configs_control_defaults_to_experiment(self):
        """Test that control inherits unspecified fields from experiment config."""
        config = ExperimentConfig(
            name='test',
            model='gpt-5-nano',
            provider='openai',
            control={
                'label': 'Control',
                # model and provider not specified - should inherit from experiment
            },
            variants=[],
        )

        variants = config.get_variant_configs()

        label, effective_config = variants[0]
        self.assertEqual(effective_config['model'], 'gpt-5-nano')  # From experiment
        self.assertEqual(effective_config['provider'], 'openai')  # From experiment

    def test_get_total_tournaments_legacy_mode(self):
        """Test get_total_tournaments in legacy mode."""
        config = ExperimentConfig(
            name='test',
            num_tournaments=5,
        )

        self.assertEqual(config.get_total_tournaments(), 5)

    def test_get_total_tournaments_with_variants(self):
        """Test get_total_tournaments with control and variants."""
        config = ExperimentConfig(
            name='test',
            num_tournaments=3,
            control={'label': 'Control'},
            variants=[
                {'label': 'Variant 1'},
                {'label': 'Variant 2'},
            ],
        )

        # 3 tournaments per variant × 3 variants (control + 2) = 9
        self.assertEqual(config.get_total_tournaments(), 9)

    def test_get_total_tournaments_control_only(self):
        """Test get_total_tournaments with control but no variants."""
        config = ExperimentConfig(
            name='test',
            num_tournaments=5,
            control={'label': 'Control'},
            variants=[],
        )

        # 5 tournaments × 1 variant (control only) = 5
        self.assertEqual(config.get_total_tournaments(), 5)


class TestBuildEffectiveVariantConfigGameMode(unittest.TestCase):
    """Test cases for build_effective_variant_config with game_mode."""

    def test_build_effective_with_game_mode(self):
        """build_effective_variant_config should include game_mode."""
        result = build_effective_variant_config(
            {'label': 'V1', 'game_mode': 'pro'},
            control_dict={'game_mode': 'standard'}
        )
        self.assertEqual(result['game_mode'], 'pro')  # Variant overrides control

    def test_game_mode_inheritance_from_control(self):
        """Variant should inherit game_mode from control if not set."""
        result = build_effective_variant_config(
            {'label': 'V1'},  # No game_mode
            control_dict={'game_mode': 'standard'}
        )
        self.assertEqual(result['game_mode'], 'standard')

    def test_game_mode_none_when_not_set(self):
        """game_mode should be None when not set anywhere."""
        result = build_effective_variant_config(
            {'label': 'V1'},
            control_dict=None
        )
        self.assertIsNone(result['game_mode'])

    def test_variant_config_dataclass_game_mode(self):
        """VariantConfig dataclass should have game_mode field."""
        config = VariantConfig(label="Test", game_mode="pro")
        self.assertEqual(config.game_mode, "pro")
        d = config.to_dict()
        self.assertEqual(d['game_mode'], "pro")

    def test_control_config_dataclass_game_mode(self):
        """ControlConfig dataclass should have game_mode field."""
        config = ControlConfig(label="Control", game_mode="standard")
        self.assertEqual(config.game_mode, "standard")
        d = config.to_dict()
        self.assertEqual(d['game_mode'], "standard")

    def test_variant_config_from_dict_with_game_mode(self):
        """VariantConfig.from_dict should preserve game_mode."""
        config = VariantConfig.from_dict({'label': 'Test', 'game_mode': 'casual'})
        self.assertEqual(config.game_mode, 'casual')

    def test_control_config_from_dict_with_game_mode(self):
        """ControlConfig.from_dict should preserve game_mode."""
        config = ControlConfig.from_dict({'label': 'Control', 'game_mode': 'pro'})
        self.assertEqual(config.game_mode, 'pro')


class TestGameModeInVariants(unittest.TestCase):
    """Test cases for game_mode field in ExperimentConfig control and variants."""

    def test_game_mode_in_variant_config(self):
        """game_mode should be included in variant config."""
        config = ExperimentConfig(
            name='test_game_mode',
            model='gpt-5-nano',
            provider='openai',
            control={'label': 'Control', 'game_mode': 'casual'},
            variants=[{'label': 'Pro Mode', 'game_mode': 'pro'}],
        )

        variants = config.get_variant_configs()

        self.assertEqual(len(variants), 2)
        # Control
        label, control_config = variants[0]
        self.assertEqual(control_config['game_mode'], 'casual')
        # Variant
        label, variant_config = variants[1]
        self.assertEqual(variant_config['game_mode'], 'pro')

    def test_game_mode_inheritance_from_control(self):
        """Variant should inherit game_mode from control if not set."""
        config = ExperimentConfig(
            name='test_inheritance',
            model='gpt-5-nano',
            provider='openai',
            control={'label': 'Control', 'game_mode': 'standard'},
            variants=[{'label': 'Variant'}],  # No game_mode
        )

        variants = config.get_variant_configs()

        label, variant_config = variants[1]
        self.assertEqual(variant_config['game_mode'], 'standard')

    def test_game_mode_variant_overrides_control(self):
        """Variant game_mode should override control game_mode."""
        config = ExperimentConfig(
            name='test_override',
            model='gpt-5-nano',
            provider='openai',
            control={'label': 'Control', 'game_mode': 'casual'},
            variants=[{'label': 'Pro Mode', 'game_mode': 'pro'}],
        )

        variants = config.get_variant_configs()

        # Control stays casual
        label, control_config = variants[0]
        self.assertEqual(control_config['game_mode'], 'casual')
        # Variant is pro
        label, variant_config = variants[1]
        self.assertEqual(variant_config['game_mode'], 'pro')

    def test_game_mode_none_when_not_specified(self):
        """game_mode should be None when not specified anywhere."""
        config = ExperimentConfig(
            name='test_no_mode',
            model='gpt-5-nano',
            provider='openai',
            control={'label': 'Control'},  # No game_mode
            variants=[{'label': 'Variant'}],  # No game_mode
        )

        variants = config.get_variant_configs()

        label, control_config = variants[0]
        self.assertIsNone(control_config['game_mode'])
        label, variant_config = variants[1]
        self.assertIsNone(variant_config['game_mode'])

    def test_invalid_game_mode_in_control_raises(self):
        """Invalid game_mode in control should raise ValueError."""
        with self.assertRaises(ValueError) as context:
            ExperimentConfig(
                name='test_invalid',
                control={'label': 'Control', 'game_mode': 'invalid_mode'},
            )

        self.assertIn('Invalid control game_mode', str(context.exception))

    def test_invalid_game_mode_in_variant_raises(self):
        """Invalid game_mode in variant should raise ValueError."""
        with self.assertRaises(ValueError) as context:
            ExperimentConfig(
                name='test_invalid',
                control={'label': 'Control'},
                variants=[{'label': 'V1', 'game_mode': 'ultra_pro'}],
            )

        self.assertIn('Invalid variants[0] game_mode', str(context.exception))

    def test_valid_game_modes_accepted(self):
        """Valid game modes (casual, standard, pro, competitive) should be accepted."""
        # Should not raise
        config = ExperimentConfig(
            name='test_valid',
            control={'label': 'Control', 'game_mode': 'casual'},
            variants=[
                {'label': 'Standard', 'game_mode': 'standard'},
                {'label': 'Pro', 'game_mode': 'pro'},
                {'label': 'Competitive', 'game_mode': 'competitive'},
            ],
        )

        variants = config.get_variant_configs()
        self.assertEqual(len(variants), 4)  # control + 3 variants


class TestTournamentResultVariant(unittest.TestCase):
    """Test cases for TournamentResult with variant field."""

    def test_tournament_result_default_variant(self):
        """Test TournamentResult has None variant by default."""
        result = TournamentResult(
            experiment_name='test',
            tournament_id='test_1',
            start_time='2024-01-01T00:00:00',
            end_time='2024-01-01T01:00:00',
            duration_seconds=3600,
            hands_played=50,
            winner='Batman',
            final_standings=[{'name': 'Batman', 'stack': 40000}],
            elimination_order=['Robin', 'Joker'],
            model_config={'provider': 'openai', 'model': 'gpt-4o'},
            total_api_calls=200,
            total_cost=0.5,
            avg_latency_ms=150,
            decision_stats={},
        )

        self.assertIsNone(result.variant)

    def test_tournament_result_with_variant(self):
        """Test TournamentResult with variant label."""
        result = TournamentResult(
            experiment_name='model_comparison',
            tournament_id='test_1',
            start_time='2024-01-01T00:00:00',
            end_time='2024-01-01T01:00:00',
            duration_seconds=3600,
            hands_played=50,
            winner='Batman',
            final_standings=[{'name': 'Batman', 'stack': 40000}],
            elimination_order=['Robin', 'Joker'],
            model_config={'provider': 'anthropic', 'model': 'claude-sonnet-4-20250514'},
            total_api_calls=200,
            total_cost=0.5,
            avg_latency_ms=150,
            decision_stats={},
            variant='Claude Sonnet',
        )

        self.assertEqual(result.variant, 'Claude Sonnet')


class TestExperimentRoutesVariantValidation(unittest.TestCase):
    """Test cases for experiment route variant validation."""

    def setUp(self):
        """Create test Flask app."""
        from flask_app import create_app
        from poker.persistence import GamePersistence

        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.persistence = GamePersistence(self.test_db.name)

        self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()

    def tearDown(self):
        """Clean up temporary database."""
        os.unlink(self.test_db.name)

    def test_validate_config_with_valid_control_variants(self):
        """Test validation passes with valid control/variants structure."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {
                    'name': 'ab_test',
                    'num_tournaments': 3,
                    'control': {
                        'label': 'GPT Control',
                        'model': 'gpt-4o',
                        'provider': 'openai',
                    },
                    'variants': [
                        {
                            'label': 'Claude Variant',
                            'model': 'claude-sonnet-4-20250514',
                            'provider': 'anthropic',
                        }
                    ]
                }}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data['valid'])
            self.assertEqual(data['errors'], [])

    def test_validate_config_control_missing_label(self):
        """Test validation fails when control is missing label."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {
                    'name': 'ab_test',
                    'control': {
                        'model': 'gpt-4o',
                        # Missing label
                    },
                }}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertFalse(data['valid'])
            self.assertIn('control.label is required', data['errors'])

    def test_validate_config_variant_missing_label(self):
        """Test validation fails when variant is missing label."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {
                    'name': 'ab_test',
                    'control': {'label': 'Control'},
                    'variants': [
                        {'model': 'claude-sonnet-4-20250514'}  # Missing label
                    ]
                }}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertFalse(data['valid'])
            self.assertIn('variants[0].label is required', data['errors'])

    def test_validate_config_duplicate_variant_labels(self):
        """Test validation fails with duplicate variant labels."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {
                    'name': 'ab_test',
                    'control': {'label': 'Control'},
                    'variants': [
                        {'label': 'Same Label'},
                        {'label': 'Same Label'},  # Duplicate
                    ]
                }}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertFalse(data['valid'])
            self.assertTrue(any('Duplicate variant label' in e for e in data['errors']))

    def test_validate_config_variant_label_matches_control(self):
        """Test validation fails when variant label matches control label."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {
                    'name': 'ab_test',
                    'control': {'label': 'Same Label'},
                    'variants': [
                        {'label': 'Same Label'},  # Matches control
                    ]
                }}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertFalse(data['valid'])
            self.assertTrue(any('cannot match control label' in e for e in data['errors']))

    def test_validate_config_warns_total_tournaments_exceeds_20(self):
        """Test validation warns when total tournaments exceeds 20."""
        with patch('flask_app.routes.experiment_routes.persistence', self.persistence):
            response = self.client.post(
                '/api/experiments/validate',
                json={'config': {
                    'name': 'large_ab_test',
                    'num_tournaments': 10,
                    'control': {'label': 'Control'},
                    'variants': [
                        {'label': 'Variant 1'},
                        {'label': 'Variant 2'},
                    ]
                }}
            )
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(data['valid'])
            # 10 × 3 = 30 tournaments
            self.assertTrue(any('30' in w and 'exceeds 20' in w for w in data['warnings']))


if __name__ == '__main__':
    unittest.main()
