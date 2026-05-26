"""Phase 7.6 Step 4: per-decision intervention-trace attribution analysis.

Consumes persisted intervention traces (see Step 3b) and produces four
analyses, ordered by causal strength:

1. **shadow** (Mode 1, NOT YET IMPLEMENTED): same-state shadow
   evaluation. Re-runs the strategy pipeline against a frozen decision
   state with one rule disabled, then reports the L1 distribution
   distance vs the live pipeline. Strongest per-decision causality
   tool. Step 5 (this delivery) added the disable-rule plumbing
   (`TieredBotController.disable_rules`), so sweeps with rules ablated
   are now possible. The remaining gap is persistence-replay: we'd
   need to either (a) persist `(anchors, emotional_state,
   decision_context, base_strategy)` per decision so we can re-invoke
   the pipeline post-hoc, OR (b) run a second pipeline call live
   inside the controller with `disable_rules={target}` and persist
   both traces. Either is a self-contained follow-up.

2. **first-divergence** (Mode 2): for matched-seed candidate vs control
   runs, walks both decision streams in parallel, identifies the FIRST
   decision where chosen actions diverge, and attributes the
   divergence to whichever (layer, rule_id) trace differs at that
   point. Post-divergence decisions are labeled
   `different_trajectory_context` and excluded from per-decision
   attribution claims. Plan §"Mode 2: First-divergence analysis".

3. **aggregate** (Mode 3): per-layer / per-rule_id firing rates across
   one or more games. Reports: total decisions, fired count, fire %,
   top reason codes, average effect_size when fired. Diagnostic
   signal — what's actually running. EV-delta extension requires
   matched-seed sweep tooling (future work).

4. **ablation** (Mode 4): compares a baseline run (no rules disabled)
   to an ablation run (one or more rules disabled via
   `TieredBotController.disable_rules`). Reports per-decision
   action-change rate attributable to the ablated rules. Implementation
   keys off the `disabled_by_ablation` reason_code persisted in the
   ablation run's traces — Mode 4 reads paired games and reports how
   often the ablated rule's removal flipped the chosen action.

Usage:

    # Firing-rate diagnostic for one game
    docker compose exec backend python -m experiments.analyze_intervention_traces \\
        --mode aggregate --db /app/data/poker_games.db --game-id game_abc123

    # Aggregate across all games
    docker compose exec backend python -m experiments.analyze_intervention_traces \\
        --mode aggregate --db /app/data/poker_games.db --all-games

    # First-divergence comparison between candidate / control runs
    docker compose exec backend python -m experiments.analyze_intervention_traces \\
        --mode first-divergence --db /app/data/poker_games.db \\
        --candidate-game game_cand --control-game game_ctrl

    # Mode 1 + Mode 4 stubs — emit a structured "not yet implemented" error
    --mode shadow / --mode ablation

Output format defaults to text; pass --output json for machine-readable
JSON suitable for downstream piping into jq or a notebook.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from poker.repositories.decision_analysis_repository import DecisionAnalysisRepository

_MODE_CHOICES = ('shadow', 'first-divergence', 'aggregate', 'ablation')


# ── Shared helpers ──────────────────────────────────────────────────────


def _trace_key(entry: Dict[str, Any]) -> Tuple[str, str]:
    """Canonical (layer, rule_id) key for grouping/matching."""
    return (entry.get('layer', ''), entry.get('rule_id', 'default'))


def _index_trace_by_rule(
    trace: Sequence[Dict[str, Any]],
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Build a (layer, rule_id) → trace-entry dict for O(1) per-rule lookup."""
    return {_trace_key(entry): entry for entry in trace}


def _load_decisions(
    repo: DecisionAnalysisRepository,
    game_id: str,
) -> List[Dict[str, Any]]:
    """Load all decisions with non-null traces for a game.

    Returns rows ordered by id (insertion order = decision order).
    """
    return repo.get_intervention_traces_for_game(game_id)


# ── Mode 3: aggregate firing rates ──────────────────────────────────────


