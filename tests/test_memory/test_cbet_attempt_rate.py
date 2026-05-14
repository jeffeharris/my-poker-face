"""Tests for the Phase 8.1a c-bet attempt tracker.

Behavior under test:
  CbetDetector:
    - Emits one (PFR, attempted=True) event when the PFR voluntarily
      bets/raises/all-ins on the flop without a prior bet.
    - Emits one (PFR, attempted=False) event when the PFR checks the
      flop without a prior bet.
    - Does NOT emit when someone donk-bets into the PFR (no clean
      c-bet opportunity).
    - Does NOT emit when the PFR isn't active on the flop (no
      preflop raiser recorded).
    - Emits at most one event per hand (reset_for_new_hand clears it).
    - consume_pfr_attempt_events drains and clears the queue.

  OpponentTendencies:
    - update_cbet_attempt increments denominator on every call,
      numerator only when attempted=True.
    - cbet_attempt_rate is denominator-aware: stays at 0.5 neutral
      until first observation, then = numerator/denominator.
    - to_dict / from_dict roundtrip preserves counters and rate.

  MemoryManager integration:
    - Hand played through observe_action wiring updates the PFR's
      cbet_attempt_rate on all observer-side models.
"""

import pytest

from poker.memory.cbet_detector import CbetDetector
from poker.memory.opponent_model import (
    OpponentModel,
    OpponentModelManager,
    OpponentTendencies,
)


# ── CbetDetector ──────────────────────────────────────────────────────────

class TestPfrAttemptEvents:
    def test_no_event_before_flop(self):
        d = CbetDetector()
        d.record_action('Hero', 'raise', 'PRE_FLOP')
        d.record_action('Villain', 'call', 'PRE_FLOP')
        assert d.consume_pfr_attempt_events() == []

    def test_pfr_bets_flop_emits_attempted_true(self):
        d = CbetDetector()
        d.record_action('Hero', 'raise', 'PRE_FLOP')
        d.record_action('Villain', 'call', 'PRE_FLOP')
        d.record_action('Hero', 'bet', 'FLOP',
                         active_players=['Hero', 'Villain'])
        assert d.consume_pfr_attempt_events() == [('Hero', True)]

    def test_pfr_checks_flop_emits_attempted_false(self):
        d = CbetDetector()
        d.record_action('Hero', 'raise', 'PRE_FLOP')
        d.record_action('Villain', 'call', 'PRE_FLOP')
        d.record_action('Hero', 'check', 'FLOP',
                         active_players=['Hero', 'Villain'])
        assert d.consume_pfr_attempt_events() == [('Hero', False)]

    def test_pfr_all_in_on_flop_counts_as_attempted(self):
        d = CbetDetector()
        d.record_action('Hero', 'raise', 'PRE_FLOP')
        d.record_action('Villain', 'call', 'PRE_FLOP')
        d.record_action('Hero', 'all_in', 'FLOP',
                         active_players=['Hero', 'Villain'])
        assert d.consume_pfr_attempt_events() == [('Hero', True)]

    def test_donk_bet_into_pfr_does_not_emit(self):
        # Villain (caller IP/OOP doesn't matter) donk-bets first on the
        # flop, then PFR responds. PFR didn't have a clean c-bet
        # opportunity — no attempt event should fire.
        d = CbetDetector()
        d.record_action('Hero', 'raise', 'PRE_FLOP')
        d.record_action('Villain', 'call', 'PRE_FLOP')
        d.record_action('Villain', 'bet', 'FLOP',
                         active_players=['Hero', 'Villain'])
        d.record_action('Hero', 'call', 'FLOP')
        assert d.consume_pfr_attempt_events() == []

    def test_oop_pfr_checks_then_calls_donk_emits_check_only(self):
        # PFR is OOP and acts first with a check, then opponent bets,
        # then PFR calls. Only the initial check should emit
        # (attempted=False) — the later call is a response, not a
        # c-bet attempt.
        d = CbetDetector()
        d.record_action('Hero', 'raise', 'PRE_FLOP')
        d.record_action('Villain', 'call', 'PRE_FLOP')
        d.record_action('Hero', 'check', 'FLOP',
                         active_players=['Hero', 'Villain'])
        d.record_action('Villain', 'bet', 'FLOP')
        d.record_action('Hero', 'call', 'FLOP')
        assert d.consume_pfr_attempt_events() == [('Hero', False)]

    def test_only_first_flop_action_counts(self):
        # PFR bets → c-bet attempted=True. If the action somehow
        # comes back to PFR (re-raise war), don't emit a second event.
        d = CbetDetector()
        d.record_action('Hero', 'raise', 'PRE_FLOP')
        d.record_action('Villain', 'call', 'PRE_FLOP')
        d.record_action('Hero', 'bet', 'FLOP',
                         active_players=['Hero', 'Villain'])
        # Consume the first event
        assert d.consume_pfr_attempt_events() == [('Hero', True)]
        # Villain check-raises, PFR re-raises — no second event.
        d.record_action('Villain', 'raise', 'FLOP')
        d.record_action('Hero', 'raise', 'FLOP')
        assert d.consume_pfr_attempt_events() == []

    def test_no_preflop_raiser_no_event(self):
        # Limped flop — no PFR.
        d = CbetDetector()
        d.record_action('Hero', 'call', 'PRE_FLOP')
        d.record_action('Villain', 'check', 'PRE_FLOP')
        d.record_action('Hero', 'bet', 'FLOP',
                         active_players=['Hero', 'Villain'])
        assert d.consume_pfr_attempt_events() == []

    def test_reset_for_new_hand_clears_state(self):
        d = CbetDetector()
        d.record_action('Hero', 'raise', 'PRE_FLOP')
        d.record_action('Villain', 'call', 'PRE_FLOP')
        d.record_action('Hero', 'bet', 'FLOP',
                         active_players=['Hero', 'Villain'])
        d.consume_pfr_attempt_events()  # drain

        d.reset_for_new_hand()
        # New hand, same setup → event should fire fresh.
        d.record_action('Hero', 'raise', 'PRE_FLOP')
        d.record_action('Villain', 'call', 'PRE_FLOP')
        d.record_action('Hero', 'check', 'FLOP',
                         active_players=['Hero', 'Villain'])
        assert d.consume_pfr_attempt_events() == [('Hero', False)]


