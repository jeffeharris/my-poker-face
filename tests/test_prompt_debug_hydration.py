"""Unit tests for the prompt-debug route's JSON-column hydration helper.

These cover the contract that the React DecisionAnalyzer relies on:
intervention_trace and strategy_pipeline_snapshot must be parsed
objects (or None), never raw JSON strings.
"""

import json

import pytest

from flask_app.decision_analysis_serializer import hydrate_decision_analysis


def test_returns_none_when_row_is_none():
    assert hydrate_decision_analysis(None) is None


def test_parses_both_json_columns():
    row = {
        'id': 1,
        'intervention_trace_json': json.dumps(
            [
                {'layer': 'exploitation', 'rule_id': 'hyper_aggressive', 'effect_size': 0.4},
            ]
        ),
        'strategy_pipeline_snapshot_json': json.dumps(
            {
                'baseline_strategy': {'fold': 0.2, 'call': 0.5, 'raise': 0.3},
            }
        ),
    }
    hydrated = hydrate_decision_analysis(row)

    assert hydrated['intervention_trace'] == [
        {'layer': 'exploitation', 'rule_id': 'hyper_aggressive', 'effect_size': 0.4},
    ]
    assert hydrated['strategy_pipeline_snapshot'] == {
        'baseline_strategy': {'fold': 0.2, 'call': 0.5, 'raise': 0.3},
    }
    # Raw _json columns are dropped to avoid double-payload.
    assert 'intervention_trace_json' not in hydrated
    assert 'strategy_pipeline_snapshot_json' not in hydrated


def test_null_columns_become_none_fields():
    row = {
        'id': 2,
        'intervention_trace_json': None,
        'strategy_pipeline_snapshot_json': None,
    }
    hydrated = hydrate_decision_analysis(row)

    assert hydrated['intervention_trace'] is None
    assert hydrated['strategy_pipeline_snapshot'] is None


def test_malformed_json_falls_back_to_none():
    # Garbage JSON must not crash the API — log and return None.
    row = {
        'id': 3,
        'intervention_trace_json': '{not valid json',
        'strategy_pipeline_snapshot_json': '[also not a dict]',
    }
    hydrated = hydrate_decision_analysis(row)

    assert hydrated['intervention_trace'] is None
    assert hydrated['strategy_pipeline_snapshot'] is None
    assert 'intervention_trace_json' not in hydrated
    assert 'strategy_pipeline_snapshot_json' not in hydrated


def test_wrong_shape_falls_back_to_none():
    # Valid JSON but not the expected container type.
    row = {
        'id': 4,
        'intervention_trace_json': json.dumps({'this': 'is a dict, not a list'}),
        'strategy_pipeline_snapshot_json': json.dumps(['this is a list, not a dict']),
    }
    hydrated = hydrate_decision_analysis(row)

    assert hydrated['intervention_trace'] is None
    assert hydrated['strategy_pipeline_snapshot'] is None


def test_preserves_other_row_fields():
    row = {
        'id': 5,
        'game_id': 'g1',
        'player_name': 'Batman',
        'phase': 'FLOP',
        'intervention_trace_json': None,
        'strategy_pipeline_snapshot_json': None,
    }
    hydrated = hydrate_decision_analysis(row)

    assert hydrated['id'] == 5
    assert hydrated['game_id'] == 'g1'
    assert hydrated['player_name'] == 'Batman'
    assert hydrated['phase'] == 'FLOP'
