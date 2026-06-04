"""End-to-end: driving a session-backed tournament to a terminal state through
the real progress_game loop persists the human's career stats via the unified
completion path (step 3A.1). Heavy (real controllers + handler), so slow.
"""

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _rebind_handler_globals(monkeypatch):
    """Repair import-copied extension globals across every already-imported
    `flask_app.handlers.*` module. Those modules do `from ..extensions import
    game_repo, ...` at import time; if a test file imported them before
    init_persistence ran, the copies are None and the real progress_game loop
    NPEs (the documented xdist import-ordering gotcha in tests/CLAUDE.md). We
    only touch copies that are currently None, so this is side-effect-free in a
    correctly-ordered run.
    """
    import sys

    import flask_app.extensions as ext

    live = {n: getattr(ext, n) for n in dir(ext) if not n.startswith('_')}
    for modname, mod in list(sys.modules.items()):
        if mod is None or not modname.startswith('flask_app.handlers'):
            continue
        for name, value in live.items():
            if value is not None and getattr(mod, name, 'x') is None:
                monkeypatch.setattr(mod, name, value, raising=False)


@pytest.fixture(scope="module")
def app():
    from flask_app import create_app

    application = create_app()
    application.testing = True
    return application


class _FakeTournamentRepo:
    def __init__(self):
        self.saved = []
        self.career = []

    def save_tournament_result(self, game_id, result):
        self.saved.append((game_id, result))

    def update_career_stats(self, owner_id, player_name, result):
        self.career.append((owner_id, player_name, result))


def test_completion_writes_career_stats_through_real_loop(app, monkeypatch):
    with app.app_context():
        import flask_app.extensions as ext
        from flask_app import config as cfgmod
        from flask_app.handlers.game_handler import progress_game
        from flask_app.handlers.tournament_game_builder import build_tournament_game
        from flask_app.services import game_state_service
        from poker.poker_game import advance_to_next_active_player, play_turn
        from tournament.config import TournamentConfig
        from tournament.director import FakeHandResolver
        from tournament.session import TournamentSession

        fake_repo = _FakeTournamentRepo()
        monkeypatch.setattr(ext, 'tournament_repo', fake_repo, raising=False)
        # game_handler import-copies game_repo at import time; if it was imported
        # (by another test file) before init_persistence ran, that copy is None.
        # Re-bind it from extensions so the boundary's save_game works regardless
        # of collection order (the documented xdist import-ordering gotcha).
        import flask_app.handlers.game_handler as _gh

        _rebind_handler_globals(monkeypatch)
        # No real LLM: stub the async narration that otherwise hits a provider.
        monkeypatch.setattr(_gh, '_run_async_narration', lambda *a, **k: None, raising=False)

        original_speed = getattr(cfgmod, "ANIMATION_SPEED", None)
        try:
            cfgmod.ANIMATION_SPEED = 0
        except Exception:
            pass

        try:
            # Small 1-table field so it resolves quickly to a terminal state.
            cfg = TournamentConfig(field_size=3, table_size=3, starting_stack=3000, seed=7)
            session = TournamentSession(cfg, ai_resolver=FakeHandResolver(), human_id='P01')
            game_id = build_tournament_game(
                session, tournament_id="itest-complete", owner_id="itest-owner", owner_name="Tester"
            )

            def act_for_human():
                gd = game_state_service.get_game(game_id)
                sm = gd["state_machine"]
                gs = sm.game_state
                cp = gs.current_player
                cost = gs.highest_bet - cp.bet
                action = "check" if cost <= 0 else ("call" if cost <= cp.stack else "fold")
                ngs = play_turn(gs, action, 0)
                adv = advance_to_next_active_player(ngs)
                sm.game_state = adv if adv is not None else ngs
                gd["state_machine"] = sm
                game_state_service.set_game(game_id, gd)

            progress_game(game_id)
            for _ in range(3000):
                if session.is_complete() or session.human_out:
                    break
                gd = game_state_service.get_game(game_id)
                gs = gd["state_machine"].game_state
                if gs.current_player.is_human and gs.awaiting_action:
                    act_for_human()
                progress_game(game_id)

            assert session.is_complete() or session.human_out

            # The unified completion path ran: the human's career stats were
            # recorded exactly once, keyed to the owner + human seat.
            assert (
                len(fake_repo.career) == 1
            ), f"expected 1 career write, got {len(fake_repo.career)}"
            owner_id, player_name, result = fake_repo.career[0]
            assert owner_id == "itest-owner"
            assert player_name == session.human_id
            assert result["human_finishing_position"] == session.human_rank()
            assert len(fake_repo.saved) == 1
        finally:
            game_state_service.delete_game(game_id)
            if original_speed is not None:
                cfgmod.ANIMATION_SPEED = original_speed


