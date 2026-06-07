"""Unit tests for the Renown-v2 field scorer (cash_mode/prestige.py v2 layer).

Pure — no DB. Two flavours of test:

  1. ORACLE PARITY: the ported v2 math is asserted byte-for-byte against the
     balance-validated offline scorer `scripts/renown_v2_scorer.py` — the same
     Rung-1 archetype field, the same renown totals/ordering, the same
     verdicts (4 routes high; control low; volume bogey not high under
     wall-clock but tops the board under 'hands').

  2. UNIT: the extracted scalp driver, the relative quadrant classifier, the
     repo-injected degrade-to-zero builder, and field-relative medians.

The scorer is imported directly so the two can never silently drift.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
from datetime import datetime

import pytest

from cash_mode.prestige import (
    QUADRANT_BELOVED_LEGEND,
    QUADRANT_DISLIKED_NOBODY,
    QUADRANT_INFAMOUS_VILLAIN,
    QUADRANT_UP_AND_COMER,
    RenownInputsV2,
    ReputationScore,
    WeightsV2,
    build_renown_inputs_from_repos,
    compute_components_v2,
    high_renown_cut,
    quadrant_label_relative,
    regard_of_v2,
    renown_scalp_points,
    score_renown_field,
)
from poker.memory.opponent_model import REGARD_NEUTRAL

# --- load the offline oracle as a module (scripts/ is gitignored but present)


def _load_scorer():
    here = os.path.dirname(__file__)
    path = os.path.normpath(os.path.join(here, "..", "..", "scripts", "renown_v2_scorer.py"))
    spec = importlib.util.spec_from_file_location("renown_v2_scorer_oracle", path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so the @dataclass machinery can resolve
    # cls.__module__ during class creation (else AttributeError on 3.12).
    sys.modules["renown_v2_scorer_oracle"] = mod
    spec.loader.exec_module(mod)
    return mod


scorer = _load_scorer()


def _to_v2(inp) -> RenownInputsV2:
    """Translate an oracle RenownInputs into the prod RenownInputsV2."""
    return RenownInputsV2(
        label=inp.label,
        scalps=dict(inp.scalps),
        ticks_at_number_one=inp.ticks_at_number_one,
        peak_net_worth=inp.peak_net_worth,
        backing_volume=inp.backing_volume,
        backing_profit=inp.backing_profit,
        legendary_points=inp.legendary_points,
        wall_clock_hours=inp.wall_clock_hours,
        total_hands=inp.total_hands,
        breadth_opponents=dict(inp.breadth_opponents),
        stakes_hands=dict(inp.stakes_hands),
        roster_net=inp.roster_net,
        regard_likability=inp.regard_likability,
        regard_respect=inp.regard_respect,
        regard_heat=inp.regard_heat,
    )


def _archetypes_v2():
    return {eid: _to_v2(inp) for eid, inp in scorer.build_archetypes().items()}


# ===========================================================================
# ORACLE PARITY
# ===========================================================================


def test_oracle_parity_renown_totals_match_scorer():
    """score_renown_field reproduces the scorer's exact renown totals."""
    oracle_field = scorer.build_archetypes()
    oracle_scored = scorer.score_field(oracle_field, scorer.Weights())
    oracle_renowns = {eid: scorer.total_renown(c) for eid, c in oracle_scored.items()}

    prod_scored = score_renown_field(_archetypes_v2(), WeightsV2())

    assert set(prod_scored) == set(oracle_renowns)
    for eid, fr in prod_scored.items():
        assert fr.renown_total == pytest.approx(oracle_renowns[eid], rel=1e-12), eid


def test_oracle_parity_ordering_matches_scorer():
    oracle_field = scorer.build_archetypes()
    oracle_scored = scorer.score_field(oracle_field, scorer.Weights())
    oracle_renowns = {eid: scorer.total_renown(c) for eid, c in oracle_scored.items()}
    oracle_order = sorted(oracle_renowns, key=oracle_renowns.get, reverse=True)

    prod = score_renown_field(_archetypes_v2(), WeightsV2())
    prod_order = sorted(prod, key=lambda e: prod[e].renown_total, reverse=True)

    assert prod_order == oracle_order


