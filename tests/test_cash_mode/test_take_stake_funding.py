"""Regression tests for the take_stake funding leak + the settle-time
conservation guard (the stake state machine's keystone).

Two bugs, one class — "fund the wrong seat":

  1. `cash_mode/lobby.py::_apply_stake_creations` (AI `take_stake`: a busted
     borrower re-staked in place by another AI) funded the STAKER's own seat via
     `debit_bankroll_for_seat(staker_id)` while settlement drained the
     BORROWER's seat — minting the principal. PR #217 fixed the *aspiration*
     path but left this one. The fix routes take_stake through
     `fund_climb_stake(climber_id=borrower_id)`, the same single funding site.

  2. Nothing checked, at settle, that a stake's funding actually reached the
     seat it was about to drain. `assert_stake_funding_reached_borrower_seat`
     is that guard — path-agnostic, so a misroute from any origination path is
     caught at the one settlement chokepoint instead of silently minting.

See docs/plans/CASH_MODE_STAKE_STATE_MACHINE.md.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cash_mode.bankroll import AIBankrollState
from cash_mode.stake_lifecycle import (
    StakeConservationError,
    assert_stake_funding_reached_borrower_seat,
)
from cash_mode.tables import CashTableState, ai_slot, open_slot
from core.economy import ledger as L
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository

pytestmark = pytest.mark.integration

SB = "sbx_take_stake"
NOW = datetime(2026, 6, 8, 12, 0, 0)
STAKER = "the_staker"
BORROWER = "the_borrower"
PRINCIPAL = 2000


@pytest.fixture
def repos(db_path):
    SchemaManager(db_path).ensure_schema()
    br = BankrollRepository(db_path)
    lr = ChipLedgerRepository(db_path)
    sr = StakeRepository(db_path)
    # last_regen_tick == NOW so projection is a no-op (no regen noise).
    br.save_ai_bankroll(
        AIBankrollState(personality_id=STAKER, chips=100_000, last_regen_tick=NOW),
        sandbox_id=SB,
    )
    br.save_ai_bankroll(
        AIBankrollState(personality_id=BORROWER, chips=0, last_regen_tick=NOW),
        sandbox_id=SB,
    )
    yield br, lr, sr
    br.close()
    lr.close()
    sr.close()


@pytest.fixture
def custody_on():
    with patch("cash_mode.economy_flags.CHIP_CUSTODY_ENABLED", True):
        yield


@pytest.fixture
def guard_enforce():
    with patch("cash_mode.economy_flags.STAKE_SETTLE_GUARD_ENFORCE", True):
        yield


# --- take_stake funding: the leak fix ------------------------------------


def _take_stake_result(seat_index: int = 0):
    """A minimal stand-in for RosterRefreshResult: `_apply_stake_creations`
    only reads `.stake_creations` and `.new_table`. The borrower's seat is
    already refilled to PRINCIPAL (movement bakes that in before funding)."""
    from cash_mode.movement import StakeCreationChange

    seats = [open_slot() for _ in range(6)]
    seats[seat_index] = ai_slot(BORROWER, PRINCIPAL)
    table = CashTableState(table_id="t_take", stake_label="$2", seats=seats)
    sc = StakeCreationChange(
        borrower_id=BORROWER,
        staker_id=STAKER,
        seat_index=seat_index,
        principal=PRINCIPAL,
        stake_label="$2",
        cut=0.30,
    )
    return SimpleNamespace(stake_creations=[sc], new_table=table)


def test_take_stake_funds_borrower_seat_not_staker_seat(repos, custody_on):
    """The leak fix: take_stake must credit the BORROWER's seat (settlement
    drains it), never the staker's own seat (the historical mint)."""
    from cash_mode.lobby import _apply_stake_creations

    br, lr, sr = repos
    result = _take_stake_result()

    _apply_stake_creations(
        result,
        stake_repo=sr,
        relationship_repo=None,
        personality_repo=None,
        bankroll_repo=br,
        chip_ledger_repo=lr,
        sandbox_id=SB,
        now=NOW,
    )

    # Staker bankroll debited by the principal...
    assert br.load_ai_bankroll(STAKER, sandbox_id=SB).chips == 100_000 - PRINCIPAL
    # ...the principal landed on the BORROWER's seat...
    assert lr.balance_of(L.ai_seat(SB, BORROWER), sandbox_id=SB) == PRINCIPAL
    # ...and NOT on the staker's own seat (the bug this fixes).
    assert lr.balance_of(L.ai_seat(SB, STAKER), sandbox_id=SB) == 0
    # A stake row was persisted for the borrower.
    active = sr.load_active_for_borrower(BORROWER, "personality")
    assert active is not None and active.staker_id == STAKER


