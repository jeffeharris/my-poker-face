#!/usr/bin/env python3
"""
AI Tournament Experiment Runner

Runs automated poker tournaments with AI-only players to evaluate
decision quality across different model configurations.

Usage:
    # Run a single tournament with default settings
    python -m experiments.run_ai_tournament

    # Run multiple tournaments with specific config
    python -m experiments.run_ai_tournament --tournaments 5 --hands 50 --players 4

    # A/B test different models
    python -m experiments.run_ai_tournament --model gpt-5-nano --experiment baseline
    python -m experiments.run_ai_tournament --model claude-haiku-4-5-20251001 --experiment claude_test

    # Run with specific personalities
    python -m experiments.run_ai_tournament --personalities "Batman,Tyler Durden,Bob Ross,A Mime"
"""

import argparse
import json
import logging
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from poker.poker_game import (
    setup_hand,
    reset_game_state_for_new_hand,
    play_turn,
    advance_to_next_active_player,
    determine_winner,
    award_pot_winnings,
    create_deck,
)
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.controllers import AIPlayerController
from poker.persistence import GamePersistence as Persistence
from poker.memory.memory_manager import AIMemoryManager
from poker.utils import get_celebrities
from poker.prompt_config import PromptConfig
from poker.pressure_detector import PressureEventDetector
from poker.elasticity_manager import ElasticityManager
from experiments.pause_coordinator import PauseCoordinator
from core.llm import LLMClient, CallType, ASSISTANT_MODEL, ASSISTANT_PROVIDER


def make_experiment_owner_id(experiment_name: str) -> str:
    """Generate consistent owner_id for experiment-related resources.

    This ensures game saves, AI memory, and other resources are properly
    namespaced to their experiment.
    """
    return f"experiment_{experiment_name}"


class TournamentPausedException(Exception):
    """Raised when a tournament is paused before completion."""

    def __init__(self, tournament_id: str, hand_number: int, message: str = "Tournament paused"):
        self.tournament_id = tournament_id
        self.hand_number = hand_number
        super().__init__(f"{message} at hand {hand_number}")


# Configure logging (only if not already configured, e.g., when imported)
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper()),
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
logger = logging.getLogger(__name__)


@dataclass
class TournamentResult:
    """Results from a single tournament."""
    experiment_name: str
    tournament_id: str
    start_time: str
    end_time: str
    duration_seconds: float
    hands_played: int
    winner: str
    final_standings: List[Dict]
    elimination_order: List[str]
    model_config: Dict
    total_api_calls: int
    total_cost: float
    avg_latency_ms: float
    decision_stats: Dict
    variant: Optional[str] = None  # Variant label for A/B testing
    round_winners: List[str] = field(default_factory=list)  # Winners of each "round" before reset
    total_resets: int = 0  # How many times stacks were reset


@dataclass
class ControlConfig:
    """Control (baseline) configuration for A/B testing."""
    label: str
    model: Optional[str] = None
    provider: Optional[str] = None
    prompt_config: Optional[Dict] = None
    enable_psychology: bool = False  # Enable tilt + emotional state generation
    enable_commentary: bool = False  # Enable commentary generation
    reasoning_effort: Optional[str] = None  # 'minimal', 'low', 'medium', 'high'


@dataclass
class VariantConfig:
    """Variant configuration that overrides control for A/B testing."""
    label: str
    model: Optional[str] = None
    provider: Optional[str] = None
    prompt_config: Optional[Dict] = None
    enable_psychology: bool = False  # Enable tilt + emotional state generation
    enable_commentary: bool = False  # Enable commentary generation
    reasoning_effort: Optional[str] = None  # Inherits from control if None


@dataclass
class ExperimentConfig:
    """Configuration for an experiment run."""
    name: str
    description: str = ""
    hypothesis: str = ""
    tags: Optional[List[str]] = None
    capture_prompts: bool = True
    num_tournaments: int = 1
    hands_per_tournament: int = 100
    num_players: int = 4
    starting_stack: int = 10000
    big_blind: int = 100
    model: str = "gpt-5-nano"
    provider: str = "openai"
    personalities: Optional[List[str]] = None
    random_seed: Optional[int] = None
    # A/B testing support
    control: Optional[Dict] = None  # ControlConfig as dict
    variants: Optional[List[Dict]] = None  # List of VariantConfig as dicts
    # Parallel execution settings
    parallel_tournaments: int = 1  # Number of concurrent tournaments (1 = sequential)
    stagger_start_delay: float = 0.0  # Seconds between starting parallel workers
    rate_limit_backoff_seconds: float = 30.0  # Base backoff on rate limit detection
    # Tournament reset behavior
    reset_on_elimination: bool = False  # If true, reset all stacks when 1 player remains

    def __post_init__(self):
        """Validate control/variants structure."""
        if self.control is not None:
            if not isinstance(self.control, dict):
                raise ValueError("control must be a dict")
            if not self.control.get('label'):
                raise ValueError("control.label is required")

        if self.variants is not None:
            if not isinstance(self.variants, list):
                raise ValueError("variants must be a list")
            for i, v in enumerate(self.variants):
                if not isinstance(v, dict):
                    raise ValueError(f"variants[{i}] must be a dict")
                if not v.get('label'):
                    raise ValueError(f"variants[{i}].label is required")

    def get_variant_configs(self) -> List[Tuple[str, Dict]]:
        """
        Returns list of (label, effective_config) tuples for all variants.

        If control is None, returns a single entry with legacy flat fields.
        If control is set, returns control + all variants with inherited fields.
        """
        # Legacy mode: no control/variants defined
        if self.control is None:
            return [(None, {
                'model': self.model,
                'provider': self.provider,
            })]

        # A/B testing mode: control + variants
        result = []

        # Control is always first
        control_config = {
            'model': self.control.get('model') or self.model,
            'provider': self.control.get('provider') or self.provider,
            'prompt_config': self.control.get('prompt_config'),
            'enable_psychology': self.control.get('enable_psychology', False),
            'enable_commentary': self.control.get('enable_commentary', False),
            'reasoning_effort': self.control.get('reasoning_effort'),
        }
        control_label = self.control.get('label', 'Control')
        result.append((control_label, control_config))

        # Add variants (inherit from control, override specified fields)
        for variant in (self.variants or []):
            variant_config = {
                'model': variant.get('model') or control_config['model'],
                'provider': variant.get('provider') or control_config['provider'],
                # Use explicit None check - empty dict {} is a valid config
                'prompt_config': variant.get('prompt_config') if 'prompt_config' in variant else control_config.get('prompt_config'),
                # Psychology flags - inherit from control if not specified
                'enable_psychology': variant.get('enable_psychology', control_config.get('enable_psychology', False)),
                'enable_commentary': variant.get('enable_commentary', control_config.get('enable_commentary', False)),
                # Reasoning effort - inherit from control if not specified
                'reasoning_effort': variant.get('reasoning_effort') if 'reasoning_effort' in variant else control_config.get('reasoning_effort'),
            }
            variant_label = variant.get('label', f'Variant {len(result)}')
            result.append((variant_label, variant_config))

        return result

    def get_total_tournaments(self) -> int:
        """Returns total number of tournaments across all variants."""
        num_variants = len(self.get_variant_configs())
        return self.num_tournaments * num_variants


