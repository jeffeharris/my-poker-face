"""Tests for StrategyProfile."""

import random
from collections import Counter

import pytest

from poker.strategy.strategy_profile import StrategyProfile


class TestStrategyProfile:
    def test_immutable(self):
        sp = StrategyProfile(action_probabilities={'fold': 0.5, 'call': 0.5})
        with pytest.raises(AttributeError):
            sp.action_probabilities = {}

    def test_sample_action_deterministic(self):
        sp = StrategyProfile(action_probabilities={'raise_2.5bb': 1.0})
        rng = random.Random(42)
        assert sp.sample_action(rng) == 'raise_2.5bb'

    def test_sample_action_pure_fold(self):
        sp = StrategyProfile(action_probabilities={'fold': 1.0, 'call': 0.0})
        rng = random.Random(42)
        for _ in range(10):
            assert sp.sample_action(rng) == 'fold'

    def test_sample_action_distribution(self):
        """Mixed strategy should produce roughly correct frequencies."""
        sp = StrategyProfile(action_probabilities={'fold': 0.5, 'call': 0.3, 'raise_2.5bb': 0.2})
        rng = random.Random(42)
        counts = Counter(sp.sample_action(rng) for _ in range(10000))
        # Check rough proportions (within 5% tolerance)
        assert abs(counts['fold'] / 10000 - 0.5) < 0.05
        assert abs(counts['call'] / 10000 - 0.3) < 0.05
        assert abs(counts['raise_2.5bb'] / 10000 - 0.2) < 0.05

    def test_sample_action_zero_weight_raises(self):
        sp = StrategyProfile(action_probabilities={'fold': 0.0, 'call': 0.0})
        rng = random.Random(42)
        with pytest.raises(ValueError):
            sp.sample_action(rng)