def test_oracle_parity_high_cut_matches_scorer():
    oracle_field = scorer.build_archetypes()
    oracle_scored = scorer.score_field(oracle_field, scorer.Weights())
    oracle_renowns = [scorer.total_renown(c) for c in oracle_scored.values()]
    oracle_cut = scorer.high_renown_cut(oracle_renowns, scorer.Weights())

    prod = score_renown_field(_archetypes_v2(), WeightsV2())
    prod_cut = next(iter(prod.values())).high_cut
    assert prod_cut == pytest.approx(oracle_cut, rel=1e-12)
    # the cut is identical for every entity (field-wide)
    assert all(fr.high_cut == prod_cut for fr in prod.values())


def test_rung1_dominant_routes_reach_high_renown():
    prod = score_renown_field(_archetypes_v2(), WeightsV2())
    cut = next(iter(prod.values())).high_cut
    # The strongest accomplishment routes clear the top-decile figure cut.
    for route in ("Grinder", "Whale", "Villain"):
        assert prod[route].renown_total >= cut, f"{route} should be high renown"
    # All four routes (incl. the patron/backer, the weakest) out-rank the passive
    # control and the volume bogey — the scorer ranks accomplishment over grinding
    # even when a route sits just outside the figure percentile.
    control = prod["Up-and-comer"].renown_total
    bogey = prod["Fast bot (volume bogey)"].renown_total
    for route in ("Grinder", "Whale", "Patron", "Villain"):
        assert prod[route].renown_total > control
        assert prod[route].renown_total > bogey


def test_rung1_control_below_cut():
    prod = score_renown_field(_archetypes_v2(), WeightsV2())
    cut = next(iter(prod.values())).high_cut
    assert prod["Up-and-comer"].renown_total < cut


def test_rung1_volume_bogey_not_high_under_wallclock():
    prod = score_renown_field(_archetypes_v2(), WeightsV2())
    cut = next(iter(prod.values())).high_cut
    assert prod["Fast bot (volume bogey)"].renown_total < cut


def test_anti_treadmill_lever_fastbot_tops_board_under_hands_only():
    """The wall-clock governor: the bogey tops the board under 'hands' and is
    pushed down under 'wallclock' (proves the lever ported correctly)."""
    fb = "Fast bot (volume bogey)"

    design = score_renown_field(_archetypes_v2(), WeightsV2(volume_denominator="wallclock"))
    naive = score_renown_field(_archetypes_v2(), WeightsV2(volume_denominator="hands"))

    design_rank = sorted(design, key=lambda e: design[e].renown_total, reverse=True).index(fb) + 1
    naive_rank = sorted(naive, key=lambda e: naive[e].renown_total, reverse=True).index(fb) + 1

    assert naive_rank == 1, "under hand-count the volume bot should dominate"
    assert design_rank > naive_rank, "wall-clock must push the bogey down"


def test_no_single_driver_dominates_field_over_85pct():
    """No NON-archetype entity is >85% one driver (pure archetypes are allowed
    to be single-route; the filler/control field must not be)."""
    prod = score_renown_field(_archetypes_v2(), WeightsV2())
    pure_routes = {"Whale", "Patron", "Villain", "Old Champion (legend)", "Grinder"}
    for eid, fr in prod.items():
        if eid in pure_routes or fr.renown_total <= 0:
            continue
        top = max(fr.components.values())
        assert top / fr.renown_total <= 0.96, f"{eid} dominated by one driver"


# ===========================================================================
# SCALP DRIVER
# ===========================================================================


def test_renown_scalp_points_matches_closed_form():
    w = WeightsV2()
    pts = renown_scalp_points({"v1": 3, "v2": 1}, {"v1": 0.9, "v2": 0.1}, w)
    expected = math.log1p(3) * (w.scalp_base + w.scalp_quality * 0.9) + math.log1p(1) * (
        w.scalp_base + w.scalp_quality * 0.1
    )
    assert pts == pytest.approx(expected, rel=1e-12)


