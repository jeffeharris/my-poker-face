"""
Preflop classifier -- maps game state to a PreflopNode for strategy lookup.

Reads the live PokerGameState to determine:
  1. The acting player's 6-max position label
  2. The preflop scenario (rfi / vs_open / vs_3bet / vs_4bet)
  3. The opener's position (for faced-raise scenarios)

These three values, combined with the canonical hand string, produce the
PreflopNode used to look up the solver-derived strategy.
"""

from typing import Tuple

from .nodes import PreflopNode

# ── Position label mappings ────────────────────────────────────────────

# Maps the internal table_positions keys → 6-max labels.
_POSITION_LABEL = {
    "button": "BTN",
    "small_blind_player": "SB",
    "big_blind_player": "BB",
    "under_the_gun": "UTG",
    "middle_position_1": "HJ",
    "cutoff": "CO",
}


def get_6max_position(game_state, player_idx: int) -> str:
    """Return the 6-max position label for *player_idx*.

    Handles 2–6 player tables.  For < 6 players the engine already
    collapses the position keys (e.g. 5-player has no middle_position_1),
    so we just translate whatever keys are present.

    For 2-player (heads-up), the button is also SB; we return 'SB' for the
    button/SB player and 'BB' for the other.
    """
    player_name = game_state.players[player_idx].name
    positions = game_state.table_positions

    for key, name in positions.items():
        if name == player_name:
            # In heads-up the button entry maps to SB (button *is* SB).
            if key == "button" and "small_blind_player" in positions:
                # If the same player is listed under both button and SB,
                # return SB (heads-up convention).
                if positions.get("small_blind_player") == player_name:
                    return "SB"
            return _POSITION_LABEL.get(key, key)

    # Fallback – should not happen with a well-formed game state.
    return "UTG"


# ── Scenario classification ────────────────────────────────────────────


def classify_preflop_scenario(game_state) -> Tuple[str, str, str]:
    """Classify the preflop scenario for the current acting player.

    Returns
    -------
    (scenario, current_position, opener_position)
        scenario : one of 'rfi', 'vs_open', 'vs_3bet', 'vs_4bet'
        current_position : 6-max label of the player to act
        opener_position  : 6-max label of the relevant raiser ('' for rfi)
    """
    raises = game_state.raises_this_round
    current_idx = game_state.current_player_idx
    current_pos = get_6max_position(game_state, current_idx)

    if raises == 0:
        return ("rfi", current_pos, "")

    scenario = {1: "vs_open", 2: "vs_3bet"}.get(raises, "vs_4bet")
    opener_pos = _find_raiser_position(game_state)
    return (scenario, current_pos, opener_pos)


def _find_raiser_position(game_state) -> str:
    """Identify the position of the most recent raiser.

    Heuristic: the player (other than the current player) with the
    highest bet above the big-blind amount is the latest raiser.
    Ties are broken by scanning in reverse seat order from the current
    player (the most-recently-acting player with the highest bet wins).
    """
    current_idx = game_state.current_player_idx
    big_blind = game_state.current_ante
    players = game_state.players

    best_idx = None
    best_bet = big_blind  # must exceed big blind to count as a raise

    for i, player in enumerate(players):
        if i == current_idx:
            continue
        if player.bet > best_bet:
            best_bet = player.bet
            best_idx = i

    if best_idx is not None:
        return get_6max_position(game_state, best_idx)

    # Fallback: no player above BB found (shouldn't happen when raises > 0).
    return ""


# ── Node builder ───────────────────────────────────────────────────────


def build_preflop_node(game_state, player_idx: int, canonical_hand: str) -> PreflopNode:
    """Compose a full PreflopNode from the live game state.

    Parameters
    ----------
    game_state : PokerGameState
    player_idx : index into game_state.players
    canonical_hand : e.g. 'AKs', 'TT', '72o'
    """
    position = get_6max_position(game_state, player_idx)
    scenario, _, opener_position = classify_preflop_scenario(game_state)
    return PreflopNode(
        hand=canonical_hand,
        position=position,
        scenario=scenario,
        opener_position=opener_position,
    )
