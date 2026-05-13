"""Tests for board read injection into lean prompts.

Tests profile gating (only analytical profiles get the board read)
and emotional suppression (extreme tilted/shaken/dissociated suppresses it).
"""

import pytest
from unittest.mock import MagicMock, patch

from poker.bounded_options import (
    BoundedOption,
    EmotionalShift,
    OptionProfile,
    STYLE_PROFILES,
)
from poker.board_analyzer import build_board_read


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_controller_stub(profile_key='tight_aggressive'):
    """Create a minimal stub that has _build_lean_prompt accessible.

    We import and bind the method directly to avoid full controller setup.
    """
    from poker.hybrid_ai_controller import HybridAIController

    stub = MagicMock(spec=HybridAIController)
    stub.player_name = 'TestPlayer'
    stub._current_game_messages = []

    # prompt_config
    config = MagicMock()
    config.composed_nudges = False
    config.show_ev_labels = None
    stub.prompt_config = config

    # Bind the real _build_lean_prompt and _build_street_action_summary
    stub._build_lean_prompt = HybridAIController._build_lean_prompt.__get__(stub)
    stub._build_street_action_summary = HybridAIController._build_street_action_summary.__get__(stub)

    return stub


def _sample_options():
    """Two simple options for prompt building."""
    return [
        BoundedOption(action='check', raise_to=0, rationale='Check',
                      ev_estimate='neutral', style_tag='conservative'),
        BoundedOption(action='raise', raise_to=200, rationale='Value bet',
                      ev_estimate='+EV', style_tag='aggressive'),
    ]


def _postflop_context(**overrides):
    """Context dict for a postflop decision."""
    ctx = {
        'hole_cards': ['Ah', 'Kh'],
        'community_cards': ['Qh', 'Jh', '2s'],  # two-tone, connected
        'big_blind': 100,
        'phase': 'FLOP',
        'stack_bb': 50,
        'pot_total': 500,
    }
    ctx.update(overrides)
    return ctx


def _preflop_context(**overrides):
    """Context dict for a preflop decision."""
    ctx = {
        'hole_cards': ['Ah', 'Kh'],
        'community_cards': [],
        'big_blind': 100,
        'phase': 'PRE_FLOP',
        'stack_bb': 50,
        'pot_total': 300,
    }
    ctx.update(overrides)
    return ctx


# ── Profile Gating ───────────────────────────────────────────────────────────


class TestBoardReadProfileGating:
    """Board read only appears for profiles with board_read=True."""

    def test_tag_gets_board_read_postflop(self):
        """TAG profile sees board read on the flop."""
        stub = _make_controller_stub('tight_aggressive')
        profile = STYLE_PROFILES['tight_aggressive']
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'tight_aggressive',
                                         profile=profile)
        assert 'Board read:' in prompt

    def test_default_gets_board_read_postflop(self):
        """Default profile sees board read on the flop."""
        stub = _make_controller_stub('default')
        profile = STYLE_PROFILES['default']
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'default',
                                         profile=profile)
        assert 'Board read:' in prompt

    def test_lag_no_board_read(self):
        """LAG profile does NOT see board read."""
        stub = _make_controller_stub('loose_aggressive')
        profile = STYLE_PROFILES['loose_aggressive']
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'loose_aggressive',
                                         profile=profile)
        assert 'Board read:' not in prompt

    def test_loose_passive_no_board_read(self):
        """Loose passive profile does NOT see board read."""
        stub = _make_controller_stub('loose_passive')
        profile = STYLE_PROFILES['loose_passive']
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'loose_passive',
                                         profile=profile)
        assert 'Board read:' not in prompt

    def test_tight_passive_no_board_read(self):
        """Tight passive profile does NOT see board read."""
        stub = _make_controller_stub('tight_passive')
        profile = STYLE_PROFILES['tight_passive']
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'tight_passive',
                                         profile=profile)
        assert 'Board read:' not in prompt

    def test_no_board_read_preflop(self):
        """No board read on preflop (no community cards)."""
        stub = _make_controller_stub('tight_aggressive')
        profile = STYLE_PROFILES['tight_aggressive']
        ctx = _preflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'tight_aggressive',
                                         profile=profile)
        assert 'Board read:' not in prompt

    def test_no_board_read_when_profile_is_none(self):
        """No crash and no board read when profile is None."""
        stub = _make_controller_stub('default')
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'default',
                                         profile=None)
        assert 'Board read:' not in prompt


# ── Emotional Suppression ────────────────────────────────────────────────────


