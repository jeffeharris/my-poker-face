"""Tests for poker/strategy/defense_floor.py (plan §2)."""

import pytest

from poker.strategy.defense_floor import (
    DANGER_DAMPENER_FLOOR,
    DANGER_DAMPENER_PER_FLAG,
    FLOOR_TARGET_KEEP_ALIVE,
    FLOOR_TARGET_STRONG,
    ROW_KEEP_ALIVE_MEDIUM_MAX_REQ,
    ROW_KEEP_ALIVE_STRONG_MAX_REQ,
    ROW_STRONG_MAX_REQ,
    _floor_target_call_prob,
    _matrix_row_label,
    apply_defense_floor,
)
from poker.strategy.hand_classification import (
    FOUR_FLUSH_BOARD,
    FOUR_STRAIGHT_BOARD,
    NUT_ACTUAL,
    NUT_BLUFF_CATCHER,
    NUT_NEAR,
    NUT_NON_NUT_STRONG,
    PAIRED_BOARD,
)
from poker.strategy.strategy_profile import StrategyProfile


def _make_strategy(call: float, fold: float, raise_: float = 0.0) -> StrategyProfile:
    """Build a small {fold, call, raise_67} strategy."""
    return StrategyProfile(
        action_probabilities={
            'fold': fold,
            'call': call,
            'raise_67': raise_,
        }
    )


# ── Matrix: _floor_target_call_prob ─────────────────────────────────


class TestMatrixRows:
    """The five-row §2 matrix."""

    def test_air_returns_zero_floor(self):
        assert _floor_target_call_prob('air', NUT_NON_NUT_STRONG, 0.10) == 0.0
        assert _floor_target_call_prob('air_no_draw', NUT_BLUFF_CATCHER, 0.10) == 0.0

    def test_bluff_catcher_returns_zero_floor(self):
        # Even with cheap price + strong-looking hand_class, bluff_catcher
        # defers to §7.5 bluff_catch_override.
        assert _floor_target_call_prob('medium_made', NUT_BLUFF_CATCHER, 0.10) == 0.0

    def test_near_nuts_at_45_pct_req_strong_floor(self):
        # Row 3 fires at the ceiling
        assert (
            _floor_target_call_prob(
                'strong_made',
                NUT_NEAR,
                ROW_STRONG_MAX_REQ,
            )
            == FLOOR_TARGET_STRONG
        )

    def test_actual_nuts_above_45_pct_req_no_row3(self):
        # Past row 3's ceiling — falls through to row 4
        target = _floor_target_call_prob('nuts', NUT_ACTUAL, 0.46)
        assert target == 0.0  # row 4 needs ≤35% req; row 5 needs ≤20%

    def test_strong_made_at_35_pct_req_keep_alive(self):
        assert (
            _floor_target_call_prob(
                'strong_made',
                NUT_NON_NUT_STRONG,
                ROW_KEEP_ALIVE_STRONG_MAX_REQ,
            )
            == FLOOR_TARGET_KEEP_ALIVE
        )

    def test_non_nut_strong_at_35_pct_req_keep_alive(self):
        # Row 4 fires via the nut_status branch even when hand_class isn't strong+
        assert (
            _floor_target_call_prob(
                'medium_made',
                NUT_NON_NUT_STRONG,
                0.30,
            )
            == FLOOR_TARGET_KEEP_ALIVE
        )

    def test_medium_made_at_20_pct_req_keep_alive(self):
        assert (
            _floor_target_call_prob(
                'medium_made',
                NUT_NON_NUT_STRONG,
                ROW_KEEP_ALIVE_MEDIUM_MAX_REQ,
            )
            == FLOOR_TARGET_KEEP_ALIVE
        )

    def test_medium_made_at_25_pct_req_no_floor(self):
        # 25% req exceeds row 5's 20% ceiling, and the nut_status here
        # ('unknown' — not near/actual_nuts, not non_nut_strong) doesn't
        # match row 3 or row 4. So no row matches.
        assert _floor_target_call_prob('medium_made', 'unknown', 0.25) == 0.0

    def test_priority_strong_over_keep_alive(self):
        # actual_nuts at 20% — row 3 wins over rows 4/5
        assert (
            _floor_target_call_prob(
                'nuts',
                NUT_ACTUAL,
                0.20,
            )
            == FLOOR_TARGET_STRONG
        )


