"""Tests for `cash_mode.movement` — pressure-driven decisions + roster refresh.

Covers:
  - Forced-leave hard floor at 0.3 × min_buy_in.
  - Pressure formula components (stake_up, short, detached, tenure)
    and the dominant-factor branching.
  - Leave-vs-rebuy split for short-stack pressure.
  - Rebuy amount bucketing (min/mid/max) under different state.
  - Per-table leave cooldown.
  - `refresh_table_roster` integration: vacations, live-fill, idle-pool
    filtering, human-seat invariance, defer-on-vacate.

Pure helpers — rng is the only side effect, and the test fixtures
override `rng.random` to drive specific paths deterministically.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Dict, Optional

import pytest

from cash_mode.movement import (
    DEFAULT_LIVE_FILL_PROB,
    FORCED_LEAVE_RATIO,
    LEAVE_K,
    MIN_COOLDOWN_SECONDS,
    RESEAT_RECOVERY_FLOOR,
    VICE_COOLDOWN_SECONDS,
    W_DETACHED,
    W_SHORT,
    W_STAKE_UP,
    W_TENURE,
    MovementContext,
    RebuyChange,
    RosterRefreshResult,
    _coerce_fish_movement,
    _coerce_predator_retention,
    clear_cooldowns,
    compute_leave_cooldown_seconds,
    compute_leave_pressure,
    decide_leave_or_rebuy,
    evaluate_ai_movement,
    is_in_cooldown,
    is_in_vice_cooldown,
    pick_rebuy_amount,
    project_idle_energy,
    record_leave_cooldown,
    record_vice_cooldown,
    refresh_table_roster,
    reseat_readiness,
)
from cash_mode.tables import (
    CashTableState,
    IdlePoolEntry,
    ai_slot,
    human_slot,
    open_slot,
)


@pytest.fixture(autouse=True)
def _clear_cooldown_state():
    """Per-test isolation — `_recent_leaves` is module-level and would
    otherwise leak cooldown entries between tests."""
    clear_cooldowns()
    yield
    clear_cooldowns()


def _neutral_ctx(**overrides) -> MovementContext:
    base = dict(
        ai_chips=1500,
        min_buy_in=1000,
        max_buy_in=2500,
        projected_bankroll=5000,
        stake_idx=1,
        next_tier_min_buy_in=2000,
        energy=0.7,  # fresh — no tenure pressure
        zone="neutral",
        hands_in_detached_zone=0,
        emotional_intensity=0.0,
    )
    base.update(overrides)
    return MovementContext(**base)


# ============================================================
# Forced leave (hard floor)
# ============================================================


class TestForcedLeave:
    def test_zero_chips(self):
        ctx = _neutral_ctx(ai_chips=0)
        assert evaluate_ai_movement(ctx, random.Random(0)) == "forced_leave"

    def test_exactly_at_floor(self):
        # 300 = 0.3 × 1000
        ctx = _neutral_ctx(ai_chips=300)
        assert evaluate_ai_movement(ctx, random.Random(0)) == "forced_leave"

    def test_just_above_floor(self):
        # 301 > floor → forced_leave does NOT fire (may roll other decisions)
        ctx = _neutral_ctx(ai_chips=301)
        # Across many seeds, no forced_leave should appear above the floor.
        for seed in range(50):
            assert evaluate_ai_movement(ctx, random.Random(seed)) != "forced_leave"

    def test_floor_overrides_pressure(self):
        # Even with massive pressure from low energy, busted = forced_leave.
        ctx = _neutral_ctx(ai_chips=100, energy=0.0)
        assert evaluate_ai_movement(ctx, random.Random(0)) == "forced_leave"


# ============================================================
# Pressure formula
# ============================================================


class TestPressureFormula:
    def test_neutral_no_pressure(self):
        # Genuinely neutral: bankroll == max buy-in, so the wealth-driven
        # stake_up climb (wealth_over_tier) reads 0 too. (The default
        # _neutral_ctx is 2× over-rolled, which now carries a tiny climb
        # signal — covered separately below.)
        ctx = _neutral_ctx(energy=0.7, projected_bankroll=2500, max_buy_in=2500)
        p = compute_leave_pressure(ctx)
        assert p["stake_up"] == 0.0
        assert p["short"] == 0.0
        assert p["detached"] == 0.0
        assert p["tenure"] == 0.0
        assert p["dead"] == 0.0

    def test_stake_up_pressure_scales_with_max_buy_in(self):
        # Isolate the SEAT-stack stake_up source (bankroll == max so the
        # wealth climb term contributes 0). stack = 2x max → stake_up_raw = 1.0
        ctx = _neutral_ctx(ai_chips=5000, max_buy_in=2500, projected_bankroll=2500)
        p = compute_leave_pressure(ctx)
        assert p["stake_up"] == W_STAKE_UP * 1.0
        # At exactly max, no stake_up pressure.
        ctx2 = _neutral_ctx(ai_chips=2500, max_buy_in=2500, projected_bankroll=2500)
        assert compute_leave_pressure(ctx2)["stake_up"] == 0.0

    def test_short_pressure_scales_with_min_buy_in(self):
        # stack = 0.5 × min → short_raw = 0.5
        ctx = _neutral_ctx(ai_chips=500, min_buy_in=1000)
        p = compute_leave_pressure(ctx)
        assert p["short"] == W_SHORT * 0.5
        # At exactly min, no short pressure.
        ctx2 = _neutral_ctx(ai_chips=1000, min_buy_in=1000)
        assert compute_leave_pressure(ctx2)["short"] == 0.0

    def test_detached_pressure_requires_zone(self):
        # Hands in detached zone irrelevant when zone != 'detached'.
        ctx = _neutral_ctx(zone="neutral", hands_in_detached_zone=20)
        assert compute_leave_pressure(ctx)["detached"] == 0.0
        # With detached zone, pressure scales with hands (capped by /8).
        ctx2 = _neutral_ctx(zone="detached", hands_in_detached_zone=8)
        assert compute_leave_pressure(ctx2)["detached"] == W_DETACHED * 1.0

    def test_tenure_gated_at_neutral_energy(self):
        # Energy >= 0.5 → no tenure pressure (sticky neutral AIs).
        for energy in (0.5, 0.6, 0.7, 0.9, 1.0):
            ctx = _neutral_ctx(energy=energy)
            assert compute_leave_pressure(ctx)["tenure"] == 0.0
        # Below 0.5, tenure ramps to 1.0 at energy=0.
        ctx_low = _neutral_ctx(energy=0.0)
        assert compute_leave_pressure(ctx_low)["tenure"] == W_TENURE * 1.0


# ============================================================
# Dominant factor → decision routing
# ============================================================


class TestDecisionRouting:
    def test_neutral_ai_stays(self):
        ctx = _neutral_ctx(energy=0.7)
        # No pressure → always stay.
        for seed in range(50):
            assert evaluate_ai_movement(ctx, random.Random(seed)) == "stay"

    def test_won_big_routes_to_stake_up_when_affordable(self):
        ctx = _neutral_ctx(
            ai_chips=10000,
            max_buy_in=2500,
            min_buy_in=1000,
            projected_bankroll=10000,
            next_tier_min_buy_in=2000,
        )
        from collections import Counter

        outcomes = Counter(evaluate_ai_movement(ctx, random.Random(seed)) for seed in range(500))
        # Most stays + some stake_ups; no take_break, no rebuy.
        assert outcomes["stake_up"] > 0
        assert outcomes.get("rebuy", 0) == 0
        assert outcomes.get("take_break", 0) == 0
        assert outcomes.get("bored_move", 0) == 0

    def test_won_big_routes_to_take_break_when_top_tier(self):
        ctx = _neutral_ctx(
            ai_chips=10000,
            max_buy_in=2500,
            min_buy_in=1000,
            projected_bankroll=10000,
            next_tier_min_buy_in=None,
        )
        from collections import Counter

        outcomes = Counter(evaluate_ai_movement(ctx, random.Random(seed)) for seed in range(500))
        # No higher tier → leaves degrade to take_break.
        assert outcomes["take_break"] > 0
        assert outcomes.get("stake_up", 0) == 0

    def test_detached_routes_to_bored_move(self):
        ctx = _neutral_ctx(zone="detached", hands_in_detached_zone=12)
        from collections import Counter

        outcomes = Counter(evaluate_ai_movement(ctx, random.Random(seed)) for seed in range(500))
        # All leaves from detached zone should be bored_move.
        non_stay = sum(v for k, v in outcomes.items() if k != "stay")
        assert outcomes.get("bored_move", 0) == non_stay
        assert non_stay > 0

    def test_short_stack_routes_to_rebuy_or_take_break(self):
        ctx = _neutral_ctx(
            ai_chips=400,
            min_buy_in=1000,  # 0.4× min → above floor, short pressure
            projected_bankroll=15000,
            energy=0.7,
        )
        from collections import Counter

        outcomes = Counter(evaluate_ai_movement(ctx, random.Random(seed)) for seed in range(500))
        # Should see rebuy or take_break, no bored_move/stake_up.
        assert outcomes.get("rebuy", 0) + outcomes.get("take_break", 0) > 0
        assert outcomes.get("stake_up", 0) == 0
        assert outcomes.get("bored_move", 0) == 0


# ============================================================
# Leave vs rebuy split
# ============================================================


class TestLeaveVsRebuy:
    def test_flush_bankroll_high_energy_prefers_rebuy(self):
        ctx = _neutral_ctx(
            ai_chips=400,
            min_buy_in=1000,
            projected_bankroll=20000,
            energy=0.9,
        )
        from collections import Counter

        outcomes = Counter(decide_leave_or_rebuy(ctx, random.Random(seed)) for seed in range(500))
        assert outcomes["rebuy"] > outcomes["leave"]

    def test_low_energy_low_bankroll_prefers_leave(self):
        ctx = _neutral_ctx(
            ai_chips=400,
            min_buy_in=1000,
            projected_bankroll=500,
            energy=0.2,
        )
        from collections import Counter

        outcomes = Counter(decide_leave_or_rebuy(ctx, random.Random(seed)) for seed in range(500))
        assert outcomes["leave"] > outcomes["rebuy"]


# ============================================================
# Rebuy bucket bias
# ============================================================


class TestRebuyAmount:
    def test_flush_bankroll_biases_max_bucket(self):
        ctx = _neutral_ctx(
            min_buy_in=1000,
            max_buy_in=2500,
            projected_bankroll=25000,
            energy=0.9,
        )
        amounts = [pick_rebuy_amount(ctx, random.Random(seed)) for seed in range(500)]
        max_picks = sum(1 for a in amounts if a == 2500)
        min_picks = sum(1 for a in amounts if a == 1000)
        # Flush bankroll + high energy → max bucket beats min.
        assert max_picks > min_picks

    def test_low_energy_biases_min_bucket(self):
        ctx = _neutral_ctx(
            min_buy_in=1000,
            max_buy_in=2500,
            projected_bankroll=2000,
            energy=0.1,
        )
        amounts = [pick_rebuy_amount(ctx, random.Random(seed)) for seed in range(500)]
        min_picks = sum(1 for a in amounts if a == 1000)
        max_picks = sum(1 for a in amounts if a == 2500)
        # Tired AI with low bankroll → min beats max comfortably.
        assert min_picks > max_picks * 2

    def test_amounts_match_buckets(self):
        ctx = _neutral_ctx(min_buy_in=1000, max_buy_in=2500)
        # Possible amounts are min / mid / max.
        expected = {1000, 1750, 2500}
        for seed in range(50):
            assert pick_rebuy_amount(ctx, random.Random(seed)) in expected


# ============================================================
# Cooldown
# ============================================================


class TestCooldown:
    def test_record_and_check(self):
        now = datetime(2026, 5, 19, 12, 0, 0)
        record_leave_cooldown("table-A", "napoleon", cooldown_seconds=30, now=now)
        # Immediately within the window → in cooldown.
        assert is_in_cooldown("table-A", "napoleon", now) is True
        # After the window → no cooldown.
        later = now + timedelta(seconds=31)
        assert is_in_cooldown("table-A", "napoleon", later) is False

    def test_cooldown_is_per_table(self):
        now = datetime(2026, 5, 19, 12, 0, 0)
        record_leave_cooldown("table-A", "napoleon", cooldown_seconds=60, now=now)
        # Same AI at a different table → no cooldown.
        assert is_in_cooldown("table-B", "napoleon", now) is False

    def test_compute_cooldown_scales_with_state(self):
        rng_fixed = random.Random()
        rng_fixed.random = lambda: 0.5  # constant for repeatability
        # Fresh AI with flush bankroll → minimal cooldown.
        ctx_fresh = _neutral_ctx(
            projected_bankroll=20000,
            energy=0.9,
            emotional_intensity=0.0,
        )
        # Tilted AI with depleted bankroll → larger cooldown.
        ctx_drained = _neutral_ctx(
            projected_bankroll=200,
            energy=0.1,
            emotional_intensity=0.9,
        )
        cd_fresh = compute_leave_cooldown_seconds(ctx_fresh, rng_fixed)
        rng_fixed.random = lambda: 0.5
        cd_drained = compute_leave_cooldown_seconds(ctx_drained, rng_fixed)
        assert cd_fresh >= MIN_COOLDOWN_SECONDS
        assert cd_drained > cd_fresh


# ============================================================
# refresh_table_roster integration
# ============================================================


def _make_table(seats: list, table_id: str = "cash-table-10-001") -> CashTableState:
    return CashTableState(
        table_id=table_id,
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


def _neutral_psych(_pid: str):
    return {
        "energy": 0.7,
        "zone": "neutral",
        "hands_in_detached_zone": 0,
        "emotional_intensity": 0.0,
    }


class TestRefreshNoChanges:
    def test_neutral_table_stays_put(self):
        seats = [
            ai_slot("napoleon", 500),
            ai_slot("zeus", 500),
            ai_slot("athena", 500),
            ai_slot("gatsby", 500),
            open_slot(),
            open_slot(),
        ]
        table = _make_table(seats)
        # No pressure (neutral) → no rolls consumed for movement.
        # 2 live-fill rolls at 0.99 → no fills.
        rng = _force_rng([0.99, 0.99])
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
            psych_lookup=_neutral_psych,
        )
        assert result.idle_changes == []
        assert result.freshly_seated_personality_ids == []
        assert result.rebuy_changes == []
        assert all(d == "stay" for d in result.decisions.values())


class TestRefreshFishAreCasinoBound:
    """Fish are real `archetype='fish'` personas with pool-funded
    bankrolls. They run normal movement (so they can re-buy or go home)
    but are casino-bound — never spuriously evicted for being "broke"
    (they have a bankroll) and never tier-drift. See
    CASH_MODE_FISH_AS_PERSONAS.md.
    """

    def test_fish_with_bankroll_is_not_evicted(self):
        """A fish carries a real (pool-funded) bankroll, so the pressure
        formulas don't treat it as broke. With a comfortable stack it
        stays put, identified by the `archetype` stamp (the inline
        `ephemeral_personality` blob is gone)."""
        seats = [
            {
                "kind": "ai",
                "personality_id": "vacation_greg",
                "chips": 600,  # between min 400 and max 1000 — no short pressure
                "archetype": "fish",
            },
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        table = _make_table(seats)
        # 5 open seats → up to 5 live-fill rolls; high values block fill.
        rng = _force_rng([0.99] * 5)
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[],
            seated_globally={"vacation_greg"},
            # Healthy pool-funded bankroll — not broke, not evict-eligible.
            bankroll_lookup=_bankroll_lookup_factory({"vacation_greg": 1800}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
            psych_lookup=_neutral_psych,
        )
        assert result.decisions["vacation_greg"] == "stay"
        # Seat preserved with the archetype stamp intact.
        seat0 = result.new_table.seats[0]
        assert seat0["kind"] == "ai"
        assert seat0["personality_id"] == "vacation_greg"
        assert seat0.get("archetype") == "fish"

    def test_fish_rebuy_preserves_archetype_stamp(self):
        """A reloading fish must KEEP its `archetype='fish'` stamp.

        The stamp gates fish movement (no wander) + predator retention +
        the chip-return paths. A bare `ai_slot` rewrite on rebuy would
        silently de-stamp the fish — harmless at a casino (zombie-reclaim
        re-seats a fresh fish) but fatal for a whale at a lobby table,
        where it'd become a wandering grinder holding a deep pool-funded
        stack. A fish reloads until its bankroll is dry, so this fires
        often. Here a near-busted content fish (<= 0.3x min buy-in →
        forced_leave hard floor) reloads from a healthy bankroll.
        """
        seats = [
            {
                "kind": "ai",
                "personality_id": "vacation_greg",
                "chips": 50,  # <= 0.3 * min (400) → forced_leave hard floor
                "archetype": "fish",
            },
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        table = _make_table(seats)
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[],
            seated_globally={"vacation_greg"},
            # Healthy pool-funded bankroll → forced_leave is coerced to rebuy.
            bankroll_lookup=_bankroll_lookup_factory({"vacation_greg": 1800}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=random.Random(0),
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
            psych_lookup=_neutral_psych,
        )
        assert result.decisions["vacation_greg"] == "rebuy"
        seat0 = result.new_table.seats[0]
        assert seat0["kind"] == "ai"
        assert seat0["personality_id"] == "vacation_greg"
        # The stamp survives the reload — still a fish after rebuying.
        assert seat0.get("archetype") == "fish"
        # And it actually topped up off the short stack.
        assert seat0["chips"] > 50


class TestCoercePredatorRetention:
    """Grinders won't *drift* off a seated fish out of boredom, but the
    `stake_up` graduation exit always stands so a winner books the win and
    moves up (no permanent hoarding). Rotation exits (take_break/bust) and
    tired predators still leave."""

    FRESH = 0.8  # energy >= CASINO_PREDATOR_FATIGUE_FLOOR → boredom retained
    TIRED = 0.1  # energy < floor → released to cycle out

    def test_stake_up_not_suppressed_at_fish_table(self):
        # stake_up is the healthy release: farm enough, then move up. It is
        # never gated by retention — gating it caused permanent hoarding.
        assert _coerce_predator_retention("stake_up", True, self.FRESH) == "stake_up"

    def test_bored_move_suppressed_at_fish_table(self):
        assert _coerce_predator_retention("bored_move", True, self.FRESH) == "stay"

    def test_take_break_still_rotates_out(self):
        # Tired predators still leave — keeps the fish's chips redistributing.
        assert _coerce_predator_retention("take_break", True, self.FRESH) == "take_break"

    def test_forced_leave_still_busts(self):
        assert _coerce_predator_retention("forced_leave", True, self.FRESH) == "forced_leave"

    def test_stay_unchanged(self):
        assert _coerce_predator_retention("stay", True, self.FRESH) == "stay"

    def test_no_retention_without_fish(self):
        # No fish to farm → normal movement, predators free to move on.
        assert _coerce_predator_retention("stake_up", False, self.FRESH) == "stake_up"
        assert _coerce_predator_retention("bored_move", False, self.FRESH) == "bored_move"

    def test_tired_predator_released_to_cycle_out(self):
        # Worn down past the fatigue floor → even boredom-drift stands.
        assert _coerce_predator_retention("stake_up", True, self.TIRED) == "stake_up"
        assert _coerce_predator_retention("bored_move", True, self.TIRED) == "bored_move"

    def test_fatigue_boundary(self):
        # Only bored_move is energy-gated now (stake_up always stands), so
        # the boundary is exercised through bored_move.
        from cash_mode.movement import CASINO_PREDATOR_FATIGUE_FLOOR

        at = CASINO_PREDATOR_FATIGUE_FLOOR
        below = CASINO_PREDATOR_FATIGUE_FLOOR - 0.01
        assert _coerce_predator_retention("bored_move", True, at) == "stay"  # at floor → retained
        assert (
            _coerce_predator_retention("bored_move", True, below) == "bored_move"
        )  # below → released


class TestCoerceFishMovement:
    """Fish stay-and-reload until they genuinely bust.

    A fish reloads from the bankroll instead of leaving (so the whole
    pool-funded stake feeds the table). Tilt (`emotional_intensity`) changes
    how the fish *plays*, not whether it keeps its seat (0402d88c removed the
    storm-off path), so the only exit from an open casino is a real bust —
    a bankroll too thin to fund a reload.
    """

    def _ctx(
        self,
        *,
        projected_bankroll,
        emotional_intensity=0.0,
        min_buy_in=400,
        max_buy_in=1000,
        ai_chips=200,
    ):
        return MovementContext(
            ai_chips=ai_chips,
            min_buy_in=min_buy_in,
            max_buy_in=max_buy_in,
            projected_bankroll=projected_bankroll,
            stake_idx=1,
            next_tier_min_buy_in=None,
            emotional_intensity=emotional_intensity,
        )

    # --- content fish: stay and reload until the bankroll is dry ---
    def test_content_fish_take_break_reloads(self):
        ctx = self._ctx(projected_bankroll=800)  # >= min_buy_in
        assert _coerce_fish_movement("take_break", ctx, random.Random(0)) == "rebuy"

    def test_content_fish_forced_leave_reloads_while_funded(self):
        ctx = self._ctx(projected_bankroll=800)
        assert _coerce_fish_movement("forced_leave", ctx, random.Random(0)) == "rebuy"

    def test_content_fish_busts_when_bankroll_too_thin(self):
        ctx = self._ctx(projected_bankroll=100)  # < min_buy_in 400
        assert _coerce_fish_movement("forced_leave", ctx, random.Random(0)) == "forced_leave"

    def test_content_fish_take_break_stands_when_cant_reload(self):
        ctx = self._ctx(projected_bankroll=100)
        assert _coerce_fish_movement("take_break", ctx, random.Random(0)) == "take_break"

    def test_content_fish_never_wanders_or_moves_up(self):
        ctx = self._ctx(projected_bankroll=800)
        assert _coerce_fish_movement("stake_up", ctx, random.Random(0)) == "stay"
        assert _coerce_fish_movement("bored_move", ctx, random.Random(0)) == "stay"

    def test_content_fish_stay_unchanged(self):
        ctx = self._ctx(projected_bankroll=800)
        assert _coerce_fish_movement("stay", ctx, random.Random(0)) == "stay"

    # --- tilt no longer empties the seat (0402d88c) ---
    # emotional_intensity changes how a fish *plays*, not whether it keeps its
    # seat: an upset fish follows the same content path as a calm one. The only
    # way a fish leaves an open casino is a genuine bust (can't fund a reload).
    def test_upset_fish_reloads_when_funded(self):
        ctx = self._ctx(projected_bankroll=800, emotional_intensity=0.8)
        assert _coerce_fish_movement("take_break", ctx, _force_rng([0.0])) == "rebuy"
        assert _coerce_fish_movement("forced_leave", ctx, _force_rng([0.0])) == "rebuy"

    def test_upset_fish_never_storms_off_from_stay(self):
        # Tilt no longer converts a stay into a leave, at any roll.
        ctx = self._ctx(projected_bankroll=800, emotional_intensity=0.8)
        assert _coerce_fish_movement("stay", ctx, _force_rng([0.0])) == "stay"
        assert _coerce_fish_movement("stay", ctx, _force_rng([0.99])) == "stay"

    def test_upset_fish_leaves_only_on_genuine_bust(self):
        # The single exit: bankroll can't fund a reload.
        ctx = self._ctx(projected_bankroll=100, emotional_intensity=0.8)  # < min 400
        assert _coerce_fish_movement("forced_leave", ctx, _force_rng([0.0])) == "forced_leave"

    def test_tilt_does_not_change_movement(self):
        # Identical decision + RNG across the whole tilt range → identical
        # outcome. Regression guard for the FISH_TILT_LEAVE_THRESHOLD removal.
        for intensity in (0.0, 0.49, 0.5, 0.8, 1.0):
            ctx = self._ctx(projected_bankroll=800, emotional_intensity=intensity)
            assert _coerce_fish_movement("take_break", ctx, _force_rng([0.0])) == "rebuy"


class TestRefreshForcedLeave:
    def test_busted_ai_moves_to_idle_pool(self):
        seats = [
            ai_slot("napoleon", 0),
            ai_slot("zeus", 500),
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        table = _make_table(seats)
        # Napoleon forced_leave (no roll consumed); zeus stay (no roll).
        # 5 open seats → 5 live-fill rolls (high to avoid fill).
        rng = _force_rng([0.99] * 5)
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
            psych_lookup=_neutral_psych,
        )
        assert result.decisions["napoleon"] == "forced_leave"
        assert result.decisions["zeus"] == "stay"
        assert result.new_table.seats[0]["kind"] == "open"
        adds = [c for c in result.idle_changes if c.kind == "add"]
        assert len(adds) == 1
        assert adds[0].entry.reason == "forced_leave"


class TestRefreshRebuy:
    def test_short_stack_rebuy_records_chip_increase(self):
        seats = [
            ai_slot("napoleon", 300),  # 0.6× min, above forced floor
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        table = _make_table(seats)
        # Force every decision/sub-decision to "leave or rebuy" landing
        # on rebuy by stacking 0.0 rolls. With ai_chips=300 and min=500
        # short pressure dominates and the leave-vs-rebuy roll lands on
        # rebuy when bankroll is flush.
        rng = random.Random(0)
        rng.random = lambda: 0.0
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[],
            seated_globally={"napoleon"},
            bankroll_lookup=_bankroll_lookup_factory({"napoleon": 20000}),
            buy_in_lookup=_buy_in_lookup_factory(500),
            rng=rng,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=500,
            table_max_buy_in=1000,
            psych_lookup=lambda _pid: {
                "energy": 0.9,
                "zone": "neutral",
                "hands_in_detached_zone": 0,
                "emotional_intensity": 0.0,
            },
        )
        # Rebuy decision = stay seated with more chips + RebuyChange entry.
        assert result.decisions["napoleon"] == "rebuy"
        assert result.new_table.seats[0]["kind"] == "ai"
        assert result.new_table.seats[0]["chips"] > 300
        assert len(result.rebuy_changes) == 1
        rc = result.rebuy_changes[0]
        assert rc.personality_id == "napoleon"
        assert rc.amount > 0
        # Matching to_seat BankrollChange emitted for the debit channel.
        to_seats = [b for b in result.bankroll_changes if b.direction == "to_seat"]
        assert any(b.personality_id == "napoleon" and b.amount == rc.amount for b in to_seats)


class TestRefreshLiveFill:
    def test_live_fill_at_per_hand_default(self):
        seats = [open_slot()] * 6
        table = _make_table(seats)
        # First open seat: 0.0 < 0.05 → fill triggers; rest 0.99.
        rng = _force_rng([0.0] + [0.99] * 5)
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
            psych_lookup=_neutral_psych,
        )
        assert result.freshly_seated_personality_ids == ["napoleon"]
        assert result.new_table.seats[0]["personality_id"] == "napoleon"

    def test_default_live_fill_prob_is_per_hand_rate(self):
        # The constant exposed as the default must match the per-hand rate.
        assert DEFAULT_LIVE_FILL_PROB == 0.05

    def test_enable_live_fill_false_skips_fill(self):
        # Same setup as test_live_fill_at_per_hand_default (roll 0.0 WOULD
        # fill), but enable_live_fill=False → Step 2 is skipped entirely:
        # no fresh-seated, seat stays open, no bankroll/idle changes. (The
        # loop-inversion path fills globally in the lobby instead.)
        seats = [open_slot()] * 6
        table = _make_table(seats)
        rng = _force_rng([0.0] + [0.99] * 5)
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
            psych_lookup=_neutral_psych,
            enable_live_fill=False,
        )
        assert result.freshly_seated_personality_ids == []
        assert result.new_table.seats[0]["kind"] == "open"
        assert result.bankroll_changes == []
        assert result.idle_changes == []

    def test_cooldown_skips_recent_leaver_at_same_table(self):
        seats = [open_slot()] * 6
        table = _make_table(seats)
        now = datetime(2026, 5, 19, 12, 0, 0)
        # Napoleon just left table-X with a 30-second cooldown.
        record_leave_cooldown(table.table_id, "napoleon", 30, now)
        # First seat rolls a fill that would otherwise pick Napoleon.
        rng = _force_rng([0.0] + [0.99] * 5)
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[{"personality_id": "napoleon", "name": "Napoleon"}],
            seated_globally=set(),
            bankroll_lookup=_bankroll_lookup_factory({"napoleon": 5000}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=now,
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
            psych_lookup=_neutral_psych,
        )
        # Napoleon was in cooldown → no fill, no fresh-seated event.
        assert result.freshly_seated_personality_ids == []


class TestDeadTablePush:
    """refresh_table_roster computes per-table deadness and feeds it into
    each AI's MovementContext so the Phase B `dead` term fires — a casino
    that's lost its fish pushes its grinders to go find action."""

    def _grinder_seats(self):
        # Two grinders (mid stack → no short / no seat stake_up), rest open.
        return [ai_slot("g1", 600), ai_slot("g2", 600)] + [open_slot() for _ in range(4)]

    def _run(self, table_type):
        table = CashTableState(
            table_id=f"cash-{table_type}",
            stake_label="$10",
            seats=self._grinder_seats(),
            table_type=table_type,
        )
        # Per leaving grinder: a leave roll (0.05 < the dead-driven leave
        # prob → leaves) then a cooldown roll. Two grinders → 4 values.
        # Modest bankroll keeps the wealth climb silent (inside SLUM_DEADZONE)
        # so `dead` is the only pressure. (Lobby control consumes none — its
        # total pressure is 0, so evaluate returns stay before any roll.)
        rng = _force_rng([0.05, 0.5, 0.05, 0.5])
        return refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[],
            seated_globally={"g1", "g2"},
            bankroll_lookup=_bankroll_lookup_factory({"g1": 5000, "g2": 5000}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 29, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
            psych_lookup=_neutral_psych,
            enable_live_fill=False,
        )

    def test_fishless_casino_pushes_grinders_off(self):
        result = self._run("casino")
        assert result.decisions["g1"] == "bored_move"
        assert result.decisions["g2"] == "bored_move"

    def test_fishless_lobby_does_not_push(self):
        # A lobby table isn't "dead" — grinders playing each other is the
        # game, so no dead pressure → they stay.
        result = self._run("lobby")
        assert result.decisions["g1"] == "stay"
        assert result.decisions["g2"] == "stay"


