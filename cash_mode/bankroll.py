"""Bankroll state dataclasses and projection-on-read regen.

Two persisted bankroll surfaces — one per AI personality, one per
human player — plus the pure `project_bankroll` function used to
compute live values on read without a background timer. Same pattern
as `project_heat` on the relationship layer:

  Stored value = "bankroll as of last_regen_tick"
  Read value   = stored + (elapsed * rate), clamped to cap

Persistence writes only happen on real events (sit-down, win, loss);
reads always project through elapsed wall-clock time.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2
  §"Bankroll regen (pure projection on read)" and
  §"Bankroll knob storage".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class AIBankrollState:
    """Per-personality persistent bankroll.

    Keyed by `personality_id` (the stable v85 slug, not display name).
    Survives sessions, games, and personality renames.

    `last_regen_tick` is the wall-clock anchor for projection. On
    every write (sit-down, win, loss), the caller projects the live
    value, snaps `chips` to that projected value, and resets
    `last_regen_tick = now`. Subsequent reads project from `now`
    again.

    `last_regen_tick == None` means no event has ever been recorded;
    `project_bankroll` returns the stored `chips` verbatim in that
    case (typically the starting grant for a freshly seeded AI).
    """

    personality_id: str
    chips: int
    last_regen_tick: Optional[datetime] = None


@dataclass
class PlayerBankrollState:
    """Per-player persistent bankroll.

    Player bankrolls do **not** regen in v1 — the player gets a
    fresh-grant on full bust (Part 2 §"Bust semantics") and no passive
    refill. The dataclass shape matches `AIBankrollState` for
    symmetry but the projection function isn't called for players.

    `starting_bankroll` is the fresh-grant value reset to on full
    bust. Per-player to keep the door open for staking / character
    progression to alter this in the future without a migration.
    """

    player_id: str
    chips: int
    starting_bankroll: int


@dataclass
class BankrollKnobs:
    """Per-personality bankroll behavior knobs.

    Stored in columns on `personalities` (schema v88). Read at table
    sit-down to decide buy-in size, eligibility, and (in v2)
    stop-loss / stop-win cutoffs.

    `stop_loss_buy_ins` and `stop_win_buy_ins` are persisted in v1
    but unused — v1 ships bust-only AI session behavior (Part 2
    §"AI session behavior (v1)"). v2 reads these to add stop-loss /
    stop-win gates.

    `stake_comfort_zone` is the friendly stake label ("$10",
    "$50", ...) the AI prefers when multiple are affordable. v1 has
    no selection problem (one table), so this is also persisted-but-
    unused until v2's lobby lands.
    """

    bankroll_cap: int
    bankroll_rate: int
    buy_in_multiplier: float
    stop_loss_buy_ins: int
    stop_win_buy_ins: int
    stake_comfort_zone: str


# Defaults used when a personality has no per-row override yet.
# v1 ships uniform defaults; per-personality tuning is a follow-up
# (just populate the columns from personalities.json).
BANKROLL_KNOB_DEFAULTS = BankrollKnobs(
    bankroll_cap=10_000,
    bankroll_rate=500,
    buy_in_multiplier=1.0,
    stop_loss_buy_ins=3,
    stop_win_buy_ins=5,
    stake_comfort_zone="$10",
)


def project_bankroll(
    state: AIBankrollState,
    cap: int,
    rate: int,
    now: datetime,
) -> int:
    """Project bankroll chips through elapsed time.

    Pure function. Returns the value `chips` would currently have
    given the time elapsed since the last mutation, clamped to `cap`.
    Does not mutate `state`.

      projected = stored_chips + int(rate * elapsed_days)
      projected = min(cap, projected)

    Same pattern as `project_heat`. Persistence writes only on real
    events; reads always project. The fractional-day floor uses
    `int(...)` so projection is monotonic per day boundary — a
    half-second after `last_regen_tick` reads as the stored value,
    not a no-op `+0`.

    `last_regen_tick == None` is the no-event-yet state for freshly
    seeded AI personalities; the function returns stored `chips`
    verbatim so the seed value isn't immediately inflated.
    """
    if state.last_regen_tick is None:
        return state.chips
    elapsed_days = (now - state.last_regen_tick).total_seconds() / 86400.0
    projected = state.chips + int(rate * elapsed_days)
    return min(cap, projected)