def aggregate_firing_rates(
    repo: DecisionAnalysisRepository,
    game_ids: Sequence[str],
) -> Dict[str, Any]:
    """Compute per-(layer, rule_id) firing rates across `game_ids`.

    For each rule, reports total evaluations, fired count, fire rate,
    top 3 reason_codes (with counts), and mean effect_size when fired.
    """
    total_per_rule: Counter = Counter()
    fired_per_rule: Counter = Counter()
    reason_codes: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    effect_sizes_when_fired: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    decision_count = 0

    for game_id in game_ids:
        decisions = _load_decisions(repo, game_id)
        for decision in decisions:
            decision_count += 1
            for entry in decision.get('trace', []):
                key = _trace_key(entry)
                total_per_rule[key] += 1
                reason_codes[key][entry.get('reason_code', '')] += 1
                if entry.get('fired'):
                    fired_per_rule[key] += 1
                    es = entry.get('effect_size', 0.0)
                    if isinstance(es, int | float):
                        effect_sizes_when_fired[key].append(float(es))

    rules_sorted = sorted(
        total_per_rule.keys(),
        key=lambda k: (k[0], k[1]),  # (layer asc, rule_id asc)
    )
    per_rule: List[Dict[str, Any]] = []
    for layer, rule_id in rules_sorted:
        total = total_per_rule[(layer, rule_id)]
        fired = fired_per_rule.get((layer, rule_id), 0)
        es_samples = effect_sizes_when_fired.get((layer, rule_id), [])
        mean_es = round(sum(es_samples) / len(es_samples), 4) if es_samples else 0.0
        top_reasons = reason_codes[(layer, rule_id)].most_common(3)
        per_rule.append(
            {
                'layer': layer,
                'rule_id': rule_id,
                'evaluated': total,
                'fired': fired,
                'fire_rate_pct': round(100.0 * fired / total, 2) if total else 0.0,
                'mean_effect_size_when_fired': mean_es,
                'top_reason_codes': [{'code': code, 'count': count} for code, count in top_reasons],
            }
        )
    return {
        'mode': 'aggregate',
        'games': list(game_ids),
        'decisions_total': decision_count,
        'per_rule': per_rule,
    }


def _format_aggregate_text(report: Dict[str, Any]) -> str:
    lines = [
        "Mode: aggregate firing rates",
        f"Games: {len(report['games'])} ({', '.join(report['games'])[:80]})",
        f"Decisions analyzed: {report['decisions_total']}",
        '',
        f"{'layer':<22} {'rule_id':<20} {'evaluated':>9} {'fired':>6} {'fire%':>7} {'mean_size':>10}  top reasons",
        '─' * 110,
    ]
    for row in report['per_rule']:
        reasons = ', '.join(f"{r['code']}={r['count']}" for r in row['top_reason_codes'])
        lines.append(
            f"{row['layer']:<22} {row['rule_id']:<20} "
            f"{row['evaluated']:>9} {row['fired']:>6} "
            f"{row['fire_rate_pct']:>6.1f}% "
            f"{row['mean_effect_size_when_fired']:>10.4f}  {reasons[:60]}"
        )
    return '\n'.join(lines)


# ── Mode 2: first-divergence analysis ───────────────────────────────────


