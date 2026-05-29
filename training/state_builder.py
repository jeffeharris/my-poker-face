"""Build a poker state machine from a training scenario.

Phase 2 handles `TablePreset` — a normal freshly-initialized table at the
preset's seat count, stack depth, and blinds. This is the seam Phase 3's
`ScriptedSpot` (fixed hole cards + board, injected mid-hand via
`PokerStateMachine.from_saved_state`) will extend with a second branch.

Returns an un-advanced `StateMachineAdapter`; the caller wires controllers and
then calls `run_until_player_action()` (so hole cards exist before the memory
manager records the hand start).
"""

from __future__ import annotations

from .scenario import TablePreset


def _flat_blind_config(big_blind: int) -> dict:
    """Flat blinds — a stable practice table (no escalation), like cash."""
    return {"growth": 1.0, "hands_per_level": 999999, "max_blind": big_blind}


def build_table_preset_state_machine(preset: TablePreset, human_name: str, ai_names: list[str]):
    """Build an un-advanced StateMachineAdapter for a table-preset game."""
    from flask_app.game_adapter import StateMachineAdapter
    from poker.poker_game import initialize_game_state
    from poker.poker_state_machine import PokerStateMachine

    game_state = initialize_game_state(
        player_names=ai_names,
        human_name=human_name,
        starting_stack=preset.starting_stack,
        big_blind=preset.big_blind,
    )
    base = PokerStateMachine(
        game_state=game_state,
        blind_config=_flat_blind_config(preset.big_blind),
    )
    return StateMachineAdapter(base)
