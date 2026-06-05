"""Tests for the tournament buy-in / escrow-in flow (P2 step 3).

Exercises `tournament_economy_service.plan_funding` + `apply_buy_in` against real
repos on a temp DB: regime-driven plans, the human debit + escrow earmark, the
402 affordability gate, freeroll no-op, and rollback on a hard failure.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cash_mode.bankroll import PlayerBankrollState
from core.economy.ledger import player, tournament
from flask_app.services import tournament_economy_service as econ
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_session_repository import TournamentSessionRepository

SB = 'sb-buyin'
OWNER = 'alice'
TID = 'tourney_buyin'


@pytest.fixture
def repos():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "econ.db")
        SchemaManager(path).ensure_schema()
        ledger_repo = ChipLedgerRepository(path)
        bankroll_repo = BankrollRepository(path)
        session_repo = TournamentSessionRepository(path)
        # A registered (but pre-economy) tournament row to stamp economy onto.
        session_repo.save(
            tournament_id=TID,
            owner_id=OWNER,
            status='active',
            resolver_kind='fake',
            session_json='{}',
            created_at='2026-06-01T00:00:00',
        )
        yield ledger_repo, bankroll_repo, session_repo
        ledger_repo.close()
        bankroll_repo.close()
        session_repo.close()


def _seed_player(bankroll_repo, chips: int):
    bankroll_repo.save_player_bankroll(
        PlayerBankrollState(player_id=OWNER, chips=chips, starting_bankroll=10_000)
    )


def _flush_ledger(ledger_repo):
    """Push the sandbox into a FLUSH regime (reserves high vs holdings)."""
    ledger_repo.record('central_bank', 'player:univ', 100_000, 'player_seed', sandbox_id=SB)
    ledger_repo.record('ai:rich', 'central_bank', 30_000, 'bank_pool_deposit', sandbox_id=SB)


class TestPlanFunding:
    def test_neutral_cold_sandbox(self, repos):
        ledger_repo, _, _ = repos
        plan = econ.plan_funding(
            ledger_repo=ledger_repo, sandbox_id=SB, field_size=9, buy_in=500, human_in=True
        )
        assert plan.regime == 'neutral'
        assert plan.human_buy_in == 500
        assert plan.bank_overlay == 0
        assert plan.prize_pool == 500

    def test_flush_adds_overlay(self, repos):
        ledger_repo, _, _ = repos
        _flush_ledger(ledger_repo)
        plan = econ.plan_funding(
            ledger_repo=ledger_repo, sandbox_id=SB, field_size=9, buy_in=500, human_in=True
        )
        assert plan.regime == 'flush'
        assert plan.bank_overlay > 0
        assert plan.prize_pool == 500 + plan.bank_overlay


class TestApplyBuyIn:
    def test_debits_and_escrows(self, repos):
        ledger_repo, bankroll_repo, session_repo = repos
        _seed_player(bankroll_repo, 5_000)
        plan = econ.plan_funding(
            ledger_repo=ledger_repo, sandbox_id=SB, field_size=9, buy_in=500, human_in=True
        )
        econ.apply_buy_in(
            tournament_id=TID,
            owner_id=OWNER,
            sandbox_id=SB,
            plan=plan,
            bankroll_repo=bankroll_repo,
            ledger_repo=ledger_repo,
            session_repo=session_repo,
        )
        assert bankroll_repo.load_player_bankroll(OWNER).chips == 4_500
        assert ledger_repo.balance_of(tournament(TID), sandbox_id=SB) == 500
        assert ledger_repo.balance_of(player(OWNER), sandbox_id=SB) == -500
        row = session_repo.load(TID)
        assert row['buy_in'] == 500
        assert row['prize_pool'] == 500
        assert row['payout_status'] == 'pending'

    def test_flush_overlay_lands_in_escrow(self, repos):
        ledger_repo, bankroll_repo, session_repo = repos
        _seed_player(bankroll_repo, 5_000)
        _flush_ledger(ledger_repo)
        plan = econ.plan_funding(
            ledger_repo=ledger_repo, sandbox_id=SB, field_size=9, buy_in=500, human_in=True
        )
        econ.apply_buy_in(
            tournament_id=TID,
            owner_id=OWNER,
            sandbox_id=SB,
            plan=plan,
            bankroll_repo=bankroll_repo,
            ledger_repo=ledger_repo,
            session_repo=session_repo,
        )
        # Escrow == buy-in + overlay.
        assert ledger_repo.balance_of(tournament(TID), sandbox_id=SB) == 500 + plan.bank_overlay
        assert session_repo.load(TID)['bank_overlay'] == plan.bank_overlay

    def test_insufficient_funds_raises_no_debit(self, repos):
        ledger_repo, bankroll_repo, session_repo = repos
        _seed_player(bankroll_repo, 200)
        plan = econ.plan_funding(
            ledger_repo=ledger_repo, sandbox_id=SB, field_size=9, buy_in=500, human_in=True
        )
        with pytest.raises(econ.InsufficientFundsError) as exc:
            econ.apply_buy_in(
                tournament_id=TID,
                owner_id=OWNER,
                sandbox_id=SB,
                plan=plan,
                bankroll_repo=bankroll_repo,
                ledger_repo=ledger_repo,
                session_repo=session_repo,
            )
        assert exc.value.required == 500
        assert exc.value.available == 200
        # No debit, no escrow, status untouched.
        assert bankroll_repo.load_player_bankroll(OWNER).chips == 200
        assert ledger_repo.balance_of(tournament(TID), sandbox_id=SB) == 0
        assert session_repo.load(TID)['payout_status'] == 'skipped'

    def test_freeroll_no_debit(self, repos):
        ledger_repo, bankroll_repo, session_repo = repos
        _seed_player(bankroll_repo, 5_000)
        plan = econ.plan_funding(
            ledger_repo=ledger_repo, sandbox_id=SB, field_size=9, buy_in=0, human_in=True
        )
        econ.apply_buy_in(
            tournament_id=TID,
            owner_id=OWNER,
            sandbox_id=SB,
            plan=plan,
            bankroll_repo=bankroll_repo,
            ledger_repo=ledger_repo,
            session_repo=session_repo,
        )
        assert bankroll_repo.load_player_bankroll(OWNER).chips == 5_000
        assert ledger_repo.balance_of(tournament(TID), sandbox_id=SB) == 0
        assert session_repo.load(TID)['payout_status'] == 'skipped'

    def test_rollback_recredits_on_set_economy_failure(self, repos):
        ledger_repo, bankroll_repo, _ = repos
        _seed_player(bankroll_repo, 5_000)
        plan = econ.plan_funding(
            ledger_repo=ledger_repo, sandbox_id=SB, field_size=9, buy_in=500, human_in=True
        )

        class BoomRepo:
            def set_economy(self, *a, **k):
                raise RuntimeError("db down")

        with pytest.raises(RuntimeError):
            econ.apply_buy_in(
                tournament_id=TID,
                owner_id=OWNER,
                sandbox_id=SB,
                plan=plan,
                bankroll_repo=bankroll_repo,
                ledger_repo=ledger_repo,
                session_repo=BoomRepo(),
            )
        # Human re-credited; no orphan escrow row (ledger writes come AFTER set_economy).
        assert bankroll_repo.load_player_bankroll(OWNER).chips == 5_000
        assert ledger_repo.balance_of(tournament(TID), sandbox_id=SB) == 0

    def test_rollback_is_delta_based_preserving_concurrent_change(self, repos):
        """The rollback reverses only OUR debit against the CURRENT bankroll, so a
        concurrent (cross-sandbox) credit that lands between the debit and the
        rollback is preserved — not clobbered by a stale pre-debit snapshot."""
        ledger_repo, bankroll_repo, _ = repos
        _seed_player(bankroll_repo, 5_000)
        plan = econ.plan_funding(
            ledger_repo=ledger_repo, sandbox_id=SB, field_size=9, buy_in=500, human_in=True
        )

        class RacingRepo:
            """set_economy simulates a concurrent +1000 credit, then fails."""

            def set_economy(self, *a, **k):
                cur = bankroll_repo.load_player_bankroll(OWNER)
                bankroll_repo.save_player_bankroll(
                    PlayerBankrollState(
                        player_id=OWNER,
                        chips=cur.chips + 1_000,
                        starting_bankroll=cur.starting_bankroll,
                    )
                )
                raise RuntimeError("db down")

        with pytest.raises(RuntimeError):
            econ.apply_buy_in(
                tournament_id=TID,
                owner_id=OWNER,
                sandbox_id=SB,
                plan=plan,
                bankroll_repo=bankroll_repo,
                ledger_repo=ledger_repo,
                session_repo=RacingRepo(),
            )
        # 5000 −500 (debit) +1000 (concurrent) +500 (rollback re-credit) = 6000.
        # A stale-snapshot rollback would wrongly restore 5000, losing the +1000.
        assert bankroll_repo.load_player_bankroll(OWNER).chips == 6_000
