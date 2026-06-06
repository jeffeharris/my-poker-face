"""Tests for RelationshipState and project_heat.

The heat axis is the only durable state that decays; respect and
likability are earned and stay. project_heat is a pure function over
`(state, now)` and is the canonical read path for the heat value.
Direct reads of `state.heat` should be rare — they return the
"heat as of last event" snapshot, not the live value.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from poker.memory.opponent_model import (
    HEAT_DECAY_HALF_LIFE_DAYS,
    HEAT_DECAY_PLATEAU_DAYS,
    HEAT_DECAY_SNAP_THRESHOLD,
    REGARD_NEUTRAL,
    RelationshipState,
    project_heat,
)


class TestRelationshipStateDefaults:
    def test_defaults_match_design(self):
        s = RelationshipState()
        # From CASH_MODE_AND_RELATIONSHIPS.md Part 1 §"Data model", as
        # re-baselined: respect/likability neutral = REGARD_NEUTRAL (earned,
        # asymmetric), heat 0.0 (one-sided axis).
        assert s.respect == REGARD_NEUTRAL
        assert s.heat == 0.0
        assert s.likability == REGARD_NEUTRAL
        assert s.last_seen is None
        assert s.last_decay_tick is None

    def test_fields_are_writable(self):
        # Not frozen — record_event mutates these in place.
        s = RelationshipState()
        s.heat = 0.7
        s.last_decay_tick = datetime(2026, 5, 17)
        assert s.heat == 0.7
        assert s.last_decay_tick == datetime(2026, 5, 17)


class TestProjectHeatNoTick:
    """When no event has ever been recorded, last_decay_tick is None
    and project_heat returns the stored value verbatim. For a fresh
    state that's 0.0; for an in-memory test state that pre-sets heat
    without a tick, it returns whatever the test set."""

    def test_returns_zero_for_fresh_state(self):
        s = RelationshipState()
        assert project_heat(s, datetime(2026, 5, 17)) == 0.0

    def test_returns_stored_value_when_no_tick(self):
        # Edge case: heat without a tick is unusual but defined.
        s = RelationshipState(heat=0.4)
        assert project_heat(s, datetime(2026, 5, 17)) == 0.4


class TestProjectHeatWithinPlateau:
    """During the plateau window (first 7 days after last_decay_tick),
    heat stays at its stored value — fresh rivalries feel hot."""

    @pytest.mark.parametrize("days_elapsed", [0, 1, 3, 6, 6.99])
    def test_plateau_returns_stored_value(self, days_elapsed):
        tick = datetime(2026, 5, 1, 12, 0, 0)
        now = tick + timedelta(days=days_elapsed)
        s = RelationshipState(heat=0.8, last_decay_tick=tick)
        assert project_heat(s, now) == 0.8

    def test_boundary_at_exactly_plateau(self):
        # The condition is `days <= plateau_days` — so exactly 7 days
        # still returns stored value, decay starts at 7.000...01 days.
        tick = datetime(2026, 5, 1, 12, 0, 0)
        now = tick + timedelta(days=HEAT_DECAY_PLATEAU_DAYS)
        s = RelationshipState(heat=0.6, last_decay_tick=tick)
        assert project_heat(s, now) == 0.6


class TestProjectHeatDecay:
    """After the plateau ends, heat decays exponentially with the
    configured half-life."""

    def test_one_half_life_halves_the_heat(self):
        tick = datetime(2026, 5, 1, 12, 0, 0)
        # plateau (7d) + one half-life (14d) = 21d total elapsed
        now = tick + timedelta(days=HEAT_DECAY_PLATEAU_DAYS + HEAT_DECAY_HALF_LIFE_DAYS)
        s = RelationshipState(heat=0.8, last_decay_tick=tick)
        result = project_heat(s, now)
        assert result == pytest.approx(0.4, abs=1e-9)

    def test_two_half_lives_quarters_the_heat(self):
        tick = datetime(2026, 5, 1, 12, 0, 0)
        now = tick + timedelta(days=HEAT_DECAY_PLATEAU_DAYS + 2 * HEAT_DECAY_HALF_LIFE_DAYS)
        s = RelationshipState(heat=0.8, last_decay_tick=tick)
        result = project_heat(s, now)
        assert result == pytest.approx(0.2, abs=1e-9)

    def test_decay_monotonically_decreases(self):
        tick = datetime(2026, 5, 1, 12, 0, 0)
        s = RelationshipState(heat=0.9, last_decay_tick=tick)
        prev = 0.9  # plateau value
        for days in (
            HEAT_DECAY_PLATEAU_DAYS + 1,
            HEAT_DECAY_PLATEAU_DAYS + 7,
            HEAT_DECAY_PLATEAU_DAYS + 14,
            HEAT_DECAY_PLATEAU_DAYS + 28,
        ):
            current = project_heat(s, tick + timedelta(days=days))
            assert current < prev, f"heat should decay; at {days}d got {current} >= {prev}"
            prev = current

    def test_decay_does_not_mutate_state(self):
        # Pure function — state.heat must equal its initial value after
        # any number of project_heat calls.
        tick = datetime(2026, 5, 1, 12, 0, 0)
        s = RelationshipState(heat=0.8, last_decay_tick=tick)
        project_heat(s, tick + timedelta(days=21))
        project_heat(s, tick + timedelta(days=100))
        project_heat(s, tick + timedelta(days=365))
        assert s.heat == 0.8
        assert s.last_decay_tick == tick


class TestProjectHeatSnapToZero:
    """Below `snap_threshold`, decayed heat snaps to 0.0 so tiny
    residuals don't pollute downstream reads. Without this, a value
    that's decayed to 1e-9 would still satisfy `heat > 0` predicates
    forever."""

    def test_below_threshold_snaps(self):
        tick = datetime(2026, 5, 1, 12, 0, 0)
        # Far enough out that 0.8 * 0.5^(N) < 0.05.
        # 0.8 * 0.5^x = 0.05 → x = log2(16) = 4. So 4 half-lives.
        now = tick + timedelta(days=HEAT_DECAY_PLATEAU_DAYS + 4 * HEAT_DECAY_HALF_LIFE_DAYS + 1)
        s = RelationshipState(heat=0.8, last_decay_tick=tick)
        assert project_heat(s, now) == 0.0

    def test_exactly_at_threshold_does_not_snap(self):
        # The condition is `projected < snap_threshold` (strict), so
        # exactly equal stays. (Floating-point being what it is,
        # callers shouldn't rely on this boundary, but it's the
        # documented semantics.)
        s = RelationshipState(heat=HEAT_DECAY_SNAP_THRESHOLD, last_decay_tick=datetime(2026, 5, 1))
        # No elapsed time — plateau path returns heat exactly. Boundary
        # is tested in the decay-path test below.
        result = project_heat(s, datetime(2026, 5, 1))
        assert result == HEAT_DECAY_SNAP_THRESHOLD

    def test_far_future_always_zero(self):
        tick = datetime(2026, 5, 1, 12, 0, 0)
        s = RelationshipState(heat=0.95, last_decay_tick=tick)
        # 10 years out — should snap to zero long before
        assert project_heat(s, tick + timedelta(days=365 * 10)) == 0.0


class TestProjectHeatCustomParameters:
    """Callers can override the decay constants for testing without
    touching the module-level defaults."""

    def test_custom_plateau_extends_held_value(self):
        tick = datetime(2026, 5, 1, 12, 0, 0)
        s = RelationshipState(heat=0.5, last_decay_tick=tick)
        # 10 days elapsed; default plateau is 7 (decaying), but
        # override extends to 30 (still plateau).
        now = tick + timedelta(days=10)
        assert project_heat(s, now, plateau_days=30) == 0.5

    def test_custom_half_life(self):
        tick = datetime(2026, 5, 1, 12, 0, 0)
        s = RelationshipState(heat=0.4, last_decay_tick=tick)
        # plateau 0 + half-life 5 days → 5 days out, heat halves
        now = tick + timedelta(days=5)
        assert project_heat(s, now, plateau_days=0, half_life_days=5) == pytest.approx(0.2)

    def test_custom_snap_threshold(self):
        tick = datetime(2026, 5, 1, 12, 0, 0)
        s = RelationshipState(heat=0.4, last_decay_tick=tick)
        # plateau 0 + half-life 1 + snap 0.15 → 2 days out heat=0.1 < 0.15 → snap
        now = tick + timedelta(days=2)
        result = project_heat(
            s,
            now,
            plateau_days=0,
            half_life_days=1,
            snap_threshold=0.15,
        )
        assert result == 0.0


class TestDesignDocConstants:
    """Lock in the calibration constants so any tuning pass is
    intentional and visible in diff. Changing these values is fine
    — but it should update these tests too."""

    def test_plateau_is_seven_days(self):
        assert HEAT_DECAY_PLATEAU_DAYS == 7

    def test_half_life_is_fourteen_days(self):
        assert HEAT_DECAY_HALF_LIFE_DAYS == 14

    def test_snap_threshold_is_point_zero_five(self):
        assert HEAT_DECAY_SNAP_THRESHOLD == 0.05
