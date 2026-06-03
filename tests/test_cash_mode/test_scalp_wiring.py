"""Tests for the human-table scalp wiring (game_handler._record_cash_scalps, §3b).

Imports game_handler (the Flask handler stack), so integration-marked. The
helper reads `extensions.cash_scalps_repo` live; we point it at a temp repo,
feed fake game_state/game_data, and assert the recorded (eliminator → victim)
rows for the headline-winner attribution + the documented skips.
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration

from flask_app import extensions  # noqa: E402
from flask_app.handlers import game_handler  # noqa: E402
from poker.repositories import create_repos  # noqa: E402


def _player(name, stack, is_human=False):
    return SimpleNamespace(name=name, stack=stack, is_human=is_human)


def _gd():
    return {
        "sandbox_id": "sb",
        "owner_id": "guest_jeff",
        "cash_personality_ids": {"Deadpool": "deadpool", "Batman": "batman"},
    }


class _Ctx:
    """Point extensions.cash_scalps_repo at a fresh temp repo, restore after."""

    def __enter__(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repos(self.tmp.name)["cash_scalps_repo"]
        self._orig = getattr(extensions, "cash_scalps_repo", None)
        extensions.cash_scalps_repo = self.repo
        return self.repo

    def __exit__(self, *a):
        extensions.cash_scalps_repo = self._orig
        try:
            os.unlink(self.tmp.name)
        except FileNotFoundError:
            pass


def test_human_winner_scalps_busted_ai():
    with _Ctx() as repo:
        gs = SimpleNamespace(players=[
            _player("Jeff", 8000, is_human=True),
            _player("Deadpool", 0),      # busted
            _player("Batman", 3000),     # survives
        ])
        game_handler._record_cash_scalps(_gd(), gs, "Jeff")
        assert repo.list_for_eliminator("sb", "guest_jeff") == [("deadpool", 1)]


def test_ai_winner_scalps_other_busted_ai():
    # AI-vs-AI bust at the human's table is recorded too (headline-winner rule).
    with _Ctx() as repo:
        gs = SimpleNamespace(players=[
            _player("Jeff", 2000, is_human=True),
            _player("Batman", 9000),     # AI winner
            _player("Deadpool", 0),      # busted by Batman
        ])
        game_handler._record_cash_scalps(_gd(), gs, "Batman")
        assert repo.list_for_eliminator("sb", "batman") == [("deadpool", 1)]


def test_human_victim_is_not_a_scalp():
    # The human hitting 0 is not a scalp victim (they leave, don't bust out).
    with _Ctx() as repo:
        gs = SimpleNamespace(players=[
            _player("Jeff", 0, is_human=True),   # human at 0 — excluded
            _player("Batman", 9000),
        ])
        game_handler._record_cash_scalps(_gd(), gs, "Batman")
        assert repo.total_for("sb", "batman") == 0


def test_no_busts_records_nothing():
    with _Ctx() as repo:
        gs = SimpleNamespace(players=[
            _player("Jeff", 5000, is_human=True),
            _player("Deadpool", 4000),
        ])
        game_handler._record_cash_scalps(_gd(), gs, "Jeff")
        assert repo.total_for("sb", "guest_jeff") == 0


def test_unmapped_winner_skips():
    # Winner not in cash_personality_ids and not human → no eliminator → no-op.
    with _Ctx() as repo:
        gs = SimpleNamespace(players=[
            _player("Ghost", 9000),       # not in cash_pids, not human
            _player("Deadpool", 0),
        ])
        game_handler._record_cash_scalps(_gd(), gs, "Ghost")
        assert repo.victims_of("sb", "deadpool") == []