def first_divergence(
    repo: DecisionAnalysisRepository,
    candidate_game_id: str,
    control_game_id: str,
) -> Dict[str, Any]:
    """Compare candidate vs control decision streams; identify the
    first divergence point per hand and attribute to differing rules.

    Walks both decision lists in parallel using `(hand_number, phase)`
    as the matching key. The post-divergence exclusion zone (plan §"Mode 2"):
    once a hand has a chosen-action divergence, subsequent decisions
    on that hand are labeled `different_trajectory_context` and
    counted separately — they are NOT used for per-decision
    attribution claims because the trajectories no longer share a
    state.
    """
    candidate = _load_decisions(repo, candidate_game_id)
    control = _load_decisions(repo, control_game_id)

    # Group by hand_number so the parallel walk respects hand boundaries.
    cand_by_hand: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    ctrl_by_hand: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for d in candidate:
        cand_by_hand[d.get('hand_number')].append(d)
    for d in control:
        ctrl_by_hand[d.get('hand_number')].append(d)

    shared_hands = sorted(set(cand_by_hand) & set(ctrl_by_hand))
    attribution: Counter = Counter()  # (layer, rule_id) → first-divergence count
    post_divergence_excluded = 0
    hands_with_divergence = 0
    hands_no_divergence = 0

    for hand in shared_hands:
        cand_decisions = cand_by_hand[hand]
        ctrl_decisions = ctrl_by_hand[hand]
        diverged_idx = _find_first_divergence_idx(cand_decisions, ctrl_decisions)
        if diverged_idx is None:
            hands_no_divergence += 1
            continue
        hands_with_divergence += 1
        # Attribute the divergence to differing trace entries at the
        # divergence point.
        cand_trace = cand_decisions[diverged_idx].get('trace', [])
        ctrl_trace = ctrl_decisions[diverged_idx].get('trace', [])
        differing_rules = _differing_rules(cand_trace, ctrl_trace)
        for key in differing_rules:
            attribution[key] += 1
        # Everything after the divergence on this hand is excluded.
        remaining_cand = max(0, len(cand_decisions) - diverged_idx - 1)
        remaining_ctrl = max(0, len(ctrl_decisions) - diverged_idx - 1)
        post_divergence_excluded += min(remaining_cand, remaining_ctrl)

    return {
        'mode': 'first-divergence',
        'candidate_game_id': candidate_game_id,
        'control_game_id': control_game_id,
        'shared_hands': len(shared_hands),
        'hands_with_divergence': hands_with_divergence,
        'hands_no_divergence': hands_no_divergence,
        'post_divergence_excluded_decisions': post_divergence_excluded,
        'attribution': [
            {'layer': layer, 'rule_id': rule_id, 'first_divergence_count': count}
            for (layer, rule_id), count in attribution.most_common()
        ],
    }


def _find_first_divergence_idx(
    cand: Sequence[Dict[str, Any]],
    ctrl: Sequence[Dict[str, Any]],
) -> Optional[int]:
    """Return the index of the first decision where chosen actions differ,
    or None if the two streams agree on all shared decisions."""
    n = min(len(cand), len(ctrl))
    for i in range(n):
        if cand[i].get('action_taken') != ctrl[i].get('action_taken'):
            return i
    return None


def _differing_rules(
    cand_trace: Sequence[Dict[str, Any]],
    ctrl_trace: Sequence[Dict[str, Any]],
) -> List[Tuple[str, str]]:
    """Find (layer, rule_id) entries that differ between two traces.

    Differences considered: fired flag, primary_action_after,
    reason_code. (effect_size and rationale strings are noisy — not
    used as divergence signals.)
    """
    cand_index = _index_trace_by_rule(cand_trace)
    ctrl_index = _index_trace_by_rule(ctrl_trace)
    all_keys = set(cand_index) | set(ctrl_index)
    differing: List[Tuple[str, str]] = []
    for key in all_keys:
        c = cand_index.get(key, {})
        x = ctrl_index.get(key, {})
        if (
            c.get('fired') != x.get('fired')
            or c.get('primary_action_after') != x.get('primary_action_after')
            or c.get('reason_code') != x.get('reason_code')
        ):
            differing.append(key)
    return sorted(differing)


def _format_first_divergence_text(report: Dict[str, Any]) -> str:
    lines = [
        "Mode: first-divergence (matched-seed candidate vs control)",
        f"Candidate game: {report['candidate_game_id']}",
        f"Control game:   {report['control_game_id']}",
        f"Shared hands: {report['shared_hands']}",
        f"  with divergence:    {report['hands_with_divergence']}",
        f"  no divergence:      {report['hands_no_divergence']}",
        f"Post-divergence decisions excluded: "
        f"{report['post_divergence_excluded_decisions']} "
        f"(labeled 'different_trajectory_context')",
        '',
        "First-divergence attribution by rule:",
    ]
    if not report['attribution']:
        lines.append("  (no divergences attributed)")
    else:
        for row in report['attribution']:
            lines.append(f"  {row['layer']}.{row['rule_id']}: " f"{row['first_divergence_count']}")
    return '\n'.join(lines)


# ── Mode 4: ablation compare ────────────────────────────────────────────