class TestRefreshHumanSeatPreserved:
    def test_human_seat_not_touched(self):
        seats = [
            human_slot("player-1", 1000),
            ai_slot("napoleon", 0),  # busted
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        table = _make_table(seats)
        rng = _force_rng([0.99] * 5)
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
            psych_lookup=_neutral_psych,
        )
        # Human slot unchanged.
        assert result.new_table.seats[0]["kind"] == "human"
        assert result.new_table.seats[0]["personality_id"] == "player-1"


class TestRefreshDeferFreshlyVacated:
    def test_defer_default_is_true(self):
        # Busted AI vacates; with defer on by default the freshly-open
        # seat should NOT be filled in the same pass.
        seats = [
            ai_slot("napoleon", 0),
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ]
        table = _make_table(seats)
        # Force every live-fill roll to fire (0.0). With deferral on,
        # only the SEAT THAT WAS ALREADY OPEN (index 1+) is eligible.
        rng = _force_rng([0.0] * 6)
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[
                {"personality_id": "zeus", "name": "Zeus"},
            ],
            seated_globally={"napoleon"},
            bankroll_lookup=_bankroll_lookup_factory({"napoleon": 5000, "zeus": 5000}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 18, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
            psych_lookup=_neutral_psych,
        )
        # Napoleon's seat (index 0) is deferred → still open, no zeus there.
        assert result.new_table.seats[0]["kind"] == "open"
        # Seat 1 was an existing open → zeus fills it.
        assert result.new_table.seats[1]["kind"] == "ai"
        assert result.new_table.seats[1]["personality_id"] == "zeus"


