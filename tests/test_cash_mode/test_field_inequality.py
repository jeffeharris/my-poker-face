"""Tests for the throttled field-inequality signal."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from cash_mode import economy_flags, field_inequality


class _FakeBR:
    def __init__(self, chips):
        self.chips = chips
        self.calls = 0

    def list_all_ai_bankroll_chips(self, *, sandbox_id=None):
        self.calls += 1
        return list(self.chips)


@pytest.fixture(autouse=True)
def _clear():
    field_inequality.reset_cache()
    yield
    field_inequality.reset_cache()


def test_flat_field_factor_near_one():
    br = _FakeBR([100] * 10)
    f = field_inequality.refresh_field_inequality("sb", br, datetime(2026, 6, 4))
    assert f == pytest.approx(1.0)


def test_top_heavy_factor_large():
    br = _FakeBR([100] * 9 + [1000])
    f = field_inequality.refresh_field_inequality("sb", br, datetime(2026, 6, 4))
    assert f == pytest.approx(10.0)


def test_recompute_throttled_within_window():
    br = _FakeBR([100] * 10)
    t0 = datetime(2026, 6, 4, 12, 0, 0)
    field_inequality.refresh_field_inequality("sb", br, t0)
    # Within the recompute window → cached, no second scan.
    field_inequality.refresh_field_inequality(
        "sb", br, t0 + timedelta(seconds=economy_flags.INEQUALITY_RECOMPUTE_SECONDS - 1)
    )
    assert br.calls == 1


def test_recompute_after_window():
    br = _FakeBR([100] * 10)
    t0 = datetime(2026, 6, 4, 12, 0, 0)
    field_inequality.refresh_field_inequality("sb", br, t0)
    field_inequality.refresh_field_inequality(
        "sb", br, t0 + timedelta(seconds=economy_flags.INEQUALITY_RECOMPUTE_SECONDS + 1)
    )
    assert br.calls == 2


def test_read_without_compute_is_none():
    assert field_inequality.field_inequality("never-seen") is None


def test_too_few_chips_is_none():
    br = _FakeBR([100])
    assert field_inequality.refresh_field_inequality("sb", br, datetime(2026, 6, 4)) is None