# ── Matrix row labels (for diagnostics) ─────────────────────────────


class TestMatrixRowLabels:
    def test_air_returns_none(self):
        assert _matrix_row_label('air', NUT_NON_NUT_STRONG, 0.10) is None

    def test_bluff_catcher_returns_none(self):
        assert _matrix_row_label('medium_made', NUT_BLUFF_CATCHER, 0.10) is None

    def test_row_3_label(self):
        assert _matrix_row_label('nuts', NUT_ACTUAL, 0.30) == 'strong'

    def test_row_4_label(self):
        assert (
            _matrix_row_label(
                'medium_made',
                NUT_NON_NUT_STRONG,
                0.30,
            )
            == 'keep_alive_strong'
        )

    def test_row_5_label(self):
        assert (
            _matrix_row_label(
                'medium_made',
                NUT_NEAR,
                0.15,
            )
            == 'strong'
        )  # row 3 fires first since near_nuts at ≤45%
        # But a plain medium_made (not near_nuts) at ≤20%:
        assert (
            _matrix_row_label(
                'medium_made',
                'unknown',
                0.15,
            )
            == 'keep_alive_medium'
        )


# ── apply_defense_floor entry point ─────────────────────────────────


class TestApplyDefenseFloorSkipsAndFires:
    def test_no_bet_to_face_no_op(self):
        s = _make_strategy(call=0.2, fold=0.8)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='strong_made',
            nut_status=NUT_NON_NUT_STRONG,
            danger_flags=frozenset(),
            required_equity=0.10,
            facing_bet=False,
        )
        assert new_s is s
        assert trace.fired is False
        assert trace.reason_code == 'no_bet_to_face'

    def test_prior_override_active_no_op(self):
        s = _make_strategy(call=0.2, fold=0.8)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='strong_made',
            nut_status=NUT_NON_NUT_STRONG,
            danger_flags=frozenset(),
            required_equity=0.10,
            facing_bet=True,
            prior_layer_fired=True,
        )
        assert new_s is s
        assert trace.fired is False
        assert trace.reason_code == 'prior_override_active'

    def test_disabled_emits_disabled_trace(self):
        s = _make_strategy(call=0.2, fold=0.8)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='strong_made',
            nut_status=NUT_NON_NUT_STRONG,
            danger_flags=frozenset(),
            required_equity=0.10,
            facing_bet=True,
            disable_rules={('defense_floor', 'default')},
        )
        assert new_s is s
        assert trace.reason_code == 'disabled_by_ablation'

    def test_call_action_missing_no_op(self):
        s = StrategyProfile(action_probabilities={'fold': 1.0})
        new_s, trace = apply_defense_floor(
            s,
            hand_class='nuts',
            nut_status=NUT_ACTUAL,
            danger_flags=frozenset(),
            required_equity=0.20,
            facing_bet=True,
        )
        assert new_s is s
        assert trace.reason_code == 'call_action_unavailable'

    def test_already_above_floor_no_op(self):
        s = _make_strategy(call=0.95, fold=0.05)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='nuts',
            nut_status=NUT_ACTUAL,
            danger_flags=frozenset(),
            required_equity=0.10,
            facing_bet=True,
        )
        assert new_s is s
        assert trace.reason_code == 'already_above_floor'

    def test_floor_fires_for_nut_hand_cheap_price(self):
        # The canonical case from plan example 3 (nut flush at 42% pot odds)
        s = _make_strategy(call=0.1, fold=0.9)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='strong_made',
            nut_status=NUT_ACTUAL,
            danger_flags=frozenset(),
            required_equity=0.42,
            facing_bet=True,
        )
        assert trace.fired is True
        assert new_s.action_probabilities['call'] == pytest.approx(
            FLOOR_TARGET_STRONG,
            abs=1e-6,
        )
        assert new_s.action_probabilities['fold'] < 0.9

    def test_redistribution_preserves_normalization(self):
        s = _make_strategy(call=0.1, fold=0.7, raise_=0.2)
        new_s, _ = apply_defense_floor(
            s,
            hand_class='strong_made',
            nut_status=NUT_NON_NUT_STRONG,
            danger_flags=frozenset(),
            required_equity=0.30,
            facing_bet=True,
        )
        total = sum(new_s.action_probabilities.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_redistribution_proportional_to_non_call_mass(self):
        # call=0.1, fold=0.6, raise=0.3. Target=0.80. Delta=0.7.
        # New non-call total = 0.9 - 0.7 = 0.2. Scale = 0.2/0.9 = 0.222.
        # New fold = 0.6 * 0.222 = 0.133. New raise = 0.3 * 0.222 = 0.067.
        s = _make_strategy(call=0.1, fold=0.6, raise_=0.3)
        new_s, _ = apply_defense_floor(
            s,
            hand_class='strong_made',
            nut_status=NUT_NON_NUT_STRONG,
            danger_flags=frozenset(),
            required_equity=0.30,
            facing_bet=True,
        )
        assert new_s.action_probabilities['call'] == pytest.approx(0.80)
        # fold and raise scale proportionally
        assert new_s.action_probabilities['fold'] == pytest.approx(
            0.6 * (0.2 / 0.9),
            abs=1e-6,
        )
        assert new_s.action_probabilities['raise_67'] == pytest.approx(
            0.3 * (0.2 / 0.9),
            abs=1e-6,
        )

    def test_bluff_catcher_no_op_even_at_cheap_price(self):
        s = _make_strategy(call=0.1, fold=0.9)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='medium_made',
            nut_status=NUT_BLUFF_CATCHER,
            danger_flags=frozenset({PAIRED_BOARD}),
            required_equity=0.15,
            facing_bet=True,
        )
        assert new_s is s
        assert trace.fired is False
        assert trace.reason_code == 'no_eligible_row'