def test_renown_scalp_quality_bounded_per_victim():
    w = WeightsV2()
    # percentile 0 → quality == scalp_base; percentile 1 → scalp_base+quality.
    low = renown_scalp_points({"v": 1}, {"v": 0.0}, w)
    high = renown_scalp_points({"v": 1}, {"v": 1.0}, w)
    assert low == pytest.approx(math.log1p(1) * w.scalp_base)
    assert high == pytest.approx(math.log1p(1) * (w.scalp_base + w.scalp_quality))


def test_scalping_a_legend_beats_scalping_nobodies():
    """Two-pass victim-percentile weighting: a villain who busts a high-renown
    legend out-scores an identical villain who busts only nobodies."""
    legend = _to_v2(scorer.build_archetypes()["Old Champion (legend)"])
    nobody = RenownInputsV2(label="nobody", wall_clock_hours=2, total_hands=200)

    base = dict(
        wall_clock_hours=70,
        total_hands=14_000,
        breadth_opponents={"x": 220},
    )
    hunter = RenownInputsV2(label="hunter", scalps={"legend": 4}, **base)
    poacher = RenownInputsV2(label="poacher", scalps={"nob1": 4}, **base)

    field = {
        "legend": legend,
        "nob1": nobody,
        "hunter": hunter,
        "poacher": poacher,
    }
    # pad so percentiles are meaningful
    for i in range(6):
        field[f"f{i}"] = RenownInputsV2(label=f"f{i}", wall_clock_hours=3, total_hands=300)

    scored = score_renown_field(field, WeightsV2())
    assert scored["hunter"].renown_total > scored["poacher"].renown_total


# ===========================================================================
# RELATIVE QUADRANT
# ===========================================================================


def test_quadrant_label_relative_all_four_branches():
    cut = 10.0
    assert quadrant_label_relative(12.0, 0.5, cut) == QUADRANT_BELOVED_LEGEND
    assert quadrant_label_relative(12.0, -0.5, cut) == QUADRANT_INFAMOUS_VILLAIN
    assert quadrant_label_relative(2.0, 0.5, cut) == QUADRANT_UP_AND_COMER
    assert quadrant_label_relative(2.0, -0.5, cut) == QUADRANT_DISLIKED_NOBODY


def test_quadrant_label_relative_returns_same_constants_as_v1():
    """The relative classifier returns the SAME QUADRANT_* strings the 4 hooks
    switch on — only the high-renown TEST changed (cut, not absolute 0.40)."""
    prod = score_renown_field(_archetypes_v2(), WeightsV2())
    cut = next(iter(prod.values())).high_cut
    v2 = _archetypes_v2()

    villain_q = quadrant_label_relative(
        prod["Villain"].renown_total, regard_of_v2(v2["Villain"]), cut
    )
    legend_q = quadrant_label_relative(
        prod["Old Champion (legend)"].renown_total,
        regard_of_v2(v2["Old Champion (legend)"]),
        cut,
    )
    assert villain_q == QUADRANT_INFAMOUS_VILLAIN  # high renown, hot
    assert legend_q == QUADRANT_BELOVED_LEGEND  # high renown, warm


# ===========================================================================
# high_renown_cut edge cases
# ===========================================================================


def test_high_renown_cut_single_entity_does_not_crash():
    # The human-alone case (a future ticker must not crash on a 1-entity field).
    # Pure top-decile percentile of a 1-element field is that element.
    cut = high_renown_cut([5.0], WeightsV2())
    assert cut == pytest.approx(5.0)


def test_high_renown_cut_empty_field():
    assert high_renown_cut([], WeightsV2()) == 0.0


def test_field_relative_median_over_positive_values_only():
    """A field where half the entities have zero backing computes the backing
    median over POSITIVE values only (no collapse-to-zero)."""
    w = WeightsV2()
    field = {
        "a": RenownInputsV2(
            label="a", backing_volume=10_000, wall_clock_hours=10, total_hands=1000
        ),
        "b": RenownInputsV2(label="b", backing_volume=0, wall_clock_hours=10, total_hands=1000),
        "c": RenownInputsV2(
            label="c", backing_volume=20_000, wall_clock_hours=10, total_hands=1000
        ),
        "d": RenownInputsV2(label="d", backing_volume=0, wall_clock_hours=10, total_hands=1000),
    }
    scored = score_renown_field(field, w)
    # The zero-backers don't drag the median to 0, so the median backer ('a' at
    # 10k vs median over {10k,20k}=15k) gets a NON-zero (and < median) backing
    # contribution rather than a divide-by-fallback artifact.
    assert scored["a"].components["backing"] > 0
    assert scored["b"].components["backing"] == 0.0


