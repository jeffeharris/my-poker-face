"""Map abstract strategy actions to concrete game engine actions with chip amounts.

Translates preflop strategy table outputs (e.g., 'raise_2.5bb', 'jam')
and postflop strategy outputs (e.g., 'bet_67', 'raise_150')
into (action, amount) tuples the game engine's play_turn() can consume.

Competitive-feel sizing jitter: controllers may pass an optional
`rng` (random.Random instance) and `sizing_jitter` (fraction, default
0.0) so the resolver samples bet sizes from a small uniform band
around the strategy table's target instead of always emitting the
exact mapped value. Eliminates sizing tells without changing EV
expectation (the band is symmetric around the table's intent).
"""

import random
from typing import Optional, Tuple


def _compute_raise_to(
    multiplier: float, base_amount: int, min_raise: int, max_raise: int,
    rng: Optional[random.Random] = None,
    jitter: float = 0.0,
) -> int:
    """Compute raise-to amount, clamped to legal bounds.

    Args:
        multiplier: Table-derived size multiplier (e.g., 2.5 for raise_2.5bb).
        base_amount: Reference amount (big blind, highest_bet, etc.).
        min_raise: Legal minimum raise-to value.
        max_raise: Player's effective stack (legal maximum).
        rng: Optional random.Random instance for jitter sampling. When
            None, jitter has no effect (deterministic path preserved).
        jitter: Fractional band size for size randomization. With
            jitter=0.15, the target is sampled uniformly from
            [target * 0.85, target * 1.15] before clamping. 0.0
            (default) preserves exact table-derived sizing.
    """
    target = multiplier * base_amount
    if rng is not None and jitter > 0.0:
        # Sample uniformly from [target*(1-jitter), target*(1+jitter)]
        low = target * (1.0 - jitter)
        high = target * (1.0 + jitter)
        target = rng.uniform(low, high)
    target_int = int(round(target))
    return max(min_raise, min(target_int, max_raise))


def resolve_preflop_sizing(
    abstract_action: str, game_state, player_idx: int,
    rng: Optional[random.Random] = None,
    sizing_jitter: float = 0.0,
) -> Tuple[str, int]:
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
        raise_to = _compute_raise_to(
            multiplier, big_blind, min_raise, player_total,
            rng=rng, jitter=sizing_jitter,
        )
    elif action.endswith('x'):
        # Multiplier of current bet: raise_3x, raise_4x, raise_2.2x
        multiplier = float(action.replace('raise_', '').replace('x', ''))
        raise_to = _compute_raise_to(
            multiplier, highest_bet, min_raise, player_total,
            rng=rng, jitter=sizing_jitter,
        )
    else:
        raise ValueError(f"Unknown abstract action: {abstract_action!r}")

    # If raise_to consumes entire stack, convert to all-in
    if raise_to >= player_total:
        return ('all_in', player_total)

    return ('raise', raise_to)


def resolve_postflop_sizing(
    abstract_action: str, game_state, player_idx: int,
    rng: Optional[random.Random] = None,
    sizing_jitter: float = 0.0,
) -> Tuple[str, int]:
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

    # True pot size. NOTE: this engine never resets `player.bet` between
    # streets — it is the player's *cumulative* commitment for the whole hand,
    # and `pot['total']` is kept in lockstep (empirically `pot['total'] ==
    # sum(p.bet)` at every postflop decision). So the pot is `pot['total']`
    # alone; adding `sum(p.bet)` double-counts it (2x the real pot).
    pot_total = game_state.pot.get('total', 0)

    if action.startswith('bet_'):
        # Bet actions: pot-relative sizing (first to act / betting into uncalled pot)
        pct = int(action.replace('bet_', '')) / 100.0
        target = pot_total * pct
        if rng is not None and sizing_jitter > 0.0:
            target = rng.uniform(target * (1.0 - sizing_jitter),
                                 target * (1.0 + sizing_jitter))
        # `player.bet` is the hero's cumulative commitment this hand; a bet of
        # `pct` of pot must be ADDED on top of it (mirrors the raise branch's
        # `highest_bet + ...`). Omitting it under-bet by the prior commitment.
        raise_to = player.bet + int(target)
    elif action.startswith('raise_'):
        # Raise actions: pot-relative sizing (facing a bet)
        pct = int(action.replace('raise_', '')) / 100.0
        call_amount = highest_bet - player.bet
        pot_after_call = pot_total + call_amount
        target = pot_after_call * pct
        if rng is not None and sizing_jitter > 0.0:
            target = rng.uniform(target * (1.0 - sizing_jitter),
                                 target * (1.0 + sizing_jitter))
        raise_to = highest_bet + int(target)
    else:
        raise ValueError(f"Unknown abstract action: {abstract_action!r}")

    # Clamp to legal bounds
    raise_to = max(min_raise, min(raise_to, player_total))

    # If raise_to consumes entire stack, convert to all-in
    if raise_to >= player_total:
        return ('all_in', player_total)

    return ('raise', raise_to)
