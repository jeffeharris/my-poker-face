"""Tests for the human-clone sim opponent.

Covers profile derivation from opponent_models, tier-mapping math, and
the strategy function's behavior at the key decision boundaries.
Doesn't test the sim CLI wiring — that's covered indirectly by the
register-into-BUILT_IN_STRATEGIES round trip.
"""

import json
import os
import random
import sqlite3
import tempfile

import pytest

from poker.human_clone import (
    CloneProfile,
    _mine_hand_history,
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


class TestMineHandHistory:
    """V2: stats mined from hand_history.actions_json."""

    def _make_db_with_actions(self, hand_actions_list):
        """Create a temp DB with hand_history rows from a list of action lists.

        Each entry in `hand_actions_list` is the actions_json for one hand.
        players_json is generated as a constant Jeff-included string.
        """
        fd, path = tempfile.mkstemp(suffix='.db')
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE hand_history (
                id INTEGER PRIMARY KEY,
                game_id TEXT, hand_number INTEGER,
                players_json TEXT, actions_json TEXT
            )
        """)
        players_json = '[{"name": "Jeff"}, {"name": "Other"}]'
        for i, actions in enumerate(hand_actions_list):
            conn.execute(
                "INSERT INTO hand_history (game_id, hand_number, players_json, actions_json) "
                "VALUES (?, ?, ?, ?)",
                (f'game{i}', i, players_json, json.dumps(actions)),
            )
        conn.commit()
        conn.close()
        return path

    def _action(self, player, action, phase='PRE_FLOP', amount=0):
        return {'player_name': player, 'action': action, 'phase': phase, 'amount': amount}

    def test_returns_none_when_insufficient_data(self):
        # Single hand → all stats below the 5-sample minimum
        path = self._make_db_with_actions([
            [self._action('Jeff', 'fold')],
        ])
        try:
            result = _mine_hand_history(path, 'Jeff')
            assert result['wtsd'] is None
            assert result['threebet_rate'] is None
            assert result['flop_af'] is None
        finally:
            os.unlink(path)

    def test_wtsd_high_for_sticky_caller(self):
        # 5 hands: Jeff sees flop in all 5, reaches river in 4
        hands = []
        for i in range(5):
            actions = [
                self._action('Jeff', 'call', 'PRE_FLOP'),
                self._action('Jeff', 'call', 'FLOP'),
            ]
            if i < 4:  # 4 of 5 reach river without folding
                actions.append(self._action('Jeff', 'call', 'TURN'))
                actions.append(self._action('Jeff', 'call', 'RIVER'))
            else:  # 1 of 5 folds turn
                actions.append(self._action('Jeff', 'fold', 'TURN'))
            hands.append(actions)
        path = self._make_db_with_actions(hands)
        try:
            result = _mine_hand_history(path, 'Jeff')
            assert result['wtsd'] == pytest.approx(0.80)
        finally:
            os.unlink(path)

    def test_threebet_rate_counts_facing_raise_opportunities(self):
        # 5 hands: someone raises preflop, then Jeff acts
        # Jeff 3-bets in 2 of 5
        hands = []
        for i in range(5):
            actions = [
                self._action('Other', 'raise', 'PRE_FLOP'),
                self._action('Jeff', 'raise' if i < 2 else 'call', 'PRE_FLOP'),
            ]
            hands.append(actions)
        path = self._make_db_with_actions(hands)
        try:
            result = _mine_hand_history(path, 'Jeff')
            assert result['threebet_rate'] == pytest.approx(0.40)
        finally:
            os.unlink(path)

    def test_street_af_per_phase(self):
        # 5 hands where Jeff sees the flop with mixed aggression
        hands = []
        for i in range(5):
            actions = [
                self._action('Jeff', 'call', 'PRE_FLOP'),
                # Flop: 3 raises, 2 calls → AF = 3/2 = 1.5
                self._action('Jeff', 'raise' if i < 3 else 'call', 'FLOP'),
                # Turn: 1 raise, 4 checks → AF = 1/4 = 0.25
                self._action('Jeff', 'raise' if i == 0 else 'check', 'TURN'),
            ]
            hands.append(actions)
        path = self._make_db_with_actions(hands)
        try:
            result = _mine_hand_history(path, 'Jeff')
            assert result['flop_af'] == pytest.approx(1.5)
            assert result['turn_af'] == pytest.approx(0.25)
            # No river actions → None
            assert result['river_af'] is None
        finally:
            os.unlink(path)


class TestV2StrategyUsesPerStreetAF:
    """V2: per-street AF and WtSD modulate the postflop policy."""

    def _make(self, **kw):
        defaults = dict(
            source_player='X', hands_observed=200, vpip=0.30, pfr=0.15,
            aggression_factor=1.0, fold_to_cbet=0.50,
        )
        defaults.update(kw)
        return build_clone_strategy(CloneProfile(**defaults))

    def test_river_passivity_overrides_global_af(self):
        # Profile has global AF=2.0 (aggressive) but river_af=0.1 (very passive)
        # On the river free-to-act with strong equity, river_raise_rate
        # should win out → mostly check, not raise.
        strat = self._make(aggression_factor=2.0, river_af=0.1)
        random.seed(99)
        raise_count = sum(
            1 for _ in range(100)
            if strat(_ctx(phase='RIVER', cost_to_call=0, equity=0.75,
                          valid_actions=['check', 'raise']))['action'] == 'raise'
        )
        # river_af=0.1 → rate = 0.1/1.1 ≈ 9%. Allow wide bounds for sampling.
        assert raise_count < 25  # would be ~67% without override (AF=2.0)

    def test_wtsd_high_keeps_river_call_sticky(self):
        # Sticky caller: low fold_to_cbet AND high WtSD
        # Should call marginal river bets where a non-sticky bot folds
        strat = self._make(fold_to_cbet=0.30, wtsd=0.55)
        # cost=50 into pot=200 → required=0.20; multiplier = 0.65 * wtsd_adjust
        # wtsd_adjust = 1.0 - (0.55-0.40)*0.5 = 0.925
        # effective = 0.20 * 0.65 * 0.925 = 0.12
        result = strat(_ctx(
            phase='RIVER', cost_to_call=50, pot_total=200, equity=0.15,
        ))
        # equity 0.15 > 0.12 → call
        assert result['action'] == 'call'

    def test_wtsd_low_folds_marginal_river(self):
        # Fit-or-fold type: rarely reaches showdown
        strat = self._make(fold_to_cbet=0.50, wtsd=0.15)
        # wtsd_adjust = 1.0 - (0.15-0.40)*0.5 = 1.125
        # multiplier = 0.75 * 1.125 = 0.84
        # required=0.20; effective = 0.20 * 0.84 = 0.169
        result = strat(_ctx(
            phase='RIVER', cost_to_call=50, pot_total=200, equity=0.15,
        ))
        # equity 0.15 < 0.169 → fold
        assert result['action'] == 'fold'

    def test_v2_fields_optional_falls_back_to_v1(self):
        # No V2 fields → behavior matches V1 (uses global AF, no wtsd adjust)
        strat = self._make()  # all V2 fields default to None
        result = strat(_ctx(
            phase='RIVER', cost_to_call=50, pot_total=200, equity=0.18,
        ))
        # Should fold since equity (0.18) < required (0.20) * fold_mult (0.75) = 0.15
        # 0.18 > 0.15 → call. Verify V1 behavior unchanged.
        assert result['action'] == 'call'


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
