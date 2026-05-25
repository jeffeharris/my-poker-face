"""Tests for TIEREDBOT_DECISION_QUALITY.md §3 (passive archetype behavior).

§3 is *application-level*: it documents which existing rules should
shape strategy differently (or identically) per the
`pure_station` / `sticky_jammer` archetype split that §1.5a's
classifier surfaces. Per the audit, §2 (defense floor) + Phase 8
(`value_vs_station`) cover every cell of the §3 table that's in
scope (bluff reduction is deferred to §5). No new strategy code
ships in §3 — these tests are the deliverable, verifying the
table holds.

## Behavior table being tested

| Behavior                            | `pure_station`    | `sticky_jammer`   |
|-------------------------------------|-------------------|-------------------|
| Value bet strong hands              | ↑ via Phase 8     | ↑ via Phase 8     |
| Bluff frequency                     | ↓ via §5 (deferred) | ↓ via §5 (deferred) |
| Marginal continues vs large bets    | no widening       | no widening       |
| Strong/nut continues at good prices | preserve via §2   | preserve via §2   |

The split that §5 will introduce (bluff reduction asymmetry) is
not exercised here — these tests will be the regression guard
when §5 lands.
"""

import pytest

from poker.strategy.defense_floor import (
    FLOOR_TARGET_KEEP_ALIVE,
    FLOOR_TARGET_STRONG,
    apply_defense_floor,
)
from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    OpponentSpot,
    classify_opponent_archetype,
    compute_value_vs_station_intensity,
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

# ── Canonical archetype fixtures ────────────────────────────────────


def _pure_station_stats(hands: int = 100) -> AggregatedOpponentStats:
    """Stats that classify_opponent_archetype labels 'pure_station':
    high VPIP, low AF, low all_in_frequency (below the
    PASSIVE_WITH_JAMS threshold of 0.05).

    Migrated to opportunity-normalized vpip_per_voluntary_opportunity
    so _is_hyper_passive fires under the new threshold (0.70).
    """
    return AggregatedOpponentStats(
        hands_observed=hands,
        vpip=0.75,
        pfr=0.05,
        vpip_per_voluntary_opportunity=0.85,
        preflop_voluntary_opportunities=hands - 5,
        aggression_factor=0.3,
        all_in_frequency=0.0,
    )


def _sticky_jammer_stats(hands: int = 100) -> AggregatedOpponentStats:
    """Stats that classify_opponent_archetype labels 'sticky_jammer':
    hyper_passive thresholds (high VPIP, low AF) + all_in_frequency
    above the PASSIVE_WITH_JAMS threshold but below the
    HYPER_AGG_ALL_IN_FREQ_THRESHOLD (0.30) so it doesn't get reclassified
    as hyper_aggressive via the disjunction.
    """
    return AggregatedOpponentStats(
        hands_observed=hands,
        vpip=0.65,
        pfr=0.03,
        vpip_per_voluntary_opportunity=0.80,
        preflop_voluntary_opportunities=hands - 5,
        aggression_factor=0.5,
        all_in_frequency=0.12,
    )


def _balanced_stats(hands: int = 100) -> AggregatedOpponentStats:
    """Mid-spectrum opponent that classify_opponent_archetype returns None for."""
    return AggregatedOpponentStats(
        hands_observed=hands,
        vpip=0.25,
        pfr=0.20,
        vpip_per_voluntary_opportunity=0.40,
        preflop_voluntary_opportunities=hands - 5,
        aggression_factor=2.5,
        all_in_frequency=0.02,
    )


def _spot(
    stats: AggregatedOpponentStats, *, is_active: bool = True, is_all_in: bool = False
) -> OpponentSpot:
    return OpponentSpot(
        name='Opp',
        stats=stats,
        is_active=is_active,
        is_all_in=is_all_in,
    )


def _strategy(call: float, fold: float, raise_: float = 0.0) -> StrategyProfile:
    return StrategyProfile(
        action_probabilities={
            'fold': fold,
            'call': call,
            'raise_67': raise_,
        }
    )


# ── Fixtures sanity: confirm the archetype labels are what we expect ─


