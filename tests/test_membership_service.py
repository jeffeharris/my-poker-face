"""P2 membership/turn authorization service.

Verifies the swap from "owner only" to "any seated member, on their own turn":
owner back-compat (with and without a ledger row), ledger members, admin
bypass, non-member denial, and per-turn resolution from live seat identity.
"""

from __future__ import annotations

import sqlite3

import pytest

from flask_app import extensions
from flask_app.services import membership_service
from poker.repositories.game_repository import GameRepository
from poker.repositories.membership_repository import MembershipRepository
from poker.repositories.schema_manager import SchemaManager
from poker.table.seat import HumanSeat, PersonaSeat

pytestmark = pytest.mark.flask


@pytest.fixture
def wired(db_path, monkeypatch):
    """Temp DB with membership + game repos wired into extensions."""
    SchemaManager(db_path).ensure_schema()
    membership_repo = MembershipRepository(db_path)
    game_repo = GameRepository(db_path)
    monkeypatch.setattr(extensions, "membership_repo", membership_repo)
    monkeypatch.setattr(extensions, "game_repo", game_repo)
    # Default: nobody is an admin unless a test opts in.
    monkeypatch.setattr(membership_service, "_is_admin", lambda uid: False)

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO games (game_id, phase, num_players, pot_size, game_state_json, owner_id)
        VALUES ('g1', 'PRE_FLOP', 4, 0, '{}', 'owner-1')
        """
    )
    conn.commit()
    conn.close()
    return membership_repo


# --- is_member ---


def test_owner_is_member_via_passed_owner_id(wired):
    assert membership_service.is_member("g1", "owner-1", owner_id="owner-1") is True


def test_owner_is_member_via_db_fallback(wired):
    # owner_id not supplied and no ledger row -> falls back to the persisted
    # owner, so a legacy single-human game still authorizes its owner.
    assert membership_service.is_member("g1", "owner-1") is True


def test_seated_friend_is_member_via_ledger(wired):
    wired.claim_seat("g1", "alice", seat_index=1)
    assert membership_service.is_member("g1", "alice", owner_id="owner-1") is True


def test_non_member_denied(wired):
    assert membership_service.is_member("g1", "stranger", owner_id="owner-1") is False


def test_left_member_denied(wired):
    wired.add_member("g1", "alice", seat_index=1, status="left")
    assert membership_service.is_member("g1", "alice", owner_id="owner-1") is False


def test_admin_bypass(wired, monkeypatch):
    monkeypatch.setattr(membership_service, "_is_admin", lambda uid: uid == "root")
    assert membership_service.is_member("g1", "root", owner_id="owner-1") is True


def test_no_user_denied(wired):
    assert membership_service.is_member("g1", None, owner_id="owner-1") is False


# --- turn resolution ---


class _Seat:
    def __init__(self, is_human, key):
        self.is_human = is_human
        self.seat_id = HumanSeat(key) if is_human else PersonaSeat(key)


class _GameState:
    def __init__(self, players, idx, awaiting=True):
        self.players = players
        self.current_player_idx = idx
        self.awaiting_action = awaiting


def test_resolve_turn_user_human_seat():
    gs = _GameState([_Seat(False, "ai"), _Seat(True, "alice")], idx=1)
    assert membership_service.resolve_turn_user(gs) == "alice"
    assert membership_service.is_users_turn(gs, "alice") is True
    assert membership_service.is_users_turn(gs, "bob") is False


def test_resolve_turn_user_none_when_ai_to_act():
    gs = _GameState([_Seat(False, "ai"), _Seat(True, "alice")], idx=0)
    assert membership_service.resolve_turn_user(gs) is None


def test_resolve_turn_user_none_when_not_awaiting():
    gs = _GameState([_Seat(True, "alice")], idx=0, awaiting=False)
    assert membership_service.resolve_turn_user(gs) is None