# ── OpponentTendencies counters ───────────────────────────────────────────

class TestCbetAttemptCounters:
    def test_default_is_neutral_prior(self):
        t = OpponentTendencies()
        assert t.cbet_attempt_rate == 0.5
        assert t._cbet_attempt_count == 0
        assert t._postflop_seen_as_pfr_count == 0

    def test_single_attempt_yields_full_rate(self):
        t = OpponentTendencies()
        t.update_cbet_attempt(True)
        assert t._cbet_attempt_count == 1
        assert t._postflop_seen_as_pfr_count == 1
        assert t.cbet_attempt_rate == 1.0

    def test_single_decline_yields_zero_rate(self):
        t = OpponentTendencies()
        t.update_cbet_attempt(False)
        assert t._cbet_attempt_count == 0
        assert t._postflop_seen_as_pfr_count == 1
        assert t.cbet_attempt_rate == 0.0

    def test_mixed_history_yields_fractional_rate(self):
        t = OpponentTendencies()
        for attempted in (True, True, False, True, False):
            t.update_cbet_attempt(attempted)
        assert t._cbet_attempt_count == 3
        assert t._postflop_seen_as_pfr_count == 5
        assert t.cbet_attempt_rate == pytest.approx(0.6)

    def test_serialization_roundtrip(self):
        t = OpponentTendencies()
        t.update_cbet_attempt(True)
        t.update_cbet_attempt(True)
        t.update_cbet_attempt(False)

        data = t.to_dict()
        assert data['_cbet_attempt_count'] == 2
        assert data['_postflop_seen_as_pfr_count'] == 3
        assert data['cbet_attempt_rate'] == pytest.approx(2.0 / 3.0)

        restored = OpponentTendencies.from_dict(data)
        assert restored._cbet_attempt_count == 2
        assert restored._postflop_seen_as_pfr_count == 3
        assert restored.cbet_attempt_rate == pytest.approx(2.0 / 3.0)

    def test_from_dict_missing_phase_8_1_fields(self):
        # Older serialized records won't have the Phase 8.1a counters.
        # They should default to 0 / neutral 0.5 without raising.
        data = {
            'hands_observed': 10,
            'vpip': 0.3,
            # No _cbet_attempt_count, _postflop_seen_as_pfr_count, or
            # cbet_attempt_rate fields.
        }
        restored = OpponentTendencies.from_dict(data)
        assert restored._cbet_attempt_count == 0
        assert restored._postflop_seen_as_pfr_count == 0
        assert restored.cbet_attempt_rate == 0.5


