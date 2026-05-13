"""Tests for personality_modifier and deviation_profiles."""

import numpy as np
import pytest

from poker.bounded_options import EmotionalShift
from poker.psychology_model import PersonalityAnchors
from poker.strategy.deviation_profiles import (
    DEVIATION_PROFILES,
    select_deviation_profile,
)
from poker.strategy.personality_modifier import (
    _kl_divergence,
    categorize_action,
    modify_strategy,
)
from poker.strategy.strategy_profile import StrategyProfile


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_anchors(**overrides) -> PersonalityAnchors:
    defaults = dict(
        baseline_aggression=0.5, baseline_looseness=0.5,
        ego=0.5, poise=0.7, expressiveness=0.5,
        risk_identity=0.5, adaptation_bias=0.5,
        baseline_energy=0.5, recovery_rate=0.15,
    )
    defaults.update(overrides)
    return PersonalityAnchors(**defaults)


COMPOSED = EmotionalShift(state='composed', severity='none', intensity=0.0)

BASE_PROBS = {'fold': 0.3, 'call': 0.4, 'raise_2.5bb': 0.2, 'jam': 0.1}
BASE_ACTIONS = list(BASE_PROBS.keys())
BASE_STRATEGY = StrategyProfile(action_probabilities=BASE_PROBS)


# ── 1. categorize_action ────────────────────────────────────────────────

@pytest.mark.parametrize("action,expected", [
    ('fold', 'fold'),
    ('check', 'passive'),
    ('call', 'passive'),
    ('raise_2.5bb', 'aggressive'),
    ('jam', 'aggressive'),
    ('bet_67', 'aggressive'),
])
def test_categorize_action(action, expected):
    assert categorize_action(action) == expected


# ── 2. High aggression boosts aggressive actions ────────────────────────

def test_high_aggression_boosts_raises():
    anchors = _make_anchors(baseline_aggression=0.9)
    profile = DEVIATION_PROFILES['lag']

    result = modify_strategy(
        BASE_STRATEGY, BASE_ACTIONS, anchors, COMPOSED, profile
    )

    assert result.action_probabilities['raise_2.5bb'] > BASE_PROBS['raise_2.5bb']
    assert result.action_probabilities['jam'] > BASE_PROBS['jam']


# ── 3. High looseness penalizes fold ────────────────────────────────────

def test_high_looseness_penalizes_fold():
    anchors = _make_anchors(baseline_looseness=0.9)
    profile = DEVIATION_PROFILES['lag']

    result = modify_strategy(
        BASE_STRATEGY, BASE_ACTIONS, anchors, COMPOSED, profile
    )

    assert result.action_probabilities['fold'] < BASE_PROBS['fold']


# ── 4. Zero-support preserved ───────────────────────────────────────────

def test_zero_support_preserved():
    base = StrategyProfile(action_probabilities={
        'fold': 0.0, 'call': 0.5, 'raise_2.5bb': 0.3, 'jam': 0.2,
    })
    anchors = _make_anchors(
        baseline_aggression=0.9, baseline_looseness=0.9,
        ego=0.8, poise=0.3, risk_identity=0.8,
    )
    emotional = EmotionalShift(state='tilted', severity='extreme', intensity=0.8)
    profile = DEVIATION_PROFILES['maniac']

    result = modify_strategy(
        base, ['fold', 'call', 'raise_2.5bb', 'jam'],
        anchors, emotional, profile,
    )

    assert result.action_probabilities['fold'] == 0.0


# ── 5. KL stays within budget ───────────────────────────────────────────

def test_kl_within_budget():
    anchors = _make_anchors(
        baseline_aggression=0.9, baseline_looseness=0.9,
        ego=0.9, poise=0.1, risk_identity=0.9,
    )
    emotional = EmotionalShift(state='tilted', severity='extreme', intensity=0.9)

    base_arr = np.array([BASE_PROBS[a] for a in BASE_ACTIONS])

    for name, profile in DEVIATION_PROFILES.items():
        result = modify_strategy(
            BASE_STRATEGY, BASE_ACTIONS, anchors, emotional, profile
        )
        result_arr = np.array([
            result.action_probabilities[a] for a in BASE_ACTIONS
        ])
        kl = _kl_divergence(result_arr, base_arr)
        assert kl <= profile.max_kl + 1e-6, (
            f"KL {kl:.4f} exceeds max_kl {profile.max_kl} for {name}"
        )


# ── 6. Per-action cap holds ─────────────────────────────────────────────

