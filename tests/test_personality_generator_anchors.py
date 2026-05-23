"""Tests for the anchors block in PersonalityGenerator.

Before 2026-05-22 the generator didn't emit anchors, which collapsed every
AI-generated personality to default-TAG at runtime (`PlayerPsychology.from_
personality_config` falls back to `looseness=0.30, aggression=0.50`).

These tests cover:
- _create_default_personality() includes anchors
- _generate_personality() backfills anchors when the LLM omits them
- GENERATION_PROMPT has named examples for all 7 archetypes (guards
  against the original LAG-bias where only "aggressive and unpredictable"
  was shown to the LLM)
"""

from unittest.mock import MagicMock, patch

import pytest

from poker.personality_generator import (
    PersonalityGenerator,
    _default_anchors,
)
from poker.psychology_model import PersonalityAnchors


_ANCHOR_KEYS = frozenset({
    'baseline_aggression', 'baseline_looseness', 'ego', 'poise',
    'expressiveness', 'risk_identity', 'adaptation_bias',
    'baseline_energy', 'recovery_rate',
})


def _make_generator():
    # Bypass __init__ so we don't touch the DB or real LLM client.
    gen = PersonalityGenerator.__new__(PersonalityGenerator)
    gen._client = MagicMock()
    gen.personality_repo = MagicMock()
    gen._cache = {}
    return gen


class TestDefaultAnchorsHelper:
    def test_default_anchors_has_all_keys(self):
        defaults = _default_anchors()
        assert set(defaults.keys()) == _ANCHOR_KEYS

    def test_default_anchors_pass_personality_anchors_validation(self):
        # PersonalityAnchors.__post_init__ enforces [0,1] range
        PersonalityAnchors.from_dict(_default_anchors())

    def test_defaults_collapse_to_tag(self):
        # The defaults must match runtime fallback (looseness=0.30, aggression=0.50)
        # so a personality with no anchors loaded twice produces the same archetype
        # whether it falls through the generator path or the PlayerPsychology fallback.
        defaults = _default_anchors()
        assert defaults['baseline_looseness'] == 0.30
        assert defaults['baseline_aggression'] == 0.50


class TestCreateDefaultPersonalityIncludesAnchors:
    def test_default_personality_has_anchors_block(self):
        gen = _make_generator()
        result = gen._create_default_personality('Test Char')
        assert 'anchors' in result
        assert set(result['anchors'].keys()) == _ANCHOR_KEYS


class TestGeneratePersonalityBackfillsAnchors:
    def _llm_response(self, content: dict):
        # Mimic LLMClient.complete() shape (an object with `.content` attr)
        resp = MagicMock()
        import json
        resp.content = json.dumps(content)
        return resp

    def test_anchors_present_when_llm_provides_them(self):
        gen = _make_generator()
        gen._client.complete.return_value = self._llm_response({
            'play_style': 'tight and selective',
            'default_confidence': 'steady',
            'default_attitude': 'calm',
            'personality_traits': {'bluff_tendency': 0.2, 'aggression': 0.4,
                                   'chattiness': 0.3, 'emoji_usage': 0.1},
            'anchors': {
                'baseline_aggression': 0.40, 'baseline_looseness': 0.22,
                'ego': 0.36, 'poise': 0.78, 'expressiveness': 0.47,
                'risk_identity': 0.38, 'adaptation_bias': 0.50,
                'baseline_energy': 0.52, 'recovery_rate': 0.17,
            },
        })
        result = gen._generate_personality('Abraham Lincoln')
        assert result['anchors']['baseline_looseness'] == 0.22

    def test_anchors_backfilled_when_llm_omits_block(self):
        gen = _make_generator()
        gen._client.complete.return_value = self._llm_response({
            'play_style': 'aggressive and theatrical',
            'default_confidence': 'overconfident',
            'default_attitude': 'intimidating',
            'personality_traits': {'bluff_tendency': 0.75, 'aggression': 0.8,
                                   'chattiness': 0.4, 'emoji_usage': 0.2},
            # ← no anchors block
        })
        result = gen._generate_personality('Hulk Hogan')
        assert 'anchors' in result
        assert set(result['anchors'].keys()) == _ANCHOR_KEYS
        # Should match the fallback defaults verbatim
        assert result['anchors'] == _default_anchors()

    def test_anchors_backfilled_when_llm_returns_garbage_for_anchors(self):
        # The defensive `isinstance(existing_anchors, dict)` check exists
        # because some LLMs return null/string/list for unfamiliar fields.
        gen = _make_generator()
        gen._client.complete.return_value = self._llm_response({
            'play_style': 'balanced',
            'default_confidence': 'steady',
            'default_attitude': 'friendly',
            'personality_traits': {'bluff_tendency': 0.5, 'aggression': 0.5,
                                   'chattiness': 0.5, 'emoji_usage': 0.3},
            'anchors': None,
        })
        result = gen._generate_personality('Test')
        assert result['anchors'] == _default_anchors()


class TestGenerationPromptArchetypeCoverage:
    """Backfill audit (2026-05-22) found AI-gen pool was 42% LAG and 0%
    CallingStation/Nit because GENERATION_PROMPT only showed one play_style
    example ('aggressive and unpredictable') and no archetype-anchored
    examples. The prompt was extended with a 7-archetype reference block.
    These tests guard that all 7 stay represented if the prompt is edited.
    """

    def _prompt(self) -> str:
        return PersonalityGenerator.GENERATION_PROMPT

    def test_all_seven_archetypes_have_named_examples(self):
        p = self._prompt()
        # Each archetype label appears with at least one named character.
        for archetype in ('Nit', 'Rock', 'TAG', 'Balanced', 'LAG',
                          'CallingStation', 'Maniac'):
            assert archetype in p, f"archetype {archetype!r} missing from generation prompt"

    def test_extreme_archetypes_have_concrete_anchor_values(self):
        p = self._prompt()
        # Anchor reference numbers for the under-represented extremes
        # (the bias case) must remain in the prompt.
        for marker in ('baseline_looseness ≈ 0.18', 'baseline_looseness ≈ 0.78',
                       'baseline_looseness ≈ 0.88'):
            assert marker in p, f"missing anchor target: {marker}"

    def test_prompt_warns_against_lag_default(self):
        p = self._prompt()
        # The behavioral guidance against defaulting to LAG must persist.
        assert "uniformly-LAG pool" in p or "default to LAG" in p.lower()