def ablation_compare(
    repo: DecisionAnalysisRepository,
    baseline_game_id: str,
    ablation_game_id: str,
) -> Dict[str, Any]:
    """Compare a baseline run to an ablation run (rule(s) disabled).

    The ablation run was produced by setting
    `TieredBotController.disable_rules` to one or more (layer, rule_id)
    entries before running the sim. Decisions in the ablation run
    where those rules would have fired now carry the
    `disabled_by_ablation` reason_code in the trace.

    Reports per disabled rule:
      - Decisions evaluated in the ablation run
      - Decisions where action_taken differs between baseline and
        ablation (paired by hand_number + phase)
      - "action change rate" = differing / paired
      - Aggregate distance from baseline (sum |Δ action|)

    Like Mode 2, post-divergence decisions on a hand are excluded
    from per-decision attribution — once an ablated rule changes one
    action, the trajectory diverges and downstream decisions reflect
    secondary effects.
    """
    baseline = _load_decisions(repo, baseline_game_id)
    ablation = _load_decisions(repo, ablation_game_id)

    # Discover which rules were ablated by scanning the ablation
    # run's traces for the `disabled_by_ablation` reason_code.
    ablated_rules: set = set()
    for decision in ablation:
        for entry in decision.get('trace', []):
            if entry.get('reason_code') == 'disabled_by_ablation':
                ablated_rules.add(_trace_key(entry))

    # Group both runs by hand for parallel walk.
    base_by_hand: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    abl_by_hand: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for d in baseline:
        base_by_hand[d.get('hand_number')].append(d)
    for d in ablation:
        abl_by_hand[d.get('hand_number')].append(d)

    shared_hands = sorted(set(base_by_hand) & set(abl_by_hand))
    action_changed_count = 0
    action_paired_count = 0
    post_divergence_excluded = 0
    for hand in shared_hands:
        base_decisions = base_by_hand[hand]
        abl_decisions = abl_by_hand[hand]
        diverged_idx = _find_first_divergence_idx(base_decisions, abl_decisions)
        n = min(len(base_decisions), len(abl_decisions))
        if diverged_idx is None:
            action_paired_count += n
        else:
            action_paired_count += diverged_idx + 1  # incl. divergence point
            action_changed_count += 1
            # Decisions after the divergence: excluded.
            post_divergence_excluded += max(0, n - diverged_idx - 1)

    change_rate = action_changed_count / action_paired_count if action_paired_count else 0.0
    return {
        'mode': 'ablation',
        'baseline_game_id': baseline_game_id,
        'ablation_game_id': ablation_game_id,
        'ablated_rules': [
            {'layer': layer, 'rule_id': rule_id} for (layer, rule_id) in sorted(ablated_rules)
        ],
        'shared_hands': len(shared_hands),
        'paired_decisions': action_paired_count,
        'action_changed_decisions': action_changed_count,
        'action_change_rate': round(change_rate, 4),
        'post_divergence_excluded': post_divergence_excluded,
    }


def _format_ablation_text(report: Dict[str, Any]) -> str:
    rules_str = (
        ', '.join(f"{r['layer']}.{r['rule_id']}" for r in report['ablated_rules'])
        or '(none detected)'
    )
    lines = [
        "Mode: ablation comparison",
        f"Baseline game: {report['baseline_game_id']}",
        f"Ablation game: {report['ablation_game_id']}",
        f"Ablated rules: {rules_str}",
        f"Shared hands: {report['shared_hands']}",
        f"Paired decisions (pre-divergence): {report['paired_decisions']}",
        f"Decisions where action changed:    {report['action_changed_decisions']}",
        f"Action change rate: {report['action_change_rate'] * 100:.2f}%",
        f"Post-divergence decisions excluded: {report['post_divergence_excluded']}",
    ]
    return '\n'.join(lines)


# ── Mode 1: shadow-eval (same-state per-decision attribution) ───────────


