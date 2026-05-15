"""Tests for opportunity-normalized preflop VPIP/PFR counters.

These counters fix the 1/N scaling problem with hands-dealt-normalized
VPIP and PFR. A ManiacBot raising 100% of preflop opportunities should
score `pfr_per_open_opportunity = 1.0` at every table size (HU/3p/6max),
whereas the legacy `pfr` would read 0.50 / 0.33 / 0.17 due to seat
rotation. Tests cover:

  - Once-per-hand denominator semantics (mirrors _vpip_count / _pfr_count
    so ratios stay bounded by 1.0 for 100%-action opponents).
  - Distinction between open opportunities and voluntary opportunities.
  - Numerator distinction between PFR (any raise) and open-raises (the
    counter for `pfr_per_open_opportunity`).
  - Neutral prior 0.5 until ≥1 opportunity observed.
  - Cold-start gate unchanged (legacy hands_observed gate).
  - Through-the-stack integration via AIMemoryManager.on_action.
"""

from types import SimpleNamespace

import pytest

from poker.memory.memory_manager import AIMemoryManager
from poker.memory.opponent_model import OpponentTendencies


# ── Direct OpponentTendencies tests ─────────────────────────────────────


class TestOpportunityCounters:
    def test_neutral_prior_with_no_observations(self):
        t = OpponentTendencies()
        # Default values: 0.5 (mirrors fold_to_cbet's neutral prior).
        assert t.pfr_per_open_opportunity == 0.5
        assert t.vpip_per_voluntary_opportunity == 0.5
        assert t._preflop_open_opportunities == 0
        assert t._preflop_voluntary_opportunities == 0
        assert t._preflop_open_raise_count == 0
        assert t._preflop_voluntary_action_count == 0

    def test_open_raise_increments_pfr_per_open(self):
        """A preflop open raise increments both numerator and denominator."""
        t = OpponentTendencies()
        t.record_hand_dealt()
        t.update_from_action(
            'raise', 'PRE_FLOP', is_voluntary=True,
            count_hand=True, was_facing_bet=False,
        )
        assert t._preflop_open_opportunities == 1
        assert t._preflop_open_raise_count == 1
        assert t.pfr_per_open_opportunity == 1.0
        # VPIP-side also ticks (raise = voluntary chip commit).
        assert t._preflop_voluntary_opportunities == 1
        assert t._preflop_voluntary_action_count == 1
        assert t.vpip_per_voluntary_opportunity == 1.0

    def test_fold_increments_opportunity_not_action(self):
        """A preflop fold counts as an opportunity but NOT an action."""
        t = OpponentTendencies()
        t.record_hand_dealt()
        t.update_from_action(
            'fold', 'PRE_FLOP', is_voluntary=True,
            count_hand=True, was_facing_bet=True,
        )
        # Voluntary opp ticks. Open opp does NOT (was_facing_bet=True).
        assert t._preflop_voluntary_opportunities == 1
        assert t._preflop_open_opportunities == 0
        # Numerators don't tick (fold ≠ chip commit).
        assert t._preflop_voluntary_action_count == 0
        assert t._preflop_open_raise_count == 0
        # Resulting rates.
        assert t.vpip_per_voluntary_opportunity == 0.0
        # No opp on the open denominator → stays at neutral prior.
        assert t.pfr_per_open_opportunity == 0.5

    def test_check_open_opportunity_no_action_count(self):
        """A BB check with no live raise = open opportunity but no action."""
        t = OpponentTendencies()
        t.record_hand_dealt()
        t.update_from_action(
            'check', 'PRE_FLOP', is_voluntary=True,
            count_hand=True, was_facing_bet=False,
        )
        # Both opp denominators tick.
        assert t._preflop_voluntary_opportunities == 1
        assert t._preflop_open_opportunities == 1
        # No chip-commit → numerators stay at 0.
        assert t._preflop_voluntary_action_count == 0
        assert t._preflop_open_raise_count == 0

    def test_call_facing_raise_voluntary_but_not_open(self):
        """A preflop call facing a raise = voluntary action, NOT open."""
        t = OpponentTendencies()
        t.record_hand_dealt()
        t.update_from_action(
            'call', 'PRE_FLOP', is_voluntary=True,
            count_hand=True, was_facing_bet=True,
        )
        # Voluntary opp + action tick.
        assert t._preflop_voluntary_opportunities == 1
        assert t._preflop_voluntary_action_count == 1
        # Open opp does NOT (facing a bet).
        assert t._preflop_open_opportunities == 0
        assert t._preflop_open_raise_count == 0

    def test_three_bet_does_not_count_as_open_raise(self):
        """A 3-bet (raise facing a raise) is a PFR but NOT an open raise.

        Legacy `_pfr_count` would tick for 3-bets too, which is why
        `_pfr_count / open_opportunities` can exceed 1.0. The
        opportunity-normalized field uses a separate counter that
        only ticks when raising as the first voluntary raiser.
        """
        t = OpponentTendencies()
        t.record_hand_dealt()
        t.update_from_action(
            'raise', 'PRE_FLOP', is_voluntary=True,
            count_hand=True, was_facing_bet=True,  # 3-bet!
        )
        # Legacy PFR still ticks.
        assert t._pfr_count == 1
        # But the opp-normalized numerator does NOT (no open opportunity).
        assert t._preflop_open_raise_count == 0
        assert t._preflop_open_opportunities == 0
        # Voluntary action ticks though (chip commit).
        assert t._preflop_voluntary_action_count == 1

    def test_once_per_hand_semantics(self):
        """Multiple voluntary decisions in one hand should count only once."""
        t = OpponentTendencies()
        t.record_hand_dealt()
        # Hand 1: opponent opens (raise, no facing bet), then someone
        # 3-bets them, opponent calls (raise, facing bet). Two voluntary
        # decisions but only ONE hand of data.
        t.update_from_action(
            'raise', 'PRE_FLOP', is_voluntary=True,
            count_hand=True, was_facing_bet=False,
        )
        t.update_from_action(
            'call', 'PRE_FLOP', is_voluntary=True,
            count_hand=False, was_facing_bet=True,
        )
        # Once-per-hand: denominators don't double-count.
        assert t._preflop_voluntary_opportunities == 1
        assert t._preflop_open_opportunities == 1
        # Numerators also gate once-per-hand.
        assert t._preflop_voluntary_action_count == 1
        assert t._preflop_open_raise_count == 1
        # Resulting rates stay bounded by 1.0.
        assert t.pfr_per_open_opportunity == 1.0
        assert t.vpip_per_voluntary_opportunity == 1.0

    def test_blind_post_not_an_opportunity(self):
        """Forced blind posts (sb/bb) should never tick opportunity counters."""
        t = OpponentTendencies()
        t.record_hand_dealt()
        t.update_from_action(
            'sb', 'PRE_FLOP', is_voluntary=False,
            count_hand=True, was_facing_bet=False,
        )
        assert t._preflop_voluntary_opportunities == 0
        assert t._preflop_open_opportunities == 0

    def test_skipped_when_was_facing_bet_none(self):
        """Caller couldn't determine context → skip counters entirely."""
        t = OpponentTendencies()
        t.record_hand_dealt()
        # No was_facing_bet supplied (default None).
        t.update_from_action(
            'raise', 'PRE_FLOP', is_voluntary=True, count_hand=True,
        )
        # Neither denominator nor numerator should move.
        assert t._preflop_voluntary_opportunities == 0
        assert t._preflop_open_opportunities == 0
        # Legacy _pfr_count still ticks (that path is unchanged).
        assert t._pfr_count == 1

    def test_maniac_at_hu_pfr_per_open_one(self):
        """Sim baseline check: a 100%-raising opponent at HU should
        accumulate pfr_per_open_opportunity ≈ 1.0 even though legacy
        pfr ≈ 0.5 (HU seat rotation halves the open opportunities).
        """
        t = OpponentTendencies()
        # 10 hands: 5 as SB-equivalent (open opp), 5 as BB-equivalent
        # facing a raise (not open). Opponent raises every time.
        for i in range(5):
            t.record_hand_dealt()
            t.update_from_action(
                'raise', 'PRE_FLOP', is_voluntary=True,
                count_hand=True, was_facing_bet=False,
            )
        for i in range(5):
            t.record_hand_dealt()
            t.update_from_action(
                'raise', 'PRE_FLOP', is_voluntary=True,
                count_hand=True, was_facing_bet=True,
            )
        # Legacy pfr: 10/10 hands_dealt = 1.0 (since each hand had an
        # action). But legacy pfr could be lower if not every hand
        # produced an action. This isn't the legacy 1/N test (which
        # requires the full state machine for seat-rotation accounting).
        # The key invariant: pfr_per_open ratio uses the OPEN counter,
        # which only ticks on no-facing-bet decisions.
        assert t._preflop_open_opportunities == 5
        assert t._preflop_open_raise_count == 5
        assert t.pfr_per_open_opportunity == 1.0
        # Voluntary opp counts every voluntary decision.
        assert t._preflop_voluntary_opportunities == 10
        assert t._preflop_voluntary_action_count == 10
        assert t.vpip_per_voluntary_opportunity == 1.0


