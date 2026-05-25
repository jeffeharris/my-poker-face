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
        repo.record(
            'central_bank', 'player:alice', 200, 'player_seed', context={'game_id': 'cash-1'}
        )
        repo.record('ai:bob', 'central_bank', 50, 'cap_clamp', context={'cap': 10000})

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
        repo.record('player:a', 'central_bank', 60, 'house_stake_settle')

        sums = repo.sum_destructions_by_reason()
        assert sums == {'cap_clamp': 40, 'house_stake_settle': 60}

    def test_zero_amount_round_trips(self, repo):
        """Annotation rows (forgive_balance, amount=0) survive the schema's
        CHECK constraint and come back through recent_entries."""
        repo.record(
            'player:a', 'central_bank', 0, 'forgive_balance', context={'forgiven_principal': 500}
        )
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

    def test_recent_entries_scopes_by_sandbox(self, repo):
        """recent_entries(sandbox_id=) returns only rows for that sandbox.

        Pre-v103 NULL-sandbox rows are excluded from a scoped read; the
        default (None) returns every row regardless of sandbox.
        """
        repo.record('central_bank', 'ai:zeus', 100, 'ai_seed', sandbox_id='sb1')
        repo.record('central_bank', 'ai:hera', 200, 'ai_seed', sandbox_id='sb2')
        # Legacy NULL-sandbox row (pre-v103 bucket).
        repo.record('central_bank', 'ai:ares', 50, 'ai_seed')

        all_entries = repo.recent_entries()
        assert {e['amount'] for e in all_entries} == {100, 200, 50}

        sb1 = repo.recent_entries(sandbox_id='sb1')
        assert [e['amount'] for e in sb1] == [100]

        sb2 = repo.recent_entries(sandbox_id='sb2')
        assert [e['amount'] for e in sb2] == [200]

        # Unknown sandbox returns empty, doesn't crash.
        assert repo.recent_entries(sandbox_id='unknown') == []


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


class TestCasinoSeatReturn:
    """`record_casino_seat_return` is the destruction-side mirror of
    `record_casino_seat_seed`. It returns residual seat chips to the
    bank pool when a casino tears down with chips still on a tourist's
    seat. Without this helper, the conservation invariant would break
    (chips would just vanish from the universe).
    """

    def test_writes_destruction_to_bank(self, repo):
        eid = ledger_mod.record_casino_seat_return(
            repo,
            personality_id='tourist-abc12345',
            amount=200,
            context={'site': 'casino_teardown', 'stake_label': '$2'},
            sandbox_id='sb-1',
        )
        assert eid is not None
        entries = repo.recent_entries()
        assert entries[0]['source'] == 'ai:tourist-abc12345'
        assert entries[0]['sink'] == 'central_bank'
        assert entries[0]['amount'] == 200
        assert entries[0]['reason'] == 'casino_seat_return'

    def test_counts_as_bank_pool_deposit(self, repo):
        """Pool depth must absorb the returned chips — that's the whole
        point of using a BANK_POOL_DEPOSIT_REASON."""
        from core.economy.ledger import BANK_POOL_DEPOSIT_REASONS

        assert 'casino_seat_return' in BANK_POOL_DEPOSIT_REASONS

    def test_noop_on_none_repo(self):
        result = ledger_mod.record_casino_seat_return(
            None,
            personality_id='tourist-x',
            amount=100,
        )
        assert result is None

    def test_noop_on_zero_or_negative_amount(self, repo):
        assert (
            ledger_mod.record_casino_seat_return(
                repo,
                personality_id='tourist-x',
                amount=0,
            )
            is None
        )
        assert (
            ledger_mod.record_casino_seat_return(
                repo,
                personality_id='tourist-x',
                amount=-1,
            )
            is None
        )
        assert repo.recent_entries() == []

    def test_pairs_with_seat_seed_for_net_zero(self, repo):
        """Seed 80 chips to a tourist seat, return 80 → net pool change
        is zero. This is the conservation property we care about."""
        ledger_mod.record_casino_seat_seed(
            repo,
            personality_id='tourist-roundtrip',
            amount=80,
            sandbox_id='sb-1',
        )
        ledger_mod.record_casino_seat_return(
            repo,
            personality_id='tourist-roundtrip',
            amount=80,
            sandbox_id='sb-1',
        )
        from cash_mode.closed_economy import compute_bank_pool_reserves

        # Seed+return cancel out: depth unchanged from initial 0.
        assert compute_bank_pool_reserves(repo, sandbox_id='sb-1') == 0


class TestRecordSideHustleEarning:
    """`side_hustle_earning` draws from the bank pool: central_bank -> ai.

    Mirror of `record_tourist_injection` — the faucet that replaces
    passive ai_regen (CASH_MODE_SIDE_HUSTLE.md). The pool funds it, so
    the reason lives in BANK_POOL_DRAW_REASONS.
    """

    def test_records_bank_to_ai(self, repo):
        eid = ledger_mod.record_side_hustle_earning(
            repo,
            personality_id='napoleon',
            amount=250,
            context={'site': 'side_hustle_return', 'duration_bucket': 'medium'},
            sandbox_id='sb-1',
        )
        assert eid is not None
        entry = repo.recent_entries()[0]
        assert entry['source'] == 'central_bank'
        assert entry['sink'] == 'ai:napoleon'
        assert entry['amount'] == 250
        assert entry['reason'] == 'side_hustle_earning'

    def test_is_a_bank_pool_draw_reason(self):
        from core.economy.ledger import BANK_POOL_DRAW_REASONS

        assert 'side_hustle_earning' in BANK_POOL_DRAW_REASONS

    def test_draws_down_pool_reserves(self, repo):
        """Rake deposits 100; a 30-chip hustle payout draws it back down,
        leaving 70 — proving the hustle spends recyclable pool depth."""
        from cash_mode.closed_economy import compute_bank_pool_reserves

        ledger_mod.record_table_rake(
            repo,
            source=ledger_mod.ai('bezos'),
            amount=100,
            sandbox_id='sb-1',
        )
        ledger_mod.record_side_hustle_earning(
            repo,
            personality_id='napoleon',
            amount=30,
            sandbox_id='sb-1',
        )
        assert compute_bank_pool_reserves(repo, sandbox_id='sb-1') == 70

    def test_noop_on_none_repo(self):
        assert (
            ledger_mod.record_side_hustle_earning(
                None,
                personality_id='napoleon',
                amount=100,
            )
            is None
        )

    def test_noop_on_zero_or_negative_amount(self, repo):
        assert (
            ledger_mod.record_side_hustle_earning(
                repo,
                personality_id='napoleon',
                amount=0,
            )
            is None
        )
        assert (
            ledger_mod.record_side_hustle_earning(
                repo,
                personality_id='napoleon',
                amount=-5,
            )
            is None
        )
        assert repo.recent_entries() == []
