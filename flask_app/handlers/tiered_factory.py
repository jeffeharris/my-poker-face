"""Shared factory for constructing TieredBotController / BaselineSolverBot instances.

Used by `api_new_game()` (creating a new live game) and
`restore_ai_controllers()` (rehydrating after server restart).
Mirrors the canonical pattern in experiments/run_ai_tournament.py.
"""

import logging
from typing import Optional

from core.llm import CallType, LLMClient
from poker.strategy.expression_generator import ExpressionGenerator
from poker.strategy.strategy_table import (
    load_depth_strategy_tables,
    load_hu_strategy_table,
    load_strategy_table,
)
from poker.tiered_bot_controller import BaselineSolverBot, TieredBotController

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
    baseline: bool = False,
) -> TieredBotController:
    """Build a TieredBotController, optionally with the Layer 3 expression generator wired.

    Args:
        baseline: If True, instantiate BaselineSolverBot (pure solver, no personality
            distortion, no expression layer regardless of expression_enabled).
    """
    llm_config = llm_config or {}
    strategy_table = load_strategy_table()
    hu_strategy_table = load_hu_strategy_table()  # None if file missing
    depth_strategy_tables = load_depth_strategy_tables()  # {} if files missing
    controller_cls = BaselineSolverBot if baseline else TieredBotController
    controller = controller_cls(
        player_name=player_name,
        state_machine=state_machine,
        strategy_table=strategy_table,
        hu_strategy_table=hu_strategy_table,
        depth_strategy_tables=depth_strategy_tables,
        llm_config=llm_config,
        game_id=game_id,
        owner_id=owner_id,
        capture_label_repo=capture_label_repo,
        decision_analysis_repo=decision_analysis_repo,
        debug_logging=debug_logging,
    )

    # Baseline solver intentionally skips the expression layer — it's the
    # "pure GTO, no personality" option.
    if expression_enabled and not baseline:
        from core.llm.config import INGAME_LLM_TIMEOUT_SECONDS

        llm_client = LLMClient(
            provider=llm_config.get('provider', 'openai'),
            model=llm_config.get('model'),
            # PRH-18: in-game narration — bound it so a stalled provider can't
            # hang the hand under the per-game lock.
            default_timeout=INGAME_LLM_TIMEOUT_SECONDS,
        )
        controller.expression_generator = ExpressionGenerator(
            llm_client=llm_client,
            prompt_manager=controller.prompt_manager,
        )
        controller._expression_call_type = CallType.COMMENTARY

    return controller
