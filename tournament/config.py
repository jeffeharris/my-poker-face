"""Configuration for a headless multi-table tournament.

Phase 1 targets a mid-size field (18-24 entrants across 3-4 tables) — big enough
to exercise real table balancing and breaking, small enough to stay cheap and
fully debuggable. Everything is configurable so custom tournaments can grow the
field later without touching the engine.
"""

from dataclasses import dataclass, field

from .blinds import BlindSchedule

# Default field composition for v1: a spread of no-LLM controllers so chip
# dynamics (and thus eliminations) are varied and lifelike. All of these run
# WITHOUT any LLM call:
#   - TAG / LAG / Rock / Nit : tiered solver bots + personality deviation
#   - GTO-Lite / CaseBot     : pure rule bots
# (Names must exist in experiments.simulate_bb100.ARCHETYPES.)
DEFAULT_FIELD_ARCHETYPES: tuple[str, ...] = (
    'TAG',
    'LAG',
    'Rock',
    'Nit',
    'GTO-Lite',
    'CaseBot',
)


@dataclass(frozen=True)
class TournamentConfig:
    """Immutable tournament setup.

    `seed` makes a whole tournament reproducible: every per-table, per-round
    hand seed is derived from it.
    """

    field_size: int = 18
    table_size: int = 6
    starting_stack: int = 10_000
    seed: int = 0

    # Blind clock.
    starting_big_blind: int = 100
    blind_growth: float = 1.5
    rounds_per_level: int = 5

    # Field composition (cycled across seats).
    field_archetypes: tuple[str, ...] = field(default=DEFAULT_FIELD_ARCHETYPES)

    # Safety cap so a pathological run can't loop forever. Escalating blinds end
    # real tournaments far sooner.
    max_rounds: int = 100_000

    def __post_init__(self) -> None:
        if self.field_size < 2:
            raise ValueError("field_size must be >= 2")
        if self.table_size < 2:
            raise ValueError("table_size must be >= 2")
        if self.starting_stack < 1:
            raise ValueError("starting_stack must be >= 1")
        if not self.field_archetypes:
            raise ValueError("field_archetypes must be non-empty")

    @property
    def total_chips(self) -> int:
        """The conserved chip total: every entrant starts with `starting_stack`
        and no chips ever enter or leave a tournament."""
        return self.field_size * self.starting_stack

    def blind_schedule(self) -> BlindSchedule:
        return BlindSchedule.geometric(
            start_big_blind=self.starting_big_blind,
            growth=self.blind_growth,
            rounds_per_level=self.rounds_per_level,
        )

    # ── serialization (for tournament persistence) ──────────────────────────────

    def to_dict(self) -> dict:
        return {
            'field_size': self.field_size,
            'table_size': self.table_size,
            'starting_stack': self.starting_stack,
            'seed': self.seed,
            'starting_big_blind': self.starting_big_blind,
            'blind_growth': self.blind_growth,
            'rounds_per_level': self.rounds_per_level,
            'field_archetypes': list(self.field_archetypes),
            'max_rounds': self.max_rounds,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'TournamentConfig':
        return cls(
            field_size=d['field_size'],
            table_size=d['table_size'],
            starting_stack=d['starting_stack'],
            seed=d['seed'],
            starting_big_blind=d['starting_big_blind'],
            blind_growth=d['blind_growth'],
            rounds_per_level=d['rounds_per_level'],
            field_archetypes=tuple(d['field_archetypes']),
            max_rounds=d['max_rounds'],
        )
