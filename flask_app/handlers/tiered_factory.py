"""Shared factory for constructing TieredBotController instances.

Used by both `api_new_game()` (creating a new live game) and
`restore_ai_controllers()` (rehydrating a tiered bot after server restart).
Mirrors the canonical pattern in experiments/run_ai_tournament.py.
"""

import logging
from typing import Optional

from poker.tiered_bot_controller import TieredBotController
from poker.strategy.strategy_table import load_strategy_table, load_hu_strategy_table
from poker.strategy.expression_generator import ExpressionGenerator
from core.llm import LLMClient, CallType

logger = logging.getLogger(__name__)


def build_tiered_controller(
    *,
    player_name: str,
    state_machine,
    llm_config: Optional[dict],
    game_id: Optional[str],
    owner_id: Optional[str],
    capture_label_repo=None,
    decision_analysis_repo=None,
    expression_enabled: bool = True,
    debug_logging: bool = False,
) -> TieredBotController:
    """Build a TieredBotController, optionally with the Layer 3 expression generator wired."""
    llm_config = llm_config or {}
    strategy_table = load_strategy_table()
    hu_strategy_table = load_hu_strategy_table()  # None if file missing
    controller = TieredBotController(
        player_name=player_name,
        state_machine=state_machine,
        strategy_table=strategy_table,
        hu_strategy_table=hu_strategy_table,
        llm_config=llm_config,
        game_id=game_id,
        owner_id=owner_id,
        capture_label_repo=capture_label_repo,
        decision_analysis_repo=decision_analysis_repo,
        debug_logging=debug_logging,
    )

    if expression_enabled:
        llm_client = LLMClient(
            provider=llm_config.get('provider', 'openai'),
            model=llm_config.get('model'),
        )
        controller.expression_generator = ExpressionGenerator(
            llm_client=llm_client,
            prompt_manager=controller.prompt_manager,
        )
        controller._expression_call_type = CallType.COMMENTARY

    return controller