# ── OpponentModel wrapper ────────────────────────────────────────────────

class TestOpponentModelObserveCbetAttempt:
    def test_wrapper_delegates_to_tendencies(self):
        m = OpponentModel(observer='Hero', opponent='Villain')
        m.observe_cbet_attempt(True)
        m.observe_cbet_attempt(False)
        assert m.tendencies._cbet_attempt_count == 1
        assert m.tendencies._postflop_seen_as_pfr_count == 2
        assert m.tendencies.cbet_attempt_rate == 0.5


# ── MemoryManager integration ────────────────────────────────────────────

class TestMemoryManagerIntegration:
    """End-to-end: when a player raises preflop and acts on the flop,
    every other observer's model of that player should reflect the
    cbet_attempt event."""

    def test_pfr_cbet_attempt_propagates_to_observers(self):
        from poker.memory.memory_manager import AIMemoryManager

        mgr = AIMemoryManager(game_id='test-cbet-attempt')
        # initialize_players takes a list — every player observes every other
        for name in ('Hero', 'Villain', 'Bystander'):
            mgr.initialize_for_player(name)

        # PFR raises, callers come along, PFR c-bets flop
        actives = ['Hero', 'Villain', 'Bystander']
        mgr.on_action('Hero', 'raise', 100, 'PRE_FLOP', 150,
                       active_players=actives)
        mgr.on_action('Villain', 'call', 100, 'PRE_FLOP', 250,
                       active_players=actives)
        mgr.on_action('Bystander', 'call', 100, 'PRE_FLOP', 350,
                       active_players=actives)
        mgr.on_action('Hero', 'bet', 200, 'FLOP', 550,
                       active_players=actives)

        # Every non-Hero observer's model of Hero should record the attempt
        for observer in ('Villain', 'Bystander'):
            model = mgr.opponent_model_manager.get_model(observer, 'Hero')
            assert model.tendencies._cbet_attempt_count == 1
            assert model.tendencies._postflop_seen_as_pfr_count == 1
            assert model.tendencies.cbet_attempt_rate == 1.0

    def test_pfr_check_on_flop_records_decline(self):
        from poker.memory.memory_manager import AIMemoryManager

        mgr = AIMemoryManager(game_id='test-cbet-attempt')
        for name in ('Hero', 'Villain'):
            mgr.initialize_for_player(name)

        actives = ['Hero', 'Villain']
        mgr.on_action('Hero', 'raise', 100, 'PRE_FLOP', 150, active_players=actives)
        mgr.on_action('Villain', 'call', 100, 'PRE_FLOP', 250, active_players=actives)
        mgr.on_action('Hero', 'check', 0, 'FLOP', 250, active_players=actives)

        model = mgr.opponent_model_manager.get_model('Villain', 'Hero')
        assert model.tendencies._cbet_attempt_count == 0
        assert model.tendencies._postflop_seen_as_pfr_count == 1
        assert model.tendencies.cbet_attempt_rate == 0.0
