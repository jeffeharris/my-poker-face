"""Tests for poker.memory.cbet_detector.CbetDetector."""

import pytest

from poker.memory.cbet_detector import CbetDetector


# ── State machine basics ────────────────────────────────────────────────

class TestPreflopAggressorTracking:
    def test_initial_state_none(self):
        det = CbetDetector()
        assert det.preflop_aggressor is None
        assert det.cbet_made is False

    def test_preflop_raise_sets_aggressor(self):
        det = CbetDetector()
        det.record_action('Hero', 'raise', 'PRE_FLOP')
        assert det.preflop_aggressor == 'Hero'

    def test_preflop_all_in_sets_aggressor(self):
        det = CbetDetector()
        det.record_action('Maniac', 'all_in', 'PRE_FLOP')
        assert det.preflop_aggressor == 'Maniac'

    def test_preflop_call_does_not_set(self):
        det = CbetDetector()
        det.record_action('Hero', 'raise', 'PRE_FLOP')
        det.record_action('Villain', 'call', 'PRE_FLOP')
        assert det.preflop_aggressor == 'Hero'

    def test_three_bet_transfers_aggressor(self):
        det = CbetDetector()
        det.record_action('Hero', 'raise', 'PRE_FLOP')
        det.record_action('Villain', 'raise', 'PRE_FLOP')
        assert det.preflop_aggressor == 'Villain'

    def test_postflop_action_does_not_change_preflop_aggressor(self):
        det = CbetDetector()
        det.record_action('Hero', 'raise', 'PRE_FLOP')
        det.record_action('Hero', 'bet', 'FLOP', active_players=['Hero', 'Villain'])
        det.record_action('Villain', 'raise', 'FLOP')
        assert det.preflop_aggressor == 'Hero'

    def test_reset_clears_state(self):
        det = CbetDetector()
        det.record_action('Hero', 'raise', 'PRE_FLOP')
        det.record_action('Hero', 'bet', 'FLOP', active_players=['Hero', 'Villain'])
        assert det.cbet_made is True
        det.reset_for_new_hand()
        assert det.preflop_aggressor is None
        assert det.cbet_made is False

    def test_manual_record_preflop_aggression(self):
        det = CbetDetector()
        det.record_preflop_aggression('Hero')
        assert det.preflop_aggressor == 'Hero'


# ── C-bet detection + responses ─────────────────────────────────────────

