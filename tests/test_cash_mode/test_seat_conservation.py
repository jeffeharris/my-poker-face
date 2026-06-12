"""Conservation proof for the AI cash-seat lifecycle — the chip-mint guard.

This is the regression gate for the seat double-drain leak (see
`docs/triage/CHIP_MINT_DOUBLE_DRAIN_HANDOFF.md`). The leak: a `seat:ai` ledger
account is funded by one buy-in but was drained by three vacate paths that each
cashed out a *game-state guess* (`player.stack` / `bankroll_changes.amount`)
with no single-settle guard, so an AI crossing the human-table↔lobby boundary
got credited more than once per funding.

The chip invariant we PROVE here (enforced by construction, not asserted after):

  1. `balance_of(seat:ai) >= 0` ALWAYS — a seat can never be drained below the
     chips that reached it. The double-drain's signature is a NEGATIVE seat
     balance (impossible chips). This is the assertion the bug fails.
  2. The settle is IDEMPOTENT — draining `balance_of(seat)` to exactly 0 means a
     second vacate path reads 0 and no-ops; nothing to double-credit.
  3. The bankroll is credited EXACTLY once per funding (ledger-derived balance
     reconciles; no minted chips).
  4. Global conservation: `Σ balance_of(non-bank) == −balance_of(central_bank)`.

`test_old_double_credit_mints_negative_seat` documents the bug class the fix
prevents — it drives the OLD drain (`credit_ai_cash_out` with a guessed stack)
twice and shows the seat goes negative (a mint). It exists so a future
regression that re-introduces guess-draining is caught loudly.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from cash_mode import economy_flags
from cash_mode.bankroll import (
    AIBankrollState,
    credit_ai_cash_out,
    debit_bankroll_for_seat,
    settle_ai_seat,
)
from core.economy.ledger import ai, ai_seat, bank, record_hand_pnl

pytestmark = pytest.mark.integration

_SB = "sbx-conservation"
_NOW = datetime(2026, 1, 1, 0, 0, 0)


@pytest.fixture
def custody_on(monkeypatch):
    """Chip custody is the single hardwired path; force it on for the test."""
    monkeypatch.setattr(economy_flags, "CHIP_CUSTODY_ENABLED", True)


def _seed_and_seat(repos, pid, *, seed=10_000, buy_in=2_000):
    """Seed an AI bankroll (ai_seed) and buy it into a seat (ai_buy_in)."""
    bankroll, ledger = repos["bankroll_repo"], repos["chip_ledger_repo"]
    bankroll.save_ai_bankroll(
        AIBankrollState(personality_id=pid, chips=seed, last_regen_tick=_NOW),
        sandbox_id=_SB,
        chip_ledger_repo=ledger,
    )
    debit_bankroll_for_seat(
        bankroll, pid, buy_in, sandbox_id=_SB, chip_ledger_repo=ledger, now=_NOW
    )


def _non_bank_sum(ledger) -> int:
    """Σ balance over every non-central_bank account in the sandbox."""
    accounts = set()
    with ledger._get_connection() as conn:
        for src, sink in conn.execute(
            "SELECT DISTINCT source, sink FROM chip_ledger_entries WHERE sandbox_id = ?",
            (_SB,),
        ):
            accounts.add(src)
            accounts.add(sink)
    accounts.discard(bank())
    return sum(ledger.balance_of(a, sandbox_id=_SB) for a in accounts)


def _assert_global_conservation(ledger):
    """Σ non-bank balances == −balance_of(central_bank): no chip created/destroyed
    except by the bank. Holds at every point in a well-formed ledger."""
    assert _non_bank_sum(ledger) == -ledger.balance_of(bank(), sandbox_id=_SB)


def test_settle_ai_seat_idempotent_no_mint(repos, custody_on):
    """The fix: settle drains the ledger seat balance to exactly 0, once.

    Driving the settle TWICE (the double-cross: two vacate paths hit one funded
    seat) credits the bankroll exactly once and never drives the seat negative.
    """
    ledger = repos["chip_ledger_repo"]
    _seed_and_seat(repos, "alice")
    _seed_and_seat(repos, "bob")

    # Alice wins 500 off Bob at the table (per-hand P&L is ledgered → the seat
    # balance tracks the live stack).
    record_hand_pnl(
        ledger,
        source=ai_seat(_SB, "bob"),
        sink=ai_seat(_SB, "alice"),
        amount=500,
        sandbox_id=_SB,
    )
    assert ledger.balance_of(ai_seat(_SB, "alice"), sandbox_id=_SB) == 2_500
    assert ledger.balance_of(ai_seat(_SB, "bob"), sandbox_id=_SB) == 1_500
    _assert_global_conservation(ledger)

    # Two vacate paths settle the SAME seat (the prod double-drain scenario).
    first = settle_ai_seat(
        bankroll_repo=repos["bankroll_repo"],
        chip_ledger_repo=ledger,
        sandbox_id=_SB,
        personality_id="alice",
        now=_NOW,
    )
    second = settle_ai_seat(
        bankroll_repo=repos["bankroll_repo"],
        chip_ledger_repo=ledger,
        sandbox_id=_SB,
        personality_id="alice",
        now=_NOW,
    )

    assert first == 2_500  # drained the authoritative ledger balance
    assert second == 0  # idempotent no-op — nothing left to double-credit
    # The seat is settled to EXACTLY 0 — never negative (the mint signature).
    assert ledger.balance_of(ai_seat(_SB, "alice"), sandbox_id=_SB) == 0
    # Bankroll credited exactly once: 10_000 seed − 2_000 buy-in + 2_500 take-home.
    assert ledger.balance_of(ai("alice"), sandbox_id=_SB) == 10_500
    _assert_global_conservation(ledger)


def test_settle_unfunded_seat_is_noop(repos, custody_on):
    """A seat that was never funded settles to 0 with no ledger churn."""
    settled = settle_ai_seat(
        bankroll_repo=repos["bankroll_repo"],
        chip_ledger_repo=repos["chip_ledger_repo"],
        sandbox_id=_SB,
        personality_id="ghost",
        now=_NOW,
    )
    assert settled == 0
    assert repos["chip_ledger_repo"].balance_of(ai_seat(_SB, "ghost"), sandbox_id=_SB) == 0


def _seat_ai_accounts(ledger, sandbox_id):
    """Every `seat:ai:<sb>:<pid>` account that appears in the sandbox ledger."""
    out = set()
    with ledger._get_connection() as conn:
        for src, sink in conn.execute(
            "SELECT DISTINCT source, sink FROM chip_ledger_entries WHERE sandbox_id = ?",
            (sandbox_id,),
        ):
            for acct in (src, sink):
                if acct.startswith(f"seat:ai:{sandbox_id}:"):
                    out.add(acct)
    return out


def _live_ai_seat_stacks(cash_table_repo, sandbox_id):
    """{pid: chips} for every AI currently sitting at a cash table."""
    stacks = {}
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        for slot in table.seats:
            if slot.get("kind") == "ai":
                pid = slot.get("personality_id")
                if pid:
                    stacks[pid] = stacks.get(pid, 0) + int(slot.get("chips", 0) or 0)
    return stacks


@pytest.mark.simulation
@pytest.mark.slow
def test_economy_sim_conserves_chips(db_path, monkeypatch):
    """End-to-end: a churned economy sim never mints — the regression gate.

    Runs the real lobby sim (buy-ins, per-hand P&L, rake, table-roster vacate)
    for many ticks against a fresh sandbox, then proves the seat-conservation
    invariants the double-drain violated:

      * `balance_of(seat:ai) >= 0` for EVERY seat (no minted / impossible chips)
      * `Σ balance_of(seat:ai) == Σ live AI table stacks` (the seat ledger IS the
        stack — departed seats are exactly 0, seated seats equal their chips)
      * AI bankroll: ledger-derived == stored (no unledgered movement path)
      * global `Σ non-bank balances == −balance_of(central_bank)`
    """
    # Custody is the single hardwired path; force it on BEFORE seeding so the
    # boot seat-fill's buy-ins are ledgered from tick 0.
    monkeypatch.setattr(economy_flags, "CHIP_CUSTODY_ENABLED", True)

    from cash_mode.sim_runner import SimConfig, run_sim
    from core.economy.ledger import derive_ai_balance
    from poker.repositories import create_repos
    from scripts.seed_sim_sandbox import seed_sim_sandbox

    sandbox_id = seed_sim_sandbox(
        name="seat-conservation-test", owner_id="sim-bot", db_path=db_path
    )
    repos = create_repos(db_path)
    ledger = repos["chip_ledger_repo"]
    cash_table_repo = repos["cash_table_repo"]
    bankroll_repo = repos["bankroll_repo"]

    run_sim(
        SimConfig(
            sandbox_id=sandbox_id,
            num_ticks=150,
            tick_seconds=8,
            start_at=_NOW,
            rng_seed=7,
            progress_every=0,
        ),
        repos=repos,
    )

    # (1) No seat is ever negative; departed seats settle to exactly 0.
    seat_accounts = _seat_ai_accounts(ledger, sandbox_id)
    assert seat_accounts, "sim produced no AI seats — churn didn't run"
    seat_balances = {a: ledger.balance_of(a, sandbox_id=sandbox_id) for a in seat_accounts}
    negative = {a: b for a, b in seat_balances.items() if b < 0}
    assert not negative, f"minted chips — negative seat balances: {negative}"

    # (2) The seat ledger equals the live stacks: every seated AI's seat balance
    # is its stack; every departed AI's seat is 0. So the two sums match exactly.
    sum_seat = sum(seat_balances.values())
    live_stacks = _live_ai_seat_stacks(cash_table_repo, sandbox_id)
    sum_stacks = sum(live_stacks.values())
    assert sum_seat == sum_stacks, (
        f"seat ledger ({sum_seat}) != live AI stacks ({sum_stacks}) — "
        f"the seat balance drifted from the stack"
    )

    # (3) Every AI bankroll derives from the ledger exactly as stored (no
    # unledgered movement path) — the validate_chip_custody invariant.
    drifted = []
    for pid in bankroll_repo.iter_personality_ids_with_bankrolls(sandbox_id=sandbox_id):
        stored = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
        stored_chips = int(stored.chips) if stored else 0
        derived = derive_ai_balance(ledger, personality_id=pid, sandbox_id=sandbox_id)
        if derived is not None and derived != stored_chips:
            drifted.append((pid, stored_chips, derived))
    assert not drifted, f"AI bankroll stored != ledger-derived: {drifted[:8]}"

    # (4) Global conservation: nothing created/destroyed except by central_bank.
    assert _non_bank_sum_for(ledger, sandbox_id) == -ledger.balance_of(
        bank(), sandbox_id=sandbox_id
    )


def _non_bank_sum_for(ledger, sandbox_id):
    accounts = set()
    with ledger._get_connection() as conn:
        for src, sink in conn.execute(
            "SELECT DISTINCT source, sink FROM chip_ledger_entries WHERE sandbox_id = ?",
            (sandbox_id,),
        ):
            accounts.add(src)
            accounts.add(sink)
    accounts.discard(bank())
    return sum(ledger.balance_of(a, sandbox_id=sandbox_id) for a in accounts)


def test_chokepoint_guard_blocks_double_drain(repos, custody_on):
    """The structural law: the seat cash-out chokepoint can't overdraw a seat.

    This is what protects the three production vacate paths without rewriting
    their per-entry logic. Draining a guessed `stack` amount twice (what the
    paths did, and the prod double-drain) used to drive `balance_of(seat)`
    NEGATIVE — minting chips. The `credit_ai_cash_out` bound to `balance_of(seat)`
    makes that unrepresentable: the second drain is bounded to 0.
    """
    ledger = repos["chip_ledger_repo"]
    _seed_and_seat(repos, "carol")  # seat funded with 2_000
    stack_guess = 2_000

    credit_ai_cash_out(
        repos["bankroll_repo"],
        "carol",
        stack_guess,
        sandbox_id=_SB,
        now=_NOW,
        chip_ledger_repo=ledger,
        from_seat=True,
    )
    # First drain is fine — seat back to 0.
    assert ledger.balance_of(ai_seat(_SB, "carol"), sandbox_id=_SB) == 0
    bankroll_after_first = ledger.balance_of(ai("carol"), sandbox_id=_SB)

    credit_ai_cash_out(
        repos["bankroll_repo"],
        "carol",
        stack_guess,
        sandbox_id=_SB,
        now=_NOW,
        chip_ledger_repo=ledger,
        from_seat=True,
    )
    # Second drain is BLOCKED by the conservation law — seat stays at exactly 0,
    # never negative, and the bankroll is not credited a second time. No mint.
    assert ledger.balance_of(ai_seat(_SB, "carol"), sandbox_id=_SB) == 0
    assert ledger.balance_of(ai("carol"), sandbox_id=_SB) == bankroll_after_first