# ---------------------------------------------------------------------------
# Idle recovery (a): energy springs back toward baseline while idle, and the
# re-seat path gates on the recovered value.
# ---------------------------------------------------------------------------


def test_project_idle_energy_recovers_toward_baseline():
    # No elapsed time → unchanged.
    assert project_idle_energy(0.2, 0.6, 0) == 0.2
    # Half an hour at the default 0.5/hr rate → +0.25.
    assert project_idle_energy(0.2, 0.6, 1800) == pytest.approx(0.45)
    # Enough rest overshoots baseline → clamped to baseline (no over-recovery).
    assert project_idle_energy(0.2, 0.6, 3600 * 5) == pytest.approx(0.6)
    # Already at/above baseline → rest never pushes it higher.
    assert project_idle_energy(0.8, 0.6, 3600 * 5) == 0.8


def test_reseat_readiness_floor_and_ramp():
    # `reseat_readiness` takes a recovery fraction toward baseline (1.0 = rested).
    assert reseat_readiness(RESEAT_RECOVERY_FLOOR - 0.01) == 0.0  # still resting
    assert reseat_readiness(RESEAT_RECOVERY_FLOOR) == 0.0  # at floor: 0
    assert reseat_readiness(1.0) == pytest.approx(1.0)  # fully recharged: certain
    mid = (1.0 + RESEAT_RECOVERY_FLOOR) / 2
    assert reseat_readiness(mid) == pytest.approx(0.5)  # halfway: ~half