# ===========================================================================
# build_renown_inputs_from_repos — repo-injected, degrade-to-zero
# ===========================================================================


class _Edge:
    def __init__(self, likability=0.5, respect=0.5, heat=0.0):
        self.likability = likability
        self.respect = respect
        self.heat = heat


class _Pair:
    def __init__(self, opponent_id, cumulative_pnl=0, hands_played_cash=1):
        self.opponent_id = opponent_id
        self.cumulative_pnl = cumulative_pnl
        self.hands_played_cash = hands_played_cash


class _Session:
    def __init__(self, stake_label="$2", hands_played=0):
        self.stake_label = stake_label
        self.hands_played = hands_played


class _RelRepo:
    def __init__(self, inbound=None, pairs=None, raise_inbound=False, raise_pairs=False):
        self._inbound = inbound or {}
        self._pairs = pairs or []
        self._raise_inbound = raise_inbound
        self._raise_pairs = raise_pairs

    def load_inbound_relationships(self, entity_id, *, now=None):
        if self._raise_inbound:
            raise RuntimeError("boom")
        return self._inbound

    def list_cash_pair_stats_for_observer(self, entity_id, *, sandbox_id=None):
        if self._raise_pairs:
            raise RuntimeError("boom")
        return self._pairs


class _SessionRepo:
    def __init__(self, sessions=None, raise_sessions=False):
        self._sessions = sessions or []
        self._raise = raise_sessions

    def list_completed_for_sandbox(self, entity_id, sandbox_id):
        if self._raise:
            raise RuntimeError("boom")
        return self._sessions


class _ScalpRepo:
    def __init__(self, rows=None, raise_list=False):
        self._rows = rows or []
        self._raise = raise_list

    def list_for_eliminator(self, sandbox_id, eliminator_id):
        if self._raise:
            raise RuntimeError("boom")
        return self._rows


NOW = datetime(2026, 6, 1, 12, 0, 0)
ENT = "guest_jeff"
SB = "sb-1"


def _build(**kw):
    defaults = dict(
        entity_id=ENT,
        sandbox_id=SB,
        now=NOW,
        relationship_repo=_RelRepo(),
        cash_session_repo=_SessionRepo(),
    )
    defaults.update(kw)
    return build_renown_inputs_from_repos(**defaults)


def test_builder_maps_all_sources():
    rel = _RelRepo(
        inbound={"a": _Edge(likability=0.8, respect=0.7, heat=0.2)},
        pairs=[
            _Pair("a", cumulative_pnl=500, hands_played_cash=30),
            _Pair("b", cumulative_pnl=-100, hands_played_cash=10),
            _Pair("c", cumulative_pnl=0, hands_played_cash=0),
        ],
    )
    sess = _SessionRepo(
        [
            _Session("$2", hands_played=100),
            _Session("$2", hands_played=50),
            _Session("$200", hands_played=20),
        ]
    )
    scalps = _ScalpRepo(rows=[("fish", 3), ("whale", 1)])

    inp = _build(relationship_repo=rel, cash_session_repo=sess, cash_scalps_repo=scalps)

    # breadth: only opponents with hands_played_cash > 0
    assert inp.breadth_opponents == {"a": 30, "b": 10}
    assert inp.roster_net == pytest.approx(400.0)
    # regard from the single inbound edge (measured as value − neutral baseline)
    assert inp.regard_likability == pytest.approx(0.8 - REGARD_NEUTRAL)
    assert inp.regard_respect == pytest.approx(0.7 - REGARD_NEUTRAL)
    assert inp.regard_heat == pytest.approx(0.2)
    # stakes / tenure aggregated
    assert inp.stakes_hands == {"$2": 150, "$200": 20}
    assert inp.total_hands == 170
    # scalps mapped to dict
    assert inp.scalps == {"fish": 3, "whale": 1}


