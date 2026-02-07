"""
Unified Psychology Pipeline.

Owns the full detect-resolve-persist-update-recover-save cycle for post-hand
psychology processing. Both the Flask game handler and the experiment runner
invoke this pipeline with their game context.

UI concerns (socket emissions, animation delays) stay in callers via callbacks.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from poker.controllers import AIPlayerController
from poker.equity_snapshot import HandEquityHistory
from poker.moment_analyzer import MomentAnalyzer
from poker.pressure_detector import PressureEventDetector

logger = logging.getLogger(__name__)


@dataclass
class PsychologyContext:
    """Input context for one hand's psychology processing."""

    game_id: str
    hand_number: int
    game_state: Any  # PokerGameState — avoids circular import
    winner_info: dict
    winner_names: list
    pot_size: int  # pot before awarding
    controllers: Dict[str, AIPlayerController]
    hand_start_stacks: Optional[Dict[str, int]] = None
    was_short_stack: Optional[Set[str]] = None
    equity_history: Optional[HandEquityHistory] = None
    memory_manager: Optional[Any] = None  # AIMemoryManager
    big_blind: int = 100


@dataclass
class PsychologyResult:
    """Output from pipeline processing."""

    current_short_stack: Set[str]
    detected_events: List[Tuple[str, List[str]]]
    resolved_results: Dict[str, dict]
    recovery_infos: Dict[str, dict]