class TestSerialization:
    def test_to_dict_round_trip_preserves_counters(self):
        t = OpponentTendencies()
        t.record_hand_dealt()
        t.update_from_action(
            'raise', 'PRE_FLOP', is_voluntary=True,
            count_hand=True, was_facing_bet=False,
        )
        t.record_hand_dealt()
        t.update_from_action(
            'call', 'PRE_FLOP', is_voluntary=True,
            count_hand=True, was_facing_bet=True,
        )
        data = t.to_dict()
        t2 = OpponentTendencies.from_dict(data)
        assert t2._preflop_voluntary_opportunities == t._preflop_voluntary_opportunities
        assert t2._preflop_open_opportunities == t._preflop_open_opportunities
        assert t2._preflop_voluntary_action_count == t._preflop_voluntary_action_count
        assert t2._preflop_open_raise_count == t._preflop_open_raise_count
        assert t2.pfr_per_open_opportunity == pytest.approx(t.pfr_per_open_opportunity)
        assert t2.vpip_per_voluntary_opportunity == pytest.approx(
            t.vpip_per_voluntary_opportunity,
        )

    def test_from_dict_missing_fields_defaults_to_zero(self):
        """Backwards-compat: old records without the new counters
        should deserialize with sane defaults (0 / neutral prior 0.5)."""
        data = {
            'hands_observed': 50,
            'hands_dealt': 50,
            'vpip': 0.5,
            'pfr': 0.2,
            'aggression_factor': 1.5,
        }
        t = OpponentTendencies.from_dict(data)
        assert t._preflop_voluntary_opportunities == 0
        assert t._preflop_open_opportunities == 0
        assert t._preflop_voluntary_action_count == 0
        assert t._preflop_open_raise_count == 0
        # Derived fields fall back to neutral prior.
        assert t.pfr_per_open_opportunity == 0.5
        assert t.vpip_per_voluntary_opportunity == 0.5


