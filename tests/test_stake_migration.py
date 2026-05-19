"""Tests for the active_loan_* → stakes migration helper (Phase 1 Commit 3).

Three scenarios:
  - Active session present → status='active' stake row, terms verbatim.
  - No active session → status='carry' orphan row, carry_amount = principal.
  - Re-running the migration is idempotent (no duplicate rows).
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from cash_mode.bankroll import PlayerBankrollState
from cash_mode.stake_migration import (
    MigrationResult,
    UNKNOWN_STAKE_TIER,
    migrate_active_loans_to_stakes,
)
from cash_mode.stakes import (
    BORROWER_KIND_HUMAN,
    STAKE_FORMAT_HOUSE,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKER_KIND_HOUSE,
    STAKER_KIND_PERSONALITY,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository


ANCHOR = datetime(2026, 5, 19, 12, 0, 0)


@pytest.fixture
def repos():
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "test.db")
        SchemaManager(db_path).ensure_schema()
        bankroll_repo = BankrollRepository(db_path)
        stake_repo = StakeRepository(db_path)
        yield bankroll_repo, stake_repo


def _seed_player_with_loan(
    bankroll_repo: BankrollRepository,
    player_id: str,
    *,
    amount: int = 400,
    floor: float = 1.30,
    rate: float = 0.20,
    lender_id=None,
) -> None:
    bankroll_repo.save_player_bankroll(PlayerBankrollState(
        player_id=player_id,
        chips=0,
        starting_bankroll=400,
        active_loan_amount=amount,
        active_loan_floor=floor,
        active_loan_rate=rate,
        active_loan_lender_id=lender_id,
    ))


class TestActiveSession:
    def test_personality_loan_with_session(self, repos):
        bankroll_repo, stake_repo = repos
        _seed_player_with_loan(
            bankroll_repo, "alice", amount=400, rate=0.20, lender_id="napoleon",
        )

        def resolve(pid):
            assert pid == "alice"
            return ("cash-1234", "$10")

        result = migrate_active_loans_to_stakes(
            bankroll_repo=bankroll_repo,
            stake_repo=stake_repo,
            resolve_active_session=resolve,
            now=ANCHOR,
        )

        assert result == MigrationResult(active_created=1)

        stake = stake_repo.load_stake("migrated_v98_alice")
        assert stake is not None
        assert stake.status == STAKE_STATUS_ACTIVE
        assert stake.session_id == "cash-1234"
        assert stake.staker_id == "napoleon"
        assert stake.staker_kind == STAKER_KIND_PERSONALITY
        assert stake.borrower_id == "alice"
        assert stake.borrower_kind == BORROWER_KIND_HUMAN
        assert stake.format == STAKE_FORMAT_PURE
        assert stake.principal == 400
        assert stake.cut == 0.20
        assert stake.carry_amount == 0
        assert stake.stake_tier == "$10"
        assert stake.created_at == ANCHOR
        assert stake.settled_at is None

    def test_house_loan_with_session(self, repos):
        bankroll_repo, stake_repo = repos
        _seed_player_with_loan(
            bankroll_repo, "bob", amount=200, rate=0.40, lender_id=None,
        )

        result = migrate_active_loans_to_stakes(
            bankroll_repo=bankroll_repo,
            stake_repo=stake_repo,
            resolve_active_session=lambda pid: ("cash-9999", "$2"),
            now=ANCHOR,
        )

        assert result == MigrationResult(active_created=1)

        stake = stake_repo.load_stake("migrated_v98_bob")
        assert stake is not None
        assert stake.staker_id is None
        assert stake.staker_kind == STAKER_KIND_HOUSE
        assert stake.format == STAKE_FORMAT_HOUSE
        assert stake.stake_tier == "$2"


class TestOrphanCarry:
    def test_personality_loan_without_session(self, repos):
        bankroll_repo, stake_repo = repos
        _seed_player_with_loan(
            bankroll_repo, "carol", amount=300, rate=0.25, lender_id="bezos",
        )

        result = migrate_active_loans_to_stakes(
            bankroll_repo=bankroll_repo,
            stake_repo=stake_repo,
            resolve_active_session=lambda pid: None,
            now=ANCHOR,
        )

        assert result == MigrationResult(carry_created=1)

        stake = stake_repo.load_stake("migrated_v98_carol")
        assert stake is not None
        assert stake.status == STAKE_STATUS_CARRY
        assert stake.session_id == "_orphan_carol"
        assert stake.carry_amount == 300
        assert stake.principal == 300
        assert stake.staker_id == "bezos"
        assert stake.stake_tier == UNKNOWN_STAKE_TIER
        assert stake.settled_at == ANCHOR  # back-stamped to migration time

    def test_house_loan_without_session(self, repos):
        bankroll_repo, stake_repo = repos
        _seed_player_with_loan(
            bankroll_repo, "dave", amount=150, lender_id=None,
        )

        result = migrate_active_loans_to_stakes(
            bankroll_repo=bankroll_repo,
            stake_repo=stake_repo,
            resolve_active_session=lambda pid: None,
            now=ANCHOR,
        )

        assert result == MigrationResult(carry_created=1)

        stake = stake_repo.load_stake("migrated_v98_dave")
        assert stake.status == STAKE_STATUS_CARRY
        assert stake.staker_kind == STAKER_KIND_HOUSE
        assert stake.staker_id is None


class TestMultipleRowsMixed:
    def test_mixed_population(self, repos):
        bankroll_repo, stake_repo = repos
        _seed_player_with_loan(bankroll_repo, "alice", lender_id="napoleon")
        _seed_player_with_loan(bankroll_repo, "bob", lender_id=None)
        _seed_player_with_loan(bankroll_repo, "carol", lender_id="bezos")

        # alice has an active session; bob and carol don't.
        active_map = {"alice": ("cash-active", "$10")}
        result = migrate_active_loans_to_stakes(
            bankroll_repo=bankroll_repo,
            stake_repo=stake_repo,
            resolve_active_session=lambda pid: active_map.get(pid),
            now=ANCHOR,
        )

        assert result == MigrationResult(active_created=1, carry_created=2)
        assert stake_repo.load_stake("migrated_v98_alice").status == STAKE_STATUS_ACTIVE
        assert stake_repo.load_stake("migrated_v98_bob").status == STAKE_STATUS_CARRY
        assert stake_repo.load_stake("migrated_v98_carol").status == STAKE_STATUS_CARRY


class TestIdempotency:
    def test_rerunning_skips_already_migrated(self, repos):
        bankroll_repo, stake_repo = repos
        _seed_player_with_loan(bankroll_repo, "alice", lender_id="napoleon")

        first = migrate_active_loans_to_stakes(
            bankroll_repo=bankroll_repo,
            stake_repo=stake_repo,
            resolve_active_session=lambda pid: ("cash-1234", "$10"),
            now=ANCHOR,
        )
        assert first.active_created == 1

        # Second run with identical input should skip the existing row.
        second = migrate_active_loans_to_stakes(
            bankroll_repo=bankroll_repo,
            stake_repo=stake_repo,
            resolve_active_session=lambda pid: ("cash-1234", "$10"),
            now=ANCHOR,
        )
        assert second == MigrationResult(skipped_existing=1)

        # Only one stake row for the player.
        stakes = stake_repo.list_stakes_for_session("cash-1234")
        assert len(stakes) == 1

    def test_rerunning_preserves_carry_status(self, repos):
        bankroll_repo, stake_repo = repos
        _seed_player_with_loan(bankroll_repo, "alice", lender_id="napoleon")

        migrate_active_loans_to_stakes(
            bankroll_repo=bankroll_repo,
            stake_repo=stake_repo,
            resolve_active_session=lambda pid: None,
            now=ANCHOR,
        )
        # Second run — same orphan path, must not create a duplicate.
        second = migrate_active_loans_to_stakes(
            bankroll_repo=bankroll_repo,
            stake_repo=stake_repo,
            resolve_active_session=lambda pid: None,
            now=ANCHOR,
        )
        assert second.skipped_existing == 1
        carries = stake_repo.list_carries_for_borrower("alice", BORROWER_KIND_HUMAN)
        assert len(carries) == 1


class TestNoLegacyRows:
    def test_empty_db_is_noop(self, repos):
        bankroll_repo, stake_repo = repos
        result = migrate_active_loans_to_stakes(
            bankroll_repo=bankroll_repo,
            stake_repo=stake_repo,
            resolve_active_session=lambda pid: None,
            now=ANCHOR,
        )
        assert result == MigrationResult()
