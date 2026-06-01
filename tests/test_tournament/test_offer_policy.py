"""Tests for the chairman-driven offer trigger (P3.5 — cadence).

The chairman decides WHETHER to run a tournament (FLUSH = time to distribute),
not a calendar. Covers the pure policy + the orchestrator that consumes it.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.economy import economy_signal as chair
from core.economy.economy_signal import (
    DEFAULT_MAIN_EVENT,
    EMPTY,
    FLUSH,
    NEUTRAL,
    EconomyState,
    should_offer_event,
)
from flask_app.services import tournament_invites as inv
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_invite_repository import TournamentInviteRepository
from poker.repositories.tournament_session_repository import TournamentSessionRepository

SB = 'sb-offer'
OWNER = 'owner-offer'


def _state(regime):
    return EconomyState(reserves=100, holdings=1000, ratio=0.1, regime=regime)


class TestShouldOfferEvent:
    def test_flush_and_cooldown_ok_offers(self):
        assert should_offer_event(_state(FLUSH), cooldown_elapsed=True) == DEFAULT_MAIN_EVENT

    def test_flush_but_on_cooldown_holds(self):
        assert should_offer_event(_state(FLUSH), cooldown_elapsed=False) is None

    def test_neutral_never_offers(self):
        assert should_offer_event(_state(NEUTRAL), cooldown_elapsed=True) is None

    def test_empty_never_offers_in_v1(self):
        assert should_offer_event(_state(EMPTY), cooldown_elapsed=True) is None


@pytest.fixture
def kit():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "econ.db")
        SchemaManager(path).ensure_schema()
        repos = dict(
            ledger_repo=ChipLedgerRepository(path),
            session_repo=TournamentSessionRepository(path),
            invite_repo=TournamentInviteRepository(path),
        )
        yield repos
        for r in repos.values():
            r.close()


def _make_flush(ledger_repo):
    ledger_repo.record('central_bank', 'player:u', 1_000_000, 'player_seed', sandbox_id=SB)
    ledger_repo.record('ai:d', 'central_bank', 300_000, 'bank_pool_deposit', sandbox_id=SB)


class TestMaybeOfferMainEvent:
    def test_flush_offers_an_invite(self, kit):
        _make_flush(kit['ledger_repo'])
        invite = inv.maybe_offer_main_event(
            invite_repo=kit['invite_repo'], session_repo=kit['session_repo'],
            ledger_repo=kit['ledger_repo'], owner_id=OWNER, sandbox_id=SB,
        )
        assert invite is not None
        assert invite['status'] == 'offered'
        assert inv.active_invite(kit['invite_repo'], OWNER) is not None

    def test_neutral_offers_nothing(self, kit):
        # Cold ledger → NEUTRAL → no event.
        invite = inv.maybe_offer_main_event(
            invite_repo=kit['invite_repo'], session_repo=kit['session_repo'],
            ledger_repo=kit['ledger_repo'], owner_id=OWNER, sandbox_id=SB,
        )
        assert invite is None

    def test_cooldown_blocks_back_to_back(self, kit):
        _make_flush(kit['ledger_repo'])
        first = inv.maybe_offer_main_event(
            invite_repo=kit['invite_repo'], session_repo=kit['session_repo'],
            ledger_repo=kit['ledger_repo'], owner_id=OWNER, sandbox_id=SB,
        )
        assert first is not None
        # Resolve it so the "one open at a time" guard isn't what's blocking — we
        # want to prove the COOLDOWN blocks a fresh offer right after.
        kit['invite_repo'].resolve(first['invite_id'], status='declined')
        again = inv.maybe_offer_main_event(
            invite_repo=kit['invite_repo'], session_repo=kit['session_repo'],
            ledger_repo=kit['ledger_repo'], owner_id=OWNER, sandbox_id=SB,
            now=datetime.utcnow(),  # ~immediately after → within cooldown
        )
        assert again is None

    def test_offer_again_after_cooldown(self, kit):
        _make_flush(kit['ledger_repo'])
        first = inv.maybe_offer_main_event(
            invite_repo=kit['invite_repo'], session_repo=kit['session_repo'],
            ledger_repo=kit['ledger_repo'], owner_id=OWNER, sandbox_id=SB,
        )
        kit['invite_repo'].resolve(first['invite_id'], status='declined')
        future = datetime.utcnow() + timedelta(seconds=chair.MAIN_EVENT_COOLDOWN_SECONDS + 60)
        again = inv.maybe_offer_main_event(
            invite_repo=kit['invite_repo'], session_repo=kit['session_repo'],
            ledger_repo=kit['ledger_repo'], owner_id=OWNER, sandbox_id=SB,
            now=future,
        )
        assert again is not None

    def test_expiry_window_stamped(self, kit):
        _make_flush(kit['ledger_repo'])
        invite = inv.maybe_offer_main_event(
            invite_repo=kit['invite_repo'], session_repo=kit['session_repo'],
            ledger_repo=kit['ledger_repo'], owner_id=OWNER, sandbox_id=SB,
            expiry_seconds=3600,
        )
        assert invite['expires_at'] is not None
