"""Phase 7.5 Item 1c integration tests: bluff-catch override fires
through the controller pipeline.

These tests don't mock the override's gate/builder; they exercise the
real _apply_bluff_catch_override on a TieredBotController with stub
game state, manager, and stats, and verify:
  - Override fires when all gates pass (medium_made vs EXTREME maniac,
    HU, facing a bet)
  - Override does NOT fire when hand class is wrong, tier is below
    EXTREME, hero is tilted, or not facing a bet
  - Counter increments correctly
  - Returned strategy reflects the pot-odds-conditional override

See docs/plans/PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md §Item 1c.
"""

from collections import Counter
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from poker.strategy import phase_7_5_config as cfg
from poker.strategy.exploitation import AggregatedOpponentStats
from poker.strategy.strategy_profile import StrategyProfile
from poker.tiered_bot_controller import TieredBotController


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_config():
    cfg.reset_for_testing()
    yield
    cfg.reset_for_testing()


def _make_extreme_maniac_stats():
    """Stats that classify as EXTREME tier."""
    return AggregatedOpponentStats(
        hands_observed=200, vpip=0.85, pfr=0.75,
        aggression_factor=8.0, all_in_frequency=0.40,
        aggression_factor_postflop=7.0,
        all_in_per_facing_bet=0.40,
        facing_bet_opportunities=150,
        postflop_jam_open_rate=0.05,
        postflop_open_opportunities=80,
    )


def _make_neutral_stats():
    """Stats that classify as DEFAULT tier."""
    return AggregatedOpponentStats(
        hands_observed=200, vpip=0.40, pfr=0.20,
        aggression_factor=2.0, all_in_frequency=0.02,
        aggression_factor_postflop=2.0,
        all_in_per_facing_bet=0.05,
        facing_bet_opportunities=150,
        postflop_jam_open_rate=0.02,
        postflop_open_opportunities=80,
    )


def _make_manager(stats):
    """Manager mock that returns `stats` for any get_model call."""
    manager = MagicMock()
    manager._exploitation_counters = Counter()
    manager.aggregate_active_opponents.return_value = stats
    model = MagicMock()
    model.tendencies = SimpleNamespace(
        hands_observed=stats.hands_observed,
        vpip=stats.vpip, pfr=stats.pfr,
        aggression_factor=stats.aggression_factor,
        all_in_frequency=stats.all_in_frequency,
        fold_to_cbet=stats.fold_to_cbet,
        _cbet_faced_count=stats.cbet_faced_count,
        aggression_factor_postflop=stats.aggression_factor_postflop,
        all_in_per_facing_bet=stats.all_in_per_facing_bet,
        _facing_bet_opportunities=stats.facing_bet_opportunities,
        postflop_jam_open_rate=stats.postflop_jam_open_rate,
        _postflop_open_opportunities=stats.postflop_open_opportunities,
        # Empty recent window → tier decay won't fire.
        recent_postflop_stats=lambda: AggregatedOpponentStats(),
    )
    manager.get_model.return_value = model
    manager.get_model_if_exists.return_value = model
    return manager


def _make_game_state(*, hu=True, facing_bet=True, hero_stack=10000,
                     current_player_options=None):
    """Minimal game_state stub: hero + one opponent, optional facing bet.

    pot_total = 200; call_amount = 100 (so bet/pot_before_bet = 1.0 — pot-size).
    """
    hero = SimpleNamespace(
        name='Hero', stack=hero_stack, bet=0, is_folded=False, is_human=False,
    )
    maniac = SimpleNamespace(
        name='Maniac', stack=10000, bet=100 if facing_bet else 0,
        is_folded=False, is_human=False,
    )
    players = [hero, maniac]
    return SimpleNamespace(
        players=players,
        current_player=hero,
        current_player_idx=0,
        community_cards=['Kh', '8d', '4s'],  # dry flop
        pot={'total': 200},
        call_amount=100 if facing_bet else 0,
        highest_bet=100 if facing_bet else 0,
        big_blind=100,
        current_player_options=(
            current_player_options
            if current_player_options is not None
            else ['fold', 'call', 'raise']
        ),
        table_positions={'button': 'Hero', 'big_blind_player': 'Maniac'},
    )


