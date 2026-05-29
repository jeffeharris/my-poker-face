"""PRH-33 (force chat sender server-side) + PRH-19 (no double LLM call on a
transport error)."""

from unittest.mock import MagicMock, patch

from poker.prompt_config import PromptConfig

# --- PRH-33: _human_seat_name forces sender to the real human seat ----------


def _game_data_with_human(human_name="Alice"):
    ai = MagicMock()
    ai.name = "Batman"
    ai.is_human = False
    human = MagicMock()
    human.name = human_name
    human.is_human = True
    sm = MagicMock()
    sm.game_state.players = (ai, human)
    return {"state_machine": sm}


def test_human_seat_name_returns_the_human_seat():
    from flask_app.routes.game_routes import _human_seat_name

    assert _human_seat_name(_game_data_with_human("Alice")) == "Alice"


def test_human_seat_name_none_when_no_human():
    from flask_app.routes.game_routes import _human_seat_name

    ai = MagicMock()
    ai.name = "Batman"
    ai.is_human = False
    sm = MagicMock()
    sm.game_state.players = (ai,)
    assert _human_seat_name({"state_machine": sm}) is None


def test_human_seat_name_tolerates_bad_game_data():
    from flask_app.routes.game_routes import _human_seat_name

    assert _human_seat_name({}) is None
    assert _human_seat_name(None) is None


# --- PRH-19: a transport-error response must NOT trigger a second LLM call ---


def _make_controller():
    with (
        patch("poker.controllers.AIPokerPlayer") as mock_player,
        patch("poker.controllers.PromptManager"),
        patch("poker.controllers.ChattinessManager"),
        patch("poker.controllers.ResponseValidator") as mock_validator,
        patch("poker.controllers.PlayerPsychology") as mock_psych,
    ):
        mock_player.return_value.assistant = MagicMock()
        mock_player.return_value.personality_config = {}
        mock_psych.from_personality_config.return_value = MagicMock()

        from poker.controllers import AIPlayerController

        controller = AIPlayerController("TestPlayer", prompt_config=PromptConfig())
        mock_validator.return_value.clean_response.side_effect = lambda r, _: r

    controller._decision_analysis_repo = None
    controller._capture_label_repo = None
    controller.current_hand_number = 1
    return controller


def _game_state():
    player = MagicMock()
    player.name = "TestPlayer"
    player.stack = 1000
    player.bet = 0
    player.is_folded = False
    player.hand = [MagicMock(), MagicMock()]
    opp = MagicMock()
    opp.name = "Opp"
    opp.bet = 0
    opp.is_folded = False
    gs = MagicMock()
    gs.current_player = player
    gs.players = (player, opp)
    gs.pot = {"total": 100}
    gs.community_cards = ()
    gs.current_ante = 10
    return gs


def test_transport_error_skips_recovery_and_uses_fallback():
    controller = _make_controller()
    sm = MagicMock()
    sm.game_state = _game_state()
    sm.current_phase = None
    controller.state_machine = sm

    # The LLM call fails at the transport layer (timeout/budget): status="error".
    err = MagicMock()
    err.status = "error"
    err.content = ""
    err.error_code = "timeout"
    controller.assistant.chat_full = MagicMock(return_value=err)

    with (
        patch.object(controller, "_build_decision_prompt", return_value=("prompt", None)),
        patch.object(controller, "_apply_final_fixes", side_effect=lambda rd, *a, **k: rd),
        patch(
            "poker.controllers.FallbackActionSelector.select_action",
            return_value={"action": "fold", "raise_to": 0},
        ),
    ):
        result = controller._get_ai_decision(
            "msg",
            valid_actions=["fold", "call"],
            call_amount=20,
            min_raise=20,
            max_raise=100,
        )

    # PRH-19: exactly ONE call — no recovery hit against the same down provider.
    assert controller.assistant.chat_full.call_count == 1
    # ...and the decision came from the deterministic fallback.
    assert result.get("_used_fallback") is True
    assert result.get("action") == "fold"
