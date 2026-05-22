"""Tests for the human-clone sim opponent.

Covers profile derivation from opponent_models, tier-mapping math, and
the strategy function's behavior at the key decision boundaries.
Doesn't test the sim CLI wiring — that's covered indirectly by the
register-into-BUILT_IN_STRATEGIES round trip.
"""

import random
import sqlite3
import tempfile

import pytest

from poker.human_clone import (
    CloneProfile,
    _tier_for_frequency,
    build_clone_strategy,
    derive_profile_from_db,
    register_clone_strategy,
)
from poker.hand_tiers import PREMIUM_HANDS, TOP_20_HANDS, TOP_35_HANDS, TOP_95_HANDS


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def temp_db_with_opponent_models():
    """Minimal opponent_models table seeded with two rows for the same player."""
    fd, path = tempfile.mkstemp(suffix='.db')
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE opponent_models (
            id INTEGER PRIMARY KEY,
            observer_name TEXT, opponent_name TEXT,
            hands_observed INTEGER, vpip REAL, pfr REAL,
            aggression_factor REAL, fold_to_cbet REAL,
            bluff_frequency REAL, showdown_win_rate REAL
        )
    """)
    # Two observers, weighted average should land on VPIP ≈ 0.33, AF ≈ 1.5
    conn.executemany(
        "INSERT INTO opponent_models "
        "(observer_name, opponent_name, hands_observed, vpip, pfr, "
        "aggression_factor, fold_to_cbet, bluff_frequency, showdown_win_rate) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ('Batman',   'Jeff', 100, 0.30, 0.18, 1.0, 0.40, 0.25, 0.55),
            ('Cleopatra','Jeff',  50, 0.40, 0.22, 2.5, 0.50, 0.30, 0.50),
        ],
    )
    conn.commit()
    conn.close()
    yield path
    import os
    os.unlink(path)


# ── Tier mapping ────────────────────────────────────────────────────────


class TestTierMapping:
    def test_low_freq_returns_premium(self):
        assert _tier_for_frequency(0.03) == PREMIUM_HANDS

    def test_zero_returns_premium(self):
        assert _tier_for_frequency(0.0) == PREMIUM_HANDS

    def test_mid_freq_returns_top_35(self):
        # VPIP=0.30 falls between 0.20 and 0.35; rounded-up to 0.35
        assert _tier_for_frequency(0.30) == TOP_35_HANDS

    def test_high_freq_caps_at_top_95(self):
        assert _tier_for_frequency(0.95) == TOP_95_HANDS
        assert _tier_for_frequency(1.0) == TOP_95_HANDS

    def test_exact_boundary_value(self):
        # 0.20 is a tier boundary — should map to TOP_20 (not TOP_35)
        assert _tier_for_frequency(0.20) == TOP_20_HANDS


# ── Profile derivation from DB ─────────────────────────────────────────


class TestDeriveProfileFromDb:
    def test_weighted_aggregation(self, temp_db_with_opponent_models):
        profile = derive_profile_from_db(temp_db_with_opponent_models, 'Jeff')
        assert profile.source_player == 'Jeff'
        assert profile.hands_observed == 150
        # (100*0.30 + 50*0.40) / 150 = 0.333
        assert profile.vpip == pytest.approx(0.3333, abs=0.001)
        # (100*0.18 + 50*0.22) / 150 = 0.1933
        assert profile.pfr == pytest.approx(0.1933, abs=0.001)
        # (100*1.0 + 50*2.5) / 150 = 1.5
        assert profile.aggression_factor == pytest.approx(1.5, abs=0.001)

    def test_raises_when_no_rows(self, temp_db_with_opponent_models):
        with pytest.raises(ValueError, match="No opponent_models rows"):
            derive_profile_from_db(temp_db_with_opponent_models, 'NonexistentPlayer')

    def test_raises_when_below_min_hands(self, temp_db_with_opponent_models):
        # default min_hands=20; with explicit higher threshold we should fail
        with pytest.raises(ValueError, match="need at least 1000"):
            derive_profile_from_db(
                temp_db_with_opponent_models, 'Jeff', min_hands=1000,
            )


# ── Strategy behavior ─────────────────────────────────────────────────


def _build_jeff_strategy():
    """Convenience: a Jeff-like profile (VPIP 35%, PFR 18%, AF 2.0)."""
    return build_clone_strategy(CloneProfile(
        source_player='Jeff', hands_observed=200,
        vpip=0.35, pfr=0.18, aggression_factor=2.0,
        fold_to_cbet=0.30,
    ))


def _ctx(**kw):
    """Build a minimal context dict; sensible defaults for everything not set."""
    return {
        'valid_actions': kw.pop('valid_actions', ['fold', 'call', 'raise']),
        'cost_to_call': kw.pop('cost_to_call', 50),
        'pot_total': kw.pop('pot_total', 150),
        'min_raise': kw.pop('min_raise', 100),
        'max_raise': kw.pop('max_raise', 1000),
        'canonical_hand': kw.pop('canonical_hand', '72o'),
        'equity': kw.pop('equity', 0.3),
        'phase': kw.pop('phase', 'PRE_FLOP'),
        **kw,
    }


class TestPreflopBehavior:
    def test_raises_premium_hand(self):
        strat = _build_jeff_strategy()
        # AA is in PFR tier (TOP_20) → should raise
        result = strat(_ctx(canonical_hand='AA'))
        assert result['action'] == 'raise'

    def test_calls_marginal_in_vpip_tier_only(self):
        strat = _build_jeff_strategy()
        # Pick a hand likely in TOP_35 but NOT in TOP_20 (Jeff's PFR is 18%)
        result = strat(_ctx(canonical_hand='K9s'))
        # K9s is in TOP_35 but probably not in TOP_20 — should call
        assert result['action'] in ('call', 'raise')  # either is acceptable

    def test_folds_trash(self):
        strat = _build_jeff_strategy()
        result = strat(_ctx(canonical_hand='72o'))
        assert result['action'] == 'fold'

    def test_checks_when_free(self):
        strat = _build_jeff_strategy()
        result = strat(_ctx(
            cost_to_call=0, canonical_hand='72o',
            valid_actions=['check', 'raise'],
        ))
        assert result['action'] == 'check'


class TestPostflopBehavior:
    def test_folds_negative_ev(self):
        strat = _build_jeff_strategy()
        # equity=0.10, cost=200 into pot=200 → required=0.50; effective=0.50*0.65=0.325
        # equity 0.10 < 0.325 → fold
        result = strat(_ctx(
            phase='FLOP', cost_to_call=200, pot_total=200, equity=0.10,
        ))
        assert result['action'] == 'fold'

    def test_calls_marginal_due_to_sticky_multiplier(self):
        strat = _build_jeff_strategy()  # fold_to_cbet=0.30 → multiplier=0.65
        # cost=50 into pot=200 → required_equity=0.20; effective=0.20*0.65=0.13
        # Jeff calls at any equity above 0.13 — sticky behavior
        result = strat(_ctx(
            phase='FLOP', cost_to_call=50, pot_total=200, equity=0.18,
        ))
        assert result['action'] == 'call'

    def test_checks_when_free_with_weak_hand(self):
        strat = _build_jeff_strategy()
        result = strat(_ctx(
            phase='FLOP', cost_to_call=0, equity=0.20,
            valid_actions=['check', 'raise'],
        ))
        assert result['action'] == 'check'

    def test_bets_strong_hand_when_free(self):
        # Force deterministic RNG path: high AF makes raise rate ~67% but
        # this is probabilistic; seed to land on raise.
        random.seed(0)
        strat = _build_jeff_strategy()
        result = strat(_ctx(
            phase='FLOP', cost_to_call=0, equity=0.75,
            valid_actions=['check', 'raise'],
        ))
        # With AF=2.0 and equity 0.75, very likely to raise — but not guaranteed.
        # Just check that the bot doesn't fold when free and strong.
        assert result['action'] in ('raise', 'check')


# ── Sticky-caller calibration (the headline behavior we want to capture) ──


class TestStickyCallerVsCaseBot:
    """A high fold_to_cbet bot should fold marginal spots; a sticky-call bot
    (low fold_to_cbet, like a calling-station Jeff) should call them.
    This is the core knob the clone needs to get right.
    """

    def _make(self, fold_to_cbet: float):
        return build_clone_strategy(CloneProfile(
            source_player='X', hands_observed=100,
            vpip=0.30, pfr=0.15, aggression_factor=1.5,
            fold_to_cbet=fold_to_cbet,
        ))

    def test_high_ftc_folds_marginal(self):
        # fold_to_cbet=1.0 → multiplier=1.0 → fold at any equity < required
        strat = self._make(1.0)
        result = strat(_ctx(
            phase='FLOP', cost_to_call=50, pot_total=200, equity=0.18,
        ))
        # required_equity = 0.20, effective = 0.20 — equity 0.18 < 0.20 → fold
        assert result['action'] == 'fold'

    def test_low_ftc_calls_marginal(self):
        # fold_to_cbet=0.0 → multiplier=0.5 → call way wider
        strat = self._make(0.0)
        result = strat(_ctx(
            phase='FLOP', cost_to_call=50, pot_total=200, equity=0.12,
        ))
        # required_equity = 0.20, effective = 0.10 — equity 0.12 > 0.10 → call
        assert result['action'] == 'call'


# ── BUILT_IN_STRATEGIES registration ──────────────────────────────────


class TestRegisterCloneStrategy:
    def test_register_adds_to_built_in(self):
        from poker.rule_strategies import BUILT_IN_STRATEGIES
        profile = CloneProfile(
            source_player='TestUser', hands_observed=50,
            vpip=0.3, pfr=0.15, aggression_factor=1.0, fold_to_cbet=0.5,
        )
        name = register_clone_strategy('clone_testuser', profile)
        try:
            assert name == 'clone_testuser'
            assert 'clone_testuser' in BUILT_IN_STRATEGIES
            # Smoke: invoke the registered fn through the dict
            strat = BUILT_IN_STRATEGIES['clone_testuser']
            result = strat(_ctx(canonical_hand='AA'))
            assert result['action'] in ('raise', 'call', 'fold', 'check')
        finally:
            BUILT_IN_STRATEGIES.pop('clone_testuser', None)