def shadow_eval(
    repo: DecisionAnalysisRepository,
    game_id: str,
    disable_rule: Tuple[str, str],
) -> Dict[str, Any]:
    """Phase 7.6 Mode 1: same-state shadow evaluation.

    For each decision in `game_id` that has a persisted pipeline
    snapshot (column `strategy_pipeline_snapshot_json`), re-runs the
    strategy pipeline twice:
      1. Live: `disable_rules=frozenset()` — reproduces the live
         strategy.
      2. Shadow: `disable_rules={disable_rule}` — counterfactual with
         the target rule suppressed.

    Reports the L1 distance between the two distributions per
    decision, plus an action-flip rate (argmax differs between live
    and shadow).

    Decisions without snapshots are skipped and counted as
    `no_snapshot_coverage`.
    """
    # Local import to avoid hard dependency at module load: shadow-eval
    # is an offline tool, replay.py pulls in strategy modules.
    from poker.strategy.replay import replay_strategy_pipeline

    with repo._get_connection() as conn:
        cursor = conn.execute(
            "SELECT id, hand_number, phase, action_taken, "
            "intervention_trace_json, strategy_pipeline_snapshot_json "
            "FROM player_decision_analysis "
            "WHERE game_id = ? ORDER BY id ASC",
            (game_id,),
        )
        rows = cursor.fetchall()

    total = len(rows)
    no_snapshot = 0
    evaluated = 0
    l1_distances: List[float] = []
    action_flips = 0
    shadow_distances_by_decision: List[Dict[str, Any]] = []

    target = tuple(disable_rule)  # ensure tuple form
    disable_set = frozenset({target})

    for analysis_id, hand_number, phase, action_taken, _trace_json, snap_json in rows:
        if not snap_json:
            no_snapshot += 1
            continue
        try:
            snapshot = json.loads(snap_json)
        except (json.JSONDecodeError, TypeError):
            no_snapshot += 1
            continue
        if not isinstance(snapshot, dict):
            no_snapshot += 1
            continue

        try:
            live = replay_strategy_pipeline(snapshot, disable_rules=frozenset())
            shadow = replay_strategy_pipeline(snapshot, disable_rules=disable_set)
        except Exception:
            no_snapshot += 1
            continue

        # L1 distance over the union of action keys.
        actions = set(live.action_probabilities) | set(shadow.action_probabilities)
        l1 = sum(
            abs(live.action_probabilities.get(a, 0.0) - shadow.action_probabilities.get(a, 0.0))
            for a in actions
        )
        l1_distances.append(l1)

        live_argmax = _argmax(live.action_probabilities)
        shadow_argmax = _argmax(shadow.action_probabilities)
        if live_argmax != shadow_argmax:
            action_flips += 1

        evaluated += 1
        shadow_distances_by_decision.append(
            {
                'analysis_id': analysis_id,
                'hand_number': hand_number,
                'phase': phase,
                'live_argmax': live_argmax,
                'shadow_argmax': shadow_argmax,
                'l1_distance': round(l1, 4),
            }
        )

    mean_l1 = sum(l1_distances) / len(l1_distances) if l1_distances else 0.0
    max_l1 = max(l1_distances) if l1_distances else 0.0
    flip_rate = action_flips / evaluated if evaluated else 0.0

    return {
        'mode': 'shadow',
        'game_id': game_id,
        'disable_rule': f'{target[0]}.{target[1]}',
        'total_decisions': total,
        'evaluated_decisions': evaluated,
        'no_snapshot_coverage': no_snapshot,
        'mean_l1_distance': round(mean_l1, 4),
        'max_l1_distance': round(max_l1, 4),
        'action_flips': action_flips,
        'action_flip_rate': round(flip_rate, 4),
    }


def _argmax(probs: Dict[str, float]) -> str:
    """argmax action; empty distributions return ''."""
    if not probs:
        return ''
    best = ''
    best_prob = -1.0
    for action, prob in probs.items():
        if prob > best_prob:
            best_prob = prob
            best = action
    return best if best_prob > 0.0 else ''


def _format_shadow_text(report: Dict[str, Any]) -> str:
    lines = [
        "Mode: shadow-eval (same-state per-decision attribution)",
        f"Game: {report['game_id']}",
        f"Disabled rule: {report['disable_rule']}",
        f"Decisions in game: {report['total_decisions']}",
        f"  evaluated:           {report['evaluated_decisions']}",
        f"  no snapshot coverage: {report['no_snapshot_coverage']}",
        f"Mean L1 distance (live vs shadow): {report['mean_l1_distance']:.4f}",
        f"Max L1 distance:                   {report['max_l1_distance']:.4f}",
        f"Action flips (argmax differs):     {report['action_flips']} "
        f"({report['action_flip_rate'] * 100:.2f}%)",
    ]
    return '\n'.join(lines)


