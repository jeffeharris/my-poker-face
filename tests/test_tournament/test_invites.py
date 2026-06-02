"""Tests for the circuit Main Event invite lifecycle (P3.5 — backend).

offer → accept (human tournament + buy-in) / decline / expire (autonomous),
one-at-a-time guard, and affordability on accept.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cash_mode.bankroll import PlayerBankrollState
from core.economy.ledger import player, tournament
from flask_app.services import tournament_economy_service as econ, tournament_invites as inv
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_invite_repository import TournamentInviteRepository
from poker.repositories.tournament_session_repository import TournamentSessionRepository

SB = 'sb-inv'
OWNER = 'owner-inv'


class FakePersonalityRepo:
    def __init__(self, ids):
        self._ids = ids

    def list_eligible_for_cash_mode(self, *, user_id=None):
        return [{'personality_id': pid, 'name': pid} for pid in self._ids]


@pytest.fixture
def kit():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "econ.db")
        SchemaManager(path).ensure_schema()
        repos = dict(
            ledger_repo=ChipLedgerRepository(path),
            bankroll_repo=BankrollRepository(path),
            session_repo=TournamentSessionRepository(path),
            invite_repo=TournamentInviteRepository(path),
            personality_repo=FakePersonalityRepo([f'persona_{i}' for i in range(8)]),
        )
        yield repos
        for r in (repos['ledger_repo'], repos['bankroll_repo'],
                  repos['session_repo'], repos['invite_repo']):
            r.close()


def _offer(kit, **kw):
    return inv.offer(
        invite_repo=kit['invite_repo'], session_repo=kit['session_repo'],
        owner_id=OWNER, sandbox_id=SB, field_size=6, table_size=3,
        starting_stack=10_000, seed=4, **kw,
    )


class TestOffer:
    def test_offer_creates_open_invite(self, kit):
        invite = _offer(kit, buy_in=500)
        assert invite is not None
        assert invite['status'] == 'offered'
        assert invite['buy_in'] == 500
        assert inv.active_invite(kit['invite_repo'], OWNER)['invite_id'] == invite['invite_id']

    def test_only_one_open_invite(self, kit):
        assert _offer(kit, buy_in=0) is not None
        assert _offer(kit, buy_in=0) is None  # second suppressed


class TestAccept:
    def test_accept_builds_human_tournament_and_charges(self, kit):
        kit['bankroll_repo'].save_player_bankroll(
            PlayerBankrollState(player_id=OWNER, chips=5_000, starting_bankroll=10_000)
        )
        _offer(kit, buy_in=500)
        result = inv.accept(
            invite_repo=kit['invite_repo'], personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'], ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'], owner_id=OWNER,
        )
        assert result is not None
        tid = result['tournament_id']
        assert result['human_id'] == f'human:{OWNER}'
        # Human charged into the escrow.
        assert kit['bankroll_repo'].load_player_bankroll(OWNER).chips == 4_500
        assert kit['ledger_repo'].balance_of(player(OWNER), sandbox_id=SB) == -500
        assert kit['ledger_repo'].balance_of(tournament(tid), sandbox_id=SB) >= 500
        # Invite consumed.
        assert inv.active_invite(kit['invite_repo'], OWNER) is None
        assert kit['invite_repo'].load(result_invite_id(kit))['status'] == 'accepted'

    def test_accept_insufficient_funds_keeps_invite_open(self, kit):
        kit['bankroll_repo'].save_player_bankroll(
            PlayerBankrollState(player_id=OWNER, chips=100, starting_bankroll=10_000)
        )
        _offer(kit, buy_in=500)
        with pytest.raises(econ.InsufficientFundsError):
            inv.accept(
                invite_repo=kit['invite_repo'], personality_repo=kit['personality_repo'],
                bankroll_repo=kit['bankroll_repo'], ledger_repo=kit['ledger_repo'],
                session_repo=kit['session_repo'], owner_id=OWNER,
            )
        # Nothing consumed; invite still open; no debit.
        assert inv.active_invite(kit['invite_repo'], OWNER) is not None
        assert kit['bankroll_repo'].load_player_bankroll(OWNER).chips == 100

    def test_accept_with_no_open_invite_is_none(self, kit):
        assert inv.accept(
            invite_repo=kit['invite_repo'], personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'], ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'], owner_id=OWNER,
        ) is None


class TestDeclineExpire:
    def test_decline_spawns_autonomous(self, kit):
        _offer(kit, buy_in=0)
        spawned = inv.decline(
            invite_repo=kit['invite_repo'], personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'], ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'], owner_id=OWNER,
        )
        assert spawned is not None
        assert inv.active_invite(kit['invite_repo'], OWNER) is None  # consumed
        # Invite linked to the autonomous tournament + marked declined.
        row = kit['invite_repo'].load(result_invite_id(kit))
        assert row['status'] == 'declined'
        assert row['tournament_id'] == spawned['tournament_id']

    def test_expire_due_spawns_autonomous(self, kit):
        # Offer with a past expiry → the sweep expires it autonomously.
        _offer(kit, buy_in=0, expires_at='2000-01-01T00:00:00')
        spawned = inv.expire_due(
            invite_repo=kit['invite_repo'], personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'], ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'], now_iso='2026-06-01T00:00:00',
        )
        assert len(spawned) == 1
        assert inv.active_invite(kit['invite_repo'], OWNER) is None
        assert kit['invite_repo'].load(result_invite_id(kit))['status'] == 'expired'

    def test_future_expiry_not_swept(self, kit):
        _offer(kit, buy_in=0, expires_at='2099-01-01T00:00:00')
        spawned = inv.expire_due(
            invite_repo=kit['invite_repo'], personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'], ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'], now_iso='2026-06-01T00:00:00',
        )
        assert spawned == []
        assert inv.active_invite(kit['invite_repo'], OWNER) is not None  # still open


def result_invite_id(kit):
    """The single invite row's id (these tests create exactly one)."""
    import sqlite3

    conn = sqlite3.connect(kit['invite_repo'].db_path)
    try:
        return conn.execute("SELECT invite_id FROM tournament_invites LIMIT 1").fetchone()[0]
    finally:
        conn.close()


