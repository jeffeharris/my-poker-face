"""P1 schema + repos for async-friends mode.

Covers the additive migration `20260612_1200_async_friends` and the three
repository surfaces it backs: membership/seat ledger, push devices, and the
denormalized turn-state columns on `games`. No Flask, no LLM — pure DB.
"""

from __future__ import annotations

import sqlite3

import pytest

from poker.repositories.device_repository import DeviceRepository
from poker.repositories.game_repository import GameRepository
from poker.repositories.membership_repository import MembershipRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db(db_path):
    """A fresh DB with the full schema (incl. the async-friends migration)."""
    SchemaManager(db_path).ensure_schema()
    return db_path


def _insert_game(db_path: str, game_id: str, owner_id: str = "owner-1") -> None:
    """Minimal `games` row so membership/turn writes have a parent to reference."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO games (game_id, phase, num_players, pot_size, game_state_json, owner_id)
            VALUES (?, 'PRE_FLOP', 4, 0, '{}', ?)
            """,
            (game_id, owner_id),
        )
        conn.commit()
    finally:
        conn.close()


# --- Migration shape ---


def test_migration_creates_tables_and_columns(db):
    conn = sqlite3.connect(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"game_members", "game_invites", "user_devices"} <= tables

        game_cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
        assert {
            "is_async",
            "current_turn_user_id",
            "turn_started_at",
            "turn_deadline",
            "last_notified_turn_at",
        } <= game_cols
    finally:
        conn.close()


# --- Membership ---


def test_add_member_and_is_member(db):
    _insert_game(db, "g1")
    repo = MembershipRepository(db)
    assert repo.is_member("g1", "alice") is False

    repo.add_member("g1", "alice", seat_index=0, role="owner", display_name="Alice")
    assert repo.is_member("g1", "alice") is True

    m = repo.get_member("g1", "alice")
    assert m is not None
    assert m.seat_index == 0 and m.role == "owner" and m.display_name == "Alice"


def test_add_member_is_idempotent_upsert(db):
    _insert_game(db, "g1")
    repo = MembershipRepository(db)
    repo.add_member("g1", "bob", status="invited")
    repo.claim_seat("g1", "bob", seat_index=2, display_name="Bob")

    members = repo.list_members("g1")
    assert len(members) == 1
    assert members[0].seat_index == 2 and members[0].status == "joined"


def test_left_member_is_not_a_member(db):
    _insert_game(db, "g1")
    repo = MembershipRepository(db)
    repo.add_member("g1", "carol", seat_index=1, status="left")
    assert repo.is_member("g1", "carol") is False


def test_seat_taken_and_list_user_games(db):
    _insert_game(db, "g1")
    _insert_game(db, "g2")
    repo = MembershipRepository(db)
    repo.claim_seat("g1", "alice", seat_index=0)
    repo.claim_seat("g2", "alice", seat_index=3)

    assert repo.seat_taken("g1", 0) is True
    assert repo.seat_taken("g1", 1) is False
    assert set(repo.list_user_games("alice")) == {"g1", "g2"}


def test_invite_create_get_consume(db):
    _insert_game(db, "g1")
    repo = MembershipRepository(db)
    code = repo.create_invite("g1", created_by="owner-1")
    assert code

    inv = repo.get_invite(code)
    assert inv is not None and inv["game_id"] == "g1" and inv["used_count"] == 0

    repo.consume_invite(code)
    assert repo.get_invite(code)["used_count"] == 1


# --- Devices ---


def test_device_register_upsert_and_list(db):
    repo = DeviceRepository(db)
    repo.register("alice", "ios", "tok-A")
    repo.register("alice", "ios", "tok-B")
    repo.register("alice", "ios", "tok-A")  # re-register same token -> upsert, no dup

    devices = repo.list_devices("alice")
    assert {d.token for d in devices} == {"tok-A", "tok-B"}

    repo.remove("alice", "tok-A")
    assert {d.token for d in repo.list_devices("alice")} == {"tok-B"}


# --- Turn state on games ---


def test_turn_state_roundtrip_and_clock(db):
    _insert_game(db, "g1")
    repo = GameRepository(db)

    repo.set_async_flag("g1", True)
    repo.set_turn_state("g1", "alice", advance_turn_clock=True)
    meta = repo.get_async_meta("g1")
    assert meta["is_async"] is True
    assert meta["current_turn_user_id"] == "alice"
    assert meta["turn_started_at"] is not None
    assert meta["last_notified_turn_at"] is None

    # Notifying stamps the dedupe column.
    repo.mark_turn_notified("g1")
    assert repo.get_async_meta("g1")["last_notified_turn_at"] is not None

    # A non-advancing refresh keeps the notify stamp (no re-arm).
    repo.set_turn_state("g1", "alice", advance_turn_clock=False)
    assert repo.get_async_meta("g1")["last_notified_turn_at"] is not None

    # Advancing to a new actor re-arms (clears the notify stamp).
    repo.set_turn_state("g1", "bob", advance_turn_clock=True)
    meta = repo.get_async_meta("g1")
    assert meta["current_turn_user_id"] == "bob"
    assert meta["last_notified_turn_at"] is None


def test_get_async_meta_missing_game(db):
    assert GameRepository(db).get_async_meta("nope") is None