class TestDangerDampener:
    def test_single_board_danger_flag_reduces_target(self):
        # Without dampener: target=0.95, current=0.1 → dampened ≈ same.
        # With paired_board: scale = 1.0 - 0.15 = 0.85. dampened_target
        # = 0.1 + (0.95 - 0.1) * 0.85 = 0.1 + 0.7225 = 0.8225
        s = _make_strategy(call=0.1, fold=0.9)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='strong_made',
            nut_status=NUT_ACTUAL,
            danger_flags=frozenset({PAIRED_BOARD}),
            required_equity=0.30,
            facing_bet=True,
        )
        assert trace.fired is True
        # Floor still fires but at a lower target
        assert new_s.action_probabilities['call'] < FLOOR_TARGET_STRONG
        assert new_s.action_probabilities['call'] > 0.5

    def test_three_danger_flags_clamped_at_floor(self):
        # 3 flags would scale by 1 - 0.45 = 0.55, but floor is 0.40
        s = _make_strategy(call=0.0, fold=1.0)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='strong_made',
            nut_status=NUT_ACTUAL,
            danger_flags=frozenset(
                {
                    PAIRED_BOARD,
                    FOUR_STRAIGHT_BOARD,
                    FOUR_FLUSH_BOARD,
                }
            ),
            required_equity=0.30,
            facing_bet=True,
        )
        assert trace.fired is True
        # 3 flags → scale = max(0.40, 1 - 0.45) = 0.55
        # dampened = 0 + (0.95 - 0) * 0.55 = 0.5225
        expected = 0.0 + (FLOOR_TARGET_STRONG - 0.0) * max(
            DANGER_DAMPENER_FLOOR,
            1.0 - 3 * DANGER_DAMPENER_PER_FLAG,
        )
        assert new_s.action_probabilities['call'] == pytest.approx(
            expected,
            abs=1e-6,
        )

    def test_hand_specific_flags_dont_dampen(self):
        # higher_straight_possible / full_house_possible are hand-specific
        # and already routed via nut_status — they shouldn't double-count
        # as board-danger dampeners. Adding them to flags should leave
        # the dampener unchanged.
        s = _make_strategy(call=0.1, fold=0.9)
        no_flag_s, _ = apply_defense_floor(
            s,
            hand_class='strong_made',
            nut_status=NUT_ACTUAL,
            danger_flags=frozenset(),
            required_equity=0.30,
            facing_bet=True,
        )
        hand_flag_s, _ = apply_defense_floor(
            s,
            hand_class='strong_made',
            nut_status=NUT_ACTUAL,
            danger_flags=frozenset(
                {
                    'higher_straight_possible',
                    'full_house_possible',
                }
            ),
            required_equity=0.30,
            facing_bet=True,
        )
        # Both should target the un-dampened FLOOR_TARGET_STRONG
        assert no_flag_s.action_probabilities['call'] == pytest.approx(
            hand_flag_s.action_probabilities['call'],
            abs=1e-9,
        )


