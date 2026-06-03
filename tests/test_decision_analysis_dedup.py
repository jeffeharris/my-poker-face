"""The handler-level fallback analyzer must not write a second,
impoverished decision-analysis row when the acting player's controller
already wrote the richer controller-side row.

Background: every AI decision used to produce TWO `player_decision_analysis`
rows — one from the controller's `_analyze_decision` (with capture_id,
psychology snapshot, menu compliance) and one from the handler-level
`analyze_player_decision` fallback (with none of that). The fallback now
defers to the controller via a consume-on-match stamp.

See poker.controllers.AIPlayerController._analyze_decision (stamp) and
flask_app.routes.game_routes.analyze_player_decision (gate).
"""

from unittest.mock import MagicMock, patch

from flask_app.routes.game_routes import analyze_player_decision


def _state_machine(phase='PRE_FLOP'):
    sm = MagicMock()
    sm.current_phase.name = phase
    return sm


def test_skips_duplicate_when_controller_already_analyzed():
    """Matching stamp -> no fallback row written, stamp consumed."""
    controller = MagicMock()
    controller._last_analyzed_decision = (1, 'PRE_FLOP', 'call')
    repo = MagicMock()

    with patch('flask_app.routes.game_routes.extensions') as ext:
        ext.decision_analysis_repo = repo
        analyze_player_decision(
            game_id='g1',
            player_name='Batman',
            action='call',
            amount=0,
            state_machine=_state_machine('PRE_FLOP'),
            game_state=MagicMock(),
            hand_number=1,
            ai_controllers={'Batman': controller},
        )

    repo.save_decision_analysis.assert_not_called()
    assert controller._last_analyzed_decision is None  # consumed on match


def test_does_not_skip_when_stamp_mismatches():
    """A stamp for a different action is left intact and not skipped.

    (Downstream analysis may fail on the MagicMock game_state; that's
    swallowed by the function's own try/except — we only assert the gate
    declined to consume the stamp belonging to a different decision.)
    """
    controller = MagicMock()
    controller._last_analyzed_decision = (1, 'PRE_FLOP', 'call')

    with patch('flask_app.routes.game_routes.extensions'):
        analyze_player_decision(
            game_id='g1',
            player_name='Batman',
            action='raise',  # different action -> not the same decision
            amount=200,
            state_machine=_state_machine('PRE_FLOP'),
            game_state=MagicMock(),
            hand_number=1,
            ai_controllers={'Batman': controller},
        )

    assert controller._last_analyzed_decision == (1, 'PRE_FLOP', 'call')


def test_gate_keys_off_the_acting_player_only():
    """A human (no controller) is never gated, and another player's stamp
    is left untouched."""
    other = MagicMock()
    other._last_analyzed_decision = (1, 'PRE_FLOP', 'call')

    with patch('flask_app.routes.game_routes.extensions') as ext:
        ext.decision_analysis_repo = MagicMock()
        analyze_player_decision(
            game_id='g1',
            player_name='Jeff',  # acting human; no controller of their own
            action='call',
            amount=0,
            state_machine=_state_machine('PRE_FLOP'),
            game_state=MagicMock(),
            hand_number=1,
            ai_controllers={'Batman': other},
        )

    assert other._last_analyzed_decision == (1, 'PRE_FLOP', 'call')