def _idle_reseat_result(recovery: float):
    """Run a single-open-seat refresh where one affordable idle AI ('zeus',
    target_stake matching this $10 table) is the only candidate, gated by a
    fixed recovery-fraction lookup. rng passes the fill roll and readiness roll."""
    seats = [open_slot()] + [ai_slot("napoleon", 500)] * 5
    table = _make_table(seats)
    idle_pool = [
        IdlePoolEntry(
            personality_id="zeus",
            left_at=datetime(2026, 5, 19, 11, 0, 0),
            reason="take_break",
            target_stake=None,  # accepts any table
        )
    ]
    return refresh_table_roster(
        table,
        idle_pool=idle_pool,
        eligible_candidates=[],
        seated_globally=set(),
        bankroll_lookup=_bankroll_lookup_factory({"zeus": 5000}),
        buy_in_lookup=_buy_in_lookup_factory(400),
        rng=_force_rng([0.0, 0.0] + [0.99] * 6),  # fill roll + readiness roll pass
        now=datetime(2026, 5, 19, 12, 0, 0),
        stake_idx=1,
        table_min_buy_in=400,
        table_max_buy_in=1000,
        psych_lookup=_neutral_psych,
        energy_lookup=lambda _pid: recovery,
    )


def test_idle_reseat_blocked_when_still_tired():
    result = _idle_reseat_result(recovery=0.2)  # well below RESEAT_RECOVERY_FLOOR
    assert result.freshly_seated_personality_ids == []
    assert result.new_table.seats[0]["kind"] == "open"


