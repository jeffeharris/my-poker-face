"""Tests for the pure tournament draw scorer (tournaments-as-a-draw, Phase B1)."""

from __future__ import annotations

import random
from types import SimpleNamespace

from flask_app.services import tournament_draw
from flask_app.services.tournament_draw import (
    DEFAULT_WEIGHTS,
    DrawContext,
    DrawInputs,
    DrawWeights,
    build_draw_inputs,
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


# --- The effectful builder (Phase B3) --------------------------------------


class FakeBankrollRepo:
    def __init__(self, chips):
        self._chips = chips

    def load_ai_bankroll_current(self, pid, *, sandbox_id):
        return self._chips.get(pid)


class FakePrestigeRepo:
    def __init__(self, peaks):
        self._peaks = peaks

    def load_renown_v2_peaks(self, sandbox_id, entity_kind="ai"):
        return dict(self._peaks)


class FakePersonalityRepo:
    def __init__(self, ids, egos=None):
        self._ids = ids
        self._egos = egos or {}

    def list_eligible_for_cash_mode(self, *, user_id=None):
        return [{'personality_id': p, 'name': p.title()} for p in self._ids]

    def load_ego_by_ids(self, ids):
        return {p: self._egos[p] for p in ids if p in self._egos}


class FakeCashTableRepo:
    def __init__(self, seated):
        self._seated = seated  # {pid: chips}

    def list_all_tables(self, *, sandbox_id=None):
        return [
            SimpleNamespace(
                seats=[
                    {'kind': 'ai', 'personality_id': p, 'chips': c} for p, c in self._seated.items()
                ]
            )
        ]


def _ctx(*, ids, chips=None, peaks=None, egos=None, seated=None, ledger_repo=None, weights=None):
    return DrawContext(
        personality_repo=FakePersonalityRepo(ids, egos=egos),
        bankroll_repo=FakeBankrollRepo(chips or {}),
        prestige_repo=FakePrestigeRepo(peaks or {}),
        cash_table_repo=FakeCashTableRepo(seated or {}),
        ledger_repo=ledger_repo,
        weights=weights or DEFAULT_WEIGHTS,
    )


class TestBuildDrawInputs:
    def test_one_input_per_eligible_persona(self):
        ctx = _ctx(ids=['a', 'b', 'c'])
        out = build_draw_inputs(ctx, sandbox_id='sb', owner_id='o', field_size=2)
        assert {i.personality_id for i in out} == {'a', 'b', 'c'}

    def test_empty_pool_is_empty(self):
        ctx = _ctx(ids=[])
        assert build_draw_inputs(ctx, sandbox_id='sb', owner_id='o', field_size=2) == []

    def test_bankroll_and_ego_mapped(self):
        ctx = _ctx(ids=['a', 'b'], chips={'a': 1_000, 'b': 50_000}, egos={'a': 0.9})
        by = {
            i.personality_id: i
            for i in build_draw_inputs(ctx, sandbox_id='sb', owner_id='o', field_size=2)
        }
        assert by['a'].own_bankroll == 1_000
        assert by['b'].own_bankroll == 50_000
        assert by['a'].status_appetite == 0.9
        assert by['b'].status_appetite == 0.5  # neutral default when no ego row

    def test_cash_comfort_is_seat_stack_depth(self):
        # seated with 5k against a 10k baseline → comfort 0.5; unseated → 0.
        ctx = _ctx(ids=['seated', 'idle'], seated={'seated': 5_000})
        by = {
            i.personality_id: i
            for i in build_draw_inputs(
                ctx, sandbox_id='sb', owner_id='o', field_size=2, starting_stack=10_000
            )
        }
        assert by['seated'].cash_comfort == 0.5
        assert by['idle'].cash_comfort == 0.0

    def test_renown_off_zeroes_renown_terms(self, monkeypatch):
        from cash_mode import economy_flags

        monkeypatch.setattr(economy_flags, 'RENOWN_V2_PERSIST_AI', False)
        ctx = _ctx(ids=['a'], peaks={'a': 9.0})
        out = build_draw_inputs(ctx, sandbox_id='sb', owner_id='o', field_size=1)
        assert out[0].own_renown == 0.0
        assert out[0].field_top_renown == 0.0
        assert out[0].renown_on_offer == 0.0

    def test_renown_on_normalizes_field_relative(self, monkeypatch):
        from cash_mode import economy_flags

        monkeypatch.setattr(economy_flags, 'RENOWN_V2_PERSIST_AI', True)
        # peaks are uncapped points; the top persona normalizes to 1.0.
        ctx = _ctx(ids=['big', 'small'], peaks={'big': 10.0, 'small': 2.0})
        by = {
            i.personality_id: i
            for i in build_draw_inputs(ctx, sandbox_id='sb', owner_id='o', field_size=2)
        }
        assert by['big'].own_renown == 1.0
        assert by['small'].own_renown == 0.2
        assert by['big'].field_top_renown == 1.0
        assert by['big'].renown_on_offer == tournament_draw.DEFAULT_RENOWN_ON_OFFER

    def test_prize_pool_read_via_plan_funding(self, monkeypatch):
        from flask_app.services import tournament_economy_service as econ

        monkeypatch.setattr(econ, 'plan_funding', lambda **kw: SimpleNamespace(prize_pool=42_000))
        # ledger_repo just needs to be non-None for the prize read to fire.
        ctx = _ctx(ids=['a'], chips={'a': 1_000}, ledger_repo=object())
        out = build_draw_inputs(ctx, sandbox_id='sb', owner_id='o', field_size=1)
        assert out[0].prize_pool == 42_000

    def test_repo_failure_degrades_term_not_candidate(self):
        # A bankroll repo that throws must not drop the candidate — bankroll → 0.
        class BoomBankroll:
            def load_ai_bankroll_current(self, pid, *, sandbox_id):
                raise RuntimeError("boom")

        ctx = DrawContext(
            personality_repo=FakePersonalityRepo(['a']),
            bankroll_repo=BoomBankroll(),
            prestige_repo=FakePrestigeRepo({}),
            cash_table_repo=FakeCashTableRepo({}),
            ledger_repo=None,
        )
        out = build_draw_inputs(ctx, sandbox_id='sb', owner_id='o', field_size=1)
        assert len(out) == 1 and out[0].own_bankroll == 0
