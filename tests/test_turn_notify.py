"""P4 turn-state mirroring + notification policy.

Verifies notify_turn_if_offline: pushes exactly once for an offline player's
turn, skips when they're connected, dedupes within a turn, re-arms on a new
turn, and that the dispatcher prunes dead tokens.
"""

from __future__ import annotations

import sqlite3

import pytest

from flask_app import extensions
from flask_app.services import turn_notify
from flask_app.services.notifications import dispatcher
from flask_app.services.notifications.channel import NotificationChannel
from poker.repositories.device_repository import DeviceRepository
from poker.repositories.game_repository import GameRepository
from poker.repositories.schema_manager import SchemaManager
from poker.table.seat import HumanSeat, PersonaSeat

pytestmark = pytest.mark.flask


class _FakeChannel(NotificationChannel):
    platform = 'ios'

    def __init__(self, *, prune=False):
        self.sent = []
        self._prune = prune

    def send(self, token, notification):
        self.sent.append((token, notification))
        return not self._prune  # False => caller prunes the token


class _Seat:
    def __init__(self, is_human, key):
        self.is_human = is_human
        self.seat_id = HumanSeat(key) if is_human else PersonaSeat(key)


class _GameState:
    def __init__(self, turn_owner):
        self.awaiting_action = True
        self.current_player_idx = 1
        self.players = [_Seat(False, 'ai'), _Seat(True, turn_owner)]


@pytest.fixture
def wired(db_path, monkeypatch):
    SchemaManager(db_path).ensure_schema()
    game_repo = GameRepository(db_path)
    device_repo = DeviceRepository(db_path)
    monkeypatch.setattr(extensions, "game_repo", game_repo)
    monkeypatch.setattr(extensions, "device_repo", device_repo)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO games (game_id, phase, num_players, pot_size, game_state_json, owner_id, is_async) "
        "VALUES ('g1','PRE_FLOP',2,0,'{}','owner-1',1)"
    )
    conn.commit()
    conn.close()

    device_repo.register("alice", "ios", "tok-A")
    channel = _FakeChannel()
    dispatcher.set_channels_for_test({'ios': channel})
    # Default: alice is offline unless a test says otherwise.
    monkeypatch.setattr("flask_app.services.presence.is_active", lambda uid: False)
    yield game_repo, device_repo, channel
    dispatcher._channels = None  # reset the cached channel registry


def test_offline_player_is_notified_once(wired):
    game_repo, _devices, channel = wired
    gs = _GameState("alice")

    assert turn_notify.notify_turn_if_offline("g1", gs) is True
    assert len(channel.sent) == 1
    assert channel.sent[0][1].data['game_id'] == 'g1'

    # Same turn again -> deduped, no second push.
    assert turn_notify.notify_turn_if_offline("g1", gs) is False
    assert len(channel.sent) == 1


def test_connected_player_is_not_notified(wired, monkeypatch):
    _game_repo, _devices, channel = wired
    monkeypatch.setattr("flask_app.services.presence.is_active", lambda uid: True)
    assert turn_notify.notify_turn_if_offline("g1", _GameState("alice")) is False
    assert channel.sent == []


def test_new_turn_rearms_notification(wired):
    game_repo, device_repo, channel = wired
    device_repo.register("bob", "ios", "tok-B")

    assert turn_notify.notify_turn_if_offline("g1", _GameState("alice")) is True
    # Turn moves to bob -> clock advances, dedupe clears, bob gets his own push.
    assert turn_notify.notify_turn_if_offline("g1", _GameState("bob")) is True
    assert len(channel.sent) == 2
    assert {t for t, _ in channel.sent} == {"tok-A", "tok-B"}


def test_no_human_on_clock_is_noop(wired):
    _game_repo, _devices, channel = wired

    class _AiTurn:
        awaiting_action = True
        current_player_idx = 0
        players = [_Seat(False, 'ai'), _Seat(True, 'alice')]

    assert turn_notify.notify_turn_if_offline("g1", _AiTurn()) is False
    assert channel.sent == []


def test_dispatcher_prunes_dead_token(wired):
    game_repo, device_repo, _channel = wired
    dispatcher.set_channels_for_test({'ios': _FakeChannel(prune=True)})

    delivered = dispatcher.notify_turn("g1", "alice")
    assert delivered == 0
    # The 410-style token was pruned.
    assert device_repo.list_devices("alice") == []