def test_idle_reseat_allowed_when_recovered():
    result = _idle_reseat_result(recovery=1.0)  # fully recharged → readiness 1.0
    assert result.freshly_seated_personality_ids == ["zeus"]
    assert result.new_table.seats[0]["personality_id"] == "zeus"


# ============================================================
# Vice cooldown (post-vice refractory window)
# ============================================================


class TestViceCooldown:
    def test_record_and_check(self):
        now = datetime(2026, 5, 27, 12, 0, 0)
        record_vice_cooldown("deadpool", now, cooldown_seconds=300)
        assert is_in_vice_cooldown("deadpool", now) is True
        # Inside the window.
        assert is_in_vice_cooldown("deadpool", now + timedelta(seconds=299)) is True
        # Past it → cleared.
        assert is_in_vice_cooldown("deadpool", now + timedelta(seconds=301)) is False

    def test_default_window_is_30_min(self):
        now = datetime(2026, 5, 27, 12, 0, 0)
        record_vice_cooldown("ace_ventura", now)
        assert is_in_vice_cooldown("ace_ventura", now + timedelta(minutes=29)) is True
        assert is_in_vice_cooldown("ace_ventura", now + timedelta(minutes=31)) is False
        assert VICE_COOLDOWN_SECONDS == 30 * 60

    def test_cooldown_follows_the_ai_not_a_table(self):
        # Unlike the per-table leave cooldown, this is keyed on the AI.
        now = datetime(2026, 5, 27, 12, 0, 0)
        record_vice_cooldown("tyler_durden", now)
        assert is_in_vice_cooldown("tyler_durden", now) is True
        assert is_in_vice_cooldown("someone_else", now) is False

    def test_clear_cooldowns_wipes_vice(self):
        now = datetime(2026, 5, 27, 12, 0, 0)
        record_vice_cooldown("deadpool", now)
        clear_cooldowns()
        assert is_in_vice_cooldown("deadpool", now) is False


