"""End-to-end: the Act-1 keyring through `GET /api/cash/lobby`.

Asserts the thesis of M1 — a brand-new sandbox sees ONLY the pinned Scene-0
table (Sal + the fish + you), not the full cardroom grid. Mirrors the harness in
`test_cash_lobby_route.py` but additionally wires `career_progress_repo` and
seeds the authored Scene-0 personas so the keyring is live. See
`cash_mode/career_progression.py`.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from cash_mode import career_progression as cp
from flask_app import create_app
from poker.repositories import create_repos

pytestmark = [pytest.mark.flask, pytest.mark.integration]

PLAYER_OWNER_ID = "career-player-1"


def _mock_authorization_service(user):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = True
    return authz


class TestCareerKeyringLobby(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        cls.test_db.close()
        repos = create_repos(cls.test_db.name)
        cls.repos = repos
        # The authored Scene-0 cast must exist as real personas (non-circulating).
        repos['personality_repo'].seed_personalities_from_json('poker/personalities.json')

        import flask_app.extensions as ext

        # Snapshot the extension globals we're about to clobber so tearDownClass
        # can restore them — these are module-level singletons, and leaving our
        # tempdb repos (esp. the new career_progress_repo) set would pollute
        # later tests whose harness expects them None (the xdist import-ordering
        # gotcha in tests/CLAUDE.md).
        cls._ext_keys = [k for k in repos if k != 'db_path'] + ['persistence_db_path']
        cls._ext_snapshot = {k: getattr(ext, k, None) for k in cls._ext_keys}

        def mock_init_persistence():
            for key, val in repos.items():
                if key == 'db_path':
                    continue
                setattr(ext, key, val)
            ext.persistence_db_path = repos['db_path']

        from tests._sandbox_test_helper import pin_sandbox_for

        pin_sandbox_for(PLAYER_OWNER_ID, repos['sandbox_repo'])

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            cls.app = create_app()
        cls.app.testing = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        import flask_app.extensions as ext

        for k, v in cls._ext_snapshot.items():
            setattr(ext, k, v)
        try:
            os.unlink(cls.test_db.name)
        except FileNotFoundError:
            pass

    def setUp(self):
        user = {'id': PLAYER_OWNER_ID, 'name': 'Career Tester'}
        self._authz_patcher = patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(user=user),
        )
        self._authz_patcher.start()
        auth_mock = MagicMock()
        auth_mock.get_current_user.return_value = user
        self._auth_patcher = patch('flask_app.extensions.auth_manager', auth_mock)
        self._auth_patcher.start()

    def tearDown(self):
        self._auth_patcher.stop()
        self._authz_patcher.stop()

    def test_brand_new_player_sees_only_scene0(self):
        resp = self.client.get("/api/cash/lobby")
        assert resp.status_code == 200
        data = resp.get_json()
        tables = data["tables"]
        # The whole world (11 cardrooms) was seeded behind the scenes, but the
        # keyring filters the view down to the single pinned tutorial table.
        assert len(tables) == 1, [t["table_id"] for t in tables]
        only = tables[0]
        assert only["table_id"] == cp.SCENE0_TABLE_ID
        assert only["table_type"] == "scripted"
        assert only["table_name"] == cp.SCENE0_TABLE_NAME
        # Sal + the fish are seated; the rest of the chairs are open for the player.
        ai_pids = {s.get("personality_id") for s in only["seats"] if s.get("kind") == "ai"}
        assert cp.SAL_ID in ai_pids
        assert cp.SCENE0_FISH_ID in ai_pids

    def test_keyring_is_idempotent_across_loads(self):
        # A second load must not re-seed or duplicate the Scene-0 table.
        self.client.get("/api/cash/lobby")
        data = self.client.get("/api/cash/lobby").get_json()
        scene0 = [t for t in data["tables"] if t["table_id"] == cp.SCENE0_TABLE_ID]
        assert len(scene0) == 1
        # Persisted progress reflects an active keyring with the Scene-0 reveal.
        prog = self.repos['career_progress_repo'].load(
            self.repos['sandbox_repo'].list_for_owner(PLAYER_OWNER_ID)[0].sandbox_id,
            PLAYER_OWNER_ID,
        )
        assert prog.career_active is True
        assert prog.scene0_seeded is True
        assert cp.SCENE0_TABLE_ID in prog.revealed_table_ids

    def test_mentor_intro_handoff_is_served_once_then_cleared(self):
        # Simulate "just graduated": the first vouch queued Sal's lobby handoff.
        repo = self.repos['career_progress_repo']
        sb = self.repos['sandbox_repo'].list_for_owner(PLAYER_OWNER_ID)[0].sandbox_id
        prog = repo.load(sb, PLAYER_OWNER_ID)
        prog.mentor_intro_table_id = "cash-table-3-001"
        repo.save(prog)

        first = self.client.get("/api/cash/lobby").get_json()
        assert first["mentor_intro"] is not None
        assert first["mentor_intro"]["table_id"] == "cash-table-3-001"
        assert first["mentor_intro"]["name"] == cp.SAL_NAME
        assert first["mentor_intro"]["line"]

        # One-shot: cleared after the first serve so it doesn't replay.
        second = self.client.get("/api/cash/lobby").get_json()
        assert second["mentor_intro"] is None
        assert repo.load(sb, PLAYER_OWNER_ID).mentor_intro_table_id is None

    def test_intake_christens_fish_name_and_clears_the_gate(self):
        # Brand-new career player → the lobby asks for the intake first.
        first = self.client.get("/api/cash/lobby").get_json()
        assert first["intake_needed"] is True
        # Submit the cold-open: name + table-talk vibe → christened a handle + bio.
        res = self.client.post(
            "/api/cash/intake", json={"name": "Jeff", "intensity": "spicy", "style": "needle"}
        )
        body = res.get_json()
        assert body["player_name"] == "Jeff"
        assert body["fish_name"]  # LLM- or fallback-generated handle
        assert body["intensity"] == "spicy"
        assert "avatar_prompt" in body  # the avatar seam is present
        # Intake is now done → the gate clears and the handle is surfaced.
        after = self.client.get("/api/cash/lobby").get_json()
        assert after["intake_needed"] is False
        assert after["fish_name"] == body["fish_name"]
