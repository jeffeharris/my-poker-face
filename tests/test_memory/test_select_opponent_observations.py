"""Tests for OpponentModelManager.select_opponent_observations.

Validates the selection logic the LLM prompt path depends on:
  - Empty / no-observation cases return [].
  - Recency boost orders newer observations above older ones from the
    same opponent.
  - Facing-opponent bonus pulls that opponent's observation above
    competing reads from non-facing opponents.
  - One observation per opponent in the output (no opponent dominates).
  - Cap respected.
  - format_opponent_observations renders the section correctly.
"""

import pytest

from poker.memory.opponent_model import (
    OpponentModelManager,
    format_opponent_observations,
)


@pytest.fixture
def manager():
    return OpponentModelManager()


def _observe(manager, observer: str, opponent: str, *obs: str) -> None:
    """Add one or more narrative observations to (observer, opponent) model."""
    model = manager.get_model(observer, opponent)
    for line in obs:
        model.add_narrative_observation(line)


class TestEmptyCases:
    def test_no_observer_returns_empty(self, manager):
        assert manager.select_opponent_observations(
            observer='hero', active_opponents=['villain'],
        ) == []

    def test_no_active_opponents_returns_empty(self, manager):
        _observe(manager, 'hero', 'villain', 'folds to pressure')
        assert manager.select_opponent_observations(
            observer='hero', active_opponents=[],
        ) == []

    def test_opponent_with_no_observations_returns_empty(self, manager):
        manager.get_model('hero', 'villain')  # creates empty model
        assert manager.select_opponent_observations(
            observer='hero', active_opponents=['villain'],
        ) == []

    def test_active_opponent_not_in_models_skipped(self, manager):
        _observe(manager, 'hero', 'known', 'plays tight')
        result = manager.select_opponent_observations(
            observer='hero', active_opponents=['unknown'],
        )
        # 'unknown' has no model → filtered; nothing else active → empty
        assert result == []


class TestRecencyOrdering:
    def test_newest_observation_wins_within_opponent(self, manager):
        _observe(manager, 'hero', 'villain', 'old read', 'middle read', 'fresh read')
        result = manager.select_opponent_observations(
            observer='hero', active_opponents=['villain'], max_observations=1,
        )
        assert result == [('villain', 'fresh read')]


class TestFacingBonus:
    def test_facing_opponent_outranks_recency(self, manager):
        # 'facing' has an OLD observation, 'other' has a NEW observation.
        # Without facing bonus, 'other' would win on recency. With it,
        # 'facing' beats 'other'.
        _observe(manager, 'hero', 'facing', 'old read on facing')
        _observe(manager, 'hero', 'other', 'fresh read on other')
        result = manager.select_opponent_observations(
            observer='hero',
            active_opponents=['facing', 'other'],
            facing_opponent='facing',
            max_observations=2,
        )
        assert result[0] == ('facing', 'old read on facing')
        assert ('other', 'fresh read on other') in result

    def test_no_facing_falls_back_to_recency(self, manager):
        _observe(manager, 'hero', 'villain_a', 'older')
        _observe(manager, 'hero', 'villain_b', 'newer')
        # No facing_opponent provided. Each opponent's observation gets
        # only recency bonus. Since each opponent has 1 observation
        # (recency = 0.3 each), the order is stable but tied. Just
        # confirm both come back when max_observations=2.
        result = manager.select_opponent_observations(
            observer='hero',
            active_opponents=['villain_a', 'villain_b'],
            max_observations=2,
        )
        names = sorted(n for n, _ in result)
        assert names == ['villain_a', 'villain_b']


class TestOnePerOpponent:
    def test_one_observation_per_opponent_in_output(self, manager):
        # If villain has 5 observations, they shouldn't ALL claim slots
        # — that would crowd out other_opp's read.
        _observe(manager, 'hero', 'villain', *(f'read {i}' for i in range(5)))
        _observe(manager, 'hero', 'other_opp', 'one read')
        result = manager.select_opponent_observations(
            observer='hero',
            active_opponents=['villain', 'other_opp'],
            max_observations=2,
        )
        names = [n for n, _ in result]
        assert sorted(names) == ['other_opp', 'villain']


class TestCap:
    def test_max_observations_respected(self, manager):
        for opp in ('a', 'b', 'c', 'd'):
            _observe(manager, 'hero', opp, f'read on {opp}')
        result = manager.select_opponent_observations(
            observer='hero',
            active_opponents=['a', 'b', 'c', 'd'],
            max_observations=2,
        )
        assert len(result) == 2

    def test_max_observations_one(self, manager):
        _observe(manager, 'hero', 'a', 'read a')
        _observe(manager, 'hero', 'b', 'read b')
        result = manager.select_opponent_observations(
            observer='hero', active_opponents=['a', 'b'],
            max_observations=1,
        )
        assert len(result) == 1


class TestNoRelationshipRepo:
    """When no relationship_repo is configured (in-memory tests), the
    nemesis lookup must degrade gracefully — no exceptions, just no
    nemesis bonus."""

    def test_no_repo_no_exception(self, manager):
        # manager has _relationship_repo=None by default
        _observe(manager, 'hero', 'villain', 'tight player')
        result = manager.select_opponent_observations(
            observer='hero', active_opponents=['villain'],
        )
        assert result == [('villain', 'tight player')]


class TestFormatHelper:
    def test_empty_returns_empty_string(self):
        assert format_opponent_observations([]) == ''

    def test_renders_lines(self):
        out = format_opponent_observations([
            ('Alice', 'folds to cbets'),
            ('Bob', 'overvalues top pair'),
        ])
        lines = out.split('\n')
        assert lines[0] == 'Your reads on opponents:'
        assert lines[1] == '- Alice: folds to cbets'
        assert lines[2] == '- Bob: overvalues top pair'