class TestArchetypeFixtures:
    """Sanity check that the fixtures land on the expected archetype labels.

    If `_pure_station_stats` or `_sticky_jammer_stats` drifts, every
    other §3 test downstream becomes meaningless — so we lock the
    fixture-to-label mapping at the start.
    """

    def test_pure_station_label(self):
        assert classify_opponent_archetype(_pure_station_stats()) == 'pure_station'

    def test_sticky_jammer_label(self):
        assert classify_opponent_archetype(_sticky_jammer_stats()) == 'sticky_jammer'

    def test_balanced_returns_none(self):
        assert classify_opponent_archetype(_balanced_stats()) is None


# ── Row 1: Value bet strong hands ↑ via Phase 8 for both archetypes ──


class TestValueVsStationFiresForBothPassiveArchetypes:
    """The Phase 8 `value_vs_station` rule must fire (intensity > 0)
    for both pure_station and sticky_jammer, since hero's value-bet
    upside is similar against both — they call value bets a lot. The
    plan's table assigns ↑ to both columns.
    """

    def test_pure_station_drives_value_vs_station_upside(self):
        intensity = compute_value_vs_station_intensity(
            [
                _spot(_pure_station_stats()),
            ]
        )
        assert intensity > 0.0

    def test_sticky_jammer_drives_value_vs_station_upside(self):
        intensity = compute_value_vs_station_intensity(
            [
                _spot(_sticky_jammer_stats()),
            ]
        )
        assert intensity > 0.0

    def test_non_station_does_not_drive_upside(self):
        # Balanced / aggressive opponent → no station upside
        intensity = compute_value_vs_station_intensity(
            [
                _spot(_balanced_stats()),
            ]
        )
        assert intensity == 0.0

    def test_both_intensities_nontrivially_above_zero(self):
        # Plan's table assigns ↑ to both archetypes. Magnitudes can
        # differ — the underlying `hyper_passive` intensity ramp keys
        # off VPIP, so a higher-VPIP `pure_station` fixture will produce
        # higher intensity than a lower-VPIP `sticky_jammer`. The §3
        # claim is just that both ↑ — confirm both clear the
        # "nontrivial" bar (> 10%).
        pure = compute_value_vs_station_intensity([_spot(_pure_station_stats())])
        sticky = compute_value_vs_station_intensity([_spot(_sticky_jammer_stats())])
        assert pure > 0.10
        assert sticky > 0.10


# ── Row 4: Strong/nut continues at good prices fire for both ─────────


class TestDefenseFloorFiresForStrongHandsRegardlessOfArchetype:
    """The §2 defense floor doesn't read archetype — only hand class +
    nut status + price. Per the table, strong/nut continues at good
    prices should preserve for both archetypes. This test confirms the
    floor fires identically regardless of which archetype hero faces.
    """

    @pytest.mark.parametrize(
        'hand_class,nut_status,req,expected_target',
        [
            # Row 3: near/actual_nuts at ≤45% req → strong target
            ('strong_made', NUT_ACTUAL, 0.40, FLOOR_TARGET_STRONG),
            ('nuts', NUT_NEAR, 0.30, FLOOR_TARGET_STRONG),
            # Row 4: strong_made at ≤35% req → keep_alive target
            ('strong_made', NUT_NON_NUT_STRONG, 0.30, FLOOR_TARGET_KEEP_ALIVE),
        ],
    )
    def test_strong_hands_at_good_prices_fire_floor(
        self,
        hand_class,
        nut_status,
        req,
        expected_target,
    ):
        # The floor doesn't see the archetype directly — it only sees
        # what the hand_class + nut_status + price say. Confirming that
        # the table's "preserve via §2" cell holds for both columns by
        # virtue of the floor being archetype-agnostic.
        s = _strategy(call=0.1, fold=0.9)
        new_s, trace = apply_defense_floor(
            s,
            hand_class=hand_class,
            nut_status=nut_status,
            danger_flags=frozenset(),
            required_equity=req,
            facing_bet=True,
        )
        assert trace.fired is True
        assert new_s.action_probabilities['call'] == pytest.approx(
            expected_target,
            abs=1e-6,
        )


# ── Row 3: Marginal continues vs large bets / jams — no widening ─────