def test_take_stake_refusal_reverts_seat(repos, custody_on):
    """If the staker can't fund (returns None), the borrower's unbacked seat
    refill must be reverted — leaving minted chips on the seat is the mint we
    are closing. Force refusal by draining the staker below the principal."""
    from cash_mode.lobby import _apply_stake_creations

    br, lr, sr = repos
    br.save_ai_bankroll(
        AIBankrollState(personality_id=STAKER, chips=10, last_regen_tick=NOW),
        sandbox_id=SB,
    )
    result = _take_stake_result()

    _apply_stake_creations(
        result,
        stake_repo=sr,
        relationship_repo=None,
        personality_repo=None,
        bankroll_repo=br,
        chip_ledger_repo=lr,
        sandbox_id=SB,
        now=NOW,
    )

    # Seat reverted to open — no unbacked principal stranded on it.
    assert result.new_table.seats[0].get("kind") == "open"
    assert lr.balance_of(L.ai_seat(SB, BORROWER), sandbox_id=SB) == 0
    # No stake row created; staker untouched.
    assert sr.load_active_for_borrower(BORROWER, "personality") is None
    assert br.load_ai_bankroll(STAKER, sandbox_id=SB).chips == 10


# --- settle-time conservation guard --------------------------------------


def _fund_correctly(lr):
    """Funding that correctly lands on the borrower's seat (stake_fund)."""
    L.record_stake_fund(
        lr,
        source=L.ai(STAKER),
        sink=L.ai_seat(SB, BORROWER),
        amount=PRINCIPAL,
        context={"stake_id": "s1", "site": "ai_aspire_grubstake"},
        sandbox_id=SB,
    )


def _fund_wrong_seat(lr):
    """Funding that lands on the STAKER's own seat — the historical mint."""
    L.record_stake_fund(
        lr,
        source=L.ai(STAKER),
        sink=L.ai_seat(SB, STAKER),
        amount=PRINCIPAL,
        context={"stake_id": "s1", "site": "buggy_wrong_seat"},
        sandbox_id=SB,
    )


def test_guard_passes_when_funding_reached_borrower_seat(repos, custody_on, guard_enforce):
    _, lr, _ = repos
    _fund_correctly(lr)
    assert (
        assert_stake_funding_reached_borrower_seat(
            stake_id="s1",
            borrower_id=BORROWER,
            principal=PRINCIPAL,
            sandbox_id=SB,
            chip_ledger_repo=lr,
        )
        is True
    )


def test_guard_raises_on_wrong_seat_funding_when_enforced(repos, custody_on, guard_enforce):
    _, lr, _ = repos
    _fund_wrong_seat(lr)
    with pytest.raises(StakeConservationError) as exc:
        assert_stake_funding_reached_borrower_seat(
            stake_id="s1",
            borrower_id=BORROWER,
            principal=PRINCIPAL,
            sandbox_id=SB,
            chip_ledger_repo=lr,
        )
    # The message names the offending non-borrower seat.
    assert L.ai_seat(SB, STAKER) in str(exc.value)


def test_guard_raises_when_borrower_seat_never_funded(repos, custody_on, guard_enforce):
    """A stake about to drain a seat its funding never reached (no funding row
    at all) trips invariant (b)."""
    _, lr, _ = repos  # no funding recorded
    with pytest.raises(StakeConservationError):
        assert_stake_funding_reached_borrower_seat(
            stake_id="s1",
            borrower_id=BORROWER,
            principal=PRINCIPAL,
            sandbox_id=SB,
            chip_ledger_repo=lr,
        )


def test_guard_alarms_but_does_not_raise_when_not_enforced(repos, custody_on):
    """Prod default (alarm-only): a violation logs + returns False, never raises
    — so a live player's table-leave can't be wedged by the backstop."""
    _, lr, _ = repos
    _fund_wrong_seat(lr)
    with patch("cash_mode.economy_flags.STAKE_SETTLE_GUARD_ENFORCE", False):
        result = assert_stake_funding_reached_borrower_seat(
            stake_id="s1",
            borrower_id=BORROWER,
            principal=PRINCIPAL,
            sandbox_id=SB,
            chip_ledger_repo=lr,
        )
    assert result is False


def test_guard_noop_without_ledger_or_sandbox(repos):
    """No ledger / no sandbox → nothing to assert against; the guard passes."""
    _, lr, _ = repos
    assert (
        assert_stake_funding_reached_borrower_seat(
            stake_id="s1",
            borrower_id=BORROWER,
            principal=PRINCIPAL,
            sandbox_id=None,
            chip_ledger_repo=lr,
        )
        is True
    )
    assert (
        assert_stake_funding_reached_borrower_seat(
            stake_id="s1",
            borrower_id=BORROWER,
            principal=PRINCIPAL,
            sandbox_id=SB,
            chip_ledger_repo=None,
        )
        is True
    )