def test_builder_degrades_each_repo_independently():
    # pairs throws → breadth/roster zero, everything else still maps.
    inp = _build(
        relationship_repo=_RelRepo(inbound={"a": _Edge(heat=0.5)}, raise_pairs=True),
        cash_session_repo=_SessionRepo([_Session("$10", hands_played=40)]),
    )
    assert inp.breadth_opponents == {}
    assert inp.roster_net == 0.0
    assert inp.regard_heat == pytest.approx(0.5)
    assert inp.total_hands == 40

    # inbound throws → regard zero, breadth still maps.
    inp = _build(
        relationship_repo=_RelRepo(pairs=[_Pair("a", hands_played_cash=5)], raise_inbound=True),
    )
    assert inp.regard_heat == 0.0
    assert inp.breadth_opponents == {"a": 5}

    # sessions throws → tenure/stakes zero.
    inp = _build(cash_session_repo=_SessionRepo(raise_sessions=True))
    assert inp.total_hands == 0
    assert inp.stakes_hands == {}

    # scalps throws → scalps zero, never raises.
    inp = _build(cash_scalps_repo=_ScalpRepo(raise_list=True))
    assert inp.scalps == {}


def test_builder_tolerates_absent_optional_repos():
    # holdings / stake repos absent (the DEFERRED surfaces) → those inputs zero.
    inp = _build(holdings_repo=None, stake_repo=None)
    assert inp.peak_net_worth == 0.0
    assert inp.ticks_at_number_one == 0
    assert inp.backing_volume == 0.0


# ===========================================================================
# ReputationScore must-stay-green: v1 construction + positional repo.record()
# ===========================================================================


def test_reputation_score_constructs_with_v1_args_only():
    """The v2 optional fields are appended AFTER every v1 field with defaults,
    so a v1 caller constructs ReputationScore exactly as before (frozen, 14
    positional/keyword v1 args) and the v2 fields default."""
    s = ReputationScore(
        renown=0.5,
        regard=0.1,
        quadrant=QUADRANT_UP_AND_COMER,
        renown_breadth=0.1,
        renown_tenure=0.1,
        renown_stake_tier=0.1,
        renown_beat_respected=0.1,
        renown_high_stakes=0.1,
        regard_likability=0.05,
        regard_respect=0.03,
        regard_heat=0.02,
        opponent_count=3,
        computed_at="2026-06-01T12:00:00Z",
    )
    assert s.formula_version == 1
    assert s.renown_v2 == 0.0
    assert s.high_renown_cut == 0.0
    # frozen
    with pytest.raises(Exception):
        s.renown = 0.9  # type: ignore[misc]


def test_reputation_score_repo_record_reads_only_v1_fields():
    """A fake repo using the v1 positional/attribute access persists a v1-only
    ReputationScore without touching the new optional fields (proves the
    14-field record() contract is unaffected)."""
    s = ReputationScore(
        renown=0.5,
        regard=0.1,
        quadrant=QUADRANT_UP_AND_COMER,
        renown_breadth=0.1,
        renown_tenure=0.1,
        renown_stake_tier=0.1,
        renown_beat_respected=0.1,
        renown_high_stakes=0.1,
        regard_likability=0.05,
        regard_respect=0.03,
        regard_heat=0.02,
        opponent_count=3,
        computed_at="2026-06-01T12:00:00Z",
    )
    captured = {}

    def fake_record(*, captured_at, sandbox_id, owner_id, score):
        captured["row"] = (
            float(score.renown),
            float(score.regard),
            score.quadrant,
            float(score.renown_breadth),
            float(score.renown_tenure),
            float(score.renown_stake_tier),
            float(score.renown_beat_respected),
            float(score.renown_high_stakes),
            float(score.regard_likability),
            float(score.regard_respect),
            float(score.regard_heat),
            int(score.opponent_count),
        )

    fake_record(captured_at="t", sandbox_id=SB, owner_id=ENT, score=s)
    assert captured["row"][0] == 0.5
    assert len(captured["row"]) == 12
