"""The handler-level analyzer must write exactly one decision-analysis row
per decision, and must never graft a pipeline snapshot off the controller.

Background: every AI decision could produce TWO `player_decision_analysis`
rows — one from the controller's own `_analyze_decision` (with capture_id,
psychology snapshot, menu compliance, and the FRESH in-call pipeline
snapshot) and one from the handler-level `analyze_player_decision`. They
used to be reconciled by a fragile in-memory `_last_analyzed_decision`
stamp; on controller-instance divergence (cold-load) the stamp fell through
and the handler grafted the controller's STALE `_last_pipeline_snapshot`
(the player's PREVIOUS decision) onto the row — surfacing as "resolved
RAISE next to an actual FOLD" in the analyzer.

Now the handler defers to self-saving controllers via the static
`WRITES_OWN_DECISION_ANALYSIS` capability flag (no stamp, no race), and it
never reads the controller's `_last_*` accumulators.

See poker.controllers.AIPlayerController.WRITES_OWN_DECISION_ANALYSIS and
flask_app.routes.game_routes.analyze_player_decision.
"""

import inspect
from unittest.mock import MagicMock, patch

from flask_app.routes.game_routes import analyze_player_decision


def _state_machine(phase='PRE_FLOP'):
    sm = MagicMock()
    sm.current_phase.name = phase
    return sm


class _GameStateProbe:
    """Records whether execution got past the controller gate.

    `analyze_player_decision` touches `game_state.current_player` as its
    first step after the gate, so a False `touched` means we returned at the
    gate. We raise immediately afterwards to bail cheaply — the function's
    own try/except swallows it, so the call still returns cleanly.
    """

    def __init__(self):
        self.touched = False

    @property
    def current_player(self):
        self.touched = True
        raise RuntimeError('probe: stop after gate')


def test_skips_when_controller_self_saves():
    """A self-saving controller (with a repo wired) is gated out: the handler
    writes no row and never even reaches the analysis body."""
    controller = MagicMock()
    controller.WRITES_OWN_DECISION_ANALYSIS = True
    controller._decision_analysis_repo = MagicMock()
    repo = MagicMock()
    probe = _GameStateProbe()

    with patch('flask_app.routes.game_routes.extensions') as ext:
        ext.decision_analysis_repo = repo
        analyze_player_decision(
            game_id='g1',
            player_name='Batman',
            action='call',
            amount=0,
            state_machine=_state_machine('PRE_FLOP'),
            game_state=probe,
            hand_number=1,
            ai_controllers={'Batman': controller},
        )

    assert probe.touched is False  # returned at the gate
    repo.save_decision_analysis.assert_not_called()


def test_does_not_skip_when_controller_does_not_self_save():
    """A RuleBot-style controller (WRITES_OWN_DECISION_ANALYSIS = False) is
    NOT gated — the handler is its only writer, so execution proceeds past
    the gate into the analysis body."""
    controller = MagicMock()
    controller.WRITES_OWN_DECISION_ANALYSIS = False
    controller._decision_analysis_repo = MagicMock()
    probe = _GameStateProbe()

    with patch('flask_app.routes.game_routes.extensions') as ext:
        ext.decision_analysis_repo = MagicMock()
        analyze_player_decision(
            game_id='g1',
            player_name='CaseBot',
            action='raise',
            amount=200,
            state_machine=_state_machine('PRE_FLOP'),
            game_state=probe,
            hand_number=1,
            ai_controllers={'CaseBot': controller},
        )

    assert probe.touched is True  # fell through the gate, did not skip


def test_does_not_skip_self_saving_controller_without_repo():
    """A controller that claims to self-save but has no repo wired can't have
    written a row, so the handler must still act as the safety net."""
    controller = MagicMock()
    controller.WRITES_OWN_DECISION_ANALYSIS = True
    controller._decision_analysis_repo = None
    probe = _GameStateProbe()

    with patch('flask_app.routes.game_routes.extensions') as ext:
        ext.decision_analysis_repo = MagicMock()
        analyze_player_decision(
            game_id='g1',
            player_name='Batman',
            action='call',
            amount=0,
            state_machine=_state_machine('PRE_FLOP'),
            game_state=probe,
            hand_number=1,
            ai_controllers={'Batman': controller},
        )

    assert probe.touched is True  # not gated — no row exists to defer to


def test_human_is_never_gated():
    """A human (no controller of their own) is always written by the handler,
    and another player's controller is irrelevant to the gate."""
    other = MagicMock()
    other.WRITES_OWN_DECISION_ANALYSIS = True
    other._decision_analysis_repo = MagicMock()
    probe = _GameStateProbe()

    with patch('flask_app.routes.game_routes.extensions') as ext:
        ext.decision_analysis_repo = MagicMock()
        analyze_player_decision(
            game_id='g1',
            player_name='Jeff',  # acting human; no controller of their own
            action='call',
            amount=0,
            state_machine=_state_machine('PRE_FLOP'),
            game_state=probe,
            hand_number=1,
            ai_controllers={'Batman': other},
        )

    assert probe.touched is True  # human fell through to be written


def test_handler_never_grafts_controller_pipeline_snapshot():
    """Regression guard: the handler must not read the controller's mutable
    `_last_pipeline_snapshot` / `_last_intervention_trace` accumulators. That
    graft was the source of stale snapshots; the snapshot is owned solely by
    the controller's own in-call save."""
    src = inspect.getsource(analyze_player_decision)
    assert '_last_pipeline_snapshot' not in src
    assert '_last_intervention_trace' not in src
