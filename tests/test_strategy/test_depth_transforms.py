"""Unit tests for the depth-chart transforms (generate_depth_charts.t_vs_open).

Locks the 25bb BB-defense fix: a BB-defends node commits its flat defense by
JAMMING short-stacked (it closes the action and gets a price), rather than the
over-fold the cold-defender flat-drop produces. Without this, 25bb BB defense
collapsed ~38pp below 100bb (far past MDF).
"""

from poker.strategy.data import generate_depth_charts as g


def test_bb_jams_its_flat_defense_at_25bb():
    flat = {"call": 0.8, "fold": 0.2}  # a non-value BB flat at 100bb
    bb = g.t_vs_open(flat, 25, is_bb=True)
    cold = g.t_vs_open(flat, 25, is_bb=False)
    assert bb.get("jam", 0) > 0 and "call" not in bb, bb  # BB commits by jamming
    assert cold.get("call", 0) > 0 and "jam" not in cold, cold  # cold-defender keeps a thin call
    assert bb["jam"] > cold.get("call", 0)  # and continues more


def test_bb_value_still_jams_and_pure_fold_untouched_at_25bb():
    value = {"raise_3x": 0.85, "call": 0.1, "fold": 0.05}
    assert g.t_vs_open(value, 25, is_bb=True).get("jam", 0) > 0
    assert g.t_vs_open({"fold": 1.0}, 25, is_bb=True) == {"fold": 1.0}  # load-bearing pure fold


def test_is_bb_is_a_noop_at_100bb():
    p = {"call": 0.6, "raise_3x": 0.1, "fold": 0.3}
    assert g.t_vs_open(p, 100, is_bb=True) == g.t_vs_open(p, 100, is_bb=False)
