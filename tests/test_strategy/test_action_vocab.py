"""Canonical action vocabularies (poker.strategy.action_vocab) + the resolver
boundary that keeps engine tokens out of the abstract strategy space.

Regression for the deep-stack call-off crash: the bluff-catch override copied
the engine token 'all_in' into an abstract StrategyProfile, which the
action_mapper then couldn't size ("Unknown abstract action: 'all_in'"). The fix
is a single source of truth for the two vocabularies + the producer translating
via abstract_call_token — not defensive aliasing in the resolver.
"""

from types import SimpleNamespace

import pytest

from poker.strategy.action_mapper import resolve_postflop_sizing, resolve_preflop_sizing
from poker.strategy.action_vocab import (
    AbstractAction,
    EngineAction,
    abstract_call_token,
    is_resolvable,
    is_sized,
)
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.value_override import compute_bluff_catch_strategy


def _gs(stack=10000, bet=0, current_ante=100, highest_bet=100, pot_total=300):
    player = SimpleNamespace(stack=stack, bet=bet)
    return SimpleNamespace(
        players=[player],
        current_ante=current_ante,
        highest_bet=highest_bet,
        min_raise_amount=current_ante,
        pot={'total': pot_total},
    )


class TestAbstractCallToken:
    def test_call_legal_returns_call(self):
        assert abstract_call_token(['fold', 'call', 'raise', 'all_in']) == AbstractAction.CALL.value

    def test_calloff_when_call_illegal_returns_jam(self):
        # Facing a bet >= stack: the engine offers all_in instead of call.
        assert abstract_call_token(['fold', 'all_in']) == AbstractAction.JAM.value

    def test_never_returns_engine_all_in(self):
        assert abstract_call_token(['fold', 'all_in']) != EngineAction.ALL_IN.value

    def test_empty_defaults_to_call(self):
        assert abstract_call_token([]) == AbstractAction.CALL.value


class TestVocabulary:
    def test_fixed_and_sized_are_resolvable(self):
        for a in (
            'fold',
            'check',
            'call',
            'jam',
            'bet_33',
            'bet_100',
            'raise_67',
            'raise_150',
            'raise_2.5bb',
        ):
            assert is_resolvable(a), a

    def test_engine_only_tokens_not_resolvable(self):
        for a in ('all_in', 'bet', 'raise'):
            assert not is_resolvable(a), a

    def test_is_sized(self):
        assert is_sized('bet_67') and is_sized('raise_150')
        assert not is_sized('jam') and not is_sized('call')


class TestResolverBoundary:
    def test_jam_resolves_to_all_in(self):
        gs = _gs(stack=5000, bet=0)
        assert resolve_postflop_sizing('jam', gs, 0) == ('all_in', 5000)
        assert resolve_preflop_sizing('jam', gs, 0) == ('all_in', 5000)

    def test_engine_all_in_token_raises_precisely(self):
        # No silent aliasing: an engine token in the abstract slot is a producer
        # bug, surfaced with a pointer to the vocabulary — not mapped away.
        gs = _gs()
        for resolve in (resolve_postflop_sizing, resolve_preflop_sizing):
            with pytest.raises(ValueError, match="leaked into the abstract"):
                resolve('all_in', gs, 0)

    def test_every_fixed_abstract_action_resolves(self):
        # Closed loop: each fixed abstract token a producer may emit must be
        # sizable — no token can be emitted-but-unresolvable.
        gs = _gs(stack=5000, bet=0, highest_bet=200, pot_total=400)
        for a in (
            AbstractAction.FOLD,
            AbstractAction.CHECK,
            AbstractAction.CALL,
            AbstractAction.JAM,
        ):
            resolve_postflop_sizing(a.value, gs, 0)  # must not raise


class TestBluffCatchEmitsAbstractJam:
    def test_calloff_emits_jam_not_engine_all_in(self):
        # The exact deep-stack spot that crashed: call illegal, all_in legal.
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = SimpleNamespace(
            bet_size_pot_ratio=0.5,
            facing_all_in=True,
            facing_big_bet=True,
            street='flop',
            board_texture='dry_high',
            is_paired_board=False,
        )
        strategy, _ = compute_bluff_catch_strategy(
            baseline,
            ctx,
            'medium_made',
            max_total_shift=0.9,
            legal_actions=['fold', 'all_in'],
        )
        keys = set(strategy.action_probabilities)
        # The engine token must NEVER appear in an abstract profile...
        assert 'all_in' not in keys
        # ...and every key the override emits must be resolvable (the original
        # crash — sampling an unresolvable 'all_in' — can no longer recur).
        assert all(is_resolvable(k) for k in keys), keys
