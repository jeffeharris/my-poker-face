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
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

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
)
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.controllers import AIPlayerController
from poker.persistence import GamePersistence as Persistence
from poker.memory.memory_manager import AIMemoryManager
from poker.utils import get_celebrities

# Configure logging
logging.basicConfig(
    level=logging.INFO,
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


@dataclass
class ExperimentConfig:
    """Configuration for an experiment run."""
    name: str
    description: str = ""
    hypothesis: str = ""
    tags: Optional[List[str]] = None
    capture_prompts: bool = True
    num_tournaments: int = 1
    max_hands_per_tournament: int = 100
    num_players: int = 4
    starting_stack: int = 10000
    big_blind: int = 100
    model: str = "gpt-5-nano"
    provider: str = "openai"
    personalities: Optional[List[str]] = None
    random_seed: Optional[int] = None


class AITournamentRunner:
    """Runs AI-only poker tournaments for experimentation."""

    def __init__(self, config: ExperimentConfig, db_path: Optional[str] = None):
        self.config = config
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

    def create_game(self, tournament_id: str) -> Tuple[PokerStateMachine, Dict[str, AIPlayerController], AIMemoryManager]:
        """Create a new game with AI players only.

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
        game_state = PokerGameState(
            players=ai_players,
            current_ante=self.config.big_blind,
            last_raise_amount=self.config.big_blind
        )

        # Create state machine
        state_machine = PokerStateMachine(game_state)

        # Create memory manager for hand tracking
        memory_manager = AIMemoryManager(
            game_id=tournament_id,
            db_path=self.db_path,
            owner_id=f"experiment_{self.config.name}"
        )
        memory_manager.set_persistence(self.persistence)

        # Create AI controllers for each player
        llm_config = {
            'provider': self.config.provider,
            'model': self.config.model,
        }

        controllers = {}
        for player in game_state.players:
            controller = AIPlayerController(
                player_name=player.name,
                state_machine=state_machine,
                llm_config=llm_config,
                game_id=tournament_id,
                owner_id=f"experiment_{self.config.name}",
                persistence=self.persistence,
                debug_capture=self.config.capture_prompts,
            )
            controllers[player.name] = controller
            # Initialize memory manager for this player
            memory_manager.initialize_for_player(player.name)

        return state_machine, controllers, memory_manager

    def run_hand(self, state_machine: PokerStateMachine,
                 controllers: Dict[str, AIPlayerController],
                 memory_manager: AIMemoryManager,
                 hand_number: int) -> bool:
        """
        Run a single hand to completion.

        Returns True if game should continue, False if tournament is over.
        """
        # Let the state machine handle setup_hand via its INITIALIZING_HAND transition
        # Do NOT call setup_hand() directly - that would deal cards twice!
        game_state = state_machine.game_state

        # Check if tournament should end BEFORE setting up the hand
        active_players = [p for p in game_state.players if p.stack > 0]
        if len(active_players) <= 1:
            logger.info(f"Tournament ending: {len(active_players)} player(s) with chips remaining")
            return False

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

                    except Exception as e:
                        logger.warning(f"AI error for {current_player.name}: {e}, defaulting to fold")
                        game_state = play_turn(game_state, 'fold', 0)
                        game_state = advance_to_next_active_player(game_state)
                        state_machine.game_state = game_state  # Use property setter

                action_count += 1

        # Evaluate hand
        if state_machine.current_phase == PokerPhase.EVALUATING_HAND:
            winner_info = determine_winner(game_state)
            game_state = award_pot_winnings(game_state, winner_info)

            winners = winner_info.get('pot_breakdown', [{}])[0].get('winners', [])
            logger.debug(f"Hand {hand_number}: Winners = {winners}")

        # Reset for next hand
        game_state = reset_game_state_for_new_hand(game_state)
        state_machine.game_state = game_state  # Use property setter
        state_machine.update_phase(PokerPhase.INITIALIZING_HAND)

        # Check if tournament should continue
        active_players = [p for p in game_state.players if p.stack > 0]
        return len(active_players) > 1

    def run_tournament(self, tournament_id: str) -> TournamentResult:
        """Run a complete tournament to conclusion."""
        start_time = datetime.now()
        logger.info(f"Starting tournament {tournament_id}")

        state_machine, controllers, memory_manager = self.create_game(tournament_id)

        elimination_order = []
        prev_active = set(p.name for p in state_machine.game_state.players)

        hand_number = 0
        while hand_number < self.config.max_hands_per_tournament:
            hand_number += 1

            should_continue = self.run_hand(state_machine, controllers, memory_manager, hand_number)

            # Track eliminations
            current_active = set(p.name for p in state_machine.game_state.players if p.stack > 0)
            eliminated = prev_active - current_active
            for name in eliminated:
                elimination_order.append(name)
                logger.info(f"  Eliminated: {name}")
            prev_active = current_active

            if not should_continue:
                break

            # Log progress every 10 hands
            if hand_number % 10 == 0:
                stacks = {p.name: p.stack for p in state_machine.game_state.players if p.stack > 0}
                logger.info(f"Hand {hand_number}: Stacks = {stacks}")

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
            model_config={
                "provider": self.config.provider,
                "model": self.config.model,
            },
            total_api_calls=self.api_calls,
            total_cost=self.total_cost,
            avg_latency_ms=avg_latency,
            decision_stats=self._get_decision_stats(tournament_id),
        )

        logger.info(f"Tournament {tournament_id} complete: Winner = {winner}, Hands = {hand_number}")
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
        """Run the full experiment (multiple tournaments)."""
        results = []

        # Create experiment record at start
        experiment_config = {
            'name': self.config.name,
            'description': self.config.description,
            'hypothesis': self.config.hypothesis,
            'tags': self.config.tags or [],
            'num_tournaments': self.config.num_tournaments,
            'max_hands_per_tournament': self.config.max_hands_per_tournament,
            'num_players': self.config.num_players,
            'starting_stack': self.config.starting_stack,
            'big_blind': self.config.big_blind,
            'model': self.config.model,
            'provider': self.config.provider,
            'personalities': self.config.personalities,
            'capture_prompts': self.config.capture_prompts,
        }

        try:
            self.experiment_id = self.persistence.create_experiment(experiment_config)
            logger.info(f"Created experiment record with id {self.experiment_id}")
        except Exception as e:
            logger.warning(f"Could not create experiment record: {e}")
            self.experiment_id = None

        for i in range(self.config.num_tournaments):
            tournament_id = f"exp_{self.config.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{i+1}"

            # Reset per-tournament metrics
            self.api_calls = 0
            self.total_latency = 0
            self.total_cost = 0.0

            result = self.run_tournament(tournament_id)
            results.append(result)

            # Link game to experiment
            if self.experiment_id:
                try:
                    self.persistence.link_game_to_experiment(
                        experiment_id=self.experiment_id,
                        game_id=tournament_id,
                        variant=None,  # Can be extended for A/B testing
                        variant_config=None,
                        tournament_number=i + 1,
                    )
                except Exception as e:
                    logger.warning(f"Could not link game to experiment: {e}")

            # Save result to file
            self._save_result(result)

        # Complete experiment with summary
        if self.experiment_id:
            try:
                summary = self._compute_experiment_summary(results)
                self.persistence.complete_experiment(self.experiment_id, summary)
            except Exception as e:
                logger.warning(f"Could not complete experiment: {e}")

        return results

    def _compute_experiment_summary(self, results: List[TournamentResult]) -> Dict:
        """Compute aggregated summary for the experiment.

        Args:
            results: List of tournament results

        Returns:
            Summary dictionary with aggregated statistics
        """
        if not results:
            return {}

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
        max_hands_per_tournament=args.hands,
        num_players=args.players,
        starting_stack=args.stack,
        big_blind=args.blind,
        model=args.model,
        provider=args.provider,
        personalities=personalities,
        random_seed=args.seed,
    )

    print(f"Running experiment: {config.name}")
    print(f"  Model: {config.provider}/{config.model}")
    print(f"  Tournaments: {config.num_tournaments}")
    print(f"  Max hands: {config.max_hands_per_tournament}")
    print(f"  Players: {config.num_players}")

    runner = AITournamentRunner(config)
    results = runner.run_experiment()

    print_summary(results)

    return 0


if __name__ == "__main__":
    sys.exit(main())
