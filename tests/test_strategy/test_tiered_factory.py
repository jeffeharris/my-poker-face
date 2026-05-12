"""Tests for the tiered bot factory used by Flask handlers."""

from unittest.mock import patch, MagicMock

from flask_app.handlers.tiered_factory import build_tiered_controller


@patch('flask_app.handlers.tiered_factory.LLMClient')
@patch('flask_app.handlers.tiered_factory.ExpressionGenerator')
@patch('flask_app.handlers.tiered_factory.TieredBotController')
@patch('flask_app.handlers.tiered_factory.load_strategy_table')
def test_factory_attaches_expression_when_enabled(
    mock_load_table, mock_controller_cls, mock_expr_cls, mock_llm_cls
):
    """expression_enabled=True wires LLMClient + ExpressionGenerator onto the controller."""
    mock_table = MagicMock()
    mock_load_table.return_value = mock_table

    fake_pm = MagicMock(name='prompt_manager')
    fake_controller = MagicMock(name='controller')
    fake_controller.prompt_manager = fake_pm
    mock_controller_cls.return_value = fake_controller

    fake_client = MagicMock(name='llm_client')
    mock_llm_cls.return_value = fake_client
    fake_expr = MagicMock(name='expression_generator')
    mock_expr_cls.return_value = fake_expr

    result = build_tiered_controller(
        player_name='Lincoln',
        state_machine=MagicMock(),
        llm_config={'provider': 'openai', 'model': 'gpt-5-nano'},
        game_id='game_test',
        owner_id='user_test',
        expression_enabled=True,
    )

    # Strategy table loaded once and passed to the controller
    mock_load_table.assert_called_once()
    _, ctor_kwargs = mock_controller_cls.call_args
    assert ctor_kwargs['strategy_table'] is mock_table
    assert ctor_kwargs['player_name'] == 'Lincoln'
    assert ctor_kwargs['game_id'] == 'game_test'

    # LLMClient built from the player's llm_config
    mock_llm_cls.assert_called_once_with(provider='openai', model='gpt-5-nano')
    # ExpressionGenerator gets the controller's prompt_manager
    mock_expr_cls.assert_called_once_with(llm_client=fake_client, prompt_manager=fake_pm)

    # Generator and call type assigned to the controller
    assert result.expression_generator is fake_expr
    from core.llm import CallType
    assert result._expression_call_type == CallType.COMMENTARY


@patch('flask_app.handlers.tiered_factory.LLMClient')
@patch('flask_app.handlers.tiered_factory.ExpressionGenerator')
@patch('flask_app.handlers.tiered_factory.TieredBotController')
@patch('flask_app.handlers.tiered_factory.load_strategy_table')
def test_factory_skips_expression_when_disabled(
    mock_load_table, mock_controller_cls, mock_expr_cls, mock_llm_cls
):
    """expression_enabled=False leaves the controller's expression_generator untouched."""
    fake_controller = MagicMock(name='controller')
    mock_controller_cls.return_value = fake_controller

    build_tiered_controller(
        player_name='Lincoln',
        state_machine=MagicMock(),
        llm_config={'provider': 'openai'},
        game_id='game_test',
        owner_id='user_test',
        expression_enabled=False,
    )

    mock_llm_cls.assert_not_called()
    mock_expr_cls.assert_not_called()


@patch('flask_app.handlers.tiered_factory.LLMClient')
@patch('flask_app.handlers.tiered_factory.ExpressionGenerator')
@patch('flask_app.handlers.tiered_factory.TieredBotController')
@patch('flask_app.handlers.tiered_factory.load_strategy_table')
def test_factory_handles_missing_llm_config(
    mock_load_table, mock_controller_cls, mock_expr_cls, mock_llm_cls
):
    """A None llm_config defaults provider to openai and model to None."""
    fake_controller = MagicMock(name='controller')
    mock_controller_cls.return_value = fake_controller

    build_tiered_controller(
        player_name='Lincoln',
        state_machine=MagicMock(),
        llm_config=None,
        game_id='game_test',
        owner_id='user_test',
        expression_enabled=True,
    )

    mock_llm_cls.assert_called_once_with(provider='openai', model=None)
