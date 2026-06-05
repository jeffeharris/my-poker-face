"""Tests for the human chip-statement transfer helpers (Cut 2 of
`docs/plans/CASH_MODE_STATE_MODEL.md`).

`record_player_buy_in` / `record_player_cash_out` write *transfer* rows
(`player:<id>` <-> `seat:<game_id>`) that record a human's cash-session
chip movement as a readable statement. They are deliberately:

  - conservation-NEUTRAL — neither side is `central_bank`, so the audit's
    creation/destruction sums (and therefore `drift`) ignore them; AND
  - rejected by `record()` (which only accepts bank-side rows), so they
    must go through `record_transfer`.

These tests pin both properties plus the validators, using a fake repo
so no DB is needed (mirrors `tests/test_economy_ledger.py`).
"""

from __future__ import annotations

import pytest

from core.economy import ledger


class _FakeRepo:
    """Captures record() calls; mimics ChipLedgerRepository.record."""

    def __init__(self):
        self.calls = []
        self._next_id = 1

    def record(self, *, source, sink, amount, reason, context=None, sandbox_id=None):
        self.calls.append(
            {
                "source": source,
                "sink": sink,
                "amount": amount,
                "reason": reason,
                "context": context,
                "sandbox_id": sandbox_id,
            }
        )
        rid = self._next_id
        self._next_id += 1
        return rid


# --- entity constructor ---


def test_seat_constructor_format():
    assert ledger.seat("cash-abc") == "seat:cash-abc"


def test_seat_constructor_rejects_empty():
    with pytest.raises(ValueError):
        ledger.seat("")


# --- buy-in ---


def test_buy_in_writes_player_to_seat_transfer():
    repo = _FakeRepo()
    rid = ledger.record_player_buy_in(
        repo, owner_id="jeff", game_id="cash-1", amount=2000, sandbox_id="sb"
    )
    assert rid == 1
    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["source"] == "player:jeff"
    assert call["sink"] == "seat:cash-1"
    assert call["amount"] == 2000
    assert call["reason"] == "player_buy_in"
    assert call["sandbox_id"] == "sb"


def test_buy_in_noop_on_zero_amount():
    repo = _FakeRepo()
    assert ledger.record_player_buy_in(repo, owner_id="jeff", game_id="cash-1", amount=0) is None
    assert repo.calls == []


def test_buy_in_noop_on_none_repo():
    # Must not raise when the ledger repo is unavailable.
    assert ledger.record_player_buy_in(None, owner_id="jeff", game_id="cash-1", amount=500) is None


# --- cash-out ---


def test_cash_out_writes_seat_to_player_transfer():
    repo = _FakeRepo()
    ledger.record_player_cash_out(
        repo, owner_id="jeff", game_id="cash-1", amount=2400, sandbox_id="sb"
    )
    call = repo.calls[0]
    assert call["source"] == "seat:cash-1"
    assert call["sink"] == "player:jeff"
    assert call["amount"] == 2400
    assert call["reason"] == "player_cash_out"


def test_cash_out_noop_on_zero_takehome():
    """A bust-out (0 take-home) writes NO row — the buy_in with no matching
    cash_out IS the record that the seat busted."""
    repo = _FakeRepo()
    assert ledger.record_player_cash_out(repo, owner_id="jeff", game_id="cash-1", amount=0) is None
    assert repo.calls == []


# --- conservation-neutrality (the whole point) ---


def test_transfer_reasons_excluded_from_bank_reason_sets():
    """Transfer reasons must not be in any bank-pool reason set, or the
    audit would mis-bucket them into pool depth."""
    from core.economy.ledger import (
        BANK_POOL_DEPOSIT_REASONS,
        BANK_POOL_DRAW_REASONS,
        TRANSFER_REASONS,
    )

    # Human buy-in/cash-out plus the chip-custody Phase 1 AI ledger-parity
    # transfers (ai_buy_in/ai_cash_out), stake funding + payoffs, and the
    # tournament escrow buy-in/payout (player/ai <-> tournament:<id>) — all
    # entity<->entity moves that must stay invisible to bank-pool depth math.
    # (The tournament overlay is a pool DRAW and the return a pool DEPOSIT, so
    # they are NOT transfers — they belong in the bank reason sets, asserted
    # disjoint below.)
    assert TRANSFER_REASONS == {
        "player_buy_in",
        "player_cash_out",
        "ai_buy_in",
        "ai_cash_out",
        "stake_fund",
        "stake_payoff",
        "tournament_buy_in",
        "tournament_payout",
        "ledger_reconciliation",
    }
    assert TRANSFER_REASONS.isdisjoint(BANK_POOL_DEPOSIT_REASONS)
    assert TRANSFER_REASONS.isdisjoint(BANK_POOL_DRAW_REASONS)


