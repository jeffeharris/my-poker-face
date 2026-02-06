"""Helper functions for game state logic."""
from .poker_state_machine import PokerPhase

NON_BETTING_PHASES = frozenset({
    PokerPhase.EVALUATING_HAND,
    PokerPhase.HAND_OVER,
    PokerPhase.SHOWDOWN,
    PokerPhase.GAME_OVER,
})


def should_clear_player_options(game_state, state_machine) -> bool:
    """Determine if player options should be cleared.

    Options are cleared during run_it_out mode or non-betting phases
    (EVALUATING_HAND, HAND_OVER, SHOWDOWN, GAME_OVER).

    Args:
        game_state: The current game state object with run_it_out attribute.
        state_machine: The state machine with current_phase attribute.

    Returns:
        True if player options should be cleared, False otherwise.
    """
    return game_state.run_it_out or state_machine.current_phase in NON_BETTING_PHASES
