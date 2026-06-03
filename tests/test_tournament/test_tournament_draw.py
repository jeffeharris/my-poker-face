"""Tests for the pure tournament draw scorer (tournaments-as-a-draw, Phase B1)."""

from __future__ import annotations

import random

from flask_app.services.tournament_draw import (
    DEFAULT_WEIGHTS,
    DrawInputs,
    DrawWeights,
    rank_field,
    score_draw,
)


def _inp(pid="p", **kw) -> DrawInputs:
    base = dict(
        personality_id=pid,
        own_bankroll=10_000,
        own_renown=0.5,
        status_appetite=0.5,
        prize_pool=10_000,
        renown_on_offer=0.5,
        field_top_renown=0.5,
        cash_comfort=0.0,
    )
    base.update(kw)
    return DrawInputs(**base)


class TestScoreDraw:
    def test_prize_appeal_pulls_small_bankroll_harder(self):
        # Same prize; the poorer persona finds it more appealing.
        poor = score_draw(_inp(own_bankroll=1_000, prize_pool=10_000))
        rich = score_draw(_inp(own_bankroll=500_000, prize_pool=10_000))
        assert poor > rich

    def test_prize_appeal_clamped(self):
        # prize >> bankroll caps the term at the weight (no unbounded blowup).
        huge = score_draw(_inp(own_bankroll=1, prize_pool=10_000_000, cash_comfort=0))
        # prize_appeal clamps to 1.0; the other terms here are modest, so the
        # score can't exceed the sum of positive weights.
        assert huge <= (DEFAULT_WEIGHTS.prize + DEFAULT_WEIGHTS.renown + DEFAULT_WEIGHTS.field)

    def test_renown_upside_favors_low_renown(self):
        # Same appetite + offer; the lower-renown persona has more to gain.
        low = score_draw(_inp(own_renown=0.1, renown_on_offer=0.8, status_appetite=1.0))
        high = score_draw(_inp(own_renown=0.9, renown_on_offer=0.8, status_appetite=1.0))
        assert low > high

    def test_status_appetite_scales_renown_term(self):
        eager = score_draw(_inp(status_appetite=1.0, renown_on_offer=0.8, own_renown=0.2))
        indifferent = score_draw(_inp(status_appetite=0.0, renown_on_offer=0.8, own_renown=0.2))
        assert eager > indifferent

    def test_field_appeal_pulls_small_fish_to_the_bigs(self):
        # A low-renown persona is pulled by high-renown peers; a big is not.
        fish = score_draw(_inp(own_renown=0.1, field_top_renown=1.0))
        big = score_draw(_inp(own_renown=0.95, field_top_renown=1.0))
        assert fish > big

    def test_cash_comfort_damps(self):
        comfy = score_draw(_inp(cash_comfort=1.0))
        restless = score_draw(_inp(cash_comfort=0.0))
        assert restless > comfy

    def test_weights_are_tunable(self):
        inp = _inp(own_bankroll=1_000, prize_pool=10_000, cash_comfort=0)
        hi = score_draw(inp, DrawWeights(prize=1.0, renown=0, field=0, cash_comfort=0))
        lo = score_draw(inp, DrawWeights(prize=0.1, renown=0, field=0, cash_comfort=0))
        assert hi > lo


class TestRankField:
    def test_takes_top_n_by_score(self):
        # Three candidates differing only in bankroll (poorer = higher draw).
        cands = [
            _inp("rich", own_bankroll=500_000, prize_pool=10_000),
            _inp("mid", own_bankroll=20_000, prize_pool=10_000),
            _inp("poor", own_bankroll=1_000, prize_pool=10_000),
        ]
        # Deterministic (no rng): poorest two are drawn.
        assert rank_field(cands, field_size=2, rng=None) == ["poor", "mid"]

    def test_field_size_caps_and_zero_is_empty(self):
        cands = [_inp("a"), _inp("b")]
        assert rank_field(cands, field_size=10, rng=None) == ["a", "b"] or set(
            rank_field(cands, field_size=10, rng=None)
        ) == {"a", "b"}
        assert rank_field(cands, field_size=0, rng=None) == []
        assert rank_field([], field_size=3, rng=None) == []

    def test_deterministic_without_rng(self):
        cands = [_inp(f"p{i}", own_bankroll=1000 * (i + 1)) for i in range(6)]
        a = rank_field(cands, field_size=3, rng=None)
        b = rank_field(cands, field_size=3, rng=None)
        assert a == b

    def test_jitter_can_reorder_clustered_scores(self):
        # Identical inputs (tied scores) → with noise, the chosen subset can vary
        # across seeds; without noise it's the stable id tie-break.
        cands = [_inp(f"p{i}") for i in range(10)]
        no_noise = rank_field(cands, field_size=3, rng=None)
        assert no_noise == ["p0", "p1", "p2"]  # stable id order on exact ties
        seen = set()
        for seed in range(40):
            seen.add(
                tuple(rank_field(cands, field_size=3, rng=random.Random(seed), noise_sigma=0.1))
            )
        # Jitter should produce more than one distinct top-3 across seeds.
        assert len(seen) > 1