# ── CLI entry point ─────────────────────────────────────────────────────


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='analyze_intervention_traces',
        description=(
            'Phase 7.6 per-decision intervention-trace attribution. '
            'See module docstring for full mode descriptions.'
        ),
    )
    parser.add_argument(
        '--mode',
        choices=_MODE_CHOICES,
        required=True,
        help='Which attribution mode to run.',
    )
    parser.add_argument(
        '--db',
        required=True,
        help='Path to poker_games.db (e.g. /app/data/poker_games.db in Docker).',
    )
    parser.add_argument(
        '--game-id',
        help='Game id to analyze. For aggregate mode, omit to scan all games.',
    )
    parser.add_argument(
        '--all-games',
        action='store_true',
        help='Aggregate mode only: aggregate across all games in the DB.',
    )
    parser.add_argument(
        '--candidate-game',
        help='first-divergence mode: candidate run game id.',
    )
    parser.add_argument(
        '--control-game',
        help='first-divergence mode: control run game id.',
    )
    parser.add_argument(
        '--baseline-game',
        help='ablation mode: baseline run game id (no rules disabled).',
    )
    parser.add_argument(
        '--ablation-game',
        help=(
            'ablation mode: ablation run game id (one or more rules '
            'disabled via TieredBotController.disable_rules).'
        ),
    )
    parser.add_argument(
        '--disable-rule',
        help=(
            'shadow mode (not yet implemented): rule to disable in '
            'the format "layer.rule_id" '
            '(e.g. "bluff_catch_override.default"). For Mode 4, the '
            'ablated rules are auto-detected from the ablation run\'s '
            'traces (look for reason_code=disabled_by_ablation).'
        ),
    )
    parser.add_argument(
        '--output',
        choices=('text', 'json'),
        default='text',
        help='Report format.',
    )
    return parser.parse_args(argv)


def _list_all_game_ids(repo: DecisionAnalysisRepository) -> List[str]:
    """Distinct game_ids that have at least one trace-bearing decision row."""
    # Use a small inline query — keeps the public repo API focused.
    with repo._get_connection() as conn:
        cursor = conn.execute(
            "SELECT DISTINCT game_id FROM player_decision_analysis "
            "WHERE intervention_trace_json IS NOT NULL"
        )
        return [row[0] for row in cursor.fetchall()]


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    repo = DecisionAnalysisRepository(args.db)

    if args.mode == 'shadow':
        if not args.game_id:
            sys.stderr.write("shadow mode requires --game-id\n")
            return 2
        if not args.disable_rule:
            sys.stderr.write("shadow mode requires --disable-rule layer.rule_id\n")
            return 2
        try:
            layer, rule_id = args.disable_rule.split('.', 1)
        except ValueError:
            sys.stderr.write("--disable-rule must be in 'layer.rule_id' format\n")
            return 2
        report = shadow_eval(repo, args.game_id, (layer, rule_id))
        text_formatter = _format_shadow_text

    elif args.mode == 'aggregate':
        if args.all_games:
            game_ids = _list_all_game_ids(repo)
        elif args.game_id:
            game_ids = [args.game_id]
        else:
            sys.stderr.write("aggregate mode requires --game-id GAME or --all-games\n")
            return 2
        report = aggregate_firing_rates(repo, game_ids)
        text_formatter = _format_aggregate_text

    elif args.mode == 'first-divergence':
        if not (args.candidate_game and args.control_game):
            sys.stderr.write(
                "first-divergence mode requires --candidate-game and " "--control-game\n"
            )
            return 2
        report = first_divergence(repo, args.candidate_game, args.control_game)
        text_formatter = _format_first_divergence_text

    elif args.mode == 'ablation':
        if not (args.baseline_game and args.ablation_game):
            sys.stderr.write("ablation mode requires --baseline-game and --ablation-game\n")
            return 2
        report = ablation_compare(repo, args.baseline_game, args.ablation_game)
        text_formatter = _format_ablation_text

    else:
        sys.stderr.write(f"Unknown mode: {args.mode}\n")
        return 2

    if args.output == 'json':
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(text_formatter(report))
    return 0


if __name__ == '__main__':
    sys.exit(main())
