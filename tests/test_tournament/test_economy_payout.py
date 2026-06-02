"""Tests for the tournament payout layer (P2 step 4).

- Pure prize math (`tournament.economy`): schedule sums to prize_pool, rounding
  → 1st, in/out of money, curve override.
- The effectful payout service: human credit, synthetic-AI sweep, the escrow
  nets to 0 (conservation), the payout_status idempotency guard blocks a second
  distribution, freeroll → skipped.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cash_mode.bankroll import PlayerBankrollState
from core.economy import ledger as L
from core.economy.ledger import player, tournament
from flask_app.services import tournament_economy_service as econ
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_session_repository import TournamentSessionRepository
from tournament.economy import (
    compute_payout_schedule,
    paid_places_for,
    payout_for_position,
)

SB = 'sb-payout'
OWNER = 'alice'
TID = 'tourney_payout'


class TestPrizeMath:
    def test_schedule_sums_exactly_to_prize_pool(self):
        for field_size in (2, 6, 9, 18, 50, 200):
            for pool in (1, 100, 999, 10_000, 2_801):
                sched = compute_payout_schedule(field_size, pool)
                assert sum(e['amount'] for e in sched) == pool

    def test_rounding_residual_to_first(self):
        # 100 across 0.38/0.24/0.15(+share) won't divide evenly; 1st absorbs it.
        sched = compute_payout_schedule(field_size=9, prize_pool=100)
        assert sum(e['amount'] for e in sched) == 100
        first = payout_for_position(1, sched)
        assert first >= payout_for_position(2, sched)

    def test_zero_pool_is_empty(self):
        assert compute_payout_schedule(18, 0) == []
        assert compute_payout_schedule(18, -5) == []

    def test_paid_places_top_30pct(self):
        assert paid_places_for(18) == 5  # round(5.4)
        assert paid_places_for(9) == 3
        assert paid_places_for(2) == 1  # round(0.6)→1, min 1

    def test_out_of_money_returns_zero(self):
        sched = compute_payout_schedule(field_size=9, prize_pool=1000)
        assert payout_for_position(99, sched) == 0

    def test_front_loaded(self):
        sched = compute_payout_schedule(field_size=18, prize_pool=100_000)
        amts = [e['amount'] for e in sched]
        assert amts == sorted(amts, reverse=True)  # monotone non-increasing

    def test_curve_override(self):
        sched = compute_payout_schedule(field_size=9, prize_pool=1000, payout_curve=(1.0,))
        assert sched == [{'finishing_position': 1, 'amount': 1000}]

    def test_malformed_curve_summing_over_one_stays_conserving(self):
        """A caller-supplied curve whose front sums to >1.0 must not produce a
        negative residual / negative 1st-place payout — normalisation keeps the
        schedule conserving with all-non-negative amounts."""
        # front sums to 1.5 across 3 entries, field 18 → paid 5 (paid > len(front)).
        sched = compute_payout_schedule(
            field_size=18, prize_pool=100_000, payout_curve=(0.9, 0.4, 0.2)
        )
        assert sum(e['amount'] for e in sched) == 100_000  # exact, no leakage
        assert all(e['amount'] >= 0 for e in sched)  # no negative payout
        # 1st place is still the largest and positive.
        assert payout_for_position(1, sched) > 0
        assert payout_for_position(1, sched) >= payout_for_position(2, sched)

    def test_tiny_pool_conserves_even_with_zero_amount_spots(self):
        """A pool smaller than paid_places floors lower spots to 0 (omitted), with
        the crumbs going to 1st — conservation still holds exactly."""
        sched = compute_payout_schedule(field_size=50, prize_pool=2)  # paid=15
        assert sum(e['amount'] for e in sched) == 2
        assert payout_for_position(1, sched) == 2


# --- Service-level (effectful) -------------------------------------------------


@pytest.fixture
def repos():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "econ.db")
        SchemaManager(path).ensure_schema()
        ledger_repo = ChipLedgerRepository(path)
        bankroll_repo = BankrollRepository(path)
        session_repo = TournamentSessionRepository(path)
        yield ledger_repo, bankroll_repo, session_repo
        ledger_repo.close()
        bankroll_repo.close()
        session_repo.close()


class FakeField:
    def __init__(self, field_size, eliminations, winner):
        self.field_size = field_size
        self.eliminations = eliminations
        self._winner = winner


class FakeElim:
    def __init__(self, player_id, finishing_position):
        self.player_id = player_id
        self.finishing_position = finishing_position


class FakeSession:
    """A completed-tournament stand-in for the payout service (it only reads
    field_size, eliminations, winner(), is_complete(), human_id)."""

    def __init__(self, *, field_size, human_id, winner, eliminations, complete=True):
        self.field = FakeField(field_size, eliminations, winner)
        self.human_id = human_id
        self._winner = winner
        self._complete = complete

    def winner(self):
        return self._winner

    def is_complete(self):
        return self._complete


def _register_with_economy(session_repo, *, buy_in, rake, overlay, prize_pool, status='pending'):
    session_repo.save(
        tournament_id=TID, owner_id=OWNER, status='active',
        resolver_kind='fake', session_json='{}', created_at='2026-06-01T00:00:00',
    )
    session_repo.set_economy(
        TID, buy_in=buy_in, rake=rake, bank_overlay=overlay,
        prize_pool=prize_pool, payout_status=status,
    )


def _fund_escrow(ledger_repo, *, buy_in_from_human, overlay):
    """Mirror escrow-in so the escrow holds buy_in + overlay before distribute."""
    if buy_in_from_human:
        L.record_tournament_buy_in(
            ledger_repo, source=player(OWNER), tournament_id=TID,
            amount=buy_in_from_human, sandbox_id=SB,
        )
    if overlay:
        L.record_tournament_overlay(
            ledger_repo, tournament_id=TID, amount=overlay, sandbox_id=SB
        )


class TestPayoutService:
    def test_human_wins_credited_and_escrow_drains(self, repos):
        ledger_repo, bankroll_repo, session_repo = repos
        # Human bought in 500; overlay 2000 → pool 2500, escrow 2500.
        _register_with_economy(session_repo, buy_in=500, rake=0, overlay=2000, prize_pool=2500)
        _fund_escrow(ledger_repo, buy_in_from_human=500, overlay=2000)
        bankroll_repo.save_player_bankroll(
            PlayerBankrollState(player_id=OWNER, chips=0, starting_bankroll=10_000)
        )
        # Human (P01) wins; rest are synthetic AI.
        session = FakeSession(
            field_size=9, human_id='P01', winner='P01',
            eliminations=[FakeElim(f'P0{i}', 9 - i + 1) for i in range(2, 10)],
        )
        ran = econ.apply_payout_on_complete(
            tournament_id=TID, session=session, human_owner_id=OWNER, sandbox_id=SB,
            bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
        )
        assert ran is True
        sched = compute_payout_schedule(9, 2500)
        first_prize = payout_for_position(1, sched)
        assert bankroll_repo.load_player_bankroll(OWNER).chips == first_prize
        # Escrow nets to 0; status complete.
        assert econ.verify_tournament_conservation(TID, ledger_repo, sandbox_id=SB)['balanced']
        assert session_repo.load(TID)['payout_status'] == 'complete'

    def test_synthetic_ai_field_sweeps_to_bank(self, repos):
        ledger_repo, bankroll_repo, session_repo = repos
        # Pure overlay pool, human not ITM (busts first).
        _register_with_economy(session_repo, buy_in=0, rake=0, overlay=3000, prize_pool=3000)
        _fund_escrow(ledger_repo, buy_in_from_human=0, overlay=3000)
        # P01 wins; P02..P09 finish 2nd..9th. Human is P09 (last → out of the
        # money, since paid_places=3 for a 9-field), so all paid seats are
        # synthetic AI.
        session = FakeSession(
            field_size=9, human_id='P09', winner='P01',
            eliminations=[FakeElim(f'P{n:02d}', n) for n in range(2, 10)],
        )
        econ.apply_payout_on_complete(
            tournament_id=TID, session=session, human_owner_id=OWNER, sandbox_id=SB,
            bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
        )
        # No human payout (P09 finished out of the money in a 9-field/3-paid).
        assert bankroll_repo.load_player_bankroll(OWNER) is None
        # Everything returned to the pool; escrow 0.
        assert ledger_repo.balance_of(tournament(TID), sandbox_id=SB) == 0
        dest = ledger_repo.sum_destructions_by_reason(sandbox_id=SB)
        assert dest.get('tournament_return') == 3000

    def test_idempotent_second_call_is_noop(self, repos):
        ledger_repo, bankroll_repo, session_repo = repos
        _register_with_economy(session_repo, buy_in=500, rake=0, overlay=0, prize_pool=500)
        _fund_escrow(ledger_repo, buy_in_from_human=500, overlay=0)
        bankroll_repo.save_player_bankroll(
            PlayerBankrollState(player_id=OWNER, chips=0, starting_bankroll=10_000)
        )
        session = FakeSession(
            field_size=9, human_id='P01', winner='P01',
            eliminations=[FakeElim(f'P0{i}', 11 - i) for i in range(2, 10)],
        )
        kw = dict(
            tournament_id=TID, session=session, human_owner_id=OWNER, sandbox_id=SB,
            bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
        )
        assert econ.apply_payout_on_complete(**kw) is True
        chips_after_first = bankroll_repo.load_player_bankroll(OWNER).chips
        # Second call must NOT pay again (status now 'complete').
        assert econ.apply_payout_on_complete(**kw) is False
        assert bankroll_repo.load_player_bankroll(OWNER).chips == chips_after_first

    def test_freeroll_zero_pool_marked_skipped(self, repos):
        ledger_repo, bankroll_repo, session_repo = repos
        _register_with_economy(session_repo, buy_in=0, rake=0, overlay=0, prize_pool=0)
        session = FakeSession(
            field_size=9, human_id='P01', winner='P01',
            eliminations=[FakeElim(f'P0{i}', 11 - i) for i in range(2, 10)],
        )
        ran = econ.apply_payout_on_complete(
            tournament_id=TID, session=session, human_owner_id=OWNER, sandbox_id=SB,
            bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
        )
        assert ran is False
        assert session_repo.load(TID)['payout_status'] == 'skipped'

    def test_rake_skimmed_and_remainder_swept(self, repos):
        ledger_repo, bankroll_repo, session_repo = repos
        # buy_in 1000, rake 50 → pool 950, escrow 1000.
        _register_with_economy(session_repo, buy_in=1000, rake=50, overlay=0, prize_pool=950)
        _fund_escrow(ledger_repo, buy_in_from_human=1000, overlay=0)
        bankroll_repo.save_player_bankroll(
            PlayerBankrollState(player_id=OWNER, chips=0, starting_bankroll=10_000)
        )
        session = FakeSession(
            field_size=9, human_id='P01', winner='P01',
            eliminations=[FakeElim(f'P0{i}', 11 - i) for i in range(2, 10)],
        )
        econ.apply_payout_on_complete(
            tournament_id=TID, session=session, human_owner_id=OWNER, sandbox_id=SB,
            bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
        )
        dest = ledger_repo.sum_destructions_by_reason(sandbox_id=SB)
        assert dest.get('table_rake') == 50  # rake skimmed to the pool
        assert ledger_repo.balance_of(tournament(TID), sandbox_id=SB) == 0

    def test_not_complete_does_not_pay(self, repos):
        ledger_repo, bankroll_repo, session_repo = repos
        _register_with_economy(session_repo, buy_in=500, rake=0, overlay=0, prize_pool=500)
        session = FakeSession(
            field_size=9, human_id='P01', winner=None, eliminations=[], complete=False,
        )
        assert econ.apply_payout_on_complete(
            tournament_id=TID, session=session, human_owner_id=OWNER, sandbox_id=SB,
            bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
        ) is False
        assert session_repo.load(TID)['payout_status'] == 'pending'
