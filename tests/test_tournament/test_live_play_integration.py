"""End-to-end live-play integration: drive the human through REAL progress_game
hands and confirm the gated hand-boundary hook fires correctly (no redeal loop).

This is the test that closes the Phase 2c gap — it exercises the production
game_handler loop (run_until_player_action, handle_ai_action,
handle_evaluating_hand_phase + the tournament hook) for a built tournament game,
driving the human via the same play_turn path the action route uses.

Heavy (real tiered controllers + DB), so marked slow+integration.
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


def test_human_plays_real_hands_to_a_terminal_state(app, monkeypatch):
    """Build a small tournament, drive the human (auto check/call/fold) through
    real hands, and assert it reaches a terminal state without looping —
    conservation holds and the human actually got to act."""
    with app.app_context():
        import flask_app.extensions as _ext
        import flask_app.handlers.game_handler as _gh

        _rebind_handler_globals(monkeypatch)
        monkeypatch.setattr(_gh, '_run_async_narration', lambda *a, **k: None, raising=False)

        from flask_app import config as cfgmod
        from flask_app.handlers.game_handler import progress_game
        from flask_app.handlers.tournament_game_builder import build_tournament_game
        from flask_app.services import game_state_service
        from poker.poker_game import advance_to_next_active_player, play_turn
        from tournament.config import TournamentConfig
        from tournament.director import FakeHandResolver
        from tournament.session import TournamentSession

        # Kill animation/showdown sleeps so the test runs fast.
        original_speed = getattr(cfgmod, "ANIMATION_SPEED", None)
        try:
            cfgmod.ANIMATION_SPEED = 0
        except Exception:
            pass

        try:
            cfg = TournamentConfig(field_size=4, table_size=4, starting_stack=4000, seed=1)
            session = TournamentSession(cfg, ai_resolver=FakeHandResolver())
            game_id = build_tournament_game(
                session, tournament_id="itest", owner_id="itest-owner", owner_name="Tester"
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

            progress_game(game_id)  # drive AIs to the first human action

            human_turns = 0
            terminal = False
            for _ in range(2000):  # generous cap; a real event ends far sooner
                if session.is_complete() or session.human_out:
                    terminal = True
                    break
                gd = game_state_service.get_game(game_id)
                assert gd is not None
                gs = gd["state_machine"].game_state
                cp = gs.current_player
                # The loop must always come to rest on the human (never spin
                # through hands without giving them a turn).
                assert cp.is_human and gs.awaiting_action, (
                    f"progress_game settled on non-human turn: {cp.name} "
                    f"is_human={cp.is_human} awaiting={gs.awaiting_action}"
                )
                act_for_human()
                human_turns += 1
                progress_game(game_id)
                # conservation holds across every boundary
                assert session.field.chip_sum() == cfg.total_chips

            assert terminal, "tournament never reached a terminal state (possible redeal loop)"
            assert human_turns > 3, "human barely acted — turns aren't reaching the human"
            assert session.field.chip_sum() == cfg.total_chips
            # the field advanced past the start
            assert session.rounds > 0
        finally:
            if original_speed is not None:
                cfgmod.ANIMATION_SPEED = original_speed