class TestBoardReadEmotionalSuppression:
    """Extreme tilted/shaken/dissociated suppress the board read."""

    def _get_prompt(self, emotional_shift):
        stub = _make_controller_stub('tight_aggressive')
        profile = STYLE_PROFILES['tight_aggressive']
        ctx = _postflop_context()
        return stub._build_lean_prompt(
            _sample_options(), ctx, 'tight_aggressive',
            profile=profile, emotional_shift=emotional_shift,
        )

    def test_extreme_tilted_suppresses(self):
        """Extreme tilted → no board read."""
        shift = EmotionalShift(state='tilted', severity='extreme', intensity=0.8)
        assert 'Board read:' not in self._get_prompt(shift)

    def test_extreme_shaken_suppresses(self):
        """Extreme shaken → no board read."""
        shift = EmotionalShift(state='shaken', severity='extreme', intensity=0.8)
        assert 'Board read:' not in self._get_prompt(shift)

    def test_extreme_dissociated_suppresses(self):
        """Extreme dissociated → no board read."""
        shift = EmotionalShift(state='dissociated', severity='extreme', intensity=0.8)
        assert 'Board read:' not in self._get_prompt(shift)

    def test_extreme_overconfident_does_not_suppress(self):
        """Extreme overconfident → board read still appears."""
        shift = EmotionalShift(state='overconfident', severity='extreme', intensity=0.8)
        assert 'Board read:' in self._get_prompt(shift)

    def test_moderate_tilted_does_not_suppress(self):
        """Moderate tilted → board read still appears."""
        shift = EmotionalShift(state='tilted', severity='moderate', intensity=0.5)
        assert 'Board read:' in self._get_prompt(shift)

    def test_mild_shaken_does_not_suppress(self):
        """Mild shaken → board read still appears."""
        shift = EmotionalShift(state='shaken', severity='mild', intensity=0.2)
        assert 'Board read:' in self._get_prompt(shift)

    def test_composed_does_not_suppress(self):
        """Composed state → board read appears normally."""
        shift = EmotionalShift(state='composed', severity='none', intensity=0.0)
        assert 'Board read:' in self._get_prompt(shift)

    def test_no_emotional_shift_does_not_suppress(self):
        """No emotional_shift param → board read appears normally."""
        stub = _make_controller_stub('tight_aggressive')
        profile = STYLE_PROFILES['tight_aggressive']
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(
            _sample_options(), ctx, 'tight_aggressive',
            profile=profile, emotional_shift=None,
        )
        assert 'Board read:' in prompt


# ── Board Read Content Verification ──────────────────────────────────────────


class TestBoardReadContent:
    """Verify board read content matches expected texture descriptions."""

    def test_wet_two_tone_board_content(self):
        """Two-tone connected board mentions flush and straight draws."""
        stub = _make_controller_stub('tight_aggressive')
        profile = STYLE_PROFILES['tight_aggressive']
        ctx = _postflop_context(community_cards=['Qh', 'Jh', 'Ts'])
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'tight_aggressive',
                                         profile=profile)
        assert 'flush draw' in prompt
        assert 'straight draw' in prompt

    def test_dry_rainbow_board_content(self):
        """Dry rainbow board: few draws possible."""
        stub = _make_controller_stub('tight_aggressive')
        profile = STYLE_PROFILES['tight_aggressive']
        ctx = _postflop_context(community_cards=['Kh', '7d', '2s'])
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'tight_aggressive',
                                         profile=profile)
        assert 'Board read:' in prompt
        assert 'dry' in prompt
        assert 'few draws' in prompt

    def test_monotone_board_content(self):
        """Monotone board mentions flush draw."""
        stub = _make_controller_stub('default')
        profile = STYLE_PROFILES['default']
        ctx = _postflop_context(community_cards=['Ah', '9h', '4h'])
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'default',
                                         profile=profile)
        assert 'monotone' in prompt
        assert 'flush draw' in prompt


# ── EV Label Profile Gating ─────────────────────────────────────────────────


class TestEvLabelProfileGating:
    """EV labels appear or hide based on profile.show_ev_labels + PromptConfig override."""

    def test_tag_shows_ev_labels(self):
        """TAG profile (show_ev_labels=True) shows [+EV] in prompt."""
        stub = _make_controller_stub('tight_aggressive')
        profile = STYLE_PROFILES['tight_aggressive']
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'tight_aggressive',
                                         profile=profile)
        assert '[+EV]' in prompt

    def test_lag_hides_ev_labels(self):
        """LAG profile (show_ev_labels=False) hides [+EV] from prompt."""
        stub = _make_controller_stub('loose_aggressive')
        profile = STYLE_PROFILES['loose_aggressive']
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'loose_aggressive',
                                         profile=profile)
        assert '[+EV]' not in prompt
        assert '[neutral]' not in prompt

    def test_prompt_config_override_shows_ev_on_lag(self):
        """PromptConfig show_ev_labels=True overrides LAG profile to show."""
        stub = _make_controller_stub('loose_aggressive')
        stub.prompt_config.show_ev_labels = True
        profile = STYLE_PROFILES['loose_aggressive']
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'loose_aggressive',
                                         profile=profile)
        assert '[+EV]' in prompt

    def test_prompt_config_override_hides_ev_on_tag(self):
        """PromptConfig show_ev_labels=False overrides TAG profile to hide."""
        stub = _make_controller_stub('tight_aggressive')
        stub.prompt_config.show_ev_labels = False
        profile = STYLE_PROFILES['tight_aggressive']
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'tight_aggressive',
                                         profile=profile)
        assert '[+EV]' not in prompt
        assert '[neutral]' not in prompt

    def test_prompt_config_none_defers_to_profile(self):
        """PromptConfig show_ev_labels=None defers to profile default."""
        stub = _make_controller_stub('loose_passive')
        stub.prompt_config.show_ev_labels = None
        profile = STYLE_PROFILES['loose_passive']
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'loose_passive',
                                         profile=profile)
        # loose_passive has show_ev_labels=False
        assert '[+EV]' not in prompt

    def test_no_profile_defaults_to_show(self):
        """When profile is None, EV labels show by default."""
        stub = _make_controller_stub('default')
        stub.prompt_config.show_ev_labels = None
        ctx = _postflop_context()
        prompt = stub._build_lean_prompt(_sample_options(), ctx, 'default',
                                         profile=None)
        assert '[+EV]' in prompt