@dataclass
class RateLimitState:
    """Thread-safe shared state for rate limit coordination across parallel workers.

    When any worker detects a rate limit, all workers back off using exponential
    backoff (30s -> 60s -> 120s -> 240s max). Successful API calls reduce pressure.
    """
    is_rate_limited: bool = False
    rate_limit_until: Optional[datetime] = None
    consecutive_rate_limits: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check_and_wait(self) -> float:
        """Check if rate limited and return wait time.

        Returns:
            Seconds to wait (0 if not rate limited)
        """
        with self._lock:
            if not self.is_rate_limited or self.rate_limit_until is None:
                return 0.0

            now = datetime.now()
            if now >= self.rate_limit_until:
                self.is_rate_limited = False
                self.rate_limit_until = None
                return 0.0

            return (self.rate_limit_until - now).total_seconds()

    def signal_rate_limit(self, base_backoff_seconds: float = 30.0):
        """Signal that a rate limit was hit. Uses exponential backoff."""
        with self._lock:
            self.consecutive_rate_limits += 1
            # Exponential backoff: 30s, 60s, 120s, 240s max
            actual_backoff = min(
                base_backoff_seconds * (2 ** (self.consecutive_rate_limits - 1)),
                240.0
            )
            self.is_rate_limited = True
            self.rate_limit_until = datetime.now() + timedelta(seconds=actual_backoff)
            logger.warning(
                f"Rate limit detected (#{self.consecutive_rate_limits}), "
                f"all workers backing off for {actual_backoff:.1f}s"
            )

    def signal_success(self):
        """Signal a successful API call, reducing rate limit pressure."""
        with self._lock:
            if self.consecutive_rate_limits > 0:
                self.consecutive_rate_limits = max(0, self.consecutive_rate_limits - 1)


@dataclass
class TournamentTask:
    """A tournament to be executed by a parallel worker."""
    tournament_id: str
    tournament_number: int  # Global sequence number across all variants
    variant_label: Optional[str]
    variant_config: Optional[Dict]


@dataclass
class TournamentOutcome:
    """Result of a tournament execution attempt (success or failure)."""
    task: TournamentTask
    result: Optional[TournamentResult] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        return self.result is not None and self.error is None


class TournamentWorker:
    """Executes a single tournament with error isolation for parallel execution.

    Each worker:
    - Creates its own AITournamentRunner instance (thread-local state)
    - Checks rate limit coordinator before starting
    - Reports rate limits back to coordinator
    - Catches and encapsulates all errors without crashing other workers
    """

    def __init__(
        self,
        config: ExperimentConfig,
        experiment_id: Optional[int],
        db_path: str,
        rate_limit_state: RateLimitState,
        pause_coordinator: Optional[PauseCoordinator] = None,
    ):
        self.config = config
        self.experiment_id = experiment_id
        self.db_path = db_path
        self.rate_limit_state = rate_limit_state
        self.pause_coordinator = pause_coordinator

    def execute(self, task: TournamentTask) -> TournamentOutcome:
        """Execute a single tournament with full error isolation.

        Args:
            task: Tournament task to execute

        Returns:
            TournamentOutcome with result or error details
        """
        start_time = time.time()

        try:
            # Check rate limit before starting
            wait_time = self.rate_limit_state.check_and_wait()
            if wait_time > 0:
                logger.info(f"Worker waiting {wait_time:.1f}s for rate limit cooldown")
                time.sleep(wait_time)

            # Create thread-local runner instance
            runner = AITournamentRunner(
                self.config,
                db_path=self.db_path,
                pause_coordinator=self.pause_coordinator
            )
            runner.experiment_id = self.experiment_id

            # Link game to experiment before running (enables live progress tracking)
            if self.experiment_id:
                try:
                    runner.persistence.link_game_to_experiment(
                        experiment_id=self.experiment_id,
                        game_id=task.tournament_id,
                        variant=task.variant_label,
                        variant_config=task.variant_config,
                        tournament_number=task.tournament_number,
                    )
                except Exception as e:
                    logger.warning(f"Could not link game to experiment: {e}")

            # Run the tournament
            result = runner.run_tournament(
                tournament_id=task.tournament_id,
                variant_label=task.variant_label,
                variant_config=task.variant_config,
                tournament_number=task.tournament_number,
            )

            # Save result to JSON
            runner._save_result(result)

            # Signal success to rate limiter
            self.rate_limit_state.signal_success()

            duration = time.time() - start_time
            logger.info(f"Tournament {task.tournament_id} completed in {duration:.1f}s")

            return TournamentOutcome(
                task=task,
                result=result,
                duration_seconds=duration,
            )

        except TournamentPausedException as e:
            # Tournament was paused - this is not an error, just an interruption
            duration = time.time() - start_time
            logger.info(
                f"Tournament {task.tournament_id} paused after {duration:.1f}s "
                f"at hand {e.hand_number}"
            )
            return TournamentOutcome(
                task=task,
                error=str(e),
                error_type='TournamentPausedException',
                duration_seconds=duration,
            )

        except Exception as e:
            duration = time.time() - start_time
            error_type = type(e).__name__
            error_msg = str(e)

            # Check if rate limit error and signal coordinator
            if "rate limit" in error_msg.lower() or "429" in error_msg:
                self.rate_limit_state.signal_rate_limit(
                    self.config.rate_limit_backoff_seconds
                )

            logger.error(
                f"Tournament {task.tournament_id} failed after {duration:.1f}s: "
                f"{error_type}: {error_msg}",
                exc_info=True
            )

            return TournamentOutcome(
                task=task,
                error=error_msg,
                error_type=error_type,
                duration_seconds=duration,
            )