def test_per_action_cap_holds():
    anchors = _make_anchors(
        baseline_aggression=0.95, baseline_looseness=0.95,
        ego=0.95, poise=0.05, risk_identity=0.95,
    )
    emotional = EmotionalShift(state='tilted', severity='extreme', intensity=0.95)

    for name, profile in DEVIATION_PROFILES.items():
        result = modify_strategy(
            BASE_STRATEGY, BASE_ACTIONS, anchors, emotional, profile
        )

        for action, base_p in BASE_PROBS.items():
            shift = abs(result.action_probabilities[action] - base_p)
            assert shift <= profile.max_per_action_shift + 1e-6, (
                f"Action {action} shift {shift:.4f} exceeds cap "
                f"{profile.max_per_action_shift} for {name}"
            )


# ── 7. No negative probabilities ────────────────────────────────────────

def test_no_negative_probabilities():
    base = StrategyProfile(action_probabilities={
        'fold': 0.05, 'call': 0.05, 'raise_2.5bb': 0.45, 'jam': 0.45,
    })
    anchors = _make_anchors(
        baseline_aggression=0.0, baseline_looseness=0.0,
        ego=1.0, poise=0.0, risk_identity=0.0,
    )
    emotional = EmotionalShift(state='shaken', severity='extreme', intensity=1.0)
    profile = DEVIATION_PROFILES['nit']

    result = modify_strategy(
        base, ['fold', 'call', 'raise_2.5bb', 'jam'],
        anchors, emotional, profile,
    )

    for action, prob in result.action_probabilities.items():
        assert prob >= 0.0, f"Negative probability {prob} for {action}"


# ── 8. Probabilities sum to 1.0 ─────────────────────────────────────────

def test_probabilities_sum_to_one():
    anchors = _make_anchors(
        baseline_aggression=0.8, baseline_looseness=0.7,
        ego=0.6, poise=0.4, risk_identity=0.7,
    )
    emotional = EmotionalShift(
        state='overconfident', severity='moderate', intensity=0.5,
    )
    profile = DEVIATION_PROFILES['lag']

    result = modify_strategy(
        BASE_STRATEGY, BASE_ACTIONS, anchors, emotional, profile
    )

    total = sum(result.action_probabilities.values())
    assert abs(total - 1.0) < 1e-6, f"Sum is {total}, expected 1.0"


# ── 9. select_deviation_profile ─────────────────────────────────────────

def test_select_deviation_profile_nit():
    anchors = _make_anchors(baseline_aggression=0.1, baseline_looseness=0.1)
    assert select_deviation_profile(anchors) == DEVIATION_PROFILES['nit']


def test_select_deviation_profile_maniac():
    anchors = _make_anchors(baseline_aggression=0.9, baseline_looseness=0.9)
    assert select_deviation_profile(anchors) == DEVIATION_PROFILES['maniac']


def test_select_deviation_profile_rock():
    anchors = _make_anchors(baseline_aggression=0.3, baseline_looseness=0.3)
    assert select_deviation_profile(anchors) == DEVIATION_PROFILES['rock']


def test_select_deviation_profile_tag():
    anchors = _make_anchors(baseline_aggression=0.7, baseline_looseness=0.3)
    assert select_deviation_profile(anchors) == DEVIATION_PROFILES['tag']


def test_select_deviation_profile_calling_station():
    anchors = _make_anchors(baseline_aggression=0.3, baseline_looseness=0.8)
    assert select_deviation_profile(anchors) == DEVIATION_PROFILES['calling_station']


def test_select_deviation_profile_lag():
    anchors = _make_anchors(baseline_aggression=0.7, baseline_looseness=0.8)
    assert select_deviation_profile(anchors) == DEVIATION_PROFILES['lag']


def test_select_deviation_profile_default_maps_to_tag():
    """Balanced anchors (between tight and loose) map to 'tag'."""
    anchors = _make_anchors(baseline_aggression=0.5, baseline_looseness=0.5)
    assert select_deviation_profile(anchors) == DEVIATION_PROFILES['tag']


# ── 10. Composed emotional state has no effect ──────────────────────────

def test_composed_emotional_no_effect():
    """With all anchors at neutral center and ego=0, composed state
    produces zero offsets so the result matches the base exactly."""
    anchors = _make_anchors(
        baseline_aggression=0.5, baseline_looseness=0.5,
        ego=0.0, poise=0.5, risk_identity=0.5,
    )
    profile = DEVIATION_PROFILES['tag']

    result = modify_strategy(
        BASE_STRATEGY, BASE_ACTIONS, anchors, COMPOSED, profile
    )

    for action in BASE_PROBS:
        assert abs(
            result.action_probabilities[action] - BASE_PROBS[action]
        ) < 1e-6, (
            f"Composed state changed {action}: "
            f"{BASE_PROBS[action]} -> {result.action_probabilities[action]}"
        )