# ── Integration via AIMemoryManager.on_action ───────────────────────────


def _gs(*names):
    players = [
        SimpleNamespace(name=n, stack=10000, is_human=False, hand=None)
        for n in names
    ]
    return SimpleNamespace(players=players, table_positions={})


class TestMemoryManagerIntegration:
    """End-to-end: feed actions through AIMemoryManager.on_action and
    confirm the counters reflect the action sequence correctly.

    The manager computes was_facing_bet itself from cbet_detector's
    preflop_aggressor state, so these tests verify the wiring captures
    facing-bet status BEFORE the cbet_detector updates per-action.
    """

    def test_first_actor_no_prior_raise_is_open(self):
        mm = AIMemoryManager(game_id='t')
        mm.initialize_for_player('Hero')
        mm.initialize_for_player('Villain')
        mm.on_hand_start(_gs('Hero', 'Villain'), hand_number=1)
        # Villain acts first preflop with no prior raise → open opp.
        mm.on_action(
            'Villain', 'raise', amount=300, phase='PRE_FLOP',
            pot_total=300, active_players=['Hero', 'Villain'],
        )
        t = mm.opponent_model_manager.get_model('Hero', 'Villain').tendencies
        assert t._preflop_open_opportunities == 1
        assert t._preflop_open_raise_count == 1
        assert t.pfr_per_open_opportunity == 1.0

    def test_second_actor_facing_raise_not_open(self):
        mm = AIMemoryManager(game_id='t')
        mm.initialize_for_player('Hero')
        mm.initialize_for_player('Villain')
        mm.on_hand_start(_gs('Hero', 'Villain'), hand_number=1)
        # Hero raises first (sets preflop aggressor).
        mm.on_action(
            'Hero', 'raise', amount=300, phase='PRE_FLOP',
            pot_total=300, active_players=['Hero', 'Villain'],
        )
        # Villain now facing a raise — calls.
        mm.on_action(
            'Villain', 'call', amount=300, phase='PRE_FLOP',
            pot_total=600, active_players=['Hero', 'Villain'],
        )
        t = mm.opponent_model_manager.get_model('Hero', 'Villain').tendencies
        # Voluntary opp ticks.
        assert t._preflop_voluntary_opportunities == 1
        assert t._preflop_voluntary_action_count == 1
        # But open opp does NOT (was facing a raise).
        assert t._preflop_open_opportunities == 0
        assert t._preflop_open_raise_count == 0

    def test_villain_3bet_not_an_open_raise(self):
        mm = AIMemoryManager(game_id='t')
        mm.initialize_for_player('Hero')
        mm.initialize_for_player('Villain')
        mm.on_hand_start(_gs('Hero', 'Villain'), hand_number=1)
        # Hero raises, Villain 3-bets.
        mm.on_action(
            'Hero', 'raise', amount=300, phase='PRE_FLOP',
            pot_total=300, active_players=['Hero', 'Villain'],
        )
        mm.on_action(
            'Villain', 'raise', amount=900, phase='PRE_FLOP',
            pot_total=1200, active_players=['Hero', 'Villain'],
        )
        t = mm.opponent_model_manager.get_model('Hero', 'Villain').tendencies
        # Legacy PFR ticks (any preflop raise).
        assert t._pfr_count == 1
        # But opp-normalized open-raise does NOT.
        assert t._preflop_open_raise_count == 0
        assert t._preflop_open_opportunities == 0

    def test_postflop_action_does_not_affect_preflop_counters(self):
        mm = AIMemoryManager(game_id='t')
        mm.initialize_for_player('Hero')
        mm.initialize_for_player('Villain')
        mm.on_hand_start(_gs('Hero', 'Villain'), hand_number=1)
        mm.on_action(
            'Villain', 'bet', amount=300, phase='FLOP',
            pot_total=300, active_players=['Hero', 'Villain'],
        )
        t = mm.opponent_model_manager.get_model('Hero', 'Villain').tendencies
        # Preflop counters stay at zero — the action was postflop.
        assert t._preflop_voluntary_opportunities == 0
        assert t._preflop_open_opportunities == 0
        assert t.pfr_per_open_opportunity == 0.5  # neutral prior