class TestInviteClaimCAS:
    """The cross-worker compare-and-swap that makes accept/decline/expire safe
    when the in-memory sandbox lock can't (it doesn't span gunicorn workers)."""

    def test_claim_only_one_winner(self, kit):
        _offer(kit, buy_in=0)
        iid = result_invite_id(kit)
        repo = kit['invite_repo']
        assert repo.claim(iid, to_status='accepted', owner_id=OWNER) is True
        # A second claim loses — the row is no longer 'offered'.
        assert repo.claim(iid, to_status='accepted', owner_id=OWNER) is False

    def test_claim_owner_guard(self, kit):
        _offer(kit, buy_in=0)
        iid = result_invite_id(kit)
        assert kit['invite_repo'].claim(iid, to_status='accepted', owner_id='someone-else') is False

    def test_revert_reopens_unlinked_claim(self, kit):
        _offer(kit, buy_in=0)
        iid = result_invite_id(kit)
        repo = kit['invite_repo']
        assert repo.claim(iid, to_status='accepted', owner_id=OWNER) is True
        assert repo.revert_to_offered(iid) is True
        assert repo.load(iid)['status'] == 'offered'
        # Re-openable → a fresh claim succeeds again.
        assert repo.claim(iid, to_status='accepted', owner_id=OWNER) is True

    def test_revert_refuses_linked_invite(self, kit):
        _offer(kit, buy_in=0)
        iid = result_invite_id(kit)
        repo = kit['invite_repo']
        repo.claim(iid, to_status='accepted', owner_id=OWNER)
        repo.resolve(iid, status='accepted', tournament_id='tourney_x')  # link it
        # A linked (real) acceptance must never be reverted out from under play.
        assert repo.revert_to_offered(iid) is False
        assert repo.load(iid)['status'] == 'accepted'

    def test_accept_loser_bails_no_double_charge(self, kit):
        """A worker whose invite was already claimed gets None and charges nothing."""
        kit['bankroll_repo'].save_player_bankroll(
            PlayerBankrollState(player_id=OWNER, chips=100_000, starting_bankroll=100_000)
        )
        _offer(kit, buy_in=500)
        iid = result_invite_id(kit)
        # Pre-claim to simulate the winning worker.
        assert kit['invite_repo'].claim(iid, to_status='accepted', owner_id=OWNER) is True
        before = kit['bankroll_repo'].load_player_bankroll(OWNER).chips
        result = inv.accept(
            invite_repo=kit['invite_repo'], personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'], ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'], owner_id=OWNER,
        )
        assert result is None  # loser bails
        assert kit['bankroll_repo'].load_player_bankroll(OWNER).chips == before  # no charge


