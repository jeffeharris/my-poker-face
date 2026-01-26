"""
Tests for the PromptConfig toggleable prompt components system.
"""
import unittest
from poker.prompt_config import PromptConfig


class TestPromptConfig(unittest.TestCase):
    """Tests for PromptConfig dataclass."""

    def test_default_all_enabled(self):
        """All components should be enabled by default (except bb_normalized)."""
        config = PromptConfig()
        self.assertTrue(config.pot_odds)
        self.assertTrue(config.hand_strength)
        self.assertFalse(config.bb_normalized)  # Defaults to False (opt-in feature)
        self.assertTrue(config.session_memory)
        self.assertTrue(config.opponent_intel)
        self.assertTrue(config.strategic_reflection)
        self.assertEqual(config.memory_keep_exchanges, 0)
        self.assertTrue(config.chattiness)
        self.assertTrue(config.emotional_state)
        self.assertTrue(config.tilt_effects)
        self.assertTrue(config.mind_games)
        self.assertTrue(config.persona_response)
        self.assertTrue(config.situational_guidance)

    def test_to_dict(self):
        """to_dict should serialize all fields."""
        config = PromptConfig()
        d = config.to_dict()

        self.assertEqual(len(d), 14)  # 12 bool + 1 int + 1 str
        self.assertIn('pot_odds', d)
        self.assertIn('mind_games', d)
        self.assertIn('persona_response', d)
        self.assertIn('strategic_reflection', d)
        self.assertIn('memory_keep_exchanges', d)
        self.assertIn('situational_guidance', d)
        self.assertEqual(d['memory_keep_exchanges'], 0)

    def test_from_dict_full(self):
        """from_dict should restore from a full dict."""
        original = PromptConfig(mind_games=False, pot_odds=False)
        d = original.to_dict()
        restored = PromptConfig.from_dict(d)

        self.assertEqual(original.mind_games, restored.mind_games)
        self.assertEqual(original.pot_odds, restored.pot_odds)
        self.assertEqual(original.hand_strength, restored.hand_strength)

    def test_from_dict_partial(self):
        """from_dict should handle partial dicts with defaults."""
        d = {'mind_games': False}
        config = PromptConfig.from_dict(d)

        self.assertFalse(config.mind_games)
        self.assertTrue(config.pot_odds)  # Default
        self.assertTrue(config.persona_response)  # Default

    def test_from_dict_empty(self):
        """from_dict should handle empty dict."""
        config = PromptConfig.from_dict({})
        # Should return all defaults
        self.assertTrue(config.pot_odds)
        self.assertTrue(config.mind_games)

    def test_from_dict_none(self):
        """from_dict should handle None input."""
        config = PromptConfig.from_dict(None)
        # Should return all defaults
        self.assertTrue(config.pot_odds)
        self.assertTrue(config.mind_games)

    def test_from_dict_unknown_fields(self):
        """from_dict should ignore unknown fields."""
        d = {
            'mind_games': False,
            'unknown_field': True,
            'another_unknown': 'value'
        }
        config = PromptConfig.from_dict(d)
        self.assertFalse(config.mind_games)
        self.assertFalse(hasattr(config, 'unknown_field'))

    def test_disable_all(self):
        """disable_all should return config with all boolean components False."""
        config = PromptConfig(memory_keep_exchanges=5)
        disabled = config.disable_all()

        self.assertFalse(disabled.pot_odds)
        self.assertFalse(disabled.hand_strength)
        self.assertFalse(disabled.session_memory)
        self.assertFalse(disabled.opponent_intel)
        self.assertFalse(disabled.strategic_reflection)
        self.assertFalse(disabled.chattiness)
        self.assertFalse(disabled.emotional_state)
        self.assertFalse(disabled.tilt_effects)
        self.assertFalse(disabled.mind_games)
        self.assertFalse(disabled.persona_response)
        self.assertFalse(disabled.situational_guidance)

        # Int field should be preserved
        self.assertEqual(disabled.memory_keep_exchanges, 5)

        # Original should be unchanged
        self.assertTrue(config.pot_odds)

    def test_enable_all(self):
        """enable_all should return config with all boolean components True."""
        config = PromptConfig(mind_games=False, pot_odds=False, strategic_reflection=False,
                             situational_guidance=False, memory_keep_exchanges=3)
        enabled = config.enable_all()

        self.assertTrue(enabled.pot_odds)
        self.assertTrue(enabled.mind_games)
        self.assertTrue(enabled.strategic_reflection)
        self.assertTrue(enabled.situational_guidance)

        # Int field should be preserved
        self.assertEqual(enabled.memory_keep_exchanges, 3)

        # Original should be unchanged
        self.assertFalse(config.mind_games)
        self.assertFalse(config.situational_guidance)

    def test_strategic_reflection_and_memory_keep_exchanges(self):
        """New fields should serialize and deserialize correctly."""
        config = PromptConfig(strategic_reflection=False, memory_keep_exchanges=10)
        d = config.to_dict()

        self.assertFalse(d['strategic_reflection'])
        self.assertEqual(d['memory_keep_exchanges'], 10)

        restored = PromptConfig.from_dict(d)
        self.assertFalse(restored.strategic_reflection)
        self.assertEqual(restored.memory_keep_exchanges, 10)

    def test_situational_guidance_serialization(self):
        """situational_guidance should serialize and deserialize correctly."""
        config = PromptConfig(situational_guidance=False)
        d = config.to_dict()

        self.assertFalse(d['situational_guidance'])

        restored = PromptConfig.from_dict(d)
        self.assertFalse(restored.situational_guidance)

        # Partial dict should use default (True)
        partial = PromptConfig.from_dict({'pot_odds': False})
        self.assertTrue(partial.situational_guidance)

    def test_copy_with_overrides(self):
        """copy should create a new config with overrides."""
        config = PromptConfig()
        copied = config.copy(mind_games=False, pot_odds=False)

        self.assertFalse(copied.mind_games)
        self.assertFalse(copied.pot_odds)
        self.assertTrue(copied.hand_strength)

        # Original should be unchanged
        self.assertTrue(config.mind_games)

    def test_repr_all_enabled(self):
        """repr should show 'all enabled' when all booleans are True."""
        # Default PromptConfig has bb_normalized=False, so use enable_all()
        config = PromptConfig().enable_all()
        self.assertIn('all enabled', repr(config))

    def test_repr_some_disabled(self):
        """repr should show disabled components."""
        config = PromptConfig(mind_games=False, pot_odds=False)
        r = repr(config)
        self.assertIn('disabled', r)
        self.assertIn('mind_games', r)
        self.assertIn('pot_odds', r)

    def test_roundtrip_serialization(self):
        """Config should survive serialization roundtrip."""
        original = PromptConfig(
            pot_odds=False,
            mind_games=False,
            emotional_state=False
        )
        serialized = original.to_dict()
        restored = PromptConfig.from_dict(serialized)

        self.assertEqual(original.to_dict(), restored.to_dict())


