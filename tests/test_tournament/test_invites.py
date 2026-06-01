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
from flask_app.services import tournament_economy_service as econ
from flask_app.services import tournament_invites as inv
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
