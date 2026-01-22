"""
Replay Experiment Runner

Executes replay experiments by re-running captured AI decisions with different
variants (models, prompts, guidance, etc.) and analyzing the results.

Usage:
    # Run from API (typical)
    from experiments.run_replay_experiment import ReplayExperimentRunner
    runner = ReplayExperimentRunner(persistence)
    runner.run_experiment(experiment_id)

    # Run from command line
    python -m experiments.run_replay_experiment --experiment-id 123
"""

import argparse
import json
import logging
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Any, Callable

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.llm import LLMClient, CallType
from poker.persistence import GamePersistence
from experiments.variant_config import build_effective_variant_config
from experiments.pause_coordinator import pause_coordinator

logger = logging.getLogger(__name__)


@dataclass
class ReplayResult:
    """Result from replaying a single capture with a variant."""
    capture_id: int
    variant: str
    success: bool
    new_response: str = ""
    new_action: str = ""
    new_raise_amount: Optional[int] = None
    new_quality: Optional[str] = None
    new_ev_lost: Optional[float] = None
    provider: str = ""
    model: str = ""
    reasoning_effort: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error_message: Optional[str] = None


class ReplayExperimentRunner:
    """Orchestrates replay experiments.

    Loads captured prompts, replays them with different variants,
    analyzes results, and stores them in the database.
    """

    def __init__(
        self,
        persistence: GamePersistence,
        max_workers: int = 3,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ):
        """Initialize the runner.

        Args:
            persistence: GamePersistence instance for database access
            max_workers: Maximum concurrent workers for parallel execution
            progress_callback: Optional callback(completed, total, message) for progress updates
        """
        self.persistence = persistence
        self.max_workers = max_workers
        self.progress_callback = progress_callback
        self._stop_requested = False
        self._current_experiment_id: Optional[int] = None

    def run_experiment(
        self,
        experiment_id: int,
        parallel: bool = True
    ) -> Dict[str, Any]:
        """Run a replay experiment.

        Args:
            experiment_id: The replay experiment ID to run
            parallel: If True, run replays in parallel

        Returns:
            Summary dict with results statistics
        """
        self._current_experiment_id = experiment_id
        self._stop_requested = False

        # Load experiment
        experiment = self.persistence.get_replay_experiment(experiment_id)
        if not experiment:
            raise ValueError(f"Replay experiment {experiment_id} not found")

        # Update status to running
        self.persistence.update_experiment_status(experiment_id, 'running')

        try:
            # Get captures and variants
            captures = self.persistence.get_replay_experiment_captures(experiment_id)
            config = experiment.get('config_json', {})
            variants = config.get('variants', []) if isinstance(config, dict) else []

            if not captures:
                raise ValueError("No captures linked to this experiment")
            if not variants:
                raise ValueError("No variants defined for this experiment")

            logger.info(f"Running replay experiment {experiment_id}: "
                       f"{len(captures)} captures x {len(variants)} variants")

            # Build work items
            work_items = []
            for capture in captures:
                # Load full capture data
                full_capture = self._load_capture(capture['capture_id'])
                if not full_capture:
                    logger.warning(f"Capture {capture['capture_id']} not found, skipping")
                    continue

                for variant_dict in variants:
                    work_items.append((full_capture, variant_dict))

            total_work = len(work_items)
            completed = 0
            results_by_variant = {}

            self._report_progress(completed, total_work, "Starting replay experiment...")

            if parallel and self.max_workers > 1:
                # Parallel execution
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = {
                        executor.submit(self._replay_capture, capture, variant_dict): (capture, variant_dict)
                        for capture, variant_dict in work_items
                    }

                    for future in as_completed(futures):
                        if self._stop_requested:
                            logger.info("Stop requested, cancelling remaining work")
                            executor.shutdown(wait=False, cancel_futures=True)
                            break

                        # Check pause
                        while pause_coordinator.should_pause(experiment_id):
                            time.sleep(1)
                            if self._stop_requested:
                                break

                        capture, variant_dict = futures[future]
                        try:
                            result = future.result()
                            self._store_result(experiment_id, result)

                            variant_label = variant_dict.get('label', 'Unknown')
                            if variant_label not in results_by_variant:
                                results_by_variant[variant_label] = []
                            results_by_variant[variant_label].append(result)

                        except Exception as e:
                            logger.error(f"Error in replay: {e}")
                            # Store error result
                            error_result = ReplayResult(
                                capture_id=capture['id'],
                                variant=variant_dict.get('label', 'Unknown'),
                                success=False,
                                error_message=str(e)
                            )
                            self._store_result(experiment_id, error_result)

                        completed += 1
                        self._report_progress(completed, total_work,
                                            f"Completed {completed}/{total_work}")
            else:
                # Sequential execution
                for capture, variant_dict in work_items:
                    if self._stop_requested:
                        logger.info("Stop requested, stopping")
                        break

                    # Check pause
                    while pause_coordinator.should_pause(experiment_id):
                        time.sleep(1)
                        if self._stop_requested:
                            break

                    try:
                        result = self._replay_capture(capture, variant_dict)
                        self._store_result(experiment_id, result)

                        variant_label = variant_dict.get('label', 'Unknown')
                        if variant_label not in results_by_variant:
                            results_by_variant[variant_label] = []
                        results_by_variant[variant_label].append(result)

                    except Exception as e:
                        logger.error(f"Error in replay: {e}")
                        error_result = ReplayResult(
                            capture_id=capture['id'],
                            variant=variant_dict.get('label', 'Unknown'),
                            success=False,
                            error_message=str(e)
                        )
                        self._store_result(experiment_id, error_result)

                    completed += 1
                    self._report_progress(completed, total_work,
                                        f"Completed {completed}/{total_work}")

            # Generate summary
            summary = self._generate_summary(experiment_id)

            # Update status with summary
            if self._stop_requested:
                self.persistence.update_experiment_status(experiment_id, 'interrupted')
            else:
                # Use complete_experiment which sets status and stores summary
                self.persistence.complete_experiment(experiment_id, summary)

            logger.info(f"Replay experiment {experiment_id} completed")
            return summary

        except Exception as e:
            logger.error(f"Replay experiment {experiment_id} failed: {e}")
            self.persistence.update_experiment_status(experiment_id, 'failed', str(e))
            raise

    def stop(self):
        """Request stop of current experiment."""
        self._stop_requested = True

    def _load_capture(self, capture_id: int) -> Optional[Dict[str, Any]]:
        """Load full capture data including prompts."""
        import sqlite3
        with sqlite3.connect(self.persistence.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM prompt_captures WHERE id = ?
            """, (capture_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def _replay_capture(
        self,
        capture: Dict[str, Any],
        variant_dict: Dict[str, Any]
    ) -> ReplayResult:
        """Replay a single capture with a variant.

        Args:
            capture: The captured prompt data
            variant_dict: The variant configuration

        Returns:
            ReplayResult with the outcome
        """
        start_time = time.time()
        variant_label = variant_dict.get('label', 'Unknown')

        try:
            # Build effective config
            effective_config = build_effective_variant_config(
                variant_dict,
                control_dict=None,
                experiment_model=capture.get('model', 'gpt-4o-mini'),
                experiment_provider=capture.get('provider', 'openai')
            )

            # Get model/provider for this variant
            model = effective_config['model']
            provider = effective_config['provider']
            reasoning_effort = effective_config.get('reasoning_effort')

            # Build the prompt
            system_prompt = capture.get('system_prompt', '')
            user_message = capture.get('user_message', '')

            # Apply personality override if specified
            if effective_config.get('personality'):
                # For replay, we typically keep the original system prompt
                # but could swap personality if the variant specifies it
                # This would require access to personality loader
                pass

            # Apply guidance injection
            guidance = effective_config.get('guidance_injection')
            if guidance:
                user_message = self._inject_guidance(user_message, guidance)

            # Create LLM client
            client = LLMClient(provider=provider, model=model)

            # Build messages
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]

            # Include conversation history if present
            conversation_history = capture.get('conversation_history')
            if conversation_history:
                try:
                    history = json.loads(conversation_history) if isinstance(conversation_history, str) else conversation_history
                    if isinstance(history, list):
                        # Insert history before the current user message
                        messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
                except (json.JSONDecodeError, TypeError):
                    logger.debug("Failed to parse conversation_history for capture id=%s, proceeding without history", capture.get('id'))

            # Make the LLM call
            # Note: reasoning_effort not currently supported by LLMClient
            response = client.complete(
                messages=messages,
                json_format=True,
                call_type=CallType.PLAYER_DECISION
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # Parse response
            try:
                result_data = json.loads(response.content)
            except json.JSONDecodeError:
                result_data = {"raw": response.content, "parse_error": True}

            new_action = result_data.get('action', 'unknown')
            new_raise_amount = result_data.get('adding_to_pot')

            # Assess quality (simplified - could be enhanced)
            new_quality = self._assess_quality(capture, new_action, new_raise_amount)

            return ReplayResult(
                capture_id=capture['id'],
                variant=variant_label,
                success=True,
                new_response=response.content,
                new_action=new_action,
                new_raise_amount=new_raise_amount,
                new_quality=new_quality,
                provider=provider,
                model=model,
                reasoning_effort=reasoning_effort,
                input_tokens=getattr(response, 'input_tokens', 0),
                output_tokens=getattr(response, 'output_tokens', 0),
                latency_ms=latency_ms
            )

        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Replay failed for capture {capture['id']} with variant {variant_label}: {e}")
            return ReplayResult(
                capture_id=capture['id'],
                variant=variant_label,
                success=False,
                error_message=str(e),
                latency_ms=latency_ms
            )

    def _inject_guidance(self, user_message: str, guidance: str) -> str:
        """Inject guidance text into the user message."""
        # Find a good injection point - before "What is your move"
        injection_point = user_message.find("What is your move")
        if injection_point == -1:
            # Try alternative injection points
            injection_point = user_message.find("Your options")
            if injection_point == -1:
                # Fallback: append to the message
                return user_message + "\n\n" + guidance

        # Insert guidance before the action prompt
        return (
            user_message[:injection_point] +
            "\n" + guidance + "\n\n" +
            user_message[injection_point:]
        )

    def _assess_quality(
        self,
        capture: Dict[str, Any],
        new_action: str,
        new_raise_amount: Optional[int]
    ) -> Optional[str]:
        """Assess the quality of the new action.

        This is a simplified assessment. In a full implementation,
        we'd want to run proper equity analysis.

        Returns:
            'optimal', 'acceptable', or 'mistake'
        """
        # Try to get decision analysis for this capture
        import sqlite3
        with sqlite3.connect(self.persistence.db_path) as conn:
            cursor = conn.execute("""
                SELECT optimal_action, decision_quality
                FROM player_decision_analysis
                WHERE capture_id = ?
            """, (capture['id'],))
            row = cursor.fetchone()
            if row:
                optimal_action = row[0]
                # Simple comparison
                if new_action == optimal_action:
                    return 'optimal'
                elif new_action in ('fold', 'check') and optimal_action in ('fold', 'check'):
                    return 'acceptable'
                else:
                    return 'mistake'

        return None

    def _store_result(self, experiment_id: int, result: ReplayResult) -> None:
        """Store a replay result in the database."""
        try:
            self.persistence.add_replay_result(
                experiment_id=experiment_id,
                capture_id=result.capture_id,
                variant=result.variant,
                new_response=result.new_response,
                new_action=result.new_action,
                new_raise_amount=result.new_raise_amount,
                new_quality=result.new_quality,
                new_ev_lost=result.new_ev_lost,
                provider=result.provider,
                model=result.model,
                reasoning_effort=result.reasoning_effort,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
                error_message=result.error_message
            )
        except Exception as e:
            logger.error(f"Failed to store replay result: {e}")

    def _generate_summary(self, experiment_id: int) -> Dict[str, Any]:
        """Generate summary statistics for the experiment."""
        return self.persistence.get_replay_results_summary(experiment_id)

    def _report_progress(self, completed: int, total: int, message: str) -> None:
        """Report progress via callback."""
        if self.progress_callback:
            try:
                self.progress_callback(completed, total, message)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")


def run_replay_experiment_async(
    experiment_id: int,
    persistence: GamePersistence,
    parallel: bool = True,
    max_workers: int = 3
) -> threading.Thread:
    """Run a replay experiment in a background thread.

    Args:
        experiment_id: The experiment ID to run
        persistence: GamePersistence instance
        parallel: If True, run replays in parallel
        max_workers: Maximum concurrent workers

    Returns:
        The started thread
    """
    def run():
        try:
            runner = ReplayExperimentRunner(persistence, max_workers=max_workers)
            runner.run_experiment(experiment_id, parallel=parallel)
        except Exception as e:
            logger.error(f"Async replay experiment {experiment_id} failed: {e}")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def main():
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Run a replay experiment")
    parser.add_argument("--experiment-id", type=int, required=True,
                       help="ID of the replay experiment to run")
    parser.add_argument("--db", default="data/poker_games.db",
                       help="Database path")
    parser.add_argument("--sequential", action="store_true",
                       help="Run sequentially instead of parallel")
    parser.add_argument("--max-workers", type=int, default=3,
                       help="Maximum parallel workers")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Verbose output")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    persistence = GamePersistence(args.db)

    def progress_callback(completed, total, message):
        print(f"[{completed}/{total}] {message}")

    runner = ReplayExperimentRunner(
        persistence,
        max_workers=args.max_workers,
        progress_callback=progress_callback
    )

    try:
        summary = runner.run_experiment(
            args.experiment_id,
            parallel=not args.sequential
        )
        print("\n" + "=" * 60)
        print("EXPERIMENT COMPLETE")
        print("=" * 60)
        print(json.dumps(summary, indent=2))

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
