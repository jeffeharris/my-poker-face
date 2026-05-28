"""Canonical action vocabularies — one source of truth for the two namespaces
that must never leak into each other.

The engine and the strategy layer share the tokens ``fold`` / ``check`` /
``call``, which is exactly how an engine ``all_in`` once leaked into an abstract
``StrategyProfile`` and crashed the action_mapper (which only knows the abstract
``jam``). Keeping the vocabularies named and separate makes that class of bug a
typed boundary rather than a runtime surprise:

  * **Engine actions** — the legal-action sets the engine emits and the inputs
    ``play_turn()`` consumes: ``fold, check, call, bet, raise, all_in``.
  * **Abstract actions** — the keys of a ``StrategyProfile`` / chart JSON, and
    the input vocabulary of the ``action_mapper`` resolvers: ``fold, check,
    call, jam`` plus the *sized* families ``bet_<pct>`` / ``raise_<pct>``
    (postflop) and ``raise_<x>bb`` / ``raise_<x>x`` (preflop). ``JAM`` is the
    abstract token the resolver maps to engine ``('all_in', whole stack)`` —
    **there is no abstract ``all_in``.**

A producer that builds a ``StrategyProfile`` from the engine's ``legal_actions``
(e.g. a call-off where ``call`` is illegal but ``all_in`` is) must translate to
the abstract vocabulary via :func:`abstract_call_token`, never copy the raw
engine token into the profile.
"""

from enum import Enum


class EngineAction(str, Enum):
    """What the game engine emits as legal actions and ``play_turn`` consumes."""

    FOLD = 'fold'
    CHECK = 'check'
    CALL = 'call'
    BET = 'bet'
    RAISE = 'raise'
    ALL_IN = 'all_in'


class AbstractAction(str, Enum):
    """The fixed (non-sized) strategy/chart vocabulary.

    Sized raises/bets (``bet_<pct>``, ``raise_<pct>``, ``raise_<x>bb``,
    ``raise_<x>x``) are open families, not enum members — test them with
    :func:`is_sized` / :func:`is_resolvable`. ``JAM`` resolves to engine
    ``('all_in', whole stack)`` and is distinct from ``EngineAction.ALL_IN``.
    """

    FOLD = 'fold'
    CHECK = 'check'
    CALL = 'call'
    JAM = 'jam'


# Engine tokens that must never appear as abstract strategy keys (the shared
# fold/check/call are valid in both spaces; these are engine-only).
ENGINE_ONLY_TOKENS = frozenset(
    {EngineAction.BET.value, EngineAction.RAISE.value, EngineAction.ALL_IN.value}
)

_ABSTRACT_FIXED = frozenset(a.value for a in AbstractAction)


def is_sized(action: str) -> bool:
    """A sized abstract action: ``bet_<pct>`` / ``raise_<pct>`` / ``raise_<x>bb`` / ``raise_<x>x``."""
    return action.startswith('bet_') or action.startswith('raise_')


def is_resolvable(action: str) -> bool:
    """True iff the ``action_mapper`` resolvers can size this abstract token."""
    return action in _ABSTRACT_FIXED or is_sized(action)


def abstract_call_token(legal_actions) -> str:
    """The abstract token for "call" given the engine's legal actions.

    When ``call`` is illegal but ``all_in`` is — the call amount is ≥ the
    player's stack, so calling is a call-off — the abstract representation is
    ``JAM`` (the resolver maps it to engine ``('all_in', whole stack)``), not
    the raw engine ``all_in``. Returns ``CALL`` otherwise. This is the one
    translation that previously copied the engine token straight into an
    abstract ``StrategyProfile``.
    """
    legal = set(legal_actions or ())
    if EngineAction.CALL.value not in legal and EngineAction.ALL_IN.value in legal:
        return AbstractAction.JAM.value
    return AbstractAction.CALL.value
