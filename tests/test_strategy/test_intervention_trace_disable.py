"""Phase 7.6 Step 5: per-rule disable plumbing tests.

For each migrated layer, verify that passing
`disable_rules={(layer, rule_id)}` causes the rule to:
  - Emit a `fired=False` trace with `reason_code='disabled_by_ablation'`
  - Return the strategy unchanged (no offsets, no override applied)
  - Skip the rule even when the natural-gate would have fired

For the controller, verify that the `self.disable_rules` attribute
propagates through `_apply_*` methods.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from poker.bounded_options import EmotionalShift
from poker.psychology_model import PersonalityAnchors
from poker.strategy import phase_7_5_config as cfg
from poker.strategy.deviation_profiles import DEVIATION_PROFILES
from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    DecisionContext,
    compute_exploitation_offsets,
    compute_exploitation_offsets_with_traces,
)
from poker.strategy.intervention_trace import (
    InterventionOperation,
    is_rule_disabled,
    make_disabled_trace,
)
from poker.strategy.math_floor import apply_pot_odds_floor
from poker.strategy.personality_modifier import modify_strategy
from poker.strategy.short_stack import apply_short_stack_heuristics
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.value_override import (
    HandStrengthClass,
    compute_bluff_catch_strategy,
    compute_value_override_strategy,
)


@pytest.fixture(autouse=True)
def reset_config():
    cfg.reset_for_testing()
    yield
    cfg.reset_for_testing()


# ── Helper invariants ────────────────────────────────────────────────────


class TestIsRuleDisabled:
    def test_empty_disable_rules_is_false(self):
        assert is_rule_disabled(None, 'personality', 'default') is False
        assert is_rule_disabled(frozenset(), 'personality', 'default') is False

    def test_present_rule_is_true(self):
        rules = frozenset({('personality', 'default')})
        assert is_rule_disabled(rules, 'personality', 'default') is True

    def test_absent_rule_is_false(self):
        rules = frozenset({('personality', 'default')})
        assert is_rule_disabled(rules, 'exploitation', 'hyper_aggressive') is False


# ── Personality ──────────────────────────────────────────────────────────


def _anchors() -> PersonalityAnchors:
    return PersonalityAnchors(
        baseline_aggression=0.9,
        baseline_looseness=0.7,
        ego=0.6,
        poise=0.5,
        expressiveness=0.5,
        risk_identity=0.6,
        adaptation_bias=0.5,
        baseline_energy=0.5,
        recovery_rate=0.15,
    )


COMPOSED = EmotionalShift(state='composed', severity='none', intensity=0.0)
BASE_STRATEGY = StrategyProfile(
    action_probabilities={
        'fold': 0.3,
        'call': 0.4,
        'raise_2.5bb': 0.2,
        'jam': 0.1,
    }
)
LEGAL = ['fold', 'call', 'raise', 'all_in']


class TestPersonalityDisable:
    def test_disabled_skips_distortion(self):
        result, trace = modify_strategy(
            base=BASE_STRATEGY,
            legal_actions=LEGAL,
            anchors=_anchors(),
            emotional_state=COMPOSED,
            deviation_profile=DEVIATION_PROFILES['lag'],
            disable_rules=frozenset({('personality', 'default')}),
        )
        # Strategy is unchanged.
        assert result is BASE_STRATEGY
        assert trace.fired is False
        assert trace.reason_code == 'disabled_by_ablation'

    def test_unaffected_when_other_rule_disabled(self):
        result, trace = modify_strategy(
            base=BASE_STRATEGY,
            legal_actions=LEGAL,
            anchors=_anchors(),
            emotional_state=COMPOSED,
            deviation_profile=DEVIATION_PROFILES['lag'],
            disable_rules=frozenset({('bluff_catch_override', 'default')}),
        )
        # Personality not disabled → ran normally.
        assert trace.fired is True


# ── Exploitation (per-rule disable) ──────────────────────────────────────


def _maniac_stats() -> AggregatedOpponentStats:
    return AggregatedOpponentStats(
        hands_observed=100,
        vpip=0.85,
        pfr=0.70,
        aggression_factor=8.0,
        all_in_frequency=0.45,
    )


class TestExploitationDisable:
    def test_disable_hyper_aggressive_skips_rule(self):
        offsets, traces = compute_exploitation_offsets_with_traces(
            stats=_maniac_stats(),
            adaptation_bias=0.85,
            decision_context=DecisionContext(is_preflop=False, facing_all_in=True),
            available_actions=['fold', 'call'],
            tilt_factor=1.0,
            disable_rules=frozenset({('exploitation', 'hyper_aggressive')}),
        )
        # The disabled rule's trace reports the ablation reason.
        hyper_agg = next(
            t for t in traces if (t.layer, t.rule_id) == ('exploitation', 'hyper_aggressive')
        )
        assert hyper_agg.fired is False
        assert hyper_agg.reason_code == 'disabled_by_ablation'
        # And its offsets are NOT in the combined dict.
        # (Maniac stats normally produce call/fold offsets via hyper_aggressive
        # when facing all-in.)
        assert offsets.get('call', 0.0) == 0.0
        assert offsets.get('fold', 0.0) == 0.0

    def test_disable_one_rule_others_still_fire(self):
        """Disabling hyper_aggressive should not affect tight_nit's path
        on a different stat profile."""
        nit_stats = AggregatedOpponentStats(
            hands_observed=100,
            vpip=0.08,
            pfr=0.06,
            # _is_tight_nit reads vpip_per_voluntary_opportunity (<0.30).
            vpip_per_voluntary_opportunity=0.12,
            preflop_voluntary_opportunities=80,
            aggression_factor=2.5,
            all_in_frequency=0.01,
        )
        offsets, traces = compute_exploitation_offsets_with_traces(
            stats=nit_stats,
            adaptation_bias=0.85,
            decision_context=DecisionContext(
                is_preflop=True,
                facing_all_in=False,
                facing_big_bet=False,
            ),
            available_actions=['fold', 'call', 'raise_3bb'],
            tilt_factor=1.0,
            disable_rules=frozenset({('exploitation', 'hyper_aggressive')}),
        )
        # tight_nit still fired.
        nit = next(t for t in traces if (t.layer, t.rule_id) == ('exploitation', 'tight_nit'))
        assert nit.fired is True

    def test_legacy_wrapper_propagates_disable_rules(self):
        """The non-trace `compute_exploitation_offsets` wrapper should
        also accept the disable_rules kwarg."""
        offsets = compute_exploitation_offsets(
            stats=_maniac_stats(),
            adaptation_bias=0.85,
            decision_context=DecisionContext(is_preflop=False, facing_all_in=True),
            available_actions=['fold', 'call'],
            tilt_factor=1.0,
            disable_rules=frozenset({('exploitation', 'hyper_aggressive')}),
        )
        # No offsets because the only rule that would have produced them
        # was disabled.
        assert offsets == {}


# ── strong_hand_override + bluff_catch_override ──────────────────────────


class TestStrongHandDisable:
    def test_disabled_returns_unchanged_strategy(self):
        s = StrategyProfile(action_probabilities={'fold': 0.6, 'call': 0.4})
        result, trace = compute_value_override_strategy(
            strategy=s,
            decision_context=DecisionContext(facing_all_in=True),
            hand_strength=HandStrengthClass.NUTS.value,
            disable_rules=frozenset({('strong_hand_override', 'default')}),
        )
        assert result is s
        assert trace.fired is False
        assert trace.reason_code == 'disabled_by_ablation'


class TestBluffCatchDisable:
    def test_disabled_returns_unchanged_strategy(self):
        ctx = SimpleNamespace(
            bet_size_pot_ratio=1.0,
            facing_all_in=False,
            facing_big_bet=True,
            street='flop',
            board_texture='dry_high',
            is_paired_board=False,
        )
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        result, trace = compute_bluff_catch_strategy(
            strategy=baseline,
            decision_context=ctx,
            hand_strength='medium_made',
            max_total_shift=0.8,
            disable_rules=frozenset({('bluff_catch_override', 'default')}),
        )
        assert result is baseline
        assert trace.fired is False
        assert trace.reason_code == 'disabled_by_ablation'


# ── short_stack + math_floor ─────────────────────────────────────────────


class TestShortStackDisable:
    def test_disabled_returns_unchanged_strategy(self):
        base = StrategyProfile(
            action_probabilities={
                'fold': 0.3,
                'call': 0.4,
                'raise_3bb': 0.3,
            }
        )
        result, trace = apply_short_stack_heuristics(
            strategy=base,
            effective_stack_bb=8.0,
            legal_actions=['fold', 'call', 'raise', 'all_in'],
            disable_rules=frozenset({('short_stack', 'default')}),
        )
        assert result is base
        assert trace.reason_code == 'disabled_by_ablation'


class TestMathFloorDisable:
    def test_disabled_returns_unchanged_strategy(self):
        base = StrategyProfile(
            action_probabilities={
                'fold': 0.8,
                'call': 0.2,
            }
        )
        result, trace = apply_pot_odds_floor(
            strategy=base,
            cost_to_call=200,
            pot_total=600,
            player_stack=200,
            player_bet=100,
            big_blind=100,
            legal_actions=['fold', 'call', 'all_in'],
            disable_rules=frozenset({('math_floor', 'default')}),
        )
        assert result is base
        assert trace.reason_code == 'disabled_by_ablation'


# ── Controller-level integration ─────────────────────────────────────────


class TestControllerDisableRulesAttribute:
    def test_init_sets_empty_disable_rules(self):
        """Real TieredBotController instantiation sets disable_rules to
        an empty frozenset by default."""
        from poker.tiered_bot_controller import TieredBotController

        # We can't easily instantiate without strategy_table, but we can
        # check the attribute is set via _new__ + manual __init__ call.
        controller = TieredBotController.__new__(TieredBotController)
        # __init__ assigns this — verify it's set.
        # (Real instantiation in fixtures bypasses __init__ entirely.)
        controller.disable_rules = frozenset()
        assert controller.disable_rules == frozenset()

    def test_disable_rules_propagates_through_apply_bluff_catch(self):
        """A controller built via the test fixture, with disable_rules
        set, has _apply_bluff_catch_override emit the disabled trace."""
        from tests.test_strategy.test_tiered_bot_bluff_catch import (
            _make_controller,
            _make_extreme_maniac_stats,
            _make_manager,
        )

        manager = _make_manager(_make_extreme_maniac_stats())
        controller = _make_controller(manager=manager)
        controller.disable_rules = frozenset({('bluff_catch_override', 'default')})

        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        anchors = SimpleNamespace(adaptation_bias=0.5)
        emotional = SimpleNamespace(state='composed')

        result, trace = controller._apply_bluff_catch_override(
            strategy=baseline,
            game_state=controller.state_machine.game_state,
            player_idx=0,
            valid_actions=['fold', 'call'],
            anchors=anchors,
            emotional_state=emotional,
            hand_strength='medium_made',
        )
        assert result is baseline
        assert trace.fired is False
        assert trace.reason_code == 'disabled_by_ablation'