class AITournamentRunner:
    """Runs AI-only poker tournaments for experimentation."""

    def __init__(
        self,
        config: ExperimentConfig,
        db_path: Optional[str] = None,
        pause_coordinator: Optional[PauseCoordinator] = None
    ):
        self.config = config
        self.pause_coordinator = pause_coordinator
        # Use main database for experiment data to enable JOINs with game data
        # Match the Flask app's database path logic
        if db_path:
            self.db_path = db_path
        elif (project_root / "data").exists():
            self.db_path = str(project_root / "data" / "poker_games.db")
        else:
            self.db_path = str(project_root / "poker_games.db")
        self.persistence = Persistence(self.db_path)
        self.all_personalities = get_celebrities()

        # Experiment tracking
        self.experiment_id: Optional[int] = None

        # Track metrics across tournament
        self.api_calls = 0
        self.total_latency = 0
        self.total_cost = 0.0

    @property
    def _owner_id(self) -> str:
        """Return the owner ID for this experiment."""
        return make_experiment_owner_id(self.config.name)

    def _check_pause_requested(self) -> bool:
        """Check if experiment should pause. Returns True if pause requested."""
        if self.pause_coordinator and self.experiment_id:
            if self.pause_coordinator.should_pause(self.experiment_id):
                logger.info(f"Pause requested for experiment {self.experiment_id}")
                return True
        return False

    def _save_checkpoint(self, tournament_id: str, state_machine) -> None:
        """Save game checkpoint for resume capability."""
        if tournament_id and self.experiment_id:
            try:
                self.persistence.save_game(tournament_id, state_machine, self._owner_id)
            except Exception as e:
                logger.warning(f"Checkpoint save failed: {e}")

    def select_personalities(self) -> List[str]:
        """Select personalities for the tournament."""
        if self.config.personalities:
            return self.config.personalities[:self.config.num_players]

        # Random selection from available personalities
        # get_celebrities() returns a list of names
        available = self.all_personalities if isinstance(self.all_personalities, list) else list(self.all_personalities.keys())
        if self.config.random_seed:
            random.seed(self.config.random_seed)
        return random.sample(available, min(self.config.num_players, len(available)))

    def create_game(
        self,
        tournament_id: str,
        variant_config: Optional[Dict] = None,
        tournament_number: int = 1
    ) -> Tuple[PokerStateMachine, Dict[str, AIPlayerController], AIMemoryManager]:
        """Create a new game with AI players only.

        Args:
            tournament_id: Unique identifier for this tournament
            variant_config: Optional variant-specific config (model, provider, prompt_config)
            tournament_number: Tournament number within experiment (1-indexed, for deterministic seeding)

        Returns:
            Tuple of (state_machine, controllers, memory_manager)
        """
        player_names = self.select_personalities()
        logger.info(f"Tournament {tournament_id}: Players = {player_names}")

        # Create all-AI game state directly (bypassing initialize_game_state which adds a human)
        from poker.poker_game import Player, PokerGameState

        ai_players = tuple(
            Player(name=name, stack=self.config.starting_stack, is_human=False)
            for name in player_names
        )

        # Create deterministic deck if random_seed is set (for A/B experiments)
        # Formula: base_seed + (tournament_number * 1000) + hand_number
        # This ensures:
        #   - Same tournament #, different variants → same decks (fair A/B comparison)
        #   - Different tournament #, same variant → different decks (independent samples)
        if self.config.random_seed is not None:
            deck_seed = self.config.random_seed + (tournament_number * 1000) + 1  # hand_number=1
        else:
            deck_seed = None
        initial_deck = create_deck(shuffled=True, random_seed=deck_seed)

        game_state = PokerGameState(
            players=ai_players,
            deck=initial_deck,
            current_ante=self.config.big_blind,
            last_raise_amount=self.config.big_blind
        )

        # Create state machine
        state_machine = PokerStateMachine(game_state)

        # Create memory manager for hand tracking
        # Pass commentary_enabled from variant config (defaults to False for experiments)
        commentary_enabled = variant_config.get('enable_commentary', False) if variant_config else False
        memory_manager = AIMemoryManager(
            game_id=tournament_id,
            db_path=self.db_path,
            owner_id=self._owner_id,
            commentary_enabled=commentary_enabled
        )

        # Determine LLM config: use variant_config if provided, else use experiment defaults
        if variant_config:
            llm_config = {
                'provider': variant_config.get('provider') or self.config.provider,
                'model': variant_config.get('model') or self.config.model,
            }
            # Add reasoning_effort if specified
            if variant_config.get('reasoning_effort'):
                llm_config['reasoning_effort'] = variant_config['reasoning_effort']
        else:
            llm_config = {
                'provider': self.config.provider,
                'model': self.config.model,
            }

        # Extract and convert prompt_config from variant
        prompt_config_dict = variant_config.get('prompt_config') if variant_config else None
        prompt_config = PromptConfig.from_dict(prompt_config_dict) if prompt_config_dict is not None else None

        controllers = {}
        for player in game_state.players:
            controller = AIPlayerController(
                player_name=player.name,
                state_machine=state_machine,
                llm_config=llm_config,
                game_id=tournament_id,
                owner_id=self._owner_id,
                debug_capture=self.config.capture_prompts,
                prompt_config=prompt_config,
                persistence=self.persistence,
            )
            controllers[player.name] = controller
            # Initialize memory manager for this player
            memory_manager.initialize_for_player(player.name)

        return state_machine, controllers, memory_manager

    def _process_psychology(
        self,
        game_state,
        controllers: Dict[str, AIPlayerController],
        winner_info: Dict,
        winner_names: List[str],
        hand_number: int,
        game_id: str
    ) -> None:
        """Process psychology updates (tilt + emotional state) for all AI players.

        This mirrors the logic in game_handler.py's update_tilt_states() for experiments.

        Args:
            game_state: Current game state after pot awarded
            controllers: Dict of player name -> AIPlayerController
            winner_info: Winner determination info with pot_breakdown
            winner_names: List of winning player names
            hand_number: Current hand number
            game_id: Game ID for persisting state
        """
        # Calculate pot size from winner_info
        pot_size = 0
        for pot in winner_info.get('pot_breakdown', []):
            for winner in pot.get('winners', []):
                pot_size += winner.get('amount', 0)

        # Calculate winnings per player from pot_breakdown
        winnings_by_player = {}
        for pot in winner_info.get('pot_breakdown', []):
            for winner in pot['winners']:
                winnings_by_player[winner['name']] = winnings_by_player.get(winner['name'], 0) + winner['amount']

        for player in game_state.players:
            if player.name not in controllers:
                continue

            controller = controllers[player.name]

            # Skip if controller doesn't have psychology
            if not hasattr(controller, 'psychology'):
                continue

            player_won = player.name in winner_names
            amount = winnings_by_player.get(player.name, 0) if player_won else -pot_size

            # Detect bad beat (strong hand loses at showdown)
            was_bad_beat = False
            if not player_won and not player.is_folded:
                hand_rank = winner_info.get('hand_rank', 0)
                was_bad_beat = hand_rank >= 2  # Two pair or better lost

            nemesis = winner_names[0] if not player_won and winner_names else None
            outcome = 'won' if player_won else ('folded' if player.is_folded else 'lost')
            key_moment = 'bad_beat' if was_bad_beat else None

            # Call psychology update
            try:
                controller.psychology.on_hand_complete(
                    outcome=outcome,
                    amount=amount,
                    opponent=nemesis,
                    was_bad_beat=was_bad_beat,
                    was_bluff_called=False,
                    session_context={},
                    key_moment=key_moment
                )
                logger.debug(
                    f"Psychology update for {player.name}: "
                    f"tilt={controller.psychology.tilt_level:.2f}, outcome={outcome}"
                )

                # Save psychology state to database for live monitoring
                psychology_dict = controller.psychology.to_dict()
                prompt_config_dict = controller.prompt_config.to_dict() if hasattr(controller, 'prompt_config') and controller.prompt_config else None
                self.persistence.save_controller_state(
                    game_id,
                    player.name,
                    psychology=psychology_dict,
                    prompt_config=prompt_config_dict
                )

                # Save emotional state if available
                if controller.psychology.emotional:
                    self.persistence.save_emotional_state(
                        game_id,
                        player.name,
                        controller.psychology.emotional
                    )
            except Exception as e:
                logger.warning(f"Psychology state update failed for {player.name}: {e}")

    def run_hand(self, state_machine: PokerStateMachine,
                 controllers: Dict[str, AIPlayerController],
                 memory_manager: AIMemoryManager,
                 hand_number: int,
                 tournament_id: Optional[str] = None,
                 variant_config: Optional[Dict] = None,
                 tournament_number: int = 1):
        """
        Run a single hand to completion.

        Args:
            state_machine: The game state machine
            controllers: Dict of player name -> AIPlayerController
            memory_manager: Memory manager for hand tracking
            hand_number: Current hand number
            tournament_id: Optional game ID for per-action saves (for pause/resume)
            variant_config: Optional variant-specific config with enable_psychology/enable_commentary flags
            tournament_number: Tournament number within experiment (1-indexed, for deterministic seeding)

        Returns:
            True if game should continue, False if tournament is paused,
            or "reset_needed" if only one player remains with chips.
        """
        # Let the state machine handle setup_hand via its INITIALIZING_HAND transition
        # Do NOT call setup_hand() directly - that would deal cards twice!
        game_state = state_machine.game_state

        # Check if tournament should end BEFORE setting up the hand
        active_players = [p for p in game_state.players if p.stack > 0]
        if len(active_players) <= 1:
            logger.info(f"Tournament ending: {len(active_players)} player(s) with chips remaining")
            return False

        # Set deterministic deck seed for this hand (for A/B experiments)
        # Formula: base_seed + (tournament_number * 1000) + hand_number
        # This ensures the same deck order for the same hand_number across variants
        if self.config.random_seed is not None:
            state_machine.current_hand_seed = self.config.random_seed + (tournament_number * 1000) + hand_number

        # Notify memory manager of hand start (sets hand_count internally)
        memory_manager.on_hand_start(game_state, hand_number)

        # Set hand number on all controllers for decision analysis
        for controller in controllers.values():
            controller.current_hand_number = hand_number

        logger.debug(f"Hand {hand_number}: Starting with {len(active_players)} players")

        # Run through betting rounds
        max_actions = 100  # Safety limit per hand
        action_count = 0

        # Track for stuck loop detection
        last_player_name = None
        same_player_count = 0

        while action_count < max_actions:
            # Advance state machine
            state_machine.run_until([PokerPhase.EVALUATING_HAND])
            game_state = state_machine.game_state

            # Check if hand is complete
            if state_machine.current_phase == PokerPhase.EVALUATING_HAND:
                break

            # Handle "run it out" scenario - auto-advance without player input
            # This happens when all players are all-in or only 1 can act
            if game_state.run_it_out:
                current_phase = state_machine.current_phase
                if current_phase == PokerPhase.RIVER:
                    next_phase = PokerPhase.SHOWDOWN
                else:
                    next_phase = PokerPhase.DEALING_CARDS
                # Clear flags and advance phase
                game_state = game_state.update(awaiting_action=False, run_it_out=False)
                state_machine.game_state = game_state
                state_machine.update_phase(next_phase)
                logger.debug(f"Run-it-out: advancing from {current_phase.name} to {next_phase.name}")
                continue  # Re-evaluate after phase change

            # Check if awaiting action
            if game_state.awaiting_action:
                current_player = game_state.current_player

                # Detect stuck loop (same player asked repeatedly)
                if current_player and current_player.name == last_player_name:
                    same_player_count += 1
                    if same_player_count > 5:
                        logger.warning(f"Stuck loop detected: {current_player.name} asked {same_player_count} times, forcing hand end")
                        break
                else:
                    same_player_count = 0
                    last_player_name = current_player.name if current_player else None

                controller = controllers.get(current_player.name) if current_player else None

                if controller:
                    try:
                        # Get AI decision
                        start_time = time.time()
                        response = controller.decide_action([])
                        latency = (time.time() - start_time) * 1000

                        self.api_calls += 1
                        self.total_latency += latency

                        action = response.get('action', 'fold')
                        amount = response.get('adding_to_pot', 0)

                        logger.debug(f"  {current_player.name}: {action} {amount if amount else ''}")

                        # Apply action
                        game_state = play_turn(game_state, action, amount)
                        game_state = advance_to_next_active_player(game_state)
                        state_machine.game_state = game_state  # Use property setter

                        # Per-action save for resilience (enables pause/resume)
                        if tournament_id and self.experiment_id:
                            try:
                                self.persistence.save_game(
                                    tournament_id, state_machine,
                                    self._owner_id
                                )
                                # Save AI conversation history for each controller
                                for player_name, ctrl in controllers.items():
                                    if hasattr(ctrl, 'assistant') and ctrl.assistant:
                                        messages = ctrl.assistant.memory.get_history()
                                        self.persistence.save_ai_player_state(
                                            tournament_id, player_name, messages, {}
                                        )
                            except Exception as save_error:
                                logger.warning(f"Per-action save failed: {save_error}")

                        # Check for pause request
                        if self._check_pause_requested():
                            return False  # Signal tournament should stop

                    except Exception as e:
                        logger.warning(f"AI error for {current_player.name}: {e}, defaulting to fold", exc_info=True)
                        game_state = play_turn(game_state, 'fold', 0)
                        game_state = advance_to_next_active_player(game_state)
                        state_machine.game_state = game_state  # Use property setter

                        # Save after fallback action too
                        self._save_checkpoint(tournament_id, state_machine)

                        # Check for pause request
                        if self._check_pause_requested():
                            return False

                action_count += 1

        # Evaluate hand
        if state_machine.current_phase == PokerPhase.EVALUATING_HAND:
            winner_info = determine_winner(game_state)
            game_state = award_pot_winnings(game_state, winner_info)

            winners = winner_info.get('pot_breakdown', [{}])[0].get('winners', [])
            winner_names = [w.get('name') for w in winners if w.get('name')]
            logger.debug(f"Hand {hand_number}: Winners = {winners}")

            # Post-hand psychological processing (if enabled)
            enable_psychology = variant_config.get('enable_psychology', False) if variant_config else False
            enable_commentary = variant_config.get('enable_commentary', False) if variant_config else False

            if enable_psychology:
                self._process_psychology(
                    game_state, controllers, winner_info, winner_names, hand_number,
                    game_id=tournament_id
                )

            if enable_commentary:
                # Build AI players context for commentary generation
                ai_players_context = {}
                for player in game_state.players:
                    if player.name in controllers:
                        controller = controllers[player.name]
                        ai_players_context[player.name] = {
                            'ai_player': controller,
                            'is_eliminated': player.stack == 0,
                            'spectator_context': None,
                        }
                # Generate commentary (uses memory_manager's instance-level commentary_enabled)
                memory_manager.generate_commentary_for_hand(ai_players_context)

        # Reset for next hand
        # Calculate seed for NEXT hand (hand_number + 1) if deterministic seeding is enabled
        # Formula: base_seed + (tournament_number * 1000) + hand_number
        if self.config.random_seed is not None:
            next_hand_seed = self.config.random_seed + (tournament_number * 1000) + hand_number + 1
        else:
            next_hand_seed = None
        game_state = reset_game_state_for_new_hand(game_state, deck_seed=next_hand_seed)
        state_machine.game_state = game_state  # Use property setter
        state_machine.update_phase(PokerPhase.INITIALIZING_HAND)

        # Check if tournament should continue
        active_players = [p for p in game_state.players if p.stack > 0]
        if len(active_players) <= 1:
            return "reset_needed"
        return True

    def run_tournament(
        self,
        tournament_id: str,
        variant_label: Optional[str] = None,
        variant_config: Optional[Dict] = None,
        tournament_number: int = 1
    ) -> TournamentResult:
        """Run a complete tournament to conclusion.

        Args:
            tournament_id: Unique identifier for this tournament
            variant_label: Optional variant label for A/B testing
            variant_config: Optional variant-specific config (model, provider, prompt_config)
            tournament_number: Tournament number within experiment (1-indexed, for deterministic seeding)
        """
        start_time = datetime.now()
        variant_info = f" [{variant_label}]" if variant_label else ""
        logger.info(f"Starting tournament {tournament_id}{variant_info}")

        state_machine, controllers, memory_manager = self.create_game(tournament_id, variant_config, tournament_number)

        # Store original players for reset scenarios (before any eliminations)
        original_players = state_machine.game_state.players

        # Save initial game state for live monitoring
        self.persistence.save_game(tournament_id, state_machine, self._owner_id)

        elimination_order = []
        prev_active = set(p.name for p in state_machine.game_state.players)

        # Determine hand limit and reset behavior
        # reset_on_elimination determines if hand count is maximum or exact:
        # - false: tournament ends when one player wins OR hits hand limit (variable hands)
        # - true: stacks reset on elimination, always plays exactly hands_per_tournament
        max_hands = self.config.hands_per_tournament
        should_reset = self.config.reset_on_elimination

        # Track round winners (for reset scenarios)
        round_winners: List[str] = []
        total_resets = 0

        hand_number = 0
        paused = False
        while hand_number < max_hands:
            hand_number += 1

            hand_result = self.run_hand(
                state_machine, controllers, memory_manager, hand_number,
                tournament_id=tournament_id,
                variant_config=variant_config,
                tournament_number=tournament_number
            )

            # Save game state for live monitoring (every hand)
            self.persistence.save_game(tournament_id, state_machine, self._owner_id)

            # Track eliminations
            current_active = set(p.name for p in state_machine.game_state.players if p.stack > 0)
            eliminated = prev_active - current_active
            for name in eliminated:
                elimination_order.append(name)
                logger.info(f"  Eliminated: {name}")
            prev_active = current_active

            # Handle different return values from run_hand
            if hand_result == "reset_needed":
                if should_reset:
                    # Record round winner (player with most chips) and reset
                    game_state = state_machine.game_state
                    winner = max(game_state.players, key=lambda p: p.stack)
                    round_winners.append(winner.name)
                    total_resets += 1
                    logger.info(f"Round {total_resets}: {winner.name} wins. Resetting stacks.")

                    # Reset ALL original players (not just remaining ones)
                    reset_players = tuple(
                        player.update(stack=self.config.starting_stack, is_folded=False, is_all_in=False)
                        for player in original_players
                    )
                    game_state = game_state.update(players=reset_players)
                    state_machine.game_state = game_state

                    # Reset elimination tracking for next round (all players back)
                    prev_active = set(p.name for p in original_players)
                    elimination_order = []  # Clear elimination order for new round
                    continue
                else:
                    # No reset - tournament ends when one player wins
                    break
            elif not hand_result:
                # False means paused
                if self._check_pause_requested():
                    paused = True
                break

            # Log progress every 10 hands
            if hand_number % 10 == 0:
                stacks = {p.name: p.stack for p in state_machine.game_state.players if p.stack > 0}
                resets_info = f" (resets: {total_resets})" if total_resets > 0 else ""
                logger.info(f"Hand {hand_number}: Stacks = {stacks}{resets_info}")

        # If paused, raise exception so caller can handle appropriately
        if paused:
            raise TournamentPausedException(tournament_id, hand_number)

        # Determine final standings
        end_time = datetime.now()
        final_standings = sorted(
            [{"name": p.name, "stack": p.stack} for p in state_machine.game_state.players],
            key=lambda x: x["stack"],
            reverse=True
        )
        winner = final_standings[0]["name"] if final_standings else "Unknown"

        # Calculate stats
        avg_latency = self.total_latency / max(self.api_calls, 1)

        # Use variant_config for model_config if provided, else use experiment defaults
        if variant_config:
            model_config = {
                "provider": variant_config.get('provider') or self.config.provider,
                "model": variant_config.get('model') or self.config.model,
            }
        else:
            model_config = {
                "provider": self.config.provider,
                "model": self.config.model,
            }

        result = TournamentResult(
            experiment_name=self.config.name,
            tournament_id=tournament_id,
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            duration_seconds=(end_time - start_time).total_seconds(),
            hands_played=hand_number,
            winner=winner,
            final_standings=final_standings,
            elimination_order=elimination_order,
            model_config=model_config,
            total_api_calls=self.api_calls,
            total_cost=self.total_cost,
            avg_latency_ms=avg_latency,
            decision_stats=self._get_decision_stats(tournament_id),
            variant=variant_label,
            round_winners=round_winners,
            total_resets=total_resets,
        )

        variant_info = f" [{variant_label}]" if variant_label else ""
        resets_info = f", Resets = {total_resets}" if total_resets > 0 else ""
        logger.info(f"Tournament {tournament_id}{variant_info} complete: Winner = {winner}, Hands = {hand_number}{resets_info}")
        return result

    def _get_decision_stats(self, game_id: str) -> Dict:
        """Get decision quality stats from the database."""
        try:
            import sqlite3
            with sqlite3.connect(self.persistence.db_path) as conn:
                cursor = conn.cursor()

                cursor.execute('''
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
                        SUM(CASE WHEN decision_quality = 'marginal' THEN 1 ELSE 0 END) as marginal,
                        SUM(CASE WHEN decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistake,
                        AVG(COALESCE(ev_lost, 0)) as avg_ev_lost
                    FROM player_decision_analysis
                    WHERE game_id = ?
                ''', (game_id,))

                row = cursor.fetchone()
                if row and row[0]:
                    return {
                        "total": row[0],
                        "correct": row[1] or 0,
                        "marginal": row[2] or 0,
                        "mistake": row[3] or 0,
                        "correct_pct": round((row[1] or 0) * 100 / row[0], 1) if row[0] else 0,
                        "avg_ev_lost": round(row[4] or 0, 2),
                    }
        except Exception as e:
            logger.warning(f"Could not get decision stats: {e}")

        return {}

    def run_experiment(self) -> List[TournamentResult]:
        """Run the full experiment (multiple tournaments).

        For A/B testing experiments (with control/variants), runs num_tournaments
        for each variant configuration. Results are tagged with variant labels.

        Supports parallel execution when config.parallel_tournaments > 1.
        Individual tournament failures don't crash the experiment - partial
        results are collected and failures are tracked in the summary.
        """
        # Get all variant configurations
        variant_configs = self.config.get_variant_configs()
        is_ab_test = self.config.control is not None

        # Create experiment record at start
        experiment_config = {
            'name': self.config.name,
            'description': self.config.description,
            'hypothesis': self.config.hypothesis,
            'tags': self.config.tags or [],
            'num_tournaments': self.config.num_tournaments,
            'hands_per_tournament': self.config.hands_per_tournament,
            'num_players': self.config.num_players,
            'starting_stack': self.config.starting_stack,
            'big_blind': self.config.big_blind,
            'model': self.config.model,
            'provider': self.config.provider,
            'personalities': self.config.personalities,
            'capture_prompts': self.config.capture_prompts,
            # Include A/B testing config if present
            'control': self.config.control,
            'variants': self.config.variants,
            # Parallel execution settings
            'parallel_tournaments': self.config.parallel_tournaments,
            'stagger_start_delay': self.config.stagger_start_delay,
        }

        # Only create experiment record if not already set (e.g., from web launcher)
        if self.experiment_id is None:
            try:
                self.experiment_id = self.persistence.create_experiment(experiment_config)
                logger.info(f"Created experiment record with id {self.experiment_id}")
            except Exception as e:
                logger.warning(f"Could not create experiment record: {e}")
                self.experiment_id = None
        else:
            logger.info(f"Using pre-created experiment record with id {self.experiment_id}")

        if is_ab_test:
            logger.info(f"Running A/B test with {len(variant_configs)} variants: {[v[0] for v in variant_configs]}")

        # Build task queue
        tasks = self._build_task_queue(variant_configs)
        logger.info(f"Built task queue with {len(tasks)} tournaments")

        # Execute based on parallelism setting
        if self.config.parallel_tournaments <= 1:
            results, failed = self._run_sequential(tasks)
        else:
            results, failed = self._run_parallel(tasks)

        # Complete experiment with summary (include failure info)
        if self.experiment_id:
            try:
                summary = self._compute_experiment_summary(results, failed)
                # Generate AI interpretation of results (best-effort, won't block completion)
                summary = self._generate_ai_interpretation(summary, failed)
                self.persistence.complete_experiment(self.experiment_id, summary)
            except Exception as e:
                logger.warning(f"Could not complete experiment: {e}")

        return results

    def _build_task_queue(
        self,
        variant_configs: List[Tuple[Optional[str], Dict]]
    ) -> List[TournamentTask]:
        """Build queue of tournament tasks for execution.

        Tasks are ordered to run all variants once before starting second
        tournament of any variant. This ensures early data from all variants
        and graceful degradation if experiment is interrupted.

        Args:
            variant_configs: List of (label, config) tuples from get_variant_configs()

        Returns:
            List of TournamentTask objects
        """
        tasks = []
        global_tournament_num = 0
        base_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        # Iterate tournaments first, then variants within each tournament round
        # This ensures all variants get run before any variant repeats
        for tournament_round in range(self.config.num_tournaments):
            for variant_label, variant_config in variant_configs:
                global_tournament_num += 1

                # Create unique tournament ID including variant label if present
                variant_suffix = f"_{variant_label.lower().replace(' ', '_')}" if variant_label else ""
                tournament_id = f"exp_{self.config.name}_{base_timestamp}{variant_suffix}_{global_tournament_num}"

                tasks.append(TournamentTask(
                    tournament_id=tournament_id,
                    tournament_number=global_tournament_num,
                    variant_label=variant_label,
                    variant_config=variant_config,
                ))

        return tasks

    def _run_sequential(
        self,
        tasks: List[TournamentTask]
    ) -> Tuple[List[TournamentResult], List[TournamentOutcome]]:
        """Run tournaments sequentially (original behavior).

        Args:
            tasks: List of tournament tasks to execute

        Returns:
            Tuple of (successful_results, failed_outcomes)
        """
        results = []
        failed = []

        for task in tasks:
            variant_info = f" [{task.variant_label}]" if task.variant_label else ""
            logger.info(f"Running tournament {task.tournament_number}/{len(tasks)}{variant_info}")

            # Reset per-tournament metrics
            self.api_calls = 0
            self.total_latency = 0
            self.total_cost = 0.0

            start_time = time.time()

            try:
                # Link game to experiment BEFORE running so live_stats can track progress
                if self.experiment_id:
                    try:
                        self.persistence.link_game_to_experiment(
                            experiment_id=self.experiment_id,
                            game_id=task.tournament_id,
                            variant=task.variant_label,
                            variant_config=task.variant_config if task.variant_label else None,
                            tournament_number=task.tournament_number,
                        )
                    except Exception as e:
                        logger.warning(f"Could not link game to experiment: {e}")

                result = self.run_tournament(
                    task.tournament_id,
                    task.variant_label,
                    task.variant_config,
                    task.tournament_number
                )
                results.append(result)

                # Save result to file
                self._save_result(result)

            except TournamentPausedException as e:
                # Tournament was paused - stop processing remaining tasks
                duration = time.time() - start_time
                logger.info(
                    f"Tournament {task.tournament_id} paused at hand {e.hand_number}, "
                    f"stopping experiment"
                )
                failed.append(TournamentOutcome(
                    task=task,
                    error=str(e),
                    error_type='TournamentPausedException',
                    duration_seconds=duration,
                ))
                # Break out of the loop - don't process remaining tournaments
                break

            except Exception as e:
                duration = time.time() - start_time
                error_type = type(e).__name__
                error_msg = str(e)

                logger.error(
                    f"Tournament {task.tournament_id} failed: {error_type}: {error_msg}",
                    exc_info=True
                )

                failed.append(TournamentOutcome(
                    task=task,
                    error=error_msg,
                    error_type=error_type,
                    duration_seconds=duration,
                ))

        logger.info(f"Sequential execution complete: {len(results)} succeeded, {len(failed)} failed")
        return results, failed

    def _run_parallel(
        self,
        tasks: List[TournamentTask]
    ) -> Tuple[List[TournamentResult], List[TournamentOutcome]]:
        """Run tournaments in parallel using ThreadPoolExecutor.

        Args:
            tasks: List of tournament tasks to execute

        Returns:
            Tuple of (successful_results, failed_outcomes)
        """
        results = []
        failed = []

        # Shared rate limit state across all workers
        rate_limit_state = RateLimitState()

        max_workers = min(self.config.parallel_tournaments, len(tasks))
        logger.info(
            f"Starting parallel execution with {max_workers} workers "
            f"for {len(tasks)} tournaments"
        )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit tasks with staggered starts
            futures = {}
            for i, task in enumerate(tasks):
                # Stagger start times to reduce initial burst on LLM API
                if i > 0 and self.config.stagger_start_delay > 0:
                    time.sleep(self.config.stagger_start_delay)

                worker = TournamentWorker(
                    config=self.config,
                    experiment_id=self.experiment_id,
                    db_path=self.db_path,
                    rate_limit_state=rate_limit_state,
                    pause_coordinator=self.pause_coordinator,
                )

                future = executor.submit(worker.execute, task)
                futures[future] = task

            # Collect results as they complete
            for future in as_completed(futures):
                task = futures[future]
                try:
                    outcome = future.result()
                    if outcome.success:
                        results.append(outcome.result)
                        logger.info(
                            f"Tournament {task.tournament_id} completed "
                            f"({len(results)}/{len(tasks)} done)"
                        )
                    else:
                        failed.append(outcome)
                        logger.warning(
                            f"Tournament {task.tournament_id} failed: {outcome.error}"
                        )
                except Exception as e:
                    logger.error(
                        f"Unexpected error collecting result for {task.tournament_id}: {e}"
                    )
                    failed.append(TournamentOutcome(
                        task=task,
                        error=str(e),
                        error_type=type(e).__name__,
                    ))

        logger.info(
            f"Parallel execution complete: {len(results)} succeeded, {len(failed)} failed"
        )
        return results, failed

    def _compute_experiment_summary(
        self,
        results: List[TournamentResult],
        failed: Optional[List[TournamentOutcome]] = None
    ) -> Dict:
        """Compute aggregated summary for the experiment.

        Args:
            results: List of successful tournament results
            failed: Optional list of failed tournament outcomes

        Returns:
            Summary dictionary with aggregated statistics, including per-variant
            stats for A/B testing experiments and any failure information.
        """
        failed = failed or []

        if not results and not failed:
            return {}

        # Handle case where all tournaments failed
        if not results:
            return {
                'tournaments': 0,
                'total_hands': 0,
                'total_api_calls': 0,
                'total_duration_seconds': sum(f.duration_seconds for f in failed),
                'failed_tournaments': [
                    {
                        'tournament_id': f.task.tournament_id,
                        'tournament_number': f.task.tournament_number,
                        'variant': f.task.variant_label,
                        'error': f.error,
                        'error_type': f.error_type,
                        'duration_seconds': f.duration_seconds,
                    }
                    for f in failed
                ],
                'total_failed': len(failed),
                'success_rate': 0,
            }

        # Winner distribution
        winners = {}
        for r in results:
            winners[r.winner] = winners.get(r.winner, 0) + 1

        # Aggregate tournament stats
        total_hands = sum(r.hands_played for r in results)
        total_api_calls = sum(r.total_api_calls for r in results)
        total_duration = sum(r.duration_seconds for r in results)

        # Decision quality (if available)
        total_decisions = 0
        total_correct = 0
        total_mistakes = 0
        total_marginal = 0
        total_ev_lost = 0.0

        for r in results:
            if r.decision_stats:
                total_decisions += r.decision_stats.get('total', 0)
                total_correct += r.decision_stats.get('correct', 0)
                total_mistakes += r.decision_stats.get('mistake', 0)
                total_marginal += r.decision_stats.get('marginal', 0)
                total_ev_lost += r.decision_stats.get('avg_ev_lost', 0) * r.decision_stats.get('total', 0)

        summary = {
            'tournaments': len(results),
            'total_hands': total_hands,
            'total_api_calls': total_api_calls,
            'total_duration_seconds': total_duration,
            'avg_hands_per_tournament': round(total_hands / len(results), 1),
            'winners': winners,
        }

        if total_decisions > 0:
            summary['decision_quality'] = {
                'total_decisions': total_decisions,
                'correct': total_correct,
                'marginal': total_marginal,
                'mistakes': total_mistakes,
                'correct_pct': round(total_correct * 100 / total_decisions, 1),
                'mistake_pct': round(total_mistakes * 100 / total_decisions, 1),
                'avg_ev_lost': round(total_ev_lost / total_decisions, 2),
            }

        # Compute per-variant stats for A/B testing experiments
        variant_labels = set(r.variant for r in results if r.variant is not None)
        if variant_labels:
            summary['variants'] = self._compute_variant_summaries(results)

        # Include failure information if any tournaments failed
        if failed:
            summary['failed_tournaments'] = [
                {
                    'tournament_id': f.task.tournament_id,
                    'tournament_number': f.task.tournament_number,
                    'variant': f.task.variant_label,
                    'error': f.error,
                    'error_type': f.error_type,
                    'duration_seconds': f.duration_seconds,
                }
                for f in failed
            ]
            summary['total_failed'] = len(failed)
            summary['success_rate'] = round(
                len(results) * 100 / (len(results) + len(failed)), 1
            ) if (results or failed) else 0

        return summary

    def _compute_variant_summaries(self, results: List[TournamentResult]) -> Dict[str, Dict]:
        """Compute per-variant statistics for A/B testing experiments.

        Args:
            results: List of tournament results (may include multiple variants)

        Returns:
            Dictionary mapping variant labels to their summary stats
        """
        # Group results by variant
        by_variant: Dict[str, List[TournamentResult]] = {}
        for r in results:
            label = r.variant or 'default'
            if label not in by_variant:
                by_variant[label] = []
            by_variant[label].append(r)

        variant_summaries = {}
        for label, variant_results in by_variant.items():
            # Winner distribution for this variant
            winners = {}
            for r in variant_results:
                winners[r.winner] = winners.get(r.winner, 0) + 1

            # Aggregate stats for this variant
            total_hands = sum(r.hands_played for r in variant_results)
            total_api_calls = sum(r.total_api_calls for r in variant_results)
            total_duration = sum(r.duration_seconds for r in variant_results)

            # Decision quality for this variant
            total_decisions = 0
            total_correct = 0
            total_mistakes = 0
            total_marginal = 0
            total_ev_lost = 0.0

            for r in variant_results:
                if r.decision_stats:
                    total_decisions += r.decision_stats.get('total', 0)
                    total_correct += r.decision_stats.get('correct', 0)
                    total_mistakes += r.decision_stats.get('mistake', 0)
                    total_marginal += r.decision_stats.get('marginal', 0)
                    total_ev_lost += r.decision_stats.get('avg_ev_lost', 0) * r.decision_stats.get('total', 0)

            variant_summary = {
                'tournaments': len(variant_results),
                'total_hands': total_hands,
                'total_api_calls': total_api_calls,
                'total_duration_seconds': total_duration,
                'avg_hands_per_tournament': round(total_hands / len(variant_results), 1) if variant_results else 0,
                'winners': winners,
                'model_config': variant_results[0].model_config if variant_results else {},
            }

            if total_decisions > 0:
                variant_summary['decision_quality'] = {
                    'total_decisions': total_decisions,
                    'correct': total_correct,
                    'marginal': total_marginal,
                    'mistakes': total_mistakes,
                    'correct_pct': round(total_correct * 100 / total_decisions, 1),
                    'mistake_pct': round(total_mistakes * 100 / total_decisions, 1),
                    'avg_ev_lost': round(total_ev_lost / total_decisions, 2),
                }

            # Add latency metrics from database for this variant
            game_ids = [r.tournament_id for r in variant_results]
            latency_metrics = self._get_latency_metrics_for_games(game_ids)
            if latency_metrics:
                variant_summary['latency_metrics'] = latency_metrics

            # Add error stats from database for this variant
            error_stats = self._get_error_stats_for_games(game_ids)
            if error_stats:
                variant_summary['error_stats'] = error_stats

            variant_summaries[label] = variant_summary

        return variant_summaries

    def _get_latency_metrics_for_games(self, game_ids: List[str]) -> Optional[Dict]:
        """Get aggregated latency metrics for a list of games.

        Args:
            game_ids: List of game IDs to aggregate latency from

        Returns:
            Dictionary with latency metrics (avg, p50, p95, p99) or None if no data
        """
        if not game_ids:
            return None

        try:
            import sqlite3

            with sqlite3.connect(self.persistence.db_path) as conn:
                placeholders = ','.join('?' * len(game_ids))
                cursor = conn.execute(f"""
                    SELECT latency_ms FROM api_usage
                    WHERE game_id IN ({placeholders}) AND latency_ms IS NOT NULL
                """, game_ids)
                latencies = [row[0] for row in cursor.fetchall()]

                if latencies:
                    return {
                        'avg_ms': round(float(np.mean(latencies)), 2),
                        'p50_ms': round(float(np.percentile(latencies, 50)), 2),
                        'p95_ms': round(float(np.percentile(latencies, 95)), 2),
                        'p99_ms': round(float(np.percentile(latencies, 99)), 2),
                        'count': len(latencies),
                    }
        except Exception as e:
            logger.warning(f"Could not get latency metrics: {e}")

        return None

    def _get_error_stats_for_games(self, game_ids: List[str]) -> Optional[Dict]:
        """Get aggregated error stats for a list of games.

        Args:
            game_ids: List of game IDs to aggregate errors from

        Returns:
            Dictionary with error stats or None if no data
        """
        if not game_ids:
            return None

        try:
            import sqlite3

            with sqlite3.connect(self.persistence.db_path) as conn:
                placeholders = ','.join('?' * len(game_ids))

                # Get total calls and error counts
                cursor = conn.execute(f"""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors
                    FROM api_usage
                    WHERE game_id IN ({placeholders})
                """, game_ids)
                row = cursor.fetchone()

                if row and row[0] > 0:
                    total, errors = row[0], row[1] or 0

                    # Get error breakdown by error_code
                    cursor = conn.execute(f"""
                        SELECT error_code, COUNT(*) as count
                        FROM api_usage
                        WHERE game_id IN ({placeholders}) AND status = 'error'
                        GROUP BY error_code
                        ORDER BY count DESC
                    """, game_ids)
                    by_code = {row[0]: row[1] for row in cursor.fetchall()}

                    return {
                        'total_calls': total,
                        'errors': errors,
                        'error_rate': round(errors * 100 / total, 2) if total > 0 else 0,
                        'by_error_code': by_code,
                    }
        except Exception as e:
            logger.warning(f"Could not get error stats: {e}")

        return None

    def _generate_ai_interpretation(
        self,
        summary: Dict,
        failed: Optional[List[TournamentOutcome]] = None
    ) -> Dict:
        """Generate AI interpretation of experiment results.

        Uses the experiment design assistant to analyze results and suggest
        follow-up experiments. Includes the original design conversation
        if available for context continuity.

        Args:
            summary: The computed experiment summary
            failed: Optional list of failed tournament outcomes

        Returns:
            Updated summary dict with 'ai_interpretation' field added
        """
        # Skip if no tournaments completed
        if summary.get('tournaments', 0) == 0:
            logger.info("Skipping AI interpretation: no completed tournaments")
            return summary

        try:
            # Get experiment config from persistence
            experiment_data = None
            if self.experiment_id:
                experiment_data = self.persistence.get_experiment(self.experiment_id)

            if not experiment_data:
                logger.warning("Could not retrieve experiment data for AI interpretation")
                return summary

            config = experiment_data.get('config', {})

            # Build design context from conversation history if available
            design_conversation = config.get('design_conversation', [])
            if design_conversation:
                design_context = "Below is the conversation where you helped design this experiment:"
            else:
                design_context = "No design conversation was recorded for this experiment."

            # Build system prompt for analysis
            system_prompt = f"""You are the experiment design assistant for AI poker tournament testing. You helped design this experiment, and now you're analyzing the results.

{design_context}

Given the experiment configuration and results, provide a concise analysis:

## Summary
1-2 sentences: What was tested and what was the outcome?

## Verdict
One sentence: Did the hypothesis hold? Which variant won (if A/B test)? Include the key numbers.

## Surprises
Only list genuinely unexpected or anomalous findings. If results were as expected, return an empty array.

## Next Steps
Suggest 2-3 follow-up experiments that can be configured with these options:
- num_tournaments: Number of tournaments to run (more = less variance)
- hands_per_tournament: Hands per game (more = longer games)
- model/provider: LLM model (gpt-4o-mini, claude-sonnet, gemini-flash, etc.)
- personalities: Which AI personalities play
- A/B testing: control vs variant configs with different models/providers
- prompt_config: Toggle features like pot_odds, hand_strength, session_memory, strategic_reflection

Return as objects with:
- hypothesis: Testable hypothesis that can be verified by changing the above configs
- description: What config changes to make and why

Example hypotheses:
- "GPT-4o-mini makes fewer mistakes than Claude Sonnet" → A/B test with model variants
- "More hands per tournament reduces winner variance" → Increase hands_per_tournament
- "Disabling strategic_reflection speeds up decisions without hurting quality" → A/B test prompt_config

Be extremely concise. Don't repeat information across sections.
Respond in JSON format with keys: summary, verdict, surprises (array, can be empty), next_steps (array of {hypothesis, description})"""

            # Build results context
            results_context = {
                'experiment': {
                    'name': config.get('name'),
                    'description': config.get('description'),
                    'hypothesis': config.get('hypothesis'),
                    'tags': config.get('tags'),
                },
                'config': {
                    'num_tournaments': config.get('num_tournaments'),
                    'hands_per_tournament': config.get('hands_per_tournament'),
                    'num_players': config.get('num_players'),
                    'model': config.get('model'),
                    'provider': config.get('provider'),
                },
                'results': {
                    'tournaments_completed': summary.get('tournaments', 0),
                    'total_hands': summary.get('total_hands', 0),
                    'total_duration_seconds': summary.get('total_duration_seconds', 0),
                    'winners_distribution': summary.get('winners', {}),
                },
            }

            # Add decision quality if available
            if summary.get('decision_quality'):
                results_context['results']['decision_quality'] = summary['decision_quality']

            # Add A/B test info if present
            if config.get('control'):
                results_context['ab_test'] = {
                    'control_label': config['control'].get('label'),
                    'variant_labels': [v.get('label') for v in config.get('variants', [])],
                }

            # Get live stats for rich per-variant data (includes cost metrics)
            live_stats = self.persistence.get_experiment_live_stats(self.experiment_id)
            if live_stats and live_stats.get('by_variant'):
                per_variant = {}
                for label, v in live_stats['by_variant'].items():
                    per_variant[label] = {
                        'model': v.get('model'),
                        'provider': v.get('provider'),
                        'total_decisions': v.get('decision_quality', {}).get('total', 0),
                        'correct_pct': v.get('decision_quality', {}).get('correct_pct', 0),
                        'avg_latency_ms': v.get('latency_metrics', {}).get('avg_ms'),
                        'p95_latency_ms': v.get('latency_metrics', {}).get('p95_ms'),
                        'total_cost': v.get('cost_metrics', {}).get('total_cost'),
                        'avg_cost_per_decision': v.get('cost_metrics', {}).get('avg_cost_per_decision'),
                    }
                results_context['results']['per_variant_stats'] = per_variant
            elif summary.get('variants'):
                results_context['results']['per_variant_stats'] = summary['variants']

            # Add failure info if any
            if failed:
                results_context['failures'] = {
                    'count': len(failed),
                    'success_rate': summary.get('success_rate'),
                }

            # Build messages array
            messages = [{"role": "system", "content": system_prompt}]

            # Include original design conversation if available
            for msg in design_conversation:
                # Only include user and assistant messages (not system)
                if msg.get('role') in ('user', 'assistant'):
                    messages.append(msg)

            # Add final user message with results
            messages.append({
                "role": "user",
                "content": f"The experiment has completed. Here are the results:\n\n{json.dumps(results_context, indent=2)}\n\nPlease analyze these results."
            })

            # Make LLM call
            client = LLMClient(model=ASSISTANT_MODEL, provider=ASSISTANT_PROVIDER)
            response = client.complete(
                messages=messages,
                json_format=True,
                call_type=CallType.EXPERIMENT_ANALYSIS,
                game_id=f"experiment_{self.experiment_id}" if self.experiment_id else None,
                owner_id=self._owner_id,
            )

            # Parse response
            interpretation = json.loads(response.content)
            interpretation['generated_at'] = datetime.now().isoformat()
            interpretation['model_used'] = client.model

            summary['ai_interpretation'] = interpretation
            logger.info(f"Generated AI interpretation for experiment {self.experiment_id}")

        except json.JSONDecodeError as e:
            logger.warning(f"AI interpretation returned invalid JSON: {e}")
            summary['ai_interpretation'] = {
                'error': f'Invalid JSON response: {str(e)}',
                'generated_at': datetime.now().isoformat(),
            }
        except Exception as e:
            logger.warning(f"AI interpretation failed: {e}")
            summary['ai_interpretation'] = {
                'error': str(e),
                'generated_at': datetime.now().isoformat(),
            }

        return summary

    def _save_result(self, result: TournamentResult):
        """Save tournament result to JSON file."""
        results_dir = project_root / "experiments" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{result.tournament_id}.json"
        filepath = results_dir / filename

        with open(filepath, 'w') as f:
            json.dump(asdict(result), f, indent=2)

        logger.info(f"Saved results to {filepath}")


