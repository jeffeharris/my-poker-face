"""Tests for the double-presence guard (P3.6): a persona in a tournament is
never also seated at a cash table.

- draft-time: a currently-seated (or already-in-a-tournament) persona is not
  drafted into a new field;
- during: `active_participant_pids` reports them so the cash seat-filler excludes
  them (same path as off-grid vice/hustle);
- exit: settling the tournament moves its row to 'complete', releasing them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from flask_app.services import tournament_spawn
from flask_app.services.tournament_field import select_persona_field
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_session_repository import TournamentSessionRepository

SB = 'sb-pres'
OWNER = 'owner-pres'


class FakePersonalityRepo:
    def __init__(self, ids):
        self._ids = ids

    def list_eligible_for_cash_mode(self, *, user_id=None):
        return [{'personality_id': pid, 'name': pid} for pid in self._ids]


class FakeTable:
    def __init__(self, seats):
        self.seats = seats


class FakeCashTableRepo:
    """Reports a fixed set of seated AI personas."""

    def __init__(self, seated_pids):
        self._seated = seated_pids

    def list_all_tables(self, *, sandbox_id=None):
        return [FakeTable([{'kind': 'ai', 'personality_id': p} for p in self._seated])]


@pytest.fixture
def repos():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "econ.db")
        SchemaManager(path).ensure_schema()
        ledger_repo = ChipLedgerRepository(path)
        bankroll_repo = BankrollRepository(path)
        session_repo = TournamentSessionRepository(path)
        yield ledger_repo, bankroll_repo, session_repo
        for r in (ledger_repo, bankroll_repo, session_repo):
            r.close()


def _flush(ledger_repo):
    ledger_repo.record('central_bank', 'player:u', 1_000_000, 'player_seed', sandbox_id=SB)
    ledger_repo.record('ai:d', 'central_bank', 300_000, 'bank_pool_deposit', sandbox_id=SB)


class TestSelectExclude:
    def test_exclude_omits_personas(self):
        repo = FakePersonalityRepo([f'p{i}' for i in range(10)])
        entries = select_persona_field(
            personality_repo=repo, owner_id=OWNER, field_size=6, rng_seed=1,
            exclude={'p0', 'p1', 'p2', 'p3', 'p4'},
        )
        assert entries  # still fields from the remaining 5
        assert not ({'p0', 'p1', 'p2', 'p3', 'p4'} & set(entries))


class TestDraftExclusions:
    def test_seated_pids_excluded(self, repos):
        _, _, session_repo = repos
        cash = FakeCashTableRepo({'seated_a', 'seated_b'})
        excl = tournament_spawn.draft_exclusions(
            cash_table_repo=cash, session_repo=session_repo,
            owner_id=OWNER, sandbox_id=SB,
        )
        assert excl == {'seated_a', 'seated_b'}


class TestSpawnNeverDraftsSeated:
    def test_seated_persona_not_in_field(self, repos):
        ledger_repo, bankroll_repo, session_repo = repos
        _flush(ledger_repo)
        # 6 personas, two of which are currently seated at cash.
        persona_repo = FakePersonalityRepo([f'persona_{i}' for i in range(6)])
        cash = FakeCashTableRepo({'persona_0', 'persona_1'})
        spawned = tournament_spawn.spawn_autonomous_tournament(
            owner_id=OWNER, sandbox_id=SB,
            personality_repo=persona_repo, bankroll_repo=bankroll_repo,
            ledger_repo=ledger_repo, session_repo=session_repo, cash_table_repo=cash,
            field_size=6, table_size=3, seed=2, rng_seed=2,
        )
        assert spawned is not None
        entries = spawned['entries']
        # The seated personas were not drafted.
        assert 'persona_0' not in entries
        assert 'persona_1' not in entries
        # Field is the 4 available personas.
        assert len(entries) == 4


class TestActiveParticipantsAndRelease:
    def test_active_then_released_on_complete(self, repos):
        ledger_repo, bankroll_repo, session_repo = repos
        _flush(ledger_repo)
        persona_repo = FakePersonalityRepo([f'persona_{i}' for i in range(8)])
        spawned = tournament_spawn.spawn_autonomous_tournament(
            owner_id=OWNER, sandbox_id=SB,
            personality_repo=persona_repo, bankroll_repo=bankroll_repo,
            ledger_repo=ledger_repo, session_repo=session_repo,
            field_size=6, table_size=3, seed=3, rng_seed=3,
        )
        tid, session, entries = spawned['tournament_id'], spawned['session'], spawned['entries']

        # While active, every entrant is reported as "in a tournament" → the
        # cash seat-filler excludes them.
        active = session_repo.active_participant_pids(OWNER)
        assert set(entries) <= active

        # And a fresh draft won't pick them (already in a tournament).
        excl = tournament_spawn.draft_exclusions(
            cash_table_repo=None, session_repo=session_repo, owner_id=OWNER, sandbox_id=SB,
        )
        assert set(entries) <= excl

        # Play out + settle → released.
        session.play_out()
        tournament_spawn.settle_autonomous_tournament(
            tournament_id=tid, session=session, entries=entries, sandbox_id=SB,
            bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, session_repo=session_repo,
        )
        assert session_repo.load(tid)['status'] == 'complete'
        assert session_repo.active_participant_pids(OWNER) == set()  # released
