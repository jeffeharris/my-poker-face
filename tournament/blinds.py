"""Synchronized blind clock for a multi-table tournament.

In a real WSOP MTT blinds are time-based, so every table is on the same level
regardless of how many hands it has played. The headless analog is a **round**:
one synchronized hand per table per round. The blind level is therefore a pure
function of the round index (`level_for_round`), which keeps every table on the
same level by construction.

The poker engine's blind model is driven by a single `current_ante` value that
*is* the big blind (the small blind is derived inside the engine). So the field
that actually drives play is `BlindLevel.big_blind`; `small_blind`/`ante` are
carried for display and future use.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BlindLevel:
    """One level of the blind schedule."""

    level: int
    small_blind: int
    big_blind: int
    ante: int = 0


@dataclass(frozen=True)
class BlindSchedule:
    """An ordered set of blind levels plus how many rounds each level lasts.

    `rounds_per_level` is the clock: after that many rounds the level bumps. Once
    the last level is reached it sticks (a deep stack always runs out eventually
    because blinds stop growing but stacks keep being contested).
    """

    levels: tuple[BlindLevel, ...]
    rounds_per_level: int

    def __post_init__(self) -> None:
        if not self.levels:
            raise ValueError("BlindSchedule needs at least one level")
        if self.rounds_per_level < 1:
            raise ValueError("rounds_per_level must be >= 1")

    def level_for_round(self, round_index: int) -> BlindLevel:
        """The blind level in effect for a given (0-based) round index."""
        if round_index < 0:
            raise ValueError("round_index must be >= 0")
        idx = round_index // self.rounds_per_level
        if idx >= len(self.levels):
            return self.levels[-1]
        return self.levels[idx]

    @classmethod
    def geometric(
        cls,
        start_big_blind: int = 100,
        growth: float = 1.5,
        num_levels: int = 24,
        rounds_per_level: int = 5,
    ) -> "BlindSchedule":
        """Build a turbo-ish geometric schedule: big blind grows by `growth`
        each level. With a 100bb starting stack this walks stacks down through
        the full depth progression and reliably ends a mid-size field in a
        bounded number of rounds.
        """
        if growth <= 1.0:
            raise ValueError("growth must be > 1.0")
        levels = []
        bb = float(start_big_blind)
        for lv in range(num_levels):
            big = int(round(bb))
            small = max(1, big // 2)
            levels.append(BlindLevel(level=lv + 1, small_blind=small, big_blind=big))
            bb *= growth
        return cls(levels=tuple(levels), rounds_per_level=rounds_per_level)