class TestPlanExamples:
    """The two real-leak examples from the plan (Examples 3 and 5)."""

    def test_example_3_nut_flush_at_42_pct_pot_odds(self):
        # Hero A♠3♠ on A♦T♠8♠8♥7♠ — nut spade flush. Paired board.
        # After §1: hand_class='strong_made' (downgraded), nut_status
        # = non_nut_strong (paired_board → FULL_HOUSE_POSSIBLE).
        #
        # At 42% required equity:
        #   - Row 3 (≤45% req) needs near/actual_nuts — we have
        #     non_nut_strong, so row 3 doesn't match.
        #   - Row 4 (≤35% req) caps below 42%.
        # No row fires → hero folds.
        #
        # A candidate "row 4b" (req ≤50% + non_nut_strong + strong+
        # → target 0.65) was implemented, tested, and *rejected* via
        # 1000×5 sim (the extra calls were net-negative against
        # CaseBot's actual jam range — the assumed "wide jam range"
        # turned out to be tighter than expected). See the
        # ROW_JAM_VALUE_CALL_MAX_REQ comment in defense_floor.py for
        # the empirical reasoning.
        s = _make_strategy(call=0.05, fold=0.95)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='strong_made',
            nut_status=NUT_NON_NUT_STRONG,
            danger_flags=frozenset({PAIRED_BOARD}),
            required_equity=0.42,
            facing_bet=True,
        )
        assert trace.fired is False

    def test_example_3_variant_below_35_pct_fires(self):
        # If hero faced a smaller bet (≤35% req) the floor would fire.
        s = _make_strategy(call=0.05, fold=0.95)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='strong_made',
            nut_status=NUT_NON_NUT_STRONG,
            danger_flags=frozenset({PAIRED_BOARD}),
            required_equity=0.33,
            facing_bet=True,
        )
        assert trace.fired is True
        assert new_s.action_probabilities['call'] > 0.05

    def test_example_5_top_pair_at_16_pct_pot_odds(self):
        # Hero K3 on K-Q-J-T-4 (4-Broadway). After §1:
        # hand_class='medium_made' (raw top pair weak kicker stays),
        # nut_status=NUT_BLUFF_CATCHER (FOUR_STRAIGHT_BOARD).
        # §2 row 2 says bluff_catcher → no floor; defer to bluff_catch.
        # This documents the known §1+§2 design gap: top pair on
        # 4-Broadway at 16% pot odds is a clear call, but the matrix
        # as specified routes it to bluff_catch_override which won't
        # fire without an EXTREME-tier aggressor. See plan §"Bottom line".
        s = _make_strategy(call=0.0, fold=1.0)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='medium_made',
            nut_status=NUT_BLUFF_CATCHER,
            danger_flags=frozenset({FOUR_STRAIGHT_BOARD}),
            required_equity=0.1665,
            facing_bet=True,
        )
        # Floor doesn't fire — documented design choice.
        assert trace.fired is False
        assert trace.reason_code == 'no_eligible_row'
