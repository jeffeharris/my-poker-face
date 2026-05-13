"""
Bounded Replay Experiment Runner

Replays captured AI preflop decisions through different option-framing variants
(raw-ev, nudges, rangegate) with multiple LLM samples per decision point.
This enables controlled A/B comparison of option-generation configs on identical
game states, isolating the option-framing effect from game-state noise.

Usage:
    # From command line
    python -m experiments.run_bounded_replay experiments/configs/bounded_replay_template.json
    python -m experiments.run_bounded_replay experiments/configs/bounded_replay_template.json --dry-run

    # From Python
    from experiments.run_bounded_replay import BoundedReplayRunner
    runner = BoundedReplayRunner(db_path='/app/data/poker_games.db')
    experiment_id = runner.run(config)
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.llm import LLMClient, CallType
from poker.bounded_options import (
    generate_bounded_options,
    BoundedOption,
    STYLE_PROFILES,
)
from poker.nudge_phrases import apply_composed_nudges
from poker.range_guidance import looseness_to_range_pct
from poker.hand_tiers import is_hand_in_range
from poker.controllers import _get_canonical_hand
from poker.hybrid_ai_controller import HybridAIController

logger = logging.getLogger(__name__)

# System prompt used for all bounded replay calls (matches hybrid controller)
LEAN_SYSTEM_PROMPT = HybridAIController.LEAN_SYSTEM_PROMPT

# Default looseness values per style profile for captures missing effective_looseness
PROFILE_LOOSENESS = {
    'tight_passive': 0.30,
    'tight_aggressive': 0.35,
    'loose_passive': 0.75,
    'loose_aggressive': 0.80,
    'default': 0.50,
}


@dataclass
class ReplayResult:
    """Result from replaying a single capture/variant/sample."""
    capture_id: int
    variant: str
    sample_number: int
    success: bool
    option_config_json: str = ""
    generated_options_json: str = ""
    new_response: str = ""
    choice_number: Optional[int] = None
    new_action: str = ""
    new_raise_amount: Optional[int] = None
    reasoning: str = ""
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error_message: Optional[str] = None


def reconstruct_rule_context(capture: Dict, metadata: Dict) -> Dict:
    """Build rule_context from prompt_captures columns + metadata_json.

    Derives missing fields from available data with sensible defaults.
    """
    player_stack = capture.get('player_stack') or 0
    stack_bb = metadata.get('stack_bb') or capture.get('stack_bb') or 0

    # Derive big_blind from stack/stack_bb ratio, fall back to metadata or 100
    if stack_bb and stack_bb > 0 and player_stack > 0:
        big_blind = player_stack / stack_bb
    else:
        big_blind = metadata.get('big_blind') or 100

    # Canonical hand from metadata or derive from player_hand
    canonical_hand = metadata.get('canonical_hand') or ''
    if not canonical_hand:
        player_hand = metadata.get('player_hand') or capture.get('player_hand')
        if player_hand:
            if isinstance(player_hand, str):
                try:
                    player_hand = json.loads(player_hand)
                except (json.JSONDecodeError, TypeError):
                    player_hand = []
            if isinstance(player_hand, list) and len(player_hand) == 2:
                canonical_hand = _get_canonical_hand(player_hand)

    # Position: metadata > default with warning
    position = metadata.get('position')
    if not position:
        position = 'button'
        logger.warning(
            f"Capture {capture.get('id')}: position not in metadata, defaulting to 'button'"
        )

    cost_to_call = capture.get('cost_to_call') or metadata.get('cost_to_call') or 0
    pot_total = capture.get('pot_total') or metadata.get('pot_total') or 0

    return {
        'player_name': capture.get('player_name', ''),
        'player_stack': player_stack,
        'stack_bb': stack_bb,
        'pot_total': pot_total,
        'pot_odds': pot_total / cost_to_call if cost_to_call > 0 else None,
        'cost_to_call': cost_to_call,
        'min_raise': metadata.get('min_raise') or int(big_blind * 2),
        'max_raise': metadata.get('max_raise') or player_stack,
        'big_blind': big_blind,
        'equity': metadata.get('equity') or capture.get('equity') or 0.5,
        'required_equity': metadata.get('required_equity') or (
            cost_to_call / (pot_total + cost_to_call) if (pot_total + cost_to_call) > 0 else 0
        ),
        'canonical_hand': canonical_hand,
        'hole_cards': _parse_json_field(metadata.get('player_hand') or capture.get('player_hand'), []),
        'community_cards': _parse_json_field(metadata.get('community_cards') or capture.get('community_cards'), []),
        'phase': metadata.get('phase') or capture.get('phase') or 'PRE_FLOP',
        'position': position,
        'num_opponents': metadata.get('num_opponents') or 3,
        'effective_stack': metadata.get('effective_stack') or player_stack,
        'spr': metadata.get('spr') or float('inf'),
        'valid_actions': _parse_json_field(metadata.get('valid_actions') or capture.get('valid_actions'), ['fold', 'call', 'raise']),
    }


def extract_prompt_header(user_message: str) -> str:
    """Extract everything above the numbered options from a captured user_message.

    The constant part (cards, hand classification, street/stack/pot, action summary,
    style hint) is preserved; the numbered options are stripped for regeneration.
    """
    if not user_message:
        return ''

    lines = user_message.split('\n')
    header_lines = []
    for line in lines:
        # Stop at first numbered option line (e.g., "1. FOLD ...")
        if re.match(r'^\d+\.', line.strip()):
            break
        header_lines.append(line)

    # Strip trailing empty lines
    while header_lines and not header_lines[-1].strip():
        header_lines.pop()

    return '\n'.join(header_lines)


def build_options_section(
    options: List[BoundedOption],
    big_blind: float,
    use_nudges: bool,
) -> str:
    """Format options into the lean prompt lines matching hybrid controller format.

    Args:
        options: BoundedOption list to format
        big_blind: Big blind value for BB conversion
        use_nudges: If True, use nudge format (action - phrase); else raw EV format
    """
    parts = []
    for i, opt in enumerate(options, 1):
        action_str = opt.action.upper()
        if opt.action == 'raise' and opt.raise_to > 0:
            raise_bb = opt.raise_to / big_blind if big_blind > 0 else opt.raise_to
            action_str += f" {raise_bb:.0f}BB"
        if use_nudges:
            parts.append(f"{i}. {action_str} \u2014 {opt.rationale}")
        else:
            parts.append(f"{i}. {action_str}  [{opt.ev_estimate}]  {opt.rationale}")

    parts.append("")
    parts.append(f'Respond with JSON: {{"reasoning": "...", "choice": N}} (1-{len(options)})')
    return '\n'.join(parts)


def regenerate_options_for_variant(
    rule_context: Dict,
    variant_config: Dict,
    metadata: Dict,
) -> List[BoundedOption]:
    """Regenerate bounded options for a specific variant config.

    Args:
        rule_context: Reconstructed rule context
        variant_config: Variant configuration dict
        metadata: Capture metadata for style_profile, effective_looseness, etc.
    """
    # Resolve profile
    profile_key = 'default'
    if variant_config.get('style_aware_options'):
        profile_key = metadata.get('style_profile') or 'default'
    profile = STYLE_PROFILES.get(profile_key, STYLE_PROFILES['default'])

    phase = rule_context.get('phase', 'PRE_FLOP')

    # Range gate
    in_range = True
    range_pct = None
    position_display = None

    if variant_config.get('preflop_range_gate') and phase == 'PRE_FLOP':
        effective_looseness = metadata.get('effective_looseness')
        if effective_looseness is None:
            effective_looseness = PROFILE_LOOSENESS.get(profile_key, 0.5)
            logger.debug(
                f"No effective_looseness in metadata, using default {effective_looseness} "
                f"for profile {profile_key}"
            )

        position = rule_context.get('position', 'button')
        range_pct = looseness_to_range_pct(effective_looseness, position)
        canonical = rule_context.get('canonical_hand', '')
        in_range = is_hand_in_range(canonical, range_pct) if canonical else True
        position_display = position

    # Generate options — no emotional shift, no shuffle (isolate option-framing)
    options = generate_bounded_options(
        context=rule_context,
        profile=profile,
        phase=phase,
        in_range=in_range,
        range_pct=range_pct,
        position_display=position_display,
    )

    # Apply nudges if configured
    if variant_config.get('composed_nudges'):
        options = apply_composed_nudges(options, profile_key)

    return options


def prepare_replay_prompt(
    capture: Dict,
    metadata: Dict,
    variant_config: Dict,
) -> Optional[Dict]:
    """Prepare the replay prompt for a capture × variant (shared across samples).

    Returns a dict with the rebuilt prompt and option data, or None on failure.
    """
    try:
        rule_context = reconstruct_rule_context(capture, metadata)
        options = regenerate_options_for_variant(rule_context, variant_config, metadata)
        if not options:
            return None

        prompt_header = extract_prompt_header(capture.get('user_message', ''))
        big_blind = rule_context.get('big_blind', 100)
        use_nudges = variant_config.get('composed_nudges', False)
        options_section = build_options_section(options, big_blind, use_nudges)
        user_message = prompt_header + '\n\n' + options_section

        return {
            'user_message': user_message,
            'options': options,
            'option_config_json': json.dumps(variant_config, default=str),
            'generated_options_json': json.dumps([o.to_dict() for o in options], default=str),
        }
    except Exception as e:
        logger.error(f"Failed to prepare replay for capture {capture.get('id')}: {e}")
        return None


def replay_single_sample(
    capture_id: int,
    variant_label: str,
    sample_number: int,
    prepared: Dict,
    model: str,
    provider: str,
) -> ReplayResult:
    """Execute a single LLM call for one (capture, variant, sample).

    Args:
        capture_id: The prompt_captures.id
        variant_label: Variant name
        sample_number: 1-indexed sample number
        prepared: Output of prepare_replay_prompt
        model: LLM model name
        provider: LLM provider name
    """
    options = prepared['options']
    option_config_json = prepared['option_config_json']
    generated_options_json = prepared['generated_options_json']

    start_time = time.time()
    try:
        # Disable reasoning for providers that don't support it (e.g., gemini-2.0-flash)
        reasoning = "low" if provider == "openai" else None
        client = LLMClient(provider=provider, model=model, reasoning_effort=reasoning)
        messages = [
            {"role": "system", "content": LEAN_SYSTEM_PROMPT},
            {"role": "user", "content": prepared['user_message']},
        ]
        response = client.complete(
            messages=messages,
            json_format=True,
            call_type=CallType.PLAYER_DECISION,
        )
        latency_ms = int((time.time() - start_time) * 1000)

        # Parse response
        choice_number = None
        new_action = ''
        new_raise_amount = None
        reasoning = ''
        try:
            result_data = json.loads(response.content)
            raw_choice = result_data.get('choice')
            reasoning = result_data.get('reasoning', '')

            # Coerce choice to int (Llama returns "1" as string)
            try:
                choice_number = int(raw_choice) if raw_choice is not None else None
            except (ValueError, TypeError):
                choice_number = None

            if choice_number and 1 <= choice_number <= len(options):
                chosen = options[choice_number - 1]
                new_action = chosen.action
                new_raise_amount = chosen.raise_to if chosen.raise_to > 0 else None
            else:
                new_action = 'invalid_choice'
        except (json.JSONDecodeError, TypeError):
            new_action = 'parse_error'
            reasoning = response.content[:500]

        return ReplayResult(
            capture_id=capture_id,
            variant=variant_label,
            sample_number=sample_number,
            success=True,
            option_config_json=option_config_json,
            generated_options_json=generated_options_json,
            new_response=response.content,
            choice_number=choice_number,
            new_action=new_action,
            new_raise_amount=new_raise_amount,
            reasoning=reasoning,
            provider=provider,
            model=model,
            input_tokens=getattr(response, 'input_tokens', 0),
            output_tokens=getattr(response, 'output_tokens', 0),
            latency_ms=latency_ms,
        )

    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        logger.error(
            f"Sample {sample_number} failed for capture {capture_id} "
            f"variant {variant_label}: {e}"
        )
        return ReplayResult(
            capture_id=capture_id,
            variant=variant_label,
            sample_number=sample_number,
            success=False,
            option_config_json=option_config_json,
            generated_options_json=generated_options_json,
            error_message=str(e),
            latency_ms=latency_ms,
        )


class BoundedReplayRunner:
    """Orchestrates bounded replay experiments."""

    def __init__(self, db_path: str = '/app/data/poker_games.db'):
        self.db_path = db_path

    def run(self, config: Dict) -> int:
        """Run a bounded replay experiment.

        Args:
            config: Experiment configuration dict

        Returns:
            experiment_id
        """
        name = config['name']
        description = config.get('description', '')
        source_experiment_id = config['source_experiment_id']
        capture_filters = config.get('capture_filters', {})
        variants = config['variants']
        samples_per_variant = config.get('samples_per_variant', 10)
        model = config.get('model', 'gpt-5-nano')
        provider = config.get('provider', 'openai')
        max_workers = config.get('max_workers', 5)

        # Create experiment record
        experiment_id = self._create_experiment(name, description, config)
        logger.info(f"Created bounded replay experiment {experiment_id}: {name}")

        # Load captures
        captures = self._load_captures(source_experiment_id, capture_filters)
        logger.info(f"Loaded {len(captures)} captures from experiment {source_experiment_id}")

        if not captures:
            logger.warning("No captures found matching filters")
            return experiment_id

        # Prepare prompts (CPU-only, fast) then flatten to per-sample work items
        sample_items = []  # (capture_id, variant_label, sample_num, prepared)
        prep_errors = 0
        for capture, metadata in captures:
            for variant in variants:
                prepared = prepare_replay_prompt(capture, metadata, variant)
                if prepared is None:
                    prep_errors += 1
                    continue
                variant_label = variant.get('label', 'unknown')
                for sample_num in range(1, samples_per_variant + 1):
                    sample_items.append((capture['id'], variant_label, sample_num, prepared))

        total_calls = len(sample_items)
        logger.info(
            f"Replay plan: {len(captures)} captures x {len(variants)} variants "
            f"x {samples_per_variant} samples = {total_calls} LLM calls "
            f"({prep_errors} prep failures)"
        )

        # Execute — every sample is its own parallel task
        completed = 0
        errors = 0
        log_interval = max(100, total_calls // 20)  # ~5% increments
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    replay_single_sample,
                    capture_id, variant_label, sample_num, prepared,
                    model, provider,
                ): (capture_id, variant_label, sample_num)
                for capture_id, variant_label, sample_num, prepared in sample_items
            }

            for future in as_completed(futures):
                try:
                    result = future.result()
                    self._store_result(experiment_id, result)
                    if not result.success:
                        errors += 1
                except Exception as e:
                    logger.error(f"Sample failed: {e}")
                    errors += 1

                completed += 1
                if completed % log_interval == 0 or completed == total_calls:
                    logger.info(f"Progress: {completed}/{total_calls} calls ({errors} errors)")

        # Generate and print report
        self._print_report(experiment_id, config)

        # Update experiment status
        self._complete_experiment(experiment_id)

        return experiment_id

    def dry_run(self, config: Dict) -> None:
        """Print what would happen without executing."""
        source_experiment_id = config['source_experiment_id']
        capture_filters = config.get('capture_filters', {})
        variants = config['variants']
        samples_per_variant = config.get('samples_per_variant', 10)
        model = config.get('model', 'gpt-5-nano')
        provider = config.get('provider', 'openai')

        captures = self._load_captures(source_experiment_id, capture_filters)

        total_captures = len(captures)
        total_calls = total_captures * len(variants) * samples_per_variant

        # Rough cost estimate (gpt-5-nano ~$0.00002/call)
        cost_estimate = total_calls * 0.00002

        print(f"\n=== Bounded Replay Dry Run ===")
        print(f"Source experiment: {source_experiment_id}")
        print(f"Captures found:   {total_captures}")
        print(f"Filters:          {json.dumps(capture_filters)}")
        print(f"Variants:         {len(variants)}")
        for v in variants:
            print(f"  - {v.get('label', '?')}: style_aware={v.get('style_aware_options')}, "
                  f"nudges={v.get('composed_nudges')}, rangegate={v.get('preflop_range_gate')}")
        print(f"Samples/variant:  {samples_per_variant}")
        print(f"Model:            {provider}/{model}")
        print(f"Total LLM calls:  {total_calls}")
        print(f"Est. cost:        ${cost_estimate:.2f}")
        print()

        # Show sample of captures
        if captures:
            print("Sample captures:")
            for capture, metadata in captures[:5]:
                player = capture.get('player_name', '?')
                phase = capture.get('phase') or metadata.get('phase', '?')
                action = capture.get('action_taken', '?')
                profile = metadata.get('style_profile', '?')
                print(f"  #{capture['id']}: {player} ({profile}) - {phase} - {action}")
            if len(captures) > 5:
                print(f"  ... and {len(captures) - 5} more")
        print()

    def _create_experiment(self, name: str, description: str, config: Dict) -> int:
        """Create an experiment record in the experiments table."""
        with sqlite3.connect(self.db_path) as conn:
            # Use a unique name to avoid conflicts
            import datetime
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            unique_name = f"{name}_{timestamp}"

            cursor = conn.execute(
                """INSERT INTO experiments (name, description, config_json, status)
                   VALUES (?, ?, ?, 'running')""",
                (unique_name, description, json.dumps(config, default=str))
            )
            return cursor.lastrowid

    def _load_captures(
        self,
        source_experiment_id: int,
        capture_filters: Dict,
    ) -> List[tuple]:
        """Load captures from source experiment with filters.

        Returns list of (capture_dict, metadata_dict) tuples.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Build query: join prompt_captures with experiment_games
            # Match both 'lean_bounded' and 'decision_lean_bounded' template names
            query = """
                SELECT pc.*
                FROM prompt_captures pc
                JOIN experiment_games eg ON pc.game_id = eg.game_id
                WHERE eg.experiment_id = ?
                  AND pc.prompt_template IN ('lean_bounded', 'decision_lean_bounded')
            """
            params = [source_experiment_id]

            # Apply capture filters
            if capture_filters.get('phase'):
                query += " AND pc.phase = ?"
                params.append(capture_filters['phase'])
            if capture_filters.get('player_name'):
                query += " AND pc.player_name = ?"
                params.append(capture_filters['player_name'])

            query += " ORDER BY pc.id"

            rows = conn.execute(query, params).fetchall()

            results = []
            for row in rows:
                capture = dict(row)
                # Parse metadata_json
                metadata = {}
                if capture.get('metadata_json'):
                    try:
                        metadata = json.loads(capture['metadata_json'])
                    except (json.JSONDecodeError, TypeError):
                        pass
                results.append((capture, metadata))

            return results

    def _store_result(self, experiment_id: int, result: ReplayResult) -> None:
        """Store a single replay result."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO bounded_replay_results (
                        experiment_id, capture_id, variant, sample_number,
                        option_config_json, generated_options_json,
                        new_response, choice_number, new_action, new_raise_amount,
                        reasoning, provider, model,
                        input_tokens, output_tokens, latency_ms,
                        error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    experiment_id, result.capture_id, result.variant, result.sample_number,
                    result.option_config_json, result.generated_options_json,
                    result.new_response, result.choice_number, result.new_action,
                    result.new_raise_amount,
                    result.reasoning, result.provider, result.model,
                    result.input_tokens, result.output_tokens, result.latency_ms,
                    result.error_message,
                ))
        except Exception as e:
            logger.error(f"Failed to store result: {e}")

    def _generate_summary(self, experiment_id: int) -> Dict:
        """Generate VPIP summary by player x variant."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get player name for each capture
            rows = conn.execute("""
                SELECT
                    brr.variant,
                    pc.player_name,
                    brr.new_action,
                    COUNT(*) as cnt
                FROM bounded_replay_results brr
                JOIN prompt_captures pc ON brr.capture_id = pc.id
                WHERE brr.experiment_id = ?
                  AND brr.error_message IS NULL
                GROUP BY brr.variant, pc.player_name, brr.new_action
                ORDER BY brr.variant, pc.player_name
            """, (experiment_id,)).fetchall()

            # Build summary: variant -> player -> {action: count}
            summary = {}
            for row in rows:
                variant = row['variant']
                player = row['player_name']
                action = row['new_action']
                count = row['cnt']

                if variant not in summary:
                    summary[variant] = {}
                if player not in summary[variant]:
                    summary[variant][player] = {}
                summary[variant][player][action] = count

            return summary

    def _print_report(self, experiment_id: int, config: Dict) -> None:
        """Print VPIP comparison matrix to console."""
        summary = self._generate_summary(experiment_id)

        if not summary:
            print("\nNo results to report.")
            return

        # Collect all players
        all_players = set()
        for variant_data in summary.values():
            all_players.update(variant_data.keys())
        players = sorted(all_players)

        variants = [v.get('label', '?') for v in config.get('variants', [])]

        print(f"\n{'='*70}")
        print(f"BOUNDED REPLAY RESULTS — Experiment {experiment_id}")
        print(f"{'='*70}")

        # VPIP table header
        header = f"{'Player':<20}"
        for v in variants:
            header += f"{v:>15}"
        print(header)
        print("-" * len(header))

        for player in players:
            row = f"{player:<20}"
            for variant_label in variants:
                player_actions = summary.get(variant_label, {}).get(player, {})
                total = sum(player_actions.values())
                folds = player_actions.get('fold', 0)
                vpip = ((total - folds) / total * 100) if total > 0 else 0
                row += f"{vpip:>13.1f}%"
            print(row)

        print("-" * len(header))

        # Action breakdown per variant
        print(f"\nAction Distribution:")
        for variant_label in variants:
            variant_data = summary.get(variant_label, {})
            total_actions = {}
            grand_total = 0
            for player_actions in variant_data.values():
                for action, count in player_actions.items():
                    total_actions[action] = total_actions.get(action, 0) + count
                    grand_total += count

            if grand_total > 0:
                action_str = ", ".join(
                    f"{a}: {c} ({c/grand_total*100:.0f}%)"
                    for a, c in sorted(total_actions.items())
                )
                print(f"  {variant_label}: {action_str}")

        # Error summary
        with sqlite3.connect(self.db_path) as conn:
            error_count = conn.execute(
                "SELECT COUNT(*) FROM bounded_replay_results WHERE experiment_id = ? AND error_message IS NOT NULL",
                (experiment_id,)
            ).fetchone()[0]
            total_count = conn.execute(
                "SELECT COUNT(*) FROM bounded_replay_results WHERE experiment_id = ?",
                (experiment_id,)
            ).fetchone()[0]

        print(f"\nTotal samples: {total_count} ({error_count} errors)")
        print(f"{'='*70}\n")

    def _complete_experiment(self, experiment_id: int) -> None:
        """Mark experiment as completed with summary."""
        summary = self._generate_summary(experiment_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE experiments SET status = 'completed', completed_at = CURRENT_TIMESTAMP,
                   summary_json = ? WHERE id = ?""",
                (json.dumps(summary, default=str), experiment_id)
            )


def _parse_json_field(value, default):
    """Parse a JSON string field, returning default on failure."""
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def main():
    parser = argparse.ArgumentParser(
        description="Run bounded replay experiment on captured AI decisions"
    )
    parser.add_argument(
        'config_path',
        help='Path to experiment config JSON file'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would happen without executing'
    )
    parser.add_argument(
        '--db-path',
        default='/app/data/poker_games.db',
        help='Database path (default: /app/data/poker_games.db)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )

    # Load config
    config_path = Path(args.config_path)
    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    runner = BoundedReplayRunner(db_path=args.db_path)

    if args.dry_run:
        runner.dry_run(config)
    else:
        experiment_id = runner.run(config)
        print(f"Experiment completed. ID: {experiment_id}")


if __name__ == '__main__':
    main()