class TestPromptConfigIntegration(unittest.TestCase):
    """Integration tests for PromptConfig with other components."""

    def test_render_decision_prompt_all_enabled(self):
        """render_decision_prompt should include all sections when enabled."""
        from poker.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.render_decision_prompt(
            message="Test message",
            include_mind_games=True,
            include_persona_response=True
        )

        self.assertIn("Test message", result)
        self.assertIn("MIND GAMES", result)
        self.assertIn("PERSONA RESPONSE", result)

    def test_render_decision_prompt_mind_games_disabled(self):
        """render_decision_prompt should exclude MIND GAMES when disabled."""
        from poker.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.render_decision_prompt(
            message="Test message",
            include_mind_games=False,
            include_persona_response=True
        )

        self.assertIn("Test message", result)
        self.assertNotIn("MIND GAMES", result)
        self.assertIn("PERSONA RESPONSE", result)

    def test_render_decision_prompt_persona_disabled(self):
        """render_decision_prompt should exclude PERSONA RESPONSE when disabled."""
        from poker.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.render_decision_prompt(
            message="Test message",
            include_mind_games=True,
            include_persona_response=False
        )

        self.assertIn("Test message", result)
        self.assertIn("MIND GAMES", result)
        self.assertNotIn("PERSONA RESPONSE", result)

    def test_render_decision_prompt_both_disabled(self):
        """render_decision_prompt should exclude both when disabled."""
        from poker.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.render_decision_prompt(
            message="Test message",
            include_mind_games=False,
            include_persona_response=False
        )

        self.assertIn("Test message", result)
        self.assertNotIn("MIND GAMES", result)
        self.assertNotIn("PERSONA RESPONSE", result)
        # Should still have the base instruction
        self.assertIn("CRITICAL", result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
