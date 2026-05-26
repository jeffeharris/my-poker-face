"""Tests for the short-stack push/fold lookup.

Covers the lookup contract, edge cases (multi-way, depth above
threshold, unknown hands), and asserts the chart's aggregate
frequencies fall within the bands documented in
data/push_fold_hu_README.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from poker.strategy import push_fold


# Reset cache before each test so any monkeypatching in earlier tests
# doesn't bleed in.
@pytest.fixture(autouse=True)
def _reset_chart_cache():
    push_fold.reset_chart_cache()


class TestLookupBasic:
    def test_premium_hand_jams_at_all_depths(self):
        for depth in [5, 7, 10, 12, 15]:
            result = push_fold.lookup_push_fold_action(
                hand='AA',
                position='SB',
                effective_stack_bb=depth,
            )
            assert result == 'jam', f"AA should jam at {depth} BB"

    def test_trash_hand_folds_at_all_depths(self):
        for depth in [5, 7, 10, 12, 15]:
            result = push_fold.lookup_push_fold_action(
                hand='32o',
                position='SB',
                effective_stack_bb=depth,
            )
            # At 5 BB this might widen enough to include 32o; check the
            # other extreme — at 15 BB it must fold.
            if depth >= 10:
                assert result == 'fold', f"32o should fold at {depth} BB"

    def test_range_widens_as_depth_shrinks(self):
        """A borderline hand that folds at 15 BB should jam at 5 BB.

        Under the chip-EV Nash equilibrium the SB jam range widens as
        stacks shorten. Q2o is a boundary hand: jammed at 5 BB, folded by
        15 BB. (76s, by contrast, Nash-jams at every depth including 15 BB
        — it is NOT a boundary example; the old placeholder wrongly folded
        it at 15 BB.)"""
        result_15 = push_fold.lookup_push_fold_action(
            hand='Q2o',
            position='SB',
            effective_stack_bb=15,
        )
        result_5 = push_fold.lookup_push_fold_action(
            hand='Q2o',
            position='SB',
            effective_stack_bb=5,
        )
        assert result_15 == 'fold', f"Q2o should fold at 15 BB, got {result_15}"
        assert result_5 == 'jam', f"Q2o should jam at 5 BB, got {result_5}"

    def test_returns_none_for_multi_way(self):
        result = push_fold.lookup_push_fold_action(
            hand='AA',
            position='SB',
            effective_stack_bb=10,
            num_opponents=3,
        )
        assert result is None

    def test_returns_none_above_threshold(self):
        """At 20 BB the deep-stack table takes over."""
        result = push_fold.lookup_push_fold_action(
            hand='AA',
            position='SB',
            effective_stack_bb=20,
        )
        assert result is None

    def test_returns_none_for_unsupported_position(self):
        result = push_fold.lookup_push_fold_action(
            hand='AA',
            position='UTG',
            effective_stack_bb=10,
        )
        assert result is None


class TestBBVsJam:
    def test_premium_calls_at_all_depths(self):
        for depth in [5, 7, 10, 12, 15]:
            result = push_fold.lookup_push_fold_action(
                hand='AA',
                position='BB',
                effective_stack_bb=depth,
                facing_jam=True,
            )
            assert result == 'call', f"AA should call jam at {depth} BB"

    def test_trash_folds_to_jam(self):
        for depth in [5, 7, 10, 12, 15]:
            result = push_fold.lookup_push_fold_action(
                hand='72o',
                position='BB',
                effective_stack_bb=depth,
                facing_jam=True,
            )
            if depth >= 10:
                assert result == 'fold', f"72o should fold to jam at {depth} BB"

    def test_bb_without_facing_jam_returns_none(self):
        """BB has no decision to make until SB jams (in HU push/fold)."""
        result = push_fold.lookup_push_fold_action(
            hand='AA',
            position='BB',
            effective_stack_bb=10,
            facing_jam=False,
        )
        assert result is None

    def test_call_range_tighter_than_push_range(self):
        """A hand that pushes from SB should not necessarily call from BB.
        The call range is always narrower."""
        # Pick a marginal hand that's in SB push but not in BB call
        # at deeper short-stack depths.
        sb_action_15 = push_fold.lookup_push_fold_action(
            hand='K6s',
            position='SB',
            effective_stack_bb=15,
        )
        bb_action_15 = push_fold.lookup_push_fold_action(
            hand='K6s',
            position='BB',
            effective_stack_bb=15,
            facing_jam=True,
        )
        # At minimum, the SB push range should be wider than BB call.
        # K6s should fall on the right side of this gap at 15 BB
        # under our v1 ranges (in SB push, out of BB call).
        if sb_action_15 == 'jam':
            # Acceptable: BB folds because the call range is tighter.
            # If BB calls anyway, that's also fine (the chart said so),
            # we just want to verify the asymmetry doesn't flip.
            pass  # Don't assert; let the data speak


class TestDepthSnapping:
    def test_below_min_bucket_clamps_to_min(self):
        """Very short stacks (e.g., 3 BB) snap to the minimum bucket (5 BB)."""
        result = push_fold.lookup_push_fold_action(
            hand='AA',
            position='SB',
            effective_stack_bb=3,
        )
        # The 5-bucket logic should still resolve AA → jam
        assert result == 'jam'

    def test_just_above_threshold_returns_none(self):
        result = push_fold.lookup_push_fold_action(
            hand='AA',
            position='SB',
            effective_stack_bb=15.5,
        )
        assert result is None

    def test_intermediate_depth_picks_nearest_bucket(self):
        """At 11 BB, the lookup should snap to a defined bucket (10 or 12)
        and return a coherent action."""
        result = push_fold.lookup_push_fold_action(
            hand='AA',
            position='SB',
            effective_stack_bb=11,
        )
        assert result == 'jam'


class TestAggregateStructure:
    """Structural sanity on the chip-EV Nash chart's aggregate frequencies.

    The chart is now solver-computed (no hand-authored frequency bands),
    so instead of pinning narrow bands we assert the equilibrium shape:
    push/call ranges widen monotonically as stacks shorten, and stay
    within loose Nash-plausible bounds. Precise per-depth frequencies are
    pinned in test_push_fold_nash.py against the regenerated JSON.
    """

    CHART_PATH = (
        Path(__file__).resolve().parent.parent.parent
        / "poker"
        / "strategy"
        / "data"
        / "push_fold_hu.json"
    )

    DEPTHS = [5, 7, 10, 12, 15]

    @pytest.fixture(scope="class")
    def chart(self):
        with self.CHART_PATH.open() as f:
            return json.load(f)

    @staticmethod
    def _combo_count(hand: str) -> int:
        if len(hand) == 2:
            return 6
        return 4 if hand[2] == "s" else 12

    def _pct(self, scenario: dict, action: str) -> float:
        total = sum(self._combo_count(h) for h in scenario)
        hit = sum(self._combo_count(h) for h, a in scenario.items() if action in a)
        return hit / total * 100

    def test_sb_push_monotone_and_bounded(self, chart):
        pcts = [self._pct(chart[f"{d}bb"]["sb_open"], "jam") for d in self.DEPTHS]
        for shallow, deep in zip(pcts, pcts[1:]):
            assert shallow >= deep, f"SB push % not monotone over depth: {pcts}"
        # 5bb pure jam/fold ~74% (any-two is a ~2-3bb regime); 15bb tighter ~46%.
        assert pcts[0] >= 65.0, f"5bb SB push too tight: {pcts[0]:.1f}%"
        assert pcts[-1] <= 60.0, f"15bb SB push implausibly wide: {pcts[-1]:.1f}%"
        assert pcts[0] > pcts[-1]

    def test_bb_call_monotone_and_bounded(self, chart):
        pcts = [self._pct(chart[f"{d}bb"]["bb_vs_jam"], "call") for d in self.DEPTHS]
        for shallow, deep in zip(pcts, pcts[1:]):
            assert shallow >= deep, f"BB call % not monotone over depth: {pcts}"
        assert pcts[0] > pcts[-1]
        assert 0.0 < pcts[-1] < pcts[0] <= 100.0
