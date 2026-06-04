"""Human-staking ledger conservation (closes the staker-side gap).

Before this fix, a HUMAN staker's chips moved without a ledger row at two
points — funding the stake at origination (player debit) and receiving the
cut at leave-time settlement (player credit). With `CHIP_CUSTODY_DERIVE_READS`
off the stored int still served correctly, but the ledger-derived player
balance drifted from the stored one (the divergence that gates flipping
derive-reads on).

These tests assert the player's ledger delta now tracks the stored-bankroll
delta exactly across an origination → settlement round, via:
  - origination:  player:<staker> -> seat:ai:<sb>:<borrower>   (`stake_fund`)
  - settlement:   seat:ai:<sb>:<borrower> -> player:<staker>   (`stake_payoff`)
"""

from __future__ import annotations

from datetime import datetime

import pytest

from cash_mode.bankroll import PlayerBankrollState
from cash_mode.lobby import settle_departed_ai_stake
from cash_mode.stakes import (
    BORROWER_KIND_PERSONALITY,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKER_KIND_HUMAN,
    Stake,
)
from core.economy import ledger as L
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository

SB = "stk-sb"
PID = "frida_kahlo"
OID = "guest_jeff"


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "stk.db")
    SchemaManager(p).ensure_schema()
    return p


@pytest.fixture
def ledger_repo(db_path):
    r = ChipLedgerRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def bankroll_repo(db_path, ledger_repo):
    r = BankrollRepository(db_path)
    r.chip_ledger_repo = ledger_repo
    yield r
    r.close()


@pytest.fixture
def stake_repo(db_path):
    r = StakeRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def custody_on(monkeypatch):
    monkeypatch.setattr("cash_mode.economy_flags.CHIP_CUSTODY_ENABLED", True)


def _seed_active_human_stake(stake_repo, *, principal, fee, cut):
    stake_repo.create_stake(
        Stake(
            stake_id="stk-1",
            session_id="player_session_frida_x",
            staker_id=OID,
            staker_kind=STAKER_KIND_HUMAN,
            borrower_id=PID,
            borrower_kind=BORROWER_KIND_PERSONALITY,
            format=STAKE_FORMAT_PURE,
            principal=principal,
            match_amount=0,
            origination_fee=fee,
            cut=cut,
            status=STAKE_STATUS_ACTIVE,
            carry_amount=0,
            stake_tier="$200",
            created_at=datetime.utcnow(),
            table_id="cash-table-200-001",
        )
    )


def test_player_ledger_delta_tracks_stored_delta_winning_stake(
    bankroll_repo, ledger_repo, stake_repo, custody_on
):
    # principal 20k, no fee, 40% cut; AI leaves with 30k.
    # net = 10k → staker_total = 20k + 0.4*10k = 24k. Player nets +4k.
    principal, fee, cut, chips_at_leave = 20_000, 0, 0.40, 30_000
    bankroll_repo.save_player_bankroll(
        PlayerBankrollState(player_id=OID, chips=100_000, starting_bankroll=100_000)
    )

    stored_0 = bankroll_repo.load_player_bankroll(OID).chips
    ledger_0 = ledger_repo.balance_of(L.player(OID), sandbox_id=None)

    # --- Origination (the route's debit + funding ledger row) ---
    bankroll_repo.save_player_bankroll(
        PlayerBankrollState(
            player_id=OID, chips=stored_0 - (principal + fee), starting_bankroll=100_000
        )
    )
    L.record_stake_fund(
        ledger_repo,
        source=L.player(OID),
        sink=L.ai_seat(SB, PID),
        amount=principal,
        sandbox_id=SB,
    )
    _seed_active_human_stake(stake_repo, principal=principal, fee=fee, cut=cut)

    # --- Settlement via the shared leave-path helper ---
    settlement = settle_departed_ai_stake(
        PID,
        chips_at_leave,
        stake_repo=stake_repo,
        bankroll_repo=bankroll_repo,
        chip_ledger_repo=ledger_repo,
        relationship_repo=None,
        personality_repo=None,
        table_id="cash-table-200-001",
        sandbox_id=SB,
        now=datetime.utcnow(),
    )
    assert settlement is not None
    assert settlement.staker_total == 24_000

    stored_1 = bankroll_repo.load_player_bankroll(OID).chips
    ledger_1 = ledger_repo.balance_of(L.player(OID), sandbox_id=None)

    # Stored bankroll moved by (−principal + staker_total) = +4000 …
    assert stored_1 - stored_0 == -principal + settlement.staker_total == 4_000
    # … and the ledger-derived player balance moved by exactly the same amount:
    # the gap is closed (stake_fund −20k + stake_payoff +24k).
    assert ledger_1 - ledger_0 == stored_1 - stored_0

    # The two new player-side rows exist with the expected reasons.
    reasons = (
        {r["reason"] for r in ledger_repo.entries_for_account(L.player(OID))}
        if hasattr(ledger_repo, "entries_for_account")
        else None
    )
    if reasons is not None:
        assert "stake_fund" in reasons
        assert "stake_payoff" in reasons


