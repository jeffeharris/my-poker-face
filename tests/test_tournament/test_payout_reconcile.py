"""Tests for resuming a tournament payout wedged at `payout_status='in_progress'`
(the crash-mid-distribute reconcile path).

A payout writes a `tournament_payout` ledger row + a bankroll bump per finisher
with no enclosing transaction; a crash mid-loop leaves partial credits and the
`apply_payout_on_complete` guard actively BLOCKS re-entry (its `== 'pending'`
guard), so without a reconcile the tournament stays wedged forever (escrow
non-zero, finishers unpaid). `reconcile_stuck_payout` resumes it from the ledger:
pays only the unpaid remainder per sink (never a double credit), sweeps the
escrow to 0, and stamps `complete`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.economy import ledger as chip_ledger
from core.economy.ledger import ai, tournament
from flask_app.services import tournament_economy_service as econ
from flask_app.services.tournament_spawn import spawn_autonomous_tournament
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_session_repository import TournamentSessionRepository
from tournament.economy import compute_payout_schedule

SB = 'sb-recon'
OWNER = 'owner-recon'


class FakePersonalityRepo:
    def __init__(self, ids):
        self._ids = ids

    def list_eligible_for_cash_mode(self, *, user_id=None):
        return [{'personality_id': pid, 'name': pid} for pid in self._ids]


@pytest.fixture
def repos():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "recon.db")
        SchemaManager(path).ensure_schema()
        ledger_repo = ChipLedgerRepository(path)
        bankroll_repo = BankrollRepository(path)
        session_repo = TournamentSessionRepository(path)
        yield ledger_repo, bankroll_repo, session_repo
        ledger_repo.close()
        bankroll_repo.close()
        session_repo.close()


def _make_flush(ledger_repo):
    ledger_repo.record('central_bank', 'player:univ', 1_000_000, 'player_seed', sandbox_id=SB)
    ledger_repo.record('ai:donor', 'central_bank', 300_000, 'bank_pool_deposit', sandbox_id=SB)


def _completed_autonomous(repos):
    """Spawn + play out an autonomous tournament; return (tid, session, entries)
    at payout_status='pending' (not yet distributed)."""
    ledger_repo, bankroll_repo, session_repo = repos
    _make_flush(ledger_repo)
    persona_repo = FakePersonalityRepo([f'persona_{i}' for i in range(8)])
    spawned = spawn_autonomous_tournament(
        owner_id=OWNER, sandbox_id=SB,
        personality_repo=persona_repo, bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo, session_repo=session_repo,
        field_size=6, table_size=3, starting_stack=10_000, seed=7, rng_seed=7,
    )
    spawned['session'].play_out()
    assert spawned['session'].is_complete()
    return spawned['tournament_id'], spawned['session'], spawned['entries']


def _pay_one_finisher_partially(repos, tid, session):
    """Simulate a crash after the FIRST finisher was paid: credit position 1 only
    (ledger row + bankroll), flip payout_status to 'in_progress', leave the rest
    of the pool sitting in escrow. Returns (winner_pid, winner_amount)."""
    ledger_repo, bankroll_repo, session_repo = repos
    row = session_repo.load(tid)
    prize_pool = int(row['prize_pool'])
    schedule = compute_payout_schedule(session.field.field_size, prize_pool, None)
    pos_to_player = econ._position_to_player(session)
    first = schedule[0]
    winner = pos_to_player[first['finishing_position']]
    amount = first['amount']
    chip_ledger.record_tournament_payout(
        ledger_repo, sink=ai(winner), tournament_id=tid, amount=amount,
        context={'site': 'payout'}, sandbox_id=SB,
    )
    from datetime import datetime

    from cash_mode.bankroll import AIBankrollState
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(personality_id=winner, chips=amount, last_regen_tick=datetime.utcnow()),
        sandbox_id=SB,
    )
    session_repo.set_payout_status(tid, 'in_progress')
    return winner, amount


def test_reconcile_resumes_partial_payout(repos):
    ledger_repo, bankroll_repo, session_repo = repos
    tid, session, entries = _completed_autonomous(repos)
    winner, winner_amount = _pay_one_finisher_partially(repos, tid, session)

    # Pre-state: stuck, escrow still holds the undistributed remainder.
    assert session_repo.load(tid)['payout_status'] == 'in_progress'
    assert ledger_repo.balance_of(tournament(tid), sandbox_id=SB) > 0

    ran = econ.reconcile_stuck_payout(
        tournament_id=tid, session=session, human_owner_id=None, sandbox_id=SB,
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
        real_persona_ids=frozenset(entries.keys()),
    )
    assert ran is True
    # Escrow nets to 0 and the status is terminal.
    assert ledger_repo.balance_of(tournament(tid), sandbox_id=SB) == 0
    assert session_repo.load(tid)['payout_status'] == 'complete'

    # The winner was NOT double-paid — their bankroll is exactly their one share.
    assert bankroll_repo.load_ai_bankroll(winner, sandbox_id=SB).chips == winner_amount

    # Total credited to real personas == the prize pool (full distribution).
    prize_pool = int(session_repo.load(tid)['prize_pool'])
    credited = sum(
        bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB).chips
        for pid in entries
        if bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB) is not None
    )
    assert credited == prize_pool


def test_reconcile_is_idempotent(repos):
    ledger_repo, bankroll_repo, session_repo = repos
    tid, session, entries = _completed_autonomous(repos)
    _pay_one_finisher_partially(repos, tid, session)

    assert econ.reconcile_stuck_payout(
        tournament_id=tid, session=session, human_owner_id=None, sandbox_id=SB,
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
        real_persona_ids=frozenset(entries.keys()),
    ) is True
    credited_after_first = sum(
        bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB).chips
        for pid in entries if bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB)
    )
    # A second reconcile is a no-op: status is no longer 'in_progress'.
    assert econ.reconcile_stuck_payout(
        tournament_id=tid, session=session, human_owner_id=None, sandbox_id=SB,
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
        real_persona_ids=frozenset(entries.keys()),
    ) is False
    credited_after_second = sum(
        bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB).chips
        for pid in entries if bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB)
    )
    assert credited_after_first == credited_after_second  # no extra chips


def test_reconcile_ignores_non_stuck(repos):
    """Only 'in_progress' rows are resumed — a 'pending' or 'complete' one is left
    for its normal path."""
    ledger_repo, bankroll_repo, session_repo = repos
    tid, session, entries = _completed_autonomous(repos)
    # payout_status is 'pending' (never started) — reconcile must not touch it.
    assert econ.reconcile_stuck_payout(
        tournament_id=tid, session=session, human_owner_id=None, sandbox_id=SB,
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
        real_persona_ids=frozenset(entries.keys()),
    ) is False
    assert session_repo.load(tid)['payout_status'] == 'pending'


def test_list_stuck_payouts(repos):
    ledger_repo, bankroll_repo, session_repo = repos
    tid, session, entries = _completed_autonomous(repos)
    assert session_repo.list_stuck_payouts() == []  # pending, not stuck
    session_repo.set_payout_status(tid, 'in_progress')
    stuck = session_repo.list_stuck_payouts()
    assert [r['tournament_id'] for r in stuck] == [tid]
    session_repo.set_payout_status(tid, 'complete')
    assert session_repo.list_stuck_payouts() == []


def test_payouts_by_sink(repos):
    ledger_repo, _bankroll, _session = repos
    chip_ledger.record_tournament_overlay(  # fund the escrow first (bank → escrow)
        ledger_repo, tournament_id='t1', amount=1000, context={}, sandbox_id=SB,
    ) if hasattr(chip_ledger, 'record_tournament_overlay') else None
    chip_ledger.record_tournament_payout(ledger_repo, sink=ai('a'), tournament_id='t1', amount=300, context={}, sandbox_id=SB)
    chip_ledger.record_tournament_payout(ledger_repo, sink=ai('b'), tournament_id='t1', amount=200, context={}, sandbox_id=SB)
    chip_ledger.record_tournament_payout(ledger_repo, sink=ai('a'), tournament_id='t1', amount=50, context={}, sandbox_id=SB)
    paid = ledger_repo.payouts_by_sink(tournament('t1'), reason='tournament_payout', sandbox_id=SB)
    assert paid == {ai('a'): 350, ai('b'): 200}


def test_claim_payout_is_atomic_cas(repos):
    """Only one caller wins pending→in_progress (the double-pay guard)."""
    ledger_repo, bankroll_repo, session_repo = repos
    tid, session, entries = _completed_autonomous(repos)
    assert session_repo.load(tid)['payout_status'] == 'pending'
    assert session_repo.claim_payout(tid) is True
    assert session_repo.load(tid)['payout_status'] == 'in_progress'
    # A second claim loses — no longer 'pending'.
    assert session_repo.claim_payout(tid) is False


def test_failed_payout_leaves_status_active(repos, monkeypatch):
    """A payout that throws must leave status='active' (NOT 'complete') so the
    stranded escrow stays visible to the reconcile watchdog — #2."""
    from flask_app.services import tournament_spawn

    ledger_repo, bankroll_repo, session_repo = repos
    tid, session, entries = _completed_autonomous(repos)

    # Force the distribution loop to blow up after claiming in_progress.
    import flask_app.services.tournament_economy_service as econ_mod
    real = econ_mod.compute_payout_schedule
    monkeypatch.setattr(econ_mod, 'compute_payout_schedule',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    tournament_spawn.settle_autonomous_tournament(
        tournament_id=tid, session=session, entries=entries, sandbox_id=SB,
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
    )
    row = session_repo.load(tid)
    assert row['payout_status'] == 'in_progress'   # wedged
    assert row['status'] == 'active'               # NOT released — visible to recovery

    # Restore + reconcile → both terminal, field released.
    monkeypatch.setattr(econ_mod, 'compute_payout_schedule', real)
    assert econ.reconcile_stuck_payout(
        tournament_id=tid, session=session, human_owner_id=None, sandbox_id=SB,
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
        real_persona_ids=frozenset(entries.keys()),
    ) is True
    row = session_repo.load(tid)
    assert row['payout_status'] == 'complete'
    assert row['status'] == 'complete'             # released after settle


def test_active_participant_pids_recency_bound(repos):
    """An abandoned active tournament ages out of the double-presence exclusion
    so its field is released back to cash seating — #3."""
    from datetime import datetime, timedelta

    ledger_repo, bankroll_repo, session_repo = repos
    tid, session, entries = _completed_autonomous(repos)
    # The freshly-saved row is within the window → its field is excluded.
    pids = session_repo.active_participant_pids(OWNER)
    assert set(entries.keys()) <= pids
    # With a cutoff in the future (everything is "too old") → released.
    future = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    assert session_repo.active_participant_pids(OWNER, active_since_iso=future) == set()
