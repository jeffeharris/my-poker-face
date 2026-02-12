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
from poker.rule_based_controller import RuleBasedController, RuleConfig, CHAOS_BOTS
from poker.hybrid_ai_controller import HybridAIController
from poker.repositories import create_repos
from poker.memory.memory_manager import AIMemoryManager
from poker.utils import get_celebrities
from poker.prompt_config import PromptConfig
from poker.pressure_detector import PressureEventDetector
from poker.moment_analyzer import MomentAnalyzer
from poker.psychology_pipeline import PsychologyPipeline, PsychologyContext
from experiments.pause_coordinator import PauseCoordinator
from core.llm import LLMClient, CallType
from flask_app.config import get_assistant_model, get_assistant_provider


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


class TournamentSupersededException(Exception):
    """Raised when a tournament is superseded by a resume operation.

    This happens when another process has acquired the resume lock,
    indicating this process should exit gracefully.
    """

    def __init__(self, tournament_id: str, message: str = "Tournament superseded by resume"):
        self.tournament_id = tournament_id
        super().__init__(f"{message}: {tournament_id}")


@dataclass
class HandResult:
    """Result from running a single hand.

    Replaces the untyped Dict return with a typed dataclass.

    Status values:
    - 'continue': Game should continue normally
    - 'paused': Tournament was paused (user request)
    - 'end': Tournament should end (only 1 player with chips before hand started)
    - 'reset_needed': Only 1 player with chips after hand (may need stack reset)
    """
    status: str
    all_in_winners: List[str] = field(default_factory=list)

    @property
    def should_continue(self) -> bool:
        """True if game should continue to next hand."""
        return self.status == 'continue'

    @property
    def is_paused(self) -> bool:
        """True if tournament was paused."""
        return self.status == 'paused'

    @property
    def needs_reset(self) -> bool:
        """True if tournament ended and may need stack reset."""
        return self.status == 'reset_needed'

    @property
    def is_end(self) -> bool:
        """True if tournament should end (only 1 player before hand started)."""
        return self.status == 'end'


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
    elimination_order: List[str]  # Legacy: names only (for backwards compatibility)
    model_config: Dict
    total_api_calls: int
    total_cost: float
    avg_latency_ms: float
    decision_stats: Dict
    variant: Optional[str] = None  # Variant label for A/B testing
    round_winners: List[str] = field(default_factory=list)  # Winners of each "round" before reset
    total_resets: int = 0  # How many times stacks were reset
    # Detailed elimination tracking (new)
    eliminations: List[Dict] = field(default_factory=list)  # [{player_name, hand_number, round_number}]


@dataclass
class ControlConfig:
    """Control (baseline) configuration for A/B testing."""
    label: str
    model: Optional[str] = None
    provider: Optional[str] = None
    game_mode: Optional[str] = None  # 'casual', 'standard', 'pro', 'competitive'
    prompt_config: Optional[Dict] = None
    prompt_preset_id: Optional[int] = None  # Load prompt config from saved preset
    guidance_injection: Optional[str] = None  # Extra text appended to decision prompts
    enable_psychology: bool = False  # Enable tilt + emotional state generation
    enable_playstyle: Optional[bool] = None  # None=inherit from enable_psychology, True/False=override
    enable_commentary: bool = False  # Enable commentary generation
    reasoning_effort: Optional[str] = None  # 'minimal', 'low', 'medium', 'high'


