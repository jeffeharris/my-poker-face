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
    _sanitize_spot_tendencies,
)
from poker.psychology_model import PersonalityAnchors
from poker.strategy.spot_tendencies import REGISTERED_SPOT_TENDENCIES

_ANCHOR_KEYS = frozenset(
    {
        'baseline_aggression',
        'baseline_looseness',
        'ego',
        'poise',
        'expressiveness',
        'risk_identity',
        'adaptation_bias',
        'baseline_energy',
        'recovery_rate',
        'self_belief',
    }
)


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
        gen._client.complete.return_value = self._llm_response(
            {
                'play_style': 'tight and selective',
                'default_confidence': 'steady',
                'default_attitude': 'calm',
                'personality_traits': {
                    'bluff_tendency': 0.2,
                    'aggression': 0.4,
                    'chattiness': 0.3,
                    'emoji_usage': 0.1,
                },
                'anchors': {
                    'baseline_aggression': 0.40,
                    'baseline_looseness': 0.22,
                    'ego': 0.36,
                    'poise': 0.78,
                    'expressiveness': 0.47,
                    'risk_identity': 0.38,
                    'adaptation_bias': 0.50,
                    'baseline_energy': 0.52,
                    'recovery_rate': 0.17,
                },
            }
        )
        result = gen._generate_personality('Abraham Lincoln')
        assert result['anchors']['baseline_looseness'] == 0.22

    def test_anchors_backfilled_when_llm_omits_block(self):
        gen = _make_generator()
        gen._client.complete.return_value = self._llm_response(
            {
                'play_style': 'aggressive and theatrical',
                'default_confidence': 'overconfident',
                'default_attitude': 'intimidating',
                'personality_traits': {
                    'bluff_tendency': 0.75,
                    'aggression': 0.8,
                    'chattiness': 0.4,
                    'emoji_usage': 0.2,
                },
                # ← no anchors block
            }
        )
        result = gen._generate_personality('Hulk Hogan')
        assert 'anchors' in result
        assert set(result['anchors'].keys()) == _ANCHOR_KEYS
        # Should match the fallback defaults verbatim
        assert result['anchors'] == _default_anchors()

    def test_anchors_backfilled_when_llm_returns_garbage_for_anchors(self):
        # The defensive `isinstance(existing_anchors, dict)` check exists
        # because some LLMs return null/string/list for unfamiliar fields.
        gen = _make_generator()
        gen._client.complete.return_value = self._llm_response(
            {
                'play_style': 'balanced',
                'default_confidence': 'steady',
                'default_attitude': 'friendly',
                'personality_traits': {
                    'bluff_tendency': 0.5,
                    'aggression': 0.5,
                    'chattiness': 0.5,
                    'emoji_usage': 0.3,
                },
                'anchors': None,
            }
        )
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
        for archetype in ('Nit', 'Rock', 'TAG', 'Balanced', 'LAG', 'CallingStation', 'Maniac'):
            assert archetype in p, f"archetype {archetype!r} missing from generation prompt"

    def test_extreme_archetypes_have_concrete_anchor_values(self):
        p = self._prompt()
        # Anchor reference numbers for the under-represented extremes
        # (the bias case) must remain in the prompt.
        for marker in (
            'baseline_looseness ≈ 0.18',
            'baseline_looseness ≈ 0.78',
            'baseline_looseness ≈ 0.88',
        ):
            assert marker in p, f"missing anchor target: {marker}"

    def test_prompt_warns_against_lag_default(self):
        p = self._prompt()
        # The behavioral guidance against defaulting to LAG must persist.
        assert "uniformly-LAG pool" in p or "default to LAG" in p.lower()


