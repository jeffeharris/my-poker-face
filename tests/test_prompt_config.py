"""
Tests for the PromptConfig toggleable prompt components system.
"""
import unittest
from poker.prompt_config import PromptConfig


class TestPromptConfig(unittest.TestCase):
    """Tests for PromptConfig dataclass."""

    def test_default_all_enabled(self):
        """All components should be enabled by default."""
        config = PromptConfig()
        self.assertTrue(config.pot_odds)
        self.assertTrue(config.hand_strength)
        self.assertTrue(config.session_memory)
        self.assertTrue(config.opponent_intel)
        self.assertTrue(config.strategic_reflection)
        self.assertEqual(config.memory_keep_exchanges, 0)
        self.assertTrue(config.chattiness)
        self.assertTrue(config.emotional_state)
        self.assertTrue(config.tilt_effects)
        self.assertTrue(config.mind_games)
        self.assertTrue(config.dramatic_sequence)
        self.assertTrue(config.situational_guidance)

    def test_to_dict(self):
        """to_dict should serialize all fields."""
        config = PromptConfig()
        d = config.to_dict()

        self.assertEqual(len(d), 23)  # 21 bool + 1 int + 1 str
        self.assertIn('pot_odds', d)
        self.assertIn('mind_games', d)
        self.assertIn('dramatic_sequence', d)
        self.assertIn('strategic_reflection', d)
        self.assertIn('memory_keep_exchanges', d)
        self.assertIn('situational_guidance', d)
        self.assertIn('guidance_injection', d)
        self.assertEqual(d['memory_keep_exchanges'], 0)
        self.assertEqual(d['guidance_injection'], "")

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
        self.assertTrue(config.dramatic_sequence)  # Default

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
        self.assertFalse(disabled.dramatic_sequence)
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
        # Must explicitly set fields that default to False
        config = PromptConfig(
            gto_equity=True,
            gto_verdict=True,
            use_simple_response_format=True,
            lean_bounded=True,
        )
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
            include_dramatic_sequence=True
        )

        self.assertIn("Test message", result)
        self.assertIn("MIND GAMES", result)
        self.assertIn("DRAMATIC SEQUENCE", result)

    def test_render_decision_prompt_mind_games_disabled(self):
        """render_decision_prompt should exclude MIND GAMES when disabled."""
        from poker.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.render_decision_prompt(
            message="Test message",
            include_mind_games=False,
            include_dramatic_sequence=True
        )

        self.assertIn("Test message", result)
        self.assertNotIn("MIND GAMES", result)
        self.assertIn("DRAMATIC SEQUENCE", result)

    def test_render_decision_prompt_dramatic_sequence_disabled(self):
        """render_decision_prompt should exclude DRAMATIC SEQUENCE when disabled."""
        from poker.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.render_decision_prompt(
            message="Test message",
            include_mind_games=True,
            include_dramatic_sequence=False
        )

        self.assertIn("Test message", result)
        self.assertIn("MIND GAMES", result)
        self.assertNotIn("DRAMATIC SEQUENCE", result)

    def test_render_decision_prompt_both_disabled(self):
        """render_decision_prompt should exclude both when disabled."""
        from poker.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.render_decision_prompt(
            message="Test message",
            include_mind_games=False,
            include_dramatic_sequence=False
        )

        self.assertIn("Test message", result)
        self.assertNotIn("MIND GAMES", result)
        self.assertNotIn("DRAMATIC SEQUENCE", result)
        # Should still have the base instruction
        self.assertIn("CRITICAL", result)


