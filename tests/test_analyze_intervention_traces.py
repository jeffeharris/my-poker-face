"""Tests for experiments/analyze_intervention_traces.py.

Builds a temp DB, populates it with synthetic intervention traces,
and exercises the implemented modes (aggregate, first-divergence).
Mode 1 (shadow) and Mode 4 (ablation) are stubs — the test asserts
they exit with the expected message + exit code.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile

import pytest

from experiments.analyze_intervention_traces import (
    aggregate_firing_rates,
    first_divergence,
    main,
)
from poker.repositories.decision_analysis_repository import DecisionAnalysisRepository
from poker.repositories.schema_manager import SchemaManager

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    SchemaManager(path).ensure_schema()
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _insert_game(conn: sqlite3.Connection, game_id: str) -> None:
    conn.execute(
        "INSERT INTO games "
        "(game_id, phase, num_players, pot_size, game_state_json, owner_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (game_id, 'FLOP', 2, 0.0, '{}', 'test'),
    )


def _insert_decision(
    conn: sqlite3.Connection,
    *,
    game_id: str,
    hand_number: int,
    phase: str,
    action_taken: str,
    trace_entries: list,
    snapshot: dict = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO player_decision_analysis "
        "(game_id, player_name, hand_number, phase, action_taken, "
        "intervention_trace_json, strategy_pipeline_snapshot_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            game_id,
            'Hero',
            hand_number,
            phase,
            action_taken,
            json.dumps(trace_entries),
            json.dumps(snapshot) if snapshot is not None else None,
        ),
    )
    return cur.lastrowid


# ── Mode 3: aggregate firing rates ──────────────────────────────────────


class TestAggregate:
    def test_counts_per_rule_evaluations_and_fires(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        try:
            _insert_game(conn, 'g1')
            # Decision 1: hyper_aggressive fires, hyper_passive doesn't
            _insert_decision(
                conn,
                game_id='g1',
                hand_number=1,
                phase='FLOP',
                action_taken='call',
                trace_entries=[
                    {
                        'layer': 'exploitation',
                        'rule_id': 'hyper_aggressive',
                        'fired': True,
                        'operation': 'adjust',
                        'effect_size': 0.4,
                        'reason_code': 'extreme_tier',
                    },
                    {
                        'layer': 'exploitation',
                        'rule_id': 'hyper_passive',
                        'fired': False,
                        'operation': 'no_op',
                        'effect_size': 0.0,
                        'reason_code': 'intensity_below_threshold',
                    },
                ],
            )
            # Decision 2: hyper_aggressive doesn't fire either
            _insert_decision(
                conn,
                game_id='g1',
                hand_number=2,
                phase='TURN',
                action_taken='fold',
                trace_entries=[
                    {
                        'layer': 'exploitation',
                        'rule_id': 'hyper_aggressive',
                        'fired': False,
                        'operation': 'no_op',
                        'effect_size': 0.0,
                        'reason_code': 'intensity_below_threshold',
                    },
                    {
                        'layer': 'exploitation',
                        'rule_id': 'hyper_passive',
                        'fired': False,
                        'operation': 'no_op',
                        'effect_size': 0.0,
                        'reason_code': 'intensity_below_threshold',
                    },
                ],
            )
            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        report = aggregate_firing_rates(repo, ['g1'])

        assert report['mode'] == 'aggregate'
        assert report['decisions_total'] == 2
        rule_index = {(r['layer'], r['rule_id']): r for r in report['per_rule']}
        agg = rule_index[('exploitation', 'hyper_aggressive')]
        assert agg['evaluated'] == 2
        assert agg['fired'] == 1
        assert agg['fire_rate_pct'] == 50.0
        assert agg['mean_effect_size_when_fired'] == pytest.approx(0.4)
        # Top reasons captured.
        codes = {r['code'] for r in agg['top_reason_codes']}
        assert 'extreme_tier' in codes
        assert 'intensity_below_threshold' in codes

    def test_aggregates_across_multiple_games(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        try:
            for gid in ('g_a', 'g_b'):
                _insert_game(conn, gid)
                _insert_decision(
                    conn,
                    game_id=gid,
                    hand_number=1,
                    phase='FLOP',
                    action_taken='raise',
                    trace_entries=[
                        {
                            'layer': 'personality',
                            'rule_id': 'default',
                            'fired': True,
                            'operation': 'adjust',
                            'effect_size': 0.3,
                            'reason_code': 'deviation_profile_lag',
                        },
                    ],
                )
            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        report = aggregate_firing_rates(repo, ['g_a', 'g_b'])
        assert report['decisions_total'] == 2
        assert len(report['per_rule']) == 1
        assert report['per_rule'][0]['fired'] == 2

    def test_empty_game_returns_zero_decisions(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        try:
            _insert_game(conn, 'empty')
            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        report = aggregate_firing_rates(repo, ['empty'])
        assert report['decisions_total'] == 0
        assert report['per_rule'] == []


# ── Mode 2: first-divergence ────────────────────────────────────────────


class TestFirstDivergence:
    def test_identifies_divergence_and_attributes_to_differing_rule(self, tmp_db):
        """Candidate and control: same first decision, then diverge on hand 1
        decision 2 — candidate calls, control folds. The differing rule is
        bluff_catch_override (fires only on candidate)."""
        conn = sqlite3.connect(tmp_db)
        try:
            _insert_game(conn, 'cand')
            _insert_game(conn, 'ctrl')

            # Hand 1 decision 1: both fold, traces agree.
            common_trace = [
                {
                    'layer': 'personality',
                    'rule_id': 'default',
                    'fired': True,
                    'operation': 'adjust',
                    'primary_action_after': 'fold',
                    'reason_code': 'deviation_profile_tag',
                },
            ]
            _insert_decision(
                conn,
                game_id='cand',
                hand_number=1,
                phase='FLOP',
                action_taken='fold',
                trace_entries=common_trace,
            )
            _insert_decision(
                conn,
                game_id='ctrl',
                hand_number=1,
                phase='FLOP',
                action_taken='fold',
                trace_entries=common_trace,
            )

            # Hand 1 decision 2: divergence. Candidate's bluff_catch fires.
            _insert_decision(
                conn,
                game_id='cand',
                hand_number=1,
                phase='TURN',
                action_taken='call',
                trace_entries=[
                    {
                        'layer': 'personality',
                        'rule_id': 'default',
                        'fired': True,
                        'operation': 'adjust',
                        'primary_action_after': 'call',
                        'reason_code': 'deviation_profile_tag',
                    },
                    {
                        'layer': 'bluff_catch_override',
                        'rule_id': 'default',
                        'fired': True,
                        'operation': 'override',
                        'primary_action_after': 'call',
                        'reason_code': 'medium_made_vs_extreme_facing_bet',
                    },
                ],
            )
            _insert_decision(
                conn,
                game_id='ctrl',
                hand_number=1,
                phase='TURN',
                action_taken='fold',
                trace_entries=[
                    {
                        'layer': 'personality',
                        'rule_id': 'default',
                        'fired': True,
                        'operation': 'adjust',
                        'primary_action_after': 'fold',
                        'reason_code': 'deviation_profile_tag',
                    },
                    {
                        'layer': 'bluff_catch_override',
                        'rule_id': 'default',
                        'fired': False,
                        'operation': 'no_op',
                        'primary_action_after': '',
                        'reason_code': 'gate_rejected',
                    },
                ],
            )

            # Hand 1 decision 3: post-divergence — should be excluded.
            _insert_decision(
                conn,
                game_id='cand',
                hand_number=1,
                phase='RIVER',
                action_taken='check',
                trace_entries=common_trace,
            )
            _insert_decision(
                conn,
                game_id='ctrl',
                hand_number=1,
                phase='RIVER',
                action_taken='check',
                trace_entries=common_trace,
            )

            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        report = first_divergence(repo, 'cand', 'ctrl')

        assert report['mode'] == 'first-divergence'
        assert report['shared_hands'] == 1
        assert report['hands_with_divergence'] == 1
        assert report['hands_no_divergence'] == 0
        # Decision 3 (post-divergence) excluded → 1 excluded decision.
        assert report['post_divergence_excluded_decisions'] == 1

        # Both personality and bluff_catch_override differ at the divergence
        # decision (personality primary_action_after, bluff_catch fired flag).
        attrib = {
            (r['layer'], r['rule_id']): r['first_divergence_count'] for r in report['attribution']
        }
        assert ('bluff_catch_override', 'default') in attrib
        assert attrib[('bluff_catch_override', 'default')] == 1

    def test_no_divergence_when_streams_match(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        try:
            _insert_game(conn, 'a')
            _insert_game(conn, 'b')
            trace = [
                {
                    'layer': 'personality',
                    'rule_id': 'default',
                    'fired': True,
                    'operation': 'adjust',
                    'primary_action_after': 'fold',
                    'reason_code': 'deviation_profile_tag',
                }
            ]
            for gid in ('a', 'b'):
                _insert_decision(
                    conn,
                    game_id=gid,
                    hand_number=1,
                    phase='FLOP',
                    action_taken='fold',
                    trace_entries=trace,
                )
            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        report = first_divergence(repo, 'a', 'b')
        assert report['hands_with_divergence'] == 0
        assert report['hands_no_divergence'] == 1
        assert report['attribution'] == []


# ── Mode 1 + Mode 4 stubs ───────────────────────────────────────────────


class TestShadowEval:
    """Mode 1 shadow-eval is implemented as of Step 6."""

    def test_skips_decisions_without_snapshot(self, tmp_db):
        from experiments.analyze_intervention_traces import shadow_eval

        conn = sqlite3.connect(tmp_db)
        try:
            _insert_game(conn, 'g')
            # No snapshot — Mode 1 should skip this decision.
            _insert_decision(
                conn,
                game_id='g',
                hand_number=1,
                phase='FLOP',
                action_taken='fold',
                trace_entries=[],
            )
            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        report = shadow_eval(repo, 'g', ('bluff_catch_override', 'default'))
        assert report['total_decisions'] == 1
        assert report['evaluated_decisions'] == 0
        assert report['no_snapshot_coverage'] == 1

    def test_evaluates_decisions_with_snapshot(self, tmp_db):
        """With a snapshot present, Mode 1 invokes the replay function
        live + shadow and reports L1 distance."""
        from experiments.analyze_intervention_traces import shadow_eval

        conn = sqlite3.connect(tmp_db)
        try:
            _insert_game(conn, 'g')
            # Minimal snapshot that produces a deterministic strategy.
            snapshot = {
                'phase': 'POSTFLOP',
                'base_strategy_probs': {'fold': 0.5, 'call': 0.5},
                'legal_actions': ['fold', 'call'],
            }
            _insert_decision(
                conn,
                game_id='g',
                hand_number=1,
                phase='FLOP',
                action_taken='fold',
                trace_entries=[],
                snapshot=snapshot,
            )
            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        report = shadow_eval(repo, 'g', ('bluff_catch_override', 'default'))
        assert report['evaluated_decisions'] == 1
        # No layer fires on this snapshot → L1 distance is 0.
        assert report['mean_l1_distance'] == pytest.approx(0.0)
        assert report['action_flips'] == 0

    def test_reports_l1_distance_when_rule_changes_outcome(self, tmp_db):
        """Pot-committed snapshot — disabling math_floor changes the
        strategy from 100% call back to the input distribution."""
        from experiments.analyze_intervention_traces import shadow_eval

        conn = sqlite3.connect(tmp_db)
        try:
            _insert_game(conn, 'g')
            snapshot = {
                'phase': 'POSTFLOP',
                'base_strategy_probs': {'fold': 0.7, 'call': 0.3},
                'legal_actions': ['fold', 'call'],
                'cost_to_call': 100,
                'pot_total': 5000,
                'player_stack': 400,
                'player_bet': 800,  # pot-committed
                'big_blind': 100,
            }
            _insert_decision(
                conn,
                game_id='g',
                hand_number=1,
                phase='FLOP',
                action_taken='call',
                trace_entries=[],
                snapshot=snapshot,
            )
            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        report = shadow_eval(repo, 'g', ('math_floor', 'default'))
        # Live: 100% call. Shadow (math_floor disabled): {fold:0.7, call:0.3}.
        # L1 = |0-0.7| + |1.0-0.3| = 1.4
        assert report['mean_l1_distance'] == pytest.approx(1.4, abs=0.01)
        # Argmax flips from 'call' to 'fold'.
        assert report['action_flips'] == 1
        assert report['action_flip_rate'] == pytest.approx(1.0)


class TestShadowCli:
    """Shadow mode is implemented as of Step 6 — exit cleanly with
    proper args, error on missing args."""

    def test_missing_game_id_returns_2(self, tmp_db, capsys):
        exit_code = main(['--mode', 'shadow', '--db', tmp_db])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert '--game-id' in captured.err

    def test_missing_disable_rule_returns_2(self, tmp_db, capsys):
        exit_code = main(
            [
                '--mode',
                'shadow',
                '--db',
                tmp_db,
                '--game-id',
                'g',
            ]
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert '--disable-rule' in captured.err

    def test_malformed_disable_rule_returns_2(self, tmp_db, capsys):
        exit_code = main(
            [
                '--mode',
                'shadow',
                '--db',
                tmp_db,
                '--game-id',
                'g',
                '--disable-rule',
                'not_dotted',
            ]
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert 'layer.rule_id' in captured.err


class TestAblation:
    """Mode 4 is implemented as of Step 5c."""

    def test_compares_baseline_vs_ablation(self, tmp_db):
        """Build paired runs: baseline has bluff_catch firing, ablation
        has it disabled. Confirm the analysis identifies the ablated
        rule and reports an action-change rate."""
        conn = sqlite3.connect(tmp_db)
        try:
            _insert_game(conn, 'base')
            _insert_game(conn, 'abl')

            # Hand 1 decision 1: identical, both rules emit no-op.
            common_trace = [
                {
                    'layer': 'personality',
                    'rule_id': 'default',
                    'fired': True,
                    'operation': 'adjust',
                    'primary_action_after': 'fold',
                    'reason_code': 'deviation_profile_tag',
                },
                {
                    'layer': 'bluff_catch_override',
                    'rule_id': 'default',
                    'fired': False,
                    'operation': 'no_op',
                    'reason_code': 'hand_class_not_eligible',
                },
            ]
            _insert_decision(
                conn,
                game_id='base',
                hand_number=1,
                phase='FLOP',
                action_taken='fold',
                trace_entries=common_trace,
            )

            ablation_trace_h1 = [
                common_trace[0],
                {
                    'layer': 'bluff_catch_override',
                    'rule_id': 'default',
                    'fired': False,
                    'operation': 'no_op',
                    'reason_code': 'disabled_by_ablation',
                },
            ]
            _insert_decision(
                conn,
                game_id='abl',
                hand_number=1,
                phase='FLOP',
                action_taken='fold',
                trace_entries=ablation_trace_h1,
            )

            # Hand 2 decision 1: baseline's bluff_catch fires, ablation's
            # was disabled — action diverges.
            _insert_decision(
                conn,
                game_id='base',
                hand_number=2,
                phase='TURN',
                action_taken='call',
                trace_entries=[
                    {
                        'layer': 'personality',
                        'rule_id': 'default',
                        'fired': True,
                        'operation': 'adjust',
                        'primary_action_after': 'call',
                    },
                    {
                        'layer': 'bluff_catch_override',
                        'rule_id': 'default',
                        'fired': True,
                        'operation': 'override',
                        'primary_action_after': 'call',
                        'reason_code': 'medium_made_vs_extreme_facing_bet',
                    },
                ],
            )
            _insert_decision(
                conn,
                game_id='abl',
                hand_number=2,
                phase='TURN',
                action_taken='fold',
                trace_entries=[
                    {
                        'layer': 'personality',
                        'rule_id': 'default',
                        'fired': True,
                        'operation': 'adjust',
                        'primary_action_after': 'fold',
                    },
                    {
                        'layer': 'bluff_catch_override',
                        'rule_id': 'default',
                        'fired': False,
                        'operation': 'no_op',
                        'reason_code': 'disabled_by_ablation',
                    },
                ],
            )
            conn.commit()
        finally:
            conn.close()

        from experiments.analyze_intervention_traces import ablation_compare

        repo = DecisionAnalysisRepository(tmp_db)
        report = ablation_compare(repo, 'base', 'abl')

        assert report['mode'] == 'ablation'
        # The ablated rule (bluff_catch_override.default) was detected
        # from the ablation run's traces.
        assert any(
            r['layer'] == 'bluff_catch_override' and r['rule_id'] == 'default'
            for r in report['ablated_rules']
        )
        # 2 hands shared, both have at least one paired decision.
        assert report['shared_hands'] == 2
        # Hand 2 diverges → 1 action-changed decision.
        assert report['action_changed_decisions'] == 1
        # Hand 1: no divergence (1 paired). Hand 2: divergence at idx 0
        # (1 paired decision including the divergence point). Total 2.
        assert report['paired_decisions'] == 2

    def test_no_disabled_traces_reports_empty_ablated_rules(self, tmp_db):
        """If the 'ablation' run has no `disabled_by_ablation` traces,
        the report should show an empty ablated_rules list (user
        misconfigured the sim)."""
        conn = sqlite3.connect(tmp_db)
        try:
            _insert_game(conn, 'a')
            _insert_game(conn, 'b')
            trace = [
                {
                    'layer': 'personality',
                    'fired': True,
                    'operation': 'adjust',
                    'reason_code': 'deviation_profile_tag',
                }
            ]
            for gid in ('a', 'b'):
                _insert_decision(
                    conn,
                    game_id=gid,
                    hand_number=1,
                    phase='FLOP',
                    action_taken='fold',
                    trace_entries=trace,
                )
            conn.commit()
        finally:
            conn.close()

        from experiments.analyze_intervention_traces import ablation_compare

        repo = DecisionAnalysisRepository(tmp_db)
        report = ablation_compare(repo, 'a', 'b')
        assert report['ablated_rules'] == []
        assert report['action_changed_decisions'] == 0

    def test_cli_ablation_requires_both_game_ids(self, tmp_db, capsys):
        exit_code = main(
            [
                '--mode',
                'ablation',
                '--db',
                tmp_db,
                '--baseline-game',
                'a',
            ]
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert '--baseline-game' in captured.err
        assert '--ablation-game' in captured.err


# ── CLI smoke ───────────────────────────────────────────────────────────


class TestCliSmoke:
    def test_aggregate_text_output_via_main(self, tmp_db, capsys):
        conn = sqlite3.connect(tmp_db)
        try:
            _insert_game(conn, 'g1')
            _insert_decision(
                conn,
                game_id='g1',
                hand_number=1,
                phase='FLOP',
                action_taken='call',
                trace_entries=[
                    {
                        'layer': 'bluff_catch_override',
                        'rule_id': 'default',
                        'fired': True,
                        'operation': 'override',
                        'effect_size': 0.5,
                        'reason_code': 'extreme',
                    }
                ],
            )
            conn.commit()
        finally:
            conn.close()

        exit_code = main(
            [
                '--mode',
                'aggregate',
                '--db',
                tmp_db,
                '--game-id',
                'g1',
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert 'aggregate firing rates' in captured.out
        assert 'bluff_catch_override' in captured.out

    def test_aggregate_json_output(self, tmp_db, capsys):
        conn = sqlite3.connect(tmp_db)
        try:
            _insert_game(conn, 'g1')
            _insert_decision(
                conn,
                game_id='g1',
                hand_number=1,
                phase='FLOP',
                action_taken='call',
                trace_entries=[
                    {
                        'layer': 'personality',
                        'rule_id': 'default',
                        'fired': True,
                        'operation': 'adjust',
                        'effect_size': 0.2,
                        'reason_code': 'deviation_profile_tag',
                    }
                ],
            )
            conn.commit()
        finally:
            conn.close()

        exit_code = main(
            [
                '--mode',
                'aggregate',
                '--db',
                tmp_db,
                '--game-id',
                'g1',
                '--output',
                'json',
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        decoded = json.loads(captured.out)
        assert decoded['mode'] == 'aggregate'
        assert decoded['decisions_total'] == 1

    def test_first_divergence_requires_both_games(self, tmp_db, capsys):
        exit_code = main(
            [
                '--mode',
                'first-divergence',
                '--db',
                tmp_db,
                '--candidate-game',
                'a',
            ]
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert '--candidate-game' in captured.err

    def test_aggregate_requires_game_or_all(self, tmp_db, capsys):
        exit_code = main(['--mode', 'aggregate', '--db', tmp_db])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert '--game-id' in captured.err
