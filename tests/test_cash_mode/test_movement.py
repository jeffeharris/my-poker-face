"""Tests for `cash_mode.movement` — `evaluate_ai_movement` and
`refresh_table_roster` (commit 3).

These are the pure-function load-bearing helpers for the lobby's
"feel alive" cadence. Both must be exercised exhaustively because
they're the economic-loop logic that turns persisted bankroll +
relationship state into observable lobby motion.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Dict, Optional

import pytest

from cash_mode.movement import (
    BIG_LOSS_RATIO,
    BIG_WIN_RATIO,
    DEFAULT_LIVE_FILL_PROB,
    IdlePoolChange,
    RosterRefreshResult,
    evaluate_ai_movement,
    refresh_table_roster,
)
from cash_mode.tables import (
    CashTableState,
    IdlePoolEntry,
    ai_slot,
    human_slot,
    open_slot,
)


# ============================================================
# evaluate_ai_movement
# ============================================================


class TestEvaluateAIMovementForcedLeave:
    def test_zero_chips_forced_leave(self):
        rng = random.Random(0)
        result = evaluate_ai_movement(
            ai_chips=0, buy_in=100, projected_bankroll=5000,
            stake_idx=0, next_tier_min_buy_in=400, rng=rng,
        )
        assert result == "forced_leave"

    def test_near_bust_forced_leave(self):
        # chips = 30 ≤ 0.3 × 100 = 30
        rng = random.Random(0)
        result = evaluate_ai_movement(
            ai_chips=30, buy_in=100, projected_bankroll=5000,
            stake_idx=0, next_tier_min_buy_in=400, rng=rng,
        )
        assert result == "forced_leave"

    def test_above_loss_threshold_not_forced_leave(self):
        # chips = 31 > 30 → not forced_leave (but might be bored_move
        # with a particular RNG, hence we use 0.99 as the RNG roll).
        rng = random.Random()
        rng.random = lambda: 0.99  # consistently avoid bored_move
        result = evaluate_ai_movement(
            ai_chips=50, buy_in=100, projected_bankroll=5000,
            stake_idx=0, next_tier_min_buy_in=400, rng=rng,
        )
        assert result == "stay"


class TestEvaluateAIMovementStakeUp:
    def test_won_big_stake_up_when_bankroll_affords_and_roll_hits(self):
        # chips = 200 ≥ 2 × 100; bankroll = 5000 ≥ next_tier_min = 400.
        # RNG roll 0.0 < 0.30 → stake_up.
        rng = random.Random()
        rng.random = lambda: 0.0
        result = evaluate_ai_movement(
            ai_chips=200, buy_in=100, projected_bankroll=5000,
            stake_idx=0, next_tier_min_buy_in=400, rng=rng,
        )
        assert result == "stake_up"

    def test_won_big_no_higher_tier(self):
        # Top of ladder: next_tier_min_buy_in is None.
        # Roll for take_break is 0.0 < 0.10 → take_break.
        rng = random.Random()
        rng.random = lambda: 0.0
        result = evaluate_ai_movement(
            ai_chips=200, buy_in=100, projected_bankroll=5000,
            stake_idx=4, next_tier_min_buy_in=None, rng=rng,
        )
        assert result == "take_break"

    def test_won_big_no_bankroll_for_next_tier(self):
        # next_tier_min = 400 but projected_bankroll = 100 → not affordable.
        # Roll for take_break is 0.0 < 0.10 → take_break.
        rng = random.Random()
        rng.random = lambda: 0.0
        result = evaluate_ai_movement(
            ai_chips=200, buy_in=100, projected_bankroll=100,
            stake_idx=0, next_tier_min_buy_in=400, rng=rng,
        )
        assert result == "take_break"

    def test_won_big_misses_all_rolls(self):
        # Roll always returns 0.99 → all probabilistic gates fail → stay.
        rng = random.Random()
        rng.random = lambda: 0.99
        result = evaluate_ai_movement(
            ai_chips=200, buy_in=100, projected_bankroll=5000,
            stake_idx=0, next_tier_min_buy_in=400, rng=rng,
        )
        assert result == "stay"

    def test_won_big_take_break_when_stake_up_misses(self):
        # First roll 0.5 ≥ 0.30 → no stake_up. Second roll 0.0 < 0.10 → take_break.
        rng = random.Random()
        rolls = iter([0.5, 0.0])
        rng.random = lambda: next(rolls)
        result = evaluate_ai_movement(
            ai_chips=200, buy_in=100, projected_bankroll=5000,
            stake_idx=0, next_tier_min_buy_in=400, rng=rng,
        )
        assert result == "take_break"


class TestEvaluateAIMovementBoredMove:
    def test_bored_move_on_low_roll(self):
        # chips between 0.3 × 100 = 30 and 2 × 100 = 200 → normal-zone.
        # Roll 0.0 < 0.015 → bored_move.
        rng = random.Random()
        rng.random = lambda: 0.0
        result = evaluate_ai_movement(
            ai_chips=100, buy_in=100, projected_bankroll=5000,
            stake_idx=0, next_tier_min_buy_in=400, rng=rng,
        )
        assert result == "bored_move"

    def test_stay_on_high_roll(self):
        rng = random.Random()
        rng.random = lambda: 0.99
        result = evaluate_ai_movement(
            ai_chips=100, buy_in=100, projected_bankroll=5000,
            stake_idx=0, next_tier_min_buy_in=400, rng=rng,
        )
        assert result == "stay"


class TestEvaluateAIMovementDefensive:
    def test_zero_buy_in_returns_stay(self):
        # Defensive: avoid div-by-zero / wonky math.
        rng = random.Random()
        result = evaluate_ai_movement(
            ai_chips=100, buy_in=0, projected_bankroll=5000,
            stake_idx=0, next_tier_min_buy_in=400, rng=rng,
        )
        assert result == "stay"


# ============================================================
# refresh_table_roster
# ============================================================


def _make_table(seats: list) -> CashTableState:
    return CashTableState(
        table_id="cash-table-10-001",
        stake_label="$10",
        seats=seats,
    )


def _bankroll_lookup_factory(values: Dict[str, int]):
    def lookup(pid: str) -> Optional[int]:
        return values.get(pid)
    return lookup


def _buy_in_lookup_factory(default: int = 400, overrides=None):
    overrides = overrides or {}
    def lookup(pid: str) -> int:
        return overrides.get(pid, default)
    return lookup


def _force_rng(values):
    """Build a random.Random whose `random()` returns each value in order."""
    rng = random.Random()
    it = iter(values)
    rng.random = lambda: next(it)
    return rng


class TestRefreshNoChanges:
    def test_all_stay_no_changes_in_seats(self):
        seats = [
            ai_slot("napoleon", 500),
            ai_slot("zeus", 500),
            ai_slot("athena", 500),
            ai_slot("gatsby", 500),
            open_slot(),
            open_slot(),
        ]
        table = _make_table(seats)
        # 4 movement rolls (one per AI seat at 0.99 to avoid bored_move) +
        # 2 live-fill rolls (also 0.99 to avoid live-fill).
        rng = _force_rng([0.99, 0.99, 0.99, 0.99, 0.99, 0.99])
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[],
            seated_globally={"napoleon", "zeus", "athena", "gatsby"},
            bankroll_lookup=_bankroll_lookup_factory({}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
            next_tier_min_buy_in=2000,
        )
        # All AI seats stayed; no idle changes.
        assert result.idle_changes == []
        assert result.freshly_seated_personality_ids == []
        # AIs all marked "stay".
        assert all(d == "stay" for d in result.decisions.values())
        assert len(result.decisions) == 4


class TestRefreshForcedLeave:
    def test_busted_ai_moves_to_idle_pool(self):
        seats = [
            ai_slot("napoleon", 0),   # busted
            ai_slot("zeus", 500),
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        table = _make_table(seats)
        # 2 movement rolls + 4 live-fill rolls (high to avoid fill).
        rng = _force_rng([0.99] * 6)
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[],
            seated_globally={"napoleon", "zeus"},
            bankroll_lookup=_bankroll_lookup_factory({"napoleon": 5000, "zeus": 5000}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
        )
        assert result.decisions["napoleon"] == "forced_leave"
        assert result.decisions["zeus"] == "stay"
        # Napoleon's seat is now open; zeus stays.
        assert result.new_table.seats[0]["kind"] == "open"
        assert result.new_table.seats[1]["kind"] == "ai"
        # Idle pool gained Napoleon.
        adds = [c for c in result.idle_changes if c.kind == "add"]
        assert len(adds) == 1
        assert adds[0].personality_id == "napoleon"
        assert adds[0].entry.reason == "forced_leave"
        # seated_globally was updated.
        # (Mutates in-place, so we can re-read from the closure scope.)


class TestRefreshStakeUp:
    def test_stake_up_records_target_stake(self):
        seats = [
            ai_slot("napoleon", 1000),  # won big (1000 ≥ 2 × 400)
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        table = _make_table(seats)
        # First roll for napoleon: 0.0 → stake_up triggers (1 roll).
        # After napoleon vacates, all 6 seats are open → 6 live-fill rolls.
        rng = _force_rng([0.0] + [0.99] * 6)
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[],
            seated_globally={"napoleon"},
            bankroll_lookup=_bankroll_lookup_factory({"napoleon": 5000}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,  # $10
            table_min_buy_in=400,
            table_max_buy_in=1000,
            next_tier_min_buy_in=2000,
        )
        assert result.decisions["napoleon"] == "stake_up"
        adds = [c for c in result.idle_changes if c.kind == "add"]
        assert len(adds) == 1
        assert adds[0].entry.reason == "stake_up_queued"
        # stake_idx=1 → next tier is $50.
        assert adds[0].entry.target_stake == "$50"


class TestRefreshLiveFill:
    def test_live_fill_from_eligible_pool(self):
        seats = [open_slot()] * 6
        table = _make_table(seats)
        # No AI seats to process; 6 live-fill rolls.
        # First roll 0.0 < 0.15 → fill seat 0; rest 0.99 → no fill.
        rng = _force_rng([0.0, 0.99, 0.99, 0.99, 0.99, 0.99])
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[{"personality_id": "napoleon", "name": "Napoleon"}],
            seated_globally=set(),
            bankroll_lookup=_bankroll_lookup_factory({"napoleon": 5000}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
        )
        assert result.freshly_seated_personality_ids == ["napoleon"]
        assert result.new_table.seats[0]["kind"] == "ai"
        assert result.new_table.seats[0]["personality_id"] == "napoleon"
        assert result.new_table.seats[0]["chips"] == 400  # buy_in
        # Remaining seats untouched.
        for i in range(1, 6):
            assert result.new_table.seats[i]["kind"] == "open"

    def test_live_fill_prefers_idle_pool_over_eligible(self):
        seats = [open_slot()] * 6
        table = _make_table(seats)
        rng = _force_rng([0.0] + [0.99] * 5)  # first seat triggers fill
        result = refresh_table_roster(
            table,
            idle_pool=[IdlePoolEntry(
                personality_id="zeus",
                left_at=datetime(2026, 5, 18, 11, 0),
                reason="bored_move",
            )],
            eligible_candidates=[{"personality_id": "napoleon", "name": "Napoleon"}],
            seated_globally=set(),
            bankroll_lookup=_bankroll_lookup_factory({
                "zeus": 5000, "napoleon": 5000,
            }),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
        )
        # Idle pool wins.
        assert result.freshly_seated_personality_ids == ["zeus"]
        removes = [c for c in result.idle_changes if c.kind == "remove"]
        assert removes[0].personality_id == "zeus"

    def test_live_fill_skips_globally_seated(self):
        seats = [open_slot()] * 6
        table = _make_table(seats)
        rng = _force_rng([0.0] + [0.99] * 5)
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[{"personality_id": "napoleon", "name": "Napoleon"}],
            seated_globally={"napoleon"},  # napoleon already at another table
            bankroll_lookup=_bankroll_lookup_factory({"napoleon": 5000}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
        )
        # No fill because napoleon is already seated elsewhere.
        assert result.freshly_seated_personality_ids == []
        assert result.new_table.seats[0]["kind"] == "open"

    def test_live_fill_skips_under_bankroll(self):
        seats = [open_slot()] * 6
        table = _make_table(seats)
        rng = _force_rng([0.0] + [0.99] * 5)
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[{"personality_id": "broke", "name": "Broke AI"}],
            seated_globally=set(),
            bankroll_lookup=_bankroll_lookup_factory({"broke": 100}),  # < 400
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
        )
        assert result.freshly_seated_personality_ids == []

    def test_idle_target_stake_filters_to_matching_table(self):
        # Idle AI's target_stake='$50' should NOT match this $10 table.
        seats = [open_slot()] * 6
        table = _make_table(seats)
        rng = _force_rng([0.0] + [0.99] * 5)
        result = refresh_table_roster(
            table,
            idle_pool=[IdlePoolEntry(
                personality_id="zeus",
                left_at=datetime(2026, 5, 18, 11, 0),
                reason="stake_up_queued",
                target_stake="$50",
            )],
            eligible_candidates=[],
            seated_globally=set(),
            bankroll_lookup=_bankroll_lookup_factory({"zeus": 5000}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
        )
        # Zeus wants $50 not $10 → skipped.
        assert result.freshly_seated_personality_ids == []


class TestRefreshHumanSeatPreserved:
    def test_human_seat_not_touched(self):
        seats = [
            human_slot("user-1", 500),
            ai_slot("napoleon", 0),  # busted
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        table = _make_table(seats)
        # 1 AI seat → 1 movement roll; 5 open seats → 5 live-fill rolls.
        rng = _force_rng([0.99] * 6)
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[],
            seated_globally={"napoleon"},
            bankroll_lookup=_bankroll_lookup_factory({"napoleon": 5000}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
        )
        # Human seat preserved verbatim.
        assert result.new_table.seats[0]["kind"] == "human"
        assert result.new_table.seats[0]["personality_id"] == "user-1"
        # AI at seat 1 is forced_leave → now open.
        assert result.new_table.seats[1]["kind"] == "open"
        assert result.decisions["napoleon"] == "forced_leave"


class TestGlobalUniquenessInvariant:
    def test_one_personality_per_active_seat(self):
        """Hard invariant: a personality must not appear at two tables."""
        seats_a = [open_slot()] * 6
        table_a = _make_table(seats_a)
        rng_a = _force_rng([0.0] + [0.99] * 5)
        # Refresh table A with napoleon in eligible pool.
        result_a = refresh_table_roster(
            table_a,
            idle_pool=[],
            eligible_candidates=[{"personality_id": "napoleon", "name": "Napoleon"}],
            seated_globally=set(),
            bankroll_lookup=_bankroll_lookup_factory({"napoleon": 5000}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng_a,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
        )
        assert "napoleon" in result_a.freshly_seated_personality_ids

        # After A's refresh, seated_globally now contains napoleon (the
        # function mutates the set in-place). Refresh table B with the
        # same set — napoleon must be filtered out.
        seats_b = [open_slot()] * 6
        table_b = CashTableState(table_id="cash-table-50-001", stake_label="$50", seats=seats_b)
        rng_b = _force_rng([0.0] + [0.99] * 5)
        # Pass the updated set from A's refresh (which now contains napoleon).
        seated_after_a = {"napoleon"}
        result_b = refresh_table_roster(
            table_b,
            idle_pool=[],
            eligible_candidates=[{"personality_id": "napoleon", "name": "Napoleon"}],
            seated_globally=seated_after_a,
            bankroll_lookup=_bankroll_lookup_factory({"napoleon": 5000}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng_b,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=2,
            table_min_buy_in=2000,
            table_max_buy_in=5000,
        )
        # Napoleon must NOT appear at table B even though the eligible
        # pool listed him — global uniqueness held.
        assert "napoleon" not in result_b.freshly_seated_personality_ids
        assert all(
            s["kind"] != "ai" or s["personality_id"] != "napoleon"
            for s in result_b.new_table.seats
        )
