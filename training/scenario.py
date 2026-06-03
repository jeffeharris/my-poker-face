"""Training scenarios — free-play table presets + the scripted-spot model.

`TablePreset` describes the *table shape* a free-play practice game is set up
with: named seat-count + stack-depth + blind combinations (heads-up,
short-stack, deep, full-ring). Orthogonal to difficulty — the player picks a
preset (who/how-deep) AND a difficulty tier (how the opponents play).

`ScriptedSpot` describes a fixed mid-hand state (specific hole cards, board,
stacks, line). NOTE: hand-authored drill *catalogs* were cut — curating spots
that stay interesting doesn't scale. `ScriptedSpot` survives as the
reconstruction model the **hand-replay** feature uses (Phase 3.5): a captured
real hand from `hand_history` becomes a `ScriptedSpot` and is rebuilt via
`state_builder.build_scripted_spot_state_machine`. See docs/plans/TRAINING_MODE.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TablePreset:
    """A named table shape for a free-play training game.

    `opponents` is the AI seat count (the human is seated separately, so total
    players = opponents + 1). `starting_stack` is derived from depth in big
    blinds so the table is always a clean N-bb game.
    """

    id: str
    title: str
    description: str
    opponents: int
    big_blind: int
    starting_stack_bb: int

    @property
    def starting_stack(self) -> int:
        return self.starting_stack_bb * self.big_blind


# Ordered for display. `standard` is the default (matches Phase 1 free-play:
# 6-max, 100bb deep). Stack depth is the real new teaching dimension —
# short-stack and deep play are distinct skills.
TABLE_PRESETS: dict[str, TablePreset] = {
    "standard": TablePreset(
        id="standard",
        title="6-Max",
        description="Five opponents, 100bb deep — the standard cash table.",
        opponents=5,
        big_blind=100,
        starting_stack_bb=100,
    ),
    "heads_up": TablePreset(
        id="heads_up",
        title="Heads-Up",
        description="One-on-one, 100bb deep — every hand is a decision.",
        opponents=1,
        big_blind=100,
        starting_stack_bb=100,
    ),
    "short_stack": TablePreset(
        id="short_stack",
        title="Short Stack",
        description="Five opponents, 25bb deep — push/fold and commitment math.",
        opponents=5,
        big_blind=100,
        starting_stack_bb=25,
    ),
    "deep": TablePreset(
        id="deep",
        title="Deep Stack",
        description="Five opponents, 200bb deep — post-flop play with room to maneuver.",
        opponents=5,
        big_blind=100,
        starting_stack_bb=200,
    ),
    "full_ring": TablePreset(
        id="full_ring",
        title="Full Ring",
        description="Eight opponents, 100bb deep — tighter ranges, more pressure.",
        opponents=8,
        big_blind=100,
        starting_stack_bb=100,
    ),
}


@dataclass(frozen=True)
class ScriptedSpot:
    """A fixed drill: specific hole cards, board, stacks, and a villain line.

    All chip values are authored in big blinds (`*_bb`) so a spot reads cleanly
    and scales with the stake; the factory converts to chips. Cards are short
    strings parsed by `core.card.Card.from_short` ("Ah", "10c", "A♥").

    `hero_position` is a display label in Phase 3 (the factory forces the hero
    to act by setting `current_player_idx` to the hero seat, so it does not rely
    on the engine deriving position). Full positional action-order fidelity
    matters only for the "play it out" continuation and is deferred.

    `villain_holes` is optional: when omitted the factory deals villains random
    hole cards from the remaining deck (fine for a single-decision drill where
    the hero faces a known line). Provide it to pin a specific runout.
    """

    kind: str = "scripted_spot"
    phase: str = "FLOP"  # PokerPhase name: PRE_FLOP | FLOP | TURN | RIVER
    big_blind: int = 100
    hero_hole: list[str] = field(default_factory=list)  # ["Ah", "Ks"]
    community: list[str] = field(default_factory=list)  # ["Kc", "7d", "2h"]
    hero_stack_bb: float = 100.0
    villain_stacks_bb: list[float] = field(default_factory=list)
    pot_bb: float = 0.0  # total pot before the hero acts (incl. current bets)
    hero_bet_bb: float = 0.0  # hero's outstanding current-street bet
    villain_bets_bb: list[float] = field(default_factory=list)  # aligns w/ villain_stacks
    villain_holes: list[list[str]] | None = None
    hero_position: str = "BTN"  # display label only (see class docstring)

    def chips(self, bb_amount: float) -> int:
        return round(bb_amount * self.big_blind)


DEFAULT_PRESET_ID = "standard"

VALID_PRESET_IDS: frozenset[str] = frozenset(TABLE_PRESETS)


def get_table_preset(preset_id: str | None) -> TablePreset:
    """Return the named preset, or the default when missing/unknown."""
    if preset_id is None:
        return TABLE_PRESETS[DEFAULT_PRESET_ID]
    return TABLE_PRESETS.get(preset_id, TABLE_PRESETS[DEFAULT_PRESET_ID])


def list_table_presets() -> list[TablePreset]:
    """Presets in display order."""
    return list(TABLE_PRESETS.values())
