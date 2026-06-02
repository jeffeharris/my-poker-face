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


def test_build_persists_zero_llm_intent(app, monkeypatch):
    """The MTT field is zero-LLM by design (AI seats built with
    expression_enabled=False). build_tournament_game must persist that intent on
    the games row so a cold load doesn't default ai_chat=True and rebuild the
    seats with the (404-prone) expression layer. Guards the regression where the
    headless field started firing a narration LLM call per AI decision after a
    server restart."""
    with app.app_context():
        _rebind_handler_globals(monkeypatch)

        import flask_app.extensions as _ext
        from flask_app.handlers.tournament_game_builder import build_tournament_game
        from flask_app.services import game_state_service
        from tournament.config import TournamentConfig
        from tournament.director import FakeHandResolver
        from tournament.session import TournamentSession

        cfg = TournamentConfig(field_size=4, table_size=4, starting_stack=4000, seed=7)
        session = TournamentSession(cfg, ai_resolver=FakeHandResolver())
        game_id = build_tournament_game(
            session, tournament_id="itest-zero-llm", owner_id="itest-owner", owner_name="Tester"
        )

        # Persisted intent: ai_chat off, every AI seat stamped sharp.
        llm_configs = _ext.game_repo.load_llm_configs(game_id)
        assert llm_configs is not None, "tournament game saved no llm_configs"
        assert llm_configs.get("ai_chat") is False
        bot_types = llm_configs.get("bot_types") or {}
        assert bot_types, "no bot_types persisted for the AI field"
        assert set(bot_types.values()) == {"sharp"}

        # Live controllers carry no expression layer (zero-LLM field).
        gd = game_state_service.get_game(game_id)
        for name, ctrl in gd["ai_controllers"].items():
            assert getattr(ctrl, "expression_generator", None) is None, (
                f"AI seat {name} has an expression generator — field is not zero-LLM"
            )


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


def test_tournament_observations_fold_into_dossier(app, monkeypatch):
    """P3.9a — playing the human's Main Event table accrues the opponent-dossier
    grind, keyed on the persona's `personality_id` (the SAME key the cash dossier
    reads). Drives real hands heads-up vs a seeded real persona, then asserts a
    durable `opponent_observation_lifetime` row exists for (sandbox, owner_id,
    persona_id) — proving both seams: the memory_manager carries a sandbox_id
    (Break A) and the AI seat registered its personality_id (Break B)."""
    with app.app_context():
        import flask_app.extensions as _ext
        import flask_app.handlers.game_handler as _gh

        _rebind_handler_globals(monkeypatch)
        monkeypatch.setattr(_gh, '_run_async_narration', lambda *a, **k: None, raising=False)

        from flask_app import config as cfgmod
        from flask_app.handlers.game_handler import progress_game
        from flask_app.handlers.tournament_game_builder import build_tournament_game
        from flask_app.services import game_state_service
        from flask_app.services.sandbox_resolver import resolve_default_sandbox_for
        from poker.poker_game import advance_to_next_active_player, play_turn
        from tournament.config import TournamentConfig
        from tournament.director import FakeHandResolver
        from tournament.session import TournamentSession

        owner_id = "dossier-owner"
        owner_name = "Dossier Tester"
        human_id = f"human:{owner_id}"

        # Seed a real persona so it's recognised by real_persona_ids_for (which
        # gates registration) — a synthetic P## seat would write no lifetime row.
        persona_id = _ext.personality_repo.save_personality(
            'Dossier Mark',
            {'play_style': 'aggressive', 'confidence': 'high', 'attitude': 'friendly'},
            circulating=True,
        )

        original_speed = getattr(cfgmod, "ANIMATION_SPEED", None)
        try:
            cfgmod.ANIMATION_SPEED = 0
        except Exception:
            pass

        try:
            cfg = TournamentConfig(field_size=2, table_size=2, starting_stack=8000, seed=11)
            session = TournamentSession(
                cfg,
                ai_resolver=FakeHandResolver(),
                human_id=human_id,
                entries={human_id: 'LAG', persona_id: 'CaseBot'},
            )
            game_id = build_tournament_game(
                session, tournament_id="itest-dossier",
                owner_id=owner_id, owner_name=owner_name,
            )

            # The built memory_manager must carry a sandbox (Break A) and have
            # registered the persona's id (Break B) so folds land on the shared key.
            gd = game_state_service.get_game(game_id)
            mm = gd["memory_manager"]
            assert mm.sandbox_id, "tournament memory_manager has no sandbox_id (Break A)"
            # The seat's Player.name IS persona_id (MTT bridge); it must register
            # under that same id so folds key the shared dossier row (Break B).
            assert mm.get_opponent_model_manager()._name_to_id.get(persona_id) == persona_id, (
                "persona seat did not register its personality_id (Break B)"
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

            for _ in range(2000):
                if session.is_complete() or session.human_out:
                    break
                gd = game_state_service.get_game(game_id)
                gs = gd["state_machine"].game_state
                if not (gs.current_player.is_human and gs.awaiting_action):
                    break
                act_for_human()
                progress_game(game_id)
                if session.rounds >= 3:  # enough boundaries to fold observations
                    break

            sandbox_id = resolve_default_sandbox_for(owner_id, sandbox_repo=_ext.sandbox_repo)
            lifetime = _ext.game_repo.load_observation_lifetime(
                sandbox_id, owner_id, persona_id
            )
            assert lifetime is not None, (
                "no opponent_observation_lifetime row — tournament hands did not "
                "fold into the dossier grind"
            )
            assert lifetime.get('hands_dealt', 0) >= 1, (
                f"lifetime row exists but recorded no hands dealt: {lifetime}"
            )
        finally:
            if original_speed is not None:
                cfgmod.ANIMATION_SPEED = original_speed
