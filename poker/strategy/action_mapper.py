"""Map abstract strategy actions to concrete game engine actions with chip amounts.

Translates preflop strategy table outputs (e.g., 'raise_2.5bb', 'jam')
and postflop strategy outputs (e.g., 'bet_67', 'raise_150')
into (action, amount) tuples the game engine's play_turn() can consume.
"""

from typing import Tuple


def _compute_raise_to(multiplier: float, base_amount: int, min_raise: int, max_raise: int) -> int:
    """Compute raise-to amount, clamped to legal bounds."""
    target = int(round(multiplier * base_amount))
    return max(min_raise, min(target, max_raise))


def resolve_preflop_sizing(abstract_action: str, game_state, player_idx: int) -> Tuple[str, int]:
    """Resolve abstract preflop action to concrete game engine action + amount.

    Args:
        abstract_action: Action from strategy table (e.g., 'raise_2.5bb', 'fold', 'call', 'jam')
        game_state: Current PokerGameState with player stacks, bets, blinds
        player_idx: Index of the acting player

    Returns:
        Tuple of (game_action, amount) where:
        - game_action is one of: 'fold', 'check', 'call', 'raise', 'all_in'
        - amount is the raise_to amount (0 for fold/check/call)
    """
    action = abstract_action.strip().lower()

    # Simple actions with no sizing
    if action == 'fold':
        return ('fold', 0)
    if action == 'call':
        return ('call', 0)
    if action == 'check':
        return ('check', 0)

    player = game_state.players[player_idx]
    player_total = player.stack + player.bet  # total chips including current bet
    big_blind = game_state.current_ante

    if action == 'jam':
        return ('all_in', player_total)

    # Raise actions: determine multiplier and base amount.
    # NL hold'em rule: re-raise increment must be >= the size of the
    # previous raise. Using big_blind here would understate the legal
    # minimum after any raise; the engine would silently sanitize the
    # bot's amount upward, over-committing chips relative to the
    # sampled strategy.
    highest_bet = game_state.highest_bet
    min_raise = highest_bet + game_state.min_raise_amount

    if action.endswith('bb'):
        # BB-relative sizing: raise_2.5bb, raise_3bb
        multiplier = float(action.replace('raise_', '').replace('bb', ''))
        raise_to = _compute_raise_to(multiplier, big_blind, min_raise, player_total)
    elif action.endswith('x'):
        # Multiplier of current bet: raise_3x, raise_4x, raise_2.2x
        multiplier = float(action.replace('raise_', '').replace('x', ''))
        raise_to = _compute_raise_to(multiplier, highest_bet, min_raise, player_total)
    else:
        raise ValueError(f"Unknown abstract action: {abstract_action!r}")

    # If raise_to consumes entire stack, convert to all-in
    if raise_to >= player_total:
        return ('all_in', player_total)

    return ('raise', raise_to)


def resolve_postflop_sizing(abstract_action: str, game_state, player_idx: int) -> Tuple[str, int]:
    """Resolve abstract postflop action to concrete game engine action + amount.

    Postflop actions use pot-relative sizing instead of BB-relative.

    Args:
        abstract_action: Action from strategy (e.g., 'bet_67', 'raise_150', 'fold', 'jam')
        game_state: Current PokerGameState with player stacks, bets, pot, blinds
        player_idx: Index of the acting player

    Returns:
        Tuple of (game_action, amount) where:
        - game_action is one of: 'fold', 'check', 'call', 'raise', 'all_in'
        - amount is the raise_to amount (0 for fold/check/call)
    """
    action = abstract_action.strip().lower()

    # Simple actions with no sizing
    if action == 'fold':
        return ('fold', 0)
    if action == 'call':
        return ('call', 0)
    if action == 'check':
        return ('check', 0)

    player = game_state.players[player_idx]
    player_total = player.stack + player.bet

    if action == 'jam':
        return ('all_in', player_total)

    big_blind = game_state.current_ante
    highest_bet = game_state.highest_bet
    # See preflop comment: re-raise increment must >= prior raise size.
    min_raise = highest_bet + game_state.min_raise_amount

    # Total money in play: committed pot + current round bets
    pot_total = game_state.pot.get('total', 0) + sum(p.bet for p in game_state.players)

    if action.startswith('bet_'):
        # Bet actions: pot-relative sizing (first to act / betting into uncalled pot)
        pct = int(action.replace('bet_', '')) / 100.0
        raise_to = int(pot_total * pct)
    elif action.startswith('raise_'):
        # Raise actions: pot-relative sizing (facing a bet)
        pct = int(action.replace('raise_', '')) / 100.0
        call_amount = highest_bet - player.bet
        pot_after_call = pot_total + call_amount
        raise_to = highest_bet + int(pot_after_call * pct)
    else:
        raise ValueError(f"Unknown abstract action: {abstract_action!r}")

    # Clamp to legal bounds
    raise_to = max(min_raise, min(raise_to, player_total))

    # If raise_to consumes entire stack, convert to all-in
    if raise_to >= player_total:
        return ('all_in', player_total)

    return ('raise', raise_to)
