"""Unit tests for the field-relative liquid-wealth snapshot."""

import random
from datetime import datetime

import pytest

from cash_mode.ai_side_hustle import compute_field_hustle_amount
from cash_mode.field_wealth import (
    FieldWealthSnapshot,
    build_field_wealth_snapshot,
)


def snap(d):
    return FieldWealthSnapshot.from_liquid(d)


class TestSnapshotStats:
    def test_empty(self):
        s = snap({})
        assert s.is_empty()
        assert s.median() == 0
        assert s.percentile(0.5) == 0.0
        assert s.concentration("x") == 0.0
        assert s.pct_rank("x") == 0.0

    def test_median_odd_even(self):
        assert snap({"a": 10, "b": 20, "c": 30}).median() == 20
        assert snap({"a": 10, "b": 30}).median() == 20  # mean of two middles

    def test_concentration(self):
        s = snap({"poor": 1_000, "mid": 10_000, "rich": 100_000})
        # median is 10_000
        assert s.median() == 10_000
        assert s.concentration("rich") == pytest.approx(10.0)
        assert s.concentration("mid") == pytest.approx(1.0)
        assert s.concentration("poor") == pytest.approx(0.1)
        assert s.concentration("absent") == 0.0

    def test_concentration_zero_median(self):
        # All-zero field → no concentration signal (avoids div-by-zero).
        assert snap({"a": 0, "b": 0}).concentration("a") == 0.0

    def test_pct_rank_orders_bottom_to_top(self):
        s = snap({"a": 1, "b": 2, "c": 3, "d": 4})
        assert s.pct_rank("a") == pytest.approx(0.25)
        assert s.pct_rank("d") == pytest.approx(1.0)
        assert s.pct_rank("absent") == 0.0

    def test_percentile_interpolates(self):
        s = snap({f"p{i}": v for i, v in enumerate([0, 100])})
        assert s.percentile(0.0) == 0.0
        assert s.percentile(1.0) == 100.0
        assert s.percentile(0.5) == pytest.approx(50.0)


class _FakeBankrollRepo:
    def __init__(self, chips_by_pid):
        self._chips = chips_by_pid

    def iter_personality_ids_with_bankrolls(self, *, sandbox_id):
        return list(self._chips)

    def load_ai_bankroll_current(self, pid, *, sandbox_id, now):
        return self._chips.get(pid, 0)


class _FakeTable:
    def __init__(self, seats):
        self.seats = seats


class _FakeCashTableRepo:
    def __init__(self, tables):
        self._tables = tables

    def list_all_tables(self, *, sandbox_id):
        return self._tables


class TestFactory:
    def test_liquid_is_bankroll_plus_seat_and_excludes_fish(self):
        bankroll = _FakeBankrollRepo({"hero": 5_000, "seated": 2_000, "fish": 9_999})
        tables = _FakeCashTableRepo(
            [
                _FakeTable(
                    [
                        {"kind": "ai", "personality_id": "seated", "chips": 40_000},
                        {"kind": "ai", "personality_id": "fish", "chips": 1_000},
                        {"kind": "human", "personality_id": None, "chips": 7},
                        {"kind": "open"},
                    ]
                )
            ]
        )
        s = build_field_wealth_snapshot(
            bankroll_repo=bankroll,
            cash_table_repo=tables,
            sandbox_id="sb",
            now=datetime(2026, 1, 1),
            fish_ids={"fish"},
        )
        assert s is not None
        # seated AI's wealth folds in its 40k seat stack; fish excluded.
        assert s.liquid_chips == {"hero": 5_000, "seated": 42_000}

    def test_none_repo_returns_none(self):
        assert (
            build_field_wealth_snapshot(
                bankroll_repo=None,
                cash_table_repo=None,
                sandbox_id="sb",
                now=datetime(2026, 1, 1),
                fish_ids=set(),
            )
            is None
        )


class TestFieldHustleAmount:
    """The field-relative side-hustle target (vs. own-start)."""

    def test_below_field_target_earns(self):
        # 3k below a 25k field target → positive, capped to the gap.
        amt = compute_field_hustle_amount(3_000, 25_000, random.Random(1))
        assert 0 < amt <= 25_000 - 3_000

    def test_at_or_above_target_earns_nothing(self):
        assert compute_field_hustle_amount(25_000, 25_000, random.Random(1)) == 0
        assert compute_field_hustle_amount(40_000, 25_000, random.Random(1)) == 0

    def test_zero_target_is_noop(self):
        assert compute_field_hustle_amount(0, 0, random.Random(1)) == 0

    def test_earns_even_above_own_starting_baseline(self):
        # The whole point of field mode: a persona at/above its OWN low
        # baseline still earns when it's poor by the FIELD's standard.
        # (own-start compute_hustle_amount would return 0 here.)
        amt = compute_field_hustle_amount(6_000, 25_000, random.Random(2))
        assert amt > 0