def _make_controller(*, manager, game_state=None, phase='FLOP'):
    """Build a TieredBotController with parent __init__ mocked."""
    with patch('poker.tiered_bot_controller.AIPlayerController.__init__',
               return_value=None):
        controller = TieredBotController.__new__(TieredBotController)

    if game_state is None:
        game_state = _make_game_state()
    phase_obj = SimpleNamespace(name=phase)
    sm = SimpleNamespace(
        game_state=game_state,
        current_phase=phase_obj,
        phase=phase_obj,
    )

    controller.player_name = 'Hero'
    controller.state_machine = sm
    controller.opponent_model_manager = manager
    controller.debug_logging = False
    controller.skip_personality_distortion = False
    controller._deviation_profile = None
    controller.psychology = None
    controller.strategy_table = None
    controller.hu_strategy_table = None
    controller._sim_last_preflop_aggressor = None
    controller._sim_recent_aggressor = None
    return controller


# ── Override fires when conditions met ───────────────────────────────────

class TestBluffCatchFires:
    def test_medium_made_vs_extreme_maniac_hu_fires(self):
        manager = _make_manager(_make_extreme_maniac_stats())
        controller = _make_controller(manager=manager)

        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        anchors = SimpleNamespace(adaptation_bias=0.5)
        emotional = SimpleNamespace(state='composed')

        result, _trace = controller._apply_bluff_catch_override(
            strategy=baseline, game_state=controller.state_machine.game_state,
            player_idx=0, valid_actions=['fold', 'call'],
            anchors=anchors, emotional_state=emotional,
            hand_strength='medium_made',
        )

        # Override fired — strategy now has nonzero call mass.
        assert 'call' in result.action_probabilities
        assert result.action_probabilities['call'] > 0.0
        # Counter recorded the fire.
        assert manager._exploitation_counters['bluff_catch_fired'] >= 1
        assert manager._exploitation_counters['bluff_catch_eligible'] >= 1

    def test_weak_made_vs_extreme_maniac_hu_fires(self):
        manager = _make_manager(_make_extreme_maniac_stats())
        controller = _make_controller(manager=manager)

        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        anchors = SimpleNamespace(adaptation_bias=0.5)
        emotional = SimpleNamespace(state='composed')

        result, _trace = controller._apply_bluff_catch_override(
            strategy=baseline, game_state=controller.state_machine.game_state,
            player_idx=0, valid_actions=['fold', 'call'],
            anchors=anchors, emotional_state=emotional,
            hand_strength='weak_made',
        )

        # Weak made facing pot-size bet, no danger → composed prob = 0.40.
        # Clamp 0.8 → L1 = 0.80 ≤ cap → returned as-is.
        assert result.action_probabilities['call'] > 0.0
        assert manager._exploitation_counters['bluff_catch_fired'] >= 1

    def test_call_off_uses_all_in_when_call_is_not_legal(self):
        """Exact/short call-off spots offer all_in instead of call. The
        override must keep continuing mass on a legal action."""
        manager = _make_manager(_make_extreme_maniac_stats())
        gs = _make_game_state(
            hero_stack=100,
            current_player_options=['fold', 'all_in'],
        )
        controller = _make_controller(manager=manager, game_state=gs)

        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        anchors = SimpleNamespace(adaptation_bias=0.5)
        emotional = SimpleNamespace(state='composed')

        result, _trace = controller._apply_bluff_catch_override(
            strategy=baseline, game_state=gs, player_idx=0,
            valid_actions=['fold', 'all_in'],
            anchors=anchors, emotional_state=emotional,
            hand_strength='medium_made',
        )

        assert 'call' not in result.action_probabilities
        assert result.action_probabilities['all_in'] > 0.0
        assert manager._exploitation_counters['bluff_catch_fired'] >= 1