class TestDefenseFloorDoesNotWidenMarginalsAtLargeBets:
    """The plan's row 3 says marginal continues vs large bets / jams
    should NOT widen — even for pure_station, where §2 is allowed to
    "widen at good prices" only. At large prices, §2's matrix
    correctly rejects marginal hands (rows 3-4 require near_nuts /
    strong+ / non_nut_strong; row 5 requires ≤20% req).

    This is the regression guard for Phase 8.1b's failure mode: a
    blanket fold-mass suppression that bled bb/100 by calling
    marginals into jam ranges.
    """

    def test_medium_made_at_large_bet_does_not_fire(self):
        # 40% req = "large" bucket. medium_made + non_nut_strong
        # would fire row 4 only at ≤35% req. At 40% no row matches.
        s = _strategy(call=0.05, fold=0.95)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='medium_made',
            nut_status=NUT_NON_NUT_STRONG,
            danger_flags=frozenset(),
            required_equity=0.40,
            facing_bet=True,
        )
        assert trace.fired is False
        assert new_s is s

    def test_weak_made_at_large_bet_does_not_fire(self):
        # weak_made never qualifies for any row (rows 3-4 need strong
        # classifiers; row 5 needs medium+).
        s = _strategy(call=0.05, fold=0.95)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='weak_made',
            nut_status=NUT_NON_NUT_STRONG,
            danger_flags=frozenset(),
            required_equity=0.40,
            facing_bet=True,
        )
        assert trace.fired is False
        assert new_s is s

    def test_medium_made_at_jam_price_does_not_fire(self):
        # 47% req is in the 'jam' bucket. No matrix row covers
        # medium_made at this price.
        s = _strategy(call=0.0, fold=1.0)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='medium_made',
            nut_status=NUT_NON_NUT_STRONG,
            danger_flags=frozenset(),
            required_equity=0.47,
            facing_bet=True,
        )
        assert trace.fired is False
        assert new_s is s

    def test_medium_bluff_catcher_on_dangerous_board_does_not_fire(self):
        # The compound case: medium_made hand with bluff_catcher status
        # (e.g., top pair on 4-Broadway, paired-board pair) — row 2
        # exits regardless of price. This is the routing that protects
        # against Phase 8.1b-style over-widening.
        s = _strategy(call=0.1, fold=0.9)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='medium_made',
            nut_status=NUT_BLUFF_CATCHER,
            danger_flags=frozenset({FOUR_STRAIGHT_BOARD}),
            required_equity=0.15,
            facing_bet=True,
        )
        assert trace.fired is False
        assert trace.reason_code == 'no_eligible_row'


# ── Row 3: pure_station — allow wider AT GOOD PRICES (§2 row 5) ──────


class TestDefenseFloorWidensCheapMediumHands:
    """The plan's row 3 says pure_station's marginal continues can
    widen *at good prices*. §2 row 5 handles this: medium_made at
    ≤20% req gets call mass pumped to FLOOR_TARGET_KEEP_ALIVE.

    Sticky_jammer's marginal cell says "no change" — but §2 isn't
    archetype-aware, so it fires for both at cheap prices. Per the
    plan's "Phase 8.1b" lesson, the failure mode was at LARGE
    bets, not cheap ones; widening cheap calls vs sticky_jammer is
    not the regression we're guarding against.
    """

    def test_medium_made_at_cheap_price_fires(self):
        # 18% req = "small" bucket → row 5 fires for medium_made+
        s = _strategy(call=0.1, fold=0.9)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='medium_made',
            nut_status=NUT_NON_NUT_STRONG,
            danger_flags=frozenset(),
            required_equity=0.18,
            facing_bet=True,
        )
        assert trace.fired is True
        assert new_s.action_probabilities['call'] == pytest.approx(
            FLOOR_TARGET_KEEP_ALIVE,
            abs=1e-6,
        )

    def test_medium_made_just_above_cheap_threshold_does_not_fire(self):
        # 22% req exceeds row 5's 20% ceiling; row 4 needs strong+ or
        # non_nut_strong but with medium_made class — row 4 fires only
        # if nut_status is non_nut_strong. Let me pick a status that
        # disqualifies row 4 to confirm row 5's ceiling.
        s = _strategy(call=0.1, fold=0.9)
        new_s, trace = apply_defense_floor(
            s,
            hand_class='medium_made',
            nut_status='unknown',
            danger_flags=frozenset(),
            required_equity=0.22,
            facing_bet=True,
        )
        assert trace.fired is False
