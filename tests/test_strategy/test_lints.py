"""Unit tests for poker/strategy/lints.py.

Tests the lint *logic* against crafted inputs (clean charts pass, planted bugs
fire), so they stay valid after the live charts are fixed — unlike asserting on
the current broken chart state.
"""

from poker.strategy import lints


def _node(overrides=None):
    node = {h: {"fold": 1.0} for h in lints.canonical_hands()}
    for h, d in (overrides or {}).items():
        node[h] = d
    return node


def test_canonical_hands_shape():
    hands = lints.canonical_hands()
    assert len(hands) == 169
    assert {"AA", "AKs", "AKo"} <= set(hands)
    assert lints._combos("AA") == 6 and lints._combos("AKs") == 4 and lints._combos("AKo") == 12


def test_vs3bet_ip_classification():
    assert lints._vs3bet_is_ip("BTN_vs_SB")      # 3-bettor in the blinds → opener IP
    assert not lints._vs3bet_is_ip("UTG_vs_HJ")  # 3-bettor acts after → opener OOP
    assert not lints._vs3bet_is_ip("SB_vs_BB")   # SB open vs BB 3-bet → SB still OOP


def test_weights_sum_catches_bad_cells():
    bad = {"vs_open": {"BB_vs_BTN": _node({"AA": {"call": 0.9}, "KK": {"call": 0.5, "fold": -0.1, "raise_3x": 0.6}})}}
    fails = lints.lint_weights_sum(bad)
    assert any("AA" in f and "≠ 1.0" in f for f in fails)
    assert any("KK" in f and "negative" in f for f in fails)
    assert lints.lint_weights_sum({"vs_open": {"BB_vs_BTN": _node()}}) == []


def test_anti_clone_cross_opener_vs_same_opener():
    n = _node({"AA": {"raise_2.2x": 1.0}})
    # vs_3bet opener = first token. Different openers + identical → copied-range bug.
    cross = {"vs_3bet": {"UTG_vs_HJ": n, "CO_vs_BTN": dict(n)}}
    assert lints.lint_anti_clone(cross)
    # Same opener (UTG) + identical → accepted opener-keyed simplification, NOT flagged.
    same = {"vs_3bet": {"UTG_vs_HJ": n, "UTG_vs_CO": dict(n)}}
    assert lints.lint_anti_clone(same) == []
    # Genuinely distinct → no flag.
    distinct = {"vs_3bet": {"UTG_vs_HJ": _node({"AA": {"raise_2.2x": 1.0}}),
                            "CO_vs_BTN": _node({"KK": {"raise_2.2x": 1.0}})}}
    assert lints.lint_anti_clone(distinct) == []


def test_legal_vocab_flags_jam_at_100bb():
    chart = {"vs_3bet": {"UTG_vs_HJ": _node({"AA": {"jam": 1.0}})}}
    assert lints.lint_legal_vocab(chart)              # jam illegal in 100bb vs_3bet
    assert lints.lint_legal_vocab(chart, allow_jam=True) == []  # allowed at depth


def test_bb_defend_floor_fires_when_too_tight():
    tight = {"vs_open": {"BB_vs_SB": _node({"AA": {"call": 1.0}})}}  # ~0.5% defend ≪ 58%
    assert lints.lint_bb_defend_floors(tight)
    # non-BB nodes are ignored by this lint
    assert lints.lint_bb_defend_floors({"vs_open": {"BTN_vs_UTG": _node()}}) == []


def test_vs3bet_fold_to_3bet_relative_to_open_range():
    rfi = {"BTN": {"AA": {"raise_2.5bb": 1.0}, "KK": {"raise_2.5bb": 1.0},
                   "72o": {"raise_2.5bb": 1.0}}}
    # continues only AA of a 3-hand open → fold-to-3bet ~75% > IP ceiling → fires
    leak = {"rfi": rfi, "vs_3bet": {"BTN_vs_SB": _node({"AA": {"call": 1.0}})}}
    assert lints.lint_vs3bet_fold_to_3bet(leak)
    # continues the whole open range → fold-to-3bet 0 → passes
    ok = {"rfi": rfi, "vs_3bet": {"BTN_vs_SB": _node({
        "AA": {"call": 1.0}, "KK": {"call": 1.0}, "72o": {"call": 1.0}})}}
    assert lints.lint_vs3bet_fold_to_3bet(ok) == []


def test_cliff_band_guard():
    chart = {"vs_open": {"BB_vs_SB": _node({"AA": {"raise_3x": 0.47, "fold": 0.53}})}}
    assert lints.lint_cliff_band(chart)  # 0.47 is in (0.45, 0.50)
    clean = {"vs_open": {"BB_vs_SB": _node({"AA": {"raise_3x": 0.85, "fold": 0.15},
                                            "A5s": {"raise_3x": 0.35, "fold": 0.65}})}}
    assert lints.lint_cliff_band(clean) == []


def test_depth_rfi_passthrough():
    base = {"rfi": {"BTN": {"AA": {"raise_2.5bb": 1.0}}}}
    same = {"rfi": {"BTN": {"AA": {"raise_2.5bb": 1.0}}}}
    drift = {"rfi": {"BTN": {"AA": {"raise_2.5bb": 0.5, "fold": 0.5}}}}
    assert lints.lint_depth_rfi_passthrough(base, same, 50) == []
    assert lints.lint_depth_rfi_passthrough(base, drift, 50)


def test_base_runner_executes_over_live_chart():
    """Smoke: the runner wires every lint and returns a result per lint (no
    assertion on pass/fail — that depends on the live chart's current state)."""
    import json
    import os
    with open(os.path.join(lints._DATA, "preflop_100bb_6max.json")) as f:
        base = json.load(f)
    report = lints.lint_base_chart(base)
    assert set(report) == {fn.__name__ for fn in lints.BASE_LINTS}
    assert all(isinstance(v, list) for v in report.values())