class PsychologyPipeline:
    """Runs the full post-hand psychology pipeline.

    Stages: detect -> resolve -> persist -> callback -> update -> recover -> save.

    Args:
        pressure_detector: Detects showdown, equity, stack, streak, nemesis events.
        pressure_event_repo: Optional repository for persisting events.
        game_repo: Optional repository for saving controller/emotional state.
        hand_history_repo: Optional repository for session stats lookups.
        enable_emotional_narration: If True, call on_hand_complete (LLM call).
            If False, use lightweight composure_state.update_from_hand.
        persist_controller_state: If True, save psychology/emotional state to DB
            after recovery.
    """

    def __init__(
        self,
        pressure_detector: PressureEventDetector,
        pressure_event_repo=None,
        game_repo=None,
        hand_history_repo=None,
        *,
        enable_emotional_narration: bool = True,
        persist_controller_state: bool = True,
    ):
        self.pressure_detector = pressure_detector
        self.pressure_event_repo = pressure_event_repo
        self.game_repo = game_repo
        self.hand_history_repo = hand_history_repo
        self.enable_emotional_narration = enable_emotional_narration
        self.persist_controller_state = persist_controller_state

    def process_hand(
        self,
        ctx: PsychologyContext,
        *,
        on_events_resolved: Optional[Callable] = None,
    ) -> PsychologyResult:
        """Run full pipeline: detect -> resolve -> persist -> update -> recover -> save.

        Args:
            ctx: All context needed for processing.
            on_events_resolved: Optional callback fired after events are resolved
                and persisted. Receives (all_events, player_resolved_results,
                controllers) — callers use this for UI updates (emit elasticity,
                pressure_stats recording, debug messages).

        Returns:
            PsychologyResult with detected events, resolved results, recovery info,
            and updated short-stack set.
        """
        controllers = ctx.controllers
        game_state = ctx.game_state

        # === 1. DETECT ALL EVENTS ===
        all_events = self._detect_events(ctx)

        if not all_events:
            return PsychologyResult(
                current_short_stack=ctx.was_short_stack or set(),
                detected_events=[],
                resolved_results={},
                recovery_infos={},
            )

        # === 2. BUILD PER-PLAYER EVENT LISTS AND RESOLVE ===
        winner_names = ctx.winner_names
        loser_names = [
            p.name
            for p in game_state.players
            if not p.is_folded and p.name not in winner_names
        ]

        player_events: Dict[str, list] = {}
        for event_name, affected_players in all_events:
            for player_name in affected_players:
                if player_name in controllers:
                    player_events.setdefault(player_name, []).append(event_name)

        resolved_results = {}
        for player_name, player_event_list in player_events.items():
            controller = controllers[player_name]
            if not hasattr(controller, 'psychology'):
                continue

            # Opponent logic: both winners and losers get opponents
            opponent = None
            if player_name in winner_names and loser_names:
                opponent = loser_names[0]
            elif player_name in loser_names and winner_names:
                opponent = winner_names[0]

            try:
                result = controller.psychology.resolve_hand_events(
                    player_event_list, opponent
                )
                resolved_results[player_name] = result
                logger.debug(
                    f"[Psychology] {player_name}: Resolved {result['events_applied']}. "
                    f"Conf={controller.psychology.confidence:.2f}, "
                    f"Comp={controller.psychology.composure:.2f}"
                )
            except Exception as e:
                logger.warning(f"Failed to resolve events for {player_name}: {e}")

        # === 3. PERSIST RESOLVED EVENTS ===
        if self.pressure_event_repo:
            for player_name, result in resolved_results.items():
                per_event_deltas = result.get('per_event_deltas', {})
                player_event_list = player_events.get(player_name, [])
                # Determine opponent (same logic as above)
                opponent = None
                if player_name in winner_names and loser_names:
                    opponent = loser_names[0]
                elif player_name in loser_names and winner_names:
                    opponent = winner_names[0]

                for event_name in result['events_applied']:
                    event_deltas = per_event_deltas.get(event_name, {})
                    self.pressure_event_repo.save_event(
                        game_id=ctx.game_id,
                        player_name=player_name,
                        event_type=event_name,
                        hand_number=ctx.hand_number,
                        details={
                            'conf_delta': event_deltas.get('conf_delta', 0),
                            'comp_delta': event_deltas.get('comp_delta', 0),
                            'energy_delta': event_deltas.get('energy_delta', 0),
                            'conf_after': result['conf_after'],
                            'comp_after': result['comp_after'],
                            'energy_after': result['energy_after'],
                            'opponent': opponent,
                            'resolved_from': player_event_list,
                        },
                    )

        # === 4. FIRE CALLBACK ===
        if on_events_resolved:
            try:
                on_events_resolved(all_events, resolved_results, controllers)
            except Exception as e:
                logger.warning(f"on_events_resolved callback failed: {e}")

        # === 5. UPDATE COMPOSURE / EMOTIONAL STATE ===
        self._update_composure(ctx, winner_names)

        # === 6. APPLY RECOVERY ===
        recovery_infos = self._apply_recovery(ctx)

        # === 7. SAVE STATE ===
        if self.persist_controller_state and self.game_repo:
            self._save_state(ctx, recovery_infos)

        # Compute current_short_stack from detection
        current_short = ctx.was_short_stack or set()
        # Stack event detection already updates current_short via _detect_events;
        # we capture it there and return it here.
        current_short = getattr(self, '_current_short_stack', current_short)

        return PsychologyResult(
            current_short_stack=current_short,
            detected_events=all_events,
            resolved_results=resolved_results,
            recovery_infos=recovery_infos,
        )

    # === PRIVATE: DETECTION ===

    def _detect_events(self, ctx: PsychologyContext) -> List[Tuple[str, List[str]]]:
        """Detect all post-hand events."""
        all_events = []
        controllers = ctx.controllers
        game_state = ctx.game_state

        # Calculate pot_size and big_blind
        pot_size = ctx.pot_size
        big_blind = ctx.big_blind

        # Check if this is a big pot
        active_stacks = [p.stack for p in game_state.players if p.stack > 0]
        avg_stack = sum(active_stacks) / len(active_stacks) if active_stacks else 1000
        is_big_pot = MomentAnalyzer.is_big_pot(pot_size, 0, avg_stack)

        # 1. Standard showdown events
        bluff_likelihoods = {
            name: ctrl.get_hand_bluff_likelihood()
            for name, ctrl in controllers.items()
            if hasattr(ctrl, 'get_hand_bluff_likelihood')
        }
        showdown_events = self.pressure_detector.detect_showdown_events(
            game_state, ctx.winner_info, player_bluff_likelihoods=bluff_likelihoods
        )
        all_events.extend(showdown_events)

        # 2. Equity shock events
        if (
            ctx.equity_history
            and ctx.equity_history.snapshots
            and ctx.hand_start_stacks
        ):
            try:
                equity_events = self.pressure_detector.detect_equity_shock_events(
                    ctx.equity_history,
                    ctx.winner_names,
                    pot_size,
                    ctx.hand_start_stacks,
                )
                all_events.extend(equity_events)
            except Exception as e:
                logger.warning(f"Equity shock detection failed: {e}")

        # 3. Stack events (crippled, short_stack)
        current_short = ctx.was_short_stack or set()
        if ctx.hand_start_stacks:
            stack_events, current_short = self.pressure_detector.detect_stack_events(
                game_state,
                ctx.winner_names,
                ctx.hand_start_stacks,
                ctx.was_short_stack or set(),
                big_blind,
            )
            all_events.extend(stack_events)

            # Short-stack survival
            survival_events = (
                self.pressure_detector.detect_short_stack_survival_events(
                    current_short, ctx.hand_number
                )
            )
            all_events.extend(survival_events)

        # Store current_short for return value
        self._current_short_stack = current_short

        # 4. Streak events (from DB-backed session stats)
        hand_history_repo = self.hand_history_repo
        if hand_history_repo:
            for player_name in controllers:
                try:
                    session_stats = hand_history_repo.get_session_stats(
                        ctx.game_id, player_name
                    )
                    streak_events = self.pressure_detector.detect_streak_events(
                        player_name, session_stats
                    )
                    all_events.extend(streak_events)
                except Exception as e:
                    logger.warning(
                        f"Failed to get session stats for {player_name}: {e}"
                    )

        # 5. Nemesis events
        loser_names = [
            p.name
            for p in game_state.players
            if not p.is_folded and p.name not in ctx.winner_names
        ]
        player_nemesis_map = {}
        for name, controller in controllers.items():
            if (
                hasattr(controller, 'psychology')
                and controller.psychology.tilt.nemesis
            ):
                player_nemesis_map[name] = controller.psychology.tilt.nemesis

        if player_nemesis_map:
            nemesis_events = self.pressure_detector.detect_nemesis_events(
                ctx.winner_names,
                loser_names,
                player_nemesis_map,
                is_big_pot=is_big_pot,
            )
            all_events.extend(nemesis_events)

        # 6. Big pot involvement (pressure/fatigue)
        if is_big_pot:
            active_player_names = [
                p.name for p in game_state.players if not p.is_folded
            ]
            all_events.append(("big_pot_involved", active_player_names))

        return all_events

    # === PRIVATE: COMPOSURE UPDATE ===

    def _update_composure(
        self,
        ctx: PsychologyContext,
        winner_names: List[str],
    ) -> None:
        """Update composure tracking and emotional state for all AI players."""
        game_state = ctx.game_state
        controllers = ctx.controllers

        # Calculate winnings per player from pot_breakdown (split-pot support)
        winnings_by_player = {}
        for pot in ctx.winner_info.get('pot_breakdown', []):
            for winner in pot.get('winners', []):
                winnings_by_player[winner['name']] = (
                    winnings_by_player.get(winner['name'], 0) + winner.get('amount', 0)
                )

        for player in game_state.players:
            if player.name not in controllers:
                continue

            controller = controllers[player.name]
            if not hasattr(controller, 'psychology'):
                continue

            # Clear per-hand bluff tracking (always, regardless of narration mode)
            if hasattr(controller, 'clear_hand_bluff_likelihood'):
                controller.clear_hand_bluff_likelihood()

            # Calculate net amount
            player_contribution = (
                game_state.pot.get(player.name, 0)
                if isinstance(game_state.pot, dict)
                else 0
            )
            player_won = player.name in winner_names
            if player_won:
                amount = winnings_by_player.get(player.name, 0) - player_contribution
            else:
                amount = -player_contribution

            # Determine outcome details
            was_bad_beat = (
                not player_won
                and not player.is_folded
                and ctx.winner_info.get('hand_rank', 0) >= 2
            )
            was_bluff_called = False
            outcome = (
                'won'
                if player_won
                else ('folded' if player.is_folded else 'lost')
            )
            nemesis = winner_names[0] if not player_won and winner_names else None
            key_moment = (
                'bad_beat'
                if was_bad_beat
                else ('bluff_called' if was_bluff_called else None)
            )

            # Build session_context from memory_manager when available
            session_context = {}
            if ctx.memory_manager:
                session_memory = ctx.memory_manager.get_session_memory(player.name)
                if session_memory and hasattr(session_memory, 'context'):
                    sc = session_memory.context
                    session_context = {
                        'net_change': getattr(sc, 'total_winnings', 0),
                        'streak_type': getattr(sc, 'current_streak', 'neutral'),
                        'streak_count': getattr(sc, 'streak_count', 0),
                    }

            try:
                if self.enable_emotional_narration:
                    controller.psychology.on_hand_complete(
                        outcome=outcome,
                        amount=amount,
                        opponent=nemesis,
                        was_bad_beat=was_bad_beat,
                        was_bluff_called=was_bluff_called,
                        session_context=session_context,
                        key_moment=key_moment,
                        big_blind=ctx.big_blind,
                    )
                else:
                    # Lightweight: composure tracking only (no LLM call)
                    controller.psychology.composure_state.update_from_hand(
                        outcome=outcome,
                        amount=amount,
                        opponent=nemesis,
                        was_bad_beat=was_bad_beat,
                        was_bluff_called=was_bluff_called,
                    )
            except Exception as e:
                logger.warning(
                    f"Psychology state update failed for {player.name}: {e}"
                )

    # === PRIVATE: RECOVERY ===

    def _apply_recovery(self, ctx: PsychologyContext) -> Dict[str, dict]:
        """Apply recovery between hands and persist recovery/gravity events."""
        recovery_infos = {}

        for player_name, controller in ctx.controllers.items():
            if not hasattr(controller, 'psychology') or not controller.psychology:
                continue

            try:
                recovery_info = controller.psychology.recover()
                recovery_infos[player_name] = recovery_info

                if self.pressure_event_repo and recovery_info:
                    # Recovery force
                    if (
                        abs(recovery_info['recovery_conf']) > 0.001
                        or abs(recovery_info['recovery_comp']) > 0.001
                    ):
                        self.pressure_event_repo.save_event(
                            game_id=ctx.game_id,
                            player_name=player_name,
                            event_type='_recovery',
                            hand_number=ctx.hand_number,
                            details={
                                'conf_delta': recovery_info['recovery_conf'],
                                'comp_delta': recovery_info['recovery_comp'],
                                'energy_delta': recovery_info['recovery_energy'],
                            },
                        )
                    # Zone gravity force
                    gravity_conf = recovery_info.get('gravity_conf', 0)
                    gravity_comp = recovery_info.get('gravity_comp', 0)
                    if abs(gravity_conf) > 0.001 or abs(gravity_comp) > 0.001:
                        self.pressure_event_repo.save_event(
                            game_id=ctx.game_id,
                            player_name=player_name,
                            event_type='_gravity',
                            hand_number=ctx.hand_number,
                            details={
                                'conf_delta': gravity_conf,
                                'comp_delta': gravity_comp,
                            },
                        )
            except Exception as e:
                logger.warning(
                    f"Psychology recovery failed for {player_name}: {e}"
                )

        return recovery_infos

    # === PRIVATE: SAVE STATE ===

    def _save_state(self, ctx: PsychologyContext, recovery_infos: dict) -> None:
        """Save psychology and emotional state to database."""
        for player_name, controller in ctx.controllers.items():
            if not hasattr(controller, 'psychology') or not controller.psychology:
                continue

            try:
                # Save controller state (psychology + prompt_config)
                psychology_dict = controller.psychology.to_dict()
                prompt_config_dict = (
                    controller.prompt_config.to_dict()
                    if hasattr(controller, 'prompt_config') and controller.prompt_config
                    else None
                )
                self.game_repo.save_controller_state(
                    ctx.game_id,
                    player_name,
                    psychology=psychology_dict,
                    prompt_config=prompt_config_dict,
                )

                # Save emotional state
                if controller.psychology.emotional:
                    self.game_repo.save_emotional_state(
                        ctx.game_id,
                        player_name,
                        controller.psychology.emotional,
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to save psychology state for {player_name}: {e}"
                )
