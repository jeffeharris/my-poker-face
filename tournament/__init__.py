"""Multi-table (WSOP-style) tournament engine.

Phase 1 scope: a headless AI-vs-AI multi-table tournament engine — table
balancing, table breaking, a synchronized blind clock, field-wide standings,
and a final table. No economy, no live human, no Flask/Socket.IO/DB coupling.

Design (see docs/plans/MULTI_TABLE_TOURNAMENT_PLAN.md):

  - The **seating** and **field** layers are pure data structures with zero
    poker-engine dependency, so the balancing/shuffling/standings logic — the
    part most worth getting right — is unit-testable in isolation.
  - The actual hand-playing is a pluggable `HandResolver`. `FakeHandResolver`
    is a deterministic, chip-conserving model for fast tests and demos;
    `EngineHandResolver` (in `engine_resolver.py`) drives the real poker engine
    with no-LLM tiered/rule bots.

The whole thing is reproducible from a single seed and asserts the chip
conservation invariant (`sum(stacks) == field_size * starting_stack`) every
round.
"""

from .blinds import BlindLevel, BlindSchedule
from .config import TournamentConfig
from .director import (
    FakeHandResolver,
    HandResolver,
    Standing,
    TournamentDirector,
    TournamentResult,
)
from .field import Elimination, TournamentField
from .seating import SeatingManager, SeatMove, Table, build_initial_seating
from .session import TournamentSession

__all__ = [
    'BlindLevel',
    'BlindSchedule',
    'TournamentConfig',
    'TournamentDirector',
    'TournamentResult',
    'TournamentSession',
    'Standing',
    'HandResolver',
    'FakeHandResolver',
    'TournamentField',
    'Elimination',
    'SeatMove',
    'SeatingManager',
    'Table',
    'build_initial_seating',
]
