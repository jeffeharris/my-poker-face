"""PRH-41: spend-velocity early-warning telemetry on the spend gate."""

import logging

import pytest

pytestmark = pytest.mark.llm


class _FakeTracker:
    def __init__(self, spend):
        self._spend = spend

    def get_recent_spend(self, owner_id=None, window_hours=24):
        return self._spend


def _fresh_gate():
    from core.llm.budget import SpendGate

    return SpendGate()


def test_velocity_warns_in_band(caplog):
    gate = _fresh_gate()
    gate.configure(global_daily_budget_usd=10.0, per_owner_daily_budget_usd=0.0)
    with caplog.at_level(logging.WARNING, logger="core.llm.budget"):
        # 85% of the $10 global cap → warn band, not yet over.
        assert gate.over_budget_reason(None, _FakeTracker(8.5)) is None
    assert any("[LLM BUDGET] velocity" in r.getMessage() for r in caplog.records)


def test_no_velocity_below_band(caplog):
    gate = _fresh_gate()
    gate.configure(global_daily_budget_usd=10.0, per_owner_daily_budget_usd=0.0)
    with caplog.at_level(logging.WARNING, logger="core.llm.budget"):
        assert gate.over_budget_reason(None, _FakeTracker(5.0)) is None  # 50%
    assert not any("velocity" in r.getMessage() for r in caplog.records)


def test_over_budget_blocks_without_velocity(caplog):
    gate = _fresh_gate()
    gate.configure(global_daily_budget_usd=10.0, per_owner_daily_budget_usd=0.0)
    with caplog.at_level(logging.WARNING, logger="core.llm.budget"):
        reason = gate.over_budget_reason(None, _FakeTracker(11.0))  # over
    assert reason and "global daily LLM budget exceeded" in reason
    # At/over the cap is the block path, not the velocity early-warning.
    assert not any("velocity" in r.getMessage() for r in caplog.records)


def test_velocity_is_throttled(caplog):
    gate = _fresh_gate()
    gate.configure(global_daily_budget_usd=10.0, per_owner_daily_budget_usd=0.0)
    with caplog.at_level(logging.WARNING, logger="core.llm.budget"):
        gate.over_budget_reason(None, _FakeTracker(8.5))
        gate.over_budget_reason(None, _FakeTracker(9.0))  # same scope, immediately
    velocity = [r for r in caplog.records if "velocity" in r.getMessage()]
    assert len(velocity) == 1  # throttled to one within the window
