"""Shared input validation for player actions (HTTP + socket handlers)."""


VALID_ACTIONS = frozenset({'fold', 'check', 'call', 'raise', 'all_in'})


def validate_player_action(game_state, action, amount):
    """Validate a player action against the current game state.

    Returns:
        (is_valid, error_message) tuple. error_message is empty string when valid.
    """
    if not game_state.awaiting_action:
        return False, "Not awaiting player action"

    if game_state.run_it_out:
        return False, "Game is in run-it-out mode"

    if not game_state.current_player.is_human:
        return False, "Not human player's turn"

    if action not in VALID_ACTIONS:
        return False, f"Invalid action: {action}"

    if action not in game_state.current_player_options:
        return False, f"Action '{action}' not available. Options: {game_state.current_player_options}"

    if action == 'raise' and (not isinstance(amount, (int, float)) or amount < 0):
        return False, "Invalid raise amount"

    return True, ""