class TestSelfBeliefAnchor:
    """self_belief (the bravado/delusion dial, decoupled from ego) is the
    newest anchor. The generator must emit it and tolerate older LLM responses
    that omit it from an otherwise-complete anchors block.
    """

    def _llm_response(self, content: dict):
        resp = MagicMock()
        import json

        resp.content = json.dumps(content)
        return resp

    def test_default_anchors_includes_self_belief(self):
        assert _default_anchors()['self_belief'] == 0.50

    def test_self_belief_backfilled_when_llm_omits_it_from_anchors(self):
        gen = _make_generator()
        anchors_without_self_belief = {
            'baseline_aggression': 0.75,
            'baseline_looseness': 0.72,
            'ego': 0.80,
            'poise': 0.40,
            'expressiveness': 0.80,
            'risk_identity': 0.85,
            'adaptation_bias': 0.50,
            'baseline_energy': 0.85,
            'recovery_rate': 0.20,
        }
        gen._client.complete.return_value = self._llm_response(
            {
                'play_style': 'bold and unpredictable',
                'default_confidence': 'overconfident',
                'default_attitude': 'brash',
                'personality_traits': {
                    'bluff_tendency': 0.6,
                    'aggression': 0.8,
                    'emoji_usage': 0.3,
                },
                'anchors': anchors_without_self_belief,
            }
        )
        result = gen._generate_personality('Cleopatra')
        # Provided anchors preserved, self_belief added with the default.
        assert result['anchors']['baseline_aggression'] == 0.75
        assert result['anchors']['self_belief'] == 0.50

    def test_self_belief_passes_personality_anchors_validation(self):
        PersonalityAnchors.from_dict(_default_anchors())


class TestSanitizeSpotTendencies:
    def test_none_and_non_list_yield_empty(self):
        assert _sanitize_spot_tendencies(None, 'X') == []
        assert _sanitize_spot_tendencies('sticky', 'X') == []
        assert _sanitize_spot_tendencies({'sticky': 0.5}, 'X') == []

    def test_valid_pairs_kept_and_clamped(self):
        out = _sanitize_spot_tendencies([['sticky', 0.6], ['over_bluff', 1.4]], 'X')
        assert out == [['sticky', 0.6], ['over_bluff', 1.0]]

    def test_unknown_names_dropped(self):
        out = _sanitize_spot_tendencies([['slow_roll', 0.9], ['sticky', 0.5]], 'X')
        assert out == [['sticky', 0.5]]

    def test_every_kept_name_is_registered(self):
        catalog = [[name, 0.5] for name in REGISTERED_SPOT_TENDENCIES]
        out = _sanitize_spot_tendencies(catalog, 'X')
        assert {name for name, _ in out} <= REGISTERED_SPOT_TENDENCIES

    def test_dedupe_first_wins(self):
        out = _sanitize_spot_tendencies([['sticky', 0.5], ['sticky', 0.9]], 'X')
        assert out == [['sticky', 0.5]]

    def test_capped_at_three(self):
        raw = [
            ['sticky', 0.5],
            ['over_bluff', 0.5],
            ['under_bluff', 0.5],
            ['slowplay', 0.5],
        ]
        assert len(_sanitize_spot_tendencies(raw, 'X')) == 3

    def test_malformed_pairs_skipped(self):
        out = _sanitize_spot_tendencies(
            [['sticky'], ['over_bluff', 'loud'], ['slowplay', 0.4]], 'X'
        )
        assert out == [['slowplay', 0.4]]


class TestGeneratePersonalityEmitsSpotTendencies:
    def _llm_response(self, content: dict):
        resp = MagicMock()
        import json

        resp.content = json.dumps(content)
        return resp

    def _base(self, **extra):
        body = {
            'play_style': 'sticky calling station',
            'default_confidence': 'steady',
            'default_attitude': 'curious',
            'personality_traits': {'bluff_tendency': 0.1, 'aggression': 0.25, 'emoji_usage': 0.2},
        }
        body.update(extra)
        return body

    def test_spot_tendencies_always_present_even_when_absent(self):
        gen = _make_generator()
        gen._client.complete.return_value = self._llm_response(self._base())
        result = gen._generate_personality('Alice')
        assert result['spot_tendencies'] == []

    def test_spot_tendencies_sanitized_from_llm(self):
        gen = _make_generator()
        gen._client.complete.return_value = self._llm_response(
            self._base(spot_tendencies=[['sticky', 0.8], ['bogus', 0.9]])
        )
        result = gen._generate_personality('Slots Linda')
        assert result['spot_tendencies'] == [['sticky', 0.8]]