@dataclass
class VariantConfig:
    """Variant configuration that overrides control for A/B testing."""
    label: str
    model: Optional[str] = None
    provider: Optional[str] = None
    personality: Optional[str] = None  # Per-variant personality assignment
    game_mode: Optional[str] = None  # 'casual', 'standard', 'pro', 'competitive'
    prompt_config: Optional[Dict] = None
    prompt_preset_id: Optional[int] = None  # Load prompt config from saved preset
    guidance_injection: Optional[str] = None  # Extra text appended to decision prompts
    enable_psychology: bool = False  # Enable tilt + emotional state generation
    enable_playstyle: Optional[bool] = None  # None=inherit from enable_psychology, True/False=override
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
    # Rule-based bot support (chaos monkeys)
    player_types: Optional[Dict[str, Dict]] = None  # {player_name: {"type": "rule_bot", "strategy": "always_fold"}}

    def __post_init__(self):
        """Validate control/variants structure."""
        VALID_GAME_MODES = {'casual', 'standard', 'pro', 'competitive', None}

        if self.control is not None:
            if not isinstance(self.control, dict):
                raise ValueError("control must be a dict")
            if not self.control.get('label'):
                raise ValueError("control.label is required")
            # Validate game_mode in control
            control_game_mode = self.control.get('game_mode')
            if control_game_mode and control_game_mode not in VALID_GAME_MODES:
                raise ValueError(f"Invalid control game_mode: {control_game_mode}. Valid: casual, standard, pro, competitive")

        if self.variants is not None:
            if not isinstance(self.variants, list):
                raise ValueError("variants must be a list")
            for i, v in enumerate(self.variants):
                if not isinstance(v, dict):
                    raise ValueError(f"variants[{i}] must be a dict")
                if not v.get('label'):
                    raise ValueError(f"variants[{i}].label is required")
                # Validate game_mode in variant
                variant_game_mode = v.get('game_mode')
                if variant_game_mode and variant_game_mode not in VALID_GAME_MODES:
                    raise ValueError(f"Invalid variants[{i}] game_mode: {variant_game_mode}. Valid: casual, standard, pro, competitive")

    def get_variant_configs(self) -> List[Tuple[str, Dict]]:
        """
        Returns list of (label, effective_config) tuples for all variants.

        If control is None, returns a single entry with legacy flat fields.
        If control is set, returns control + all variants with inherited fields.

        Supports new fields:
        - personality: Per-variant personality assignment
        - prompt_preset_id: Load prompt config from saved preset
        - guidance_injection: Extra text appended to decision prompts
        """
        # Legacy mode: no control/variants defined
        if self.control is None:
            return [(None, {
                'model': self.model,
                'provider': self.provider,
            })]

        # A/B testing mode: control + variants
        result = []

        # Control is always first - always uses experiment-level model/provider
        control_config = {
            'model': self.model,      # Always use experiment-level
            'provider': self.provider, # Always use experiment-level
            'game_mode': self.control.get('game_mode'),
            'prompt_config': self.control.get('prompt_config'),
            'enable_psychology': self.control.get('enable_psychology', False),
            'enable_playstyle': self.control.get('enable_playstyle'),
            'enable_commentary': self.control.get('enable_commentary', False),
            'reasoning_effort': self.control.get('reasoning_effort'),
            # New fields for enhanced variant support
            'guidance_injection': self.control.get('guidance_injection'),
            'prompt_preset_id': self.control.get('prompt_preset_id'),
        }
        control_label = self.control.get('label', 'Control')
        result.append((control_label, control_config))

        # Add variants (inherit model/provider from experiment, other settings from control)
        for variant in (self.variants or []):
            variant_config = {
                # Model/provider inherit from experiment-level, not control
                'model': variant.get('model') or self.model,
                'provider': variant.get('provider') or self.provider,
                # Game mode - use variant's or inherit from control
                'game_mode': variant.get('game_mode') if 'game_mode' in variant else control_config.get('game_mode'),
                # Use explicit None check - empty dict {} is a valid config
                'prompt_config': variant.get('prompt_config') if 'prompt_config' in variant else control_config.get('prompt_config'),
                # Psychology flags - inherit from control if not specified
                'enable_psychology': variant.get('enable_psychology', control_config.get('enable_psychology', False)),
                'enable_playstyle': variant.get('enable_playstyle') if 'enable_playstyle' in variant else control_config.get('enable_playstyle'),
                'enable_commentary': variant.get('enable_commentary', control_config.get('enable_commentary', False)),
                # Reasoning effort - inherit from control if not specified
                'reasoning_effort': variant.get('reasoning_effort') if 'reasoning_effort' in variant else control_config.get('reasoning_effort'),
                # New fields - personality is variant-specific (not inherited)
                'personality': variant.get('personality'),
                # Prompt preset ID - use variant's or inherit from control
                'prompt_preset_id': variant.get('prompt_preset_id') if 'prompt_preset_id' in variant else control_config.get('prompt_preset_id'),
                # Guidance injection - use variant's or inherit from control
                'guidance_injection': variant.get('guidance_injection') if 'guidance_injection' in variant else control_config.get('guidance_injection'),
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
        runner = None

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
                    runner.experiment_repo.link_game_to_experiment(
                        experiment_id=self.experiment_id,
                        game_id=task.tournament_id,
                        variant=task.variant_label,
                        variant_config=task.variant_config,
                        tournament_number=task.tournament_number,
                    )
                    # Record process_id and initial heartbeat for resume tracking
                    runner.experiment_repo.update_experiment_game_heartbeat(
                        task.tournament_id, 'processing', process_id=os.getpid()
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

        except TournamentSupersededException as e:
            # Tournament was superseded by a resume - not an error, graceful exit
            duration = time.time() - start_time
            logger.info(
                f"Tournament {task.tournament_id} superseded by resume after {duration:.1f}s"
            )
            return TournamentOutcome(
                task=task,
                error=str(e),
                error_type='TournamentSupersededException',
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

        finally:
            # Cleanup runner resources to prevent accumulation during parallel execution
            if runner:
                runner.cleanup()


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
        repos = create_repos(self.db_path)
        self.game_repo = repos['game_repo']
        self.experiment_repo = repos['experiment_repo']
        self.prompt_preset_repo = repos['prompt_preset_repo']
        self.decision_analysis_repo = repos['decision_analysis_repo']
        self.capture_label_repo = repos['capture_label_repo']
        self.tournament_repo = repos['tournament_repo']
        self.hand_history_repo = repos['hand_history_repo']
        self.all_personalities = get_celebrities()

        # Pressure event detection and persistence for psychology system
        self.pressure_detector = PressureEventDetector()
        self.pressure_event_repo = repos.get('pressure_event_repo')

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

    def cleanup(self) -> None:
        """Release resources held by the tournament runner.

        Called automatically by TournamentWorker on failure or pause to ensure
        resources don't accumulate during parallel execution.
        """
        # Clear any controller references to help garbage collection
        # Controllers hold Assistant objects with conversation memory
        if hasattr(self, '_controllers'):
            self._controllers.clear()
            self._controllers = None

    def _save_checkpoint(self, tournament_id: str, state_machine) -> None:
        """Save game checkpoint for resume capability."""
        if tournament_id and self.experiment_id:
            try:
                self.game_repo.save_game(tournament_id, state_machine, self._owner_id)
            except Exception as e:
                logger.warning(f"Checkpoint save failed: {e}")

    def _load_game_mode_preset(self, game_mode: str) -> PromptConfig:
        """Load a game mode as a preset from the database.

        Game modes (casual, standard, pro, competitive) are stored as system presets
        in the prompt_presets table, unifying them with user-defined presets.

        Args:
            game_mode: The game mode name ('casual', 'standard', 'pro', 'competitive')

        Returns:
            PromptConfig with the preset's settings applied
        """
        preset = self.prompt_preset_repo.get_prompt_preset_by_name(game_mode)
        if preset and preset.get('prompt_config'):
            return PromptConfig.from_dict(preset['prompt_config'])
        else:
            # Fallback to hardcoded mode if preset not found (e.g., migration not run)
            logger.warning(f"Preset '{game_mode}' not found in database, using fallback")
            return PromptConfig.from_mode_name(game_mode)

    def select_personalities(self) -> Tuple[List[str], Dict[str, Dict]]:
        """Select personalities for the tournament.

        Supports both simple string names and objects with per-player config:
        ["Batman", {"name": "Sherlock", "game_mode": "pro", "llm_config": {...}}]

        Returns:
            Tuple of (player_names, player_configs) where player_configs maps
            player name to their individual settings (game_mode, llm_config, prompt_config)
        """
        player_names = []
        player_configs = {}  # {player_name: {game_mode: ..., llm_config: ..., prompt_config: ...}}

        if self.config.personalities:
            for p in self.config.personalities[:self.config.num_players]:
                if isinstance(p, str):
                    player_names.append(p)
                elif isinstance(p, dict):
                    name = p.get('name')
                    if name:
                        player_names.append(name)
                        # Extract per-player config
                        config = {}
                        if 'game_mode' in p:
                            config['game_mode'] = p['game_mode']
                        if 'llm_config' in p:
                            config['llm_config'] = p['llm_config']
                        if 'prompt_config' in p:
                            config['prompt_config'] = p['prompt_config']
                        if config:
                            player_configs[name] = config
        else:
            # Random selection from available personalities
            available = self.all_personalities if isinstance(self.all_personalities, list) else list(self.all_personalities.keys())
            if self.config.random_seed:
                random.seed(self.config.random_seed)
            player_names = random.sample(available, min(self.config.num_players, len(available)))

        return player_names, player_configs

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
        player_names, per_player_configs = self.select_personalities()
        logger.info(f"Tournament {tournament_id}: Players = {player_names}")
        if per_player_configs:
            logger.info(f"Tournament {tournament_id}: Per-player configs = {list(per_player_configs.keys())}")

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

        # Store seed on state machine so it's available for hand history recording
        if deck_seed is not None:
            state_machine.current_hand_seed = deck_seed

        # Create memory manager for hand tracking
        # Pass commentary_enabled from variant config (defaults to False for experiments)
        commentary_enabled = variant_config.get('enable_commentary', False) if variant_config else False
        memory_manager = AIMemoryManager(
            game_id=tournament_id,
            db_path=self.db_path,
            owner_id=self._owner_id,
            commentary_enabled=commentary_enabled
        )
        # Set persistence so hand history is saved to database
        memory_manager.set_hand_history_repo(self.hand_history_repo)

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

        # Extract and resolve prompt_config from variant
        # Priority: 1) inline prompt_config (overrides game_mode), 2) load from preset, 3) game_mode, 4) defaults
        prompt_config_dict = variant_config.get('prompt_config') if variant_config else None
        prompt_preset_id = variant_config.get('prompt_preset_id') if variant_config else None
        guidance_injection = variant_config.get('guidance_injection') if variant_config else None
        game_mode = variant_config.get('game_mode') if variant_config else None

        # Build base config from game_mode (if set), else defaults
        # game_mode is now resolved via database presets (unified with prompt_preset system)
        if game_mode:
            base_config = self._load_game_mode_preset(game_mode)
        else:
            base_config = PromptConfig()

        if prompt_config_dict is not None:
            # Merge: game_mode provides base, prompt_config_dict overrides
            prompt_config = base_config.copy(**prompt_config_dict)
        elif prompt_preset_id is not None:
            # Load from preset and merge with base
            preset = self.prompt_preset_repo.get_prompt_preset(prompt_preset_id)
            if preset and preset.get('prompt_config'):
                prompt_config = base_config.copy(**preset['prompt_config'])
                # Use preset's guidance_injection if not overridden by variant
                if not guidance_injection and preset.get('guidance_injection'):
                    guidance_injection = preset['guidance_injection']
            else:
                prompt_config = base_config
                logger.warning(f"Prompt preset {prompt_preset_id} not found, using game_mode/defaults")
        else:
            prompt_config = base_config

        # Apply guidance injection to prompt config if set
        if guidance_injection and prompt_config:
            prompt_config = prompt_config.copy(guidance_injection=guidance_injection)
        elif guidance_injection:
            prompt_config = PromptConfig(guidance_injection=guidance_injection)

        # Apply enable_playstyle toggle (controls zone_benefits on prompt_config)
        enable_psychology = variant_config.get('enable_psychology', False) if variant_config else False
        enable_playstyle = variant_config.get('enable_playstyle') if variant_config else None
        if enable_playstyle is None:
            enable_playstyle = enable_psychology  # Inherit from psychology flag
        if not enable_playstyle:
            prompt_config = prompt_config.copy(zone_benefits=False)

        controllers = {}
        for player in game_state.players:
            # Check for per-player config override
            player_cfg = per_player_configs.get(player.name, {})

            # Resolve per-player LLM config (merge with variant default)
            if player_cfg.get('llm_config'):
                player_llm_config = {**llm_config, **player_cfg['llm_config']}
            else:
                player_llm_config = llm_config

            # Resolve per-player prompt config (per-player game_mode/prompt_config overrides variant)
            if player_cfg.get('game_mode') or player_cfg.get('prompt_config'):
                # Start with per-player game_mode or fall back to variant game_mode
                player_game_mode = player_cfg.get('game_mode') or game_mode
                if player_game_mode:
                    player_base_config = self._load_game_mode_preset(player_game_mode)
                else:
                    player_base_config = PromptConfig()

                # Apply per-player prompt_config overrides
                if player_cfg.get('prompt_config'):
                    player_prompt_config = player_base_config.copy(**player_cfg['prompt_config'])
                else:
                    player_prompt_config = player_base_config

                # Apply guidance injection if set at variant level
                if guidance_injection:
                    player_prompt_config = player_prompt_config.copy(guidance_injection=guidance_injection)

                logger.debug(f"Player {player.name} using custom config: game_mode={player_cfg.get('game_mode')}")
            else:
                player_prompt_config = prompt_config

            # Check if this player should use a special controller type
            player_type_config = (self.config.player_types or {}).get(player.name, {})
            player_type = player_type_config.get('type')

            if player_type == 'rule_bot':
                # Create a rule-based controller instead of AI
                strategy = player_type_config.get('strategy', 'always_fold')
                config_path = player_type_config.get('config_path')

                if config_path:
                    # Load from config file
                    rule_config = RuleConfig.from_json_file(config_path)
                elif strategy in CHAOS_BOTS:
                    # Use built-in strategy
                    rule_config = CHAOS_BOTS[strategy]
                else:
                    # Custom strategy name - try to use it directly
                    rule_config = RuleConfig(strategy=strategy, name=player.name)

                controller = RuleBasedController(
                    player_name=player.name,
                    state_machine=state_machine,
                    config=rule_config,
                    game_id=tournament_id,
                )
                logger.info(f"Player {player.name} using rule-based controller: {strategy}")
            elif player_type == 'hybrid':
                # Create a hybrid controller (LLM picks from rule-bounded options)
                controller = HybridAIController(
                    player_name=player.name,
                    state_machine=state_machine,
                    llm_config=player_llm_config,
                    game_id=tournament_id,
                    owner_id=self._owner_id,
                    prompt_config=player_prompt_config,
                    capture_label_repo=self.capture_label_repo,
                    decision_analysis_repo=self.decision_analysis_repo,
                    session_memory=memory_manager.get_session_memory(player.name),
                    opponent_model_manager=memory_manager.get_opponent_model_manager(),
                )
                logger.info(f"Player {player.name} using hybrid AI controller")
            else:
                # Use standard AI controller
                controller = AIPlayerController(
                    player_name=player.name,
                    state_machine=state_machine,
                    llm_config=player_llm_config,
                    game_id=tournament_id,
                    owner_id=self._owner_id,
                    debug_capture=self.config.capture_prompts,
                    prompt_config=player_prompt_config,
                    capture_label_repo=self.capture_label_repo,
                    decision_analysis_repo=self.decision_analysis_repo,
                    session_memory=memory_manager.get_session_memory(player.name),
                    opponent_model_manager=memory_manager.get_opponent_model_manager(),
                )
            controllers[player.name] = controller
            # Initialize memory manager for this player
            memory_manager.initialize_for_player(player.name)
            controller.opponent_model_manager = memory_manager.get_opponent_model_manager()

        return state_machine, controllers, memory_manager

    def _process_psychology(
        self,
        game_state,
        controllers: Dict[str, AIPlayerController],
        winner_info: Dict,
        winner_names: List[str],
        hand_number: int,
        game_id: str,
        hand_start_stacks: Optional[Dict[str, int]] = None,
        was_short_stack: Optional[set] = None,
        memory_manager: Optional['AIMemoryManager'] = None,
        equity_history=None,
        enable_commentary: bool = False,
    ) -> set:
        """Process psychology updates via unified PsychologyPipeline.

        Returns:
            Updated set of currently short-stacked players
        """
        # Calculate pot size from winner_info
        pot_size = 0
        for pot in winner_info.get('pot_breakdown', []):
            for winner in pot.get('winners', []):
                pot_size += winner.get('amount', 0)

        big_blind = game_state.current_ante or 100

        pipeline = PsychologyPipeline(
            pressure_detector=self.pressure_detector,
            pressure_event_repo=self.pressure_event_repo,
            game_repo=self.game_repo,
            hand_history_repo=self.hand_history_repo,
            enable_emotional_narration=enable_commentary,
            persist_controller_state=True,
        )

        ctx = PsychologyContext(
            game_id=game_id,
            hand_number=hand_number,
            game_state=game_state,
            winner_info=winner_info,
            winner_names=winner_names,
            pot_size=pot_size,
            controllers=controllers,
            hand_start_stacks=hand_start_stacks,
            was_short_stack=was_short_stack,
            equity_history=equity_history,
            memory_manager=memory_manager,
            big_blind=big_blind,
        )

        result = pipeline.process_hand(ctx)
        return result.current_short_stack

    def run_hand(self, state_machine: PokerStateMachine,
                 controllers: Dict[str, AIPlayerController],
                 memory_manager: AIMemoryManager,
                 hand_number: int,
                 tournament_id: Optional[str] = None,
                 variant_config: Optional[Dict] = None,
                 tournament_number: int = 1) -> HandResult:
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
            HandResult with status ('continue', 'paused', 'end', 'reset_needed')
            and list of all-in winners from the hand.
        """
        # Let the state machine handle setup_hand via its INITIALIZING_HAND transition
        # Do NOT call setup_hand() directly - that would deal cards twice!
        game_state = state_machine.game_state

        # Check if tournament should end BEFORE setting up the hand
        active_players = [p for p in game_state.players if p.stack > 0]
        if len(active_players) <= 1:
            logger.info(f"Tournament ending: {len(active_players)} player(s) with chips remaining")
            return HandResult(status="end", all_in_winners=[])

        # Set deterministic deck seed for this hand (for A/B experiments)
        # Formula: base_seed + (tournament_number * 1000) + hand_number
        # This ensures the same deck order for the same hand_number across variants
        if self.config.random_seed is not None:
            state_machine.current_hand_seed = self.config.random_seed + (tournament_number * 1000) + hand_number

        # Set hand number on all controllers for decision analysis
        for controller in controllers.values():
            controller.current_hand_number = hand_number

        logger.debug(f"Hand {hand_number}: Starting with {len(active_players)} players")

        # Run through betting rounds
        max_actions = 100  # Safety limit per hand
        action_count = 0
        hand_start_recorded = False
        hand_start_stacks = {}

        # Track for stuck loop detection
        last_player_name = None
        same_player_count = 0

        while action_count < max_actions:
            # Advance state machine
            state_machine.run_until([PokerPhase.EVALUATING_HAND])
            game_state = state_machine.game_state

            # Record hand start AFTER first advance deals cards (hole_cards now available)
            if not hand_start_recorded:
                memory_manager.on_hand_start(
                    game_state,
                    hand_number,
                    deck_seed=state_machine.current_hand_seed
                )
                hand_start_stacks = {p.name: p.stack for p in game_state.players}
                hand_start_recorded = True

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
                        # Update heartbeat before API call
                        if tournament_id and self.experiment_id:
                            self.experiment_repo.update_experiment_game_heartbeat(
                                tournament_id, 'calling_api', api_call_started=True,
                                process_id=os.getpid()
                            )

                        # Get AI decision
                        pre_decision_energy = controller.psychology.energy if hasattr(controller, 'psychology') and controller.psychology else None
                        start_time = time.time()
                        response = controller.decide_action([])
                        latency = (time.time() - start_time) * 1000

                        # Log energy events from on_action_taken (consecutive folds)
                        if (self.pressure_event_repo and tournament_id
                                and hasattr(controller, 'last_energy_events')
                                and controller.last_energy_events
                                and pre_decision_energy is not None):
                            energy_delta = controller.psychology.energy - pre_decision_energy
                            for evt_name in controller.last_energy_events:
                                self.pressure_event_repo.save_event(
                                    game_id=tournament_id,
                                    player_name=current_player.name,
                                    event_type=evt_name,
                                    hand_number=hand_number,
                                    details={
                                        'conf_delta': 0,
                                        'comp_delta': 0,
                                        'energy_delta': round(energy_delta, 6),
                                    },
                                )

                        # Update heartbeat after API call
                        if tournament_id and self.experiment_id:
                            self.experiment_repo.update_experiment_game_heartbeat(
                                tournament_id, 'processing', process_id=os.getpid()
                            )

                        self.api_calls += 1
                        self.total_latency += latency

                        action = response.get('action', 'fold')
                        amount = response.get('raise_to', 0)

                        logger.debug(f"  {current_player.name}: {action} {amount if amount else ''}")

                        # Apply action
                        game_state = play_turn(game_state, action, amount)

                        # Record action for opponent modeling
                        active_player_names = [
                            p.name for p in game_state.players
                            if not p.is_folded and p.stack > 0
                        ]
                        memory_manager.on_action(
                            player_name=current_player.name,
                            action=action,
                            amount=amount,
                            phase=state_machine.current_phase.name,
                            pot_total=game_state.pot['total'],
                            active_players=active_player_names
                        )

                        advanced_state = advance_to_next_active_player(game_state)
                        # If None, no active players remain - keep current state, state machine handles phase transition
                        if advanced_state is not None:
                            game_state = advanced_state
                        state_machine.game_state = game_state  # Use property setter

                        # Feed action to memory manager (opponent model tracking, c-bet detection)
                        if memory_manager:
                            current_phase = state_machine.current_phase
                            phase_name = (current_phase.name
                                          if hasattr(current_phase, 'name')
                                          else str(current_phase))
                            active_names = [
                                p.name for p in game_state.players
                                if not p.is_folded
                            ]
                            pot_total = (game_state.pot.get('total', 0)
                                         if isinstance(game_state.pot, dict) else 0)
                            memory_manager.on_action(
                                player_name=current_player.name,
                                action=action,
                                amount=amount,
                                phase=phase_name,
                                pot_total=pot_total,
                                active_players=active_names,
                            )

                        # Detect action-based energy events (all_in_moment, heads_up)
                        enable_psychology = variant_config.get('enable_psychology', False) if variant_config else False
                        if enable_psychology:
                            action_events = self.pressure_detector.detect_action_events(
                                game_state, current_player.name, action, amount,
                                hand_number=getattr(memory_manager, 'hand_count', 0) if memory_manager else 0,
                            )
                            for event_name, affected_players in action_events:
                                for pname in affected_players:
                                    ctrl = controllers.get(pname)
                                    if ctrl and hasattr(ctrl, 'psychology'):
                                        e_before = ctrl.psychology.energy
                                        c_before = ctrl.psychology.confidence
                                        m_before = ctrl.psychology.composure
                                        ctrl.psychology.apply_pressure_event(event_name)
                                        if self.pressure_event_repo and tournament_id:
                                            self.pressure_event_repo.save_event(
                                                game_id=tournament_id,
                                                player_name=pname,
                                                event_type=event_name,
                                                hand_number=getattr(ctrl, 'current_hand_number', 0),
                                                details={
                                                    'conf_delta': round(ctrl.psychology.confidence - c_before, 6),
                                                    'comp_delta': round(ctrl.psychology.composure - m_before, 6),
                                                    'energy_delta': round(ctrl.psychology.energy - e_before, 6),
                                                },
                                            )

                        # Per-action save for resilience (enables pause/resume)
                        if tournament_id and self.experiment_id:
                            try:
                                self.game_repo.save_game(
                                    tournament_id, state_machine,
                                    self._owner_id
                                )
                                # Save AI conversation history for each controller
                                for player_name, ctrl in controllers.items():
                                    if hasattr(ctrl, 'assistant') and ctrl.assistant:
                                        messages = ctrl.assistant.memory.get_history()
                                        self.game_repo.save_ai_player_state(
                                            tournament_id, player_name, messages, {}
                                        )
                            except Exception as save_error:
                                logger.warning(f"Per-action save failed: {save_error}")

                        # Check for pause request
                        if self._check_pause_requested():
                            return HandResult(status="paused", all_in_winners=[])

                    except Exception as e:
                        logger.warning(f"AI error for {current_player.name}: {e}, defaulting to fold", exc_info=True)
                        game_state = play_turn(game_state, 'fold', 0)

                        # Record fallback fold action
                        active_player_names = [
                            p.name for p in game_state.players
                            if not p.is_folded and p.stack > 0
                        ]
                        memory_manager.on_action(
                            player_name=current_player.name,
                            action='fold',
                            amount=0,
                            phase=state_machine.current_phase.name,
                            pot_total=game_state.pot['total'],
                            active_players=active_player_names
                        )

                        advanced_state = advance_to_next_active_player(game_state)
                        # If None, no active players remain - keep current state, state machine handles phase transition
                        if advanced_state is not None:
                            game_state = advanced_state
                        state_machine.game_state = game_state  # Use property setter

                        # Save after fallback action too
                        self._save_checkpoint(tournament_id, state_machine)

                        # Check for pause request
                        if self._check_pause_requested():
                            return HandResult(status="paused", all_in_winners=[])

                action_count += 1

        # Evaluate hand
        if state_machine.current_phase == PokerPhase.EVALUATING_HAND:
            winner_info = determine_winner(game_state)
            game_state = award_pot_winnings(game_state, winner_info)

            winners = winner_info.get('pot_breakdown', [{}])[0].get('winners', [])
            winner_names = [w.get('name') for w in winners if w.get('name')]
            logger.debug(f"Hand {hand_number}: Winners = {winners}")

            # Calculate equity history BEFORE on_hand_complete clears current_hand
            equity_history = None
            enable_psychology = variant_config.get('enable_psychology', False) if variant_config else False
            enable_telemetry = variant_config.get('enable_telemetry', True) if variant_config else True
            if enable_psychology or enable_telemetry:
                hand_in_progress = memory_manager.hand_recorder.current_hand
                if hand_in_progress and hand_in_progress.hole_cards and hand_start_stacks:
                    # Backfill community cards from game state (not recorded per-street in experiments)
                    cc = [str(c) for c in game_state.community_cards]
                    if cc:
                        if len(cc) >= 3:
                            hand_in_progress.add_community_cards('FLOP', cc[:3])
                        if len(cc) >= 4:
                            hand_in_progress.add_community_cards('TURN', cc[3:4])
                        if len(cc) >= 5:
                            hand_in_progress.add_community_cards('RIVER', cc[4:5])
                    # Remove folded players to avoid false equity events
                    folded_names = {p.name for p in game_state.players if p.is_folded}
                    for name in folded_names:
                        hand_in_progress.hole_cards.pop(name, None)
                    try:
                        from poker.equity_tracker import EquityTracker
                        equity_tracker = EquityTracker()
                        equity_history = equity_tracker.calculate_hand_equity_history(hand_in_progress)
                    except Exception as e:
                        logger.warning(f"Equity calculation failed: {e}")

            # Record hand history to database (always, for outcome metrics)
            # This persists to hand_history table via memory_manager's persistence layer
            memory_manager.on_hand_complete(
                winner_info=winner_info,
                game_state=game_state,
                ai_players={},  # No AI player context needed for hand recording
                skip_commentary=True  # Commentary handled separately below if enabled
            )

            # Save equity history to database for telemetry/analytics
            if equity_history and equity_history.snapshots:
                try:
                    from poker.repositories.hand_equity_repository import HandEquityRepository
                    from poker.equity_snapshot import HandEquityHistory
                    equity_repo = HandEquityRepository(self.db_path)
                    hand_history_id = self.hand_history_repo.get_hand_history_id(
                        tournament_id, equity_history.hand_number
                    )
                    if hand_history_id:
                        equity_history_with_id = HandEquityHistory(
                            hand_history_id=hand_history_id,
                            game_id=equity_history.game_id,
                            hand_number=equity_history.hand_number,
                            snapshots=equity_history.snapshots,
                        )
                        equity_repo.save_equity_history(equity_history_with_id)
                        logger.debug(
                            f"[Tournament {tournament_id}] Saved {len(equity_history.snapshots)} "
                            f"equity snapshots for hand {hand_number}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to save equity history for hand {hand_number}: {e}")

            # Post-hand psychological processing (if enabled)
            enable_commentary = variant_config.get('enable_commentary', False) if variant_config else False

            if enable_psychology:
                was_short = getattr(self, '_was_short_stack', None)
                current_short = self._process_psychology(
                    game_state, controllers, winner_info, winner_names, hand_number,
                    game_id=tournament_id,
                    hand_start_stacks=hand_start_stacks,
                    was_short_stack=was_short,
                    memory_manager=memory_manager,
                    equity_history=equity_history,
                    enable_commentary=enable_commentary,
                )
                self._was_short_stack = current_short

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

        # Capture all-in outcomes BEFORE reset clears the flags
        all_in_winners = []
        for player in game_state.players:
            if player.is_all_in and player.stack > 0:
                all_in_winners.append(player.name)

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
            return HandResult(status="reset_needed", all_in_winners=all_in_winners)
        return HandResult(status="continue", all_in_winners=all_in_winners)

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

        # Reset per-tournament psychology tracking
        self._was_short_stack = None

        state_machine, controllers, memory_manager = self.create_game(tournament_id, variant_config, tournament_number)

        # Store original players for reset scenarios (before any eliminations)
        original_players = state_machine.game_state.players

        # Save initial game state for live monitoring
        self.game_repo.save_game(tournament_id, state_machine, self._owner_id)

        elimination_order = []  # Legacy: names only for backwards compatibility
        all_eliminations: List[Dict] = []  # New: detailed elimination tracking
        prev_active = set(p.name for p in state_machine.game_state.players)

        # Track all-in outcomes per player: {player_name: {'wins': N, 'losses': N}}
        all_in_outcomes: Dict[str, Dict[str, int]] = {
            p.name: {'wins': 0, 'losses': 0} for p in state_machine.game_state.players
        }

        # Determine hand limit and reset behavior
        # reset_on_elimination determines if hand count is maximum or exact:
        # - false: tournament ends when one player wins OR hits hand limit (variable hands)
        # - true: stacks reset on elimination, always plays exactly hands_per_tournament
        max_hands = self.config.hands_per_tournament
        should_reset = self.config.reset_on_elimination

        # Track round winners (for reset scenarios)
        round_winners: List[str] = []
        total_resets = 0
        current_round = 1  # Track which round we're in (for elimination data)

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
            self.game_repo.save_game(tournament_id, state_machine, self._owner_id)

            # Check if we've been superseded by a resume operation
            if self.experiment_id and self.experiment_repo.check_resume_lock_superseded(tournament_id):
                logger.info(f"Tournament {tournament_id} superseded by resume operation, exiting gracefully")
                raise TournamentSupersededException(tournament_id)

            # Periodic heartbeat every 5 hands
            if self.experiment_id and hand_number % 5 == 0:
                self.experiment_repo.update_experiment_game_heartbeat(
                    tournament_id, 'processing', process_id=os.getpid()
                )

            # Track eliminations and all-in outcomes
            current_active = set(p.name for p in state_machine.game_state.players if p.stack > 0)
            eliminated = prev_active - current_active

            # Track all-in outcomes: players who were eliminated lost their all-in
            for name in eliminated:
                elimination_order.append(name)  # Legacy tracking
                all_eliminations.append({
                    'player_name': name,
                    'hand_number': hand_number,
                    'round_number': current_round,
                })
                # Getting eliminated means losing an all-in (or final chips)
                all_in_outcomes[name]['losses'] += 1
                logger.info(f"  Eliminated: {name} (hand {hand_number}, round {current_round})")

            # Track all-in wins from hand result (captured before reset cleared flags)
            for name in hand_result.all_in_winners:
                all_in_outcomes[name]['wins'] += 1
                logger.debug(f"  All-in survived: {name} (hand {hand_number})")
            prev_active = current_active

            # Handle different return values from run_hand
            if hand_result.needs_reset:
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
                    elimination_order = []  # Clear legacy order for new round
                    current_round += 1  # Increment round counter (all_eliminations persists)
                    continue
                else:
                    # No reset - tournament ends when one player wins
                    break
            elif hand_result.is_paused or hand_result.is_end:
                # Paused or tournament ending
                if hand_result.is_paused:
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

        # Determine final standings with outcome metrics
        end_time = datetime.now()

        # Get per-player outcome data from hand_history
        player_outcomes = self._get_player_outcomes(tournament_id)

        # Count eliminations per player from all_eliminations
        elimination_counts = {}
        for elim in all_eliminations:
            name = elim['player_name']
            elimination_counts[name] = elimination_counts.get(name, 0) + 1

        final_standings = sorted(
            [
                {
                    "name": p.name,
                    "stack": p.stack,
                    "final_stack": p.stack,  # Alias for persistence compatibility
                    "hands_won": player_outcomes.get(p.name, {}).get('hands_won', 0),
                    "hands_played": player_outcomes.get(p.name, {}).get('hands_played', 0),
                    "times_eliminated": elimination_counts.get(p.name, 0),
                    "all_in_wins": all_in_outcomes.get(p.name, {}).get('wins', 0),
                    "all_in_losses": all_in_outcomes.get(p.name, {}).get('losses', 0),
                }
                for p in state_machine.game_state.players
            ],
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
            eliminations=all_eliminations,
        )

        # Save tournament result and standings with outcome metrics
        standings_data = [
            {
                'player_name': s['name'],
                'is_human': False,
                'finishing_position': i + 1,
                'final_stack': s.get('final_stack', s.get('stack', 0)),
                'hands_won': s.get('hands_won', 0),
                'hands_played': s.get('hands_played', 0),
                'times_eliminated': s.get('times_eliminated', 0),
                'all_in_wins': s.get('all_in_wins', 0),
                'all_in_losses': s.get('all_in_losses', 0),
            }
            for i, s in enumerate(final_standings)
        ]
        tournament_result_data = {
            'winner_name': winner,
            'total_hands': hand_number,
            'biggest_pot': 0,  # Could track this if needed
            'starting_player_count': len(final_standings),
            'human_player_name': None,
            'human_finishing_position': None,
            'started_at': start_time.isoformat(),
            'standings': standings_data,  # Include standings for persistence
        }
        self.tournament_repo.save_tournament_result(tournament_id, tournament_result_data)

        # Mark tournament as idle (completed) for heartbeat tracking
        if self.experiment_id:
            self.experiment_repo.update_experiment_game_heartbeat(tournament_id, 'idle')
            self.experiment_repo.release_resume_lock(tournament_id)

        variant_info = f" [{variant_label}]" if variant_label else ""
        resets_info = f", Resets = {total_resets}" if total_resets > 0 else ""
        logger.info(f"Tournament {tournament_id}{variant_info} complete: Winner = {winner}, Hands = {hand_number}{resets_info}")
        return result

    def _continue_tournament(
        self,
        tournament_id: str,
        state_machine: PokerStateMachine,
        controllers: Dict[str, AIPlayerController],
        memory_manager: AIMemoryManager,
        variant_label: Optional[str] = None,
        variant_config: Optional[Dict] = None,
        starting_hand: int = 1,
    ) -> Optional[TournamentResult]:
        """Continue a tournament from saved state (for resume after pause/crash).

        This is similar to run_tournament but uses existing state_machine, controllers,
        and memory_manager instead of creating new ones.

        Args:
            tournament_id: Unique identifier for this tournament
            state_machine: Existing PokerStateMachine with saved state
            controllers: Existing AI controllers (with restored conversation history)
            memory_manager: Existing memory manager
            variant_label: Optional variant label for A/B testing
            variant_config: Optional variant-specific config
            starting_hand: Hand number to resume from

        Returns:
            TournamentResult if tournament completes, None if it gets paused again
        """
        start_time = datetime.now()
        variant_info = f" [{variant_label}]" if variant_label else ""
        logger.info(f"Continuing tournament {tournament_id}{variant_info} from hand {starting_hand}")

        # Store original players for reset scenarios
        original_players = state_machine.game_state.players

        elimination_order = []
        all_eliminations: List[Dict] = []
        prev_active = set(p.name for p in state_machine.game_state.players if p.stack > 0)

        all_in_outcomes: Dict[str, Dict[str, int]] = {
            p.name: {'wins': 0, 'losses': 0} for p in state_machine.game_state.players
        }

        max_hands = self.config.hands_per_tournament
        should_reset = self.config.reset_on_elimination

        round_winners: List[str] = []
        total_resets = 0
        current_round = 1

        hand_number = starting_hand - 1  # Will be incremented at start of loop
        paused = False

        while hand_number < max_hands:
            hand_number += 1

            hand_result = self.run_hand(
                state_machine, controllers, memory_manager, hand_number,
                tournament_id=tournament_id,
                variant_config=variant_config,
                tournament_number=1  # Resume doesn't track tournament_number
            )

            # Save game state for live monitoring
            self.game_repo.save_game(tournament_id, state_machine, self._owner_id)

            # Check for superseded
            if self.experiment_id and self.experiment_repo.check_resume_lock_superseded(tournament_id):
                logger.info(f"Tournament {tournament_id} superseded by resume operation")
                raise TournamentSupersededException(tournament_id)

            # Periodic heartbeat
            if self.experiment_id and hand_number % 5 == 0:
                self.experiment_repo.update_experiment_game_heartbeat(
                    tournament_id, 'processing', process_id=os.getpid()
                )

            # Track eliminations and all-in outcomes
            current_active = set(p.name for p in state_machine.game_state.players if p.stack > 0)
            eliminated = prev_active - current_active

            for name in eliminated:
                elimination_order.append(name)
                all_eliminations.append({
                    'player_name': name,
                    'hand_number': hand_number,
                    'round_number': current_round,
                })
                all_in_outcomes[name]['losses'] += 1
                logger.info(f"  Eliminated: {name} (hand {hand_number})")

            for name in hand_result.all_in_winners:
                all_in_outcomes[name]['wins'] += 1
            prev_active = current_active

            # Handle different hand results
            if hand_result.needs_reset:
                if should_reset:
                    game_state = state_machine.game_state
                    winner = max(game_state.players, key=lambda p: p.stack)
                    round_winners.append(winner.name)
                    total_resets += 1
                    logger.info(f"Round {total_resets}: {winner.name} wins. Resetting stacks.")

                    reset_players = tuple(
                        player.update(stack=self.config.starting_stack, is_folded=False, is_all_in=False)
                        for player in original_players
                    )
                    game_state = game_state.update(players=reset_players)
                    state_machine.game_state = game_state

                    prev_active = set(p.name for p in original_players)
                    elimination_order = []
                    current_round += 1
                    continue
                else:
                    break
            elif hand_result.is_paused or hand_result.is_end:
                if hand_result.is_paused:
                    paused = True
                break

        if paused:
            logger.info(f"Tournament {tournament_id} paused at hand {hand_number}")
            raise TournamentPausedException(tournament_id, hand_number)

        # Build final result (same as run_tournament)
        end_time = datetime.now()
        game_state = state_machine.game_state

        # Count eliminations per player
        elimination_counts = {}
        for elim in all_eliminations:
            name = elim['player_name']
            elimination_counts[name] = elimination_counts.get(name, 0) + 1

        final_standings = sorted(
            [
                {
                    "name": p.name,
                    "stack": p.stack,
                    "final_stack": p.stack,
                    "times_eliminated": elimination_counts.get(p.name, 0),
                    "all_in_wins": all_in_outcomes.get(p.name, {}).get('wins', 0),
                    "all_in_losses": all_in_outcomes.get(p.name, {}).get('losses', 0),
                }
                for p in game_state.players
            ],
            key=lambda x: x["stack"],
            reverse=True
        )
        winner = final_standings[0]["name"] if final_standings else "Unknown"

        avg_latency = self.total_latency / max(self.api_calls, 1)

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
            eliminations=all_eliminations,
        )

        # Mark tournament as idle
        if self.experiment_id:
            self.experiment_repo.update_experiment_game_heartbeat(tournament_id, 'idle')
            self.experiment_repo.release_resume_lock(tournament_id)

        logger.info(f"Tournament {tournament_id} complete: Winner = {winner}, Hands = {hand_number}")
        return result

    def _get_decision_stats(self, game_id: str) -> Dict:
        """Get decision quality stats from the database."""
        try:
            return self.experiment_repo.get_decision_stats(game_id)
        except Exception as e:
            logger.warning(f"Could not get decision stats: {e}")
            return {}

    def _get_player_outcomes(self, game_id: str) -> Dict[str, Dict[str, int]]:
        """Get per-player outcome metrics from hand_history table.

        Args:
            game_id: The tournament/game ID to query

        Returns:
            Dict mapping player name to {hands_played, hands_won}
        """
        try:
            return self.experiment_repo.get_player_outcomes(game_id)
        except Exception as e:
            logger.warning(f"Could not get player outcomes: {e}")
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
                self.experiment_id = self.experiment_repo.create_experiment(experiment_config)
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
                # If ALL tournaments failed, mark experiment as failed
                if not results and failed:
                    error_msgs = [f.error for f in failed if f.error]
                    error_summary = "; ".join(error_msgs[:3])  # First 3 errors
                    if len(error_msgs) > 3:
                        error_summary += f" (and {len(error_msgs) - 3} more)"
                    logger.error(f"All {len(failed)} tournaments failed, marking experiment as failed")
                    self.experiment_repo.update_experiment_status(
                        self.experiment_id, 'failed',
                        f"All {len(failed)} tournaments failed: {error_summary}"
                    )
                else:
                    summary = self._compute_experiment_summary(results, failed)
                    # Generate AI interpretation of results (best-effort, won't block completion)
                    summary = self._generate_ai_interpretation(summary, failed)
                    self.experiment_repo.complete_experiment(self.experiment_id, summary)
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
                        self.experiment_repo.link_game_to_experiment(
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

                        # If this was a pause, stop waiting for other workers
                        # They'll detect the pause flag at their next checkpoint
                        if outcome.error_type == 'TournamentPausedException':
                            logger.info(
                                "Pause detected, not waiting for remaining workers"
                            )
                            break
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

        # Compute quality indicators (degenerate play detection)
        if self.experiment_id:
            quality_indicators = self._compute_quality_indicators(self.experiment_id)
            if quality_indicators:
                summary['quality_indicators'] = quality_indicators

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
            return self.experiment_repo.get_latency_metrics(game_ids)
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
            return self.experiment_repo.get_error_stats(game_ids)
        except Exception as e:
            logger.warning(f"Could not get error stats: {e}")
            return None

    def _compute_quality_indicators(self, experiment_id: str) -> Optional[Dict]:
        """Compute quality indicators from player_decision_analysis + prompt_captures.

        Uses improved 3-tier stack depth detection for suspicious all-ins:
        - Short (<=10BB): Filtered out as defensible
        - Marginal (11-15BB): Tracked as marginal_allins
        - Deep (>15BB): Tracked as suspicious_allins

        A "suspicious all-in" requires:
        - bluff_likelihood < 50 (AI thinks it has a real hand)
        - Trash hand: hand_strength contains "high card" OR equity < 0.25

        Args:
            experiment_id: The experiment ID to compute metrics for

        Returns:
            Dictionary with quality indicators, or None if no data
        """
        try:
            return self.experiment_repo.get_quality_metrics(experiment_id)
        except Exception as e:
            logger.warning(f"Could not compute quality indicators: {e}")
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
                experiment_data = self.experiment_repo.get_experiment(self.experiment_id)

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
Respond in JSON format with keys: summary, verdict, surprises (array, can be empty), next_steps (array of {{hypothesis, description}})"""

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
            live_stats = self.experiment_repo.get_experiment_live_stats(self.experiment_id)
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
            client = LLMClient(model=get_assistant_model(), provider=get_assistant_provider())
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
