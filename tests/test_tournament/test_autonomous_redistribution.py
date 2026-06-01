"""End-to-end test of the P3 redistribution foundation (P3.1 + P3.2 + P3.3).

Spawn an autonomous real-persona Main Event funded by a bank overlay, play it to
completion (funny money), distribute the pool into real persona bankrolls, and
assert the chips actually moved bank → field with the escrow netting to 0.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.economy.ledger import ai, tournament
from flask_app.services import tournament_economy_service as econ
from flask_app.services.tournament_spawn import (
    advance_autonomous_tournament,
    settle_autonomous_tournament,
    spawn_autonomous_tournament,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_session_repository import TournamentSessionRepository

SB = 'sb-auto'
OWNER = 'owner-auto'


class FakePersonalityRepo:
    def __init__(self, ids):
        self._ids = ids

    def list_eligible_for_cash_mode(self, *, user_id=None):
        return [{'personality_id': pid, 'name': pid} for pid in self._ids]


@pytest.fixture
def repos():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "econ.db")
        SchemaManager(path).ensure_schema()
        ledger_repo = ChipLedgerRepository(path)
        bankroll_repo = BankrollRepository(path)
        session_repo = TournamentSessionRepository(path)
        yield ledger_repo, bankroll_repo, session_repo
        ledger_repo.close()
        bankroll_repo.close()
        session_repo.close()


def _make_flush(ledger_repo):
    """Push the sandbox into a FLUSH regime so the overlay lever fires."""
    ledger_repo.record('central_bank', 'player:univ', 1_000_000, 'player_seed', sandbox_id=SB)
    ledger_repo.record('ai:donor', 'central_bank', 300_000, 'bank_pool_deposit', sandbox_id=SB)


def test_autonomous_overlay_redistributes_to_personas(repos):
    ledger_repo, bankroll_repo, session_repo = repos
    _make_flush(ledger_repo)
    persona_repo = FakePersonalityRepo([f'persona_{i}' for i in range(8)])

    spawned = spawn_autonomous_tournament(
        owner_id=OWNER, sandbox_id=SB,
        personality_repo=persona_repo, bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo, session_repo=session_repo,
        field_size=6, table_size=3, starting_stack=10_000, seed=7, rng_seed=7,
    )
    assert spawned is not None
    tid = spawned['tournament_id']
    plan = spawned['plan']
    session = spawned['session']
    entries = spawned['entries']

    # Flush → a real overlay pool, funded by the bank (no human buy-in).
    assert plan.bank_overlay > 0
    assert plan.human_buy_in == 0
    assert ledger_repo.balance_of(tournament(tid), sandbox_id=SB) == plan.bank_overlay
    assert session_repo.load(tid)['payout_status'] == 'pending'

    # Play the funny-money field to a winner.
    session.play_out()
    assert session.is_complete()

    # Distribute → real persona bankrolls.
    ran = settle_autonomous_tournament(
        tournament_id=tid, session=session, entries=entries, sandbox_id=SB,
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
    )
    assert ran is True

    # Escrow nets to 0 (conservation) and status is terminal.
    assert econ.verify_tournament_conservation(tid, ledger_repo, sandbox_id=SB)['balanced']
    assert session_repo.load(tid)['payout_status'] == 'complete'

    # The pool landed in real persona bankrolls — total credited == overlay
    # (rake 0 in flush), and at least one persona is now richer.
    credited = sum(
        bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB).chips
        for pid in entries
        if bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB) is not None
    )
    assert credited == plan.bank_overlay
    # Ledger-derived balance agrees with the cached bankroll (D2 consistency).
    winner = session.winner()
    assert ledger_repo.balance_of(ai(winner), sandbox_id=SB) == \
        bankroll_repo.load_ai_bankroll(winner, sandbox_id=SB).chips


def test_incremental_advance_at_world_pace_then_settles(repos):
    """Advancing round-by-round (the world-tick step) reaches the same end as a
    one-shot play_out: a winner, a settled pool in real bankrolls, escrow 0."""
    ledger_repo, bankroll_repo, session_repo = repos
    _make_flush(ledger_repo)
    persona_repo = FakePersonalityRepo([f'persona_{i}' for i in range(8)])
    spawned = spawn_autonomous_tournament(
        owner_id=OWNER, sandbox_id=SB,
        personality_repo=persona_repo, bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo, session_repo=session_repo,
        field_size=6, table_size=3, starting_stack=10_000, seed=11, rng_seed=11,
    )
    tid, session, entries, plan = (
        spawned['tournament_id'], spawned['session'], spawned['entries'], spawned['plan']
    )

    # Tick it like the world ticker would — one round at a time — until settled.
    ticks = 0
    settled_seen = False
    total_reports = 0
    while True:
        result = advance_autonomous_tournament(
            tournament_id=tid, session=session, entries=entries, sandbox_id=SB,
            bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
            rounds_per_tick=1,
        )
        total_reports += result['rounds']
        ticks += 1
        if result['settled']:
            settled_seen = True
        if result['complete']:
            break
        assert ticks < 10_000, "autonomous tournament failed to converge"

    assert session.is_complete()
    assert settled_seen
    assert total_reports >= 1  # it took at least one round to resolve
    # Same end-state guarantees as the one-shot path.
    assert econ.verify_tournament_conservation(tid, ledger_repo, sandbox_id=SB)['balanced']
    assert session_repo.load(tid)['payout_status'] == 'complete'
    credited = sum(
        bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB).chips
        for pid in entries
        if bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB) is not None
    )
    assert credited == plan.bank_overlay


def test_advance_after_complete_is_idempotent(repos):
    """Extra ticks after completion don't re-pay (the settle guard holds)."""
    ledger_repo, bankroll_repo, session_repo = repos
    _make_flush(ledger_repo)
    persona_repo = FakePersonalityRepo([f'persona_{i}' for i in range(8)])
    spawned = spawn_autonomous_tournament(
        owner_id=OWNER, sandbox_id=SB,
        personality_repo=persona_repo, bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo, session_repo=session_repo,
        field_size=4, table_size=2, seed=5, rng_seed=5,
    )
    tid, session, entries = spawned['tournament_id'], spawned['session'], spawned['entries']
    session.play_out()  # complete it in one shot
    first = advance_autonomous_tournament(
        tournament_id=tid, session=session, entries=entries, sandbox_id=SB,
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
    )
    assert first['rounds'] == 0  # already complete → no rounds advanced
    credited_after_first = sum(
        bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB).chips
        for pid in entries
        if bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB) is not None
    )
    # Another tick must not pay again.
    advance_autonomous_tournament(
        tournament_id=tid, session=session, entries=entries, sandbox_id=SB,
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
    )
    credited_after_second = sum(
        bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB).chips
        for pid in entries
        if bankroll_repo.load_ai_bankroll(pid, sandbox_id=SB) is not None
    )
    assert credited_after_first == credited_after_second
    assert econ.verify_tournament_conservation(tid, ledger_repo, sandbox_id=SB)['balanced']


