"""Stack-depth helpers shared across bot controllers.

"Effective stack" here means the poker concept — the smaller of hero's
stack and the largest active opponent's stack. That's the most you can
actually win or lose this hand, and it's the right depth signal for
SPR, push/fold, and short-stack heuristics.

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


def _active_opponent_stacks(game_state, hero_name: str) -> list[int]:
    return [
        int(getattr(p, 'stack', 0) or 0)
        for p in game_state.players
        if p.name != hero_name and not getattr(p, 'is_folded', False)
    ]


def effective_stack_chips(game_state, hero) -> int:
    """min(hero stack, max active opponent stack) — the most you can
    win or lose this hand. Falls back to hero's stack alone if no
    active opponents remain.
    """
    hero_stack = int(getattr(hero, 'stack', 0) or 0)
    opp_stacks = _active_opponent_stacks(game_state, hero.name)
    if not opp_stacks:
        return hero_stack
    return min(hero_stack, max(opp_stacks))


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
