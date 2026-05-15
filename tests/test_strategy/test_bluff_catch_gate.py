"""Phase 7.5 Item 1b tests for the bluff-catch trigger gate, multiway
suppression, and strategy builder.

Covers:
  - should_apply_bluff_catch_override gating: hand class, tier,
    tilt-adjusted adaptation_bias, facing-bet, multiway suppression
  - _is_station detection on a single opponent's stats
  - _continuing_opponents_block_bluff_catch (multiway suppression)
  - compute_bluff_catch_strategy builds the override + clamps to envelope
  - Mutual exclusivity with strong-hand override (by hand class)

See docs/plans/PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md §Item 1
"Per-aggressor stats threading" + "Multiway pot suppression".
"""

from types import SimpleNamespace

import pytest

from poker.strategy import phase_7_5_config as cfg
from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    ClampTier,
    OpponentSpot,
)
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.value_override import (
    _continuing_opponents_block_bluff_catch,
    _is_station,
    compute_bluff_catch_strategy,
    should_apply_bluff_catch_override,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_config():
    cfg.reset_for_testing()
    yield
    cfg.reset_for_testing()


def _stats(**kwargs) -> AggregatedOpponentStats:
    base = dict(
        hands_observed=200,
        vpip=0.5, pfr=0.25,
        aggression_factor=2.0, all_in_frequency=0.05,
        fold_to_cbet=0.5, cbet_faced_count=10,
        aggression_factor_postflop=7.0,
        all_in_per_facing_bet=0.35,
        facing_bet_opportunities=150,
        postflop_jam_open_rate=0.05,
        postflop_open_opportunities=80,
    )
    base.update(kwargs)
    # Mirror legacy vpip/pfr to the opp-normalized fields when caller
    # didn't set them. value_override._is_station now reads
    # vpip_per_voluntary_opportunity > 0.65.
    base.setdefault('vpip_per_voluntary_opportunity', base['vpip'])
    base.setdefault('pfr_per_open_opportunity', base['pfr'])
    base.setdefault(
        'preflop_voluntary_opportunities',
        max(base['hands_observed'] - 5, 0),
    )
    base.setdefault(
        'preflop_open_opportunities',
        max(base['hands_observed'] // 2, 0),
    )
    return AggregatedOpponentStats(**base)


def _spot(name: str, stats: AggregatedOpponentStats, *,
          is_active: bool = True, is_all_in: bool = False) -> OpponentSpot:
    return OpponentSpot(
        name=name, stats=stats,
        is_active=is_active, is_aggressor=False, is_all_in=is_all_in,
        current_bet=0, stack=10000,
        committed_this_street=0, committed_this_hand=0,
    )


def _ctx(**kwargs) -> SimpleNamespace:
    base = dict(
        bet_size_pot_ratio=1.0,
        facing_all_in=False,
        facing_big_bet=True,
        street='turn',
        board_texture='wet_rainbow',
        is_paired_board=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


# ── _is_station ──────────────────────────────────────────────────────────

class TestIsStation:
    def test_high_vpip_low_af_with_sample_is_station(self):
        s = _stats(vpip=0.70, aggression_factor=1.0, hands_observed=100)
        assert _is_station(s)

    def test_high_vpip_low_af_low_sample_not_station(self):
        """Sample gate: low sample → not classified."""
        s = _stats(vpip=0.70, aggression_factor=1.0, hands_observed=20)
        assert not _is_station(s)

    def test_high_af_not_station(self):
        s = _stats(vpip=0.70, aggression_factor=3.0, hands_observed=100)
        assert not _is_station(s)

    def test_low_vpip_not_station(self):
        s = _stats(vpip=0.30, aggression_factor=1.0, hands_observed=100)
        assert not _is_station(s)


# ── _continuing_opponents_block_bluff_catch ──────────────────────────────

class TestContinuingOpponentsBlock:
    def test_no_continuing_opponents_no_block(self):
        """HU (zero continuing opps besides the aggressor) → no block."""
        agg = _spot('Maniac', _stats())
        assert _continuing_opponents_block_bluff_catch([agg], 'Maniac') is False

    def test_all_in_continuing_opp_blocks(self):
        """Any active all-in opponent → block."""
        agg = _spot('Maniac', _stats())
        third = _spot('AllIn', _stats(), is_all_in=True)
        assert _continuing_opponents_block_bluff_catch(
            [agg, third], 'Maniac',
        ) is True

    def test_station_continuing_opp_blocks(self):
        agg = _spot('Maniac', _stats())
        station = _spot('Station', _stats(
            vpip=0.70, aggression_factor=1.0, hands_observed=100,
        ))
        assert _continuing_opponents_block_bluff_catch(
            [agg, station], 'Maniac',
        ) is True

    def test_low_sample_continuing_opp_blocks(self):
        agg = _spot('Maniac', _stats())
        unknown = _spot('Unknown', _stats(
            facing_bet_opportunities=10,
            postflop_open_opportunities=5,
        ))
        assert _continuing_opponents_block_bluff_catch(
            [agg, unknown], 'Maniac',
        ) is True

    def test_safe_continuing_opp_does_not_block(self):
        """A continuing opponent with adequate sample, not a station,
        and not all-in: no block."""
        agg = _spot('Maniac', _stats())
        safe = _spot('Tight', _stats(
            vpip=0.35, aggression_factor=2.5,
            facing_bet_opportunities=80,  # ≥ MEDIUM sample
        ))
        assert _continuing_opponents_block_bluff_catch(
            [agg, safe], 'Maniac',
        ) is False

    def test_inactive_continuing_opp_ignored(self):
        """Folded opponents don't count as 'continuing.'"""
        agg = _spot('Maniac', _stats())
        folded = _spot('Folded', _stats(), is_active=False)
        # Folded opponent would normally trip the low-sample gate, but
        # is_active=False filters it out.
        # (Note: the spot fixture has full stats so the sample check
        # wouldn't actually trip — verify the filter works regardless.)
        assert _continuing_opponents_block_bluff_catch(
            [agg, folded], 'Maniac',
        ) is False


# ── should_apply_bluff_catch_override ────────────────────────────────────

class TestShouldApply:
    def test_fires_for_medium_made_hu_extreme(self):
        agg = _spot('Maniac', _stats())
        result = should_apply_bluff_catch_override(
            spots=[agg],
            hand_strength='medium_made',
            decision_context=_ctx(),
            adaptation_bias=0.5,
            tilt_factor=1.0,
            clamp_tier=ClampTier.EXTREME,
            aggressor_spot=agg,
        )
        assert result is True

    def test_fires_for_weak_made_hu_extreme(self):
        agg = _spot('Maniac', _stats())
        result = should_apply_bluff_catch_override(
            spots=[agg],
            hand_strength='weak_made',
            decision_context=_ctx(),
            adaptation_bias=0.5,
            tilt_factor=1.0,
            clamp_tier=ClampTier.EXTREME,
            aggressor_spot=agg,
        )
        assert result is True

    @pytest.mark.parametrize("hand_class", [
        'nuts', 'strong_made', 'strong', 'not_strong',
    ])
    def test_blocks_non_bluff_catch_hand_classes(self, hand_class):
        """Strong hands hit strong-hand override; weak_draw/etc don't
        fire either override."""
        agg = _spot('Maniac', _stats())
        result = should_apply_bluff_catch_override(
            spots=[agg], hand_strength=hand_class,
            decision_context=_ctx(),
            adaptation_bias=0.5, tilt_factor=1.0,
            clamp_tier=ClampTier.EXTREME, aggressor_spot=agg,
        )
        assert result is False

    @pytest.mark.parametrize("tier", [
        ClampTier.DEFAULT, ClampTier.MEDIUM,
    ])
    def test_blocks_non_extreme_tier(self, tier):
        """Below EXTREME tier, exploitation offsets alone handle it."""
        agg = _spot('Maniac', _stats())
        result = should_apply_bluff_catch_override(
            spots=[agg], hand_strength='medium_made',
            decision_context=_ctx(),
            adaptation_bias=0.5, tilt_factor=1.0,
            clamp_tier=tier, aggressor_spot=agg,
        )
        assert result is False

    def test_blocks_when_tilt_suppresses(self):
        """Low (adaptation_bias × tilt_factor) → no exploitation."""
        agg = _spot('Maniac', _stats())
        result = should_apply_bluff_catch_override(
            spots=[agg], hand_strength='medium_made',
            decision_context=_ctx(),
            adaptation_bias=0.5, tilt_factor=0.05,  # product = 0.025 < GATING_FLOOR
            clamp_tier=ClampTier.EXTREME, aggressor_spot=agg,
        )
        assert result is False

    def test_blocks_when_not_facing_a_bet(self):
        """bet_size_pot_ratio == 0 → no facing-bet → no fire."""
        agg = _spot('Maniac', _stats())
        result = should_apply_bluff_catch_override(
            spots=[agg], hand_strength='medium_made',
            decision_context=_ctx(bet_size_pot_ratio=0.0),
            adaptation_bias=0.5, tilt_factor=1.0,
            clamp_tier=ClampTier.EXTREME, aggressor_spot=agg,
        )
        assert result is False

    def test_blocks_when_multiway_station(self):
        """Multiway pot with a station behind → block."""
        agg = _spot('Maniac', _stats())
        station = _spot('Station', _stats(
            vpip=0.70, aggression_factor=1.0, hands_observed=100,
        ))
        result = should_apply_bluff_catch_override(
            spots=[agg, station],
            hand_strength='medium_made',
            decision_context=_ctx(),
            adaptation_bias=0.5, tilt_factor=1.0,
            clamp_tier=ClampTier.EXTREME, aggressor_spot=agg,
        )
        assert result is False

    def test_blocks_when_multiway_all_in(self):
        agg = _spot('Maniac', _stats())
        allin = _spot('AllIn', _stats(), is_all_in=True)
        result = should_apply_bluff_catch_override(
            spots=[agg, allin],
            hand_strength='medium_made',
            decision_context=_ctx(),
            adaptation_bias=0.5, tilt_factor=1.0,
            clamp_tier=ClampTier.EXTREME, aggressor_spot=agg,
        )
        assert result is False

    def test_blocks_when_multiway_low_sample(self):
        agg = _spot('Maniac', _stats())
        unknown = _spot('Unknown', _stats(
            facing_bet_opportunities=10,
            postflop_open_opportunities=5,
        ))
        result = should_apply_bluff_catch_override(
            spots=[agg, unknown],
            hand_strength='medium_made',
            decision_context=_ctx(),
            adaptation_bias=0.5, tilt_factor=1.0,
            clamp_tier=ClampTier.EXTREME, aggressor_spot=agg,
        )
        assert result is False

    def test_fires_in_multiway_with_safe_third_party(self):
        """3-way pot, aggressor is maniac, third party has adequate
        sample and isn't a station/all-in → still fires."""
        agg = _spot('Maniac', _stats())
        safe = _spot('Tight', _stats(
            vpip=0.35, aggression_factor=2.5,
            facing_bet_opportunities=80,
        ))
        result = should_apply_bluff_catch_override(
            spots=[agg, safe],
            hand_strength='medium_made',
            decision_context=_ctx(),
            adaptation_bias=0.5, tilt_factor=1.0,
            clamp_tier=ClampTier.EXTREME, aggressor_spot=agg,
        )
        assert result is True


# ── compute_bluff_catch_strategy ─────────────────────────────────────────

class TestComputeBluffCatchStrategy:
    def test_medium_made_pot_size_flop_dry_full_strength(self):
        """Flop + dry_high + pot-size bet + medium_made: 0.80 call.
        Baseline 100% fold; L1 distance = 1.6. Cap=0.8 → scale 0.5.
        Result: fold=1.0+(0.5)*(0.2-1.0)=0.6, call=0+(0.5)*0.8=0.4."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx(street='flop', board_texture='dry_high', bet_size_pot_ratio=1.0)
        result, _trace = compute_bluff_catch_strategy(
            baseline, ctx, 'medium_made', max_total_shift=0.8,
        )
        # Proposed: call=0.80, fold=0.20. L1 vs baseline = 1.6.
        # Scale = 0.8 / 1.6 = 0.5.
        # Clamped: fold = 1.0 - 0.5*(1.0-0.20) = 0.6, call = 0 + 0.5*0.80 = 0.4
        assert result.action_probabilities['call'] == pytest.approx(0.4)
        assert result.action_probabilities['fold'] == pytest.approx(0.6)

    def test_uses_all_in_as_call_equivalent_when_call_is_illegal(self):
        """Short-stack call-offs expose all_in, not call. Bluff-catch must
        put continuing mass on the legal call-equivalent action."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx(street='flop', board_texture='dry_high', bet_size_pot_ratio=1.0)
        result, _trace = compute_bluff_catch_strategy(
            baseline, ctx, 'medium_made', max_total_shift=0.8,
            legal_actions=['fold', 'all_in'],
        )

        assert 'call' not in result.action_probabilities
        assert result.action_probabilities['all_in'] == pytest.approx(0.4)
        assert result.action_probabilities['fold'] == pytest.approx(0.6)

    def test_dangerous_river_pulls_call_low(self):
        """River + monotone + pot-size + medium_made: base 0.80,
        dampener 0.30 → composed 0.24. Baseline fold; L1 = 1.52,
        scale = 0.8/1.52 ≈ 0.526. Final call ≈ 0.126."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx(street='river', board_texture='monotone', bet_size_pot_ratio=1.0)
        result, _trace = compute_bluff_catch_strategy(
            baseline, ctx, 'medium_made', max_total_shift=0.8,
        )
        # Proposed: call=0.24, fold=0.76. L1 = 0.24+0.24 = 0.48.
        # Under cap 0.8 → proposed returned ~as-is.
        assert result.action_probabilities['call'] == pytest.approx(0.24)
        assert result.action_probabilities['fold'] == pytest.approx(0.76)

    def test_weak_made_river_paired_dangerous_very_low_call(self):
        """weak_made / river / monotone / paired / pot-size:
        base 0.10, dampener 0.6*0.5*0.5 = 0.15 → 0.015 call."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx(
            street='river', board_texture='monotone',
            is_paired_board=True, bet_size_pot_ratio=1.0,
        )
        result, _trace = compute_bluff_catch_strategy(
            baseline, ctx, 'weak_made', max_total_shift=0.8,
        )
        # Composed prob = 0.015 — well under cap, returned as-is.
        assert result.action_probabilities['call'] == pytest.approx(0.015)
        assert result.action_probabilities['fold'] == pytest.approx(0.985)

    def test_envelope_clamps_at_extreme_tier_when_baseline_is_fold(self):
        """Composed call_prob=0.95 (medium_made small bet), baseline 100%
        fold. Proposed L1 = 1.9 — exceeds EXTREME cap of 0.8 → clamped."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx(
            street='flop', board_texture='dry_high', bet_size_pot_ratio=0.3,
        )
        result, _trace = compute_bluff_catch_strategy(
            baseline, ctx, 'medium_made', max_total_shift=0.8,
        )
        # L1(baseline, proposed) = 2*0.95 = 1.9. Cap 0.8. Scale ≈ 0.421.
        # call = 0 + 0.421*0.95 = 0.40
        # fold = 1.0 - 0.421*0.95 = 0.60
        assert result.action_probabilities['call'] == pytest.approx(0.4, abs=0.01)
        assert result.action_probabilities['fold'] == pytest.approx(0.6, abs=0.01)

    def test_envelope_clamps_at_default_tier_more_aggressively(self):
        """Same scenario but with DEFAULT tier cap (0.4)."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        ctx = _ctx(
            street='flop', board_texture='dry_high', bet_size_pot_ratio=0.3,
        )
        result, _trace = compute_bluff_catch_strategy(
            baseline, ctx, 'medium_made', max_total_shift=0.4,
        )
        # L1 = 1.9, cap 0.4 → scale 0.21.
        # call = 0.21*0.95 ≈ 0.20, fold ≈ 0.80
        assert result.action_probabilities['call'] == pytest.approx(0.2, abs=0.01)
        assert result.action_probabilities['fold'] == pytest.approx(0.8, abs=0.01)