# ============================================================
# Vice-on-leave interception (go_vice)
# ============================================================


def _rng_seq(values, default=0.99):
    """rng whose random() yields each value then `default` forever.

    The default covers the live-fill rolls (one per open seat) that
    follow the movement decisions, which we keep above live_fill_prob so
    no incidental seating happens.
    """
    rng = random.Random()
    it = iter(values)

    def _r():
        try:
            return next(it)
        except StopIteration:
            return default

    rng.random = _r
    return rng


def _tenure_leaver_table():
    # One AI with a comfortable stack (not short, not stake-up-able) at a
    # near-empty table; low energy gives it a small tenure leave-pressure
    # so it rolls a discretionary bored_move.
    seats = [ai_slot("whale", 500)] + [open_slot() for _ in range(5)]
    return _make_table(seats)


def _exhausted_psych(_pid: str):
    # energy=0 → tenure pressure only → bored_move on a leave roll.
    return {
        "energy": 0.0,
        "zone": "neutral",
        "hands_in_detached_zone": 0,
        "emotional_intensity": 0.0,
    }


def _run_leave(vice_prob):
    table = _tenure_leaver_table()
    # First roll (0.02) clears the ~0.09 leave probability → leaves;
    # second roll (0.0) is the vice roll. Remaining rolls default high.
    rng = _rng_seq([0.02, 0.0])
    return refresh_table_roster(
        table,
        idle_pool=[],
        eligible_candidates=[],
        seated_globally={"whale"},
        # Bankroll kept inside the slum deadzone (≤ ~21× the 1000 max
        # buy-in) so the wealth-driven stake_up climb stays silent and the
        # leave is the pure tenure→bored_move this test exercises for vice
        # interception. A genuinely slumming bankroll would (correctly)
        # route to stake_up instead, which vice does not intercept.
        bankroll_lookup=_bankroll_lookup_factory({"whale": 20_000}),
        buy_in_lookup=_buy_in_lookup_factory(400),
        rng=rng,
        now=datetime(2026, 5, 27, 12, 0, 0),
        stake_idx=1,
        table_min_buy_in=400,
        table_max_buy_in=1000,
        next_tier_min_buy_in=2000,
        psych_lookup=_exhausted_psych,
        vice_prob_lookup=(lambda pid: vice_prob),
    )