class TestCbetDetectionAndResponses:
    def _hero_raises_preflop(self) -> CbetDetector:
        det = CbetDetector()
        det.record_action('Hero', 'raise', 'PRE_FLOP')
        return det

    def test_no_cbet_if_non_aggressor_bets_first(self):
        det = self._hero_raises_preflop()
        # Villain (not the preflop raiser) leads the flop
        responses = det.record_action(
            'Villain', 'bet', 'FLOP',
            active_players=['Hero', 'Villain'],
        )
        assert responses == []
        assert det.cbet_made is False

    def test_cbet_fires_on_preflop_raisers_flop_bet(self):
        det = self._hero_raises_preflop()
        det.record_action(
            'Hero', 'bet', 'FLOP',
            active_players=['Hero', 'Villain'],
        )
        assert det.cbet_made is True

    def test_cbet_fires_on_all_in_shove(self):
        """T1-30 regression: PFR shoving the flop counts as a c-bet so
        opponent fold-to-cbet stats stay accurate at low SPR."""
        det = self._hero_raises_preflop()
        det.record_action('Villain', 'call', 'PRE_FLOP')
        det.record_action(
            'Hero', 'all_in', 'FLOP',
            active_players=['Hero', 'Villain'],
        )
        assert det.cbet_made is True
        responses = det.record_action('Villain', 'fold', 'FLOP')
        assert responses == [('Villain', True)]

    def test_cbet_also_fires_on_raise(self):
        det = self._hero_raises_preflop()
        # Donker leads first, then preflop raiser raises — that's a c-bet
        # only if we consider raises by the PFR as continuation. Detector
        # treats first-qualifying flop bet/raise from the PFR as c-bet.
        det.record_action('Villain', 'bet', 'FLOP',
                          active_players=['Hero', 'Villain'])
        assert det.cbet_made is False
        det.record_action('Hero', 'raise', 'FLOP',
                          active_players=['Hero', 'Villain'])
        assert det.cbet_made is True

    def test_only_first_qualifying_flop_action_counts(self):
        """After cbet_made flips, subsequent bets/raises by the PFR
        don't re-fire detection (no double-counting)."""
        det = self._hero_raises_preflop()
        det.record_action('Hero', 'bet', 'FLOP',
                          active_players=['Hero', 'Villain'])
        # Villain calls; PFR bets the turn — not a "c-bet" event again
        responses_call = det.record_action('Villain', 'call', 'FLOP')
        assert ('Villain', False) in responses_call
        # Hero leads turn
        responses_turn = det.record_action('Hero', 'bet', 'TURN')
        # The facing set was drained when Villain called the flop
        assert responses_turn == []

    def test_fold_to_cbet_response_recorded(self):
        det = self._hero_raises_preflop()
        det.record_action('Hero', 'bet', 'FLOP',
                          active_players=['Hero', 'Villain'])
        responses = det.record_action('Villain', 'fold', 'FLOP')
        assert responses == [('Villain', True)]

    def test_call_to_cbet_response_recorded(self):
        det = self._hero_raises_preflop()
        det.record_action('Hero', 'bet', 'FLOP',
                          active_players=['Hero', 'Villain'])
        responses = det.record_action('Villain', 'call', 'FLOP')
        assert responses == [('Villain', False)]

    def test_raise_to_cbet_response_recorded_as_not_folded(self):
        det = self._hero_raises_preflop()
        det.record_action('Hero', 'bet', 'FLOP',
                          active_players=['Hero', 'Villain'])
        responses = det.record_action('Villain', 'raise', 'FLOP')
        assert responses == [('Villain', False)]

    def test_multiway_facing_set_includes_all_non_bettors(self):
        det = self._hero_raises_preflop()
        det.record_action(
            'Hero', 'bet', 'FLOP',
            active_players=['Hero', 'A', 'B', 'C'],
        )
        # Three opponents responding individually
        r1 = det.record_action('A', 'fold', 'FLOP')
        r2 = det.record_action('B', 'call', 'FLOP')
        r3 = det.record_action('C', 'fold', 'FLOP')
        assert r1 == [('A', True)]
        assert r2 == [('B', False)]
        assert r3 == [('C', True)]

    def test_response_drops_player_from_facing_set(self):
        """Once a player has responded, a later action by them does not
        re-fire a response (set is drained)."""
        det = self._hero_raises_preflop()
        det.record_action('Hero', 'bet', 'FLOP',
                          active_players=['Hero', 'Villain'])
        det.record_action('Villain', 'call', 'FLOP')
        # Now both check the turn — no c-bet response should fire
        responses = det.record_action('Villain', 'check', 'TURN')
        assert responses == []

    def test_no_active_players_means_no_facing_set(self):
        """Without active_players, the detector can't build a facing
        set, so even though cbet_made flips, no responses are emitted."""
        det = self._hero_raises_preflop()
        det.record_action('Hero', 'bet', 'FLOP', active_players=None)
        assert det.cbet_made is True
        responses = det.record_action('Villain', 'fold', 'FLOP')
        assert responses == []

    def test_pfr_not_in_facing_set(self):
        """The preflop raiser doesn't face their own c-bet."""
        det = self._hero_raises_preflop()
        det.record_action(
            'Hero', 'bet', 'FLOP',
            active_players=['Hero', 'Villain'],
        )
        # If hero somehow takes another action while in the facing set
        # (they're not), they don't trigger a response
        responses = det.record_action('Hero', 'check', 'TURN')
        assert responses == []

    def test_reset_clears_facing_set(self):
        det = self._hero_raises_preflop()
        det.record_action('Hero', 'bet', 'FLOP',
                          active_players=['Hero', 'Villain'])
        det.reset_for_new_hand()
        # New hand: no facing set, no response even if cbet_made would have
        responses = det.record_action('Villain', 'fold', 'FLOP')
        assert responses == []
