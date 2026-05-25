"""Phase B Item 4: TrapBaitBot strategy tests.

Verifies the OOP check-then-barrel pattern:
  - On flop OOP first-to-act, checks ~70% of the time (sample-based check)
  - On turn first-to-act after a check-through flop, barrels hard
    (delegates to maniac)
  - On flop IP (e.g. SB seat in HU) or facing a bet, does NOT trigger
    the trap-bait check — delegates to maniac

Target: be a known-good opponent for the open-spot IP induce branch
so the `flop_check_then_barrel_rate` stat for TrapBaitBot converges
to a known-high value on the hero's side (≥ 0.55 within 50 hands).
"""

import random

import pytest

from poker.rule_strategies import (
    BUILT_IN_STRATEGIES,
    CHAOS_BOTS,
    _strategy_trap_bait,
)


def _ctx(**overrides):
    base = {
        'player_name': 'TrapBait',
        'player_stack': 5000,
        'stack_bb': 50.0,
        'pot_total': 300,
        'pot_odds': None,
        'cost_to_call': 0,
        'highest_bet': 0,
        'min_raise': 200,
        'max_raise': 5000,
        'big_blind': 100,
        'equity': 0.55,
        'canonical_hand': 'AKo',
        'hole_cards': ['Ah', 'Kd'],
        'community_cards': ['2h', '7d', 'Jc'],
        'phase': 'FLOP',
        'position': 'big_blind_player',
        'num_opponents': 1,
        'effective_stack': 5000,
        'effective_stack_bb': 50.0,
        'spr': 16.67,
        'valid_actions': ['check', 'raise'],
    }
    base.update(overrides)
    return base


class TestTrapBaitRegistration:
    def test_strategy_registered(self):
        assert 'trap_bait' in BUILT_IN_STRATEGIES

    def test_chaos_bot_preset_registered(self):
        assert 'trap_bait' in CHAOS_BOTS
        assert CHAOS_BOTS['trap_bait'].name == 'TrapBaitBot'
        assert CHAOS_BOTS['trap_bait'].strategy == 'trap_bait'


class TestFlopOOPCheckBehavior:
    def test_flop_oop_first_to_act_checks_around_70pct(self):
        """Aggregate behavior: over many samples, check-rate ~70%."""
        random.seed(0)  # seed module-level rng so .Random() is reproducible
        n = 2000
        checks = 0
        for _ in range(n):
            decision = _strategy_trap_bait(_ctx())
            if decision['action'] == 'check':
                checks += 1
        observed_rate = checks / n
        # Wide tolerance — rule_strategies uses a fresh Random per call
        # which is system-entropy seeded, so reproducibility within a
        # single test run is impractical. The check still detects
        # gross deviations (e.g. 0% or 100%).
        assert 0.60 < observed_rate < 0.80, (
            f'Expected ~70% check rate, got {observed_rate:.3f}'
        )

    def test_flop_ip_does_not_force_check(self):
        """SB seat (IP postflop in HU) skips the trap-bait check — should
        delegate to maniac (raise-heavy on most hands)."""
        # 100 trials at IP — at least most should be raises (maniac default
        # with 0.55 equity raises 75% of pot).
        raises = 0
        for _ in range(50):
            decision = _strategy_trap_bait(_ctx(position='small_blind_player'))
            if decision['action'] == 'raise':
                raises += 1
        assert raises >= 40, (
            f'Maniac fallback should raise most of the time IP, got {raises}/50'
        )

    def test_flop_oop_facing_bet_does_not_check(self):
        """When facing a bet (cost > 0), trap-bait check skipped — delegates
        to maniac call/fold logic."""
        for _ in range(20):
            decision = _strategy_trap_bait(_ctx(
                cost_to_call=300,
                pot_odds=2.0,
                valid_actions=['fold', 'call', 'raise'],
            ))
            assert decision['action'] != 'check'


class TestTurnRiverBarrelBehavior:
    """After a check-through flop, TrapBaitBot acts first on the turn. It
    must barrel hard (delegates to _strategy_maniac, which raises when
    raise is available)."""

    def test_turn_first_to_act_barrels(self):
        """Turn OOP first-to-act with moderate equity → raise (maniac)."""
        decision = _strategy_trap_bait(_ctx(
            phase='TURN',
            community_cards=['2h', '7d', 'Jc', '3s'],
            valid_actions=['check', 'raise'],
        ))
        assert decision['action'] == 'raise'

    def test_river_first_to_act_barrels(self):
        """River OOP first-to-act with moderate equity → raise (maniac)."""
        decision = _strategy_trap_bait(_ctx(
            phase='RIVER',
            community_cards=['2h', '7d', 'Jc', '3s', '9h'],
            valid_actions=['check', 'raise'],
        ))
        assert decision['action'] == 'raise'

    def test_preflop_delegates_to_maniac(self):
        """Preflop: trap-bait is dormant; maniac raises most hands."""
        raises = 0
        for _ in range(20):
            decision = _strategy_trap_bait(_ctx(
                phase='PRE_FLOP',
                community_cards=[],
                cost_to_call=100,
                pot_odds=2.0,
                valid_actions=['fold', 'call', 'raise'],
            ))
            if decision['action'] == 'raise':
                raises += 1
        assert raises >= 15, (
            f'Preflop maniac should raise most hands with 0.55 equity, got {raises}/20'
        )
