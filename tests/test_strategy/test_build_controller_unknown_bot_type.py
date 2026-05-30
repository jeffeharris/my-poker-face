"""Regression: pins the unknown-/edge bot_type dispatch contract in build_controller.

The factory deliberately routes an unrecognised bot_type to DIFFERENT classes
depending on whether ``default_strategy`` is set:

  * create semantics  (default_strategy is None)  -> HybridAIController
  * restore semantics (default_strategy is set)    -> RuleBotController(strategy=bot_type)

This divergence is intentional and only ever exercised on NON-OVERLAPPING input
domains (create never emits an unknown value; restore only reads persisted values).
These tests lock the behaviour so a future refactor can't silently make create and
restore agree (collapsing the legacy rule-strategy rehydration) or disagree on the
values that DO overlap ('standard', the known bot types, 'fish').

See the "Unknown-bot_type contract" docstring in
``flask_app/handlers/tiered_factory.py``.
"""

from unittest.mock import MagicMock, patch

from flask_app.handlers.tiered_factory import build_controller


def _build(bot_type, **extra):
    return build_controller(
        bot_type=bot_type,
        player_name='Tester',
        state_machine=MagicMock(),
        game_id='game_test',
        owner_id='user_test',
        **extra,
    )


# --- Unknown bot_type: the two-domain divergence -----------------------------


@patch('poker.hybrid_ai_controller.HybridAIController')
def test_unknown_bot_type_create_path_is_hybrid(mock_hybrid):
    """CREATE semantics (default_strategy=None): unknown -> HybridAIController."""
    sentinel = MagicMock(name='hybrid')
    mock_hybrid.return_value = sentinel

    result = _build('totally-unknown-xyz')  # default_strategy defaults to None

    assert result is sentinel
    mock_hybrid.assert_called_once()


@patch('poker.rule_bot_controller.RuleBotController')
def test_unknown_bot_type_restore_path_is_rulebot(mock_rulebot):
    """RESTORE semantics (default_strategy set): unknown -> RuleBot(strategy=bot_type).

    Mirrors how ``restore_ai_controllers`` rehydrates legacy raw rule-strategy
    names (e.g. 'abc', 'always_fold', 'case_based') that older/experiment saves
    could have stamped.
    """
    sentinel = MagicMock(name='rulebot')
    mock_rulebot.return_value = sentinel

    result = _build('always_fold', default_strategy='always_fold')

    assert result is sentinel
    _, kwargs = mock_rulebot.call_args
    assert kwargs['strategy'] == 'always_fold'


# --- The overlapping values MUST agree across create + restore ---------------


@patch('poker.hybrid_ai_controller.HybridAIController')
def test_standard_is_hybrid_on_both_paths(mock_hybrid):
    """'standard' is the one recognised key that intentionally falls through to
    Hybrid -- and it must do so even on the restore path (default_strategy set),
    NOT get treated as a rule-bot strategy name."""
    mock_hybrid.return_value = MagicMock()

    _build('standard')  # create
    _build('standard', default_strategy='standard')  # restore

    assert mock_hybrid.call_count == 2


@patch('flask_app.handlers.tiered_factory.build_fish_controller')
def test_fish_is_tiered_calling_station_on_both_paths(mock_fish):
    """'fish' resolves to a tiered calling_station (via build_fish_controller),
    NOT a RuleBotController, regardless of default_strategy.

    Fish were unified off the rule bot onto the tiered engine: their loose-passive
    anchors classify as `calling_station` and pick up the station width-tier table,
    and the fish's deliberate tell rides on its persona `spot_tendencies` (read on
    every build path) rather than the old `fish_leak` kwarg. The dispatch must agree
    on create and restore so a persisted cash fish rebuilds identically.
    See docs/plans/FISH_AS_CALLING_STATION.md."""
    sentinel = MagicMock(name='fish')
    mock_fish.return_value = sentinel

    r1 = _build('fish')  # create
    r2 = _build('fish', default_strategy='fish')  # restore (a persisted cash fish)

    assert r1 is sentinel and r2 is sentinel
    assert mock_fish.call_count == 2


@patch('poker.rule_bot_controller.RuleBotController')
def test_casebot_alias_maps_before_else_branch_on_restore(mock_rulebot):
    """A recognised rule alias ('casebot') maps via _RULE_BOT_STRATEGY_MAP to
    'case_based' -- it must hit the explicit branch, not the default_strategy
    else-branch that would pass the raw 'casebot' string through as a strategy."""
    mock_rulebot.return_value = MagicMock()

    _build('casebot', default_strategy='casebot')

    _, kwargs = mock_rulebot.call_args
    assert kwargs['strategy'] == 'case_based'