# ── Override blocks when conditions fail ─────────────────────────────────

class TestBluffCatchBlocks:
    def test_strong_made_does_not_fire_bluff_catch(self):
        """Strong hand class triggers the OTHER override (strong-hand);
        bluff-catch correctly returns unchanged strategy here."""
        manager = _make_manager(_make_extreme_maniac_stats())
        controller = _make_controller(manager=manager)

        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        anchors = SimpleNamespace(adaptation_bias=0.5)
        emotional = SimpleNamespace(state='composed')

        result, _trace = controller._apply_bluff_catch_override(
            strategy=baseline, game_state=controller.state_machine.game_state,
            player_idx=0, valid_actions=['fold', 'call'],
            anchors=anchors, emotional_state=emotional,
            hand_strength='strong_made',
        )

        assert result.action_probabilities == baseline.action_probabilities
        # bluff_catch_eligible should NOT have been incremented for
        # non-trigger class (we early-out before the gate).
        assert manager._exploitation_counters['bluff_catch_eligible'] == 0
        assert manager._exploitation_counters['bluff_catch_fired'] == 0

    def test_default_tier_does_not_fire(self):
        """Neutral opponent stats → DEFAULT tier → no fire."""
        manager = _make_manager(_make_neutral_stats())
        controller = _make_controller(manager=manager)

        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        anchors = SimpleNamespace(adaptation_bias=0.5)
        emotional = SimpleNamespace(state='composed')

        result, _trace = controller._apply_bluff_catch_override(
            strategy=baseline, game_state=controller.state_machine.game_state,
            player_idx=0, valid_actions=['fold', 'call'],
            anchors=anchors, emotional_state=emotional,
            hand_strength='medium_made',
        )

        assert result.action_probabilities == baseline.action_probabilities
        # Eligible counter incremented (hand class is in trigger set),
        # but fired counter did NOT.
        assert manager._exploitation_counters['bluff_catch_eligible'] >= 1
        assert manager._exploitation_counters['bluff_catch_fired'] == 0

    def test_no_facing_bet_does_not_fire(self):
        """Open spot (no live bet) → no fire."""
        manager = _make_manager(_make_extreme_maniac_stats())
        gs = _make_game_state(facing_bet=False)
        controller = _make_controller(manager=manager, game_state=gs)

        baseline = StrategyProfile(action_probabilities={'check': 1.0})
        anchors = SimpleNamespace(adaptation_bias=0.5)
        emotional = SimpleNamespace(state='composed')

        result, _trace = controller._apply_bluff_catch_override(
            strategy=baseline, game_state=gs, player_idx=0,
            valid_actions=['check', 'bet'],
            anchors=anchors, emotional_state=emotional,
            hand_strength='medium_made',
        )
        assert result.action_probabilities == baseline.action_probabilities
        assert manager._exploitation_counters['bluff_catch_fired'] == 0

    def test_tilted_hero_does_not_fire(self):
        """Heavy tilt (state='shaken') → tilt_factor=0 → gate suppresses."""
        manager = _make_manager(_make_extreme_maniac_stats())
        controller = _make_controller(manager=manager)

        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        anchors = SimpleNamespace(adaptation_bias=0.5)
        emotional = SimpleNamespace(state='shaken')  # tilt_factor=0

        result, _trace = controller._apply_bluff_catch_override(
            strategy=baseline, game_state=controller.state_machine.game_state,
            player_idx=0, valid_actions=['fold', 'call'],
            anchors=anchors, emotional_state=emotional,
            hand_strength='medium_made',
        )

        assert result.action_probabilities == baseline.action_probabilities
        assert manager._exploitation_counters['bluff_catch_fired'] == 0
