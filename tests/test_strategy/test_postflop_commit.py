"""Unit tests for poker/strategy/postflop_commit.py.

Low-SPR value commitment: at SPR < 2 with a value hand (nuts/strong_made),
funnel the passive action + small bets/raises into a jam. No-op otherwise.
"""

import pytest

from poker.strategy.postflop_commit import (
    apply_postflop_commit,
    _COMMIT_FRACTION,
    VALUE_CLASSES,
)
from poker.strategy.strategy_profile import StrategyProfile

LEGAL = ['fold', 'call', 'check', 'bet', 'raise', 'all_in']


def _probs(strategy):
    return strategy.action_probabilities


# ── Fires: value + low SPR ───────────────────────────────────────────────

class TestCommitFires:
    def test_nuts_unopened_check_and_small_bets_to_jam(self):
        # High-SPR nuts strategy that the SPR fallback would hand us.
        strat = StrategyProfile({'bet_33': 0.5, 'bet_67': 0.41, 'check': 0.09})
        out, trace = apply_postflop_commit(
            strat, spr_bucket='low', hand_class='nuts',
            facing_action='unopened', legal_actions=LEGAL,
        )
        p = _probs(out)
        assert trace.fired
        # 85% of all non-jam mass (everything here) → jam.
        assert p['jam'] == pytest.approx(0.85, abs=0.01)
        assert p.get('check', 0) == pytest.approx(0.09 * 0.15, abs=0.01)
        assert sum(p.values()) == pytest.approx(1.0, abs=1e-6)

    def test_nuts_facing_bet_call_to_jam(self):
        strat = StrategyProfile({'call': 0.4, 'raise_67': 0.3, 'raise_150': 0.2, 'jam': 0.1})
        out, trace = apply_postflop_commit(
            strat, spr_bucket='low', hand_class='nuts',
            facing_action='facing_bet', legal_actions=LEGAL,
        )
        p = _probs(out)
        assert trace.fired
        # call + raises (0.9 non-jam) → 85% to jam, on top of existing 0.1.
        assert p['jam'] == pytest.approx(0.1 + 0.9 * 0.85, abs=0.01)
        assert p['call'] == pytest.approx(0.4 * 0.15, abs=0.01)

    def test_strong_made_uses_lower_fraction(self):
        strat = StrategyProfile({'check': 1.0})
        out, _ = apply_postflop_commit(
            strat, spr_bucket='low', hand_class='strong_made',
            facing_action='unopened', legal_actions=LEGAL,
        )
        assert _probs(out)['jam'] == pytest.approx(_COMMIT_FRACTION['strong_made'], abs=0.01)

    def test_jam_becomes_primary_action(self):
        strat = StrategyProfile({'check': 0.6, 'bet_33': 0.4})
        out, trace = apply_postflop_commit(
            strat, spr_bucket='low', hand_class='nuts',
            facing_action='unopened', legal_actions=LEGAL,
        )
        assert trace.action_changed
        assert max(_probs(out), key=_probs(out).get) == 'jam'


# ── No-ops ───────────────────────────────────────────────────────────────

class TestCommitNoOps:
    @pytest.mark.parametrize('spr', ['high', 'medium', None])
    def test_no_op_when_spr_not_low(self, spr):
        strat = StrategyProfile({'check': 0.5, 'bet_67': 0.5})
        out, trace = apply_postflop_commit(
            strat, spr_bucket=spr, hand_class='nuts',
            facing_action='unopened', legal_actions=LEGAL,
        )
        assert not trace.fired
        assert _probs(out) == _probs(strat)

    @pytest.mark.parametrize('hand_class', ['medium_made', 'weak_made', 'air', None])
    def test_no_op_for_non_value_class(self, hand_class):
        strat = StrategyProfile({'check': 0.5, 'bet_67': 0.5})
        out, trace = apply_postflop_commit(
            strat, spr_bucket='low', hand_class=hand_class,
            facing_action='unopened', legal_actions=LEGAL,
        )
        assert not trace.fired
        assert _probs(out) == _probs(strat)

    def test_no_op_when_jam_illegal(self):
        # No all_in legal and no jam in the strategy → can't commit.
        strat = StrategyProfile({'check': 0.5, 'bet_67': 0.5})
        out, trace = apply_postflop_commit(
            strat, spr_bucket='low', hand_class='nuts',
            facing_action='unopened', legal_actions=['check', 'bet'],
        )
        assert not trace.fired
        assert _probs(out) == _probs(strat)

    def test_no_op_when_no_movable_mass(self):
        # Pure jam already — nothing non-jam to move.
        strat = StrategyProfile({'jam': 1.0})
        out, trace = apply_postflop_commit(
            strat, spr_bucket='low', hand_class='nuts',
            facing_action='unopened', legal_actions=LEGAL,
        )
        assert not trace.fired

    def test_disabled_rule_is_no_op(self):
        strat = StrategyProfile({'check': 1.0})
        out, trace = apply_postflop_commit(
            strat, spr_bucket='low', hand_class='nuts',
            facing_action='unopened', legal_actions=LEGAL,
            disable_rules=frozenset({('postflop_commit', 'default')}),
        )
        assert not trace.fired
        assert _probs(out) == _probs(strat)


# ── Invariants ───────────────────────────────────────────────────────────

class TestCommitInvariants:
    def test_value_classes_are_nuts_and_strong(self):
        assert VALUE_CLASSES == frozenset({'nuts', 'strong_made'})

    def test_distribution_normalized_after_commit(self):
        strat = StrategyProfile({'call': 0.34, 'raise_67': 0.28, 'raise_150': 0.22, 'jam': 0.16})
        out, _ = apply_postflop_commit(
            strat, spr_bucket='low', hand_class='nuts',
            facing_action='facing_bet', legal_actions=LEGAL,
        )
        assert sum(_probs(out).values()) == pytest.approx(1.0, abs=1e-6)

    def test_draws_not_committed_even_if_strong(self):
        # 'strong_draw' is not a made-value class → untouched.
        strat = StrategyProfile({'check': 0.5, 'bet_67': 0.5})
        out, trace = apply_postflop_commit(
            strat, spr_bucket='low', hand_class='strong_draw',
            facing_action='unopened', legal_actions=LEGAL,
        )
        assert not trace.fired
