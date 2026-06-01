"""Difficulty → opponent roster mapping for Training mode.

Maps a difficulty tier to a list of `bot_type` strings. These strings are the
SAME ones the warm builder (`training_routes._build_training_game`) and the
cold-load path (`game_handler.restore_ai_controllers`) dispatch on, so a
training game restores to identical controllers after eviction:

- `sharp` / `baseline_solver` → tiered solver (via `build_tiered_controller`)
- `casebot` / `gto_lite`      → `RuleBotController` with a mapped strategy
- any other name              → `RuleBotController(strategy=<name>)` (round-trips
  through `restore_ai_controllers`' else-branch; must be a key in
  `poker.rule_strategies.BUILT_IN_STRATEGIES`)

Tiers:
- **easy**   — loose-passive / over-folding rule bots (exploitable, readable):
  the place to practice value-betting and punishing leaks.
- **medium** — solid-but-beatable rule bots + the pure baseline solver.
- **hard**   — the tiered "sharp" solver (personality-distorted GTO).

The per-seat custom picker is a deferred follow-on; `resolve_opponents` is the
seam it will slot into (it can grow a per-seat override arg without changing
callers).
"""

from __future__ import annotations

# Tier → cyclic roster of bot_type strings. Every string here must be
# dispatchable by BOTH the warm builder and restore_ai_controllers.
DIFFICULTY_ROSTERS: dict[str, list[str]] = {
    "easy": ["fish", "foldy"],
    "medium": ["gto_lite", "casebot", "baseline_solver"],
    "hard": ["sharp"],
}

DEFAULT_DIFFICULTY = "medium"

VALID_DIFFICULTIES: frozenset[str] = frozenset(DIFFICULTY_ROSTERS)


def resolve_opponents(difficulty: str, num_seats: int) -> list[str]:
    """Return `num_seats` bot_type strings for the given difficulty.

    Cycles the tier's roster so a table is filled with a varied but on-tier
    mix (e.g. easy → fish, foldy, fish, ...). An unknown difficulty falls back
    to the default tier rather than erroring — callers should validate against
    `VALID_DIFFICULTIES` first if they want a hard reject.
    """
    roster = DIFFICULTY_ROSTERS.get(difficulty, DIFFICULTY_ROSTERS[DEFAULT_DIFFICULTY])
    return [roster[i % len(roster)] for i in range(max(0, num_seats))]