class TestGameModes(unittest.TestCase):
    """Tests for game mode factory methods."""

    def test_casual_mode(self):
        """Casual mode should use defaults (no equity shown)."""
        config = PromptConfig.casual()
        self.assertFalse(config.gto_equity)
        self.assertFalse(config.gto_verdict)
        self.assertTrue(config.chattiness)
        self.assertTrue(config.dramatic_sequence)

    def test_standard_mode(self):
        """Standard mode should show equity but not verdict."""
        config = PromptConfig.standard()
        self.assertTrue(config.gto_equity)
        self.assertFalse(config.gto_verdict)
        self.assertTrue(config.chattiness)
        self.assertTrue(config.dramatic_sequence)

    def test_pro_mode(self):
        """Pro mode should show equity + verdict, disable chattiness, dramatic sequence, and tilt."""
        config = PromptConfig.pro()
        self.assertTrue(config.gto_equity)
        self.assertTrue(config.gto_verdict)
        self.assertFalse(config.chattiness)
        self.assertFalse(config.dramatic_sequence)
        # Phase 9: Pro mode AIs don't tilt (harder opponents)
        self.assertTrue(config.zone_benefits)   # Still get sweet spot guidance
        self.assertFalse(config.tilt_effects)   # No tilting - harder opponents

    def test_from_mode_name_valid(self):
        """from_mode_name should resolve valid mode names."""
        casual = PromptConfig.from_mode_name('casual')
        self.assertEqual(casual.to_dict(), PromptConfig.casual().to_dict())

        standard = PromptConfig.from_mode_name('standard')
        self.assertEqual(standard.to_dict(), PromptConfig.standard().to_dict())

        pro = PromptConfig.from_mode_name('pro')
        self.assertEqual(pro.to_dict(), PromptConfig.pro().to_dict())

    def test_from_mode_name_case_insensitive(self):
        """from_mode_name should be case insensitive."""
        self.assertEqual(
            PromptConfig.from_mode_name('STANDARD').to_dict(),
            PromptConfig.standard().to_dict()
        )
        self.assertEqual(
            PromptConfig.from_mode_name('Pro').to_dict(),
            PromptConfig.pro().to_dict()
        )

    def test_from_mode_name_invalid(self):
        """from_mode_name should raise ValueError for invalid modes."""
        with self.assertRaises(ValueError) as context:
            PromptConfig.from_mode_name('invalid')
        self.assertIn('Invalid game mode', str(context.exception))
        self.assertIn('invalid', str(context.exception))

    def test_casual_is_default(self):
        """Casual mode should equal default PromptConfig."""
        casual = PromptConfig.casual()
        default = PromptConfig()
        self.assertEqual(casual.to_dict(), default.to_dict())


class TestZoneToggles(unittest.TestCase):
    """Tests for Phase 9 zone toggles across game modes."""

    def test_default_zone_toggles_enabled(self):
        """Default config has both zone toggles True."""
        config = PromptConfig()
        self.assertTrue(config.zone_benefits)
        self.assertTrue(config.tilt_effects)

    def test_casual_mode_full_psychology(self):
        """Casual mode enables all psychology features."""
        config = PromptConfig.casual()
        self.assertTrue(config.zone_benefits)
        self.assertTrue(config.tilt_effects)

    def test_standard_mode_full_psychology(self):
        """Standard mode enables all psychology features."""
        config = PromptConfig.standard()
        self.assertTrue(config.zone_benefits)
        self.assertTrue(config.tilt_effects)

    def test_pro_mode_no_tilt(self):
        """Pro mode disables tilt_effects (harder AIs)."""
        config = PromptConfig.pro()
        self.assertTrue(config.zone_benefits)   # Still get sweet spot guidance
        self.assertFalse(config.tilt_effects)   # No tilting - harder opponents

    def test_competitive_mode_full_psychology(self):
        """Competitive mode keeps full psychology (zone tuning deferred to Phase 10)."""
        config = PromptConfig.competitive()
        self.assertTrue(config.zone_benefits)
        self.assertTrue(config.tilt_effects)

    def test_from_dict_backward_compat(self):
        """Old saved games without zone toggles default to True."""
        old_data = {'pot_odds': True}  # No zone fields
        config = PromptConfig.from_dict(old_data)
        self.assertTrue(config.zone_benefits)
        self.assertTrue(config.tilt_effects)

    def test_to_dict_includes_zone_toggles(self):
        """Serialization includes zone toggles."""
        config = PromptConfig(tilt_effects=False)
        data = config.to_dict()
        self.assertIn('zone_benefits', data)
        self.assertIn('tilt_effects', data)
        self.assertFalse(data['tilt_effects'])