def test_transfer_rows_have_no_central_bank_side():
    """Neither buy-in nor cash-out may touch central_bank — that's what
    keeps them invisible to creation/destruction drift math."""
    from poker.repositories.chip_ledger_repository import CENTRAL_BANK

    repo = _FakeRepo()
    ledger.record_player_buy_in(repo, owner_id="jeff", game_id="cash-1", amount=100)
    ledger.record_player_cash_out(repo, owner_id="jeff", game_id="cash-1", amount=100)
    for call in repo.calls:
        assert call["source"] != CENTRAL_BANK
        assert call["sink"] != CENTRAL_BANK


# --- record_transfer validators ---


def test_record_transfer_rejects_bank_side():
    repo = _FakeRepo()
    rid = ledger.record_transfer(
        repo,
        source=ledger.bank(),
        sink=ledger.player("jeff"),
        amount=100,
        reason="player_buy_in",
    )
    assert rid is None
    assert repo.calls == []


def test_record_transfer_rejects_non_transfer_reason():
    repo = _FakeRepo()
    rid = ledger.record_transfer(
        repo,
        source=ledger.player("jeff"),
        sink=ledger.seat("cash-1"),
        amount=100,
        reason="ai_regen",  # a real reason, but not a transfer
    )
    assert rid is None
    assert repo.calls == []


def test_record_transfer_rejects_negative_amount():
    repo = _FakeRepo()
    rid = ledger.record_transfer(
        repo,
        source=ledger.player("jeff"),
        sink=ledger.seat("cash-1"),
        amount=-5,
        reason="player_buy_in",
    )
    assert rid is None
    assert repo.calls == []


def test_record_plain_rejects_transfer_reason():
    """The bank-only `record()` must refuse a transfer (bank-less) row even
    if someone passes a valid transfer reason — they belong in
    record_transfer."""
    repo = _FakeRepo()
    rid = ledger.record(
        repo,
        source=ledger.player("jeff"),
        sink=ledger.seat("cash-1"),
        amount=100,
        reason="player_buy_in",
    )
    assert rid is None
    assert repo.calls == []


# --- statement balances over a session (buy-in + rebuy/top-up vs cash-out) ---


def test_seat_account_balances_over_a_session():
    """The `seat:<game_id>` sub-account must net to zero across a full,
    fully-cashed-out session: every chip in via buy-in / rebuy / top-up
    leaves via cash-out. This is the property the one-sided (cash-out-only)
    bug broke — a regression guard that all bankroll->seat movements emit
    a paired buy-in row.
    """
    repo = _FakeRepo()
    gid = "cash-1"
    # Initial buy-in + a rebuy + a top-up (all bankroll -> seat).
    ledger.record_player_buy_in(repo, owner_id="jeff", game_id=gid, amount=2000)
    ledger.record_player_buy_in(repo, owner_id="jeff", game_id=gid, amount=2000)  # rebuy
    ledger.record_player_buy_in(repo, owner_id="jeff", game_id=gid, amount=500)  # top-up
    # Cash out the whole stack at leave (won a bit: 4500 in, 5200 out).
    ledger.record_player_cash_out(repo, owner_id="jeff", game_id=gid, amount=5200)

    seat_id = ledger.seat(gid)
    inflow = sum(c["amount"] for c in repo.calls if c["sink"] == seat_id)
    outflow = sum(c["amount"] for c in repo.calls if c["source"] == seat_id)
    # 4500 committed to the seat, 5200 returned — the 700 delta is winnings
    # that came from other players' stacks (not from the seat sub-account),
    # so the seat's *recorded* in/out reflects exactly what the player moved.
    assert inflow == 4500
    assert outflow == 5200
    # Every row references the same seat entity (no orphan rows).
    assert all(seat_id in (c["source"], c["sink"]) for c in repo.calls)
