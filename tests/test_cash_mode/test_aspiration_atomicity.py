"""Atomicity of `_process_aspiration_asks` (Window A — chip-minting fix).

Phase 2 of `docs/plans/CASH_SEAT_INVARIANT_HARDENING.md` (§1.2 Window A, §3).

`_process_aspiration_asks` commits two financial operations (debit the
staker, create the stake row) alongside two structural mutations on
`result` (vacate the asker's seat, queue a `from_seat` chip-return that
credits the asker `seat_chips + principal`).

The historical ordering vacated the seat + queued the chip-return BEFORE
the staker was debited. If the staker debit failed (raise OR a None
"refused" return), the asker was still credited `seat_chips + principal`
with nobody debited the `principal` → **principal chips minted**, and no
stake row was ever written.

These tests drive the function with a single climb-eligible AI and:
  - force the staker debit to fail → assert NO seat vacate, NO from_seat
    change, no stake row (chips conserved);
  - force `create_stake` to fail → assert staker refunded, NO seat vacate,
    NO from_seat change;
  - happy path → assert the success end-state is exactly the historical
    one (seat vacated, single `from_seat` of `seat_chips + principal`,
    staker debited `principal`, one stake row, idle entry, decision tag).
"""

from __future__ import annotations

import random
from datetime import datetime

import pytest

import cash_mode.bankroll as bankroll_mod
from cash_mode.lobby import _process_aspiration_asks
from cash_mode.movement import RosterRefreshResult
from cash_mode.staker_profile import BorrowerProfile, StakerProfile
from cash_mode.tables import CashTableState, ai_slot, open_slot

pytestmark = pytest.mark.integration


# --- Fixtures / fakes -------------------------------------------------------

ASKER = "asker_climber"
STAKER = "wealthy_patron"
SEAT_CHIPS = 50  # asker's current seat stack
PRINCIPAL = 400  # $10 min buy-in (target tier)


class _AlwaysFireRng(random.Random):
    """rng whose `.random()` always returns 0.0 so the aspiration roll
    (`rng.random() >= prob`) always passes, and `rng.choice`/`choices`
    pick deterministically."""

    def random(self):  # noqa: A003 - matches stdlib signature
        return 0.0


class _FakeStakeRepo:
    """Minimal stake repo for the aspiration path. No active stake, no
    carries → the AI is climb-eligible. `create_stake` records rows;
    `fail_create` forces a raise to exercise the create_stake window."""

    def __init__(self, *, fail_create: bool = False):
        self.created = []
        self.fail_create = fail_create

    def load_active_for_borrower(self, borrower_id, borrower_kind):
        return None

    def list_carries_for_borrower(self, borrower_id, borrower_kind):
        return []

    def create_stake(self, stake):
        if self.fail_create:
            raise RuntimeError("forced create_stake failure")
        self.created.append(stake)


class _FakeBankrollRepo:
    """Tracks AI bankrolls in a dict so we can assert debit/refund.

    Implements exactly the surface `_process_aspiration_asks` +
    `debit_bankroll_for_seat` (no ledger handle) touch:
      - load_aspiration_cooldown_until / save_aspiration_cooldown_until
      - load_borrower_profile
      - load_ai_bankroll / save_ai_bankroll  (used by the real debit fn)
    """

    def __init__(self, balances):
        # balances: {pid: chips}
        self.balances = dict(balances)
        self.cooldowns = {}

    # aspiration gate -------------------------------------------------
    def load_aspiration_cooldown_until(self, pid, *, sandbox_id=None):
        return self.cooldowns.get(pid)

    def save_aspiration_cooldown_until(self, pid, *, sandbox_id=None, until=None):
        self.cooldowns[pid] = until

    def load_borrower_profile(self, pid):
        # Eager climber so the probability roll has a non-zero product.
        return BorrowerProfile(willing=True, aspiration_bias=1.0)

    # real debit_bankroll_for_seat surface (no-ledger branch) ---------
    def load_ai_bankroll(self, pid, *, sandbox_id=None):
        chips = self.balances.get(pid)
        if chips is None:
            return None
        return bankroll_mod.AIBankrollState(
            personality_id=pid,
            chips=chips,
            last_regen_tick=datetime(2026, 1, 1),
        )

    def save_ai_bankroll(self, state, *, sandbox_id=None):
        self.balances[state.personality_id] = state.chips


def _make_table():
    from cash_mode.tables import TABLE_SEAT_COUNT

    seats = [open_slot() for _ in range(TABLE_SEAT_COUNT)]
    seats[0] = ai_slot(ASKER, SEAT_CHIPS)
    return CashTableState(table_id="t_climb", stake_label="$2", seats=seats)


def _make_other_table_with_staker():
    from cash_mode.tables import TABLE_SEAT_COUNT

    seats = [open_slot() for _ in range(TABLE_SEAT_COUNT)]
    seats[0] = ai_slot(STAKER, PRINCIPAL)  # seat chips irrelevant here
    return CashTableState(table_id="t_other", stake_label="$10", seats=seats)


