"""Tests for the relationship-axis → exploitation-modifier reader.

Covers:
  - Identity modifier when no relationship state exists
  - Identity modifier when manager has no repo (in-memory unit test)
  - Heat > threshold triggers bluff_freq_mult + call_threshold_offset
  - Respect > threshold triggers fold_to_pressure_mult
  - Likability > threshold scales bluff_freq_mult down (multiplicative)
  - Multiple axes compose multiplicatively
  - Projection-applied: stale heat decays before the modifier reads
  - is_identity property tracks the boolean correctly
  - Strict pairwise: only the named (observer, target) pair drives output
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

from poker.memory.opponent_model import (
    HEAT_DECAY_HALF_LIFE_DAYS,
    HEAT_DECAY_PLATEAU_DAYS,
    OpponentModelManager,
    RelationshipState,
)
from poker.memory.relationship_modifier import (
    HEAT_RIVAL_THRESHOLD,
    HIGH_LIKABILITY_BLUFF_FREQ_MULT,
    HIGH_RESPECT_FOLD_TO_PRESSURE_MULT,
    LIKABILITY_HIGH_THRESHOLD,
    RESPECT_HIGH_THRESHOLD,
    RIVAL_BLUFF_FREQ_MULT,
    RIVAL_CALL_THRESHOLD_OFFSET,
    RelationshipModifier,
    _modifier_from_axes,
    get_relationship_modifier,
)
from poker.repositories.relationship_repository import RelationshipRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "mod.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repo(db_path):
    r = RelationshipRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def manager(repo):
    return OpponentModelManager(relationship_repo=repo)


# --- Identity modifier ---


class TestIdentityModifier:
    def test_identity_modifier_defaults(self):
        m = RelationshipModifier()
        assert m.bluff_freq_mult == 1.0
        assert m.fold_to_pressure_mult == 1.0
        assert m.call_threshold_offset == 0.0
        assert m.is_identity

    def test_no_state_returns_identity(self, manager):
        result = get_relationship_modifier(
            manager,
            "alice",
            "bob",
            now=datetime(2026, 5, 17),
        )
        assert result.is_identity

    def test_no_repo_returns_identity(self):
        # Manager constructed without a repo can't read relationships,
        # but the modifier reader must degrade gracefully — no error,
        # identity modifier.
        mgr = OpponentModelManager()
        result = get_relationship_modifier(
            mgr,
            "alice",
            "bob",
            now=datetime(2026, 5, 17),
        )
        assert result.is_identity

    def test_default_state_returns_identity(self, manager, repo):
        # Even when a row exists, default axes (heat=0, respect=0.5,
        # likability=0.5) don't cross any threshold — identity.
        now = datetime(2026, 5, 17, 12, 0)
        repo.save_relationship_state(
            "alice",
            "bob",
            RelationshipState(),  # defaults
        )
        result = get_relationship_modifier(manager, "alice", "bob", now=now)
        assert result.is_identity


# --- Heat triggers ---


class TestHeatRival:
    def test_heat_above_threshold_triggers_rival_modifiers(self):
        m = _modifier_from_axes(heat=0.6, respect=0.5, likability=0.5)
        assert m.bluff_freq_mult == pytest.approx(RIVAL_BLUFF_FREQ_MULT)
        assert m.call_threshold_offset == pytest.approx(RIVAL_CALL_THRESHOLD_OFFSET)
        assert m.fold_to_pressure_mult == 1.0  # unaffected

    def test_heat_exactly_at_threshold_does_not_trigger(self):
        # Condition is strict `>`, not `>=`
        m = _modifier_from_axes(heat=HEAT_RIVAL_THRESHOLD, respect=0.5, likability=0.5)
        assert m.is_identity

    def test_heat_below_threshold_no_trigger(self):
        m = _modifier_from_axes(heat=0.3, respect=0.5, likability=0.5)
        assert m.is_identity

    def test_heat_projected_through_decay(self, manager, repo):
        """Stale heat must decay before the modifier reads — a 30-day-
        old peak rivalry should not still trigger rival modifiers."""
        thirty_days_ago = datetime(2026, 4, 17, 12, 0)
        now = datetime(2026, 5, 17, 12, 0)
        # heat=0.55 fresh would trigger; after 30 days of decay
        # (7 plateau + 23 decay = ~1.64 half-lives), it's well under 0.5
        repo.save_relationship_state(
            "alice",
            "bob",
            RelationshipState(
                heat=0.55,
                last_decay_tick=thirty_days_ago,
            ),
        )
        result = get_relationship_modifier(manager, "alice", "bob", now=now)
        # Should be identity because decayed heat is below 0.5
        assert result.is_identity


# --- Respect triggers ---


class TestRespectHigh:
    def test_respect_above_threshold_triggers_fold_resistance(self):
        m = _modifier_from_axes(heat=0.0, respect=0.8, likability=0.5)
        assert m.fold_to_pressure_mult == pytest.approx(HIGH_RESPECT_FOLD_TO_PRESSURE_MULT)
        assert m.bluff_freq_mult == 1.0  # unaffected
        assert m.call_threshold_offset == 0.0  # unaffected

    def test_respect_at_threshold_does_not_trigger(self):
        m = _modifier_from_axes(heat=0.0, respect=RESPECT_HIGH_THRESHOLD, likability=0.5)
        assert m.is_identity


# --- Likability triggers ---


class TestLikabilityHigh:
    def test_likability_above_threshold_dampens_bluffs(self):
        m = _modifier_from_axes(heat=0.0, respect=0.5, likability=0.8)
        # Starting bluff_freq_mult = 1.0, scaled by 0.85
        assert m.bluff_freq_mult == pytest.approx(HIGH_LIKABILITY_BLUFF_FREQ_MULT)
        # Other dimensions unaffected
        assert m.fold_to_pressure_mult == 1.0
        assert m.call_threshold_offset == 0.0

    def test_likability_at_threshold_does_not_trigger(self):
        m = _modifier_from_axes(heat=0.0, respect=0.5, likability=LIKABILITY_HIGH_THRESHOLD)
        assert m.is_identity


# --- Multi-axis composition ---


class TestMultiAxisComposition:
    def test_high_heat_and_high_likability_compose_multiplicatively(self):
        # heat triggers ×1.3, likability triggers ×0.85, composed:
        # 1.0 × 1.3 × 0.85 = 1.105
        m = _modifier_from_axes(heat=0.7, respect=0.5, likability=0.8)
        expected = RIVAL_BLUFF_FREQ_MULT * HIGH_LIKABILITY_BLUFF_FREQ_MULT
        assert m.bluff_freq_mult == pytest.approx(expected)
        # Heat also triggers call_threshold_offset
        assert m.call_threshold_offset == pytest.approx(RIVAL_CALL_THRESHOLD_OFFSET)

    def test_all_three_axes_high(self):
        m = _modifier_from_axes(heat=0.9, respect=0.9, likability=0.9)
        # All three triggers fire:
        #   bluff_freq_mult = 1.0 × 1.3 × 0.85
        #   fold_to_pressure_mult = 1.0 × 0.7
        #   call_threshold_offset = -0.03
        assert m.bluff_freq_mult == pytest.approx(
            RIVAL_BLUFF_FREQ_MULT * HIGH_LIKABILITY_BLUFF_FREQ_MULT
        )
        assert m.fold_to_pressure_mult == pytest.approx(HIGH_RESPECT_FOLD_TO_PRESSURE_MULT)
        assert m.call_threshold_offset == pytest.approx(RIVAL_CALL_THRESHOLD_OFFSET)


# --- End-to-end through manager + repo ---


class TestEndToEnd:
    def test_high_heat_state_drives_rival_modifier(self, manager, repo):
        now = datetime(2026, 5, 17, 12, 0)
        repo.save_relationship_state(
            "alice",
            "bob",
            RelationshipState(heat=0.7, last_decay_tick=now, last_seen=now),
        )
        result = get_relationship_modifier(manager, "alice", "bob", now=now)
        assert result.bluff_freq_mult == pytest.approx(RIVAL_BLUFF_FREQ_MULT)
        assert result.call_threshold_offset == pytest.approx(RIVAL_CALL_THRESHOLD_OFFSET)
        assert not result.is_identity

    def test_strict_pairwise(self, manager, repo):
        """Reader returns the modifier for THE named pair only —
        alice's relationship with carol must not affect alice's
        modifier toward bob."""
        now = datetime(2026, 5, 17, 12, 0)
        repo.save_relationship_state(
            "alice",
            "carol",
            RelationshipState(heat=0.9, last_decay_tick=now),
        )
        # alice→bob has no state
        result = get_relationship_modifier(manager, "alice", "bob", now=now)
        assert result.is_identity

        # alice→carol has heat state, so it should not be identity
        carol_result = get_relationship_modifier(manager, "alice", "carol", now=now)
        assert not carol_result.is_identity

    def test_observer_target_directionality(self, manager, repo):
        """alice→bob and bob→alice are separate pairs with independent
        state and independent modifiers."""
        now = datetime(2026, 5, 17, 12, 0)
        repo.save_relationship_state(
            "alice",
            "bob",
            RelationshipState(heat=0.7, last_decay_tick=now),
        )
        # bob's view of alice is default (no state)
        alice_view = get_relationship_modifier(manager, "alice", "bob", now=now)
        bob_view = get_relationship_modifier(manager, "bob", "alice", now=now)

        assert not alice_view.is_identity  # alice has heat
        assert bob_view.is_identity  # bob has no state


# --- is_identity property ---


class TestIsIdentityProperty:
    def test_default_is_identity(self):
        assert RelationshipModifier().is_identity

    def test_changed_mult_is_not_identity(self):
        assert not RelationshipModifier(bluff_freq_mult=1.3).is_identity

    def test_changed_offset_is_not_identity(self):
        assert not RelationshipModifier(call_threshold_offset=-0.03).is_identity

    def test_frozen(self):
        m = RelationshipModifier()
        with pytest.raises((AttributeError, Exception)):
            m.bluff_freq_mult = 2.0  # type: ignore[misc]


# --- Threshold constants lock-in ---


class TestThresholdConstants:
    """Lock the v1 calibration so tuning passes are intentional and
    visible in diff."""

    def test_heat_rival_threshold(self):
        assert HEAT_RIVAL_THRESHOLD == 0.5

    def test_respect_high_threshold(self):
        assert RESPECT_HIGH_THRESHOLD == 0.7

    def test_likability_high_threshold(self):
        assert LIKABILITY_HIGH_THRESHOLD == 0.7

    def test_rival_bluff_mult(self):
        assert RIVAL_BLUFF_FREQ_MULT == 1.3

    def test_high_respect_mult(self):
        assert HIGH_RESPECT_FOLD_TO_PRESSURE_MULT == 0.7

    def test_high_likability_mult(self):
        assert HIGH_LIKABILITY_BLUFF_FREQ_MULT == 0.85