def test_ai_staker_cut_sourced_from_borrower_seat(
    bankroll_repo, ledger_repo, stake_repo, custody_on
):
    # AI staker (machiavelli) backs AI borrower (frida). The staker's cut must
    # drain the BORROWER's seat, never the staker's own (empty) seat — the
    # mis-sourcing bug left the staker seat negative and the borrower seat
    # un-drained.
    from cash_mode.stakes import STAKER_KIND_PERSONALITY

    staker = "machiavelli"
    principal, cut, chips_at_leave = 20_000, 0.40, 30_000  # staker_total = 24k
    # Origination funds the borrower's seat from the staker (AI here).
    L.record_stake_fund(
        ledger_repo, source=L.ai(staker), sink=L.ai_seat(SB, PID), amount=principal, sandbox_id=SB
    )
    stake_repo.create_stake(
        Stake(
            stake_id="stk-ai-1",
            session_id="ai_session_frida_x",
            staker_id=staker,
            staker_kind=STAKER_KIND_PERSONALITY,
            borrower_id=PID,
            borrower_kind=BORROWER_KIND_PERSONALITY,
            format=STAKE_FORMAT_PURE,
            principal=principal,
            match_amount=0,
            origination_fee=0,
            cut=cut,
            status=STAKE_STATUS_ACTIVE,
            carry_amount=0,
            stake_tier="$200",
            created_at=datetime.utcnow(),
            table_id="cash-table-200-001",
        )
    )

    settlement = settle_departed_ai_stake(
        PID,
        chips_at_leave,
        stake_repo=stake_repo,
        bankroll_repo=bankroll_repo,
        chip_ledger_repo=ledger_repo,
        relationship_repo=None,
        personality_repo=None,
        table_id="cash-table-200-001",
        sandbox_id=SB,
        now=datetime.utcnow(),
    )
    assert settlement is not None and settlement.staker_total == 24_000

    # The staker's OWN seat is untouched (the bug left it at -staker_total).
    assert ledger_repo.balance_of(L.ai_seat(SB, staker), sandbox_id=SB) == 0
    # The cut drained the BORROWER's seat instead: funding (+20k) minus the
    # staker payoff (−24k) drives it to -4k. Had the payoff wrongly drained the
    # staker's seat (the bug), this seat would still read +principal.
    assert (
        ledger_repo.balance_of(L.ai_seat(SB, PID), sandbox_id=SB)
        <= principal - settlement.staker_total
    )


def test_no_ledger_rows_when_custody_disabled(bankroll_repo, ledger_repo, stake_repo):
    # Backward-compat: with custody OFF the settlement still pays the staker
    # (stored int) but writes no player ledger row.
    principal, cut, chips_at_leave = 20_000, 0.40, 30_000
    bankroll_repo.save_player_bankroll(
        PlayerBankrollState(player_id=OID, chips=100_000, starting_bankroll=100_000)
    )
    _seed_active_human_stake(stake_repo, principal=principal, fee=0, cut=cut)

    settlement = settle_departed_ai_stake(
        PID,
        chips_at_leave,
        stake_repo=stake_repo,
        bankroll_repo=bankroll_repo,
        chip_ledger_repo=ledger_repo,
        relationship_repo=None,
        personality_repo=None,
        table_id="cash-table-200-001",
        sandbox_id=SB,
        now=datetime.utcnow(),
    )
    assert settlement is not None
    # Player credited in the stored int …
    assert bankroll_repo.load_player_bankroll(OID).chips == 100_000 + settlement.staker_total
    # … but no stake_payoff to the player in the ledger (custody gated off).
    assert ledger_repo.balance_of(L.player(OID), sandbox_id=None) == 0