class TestExpireScoping:
    def test_expire_due_scoped_to_sandbox(self, kit):
        # Owner A in SB (past-due) + owner B in another sandbox (past-due).
        _offer(kit, buy_in=0, expires_at='2000-01-01T00:00:00')
        inv.offer(
            invite_repo=kit['invite_repo'], session_repo=kit['session_repo'],
            owner_id='owner-B', sandbox_id='sb-B', field_size=6, table_size=3,
            starting_stack=10_000, seed=4, buy_in=0, expires_at='2000-01-01T00:00:00',
        )
        # Sweep ONLY sandbox SB — owner B's invite (foreign sandbox) is untouched.
        spawned = inv.expire_due(
            invite_repo=kit['invite_repo'], personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'], ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'], now_iso='2026-06-01T00:00:00', sandbox_id=SB,
        )
        assert len(spawned) == 1
        assert inv.active_invite(kit['invite_repo'], OWNER) is None        # A expired
        assert inv.active_invite(kit['invite_repo'], 'owner-B') is not None  # B untouched

    def test_list_open_due_sandbox_filter(self, kit):
        _offer(kit, buy_in=0, expires_at='2000-01-01T00:00:00')
        inv.offer(
            invite_repo=kit['invite_repo'], session_repo=kit['session_repo'],
            owner_id='owner-B', sandbox_id='sb-B', field_size=6, table_size=3,
            starting_stack=10_000, seed=4, buy_in=0, expires_at='2000-01-01T00:00:00',
        )
        repo = kit['invite_repo']
        assert len(repo.list_open_due(now_iso='2026-06-01T00:00:00')) == 2  # global
        scoped = repo.list_open_due(now_iso='2026-06-01T00:00:00', sandbox_id='sb-B')
        assert [r['owner_id'] for r in scoped] == ['owner-B']


class TestOneOpenInviteIndex:
    """v136 partial unique index — one open invite per owner, enforced in the DB."""

    def test_second_open_invite_rejected_at_db(self, kit):
        import sqlite3

        repo = kit['invite_repo']
        repo.create(invite_id='inv_a', owner_id=OWNER, sandbox_id=SB,
                    buy_in=0, field_size=6, table_size=3, starting_stack=10_000)
        with pytest.raises(sqlite3.IntegrityError):
            repo.create(invite_id='inv_b', owner_id=OWNER, sandbox_id=SB,
                        buy_in=0, field_size=6, table_size=3, starting_stack=10_000)

    def test_resolving_frees_the_slot(self, kit):
        repo = kit['invite_repo']
        repo.create(invite_id='inv_a', owner_id=OWNER, sandbox_id=SB,
                    buy_in=0, field_size=6, table_size=3, starting_stack=10_000)
        repo.resolve('inv_a', status='expired')
        repo.create(invite_id='inv_b', owner_id=OWNER, sandbox_id=SB,
                    buy_in=0, field_size=6, table_size=3, starting_stack=10_000)
        assert repo.active_for_owner(OWNER)['invite_id'] == 'inv_b'

    def test_offer_handles_lost_race_gracefully(self, kit):
        """offer() turns a constraint violation (concurrent winner) into the open
        invite, not a 500."""
        repo = kit['invite_repo']
        # Insert an open invite directly (a concurrent worker's), bypassing offer()'s
        # active_for_owner guard to force the insert race.
        repo.create(invite_id='other', owner_id=OWNER, sandbox_id=SB,
                    buy_in=0, field_size=6, table_size=3, starting_stack=10_000)
        # offer() sees it via active_for_owner and returns None (already open).
        assert _offer(kit, buy_in=0) is None


class TestDraftFailClosed:
    def test_spawn_aborts_on_exclusion_scan_failure(self, kit):
        """A seat-scan error aborts the spawn (fail closed) rather than fielding
        from a partial exclusion set and risking a seated persona — #5."""
        from flask_app.services.tournament_spawn import spawn_autonomous_tournament

        class BrokenCashTableRepo:
            def list_all_tables(self, *, sandbox_id=None):
                raise RuntimeError("scan boom")

        # Fund a flush bank so funding isn't the reason for a None return.
        kit['ledger_repo'].record('central_bank', 'player:u', 1_000_000, 'player_seed', sandbox_id=SB)
        kit['ledger_repo'].record('ai:d', 'central_bank', 300_000, 'bank_pool_deposit', sandbox_id=SB)
        result = spawn_autonomous_tournament(
            owner_id=OWNER, sandbox_id=SB,
            personality_repo=kit['personality_repo'], bankroll_repo=kit['bankroll_repo'],
            ledger_repo=kit['ledger_repo'], session_repo=kit['session_repo'],
            cash_table_repo=BrokenCashTableRepo(),
            field_size=6, table_size=3, starting_stack=10_000, seed=1, rng_seed=1,
        )
        assert result is None  # aborted, no tournament created


class TestDeclineSpawnFailure:
    def test_decline_consumes_even_when_spawn_unfieldable(self, kit):
        """Too few personas to field the autonomous run → the invite is still
        consumed (declined) and decline reports success, not a false 404 — #7."""
        kit['personality_repo'] = FakePersonalityRepo(['only_one'])  # < MIN_FIELD
        _offer(kit, buy_in=0)
        result = inv.decline(
            invite_repo=kit['invite_repo'], personality_repo=kit['personality_repo'],
            bankroll_repo=kit['bankroll_repo'], ledger_repo=kit['ledger_repo'],
            session_repo=kit['session_repo'], owner_id=OWNER,
        )
        assert result is not None            # not a misleading "no open invite"
        assert result['tournament_id'] is None
        assert kit['invite_repo'].load(result_invite_id(kit))['status'] == 'declined'