def print_summary(results: List[TournamentResult]):
    """Print a summary of experiment results."""
    print("\n" + "=" * 60)
    print("EXPERIMENT SUMMARY")
    print("=" * 60)

    if not results:
        print("No results to summarize.")
        return

    config = results[0].model_config
    print(f"Model: {config.get('provider', 'unknown')}/{config.get('model', 'unknown')}")
    print(f"Tournaments: {len(results)}")

    # Aggregate stats
    total_hands = sum(r.hands_played for r in results)
    total_api_calls = sum(r.total_api_calls for r in results)
    total_duration = sum(r.duration_seconds for r in results)

    # Winner distribution
    winners = {}
    for r in results:
        winners[r.winner] = winners.get(r.winner, 0) + 1

    print(f"\nTotal hands played: {total_hands}")
    print(f"Total API calls: {total_api_calls}")
    print(f"Total duration: {total_duration:.1f}s")
    print(f"Avg hands/tournament: {total_hands / len(results):.1f}")

    print("\nWinner distribution:")
    for name, count in sorted(winners.items(), key=lambda x: -x[1]):
        print(f"  {name}: {count} wins ({count*100/len(results):.0f}%)")

    # Decision quality (if available)
    decision_stats = [r.decision_stats for r in results if r.decision_stats]
    if decision_stats:
        total_decisions = sum(s.get('total', 0) for s in decision_stats)
        total_correct = sum(s.get('correct', 0) for s in decision_stats)
        total_mistakes = sum(s.get('mistake', 0) for s in decision_stats)

        if total_decisions > 0:
            print(f"\nDecision Quality:")
            print(f"  Total decisions: {total_decisions}")
            print(f"  Correct: {total_correct} ({total_correct*100/total_decisions:.1f}%)")
            print(f"  Mistakes: {total_mistakes} ({total_mistakes*100/total_decisions:.1f}%)")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Run AI poker tournament experiments")
    parser.add_argument("--experiment", "-e", default="default", help="Experiment name")
    parser.add_argument("--description", "-d", default="", help="Experiment description")
    parser.add_argument("--hypothesis", default="", help="What we're testing")
    parser.add_argument("--tags", default="", help="Comma-separated tags for categorization")
    parser.add_argument("--no-capture", action="store_true", help="Disable prompt capture")
    parser.add_argument("--tournaments", "-t", type=int, default=1, help="Number of tournaments")
    parser.add_argument("--hands", "-n", type=int, default=100, help="Max hands per tournament")
    parser.add_argument("--players", "-p", type=int, default=4, help="Number of players")
    parser.add_argument("--model", "-m", default="gpt-5-nano", help="LLM model to use")
    parser.add_argument("--provider", default="openai", help="LLM provider")
    parser.add_argument("--personalities", help="Comma-separated list of personalities")
    parser.add_argument("--stack", type=int, default=10000, help="Starting stack")
    parser.add_argument("--blind", type=int, default=100, help="Big blind")
    parser.add_argument("--seed", type=int, help="Random seed for reproducibility")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    # Parallel execution options
    parser.add_argument(
        "--parallel", "-P",
        type=int,
        default=1,
        help="Number of tournaments to run in parallel (default: 1 = sequential)"
    )
    parser.add_argument(
        "--stagger-delay",
        type=float,
        default=0.0,
        help="Seconds to wait between starting parallel workers (default: 0)"
    )
    parser.add_argument(
        "--rate-limit-backoff",
        type=float,
        default=30.0,
        help="Base backoff seconds when rate limited (default: 30)"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Parse personalities
    personalities = None
    if args.personalities:
        personalities = [p.strip() for p in args.personalities.split(",")]

    # Parse tags
    tags = None
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",")]

    config = ExperimentConfig(
        name=args.experiment,
        description=args.description,
        hypothesis=args.hypothesis,
        tags=tags,
        capture_prompts=not args.no_capture,
        num_tournaments=args.tournaments,
        hands_per_tournament=args.hands,
        num_players=args.players,
        starting_stack=args.stack,
        big_blind=args.blind,
        model=args.model,
        provider=args.provider,
        personalities=personalities,
        random_seed=args.seed,
        parallel_tournaments=args.parallel,
        stagger_start_delay=args.stagger_delay,
        rate_limit_backoff_seconds=args.rate_limit_backoff,
    )

    print(f"Running experiment: {config.name}")
    print(f"  Model: {config.provider}/{config.model}")
    print(f"  Tournaments: {config.num_tournaments}")
    print(f"  Hands per tournament: {config.hands_per_tournament}")
    print(f"  Players: {config.num_players}")
    if config.parallel_tournaments > 1:
        print(f"  Parallel workers: {config.parallel_tournaments}")
        if config.stagger_start_delay > 0:
            print(f"  Stagger delay: {config.stagger_start_delay}s")

    runner = AITournamentRunner(config)
    results = runner.run_experiment()

    print_summary(results)

    return 0


if __name__ == "__main__":
    sys.exit(main())