class TestTrueLeanPrompt(unittest.TestCase):
    """Verify that the lean prompt config produces a truly clean prompt with no noise."""

    def test_render_decision_prompt_no_noise(self):
        """Lean config should produce a prompt with NONE of the character/drama sections."""
        from poker.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.render_decision_prompt(
            message="Test game state message",
            include_mind_games=False,
            include_dramatic_sequence=False,
            include_betting_discipline=False,
            pot_committed_info=None,
            short_stack_info=None,
            made_hand_info=None,
            equity_verdict_info=None,
            drama_context=None,
            include_pot_odds=True,
            pot_odds_info=None,  # No free check noise
            use_simple_response_format=True,
            expression_guidance=None,
            zone_guidance=None,
        )

        # Should contain the game state and base instruction
        self.assertIn("Test game state message", result)
        self.assertIn("CRITICAL", result)

        # Should NOT contain any character/drama noise
        noise_strings = [
            'MIND GAMES',
            'DRAMATIC SEQUENCE',
            'BETTING DISCIPLINE',
            'RESPONSE STYLE',
            'POKER FACE MODE',
            'EMOTIONAL STATE',
            'bet_sizing',
            'check for free',
        ]
        for noise in noise_strings:
            self.assertNotIn(noise, result, f"Lean prompt should not contain '{noise}'")

    def test_simple_response_format_no_bet_sizing(self):
        """Simple response format should not ask for bet_sizing field."""
        from poker.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.render_decision_prompt(
            message="Test message",
            include_mind_games=False,
            include_dramatic_sequence=False,
            use_simple_response_format=True,
        )

        self.assertNotIn('bet_sizing', result)
        self.assertIn('action', result)
        self.assertIn('raise_to', result)

    def test_simple_response_format_suppresses_drama_context(self):
        """Even if drama_context is passed, simple format should not include it."""
        from poker.prompt_manager import PromptManager

        # Note: the suppression happens in controllers.py _build_decision_prompt(),
        # which sets drama_context=None before calling render. We verify that
        # when drama_context IS None, no RESPONSE STYLE text appears.
        pm = PromptManager()
        result = pm.render_decision_prompt(
            message="Test message",
            include_mind_games=False,
            include_dramatic_sequence=False,
            use_simple_response_format=True,
            drama_context=None,
        )
        self.assertNotIn('RESPONSE STYLE', result)

    def test_build_base_game_state_no_persona(self):
        """build_base_game_state with include_persona=False should omit persona name."""
        from unittest.mock import MagicMock
        from poker.controllers import build_base_game_state

        # Create minimal mock game state
        player = MagicMock()
        player.name = "Napoleon"
        player.stack = 1500
        player.hand = [{'rank': 'A', 'suit': 'Hearts'}, {'rank': 'K', 'suit': 'Spades'}]
        player.bet = 50
        player.is_folded = False
        player.is_all_in = False

        game_state = MagicMock()
        game_state.current_player = player
        game_state.players = [player]
        game_state.community_cards = []
        game_state.pot = {'total': 150}
        game_state.highest_bet = 100
        game_state.current_ante = 50
        game_state.table_positions = {'BTN': 'Napoleon'}
        game_state.current_player_options = ['fold', 'call', 'raise']
        game_state.min_raise_amount = 100

        result = build_base_game_state(
            game_state, player, 'PRE_FLOP', 'Recent actions...',
            include_hand_strength=False,
            include_persona=False,
        )

        self.assertNotIn('Persona:', result)
        self.assertNotIn('What is your move, Napoleon', result)
        self.assertIn('What is your move?', result)

    def test_build_base_game_state_with_persona(self):
        """build_base_game_state with include_persona=True (default) should include persona name."""
        from unittest.mock import MagicMock
        from poker.controllers import build_base_game_state

        player = MagicMock()
        player.name = "Napoleon"
        player.stack = 1500
        player.hand = [{'rank': 'A', 'suit': 'Hearts'}, {'rank': 'K', 'suit': 'Spades'}]
        player.bet = 50
        player.is_folded = False
        player.is_all_in = False

        game_state = MagicMock()
        game_state.current_player = player
        game_state.players = [player]
        game_state.community_cards = []
        game_state.pot = {'total': 150}
        game_state.highest_bet = 100
        game_state.current_ante = 50
        game_state.table_positions = {'BTN': 'Napoleon'}
        game_state.current_player_options = ['fold', 'call', 'raise']
        game_state.min_raise_amount = 100

        result = build_base_game_state(
            game_state, player, 'PRE_FLOP', 'Recent actions...',
            include_hand_strength=False,
        )

        self.assertIn('Persona: Napoleon', result)
        self.assertIn('What is your move, Napoleon', result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
