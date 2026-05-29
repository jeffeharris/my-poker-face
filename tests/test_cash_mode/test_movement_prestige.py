"""Tests for the table-attractiveness leave-pressure wiring (Phase B).

Spec: `docs/plans/CASH_MODE_TABLE_ATTRACTIVENESS.md` §2.

Covers the three movement-side additions:
  - wealth-driven `stake_up` source (the rich climb even on a short stack),
  - the dead-table `dead` pressure term + its routing to `bored_move`,
  - the prestige retention override in `_coerce_predator_retention`.
"""

from __future__ import annotations

from cash_mode.movement import (
    CASINO_PREDATOR_FATIGUE_FLOOR,
    PRESTIGE_RETENTION_OVERRIDE,
    SLUM_DEADZONE,
    W_DEAD,
    W_SLUM,
    W_STAKE_UP,
    MovementContext,
    _coerce_predator_retention,
    compute_leave_pressure,
    evaluate_ai_movement,
)


class _FixedRng:
    """Deterministic rng stub: `random()` always returns `value`."""

    def __init__(self, value: float):
        self.value = value

    def random(self) -> float:
        return self.value


def _ctx(**over) -> MovementContext:
    base = dict(
        ai_chips=1500,
        min_buy_in=1000,
        max_buy_in=2500,
        projected_bankroll=2500,  # == max → no wealth-climb unless overridden
        stake_idx=1,
        next_tier_min_buy_in=2000,
        energy=0.7,  # fresh — no tenure
        zone="neutral",
        hands_in_detached_zone=0,
        emotional_intensity=0.0,
        table_deadness=0.0,
    )
    base.update(over)
    return MovementContext(**base)


# --- wealth-driven stake_up --------------------------------------------


def test_wealth_drives_stake_up_on_short_stack():
    # Short seat stack (no seat-stack stake_up), but a huge bankroll past
    # the slum deadzone → the AI is "slumming it" and generates climb
    # pressure. A poor AI on the same short stack generates none.
    rich = compute_leave_pressure(_ctx(ai_chips=1500, projected_bankroll=250_000))
    poor = compute_leave_pressure(_ctx(ai_chips=1500, projected_bankroll=2500))
    assert rich["stake_up"] > 0.0
    assert poor["stake_up"] == 0.0
    assert rich["stake_up"] > poor["stake_up"]


def test_modest_overroll_inside_deadzone_no_climb():
    # An AI rolled within the deadzone (correctly tiered, healthy roll) has
    # zero climb pressure — it stays content, no churn.
    inside = (SLUM_DEADZONE - 1) * 2500  # wealth_over_tier just below deadzone
    assert compute_leave_pressure(_ctx(ai_chips=1500, projected_bankroll=int(inside)))[
        "stake_up"
    ] == 0.0


def test_stake_up_takes_stronger_of_seat_or_wealth():
    # Seat term dominates: stack = 2× max (raw 1.0), bankroll modest.
    seat = compute_leave_pressure(_ctx(ai_chips=5000, projected_bankroll=2500))
    assert seat["stake_up"] == W_STAKE_UP * 1.0
    # Wealth term dominates: tiny stack, bankroll 100× the tier max (well
    # past the deadzone).
    wealth = compute_leave_pressure(_ctx(ai_chips=800, projected_bankroll=250_000))
    expected_slum = (250_000 / 2500 - 1.0) - SLUM_DEADZONE
    assert wealth["stake_up"] == W_STAKE_UP * (W_SLUM * expected_slum)


def test_wealth_climb_scales_with_bankroll():
    # Both above the deadzone so the comparison tests the ramp, not 0-vs-positive.
    a = compute_leave_pressure(_ctx(ai_chips=1500, projected_bankroll=100_000))
    b = compute_leave_pressure(_ctx(ai_chips=1500, projected_bankroll=250_000))
    assert a["stake_up"] > 0.0
    assert b["stake_up"] > a["stake_up"]


# --- dead-table term ---------------------------------------------------


def test_dead_term_zero_when_juicy():
    assert compute_leave_pressure(_ctx(table_deadness=0.0))["dead"] == 0.0


def test_dead_term_scales_and_clamps():
    assert compute_leave_pressure(_ctx(table_deadness=1.0))["dead"] == W_DEAD * 1.0
    # Clamped into [0, 1] defensively.
    assert compute_leave_pressure(_ctx(table_deadness=5.0))["dead"] == W_DEAD * 1.0
    assert compute_leave_pressure(_ctx(table_deadness=-1.0))["dead"] == 0.0


def test_dominant_dead_routes_to_bored_move():
    # A dead all-shark table with no other pressure → when the leave roll
    # fires, the dominant `dead` term routes to bored_move (go find fish).
    ctx = _ctx(table_deadness=1.0)
    assert evaluate_ai_movement(ctx, _FixedRng(0.0)) == "bored_move"
    # And a juicy table (deadness 0) with no other pressure → stay.
    assert evaluate_ai_movement(_ctx(table_deadness=0.0), _FixedRng(0.0)) == "stay"


# --- prestige retention override ---------------------------------------


def test_retention_override_graduates_the_rich_upward():
    fresh = CASINO_PREDATOR_FATIGUE_FLOOR + 0.5  # not worn down
    # Rich enough to be slumming → a boredom drift is converted to a
    # stake_up (graduate UP, not sideways), not left as bored_move.
    assert (
        _coerce_predator_retention(
            "bored_move", True, fresh, wealth_excess=PRESTIGE_RETENTION_OVERRIDE
        )
        == "stake_up"
    )
    # Just below the threshold → still pinned to farm the fish.
    assert (
        _coerce_predator_retention(
            "bored_move", True, fresh, wealth_excess=PRESTIGE_RETENTION_OVERRIDE - 0.01
        )
        == "stay"
    )
    # The override only redirects boredom — a real stake_up / leave still
    # passes through unchanged.
    assert (
        _coerce_predator_retention(
            "stake_up", True, fresh, wealth_excess=PRESTIGE_RETENTION_OVERRIDE
        )
        == "stake_up"
    )
    assert (
        _coerce_predator_retention(
            "take_break", True, fresh, wealth_excess=PRESTIGE_RETENTION_OVERRIDE
        )
        == "take_break"
    )


def test_retention_default_unchanged_without_wealth():
    # No wealth_excess passed (pre-Phase-B callers) → original pinning.
    fresh = CASINO_PREDATOR_FATIGUE_FLOOR + 0.5
    assert _coerce_predator_retention("bored_move", True, fresh) == "stay"
    assert _coerce_predator_retention("stake_up", True, fresh) == "stake_up"