class TestViceOnLeave:
    def test_leave_with_vice_hit_goes_to_vice_not_idle(self):
        result = _run_leave(vice_prob=1.0)
        assert result.decisions["whale"] == "go_vice"
        # Off-grid to vice — NOT in the idle pool.
        assert result.vice_bound == ["whale"]
        assert all(ch.personality_id != "whale" for ch in result.idle_changes)
        # Seat chips returned to bankroll so the lobby sizes vice on the
        # whole bankroll.
        from_seat = [
            bc
            for bc in result.bankroll_changes
            if bc.personality_id == "whale" and bc.direction == "from_seat"
        ]
        assert from_seat and from_seat[0].amount == 500
        # Seat is now open.
        assert result.new_table.seats[0]["kind"] == "open"

    def test_leave_with_vice_miss_goes_to_idle(self):
        result = _run_leave(vice_prob=0.0)
        # No vice → ordinary discretionary leave into the idle pool.
        assert result.decisions["whale"] == "bored_move"
        assert result.vice_bound == []
        assert any(ch.personality_id == "whale" for ch in result.idle_changes)

    def test_no_lookup_means_no_interception(self):
        # Pre-feature behavior: omit the lookup → never goes to vice.
        table = _tenure_leaver_table()
        rng = _rng_seq([0.02])
        result = refresh_table_roster(
            table,
            idle_pool=[],
            eligible_candidates=[],
            seated_globally={"whale"},
            # Inside the slum deadzone so the leave is pure tenure→bored_move
            # (see _run_leave note); a slumming bankroll would route to stake_up.
            bankroll_lookup=_bankroll_lookup_factory({"whale": 20_000}),
            buy_in_lookup=_buy_in_lookup_factory(400),
            rng=rng,
            now=datetime(2026, 5, 27, 12, 0, 0),
            stake_idx=1,
            table_min_buy_in=400,
            table_max_buy_in=1000,
            next_tier_min_buy_in=2000,
            psych_lookup=_exhausted_psych,
        )
        assert result.vice_bound == []
        assert result.decisions["whale"] == "bored_move"