def test_too_few_personas_skips(repos):
    ledger_repo, bankroll_repo, session_repo = repos
    persona_repo = FakePersonalityRepo(['only_one'])
    spawned = spawn_autonomous_tournament(
        owner_id=OWNER, sandbox_id=SB,
        personality_repo=persona_repo, bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo, session_repo=session_repo,
        field_size=6,
    )
    assert spawned is None


def test_neutral_bank_no_overlay_no_real_flow(repos):
    ledger_repo, bankroll_repo, session_repo = repos
    # Cold/empty ledger → NEUTRAL (no signal) → no overlay; pool is 0.
    persona_repo = FakePersonalityRepo([f'p{i}' for i in range(6)])
    spawned = spawn_autonomous_tournament(
        owner_id=OWNER, sandbox_id=SB,
        personality_repo=persona_repo, bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo, session_repo=session_repo,
        field_size=6, rng_seed=1,
    )
    assert spawned['plan'].bank_overlay == 0
    assert spawned['plan'].prize_pool == 0
    # Zero pool → payout marked skipped, no bankrolls created.
    spawned['session'].play_out()
    settle_autonomous_tournament(
        tournament_id=spawned['tournament_id'], session=spawned['session'],
        entries=spawned['entries'], sandbox_id=SB,
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
    )
    assert session_repo.load(spawned['tournament_id'])['payout_status'] == 'skipped'
