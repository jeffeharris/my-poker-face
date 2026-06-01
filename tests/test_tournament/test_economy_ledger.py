"""Tests for the tournament economy ledger vocabulary + escrow account (P2 step 2).

Covers the `tournament(id)` escrow account, the buy-in/payout transfer helpers,
the overlay pool-draw helper, the escrow-balance invariant (buy-ins + overlays in
→ payouts + rake out → 0), and the v132 economy-column migration.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.economy import ledger as L
from core.economy.ledger import (
    BANK_POOL_DRAW_REASONS,
    LEDGER_REASONS,
    TRANSFER_REASONS,
    ai,
    bank,
    player,
    tournament,
)
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_session_repository import TournamentSessionRepository

SB = 'sb-tourney'
TID = 'tourney_abc'


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "econ.db")
        SchemaManager(path).ensure_schema()
        yield path


@pytest.fixture
def repo(db_path):
    r = ChipLedgerRepository(db_path)
    yield r
    r.close()


class TestVocabulary:
    def test_reasons_registered(self):
        for reason in ('tournament_buy_in', 'tournament_payout', 'tournament_overlay'):
            assert reason in LEDGER_REASONS
        assert 'tournament_buy_in' in TRANSFER_REASONS
        assert 'tournament_payout' in TRANSFER_REASONS
        assert 'tournament_overlay' not in TRANSFER_REASONS  # a real bank draw
        assert 'tournament_overlay' in BANK_POOL_DRAW_REASONS

    def test_tournament_account_format(self):
        assert tournament('t1') == 'tournament:t1'
        with pytest.raises(ValueError):
            tournament('')


class TestEscrowFlow:
    def test_buy_in_is_a_transfer_into_escrow(self, repo):
        L.record_tournament_buy_in(
            repo, source=player('alice'), tournament_id=TID, amount=500, sandbox_id=SB
        )
        assert repo.balance_of(tournament(TID), sandbox_id=SB) == 500
        assert repo.balance_of(player('alice'), sandbox_id=SB) == -500
        # Drift-invisible: a transfer has no central_bank side.
        assert repo.sum_creations_by_reason(sandbox_id=SB).get('tournament_buy_in') is None

    def test_overlay_is_a_pool_draw(self, repo):
        L.record_tournament_overlay(repo, tournament_id=TID, amount=2_000, sandbox_id=SB)
        assert repo.balance_of(tournament(TID), sandbox_id=SB) == 2_000
        creations = repo.sum_creations_by_reason(sandbox_id=SB)
        assert creations.get('tournament_overlay') == 2_000  # counts in drift

    def test_payout_drains_escrow(self, repo):
        L.record_tournament_buy_in(
            repo, source=player('alice'), tournament_id=TID, amount=500, sandbox_id=SB
        )
        L.record_tournament_payout(
            repo, sink=player('alice'), tournament_id=TID, amount=500, sandbox_id=SB
        )
        assert repo.balance_of(tournament(TID), sandbox_id=SB) == 0

    def test_escrow_balance_invariant_full_event(self, repo):
        """tournament:<id> == Σ buy_ins + Σ overlays, then == Σ payouts + rake → 0."""
        # Escrow-in: human buy-in + AI buy-in + bank overlay.
        L.record_tournament_buy_in(
            repo, source=player('alice'), tournament_id=TID, amount=500, sandbox_id=SB
        )
        L.record_tournament_buy_in(
            repo, source=ai('villain'), tournament_id=TID, amount=300, sandbox_id=SB
        )
        L.record_tournament_overlay(repo, tournament_id=TID, amount=2_000, sandbox_id=SB)
        assert repo.balance_of(tournament(TID), sandbox_id=SB) == 2_800  # = 500+300+2000

        # Distribute: payouts + a rake skim (escrow → bank, reuse table_rake).
        L.record_tournament_payout(
            repo, sink=player('alice'), tournament_id=TID, amount=1_800, sandbox_id=SB
        )
        L.record_tournament_payout(
            repo, sink=ai('villain'), tournament_id=TID, amount=900, sandbox_id=SB
        )
        L.record_table_rake(
            repo, source=tournament(TID), amount=100, sandbox_id=SB,
            context={'tournament_id': TID},
        )
        assert repo.balance_of(tournament(TID), sandbox_id=SB) == 0

    def test_context_stamps_tournament_id(self, repo):
        L.record_tournament_buy_in(
            repo, source=player('alice'), tournament_id=TID, amount=500, sandbox_id=SB
        )
        entry = repo.recent_entries(limit=1, sandbox_id=SB)[0]
        assert entry['context']['tournament_id'] == TID

    def test_zero_and_none_are_noops(self, repo):
        assert L.record_tournament_buy_in(
            repo, source=player('a'), tournament_id=TID, amount=0, sandbox_id=SB
        ) is None
        assert L.record_tournament_overlay(
            None, tournament_id=TID, amount=100, sandbox_id=SB
        ) is None
        assert repo.balance_of(tournament(TID), sandbox_id=SB) == 0


class TestV132Migration:
    def test_columns_present_with_defaults(self, db_path):
        srepo = TournamentSessionRepository(db_path)
        srepo.save(
            tournament_id=TID,
            owner_id='alice',
            status='active',
            resolver_kind='fake',
            session_json='{}',
            created_at='2026-06-01T00:00:00',
        )
        row = srepo.load(TID)
        assert row['buy_in'] == 0
        assert row['rake'] == 0
        assert row['bank_overlay'] == 0
        assert row['prize_pool'] == 0
        assert row['payout_status'] == 'skipped'
        srepo.close()

    def test_set_economy_then_session_persist_preserves_it(self, db_path):
        """A routine session persist (save) must NOT wipe economy columns."""
        srepo = TournamentSessionRepository(db_path)
        srepo.save(
            tournament_id=TID, owner_id='alice', status='active',
            resolver_kind='fake', session_json='{}', created_at='2026-06-01T00:00:00',
        )
        srepo.set_economy(
            TID, buy_in=500, rake=0, bank_overlay=2_000, prize_pool=2_500,
            payout_status='pending',
        )
        # Simulate a hand-boundary persist with new session_json.
        srepo.save(
            tournament_id=TID, owner_id='alice', status='active',
            resolver_kind='fake', session_json='{"rounds": 5}',
            created_at='2026-06-01T00:00:00', game_id='g1',
        )
        row = srepo.load(TID)
        assert row['buy_in'] == 500
        assert row['bank_overlay'] == 2_000
        assert row['prize_pool'] == 2_500
        assert row['payout_status'] == 'pending'  # preserved across save()
        assert row['session_json'] == '{"rounds": 5}'
        srepo.close()

    def test_set_payout_status(self, db_path):
        srepo = TournamentSessionRepository(db_path)
        srepo.save(
            tournament_id=TID, owner_id='alice', status='active',
            resolver_kind='fake', session_json='{}', created_at='2026-06-01T00:00:00',
        )
        srepo.set_payout_status(TID, 'in_progress')
        assert srepo.load(TID)['payout_status'] == 'in_progress'
        srepo.set_payout_status(TID, 'complete')
        assert srepo.load(TID)['payout_status'] == 'complete'
        srepo.close()
