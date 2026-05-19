"""Tests for `ChipLedgerRepository` and `core.economy.ledger`.

Repo: record + sum_creations_by_reason + sum_destructions_by_reason +
recent_entries round-trip correctly, including JSON context blobs.

Module wrapper: vocabulary enforcement, sign validation, central-bank
side requirement, and graceful failure.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.economy import ledger as ledger_mod
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "ledger.db")
        SchemaManager(db_path).ensure_schema()
        r = ChipLedgerRepository(db_path)
        yield r
        r.close()


class TestRepoRoundTrip:
    def test_record_and_recent(self, repo):
        repo.record('central_bank', 'player:alice', 200, 'player_seed',
                    context={'game_id': 'cash-1'})
        repo.record('ai:bob', 'central_bank', 50, 'cap_clamp',
                    context={'cap': 10000})

        entries = repo.recent_entries(limit=10)
        assert len(entries) == 2
        # Newest first; cap_clamp was second insert.
        assert entries[0]['reason'] == 'cap_clamp'
        assert entries[0]['amount'] == 50
        assert entries[0]['context'] == {'cap': 10000}
        assert entries[1]['reason'] == 'player_seed'
        assert entries[1]['context'] == {'game_id': 'cash-1'}

    def test_sum_creations_groups_by_reason(self, repo):
        repo.record('central_bank', 'player:a', 100, 'player_seed')
        repo.record('central_bank', 'player:b', 200, 'player_seed')
        repo.record('central_bank', 'ai:c', 75, 'ai_regen')
        repo.record('ai:c', 'central_bank', 30, 'cap_clamp')  # destruction; excluded

        sums = repo.sum_creations_by_reason()
        assert sums == {'player_seed': 300, 'ai_regen': 75}

    def test_sum_destructions_groups_by_reason(self, repo):
        repo.record('central_bank', 'player:a', 100, 'player_seed')  # creation
        repo.record('ai:c', 'central_bank', 40, 'cap_clamp')
        repo.record('player:a', 'central_bank', 60, 'house_loan_settle')

        sums = repo.sum_destructions_by_reason()
        assert sums == {'cap_clamp': 40, 'house_loan_settle': 60}

    def test_zero_amount_round_trips(self, repo):
        """Annotation rows (forgive_balance, amount=0) survive the schema's
        CHECK constraint and come back through recent_entries."""
        repo.record('player:a', 'central_bank', 0, 'forgive_balance',
                    context={'forgiven_principal': 500})
        entries = repo.recent_entries()
        assert entries[0]['amount'] == 0
        assert entries[0]['context'] == {'forgiven_principal': 500}

    def test_malformed_context_does_not_crash_recent(self, repo):
        """Stored JSON that fails to parse round-trips as context=None
        with a context_raw fallback for forensics."""
        # Insert a malformed blob directly via the repo's connection.
        with repo._get_connection() as conn:
            conn.execute(
                "INSERT INTO chip_ledger_entries "
                "(source, sink, amount, reason, context_json) "
                "VALUES (?, ?, ?, ?, ?)",
                ('central_bank', 'player:x', 1, 'player_seed', '{not json'),
            )
        entries = repo.recent_entries()
        assert entries[0]['context'] is None
        assert entries[0]['context_raw'] == '{not json'


class TestLedgerModule:
    def test_record_writes_via_helpers(self, repo):
        eid = ledger_mod.record(
            repo,
            source=ledger_mod.bank(),
            sink=ledger_mod.player('alice'),
            amount=100,
            reason='player_seed',
            context={'game_id': 'cash-1'},
        )
        assert eid is not None
        entries = repo.recent_entries()
        assert entries[0]['source'] == 'central_bank'
        assert entries[0]['sink'] == 'player:alice'

    def test_unknown_reason_rejected(self, repo):
        eid = ledger_mod.record(
            repo,
            source=ledger_mod.bank(),
            sink=ledger_mod.player('alice'),
            amount=100,
            reason='made_up_reason',
        )
        assert eid is None
        assert repo.recent_entries() == []

    def test_negative_amount_rejected(self, repo):
        eid = ledger_mod.record(
            repo,
            source=ledger_mod.bank(),
            sink=ledger_mod.player('a'),
            amount=-5,
            reason='player_seed',
        )
        assert eid is None
        assert repo.recent_entries() == []

    def test_non_bank_transfer_rejected(self, repo):
        """v0 doesn't track pure transfers between non-bank entities."""
        eid = ledger_mod.record(
            repo,
            source=ledger_mod.player('a'),
            sink=ledger_mod.ai('zeus'),
            amount=100,
            reason='player_seed',
        )
        assert eid is None
        assert repo.recent_entries() == []

    def test_player_helper_requires_owner_id(self):
        with pytest.raises(ValueError):
            ledger_mod.player('')

    def test_ai_helper_requires_personality_id(self):
        with pytest.raises(ValueError):
            ledger_mod.ai('')

    def test_zero_amount_accepted(self, repo):
        """Annotation entries — forgive_balance with amount=0 is a valid
        write so the audit endpoint can reconcile."""
        eid = ledger_mod.record(
            repo,
            source=ledger_mod.player('a'),
            sink=ledger_mod.bank(),
            amount=0,
            reason='forgive_balance',
            context={'forgiven_principal': 500},
        )
        assert eid is not None
        entries = repo.recent_entries()
        assert entries[0]['amount'] == 0
        assert entries[0]['context'] == {'forgiven_principal': 500}
