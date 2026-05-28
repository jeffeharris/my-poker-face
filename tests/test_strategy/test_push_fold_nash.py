"""Pins for the chip-EV HU push/fold Nash chart (push_fold_hu.json).

These assert directly on the regenerated JSON (fast — no equilibrium
re-solve) and lock in:
  - the headline anchor behaviors from HoldemResources HUNE,
  - the OLD placeholder bug (A6o/KQo/KJo were folded at 15bb; Nash jams them),
  - structural sanity: ranges widen monotonically as stacks shorten.

The chart is produced by
`poker/strategy/data/generate_push_fold_nash.py`; see
`poker/strategy/data/push_fold_hu_README.md` for the model + anchors.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CHART_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "poker"
    / "strategy"
    / "data"
    / "push_fold_hu.json"
)

DEPTHS = [5, 7, 10, 12, 15]


@pytest.fixture(scope="module")
def chart():
    with CHART_PATH.open() as f:
        return json.load(f)


def _sb(chart, depth, hand):
    actions = chart[f"{depth}bb"]["sb_open"][hand]
    return max(actions, key=actions.get)


def _bb(chart, depth, hand):
    actions = chart[f"{depth}bb"]["bb_vs_jam"][hand]
    return max(actions, key=actions.get)


def _sb_combo_pct(chart, depth):
    from poker.strategy.data.generate_push_fold_nash import CANONICAL_HANDS, COMBO_COUNT

    scen = chart[f"{depth}bb"]["sb_open"]
    total = sum(COMBO_COUNT.values())
    jammed = sum(COMBO_COUNT[h] for h in CANONICAL_HANDS if "jam" in scen[h])
    return jammed / total * 100


def _bb_combo_pct(chart, depth):
    from poker.strategy.data.generate_push_fold_nash import CANONICAL_HANDS, COMBO_COUNT

    scen = chart[f"{depth}bb"]["bb_vs_jam"]
    total = sum(COMBO_COUNT.values())
    called = sum(COMBO_COUNT[h] for h in CANONICAL_HANDS if "call" in scen[h])
    return called / total * 100


class TestMetaIsNash:
    def test_calibration_status_is_nash(self, chart):
        assert chart["meta"]["calibration_status"] == "nash_chipEV_no_ante"

    def test_source_documents_method(self, chart):
        src = chart["meta"].get("source", "")
        assert "Nash" in src and "no ante" in src


class TestOldPlaceholderBugFixed:
    """The placeholder folded these at 15bb; Nash shoves them well above 20bb."""

    @pytest.mark.parametrize("hand", ["A6o", "KQo", "KJo", "KTo", "QJo", "JTo", "76s"])
    def test_sb_jams_at_15bb(self, chart, hand):
        assert _sb(chart, 15, hand) == "jam", f"{hand} must JAM at 15bb (was the old bug)"

    @pytest.mark.parametrize("hand", ["AA", "KK"])
    def test_premiums_jam_at_15bb(self, chart, hand):
        assert _sb(chart, 15, hand) == "jam"


class TestSBAnchors:
    def test_32o_folds_when_not_ultra_short(self, chart):
        # 32o SB push threshold ~1.5bb -> folds at every published bucket (>=5).
        for d in DEPTHS:
            assert _sb(chart, d, "32o") == "fold", f"32o should fold at {d}bb"

    def test_5bb_push_is_wide(self, chart):
        # Pure jam/fold at 5bb jams ~74% (the bottom offsuit junk still folds;
        # the true any-two regime is ~2-3bb). Sanity floor well above the old
        # placeholder's measured 77.7% was a coincidence — assert "wide".
        assert _sb_combo_pct(chart, 5) >= 65.0


class TestBBAnchors:
    @pytest.mark.parametrize("hand", ["AA", "KK", "A2o", "KQo", "KJo"])
    def test_calls_at_5bb(self, chart, hand):
        assert _bb(chart, 5, hand) == "call"

    @pytest.mark.parametrize("hand", ["AA", "KK"])
    def test_premiums_call_at_15bb(self, chart, hand):
        assert _bb(chart, 15, hand) == "call"

    def test_a2o_calls_near_15bb(self, chart):
        # A2o BB-call threshold ~15bb (HUNE anchor; pure jam/fold agrees here).
        assert _bb(chart, 12, "A2o") == "call"

    def test_weak_offsuit_folds_to_jam(self, chart):
        # 72o/32o BB-call only ~2.5bb -> fold to a jam at every published bucket.
        for d in DEPTHS:
            assert _bb(chart, d, "72o") == "fold"
            assert _bb(chart, d, "32o") == "fold"

    def test_calls_broadways_at_15bb_per_pot_odds(self, chart):
        # KQo/KJo are a clearly +EV chip-EV call at 15bb: vs the validated
        # ~46%-wide SB jam range they have ~0.54 equity > the ~0.467 price
        # (verified independently with eval7). Wider than some circulating
        # "caller charts," which are inconsistent with the wide jam range — see
        # push_fold_hu_README.md. Pins the correct best-response.
        assert _bb(chart, 15, "KQo") == "call"
        assert _bb(chart, 15, "KJo") == "call"


class TestMonotonicWidening:
    def test_sb_push_pct_widens_as_stacks_shorten(self, chart):
        pcts = [_sb_combo_pct(chart, d) for d in DEPTHS]  # depths ascending
        # Shorter stack (lower depth) => wider (higher %). Strictly non-increasing
        # as depth increases.
        for shallow, deep in zip(pcts, pcts[1:], strict=False):
            assert shallow >= deep, f"SB push % not monotone: {pcts}"
        assert pcts[0] > pcts[-1], "5bb push range must be strictly wider than 15bb"

    def test_bb_call_pct_widens_as_stacks_shorten(self, chart):
        pcts = [_bb_combo_pct(chart, d) for d in DEPTHS]
        for shallow, deep in zip(pcts, pcts[1:], strict=False):
            assert shallow >= deep, f"BB call % not monotone: {pcts}"
        assert pcts[0] > pcts[-1], "5bb call range must be strictly wider than 15bb"

    def test_per_hand_widening(self, chart):
        # Any hand jammed at 15bb must also be jammed at every shorter depth.
        from poker.strategy.data.generate_push_fold_nash import CANONICAL_HANDS

        for hand in CANONICAL_HANDS:
            if _sb(chart, 15, hand) == "jam":
                for d in DEPTHS:
                    assert _sb(chart, d, hand) == "jam", f"{hand} jam@15 but not @{d}"


class TestLookupStillWorks:
    """The drop-in lookup must read the regenerated chart sanely."""

    def test_lookup_actions(self):
        from poker.strategy import push_fold

        push_fold.reset_chart_cache()
        assert push_fold.lookup_push_fold_action("AA", "SB", 5) == "jam"
        assert push_fold.lookup_push_fold_action("AA", "SB", 10) == "jam"
        assert push_fold.lookup_push_fold_action("AA", "SB", 15) == "jam"
        assert push_fold.lookup_push_fold_action("32o", "SB", 15) == "fold"
        # The old bug: KQo must now jam at 15bb via the lookup too.
        assert push_fold.lookup_push_fold_action("KQo", "SB", 15) == "jam"
        # BB facing a jam.
        assert push_fold.lookup_push_fold_action("AA", "BB", 5, facing_jam=True) == "call"
        assert push_fold.lookup_push_fold_action("72o", "BB", 15, facing_jam=True) == "fold"
