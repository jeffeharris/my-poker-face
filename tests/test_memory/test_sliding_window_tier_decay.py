"""Phase 7.5 Item 2b tests for the sliding-window recent-event log
on OpponentTendencies.

Verifies:
  - _recent_postflop_events accumulates postflop actions
  - Maxlen cap (config window_size) evicts oldest events
  - recent_postflop_stats() builds an AggregatedOpponentStats from the
    window with the same semantics as the cumulative path
  - to_dict / from_dict round-trip the window
  - Missing-field tolerance on legacy records
  - Empty window returns zero-init stats
  - _determine_clamp consumes the window correctly when wired together

See docs/plans/PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md §Item 2b.
"""

from collections import deque

import pytest

from poker.memory.opponent_model import OpponentTendencies
from poker.strategy import phase_7_5_config as cfg
from poker.strategy.exploitation import (
    ClampTier,
    _determine_clamp,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def t() -> OpponentTendencies:
    return OpponentTendencies()


@pytest.fixture(autouse=True)
def reset_config():
    cfg.reset_for_testing()
    yield
    cfg.reset_for_testing()


# ── Window accumulation ──────────────────────────────────────────────────

class TestWindowAccumulation:
    def test_postflop_action_pushes_event(self, t):
        t.update_from_action('bet', 'FLOP', was_facing_bet=False)
        assert len(t._recent_postflop_events) == 1
        assert t._recent_postflop_events[0] == ('bet', False)

    def test_preflop_action_skipped(self, t):
        t.update_from_action('raise', 'PRE_FLOP', was_facing_bet=False)
        assert len(t._recent_postflop_events) == 0

    def test_unknown_context_skipped(self, t):
        """was_facing_bet=None (caller can't determine) doesn't push."""
        t.update_from_action('call', 'FLOP', was_facing_bet=None)
        assert len(t._recent_postflop_events) == 0

    def test_multiple_streets_all_pushed(self, t):
        t.update_from_action('bet', 'FLOP', was_facing_bet=False)
        t.update_from_action('call', 'TURN', was_facing_bet=True)
        t.update_from_action('all_in', 'RIVER', was_facing_bet=True)
        assert len(t._recent_postflop_events) == 3


# ── Maxlen eviction ──────────────────────────────────────────────────────

class TestMaxlenEviction:
    def test_window_caps_at_config_size(self, t):
        """Production config window_size=50 caps the deque."""
        for _ in range(80):
            t.update_from_action('check', 'FLOP', was_facing_bet=False)
        assert len(t._recent_postflop_events) == 50
        # Oldest events should be evicted.

    def test_eviction_preserves_recency(self, t):
        """After eviction, the most recent events are kept."""
        # 50 'check' events then 1 'all_in' — the all_in should be at the end.
        for _ in range(60):
            t.update_from_action('check', 'FLOP', was_facing_bet=False)
        t.update_from_action('all_in', 'TURN', was_facing_bet=True)
        assert t._recent_postflop_events[-1] == ('all_in', True)
        # Total is still capped at 50.
        assert len(t._recent_postflop_events) == 50


# ── recent_postflop_stats() ──────────────────────────────────────────────

class TestRecentPostflopStats:
    def test_empty_window_returns_zero_init(self, t):
        stats = t.recent_postflop_stats()
        assert stats.facing_bet_opportunities == 0
        assert stats.postflop_open_opportunities == 0
        assert stats.aggression_factor_postflop == 1.0
        assert stats.all_in_per_facing_bet == 0.0
        assert stats.postflop_jam_open_rate == 0.0

    def test_window_aggregates_counters_correctly(self, t):
        # 5 events: 3 open opps (1 jam), 2 facing-bet opps (1 jam, 1 call)
        t.update_from_action('check', 'FLOP', was_facing_bet=False)
        t.update_from_action('bet', 'TURN', was_facing_bet=False)
        t.update_from_action('all_in', 'RIVER', was_facing_bet=False)  # open jam
        t.update_from_action('call', 'FLOP', was_facing_bet=True)
        t.update_from_action('all_in', 'TURN', was_facing_bet=True)    # response jam

        stats = t.recent_postflop_stats()
        assert stats.postflop_open_opportunities == 3
        assert stats.postflop_jam_open_rate == pytest.approx(1 / 3)
        assert stats.facing_bet_opportunities == 2
        assert stats.all_in_per_facing_bet == pytest.approx(0.5)

    def test_window_postflop_af(self, t):
        """recent AF = postflop bet/raise/all-in / postflop call within window."""
        for _ in range(6):
            t.update_from_action('bet', 'FLOP', was_facing_bet=False)
        for _ in range(2):
            t.update_from_action('call', 'TURN', was_facing_bet=True)

        stats = t.recent_postflop_stats()
        assert stats.aggression_factor_postflop == pytest.approx(6 / 2)

    def test_window_af_raw_count_cap(self, t):
        """Recent AF capped at MEDIUM threshold when window has zero calls."""
        for _ in range(10):
            t.update_from_action('bet', 'FLOP', was_facing_bet=False)
        stats = t.recent_postflop_stats()
        # Pre-cap: 10. Cap at MEDIUM threshold (4.0).
        assert stats.aggression_factor_postflop == cfg.CONFIG.signal_thresholds.medium_af_postflop


# ── Round-trip persistence ───────────────────────────────────────────────

class TestPersistence:
    def test_round_trip_preserves_window(self, t):
        t.update_from_action('all_in', 'FLOP', was_facing_bet=False)
        t.update_from_action('call', 'TURN', was_facing_bet=True)
        t.update_from_action('fold', 'RIVER', was_facing_bet=True)

        snapshot = t.to_dict()
        restored = OpponentTendencies.from_dict(snapshot)

        assert list(restored._recent_postflop_events) == list(t._recent_postflop_events)
        # And the derived recent stats should match.
        assert restored.recent_postflop_stats().postflop_jam_open_rate == (
            t.recent_postflop_stats().postflop_jam_open_rate
        )

    def test_legacy_record_without_window_field(self):
        """Old records lack _recent_postflop_events — should default to empty."""
        legacy = {
            'hands_observed': 50,
            '_postflop_bet_raise_count': 10,
            '_postflop_call_count': 5,
            # No _recent_postflop_events
        }
        t = OpponentTendencies.from_dict(legacy)
        assert len(t._recent_postflop_events) == 0
        # And recent_postflop_stats() returns zero-init.
        stats = t.recent_postflop_stats()
        assert stats.facing_bet_opportunities == 0


# ── Wiring with _determine_clamp ─────────────────────────────────────────

class TestDetermineClampWithRealWindow:
    def test_recent_window_ratchets_down_when_signal_cools(self, t):
        """Opponent jammed early (cumulative EXTREME), then settled
        down (recent DEFAULT) → tier decays to DEFAULT."""
        # Cumulative: 150 facing-bet opportunities, many jams
        for _ in range(50):
            t.update_from_action('all_in', 'FLOP', was_facing_bet=True)
        for _ in range(100):
            t.update_from_action('all_in', 'FLOP', was_facing_bet=False)
        # Cumulative AggregatedOpponentStats build from t's fields.
        cumulative = _cumulative_stats_from(t)

        # Now opponent plays normally for 50 events (the window size).
        # The window evicts all the jam events.
        for _ in range(50):
            t.update_from_action('check', 'FLOP', was_facing_bet=False)

        recent = t.recent_postflop_stats()
        # Recent window should now show no aggression signal.
        assert recent.postflop_jam_open_rate < 0.01

        clamp, tier, axis = _determine_clamp(cumulative, recent_stats=recent)
        # Cumulative is EXTREME, recent is DEFAULT → caps to DEFAULT.
        assert tier == ClampTier.DEFAULT

    def test_recent_window_agrees_with_cumulative(self, t):
        """Stable opponent — recent matches cumulative → no decay."""
        for _ in range(150):
            t.update_from_action('all_in', 'FLOP', was_facing_bet=True)
        cumulative = _cumulative_stats_from(t)
        recent = t.recent_postflop_stats()
        # Recent should also be EXTREME (the last 50 events are all jams).
        clamp, tier, axis = _determine_clamp(cumulative, recent_stats=recent)
        assert tier == ClampTier.EXTREME


# ── Helper ───────────────────────────────────────────────────────────────

def _cumulative_stats_from(t: OpponentTendencies):
    """Build cumulative AggregatedOpponentStats from t's cumulative counters."""
    from poker.strategy.exploitation import AggregatedOpponentStats
    return AggregatedOpponentStats(
        hands_observed=t.hands_observed,
        aggression_factor_postflop=t.aggression_factor_postflop,
        all_in_per_facing_bet=t.all_in_per_facing_bet,
        facing_bet_opportunities=t._facing_bet_opportunities,
        postflop_jam_open_rate=t.postflop_jam_open_rate,
        postflop_open_opportunities=t._postflop_open_opportunities,
    )
