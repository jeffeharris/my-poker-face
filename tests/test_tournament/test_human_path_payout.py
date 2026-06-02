"""Regression: a human-PLAYED real-persona Main Event must CREDIT its AI personas
at payout (the redistribution), not sweep their prizes to the bank pool.

The bug: the human-path payout callsites (`_maybe_payout`, `_apply_tournament_payout`)
called `apply_payout_on_complete` WITHOUT `real_persona_ids`, so it defaulted to
`frozenset()` and every AI finisher's share was swept to the pool — the redistribution
silently no-opped whenever the human was involved. The fix derives the set via
`real_persona_ids_for(session, personality_repo)` at both callsites.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.economy.ledger import ai, tournament
from flask_app.services import tournament_economy_service as econ
from flask_app.services.tournament_spawn import create_human_tournament
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_session_repository import TournamentSessionRepository

SB = 'sb-human'
OWNER = 'owner-human'


class FakePersonalityRepo:
    """Knows a fixed set of real personality ids (for both the draft pool and the
    `load_personality_by_id` discriminator the payout helper uses)."""

    def __init__(self, ids):
        self._ids = set(ids)

    def list_eligible_for_cash_mode(self, *, user_id=None):
        return [{'personality_id': pid, 'name': pid} for pid in self._ids]

    def load_personality_by_id(self, pid):
        return {'personality_id': pid} if pid in self._ids else None


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


def _flush(ledger_repo):
    """Push the sandbox into a FLUSH regime so the overlay lever funds the pool."""
    ledger_repo.record('central_bank', 'player:univ', 1_000_000, 'player_seed', sandbox_id=SB)
    ledger_repo.record('ai:donor', 'central_bank', 300_000, 'bank_pool_deposit', sandbox_id=SB)


def test_real_persona_ids_for_excludes_human_and_synthetic():
    persona_repo = FakePersonalityRepo(['sal', 'nina'])

    class S:
        human_id = 'human-x'
        entries = {'human-x': 'h', 'sal': 'a', 'nina': 'b', 'P99': 'c'}

    got = econ.real_persona_ids_for(S(), persona_repo)
    assert got == frozenset({'sal', 'nina'})  # human seat + synthetic 'P99' excluded
    # No personality repo → empty (the safe default the helper falls back to).
    assert econ.real_persona_ids_for(S(), None) == frozenset()


def test_human_played_field_credits_personas_not_pool(repos):
    ledger_repo, bankroll_repo, session_repo = repos
    _flush(ledger_repo)
    persona_repo = FakePersonalityRepo([f'persona_{i}' for i in range(8)])

    built = create_human_tournament(
        owner_id=OWNER,
        sandbox_id=SB,
        personality_repo=persona_repo,
        bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo,
        session_repo=session_repo,
        buy_in=0,  # freeroll — the overlay funds the pool
        field_size=6,
        table_size=3,
        register=False,
    )
    assert built is not None
    session = built['session']
    tid = built['tournament_id']
    assert int(session_repo.load(tid)['prize_pool']) > 0  # overlay funded it

    # Drive to completion (the fake resolver plays every table, incl. the human's
    # seat — fine for the payout test; we only care about who gets credited).
    session.play_out()
    assert session.is_complete()

    real_ids = econ.real_persona_ids_for(session, persona_repo)
    assert real_ids, "the field should resolve to real personas"

    ran = econ.apply_payout_on_complete(
        tournament_id=tid,
        session=session,
        human_owner_id=OWNER,
        sandbox_id=SB,
        bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo,
        session_repo=session_repo,
        real_persona_ids=real_ids,
    )
    assert ran

    # THE fix: real personas were credited (the redistribution), not swept to pool.
    credited = sum(ledger_repo.balance_of(ai(pid), sandbox_id=SB) for pid in real_ids)
    assert credited > 0, "AI personas must be credited, not swept to the bank pool"
    # Conservation: the escrow nets to exactly 0.
    assert ledger_repo.balance_of(tournament(tid), sandbox_id=SB) == 0
