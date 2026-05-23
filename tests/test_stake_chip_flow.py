"""Tests for cash_mode.stake_chip_flow.

Two surfaces:
  - `build_stake_creation_flows(stake)` — produces the right flow
    list for each stake kind / format combination.
  - `build_stake_settlement_flows(settlement)` — produces the right
    flow list given a StakeSettlement.

Plus end-to-end integration: stake creation + settlement leaves the
chip-ledger audit's drift unchanged (Commit 5 contract from the spec).
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cash_mode.stake_chip_flow import (
    DIRECTION_BORROWER_BANKROLL_TO_SEAT,
    DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL,
    DIRECTION_BORROWER_SEAT_TO_HOUSE,
    DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL,
    DIRECTION_BORROWER_TO_STAKER_BANKROLL,
    DIRECTION_HOUSE_TO_BORROWER_SEAT,
    DIRECTION_STAKER_TO_BORROWER_SEAT,
    StakeChipFlow,
    build_stake_creation_flows,
    build_stake_settlement_flows,
)
from cash_mode.stake_settlement import (
    StakeSettlement,
    settle_stake_on_leave,
)
from cash_mode.stakes import (
    BORROWER_KIND_HUMAN,
    STAKE_FORMAT_HOUSE,
    STAKE_FORMAT_MATCH_SHARE,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_HOUSE,
    STAKER_KIND_HUMAN,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository


ANCHOR = datetime(2026, 5, 19, 12, 0, 0)


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "test.db")
        SchemaManager(db_path).ensure_schema()
        yield (
            db_path,
            StakeRepository(db_path),
            ChipLedgerRepository(db_path),
        )


def _stake(
    *,
    staker_id="napoleon",
    staker_kind: str = STAKER_KIND_PERSONALITY,
    format: str = STAKE_FORMAT_PURE,
    principal: int = 400,
    match_amount: int = 0,
    origination_fee: int = 0,
    status: str = STAKE_STATUS_ACTIVE,
) -> Stake:
    return Stake(
        stake_id="stk-1",
        session_id="sess-1",
        staker_id=staker_id,
        staker_kind=staker_kind,
        borrower_id="alice",
        borrower_kind=BORROWER_KIND_HUMAN,
        format=format,
        principal=principal,
        match_amount=match_amount,
        origination_fee=origination_fee,
        cut=0.20,
        status=status,
        carry_amount=0,
        stake_tier="$10",
        created_at=ANCHOR,
    )


class TestBuildCreationFlows:
    def test_personality_pure_no_fee(self):
        stake = _stake()
        flows = build_stake_creation_flows(stake)
        assert len(flows) == 1
        f = flows[0]
        assert f.direction == DIRECTION_STAKER_TO_BORROWER_SEAT
        assert f.staker_id == "napoleon"
        assert f.staker_kind == STAKER_KIND_PERSONALITY
        assert f.borrower_id == "alice"
        assert f.amount == 400

    def test_personality_pure_with_origination_fee(self):
        stake = _stake(origination_fee=20)
        flows = build_stake_creation_flows(stake)
        assert len(flows) == 2
        # Principal flow first, fee flow second.
        assert flows[0].direction == DIRECTION_STAKER_TO_BORROWER_SEAT
        assert flows[0].amount == 400
        assert flows[1].direction == DIRECTION_BORROWER_TO_STAKER_BANKROLL
        assert flows[1].amount == 20

    def test_personality_match_share(self):
        stake = _stake(
            format=STAKE_FORMAT_MATCH_SHARE,
            principal=200, match_amount=200,
        )
        flows = build_stake_creation_flows(stake)
        # Principal + match. No origination_fee on match_share.
        assert len(flows) == 2
        assert flows[0].direction == DIRECTION_STAKER_TO_BORROWER_SEAT
        assert flows[0].amount == 200
        assert flows[1].direction == DIRECTION_BORROWER_BANKROLL_TO_SEAT
        assert flows[1].amount == 200

    def test_house_stake(self):
        stake = _stake(
            staker_id=None,
            staker_kind=STAKER_KIND_HOUSE,
            format=STAKE_FORMAT_HOUSE,
        )
        flows = build_stake_creation_flows(stake)
        assert len(flows) == 1
        f = flows[0]
        assert f.direction == DIRECTION_HOUSE_TO_BORROWER_SEAT
        assert f.staker_id is None
        assert f.staker_kind == STAKER_KIND_HOUSE
        assert f.amount == 400

    def test_human_staker(self):
        stake = _stake(
            staker_id="player-bob",
            staker_kind=STAKER_KIND_HUMAN,
            origination_fee=10,
        )
        flows = build_stake_creation_flows(stake)
        assert flows[0].direction == DIRECTION_STAKER_TO_BORROWER_SEAT
        assert flows[0].staker_kind == STAKER_KIND_HUMAN
        assert flows[1].direction == DIRECTION_BORROWER_TO_STAKER_BANKROLL

    def test_personality_missing_staker_id_raises(self):
        stake = _stake(staker_id=None)  # personality kind but no id
        with pytest.raises(ValueError, match="staker_id is NULL"):
            build_stake_creation_flows(stake)

    def test_house_stake_with_origination_fee_raises(self):
        stake = _stake(
            staker_id=None,
            staker_kind=STAKER_KIND_HOUSE,
            format=STAKE_FORMAT_PURE,  # PURE so the fee branch fires
            origination_fee=20,
        )
        with pytest.raises(ValueError, match="house stake.*origination_fee"):
            build_stake_creation_flows(stake)


class TestBuildSettlementFlows:
    def _settle(
        self, *,
        staker_kind: str = STAKER_KIND_PERSONALITY,
        staker_id="napoleon",
        staker_total: int = 480,
        borrower_total: int = 320,
        new_status: str = STAKE_STATUS_SETTLED,
        carry_amount: int = 0,
        forgiven_amount: int = 0,
    ) -> StakeSettlement:
        return StakeSettlement(
            stake_id="stk-1",
            session_id="sess-1",
            staker_id=staker_id,
            staker_kind=staker_kind,
            borrower_id="alice",
            borrower_kind=BORROWER_KIND_HUMAN,
            new_status=new_status,
            staker_total=staker_total,
            borrower_total=borrower_total,
            carry_amount=carry_amount,
            forgiven_amount=forgiven_amount,
        )

    def test_personality_clean_settle(self):
        flows = build_stake_settlement_flows(
            self._settle(staker_total=480, borrower_total=320),
        )
        assert len(flows) == 2
        assert flows[0].direction == DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL
        assert flows[0].amount == 480
        assert flows[1].direction == DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL
        assert flows[1].amount == 320

    def test_house_clean_settle(self):
        flows = build_stake_settlement_flows(
            self._settle(
                staker_kind=STAKER_KIND_HOUSE,
                staker_id=None,
                staker_total=280, borrower_total=120,
            ),
        )
        assert len(flows) == 2
        assert flows[0].direction == DIRECTION_BORROWER_SEAT_TO_HOUSE
        assert flows[0].amount == 280
        assert flows[0].staker_id is None
        assert flows[1].direction == DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL

    def test_carry_no_borrower_flow(self):
        # Borrower busted partially: staker_total > 0, borrower_total = 0.
        flows = build_stake_settlement_flows(
            self._settle(
                staker_total=150,
                borrower_total=0,
                new_status="carry",
                carry_amount=250,
                forgiven_amount=250,
            ),
        )
        # Only the staker-side flow appears.
        assert len(flows) == 1
        assert flows[0].direction == DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL

    def test_full_bust_no_flows(self):
        # Borrower busted fully: nothing on the table to drain.
        flows = build_stake_settlement_flows(
            self._settle(
                staker_total=0,
                borrower_total=0,
                new_status="carry",
                carry_amount=400,
                forgiven_amount=400,
            ),
        )
        assert flows == []

    def test_house_full_bust_no_staker_flow(self):
        # After the house-forgive override at settle_stake_on_leave,
        # a house full-bust has staker_total=0 + status='settled'.
        # forgive_balance is fired separately; the chip-flow builder
        # has nothing to emit.
        flows = build_stake_settlement_flows(
            self._settle(
                staker_kind=STAKER_KIND_HOUSE,
                staker_id=None,
                staker_total=0,
                borrower_total=0,
                new_status=STAKE_STATUS_SETTLED,
                carry_amount=0,
                forgiven_amount=400,
            ),
        )
        assert flows == []


class TestHouseForgiveFiresLedgerAnnotation:
    """Full integration: settle_stake_on_leave with a house stake +
    chip_ledger_repo writes the forgive_balance annotation.
    """

    def test_partial_bust_fires_forgive_balance(self, env):
        db_path, stake_repo, ledger_repo = env
        stake_repo.create_stake(_stake(
            staker_id=None,
            staker_kind=STAKER_KIND_HOUSE,
            format=STAKE_FORMAT_HOUSE,
            principal=200,
        ))

        settlement = settle_stake_on_leave(
            "stk-1", 50,
            stake_repo=stake_repo,
            chip_ledger_repo=ledger_repo,
            now=ANCHOR + timedelta(hours=1),
            ledger_context={'game_id': 'cash-1234'},
        )

        assert settlement.forgiven_amount == 150

        entries = [
            e for e in ledger_repo.recent_entries()
            if e['reason'] == 'forgive_balance'
        ]
        assert len(entries) == 1
        assert entries[0]['amount'] == 0
        ctx = entries[0]['context']
        assert ctx['forgiven_principal'] == 150
        assert ctx['stake_id'] == 'stk-1'
        assert ctx['game_id'] == 'cash-1234'
        assert ctx['principal'] == 200
        assert ctx['chips_at_leave'] == 50

    def test_clean_settle_no_forgive_entry(self, env):
        db_path, stake_repo, ledger_repo = env
        stake_repo.create_stake(_stake(
            staker_id=None,
            staker_kind=STAKER_KIND_HOUSE,
            format=STAKE_FORMAT_HOUSE,
            principal=200,
        ))

        settle_stake_on_leave(
            "stk-1", 400,
            stake_repo=stake_repo,
            chip_ledger_repo=ledger_repo,
            now=ANCHOR + timedelta(hours=1),
        )

        forgive_entries = [
            e for e in ledger_repo.recent_entries()
            if e['reason'] == 'forgive_balance'
        ]
        assert forgive_entries == []

    def test_personality_bust_doesnt_fire_forgive(self, env):
        db_path, stake_repo, ledger_repo = env
        stake_repo.create_stake(_stake(
            principal=200,
            staker_kind=STAKER_KIND_PERSONALITY,
            staker_id="napoleon",
        ))

        settle_stake_on_leave(
            "stk-1", 50,
            stake_repo=stake_repo,
            chip_ledger_repo=ledger_repo,
            now=ANCHOR + timedelta(hours=1),
        )

        # Personality stakes use the carry path — no ledger entry.
        assert ledger_repo.recent_entries() == []