def _run(*, bankroll_repo, stake_repo):
    """Drive `_process_aspiration_asks` for a single eligible AI.

    Asker bankroll = 1000 → ratio 1000/2000 = 0.5 (wealth-gap peak; the
    target is SAFE_BUY_IN_COUNT × 400 = 2000) so the probability is
    strictly > 0 and the always-0.0 rng fires the ask.
    Staker bankroll deep enough to clear the 5%-of-bankroll capacity gate
    (`bankroll * 0.05 >= 400` → bankroll >= 8000)."""
    table = _make_table()
    other = _make_other_table_with_staker()
    result = RosterRefreshResult(new_table=table)

    def bankroll_lookup(pid):
        return bankroll_repo.balances.get(pid, 0)

    def staker_profile_lookup(pid):
        return StakerProfile(
            willing=True,
            max_loan_pct_of_bankroll=0.05,
            floor_anchor=1.20,
            rate_anchor=0.30,
            respect_floor=-0.5,
            heat_ceiling=0.7,
        )

    def relationship_lookup(a, b):
        return None  # neutral defaults clear the gates

    def history_lookup(pid):
        return {}

    def starting_bankroll_lookup(pid):
        return bankroll_repo.balances.get(pid, 0)

    _process_aspiration_asks(
        result=result,
        bankroll_repo=bankroll_repo,
        stake_repo=stake_repo,
        relationship_repo=None,
        personality_repo=None,
        chip_ledger_repo=None,
        sandbox_id=None,
        now=datetime(2026, 5, 29, 12, 0, 0),
        rng=_AlwaysFireRng(),
        staker_profile_lookup=staker_profile_lookup,
        bankroll_lookup=bankroll_lookup,
        relationship_lookup=relationship_lookup,
        history_lookup=history_lookup,
        starting_bankroll_lookup=starting_bankroll_lookup,
        all_tables=[table, other],
        idle_pool=[],
    )
    return result, table


def _from_seat_changes(result, pid):
    return [
        c for c in result.bankroll_changes if c.direction == "from_seat" and c.personality_id == pid
    ]


# --- Tests ------------------------------------------------------------------


def test_happy_path_commits_seat_chip_stake_unchanged():
    """Success path: seat vacated, single from_seat of seat_chips+principal,
    staker debited principal, one stake row, idle add + decision tag.

    This is the byte-identical success end-state the reorder must preserve.
    """
    bankroll_repo = _FakeBankrollRepo({ASKER: 1000, STAKER: 100_000})
    stake_repo = _FakeStakeRepo()

    result, table = _run(bankroll_repo=bankroll_repo, stake_repo=stake_repo)

    # Seat vacated.
    assert table.seats[0]["kind"] == "open"
    # Exactly one from_seat change crediting seat_chips + principal.
    changes = _from_seat_changes(result, ASKER)
    assert len(changes) == 1
    assert changes[0].amount == SEAT_CHIPS + PRINCIPAL
    # Staker debited exactly principal.
    assert bankroll_repo.balances[STAKER] == 100_000 - PRINCIPAL
    # One stake row, correct principal/borrower/staker.
    assert len(stake_repo.created) == 1
    stake = stake_repo.created[0]
    assert stake.borrower_id == ASKER
    assert stake.staker_id == STAKER
    assert stake.principal == PRINCIPAL
    # Idle add + decision tag.
    idle_adds = [c for c in result.idle_changes if c.kind == "add" and c.personality_id == ASKER]
    assert len(idle_adds) == 1
    assert result.decisions.get(ASKER) == "aspiration_climb"


def test_staker_debit_failure_does_not_mint_chips(monkeypatch):
    """Window A: if the staker debit FAILS, the asker's seat must NOT be
    vacated and NO from_seat chip-return may be queued — otherwise the
    asker is credited principal chips nobody was debited (minting).

    On current (buggy) code this FAILS (seat vacated + change queued
    despite the failed debit). After the fix it passes.
    """
    bankroll_repo = _FakeBankrollRepo({ASKER: 1000, STAKER: 100_000})
    stake_repo = _FakeStakeRepo()

    def _boom(*args, **kwargs):
        raise RuntimeError("forced staker debit failure")

    # Funding now flows through the single site `fund_climb_stake`
    # (cash_mode.stake_lifecycle); lobby imports it at call time, so patching
    # the module attribute intercepts the climb funding.
    monkeypatch.setattr("cash_mode.stake_lifecycle.fund_climb_stake", _boom)

    result, table = _run(bankroll_repo=bankroll_repo, stake_repo=stake_repo)

    # Seat must still be occupied by the asker (NOT vacated).
    assert table.seats[0]["kind"] == "ai", "seat was vacated despite failed staker debit"
    assert table.seats[0]["personality_id"] == ASKER
    assert int(table.seats[0]["chips"]) == SEAT_CHIPS
    # No from_seat chip-return queued → no minting.
    assert _from_seat_changes(result, ASKER) == []
    # No stake row written.
    assert stake_repo.created == []
    # Staker bankroll untouched.
    assert bankroll_repo.balances[STAKER] == 100_000
    # Ask skipped cleanly — no aspiration_climb decision tag committed.
    assert result.decisions.get(ASKER) != "aspiration_climb"


def test_create_stake_failure_refunds_staker_and_keeps_seat():
    """If create_stake fails AFTER the staker was debited, the staker must
    be refunded and the seat must NOT be vacated (no chip-return queued).
    """
    bankroll_repo = _FakeBankrollRepo({ASKER: 1000, STAKER: 100_000})
    stake_repo = _FakeStakeRepo(fail_create=True)

    result, table = _run(bankroll_repo=bankroll_repo, stake_repo=stake_repo)

    # Staker refunded to original balance.
    assert (
        bankroll_repo.balances[STAKER] == 100_000
    ), "staker not refunded after create_stake failure"
    # Seat still the asker's.
    assert table.seats[0]["kind"] == "ai"
    assert table.seats[0]["personality_id"] == ASKER
    assert int(table.seats[0]["chips"]) == SEAT_CHIPS
    # No chip-return queued.
    assert _from_seat_changes(result, ASKER) == []
    # No stake row persisted.
    assert stake_repo.created == []
    assert result.decisions.get(ASKER) != "aspiration_climb"