class TestGenerationPromptSpotTendencyVocabulary:
    def _prompt(self) -> str:
        return PersonalityGenerator.GENERATION_PROMPT

    def test_section_five_present(self):
        assert 'SPOT TENDENCIES' in self._prompt()

    def test_self_belief_documented(self):
        assert 'self_belief' in self._prompt()

    def test_all_registered_tendencies_named_in_prompt(self):
        p = self._prompt()
        for name in REGISTERED_SPOT_TENDENCIES:
            assert name in p, f"spot_tendency {name!r} missing from generation prompt"


class TestGenerateFromSpec:
    """Spec-pinned mode: LLM flavor wrapped around enforced mechanical fields."""

    def _llm_response(self, content: dict):
        resp = MagicMock()
        import json

        resp.content = json.dumps(content)
        return resp

    def _wild_llm_body(self):
        # The LLM tries to make a wild maniac; the spec must override the
        # mechanical fields regardless of what the model returns.
        return {
            'play_style': 'wild maximum-pressure maniac',
            'default_confidence': 'overconfident',
            'default_attitude': 'manic',
            'personality_traits': {'bluff_tendency': 0.9, 'aggression': 0.95, 'emoji_usage': 0.5},
            'anchors': {
                'baseline_aggression': 0.92,
                'baseline_looseness': 0.90,
                'ego': 0.8,
                'poise': 0.2,
                'expressiveness': 0.9,
                'risk_identity': 0.9,
                'adaptation_bias': 0.9,
                'baseline_energy': 0.9,
                'recovery_rate': 0.3,
            },
            'bankroll_knobs': {
                'starting_bankroll': 5000,
                'bankroll_rate': 100,
                'buy_in_multiplier': 2.5,
                'stake_comfort_zone': '$2',
            },
            'spot_tendencies': [['over_bluff', 0.9]],
        }

    def test_pinned_anchors_override_llm(self):
        gen = _make_generator()
        gen._client.complete.return_value = self._llm_response(self._wild_llm_body())
        spec = {
            'anchors': {
                'baseline_aggression': 0.40,
                'baseline_looseness': 0.22,
                'adaptation_bias': 0.35,
            },
            'archetype_hint': 'Rock',
        }
        result = gen.generate_from_spec('Abraham Lincoln', spec)
        assert result['anchors']['baseline_aggression'] == 0.40
        assert result['anchors']['baseline_looseness'] == 0.22
        # Un-pinned anchors fall through from the LLM body.
        assert result['anchors']['poise'] == 0.2
        # self_belief still guaranteed present.
        assert 'self_belief' in result['anchors']

    def test_pinned_bankroll_and_skill_enforced(self):
        gen = _make_generator()
        gen._client.complete.return_value = self._llm_response(self._wild_llm_body())
        spec = {
            'bankroll_knobs': {
                'starting_bankroll': 120000,
                'bankroll_rate': 1500,
                'buy_in_multiplier': 2.0,
                'stake_comfort_zone': '$1000',
            },
            'skill': 'reg',
        }
        result = gen.generate_from_spec('Zeus', spec)
        assert result['bankroll_knobs']['starting_bankroll'] == 120000
        assert result['skill'] == 'reg'

    def test_pinned_spot_tendencies_override_and_sanitize(self):
        gen = _make_generator()
        gen._client.complete.return_value = self._llm_response(self._wild_llm_body())
        spec = {'spot_tendencies': [['sticky', 0.7], ['not_real', 0.9]]}
        result = gen.generate_from_spec('Slots Linda', spec)
        assert result['spot_tendencies'] == [['sticky', 0.7]]

    def test_skill_derived_from_pinned_adaptation_bias_when_not_pinned(self):
        gen = _make_generator()
        gen._client.complete.return_value = self._llm_response(self._wild_llm_body())
        # adaptation_bias pinned low; skill not pinned -> must re-derive from it,
        # not inherit the LLM body's high-adaptation shark ceiling.
        spec = {'anchors': {'adaptation_bias': 0.30}}
        result = gen.generate_from_spec('Loose Larry', spec)
        from poker.strategy.skill_tiers import skill_tier_for_adaptation_bias

        assert result['skill'] == skill_tier_for_adaptation_bias(0.30)
