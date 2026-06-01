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

    def test_graduation_returns_the_comp_to_the_pool(self):
        # You were comped as a fish; on graduation the house takes it back, so you
        # land in the lobby at 0 and the comp moves to the bank pool (conserved).
        from cash_mode.closed_economy import compute_bank_pool_reserves
        from poker.repositories.bankroll_repository import PlayerBankrollState

        repo = self.repos['career_progress_repo']
        sb = self.repos['sandbox_repo'].list_for_owner(PLAYER_OWNER_ID)[0].sandbox_id
        prog = repo.load(sb, PLAYER_OWNER_ID)
        prog.career_active = True
        prog.tutorial_complete = True
        prog.comp_returned = False
        repo.save(prog)
        # Give them a comp to hand back (no active session in this harness).
        self.repos['bankroll_repo'].save_player_bankroll(
            PlayerBankrollState(player_id=PLAYER_OWNER_ID, chips=150, starting_bankroll=200)
        )
        pool_before = compute_bank_pool_reserves(self.repos['chip_ledger_repo'], sandbox_id=sb)

        data = self.client.get("/api/cash/lobby").get_json()

        assert data["bankroll"] == 0  # walked in with nothing
        after = repo.load(sb, PLAYER_OWNER_ID)
        assert after.comp_returned is True
        pool_after = compute_bank_pool_reserves(self.repos['chip_ledger_repo'], sandbox_id=sb)
        assert pool_after - pool_before == 150  # conserved: the comp moved to the pool

        # One-shot: a second load doesn't strip again (bankroll stays 0, no re-deposit).
        self.client.get("/api/cash/lobby")
        assert compute_bank_pool_reserves(self.repos['chip_ledger_repo'], sandbox_id=sb) == pool_after

    def test_scene_top_up_rebuys_the_short_fish_from_its_bankroll(self):
        """If the hero short-stacks Larry, the cast top-up rebuys him from his own
        (sandbox-scoped) bankroll so he can still play the script — no minting."""
        from cash_mode.bankroll import AIBankrollState
        from cash_mode.career_progression import SAL_ID, SCENE0_FISH_ID
        from flask_app.handlers import game_handler as gh
        from poker.poker_game import initialize_game_state
        from poker.poker_state_machine import PokerStateMachine

        sb = self.repos['sandbox_repo'].list_for_owner(PLAYER_OWNER_ID)[0].sandbox_id
        self.repos['bankroll_repo'].save_ai_bankroll(
            AIBankrollState(personality_id=SCENE0_FISH_ID, chips=5000),
            sandbox_id=sb,
            chip_ledger_repo=self.repos['chip_ledger_repo'],
        )
        before = self.repos['bankroll_repo'].load_ai_bankroll(SCENE0_FISH_ID, sandbox_id=sb).chips

        gs = initialize_game_state(player_names=["Sal Monroe", "Loose Larry"], human_name="You")
        gs = gs.update(
            players=tuple(
                p.update(stack=10) if p.name == "Loose Larry" else p for p in gs.players
            )
        )
        sm = PokerStateMachine(game_state=gs)
        game_data = {
            'sandbox_id': sb,
            'state_machine': sm,
            'cash_personality_ids': {"Sal Monroe": SAL_ID, "Loose Larry": SCENE0_FISH_ID},
            'scene_roles': {'hero': "You", 'mentor': "Sal Monroe", 'fish': "Loose Larry"},
        }
        gh._scene_top_up_cast("g-topup", game_data, sm)

        larry = next(
            p for p in game_data['state_machine'].game_state.players if p.name == "Loose Larry"
        )
        assert larry.stack == 160  # $2 table → fish target = 2 × min_buy_in
        after = self.repos['bankroll_repo'].load_ai_bankroll(SCENE0_FISH_ID, sandbox_id=sb).chips
        # Conservation: the +150 to the seat came OUT of Larry's bankroll (no mint).
        assert after < before
        assert before - after >= 100

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
        # Submit the cold-open: name + the reply they picked → christened a handle
        # + bio. The reply is plain flavor (no setting mapping); it's remembered as
        # a callback hook.
        res = self.client.post(
            "/api/cash/intake",
            json={
                "name": "Jeff",
                "reply": "Folks say I'm hard to read. Never did know what they meant by it.",
                "reply_id": "hard_to_read",
            },
        )
        body = res.get_json()
        assert body["player_name"] == "Jeff"
        assert body["fish_name"]  # LLM- or fallback-generated handle
        assert "intensity" not in body  # decoupled from quick-chat
        assert "avatar_prompt" in body  # the avatar seam is present
        # The reply is persisted verbatim (+ its id) for later narrative callbacks.
        sb = self.repos['sandbox_repo'].list_for_owner(PLAYER_OWNER_ID)[0].sandbox_id
        prog = self.repos['career_progress_repo'].load(sb, PLAYER_OWNER_ID)
        assert prog.intake_reply_id == "hard_to_read"
        assert "hard to read" in (prog.intake_reply or "")
        # Intake is now done → the gate clears and the handle is surfaced.
        after = self.client.get("/api/cash/lobby").get_json()
        assert after["intake_needed"] is False
        assert after["fish_name"] == body["fish_name"]


