"""Stack-depth helpers shared across bot controllers.

"Effective stack" here means the poker concept — the smaller of hero's
total chips and the largest active opponent's total chips. That's the most
you can actually win or lose this hand, and it's the right depth signal for
SPR, push/fold, and short-stack heuristics.

Each player's total = remaining `stack` + chips already committed this
street (`bet`). Including `bet` is load-bearing: when an opponent is all-in
their `stack` is 0 but their `bet` holds the amount at risk, so a hero facing
an all-in (or facing a table of all-ins) still reads the true effective depth
instead of collapsing to 0. (This matches the all-in test in poker_game.py,
`amount == player.stack + player.bet`, and the push/fold lookup, which is now
routed through this same helper rather than a divergent inline copy.)

Note: `BettingContext.effective_stack` in `betting_context.py` is a
different (per-action) quantity — "stack remaining after calling" — and
is unrelated to the helpers in this module.
"""

from __future__ import annotations

from typing import Optional

ANTE_FALLBACK_BB = 50


def big_blind_of(game_state, default: int = ANTE_FALLBACK_BB) -> int:
    """Big blind for this hand. Reads `game_state.current_ante` (the
    canonical field — see `poker_game.py`). Falls back to `default` only
    when the attribute is missing or non-positive.
    """
    bb = getattr(game_state, 'current_ante', None) or 0
    if bb <= 0:
        return default
    return int(bb)


def _player_total(p) -> int:
    """A player's total chips in play: remaining stack + chips committed this
    street. Counts an all-in player's at-risk `bet` (their `stack` is 0)."""
    return int(getattr(p, 'stack', 0) or 0) + int(getattr(p, 'bet', 0) or 0)


def _active_opponent_stacks(game_state, hero_name: str) -> list[int]:
    return [
        _player_total(p)
        for p in game_state.players
        if p.name != hero_name and not getattr(p, 'is_folded', False)
    ]


def effective_stack_chips(game_state, hero) -> int:
    """min(hero total, max active opponent total) — the most you can win or
    lose this hand, where each total is remaining stack + committed bet (see
    module docstring). Falls back to hero's total alone if no active opponents
    remain.
    """
    hero_total = _player_total(hero)
    opp_stacks = _active_opponent_stacks(game_state, hero.name)
    if not opp_stacks:
        return hero_total
    return min(hero_total, max(opp_stacks))


def effective_stack_bb(
    game_state,
    hero,
    big_blind: Optional[int] = None,
) -> float:
    """Effective stack measured in big blinds."""
    bb = big_blind if (big_blind and big_blind > 0) else big_blind_of(game_state)
    return effective_stack_chips(game_state, hero) / bb


def spr(game_state, hero, pot_total: Optional[float] = None) -> float:
    """Stack-to-pot ratio using effective stack. Returns +inf for an
    empty pot. Pass `pot_total` to override the value read from
    `game_state.pot['total']`.
    """
    if pot_total is None:
        pot = getattr(game_state, 'pot', None)
        pot_total = pot.get('total', 0) if isinstance(pot, dict) else 0
    if not pot_total or pot_total <= 0:
        return float('inf')
    return effective_stack_chips(game_state, hero) / float(pot_total)
