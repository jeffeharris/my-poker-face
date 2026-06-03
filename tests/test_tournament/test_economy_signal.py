"""Tests for the economy-signal chairman (`core.economy.economy_signal`).

Covers the derived read-model (`signal`) over a real ledger across
flush/neutral/empty, and the two pure policy functions (`tournament_funding`,
`cash_rake_schedule`) including the EXP_006 ~0.08 setpoint behaviour.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.economy import economy_signal as chair
from core.economy.economy_signal import (
    EMPTY,
    FLUSH,
    NEUTRAL,
    EconomyState,
    cash_rake_schedule,
    signal,
    tournament_funding,
)
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager

SB = 'sandbox-econ'


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "ledger.db")
        SchemaManager(db_path).ensure_schema()
        r = ChipLedgerRepository(db_path)
        yield r
        r.close()


def _seed_universe(repo, *, holdings: int, sandbox_id: str = SB) -> None:
    """Put `holdings` chips into circulation (a player_seed creation)."""
    repo.record('central_bank', 'player:p', holdings, 'player_seed', sandbox_id=sandbox_id)


def _deposit_pool(repo, *, amount: int, sandbox_id: str = SB) -> None:
    """Add `amount` to the recyclable bank pool (a deposit-reason destruction)."""
    repo.record('ai:rich', 'central_bank', amount, 'bank_pool_deposit', sandbox_id=sandbox_id)


class TestSignal:
    def test_none_repo_is_neutral_zero(self):
        st = signal(None)
        assert st == EconomyState(reserves=0, holdings=0, ratio=0.0, regime=NEUTRAL)

    def test_empty_ledger_is_neutral(self, repo):
        st = signal(repo, sandbox_id=SB)
        assert st.reserves == 0
        assert st.holdings == 0
        assert st.regime == NEUTRAL

    def test_reserves_and_holdings_derived(self, repo):
        _seed_universe(repo, holdings=100_000)
        _deposit_pool(repo, amount=20_000)
        st = signal(repo, sandbox_id=SB)
        # holdings = creations(120000? no): player_seed creation 100k.
        # The pool deposit is a destruction (ai → bank), so holdings = 100k - 20k.
        assert st.holdings == 80_000
        assert st.reserves == 20_000
        assert st.ratio == pytest.approx(20_000 / 80_000)
        assert st.regime == FLUSH  # 0.25 >> 0.08

    def test_flush_neutral_empty_buckets(self, repo):
        # holdings 1_000_000; reserves tuned to land each regime.
        _seed_universe(repo, holdings=1_000_000)
        # reserves 0 → ratio 0 → EMPTY
        assert signal(repo, sandbox_id=SB).regime == EMPTY
        # add to ~0.05 → NEUTRAL (between 0.02 and 0.08)
        _deposit_pool(repo, amount=50_000)
        st = signal(repo, sandbox_id=SB)
        # holdings now 950_000, reserves 50_000 → 0.0526
        assert st.regime == NEUTRAL
        # bump reserves over the flush line
        _deposit_pool(repo, amount=60_000)
        st = signal(repo, sandbox_id=SB)
        # holdings 890_000, reserves 110_000 → 0.1236
        assert st.regime == FLUSH

    def test_sandbox_scoped(self, repo):
        _seed_universe(repo, holdings=100_000, sandbox_id='sb-a')
        _deposit_pool(repo, amount=10_000, sandbox_id='sb-a')
        # other sandbox sees nothing
        assert signal(repo, sandbox_id='sb-b').holdings == 0
        assert signal(repo, sandbox_id='sb-a').holdings == 90_000


class TestTournamentFunding:
    def test_flush_overlays_no_rake(self):
        # Drain-to-setpoint: overlay = reserves − FLUSH_SETPOINT × holdings.
        # 170k − 0.08×2M = 170k − 160k = 10k.
        st = EconomyState(reserves=170_000, holdings=2_000_000, ratio=0.085, regime=FLUSH)
        plan = tournament_funding(st, field_size=18, seat_price=500, human_in=True)
        assert plan.bank_overlay == 170_000 - round(chair.FLUSH_SETPOINT * 2_000_000)  # 10_000
        assert plan.rake == 0
        assert plan.human_buy_in == 500
        assert plan.prize_pool == 500 + plan.bank_overlay

    def test_flush_overlay_capped(self):
        # A very flush bank's drain-to-setpoint (50M − 0.08×600M < 0? no: 50M −
        # 48M = 2M) exceeds the cap → capped so one event can't empty the coffers.
        st = EconomyState(reserves=50_000_000, holdings=600_000_000, ratio=0.083, regime=FLUSH)
        plan = tournament_funding(st, field_size=9, seat_price=0, human_in=False)
        assert plan.bank_overlay == chair.OVERLAY_CAP
        assert plan.prize_pool == chair.OVERLAY_CAP

    def test_neutral_buyins_only(self):
        st = EconomyState(reserves=40_000, holdings=1_000_000, ratio=0.04, regime=NEUTRAL)
        plan = tournament_funding(st, field_size=18, seat_price=500, human_in=True)
        assert plan.bank_overlay == 0
        assert plan.rake == 0
        assert plan.prize_pool == 500

    def test_empty_rakes_no_overlay(self):
        st = EconomyState(reserves=5_000, holdings=1_000_000, ratio=0.005, regime=EMPTY)
        plan = tournament_funding(st, field_size=18, seat_price=1_000, human_in=True)
        assert plan.bank_overlay == 0
        assert plan.rake == round(1_000 * chair.REFILL_RAKE_PCT)
        assert plan.prize_pool == 1_000 - plan.rake

    def test_human_out_no_buyin(self):
        st = EconomyState(reserves=0, holdings=1_000_000, ratio=0.0, regime=NEUTRAL)
        plan = tournament_funding(st, field_size=9, seat_price=500, human_in=False)
        assert plan.human_buy_in == 0
        assert plan.prize_pool == 0

    def test_ai_only_flush_pool_is_overlay(self):
        """An AI-only (no human) flush tournament still has a real pool = overlay.
        Drain-to-setpoint: 100k − 0.08×1M = 100k − 80k = 20k."""
        st = EconomyState(reserves=100_000, holdings=1_000_000, ratio=0.1, regime=FLUSH)
        plan = tournament_funding(st, field_size=18, seat_price=500, human_in=False)
        assert plan.human_buy_in == 0
        assert plan.bank_overlay == 20_000
        assert plan.prize_pool == 20_000

    def test_setpoint_boundary_is_flush(self):
        """Exactly at the 0.08 setpoint counts as flush (>= setpoint)."""
        st = signal_like(ratio=chair.FLUSH_SETPOINT)
        assert st.regime == FLUSH

    def test_freeroll_negative_seat_clamped(self):
        st = EconomyState(reserves=0, holdings=1_000_000, ratio=0.0, regime=NEUTRAL)
        plan = tournament_funding(st, field_size=9, seat_price=-100, human_in=True)
        assert plan.seat_price == 0
        assert plan.human_buy_in == 0


class TestCashRakeSchedule:
    def test_flush_top_tier_base_rate(self):
        st = EconomyState(reserves=1, holdings=1, ratio=1.0, regime=FLUSH)
        sched = cash_rake_schedule(st)
        assert sched.stake_big_blinds == frozenset({1000})
        assert sched.rate == chair._RAKE_RATE_BASE

    def test_empty_expands_tiers_and_rate(self):
        st = EconomyState(reserves=0, holdings=1, ratio=0.0, regime=EMPTY)
        sched = cash_rake_schedule(st)
        assert 200 in sched.stake_big_blinds
        assert sched.rate == chair._RAKE_RATE_EMPTY


def signal_like(*, ratio: float) -> EconomyState:
    """Build an EconomyState with a given ratio for boundary classification."""
    return EconomyState(
        reserves=int(ratio * 1_000_000),
        holdings=1_000_000,
        ratio=ratio,
        regime=chair._classify(ratio),
    )
