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

import math
import random
from typing import Optional, Tuple

from .action_vocab import ENGINE_ONLY_TOKENS, AbstractAction, EngineAction


def _raise_unknown_abstract(abstract_action: str):
    """Raise for an abstract token the resolvers can't size.

    We do NOT alias engine tokens to their abstract equivalents — that would
    hide a real producer bug. Instead, when an engine-only token (``all_in`` /
    ``bet`` / ``raise``) leaked into the abstract vocabulary, say so precisely so
    the fix lands on the producer, not here. See poker/strategy/action_vocab.py.
    """
    if abstract_action.strip().lower() in ENGINE_ONLY_TOKENS:
        raise ValueError(
            f"Engine action {abstract_action!r} leaked into the abstract strategy "
            f"vocabulary — a producer wrote an engine token into a StrategyProfile. "
            f"Emit the abstract token instead (e.g. {AbstractAction.JAM.value!r} for "
            f"a shove); see poker/strategy/action_vocab.py."
        )
    raise ValueError(f"Unknown abstract action: {abstract_action!r}")


def _nice_step(raw: float) -> int:
    """Snap a raw step to a clean chip denomination (…, 1, 2, 2.5, 5, 10, 25, 50,
    100, 250, … — the 1/2/2.5/5×10ⁿ ladder people actually use), so the rounded
    bet lands on human amounts at any blind level (bb/4 = 12.5 → step 10, not 12)."""
    if raw <= 1:
        return 1
    mag = 10 ** math.floor(math.log10(raw))
    best = mag
    for f in (1, 2, 2.5, 5, 10):
        cand = f * mag
        if abs(cand - raw) < abs(best - raw):
            best = cand
    return max(1, int(round(best)))


def round_to_human_bet(amount: float, big_blind: int) -> int:
    """Round a raise/bet amount to a natural chip increment, the way people bet.

    A jittered raise of 287 reads as a bot tell — humans bet round numbers (275,
    300). Round to a step that scales with the bet size (finer for small opens,
    coarser for big 4-bets) and snaps to a clean denomination, so the jitter's
    *variety* survives but lands on human-looking amounts:

      * < 5 BB  (opens)        → ~quarter-BB steps (25 at BB=100)
      * 5–15 BB (3-bets)       → ~half-BB steps    (50)
      * ≥ 15 BB (4-bets/deep)  → ~whole-BB steps    (100)

    Applied only on the LIVE path (callers gate on jitter>0) so deterministic
    sim / Baseline-GTO sizes stay exact. ``big_blind<=0`` → plain int rounding.
    """
    if amount <= 0 or big_blind <= 0:
        return int(round(amount))
    bb = big_blind
    if amount < 5 * bb:
        raw_step = bb / 4.0
    elif amount < 15 * bb:
        raw_step = bb / 2.0
    else:
        raw_step = float(bb)
    step = _nice_step(raw_step)
    return int(round(amount / step) * step)


def _compute_raise_to(
    multiplier: float,
    base_amount: int,
    min_raise: int,
    max_raise: int,
    rng: Optional[random.Random] = None,
    jitter: float = 0.0,
    big_blind: int = 0,
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
        big_blind: blind unit for human-rounding the jittered amount. Only used
            when jitter fires (live path); 0 keeps plain integer rounding.
    """
    target = multiplier * base_amount
    if rng is not None and jitter > 0.0:
        # Sample uniformly from [target*(1-jitter), target*(1+jitter)], then snap
        # to a natural chip increment so the jitter doesn't read as a bot tell.
        low = target * (1.0 - jitter)
        high = target * (1.0 + jitter)
        target = round_to_human_bet(rng.uniform(low, high), big_blind)
    target_int = int(round(target))
    return max(min_raise, min(target_int, max_raise))


def resolve_preflop_sizing(
    abstract_action: str,
    game_state,
    player_idx: int,
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

    if action == AbstractAction.JAM:
        return (EngineAction.ALL_IN.value, player_total)

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
            multiplier,
            big_blind,
            min_raise,
            player_total,
            rng=rng,
            jitter=sizing_jitter,
            big_blind=big_blind,
        )
    elif action.endswith('x'):
        # Multiplier of current bet: raise_3x, raise_4x, raise_2.2x
        multiplier = float(action.replace('raise_', '').replace('x', ''))
        raise_to = _compute_raise_to(
            multiplier,
            highest_bet,
            min_raise,
            player_total,
            rng=rng,
            jitter=sizing_jitter,
            big_blind=big_blind,
        )
    else:
        _raise_unknown_abstract(abstract_action)

    # If raise_to consumes entire stack, convert to all-in
    if raise_to >= player_total:
        return ('all_in', player_total)

    return ('raise', raise_to)


def resolve_postflop_sizing(
    abstract_action: str,
    game_state,
    player_idx: int,
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

    if action == AbstractAction.JAM:
        return (EngineAction.ALL_IN.value, player_total)

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
            target = rng.uniform(target * (1.0 - sizing_jitter), target * (1.0 + sizing_jitter))
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
            target = rng.uniform(target * (1.0 - sizing_jitter), target * (1.0 + sizing_jitter))
        raise_to = highest_bet + int(target)
    else:
        _raise_unknown_abstract(abstract_action)

    # Snap the live (jittered) bet to a natural chip increment so it doesn't read
    # as a bot tell — same realism pass as preflop. Live-only (jitter>0); the
    # deterministic sim/Baseline path keeps exact pot-fraction sizing.
    if rng is not None and sizing_jitter > 0.0:
        raise_to = round_to_human_bet(raise_to, game_state.current_ante)

    # Clamp to legal bounds
    raise_to = max(min_raise, min(raise_to, player_total))

    # If raise_to consumes entire stack, convert to all-in
    if raise_to >= player_total:
        return ('all_in', player_total)

    return ('raise', raise_to)