def test_single_table_session_game_completes_through_real_loop(app, monkeypatch):
    """A single-table session game (tournament_multi_table=False) drives the
    NEW single_table_hand_boundary at each hand boundary, records eliminations
    in the field, and writes the human's career stats on a terminal state."""
    with app.app_context():
        import flask_app.extensions as ext
        from flask_app import config as cfgmod
        from flask_app.handlers.game_handler import progress_game
        from flask_app.handlers.tournament_game_builder import build_tournament_game
        from flask_app.services import game_state_service
        from poker.poker_game import advance_to_next_active_player, play_turn
        from tournament.config import TournamentConfig
        from tournament.director import FakeHandResolver
        from tournament.session import TournamentSession

        fake_repo = _FakeTournamentRepo()
        monkeypatch.setattr(ext, 'tournament_repo', fake_repo, raising=False)
        import flask_app.handlers.game_handler as _gh

        _rebind_handler_globals(monkeypatch)
        # No real LLM: stub the async narration that otherwise hits a provider.
        monkeypatch.setattr(_gh, '_run_async_narration', lambda *a, **k: None, raising=False)

        original_speed = getattr(cfgmod, "ANIMATION_SPEED", None)
        try:
            cfgmod.ANIMATION_SPEED = 0
        except Exception:
            pass
        try:
            # field_size == table_size => one table. build_tournament_game wires
            # real controllers + memory; we then flip it to the single-table
            # path (no multi_table flag) so it exercises single_table_hand_boundary.
            cfg = TournamentConfig(field_size=3, table_size=3, starting_stack=2500, seed=9)
            session = TournamentSession(cfg, ai_resolver=FakeHandResolver(), human_id='P01')
            game_id = build_tournament_game(
                session, tournament_id="itest-single", owner_id="itest-owner", owner_name="Tester"
            )
            gd = game_state_service.get_game(game_id)
            gd['tournament_multi_table'] = False  # route to the single-table boundary
            game_state_service.set_game(game_id, gd)

            def act_for_human():
                g = game_state_service.get_game(game_id)
                sm = g["state_machine"]
                gs = sm.game_state
                cp = gs.current_player
                cost = gs.highest_bet - cp.bet
                action = "check" if cost <= 0 else ("call" if cost <= cp.stack else "fold")
                ngs = play_turn(gs, action, 0)
                adv = advance_to_next_active_player(ngs)
                sm.game_state = adv if adv is not None else ngs
                g["state_machine"] = sm
                game_state_service.set_game(game_id, g)

            progress_game(game_id)
            for _ in range(4000):
                if session.is_complete() or session.human_out:
                    break
                g = game_state_service.get_game(game_id)
                gs = g["state_machine"].game_state
                if gs.current_player.is_human and gs.awaiting_action:
                    act_for_human()
                progress_game(game_id)

            assert session.is_complete() or session.human_out
            # Eliminations were recorded in the session field, and the human's
            # career stats were written exactly once via the unified path.
            assert len(session.field.eliminations) >= 1
            assert len(fake_repo.career) == 1
            owner_id, name, result = fake_repo.career[0]
            assert owner_id == "itest-owner" and name == session.human_id
            assert result["human_finishing_position"] == session.human_rank()
        finally:
            game_state_service.delete_game(game_id)
            if original_speed is not None:
                cfgmod.ANIMATION_SPEED = original_speed