MENTOR_OWNER_ID = "career-mentor-1"


class TestCareerMentorStake(unittest.TestCase):
    """The mentor stake — the comp-return's other half: non-circulating Sal backs
    the graduate's first real seat at their home court.

    Its own fresh DB (NOT the keyring class's shared one): the funded sit creates
    a live cash session + world churn, and this suite runs under pytest-randomly,
    so sharing state would intermittently pollute the keyring pool assertions.
    """

    @classmethod
    def setUpClass(cls):
        cls.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        cls.test_db.close()
        repos = create_repos(cls.test_db.name)
        cls.repos = repos
        repos['personality_repo'].seed_personalities_from_json('poker/personalities.json')

        import flask_app.extensions as ext

        cls._ext_keys = [k for k in repos if k != 'db_path'] + ['persistence_db_path']
        cls._ext_snapshot = {k: getattr(ext, k, None) for k in cls._ext_keys}

        def mock_init_persistence():
            for key, val in repos.items():
                if key == 'db_path':
                    continue
                setattr(ext, key, val)
            ext.persistence_db_path = repos['db_path']

        from tests._sandbox_test_helper import pin_sandbox_for

        pin_sandbox_for(MENTOR_OWNER_ID, repos['sandbox_repo'])

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
        user = {'id': MENTOR_OWNER_ID, 'name': 'Mentor Tester'}
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
        # The funded sit registers a live `cash-*` game in game_state_service — a
        # MODULE-LEVEL in-memory store shared across ALL tests regardless of DB.
        # Left behind, it leaks into other suites' global live-session scans, so
        # evict any game this owner created here.
        from flask_app.services import game_state_service

        for gid in list(game_state_service.list_game_ids()):
            data = game_state_service.get_game(gid)
            if data and data.get('owner_id') == MENTOR_OWNER_ID:
                game_state_service.delete_game(gid)

    def _sandbox_id(self):
        return self.repos['sandbox_repo'].list_for_owner(MENTOR_OWNER_ID)[0].sandbox_id

    def _home_court(self, sb):
        """Seed the world (via a lobby load) and return a real $2 home-court table."""
        self.client.get("/api/cash/lobby")
        return next(
            t
            for t in self.repos['cash_table_repo'].list_all_tables(sandbox_id=sb)
            if t.stake_label == cp.HOME_COURT_STAKE
        )

    def _set_graduated_broke(self, sb, home_table_id):
        """Mark the player graduated, broke, and pinned to a revealed home court."""
        from poker.repositories.bankroll_repository import PlayerBankrollState

        repo = self.repos['career_progress_repo']
        prog = repo.load(sb, MENTOR_OWNER_ID)
        prog.career_active = True
        prog.tutorial_complete = True
        prog.comp_returned = True  # already handed the comp back → don't re-trigger
        prog.mentor_stake_used = False
        prog.home_court_table_id = home_table_id
        if home_table_id not in prog.revealed_table_ids:
            prog.revealed_table_ids.append(home_table_id)
        repo.save(prog)
        self.repos['bankroll_repo'].save_player_bankroll(
            PlayerBankrollState(player_id=MENTOR_OWNER_ID, chips=0, starting_bankroll=200)
        )

    def test_lobby_offers_mentor_stake_when_graduated_and_broke(self):
        sb = self._sandbox_id()
        home = self._home_court(sb)
        self._set_graduated_broke(sb, home.table_id)

        ms = self.client.get("/api/cash/lobby").get_json()["mentor_stake"]
        assert ms is not None
        assert ms["lender_id"] == cp.SAL_ID
        assert ms["lender_name"] == cp.SAL_NAME
        assert ms["table_id"] == home.table_id
        assert ms["stake_label"] == cp.HOME_COURT_STAKE

        # One-shot: once Sal's stake is spent the offer stops surfacing.
        repo = self.repos['career_progress_repo']
        prog = repo.load(sb, MENTOR_OWNER_ID)
        prog.mentor_stake_used = True
        repo.save(prog)
        assert self.client.get("/api/cash/lobby").get_json()["mentor_stake"] is None

    def test_lobby_self_heals_a_lingering_scene0_session_and_unwedges_handoff(self):
        """A graduated player still 'seated' at the finished Scene-0 table (the
        frontend leave didn't land) must not stay wedged: the lobby closes that
        session so the comp-return + mentor stake fire. Mocks the stuck session +
        the leave teardown; asserts the handoff goes through."""
        from unittest.mock import patch

        from poker.repositories.bankroll_repository import PlayerBankrollState

        sb = self._sandbox_id()
        home = self._home_court(sb)
        repo = self.repos['career_progress_repo']
        prog = repo.load(sb, MENTOR_OWNER_ID)
        prog.career_active = True
        prog.tutorial_complete = True
        prog.comp_returned = False  # the comp hasn't been swept yet
        prog.mentor_stake_used = False
        prog.home_court_table_id = home.table_id
        if home.table_id not in prog.revealed_table_ids:
            prog.revealed_table_ids.append(home.table_id)
        repo.save(prog)
        # The comped chips are still in bankroll (as if the leave settled them).
        self.repos['bankroll_repo'].save_player_bankroll(
            PlayerBankrollState(player_id=MENTOR_OWNER_ID, chips=200, starting_bankroll=200)
        )

        stuck_id = "cash-stuck-scene0"
        leave_calls = []

        def _fake_get_game(gid):
            # Surface the stuck session as seated at the Scene-0 table.
            if gid == stuck_id:
                return {"cash_table_id": cp.SCENE0_TABLE_ID, "cash_stake_label": "$2"}
            return None

        with patch(
            "flask_app.routes.cash_routes._find_active_cash_game_id",
            return_value=stuck_id,
        ), patch(
            "flask_app.routes.cash_routes._leave_table_locked",
            side_effect=lambda owner, gid: leave_calls.append((owner, gid)),
        ), patch(
            "flask_app.services.game_state_service.get_game",
            side_effect=_fake_get_game,
        ):
            data = self.client.get("/api/cash/lobby").get_json()

        # The finished Scene-0 session was torn down (leave invoked for it)...
        assert leave_calls == [(MENTOR_OWNER_ID, stuck_id)]
        # ...so the handoff is no longer wedged: comp swept (→ 0), Sal's stake offered.
        assert data["has_active_session"] is False
        assert data["bankroll"] == 0
        assert repo.load(sb, MENTOR_OWNER_ID).comp_returned is True
        assert data["mentor_stake"] is not None
        assert data["mentor_stake"]["lender_id"] == cp.SAL_ID

    def test_mentor_stake_sit_funds_from_sal_and_is_one_shot(self):
        """Non-circulating Sal can back the graduate's first seat — the carve-out —
        and the principal comes OUT of his bankroll (never minted), one time only."""
        sb = self._sandbox_id()
        home = self._home_court(sb)
        seat_index = next(i for i, s in enumerate(home.seats) if s.get("kind") == "open")
        self._set_graduated_broke(sb, home.table_id)

        # Snapshot Sal's roll before the stake. He may already have a bankroll row
        # (he's seated at Scene-0, so the world flow can seed him), or none yet —
        # in which case the mentor stake seeds his 6000 roll just-in-time. Either
        # way the principal must come OUT of that roll (the conservation check).
        sal_before = self.repos['bankroll_repo'].load_ai_bankroll(cp.SAL_ID, sandbox_id=sb)
        sal_before_chips = sal_before.chips if sal_before is not None else 6000

        resp = self.client.post(
            '/api/cash/sponsor-and-sit',
            json={
                'stake_label': cp.HOME_COURT_STAKE,
                'lender_id': cp.SAL_ID,
                'table_id': home.table_id,
                'seat_index': seat_index,
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        offer = resp.get_json()['offer']
        assert offer['kind'] == 'personality'
        assert offer['lender_id'] == cp.SAL_ID
        principal = offer['amount']

        # One-shot burned so the generic pool (which never lists Sal) can't re-offer.
        assert self.repos['career_progress_repo'].load(sb, MENTOR_OWNER_ID).mentor_stake_used is True

        # Conservation: the principal came OUT of Sal's bankroll, never minted. His
        # roll dropped by exactly the principal — a pure transfer to the player's
        # table stack (any just-in-time seed is a separate ledgered ai_seed; passive
        # regen is ~0 over a sub-second test, so the drop is exactly the principal).
        sal_after = self.repos['bankroll_repo'].load_ai_bankroll(cp.SAL_ID, sandbox_id=sb)
        assert sal_after is not None
        assert sal_before_chips - sal_after.chips == principal, (
            f"principal {principal} not funded from Sal's roll "
            f"(before={sal_before_chips}, after={sal_after.chips})"
        )

        # A second attempt is refused — both the active session AND the spent
        # one-shot block it (no double mentor-stake).
        resp2 = self.client.post(
            '/api/cash/sponsor-and-sit',
            json={
                'stake_label': cp.HOME_COURT_STAKE,
                'lender_id': cp.SAL_ID,
                'table_id': home.table_id,
                'seat_index': seat_index,
            },
        )
        assert resp2.status_code != 200
