"""Phase 7.6 Step 3b: trace persistence schema + serializer tests.

Covers:
  - `_serialize_intervention_trace` returns None for empty/None inputs
  - Serializer produces valid JSON that round-trips
  - Serializer degrades gracefully on serialization errors (returns None,
    does NOT raise — Codex r3 risk #12)
  - DecisionAnalysis dataclass exposes `intervention_trace_json` field
  - DB schema v81 adds the `intervention_trace_json` column
  - DecisionAnalysisRepository.get_intervention_trace round-trips
  - get_intervention_traces_for_game returns one entry per row with
    deserialized trace
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import replace

import pytest

from poker.controllers import _serialize_intervention_trace
from poker.decision_analyzer import DecisionAnalysis
from poker.repositories.decision_analysis_repository import DecisionAnalysisRepository
from poker.repositories.schema_manager import SchemaManager
from poker.strategy.intervention_trace import (
    InterventionOperation,
    InterventionTrace,
    make_no_op_trace,
)


# ── Serializer ────────────────────────────────────────────────────────────


class TestSerializeInterventionTrace:
    def test_none_returns_none(self):
        assert _serialize_intervention_trace(None, player_name='Hero') is None

    def test_empty_list_returns_none(self):
        assert _serialize_intervention_trace([], player_name='Hero') is None

    def test_single_trace_round_trips(self):
        traces = [make_no_op_trace(
            layer='bluff_catch_override', rule_id='default', layer_order=3,
            reason_code='hand_class_not_eligible',
        )]
        payload = _serialize_intervention_trace(traces, player_name='Hero')
        assert payload is not None
        decoded = json.loads(payload)
        assert isinstance(decoded, list)
        assert len(decoded) == 1
        assert decoded[0]['layer'] == 'bluff_catch_override'
        assert decoded[0]['fired'] is False

    def test_multiple_traces_round_trip(self):
        traces = [
            make_no_op_trace(
                layer='personality', rule_id='default', layer_order=0,
                reason_code='no_distortion',
            ),
            InterventionTrace(
                layer='bluff_catch_override', rule_id='default', layer_order=3,
                fired=True,
                operation=InterventionOperation.OVERRIDE.value,
                effect='distribution_replaced',
                replaced_prior_action=True,
            ),
        ]
        payload = _serialize_intervention_trace(traces, player_name='Hero')
        decoded = json.loads(payload)
        assert len(decoded) == 2
        assert decoded[0]['operation'] == 'no_op'
        assert decoded[1]['operation'] == 'override'

    def test_invalid_trace_object_degrades_to_none(self):
        """Serializer must not raise on bad inputs — gameplay continues."""
        # A non-trace object that can't be passed to trace_to_json_dict.
        bad_input = [object()]
        result = _serialize_intervention_trace(bad_input, player_name='Hero')
        assert result is None  # Degraded; warning logged but no raise.


# ── DecisionAnalysis dataclass field ─────────────────────────────────────


class TestDecisionAnalysisField:
    def test_intervention_trace_json_field_exists_and_defaults_none(self):
        analysis = DecisionAnalysis(game_id='g1', player_name='Hero')
        assert hasattr(analysis, 'intervention_trace_json')
        assert analysis.intervention_trace_json is None

    def test_to_dict_includes_intervention_trace_json(self):
        analysis = DecisionAnalysis(
            game_id='g1', player_name='Hero',
            intervention_trace_json='[]',
        )
        d = analysis.to_dict()
        assert 'intervention_trace_json' in d
        assert d['intervention_trace_json'] == '[]'


# ── Schema migration + repository round-trip ────────────────────────────


@pytest.fixture
def tmp_db():
    """Build a fresh DB with the v81 schema applied."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    SchemaManager(path).ensure_schema()
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestSchemaAndPersistence:
    def test_v81_column_exists_in_player_decision_analysis(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        try:
            cursor = conn.execute("PRAGMA table_info(player_decision_analysis)")
            columns = {row[1] for row in cursor.fetchall()}
            assert 'intervention_trace_json' in columns
        finally:
            conn.close()

    def test_schema_version_is_at_least_81(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        try:
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            version = cursor.fetchone()[0]
            assert version >= 81
        finally:
            conn.close()

    def test_save_with_trace_round_trips(self, tmp_db):
        # First insert a games row so the FK is satisfied.
        conn = sqlite3.connect(tmp_db)
        try:
            conn.execute(
                "INSERT INTO games "
                "(game_id, phase, num_players, pot_size, game_state_json, owner_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ('test_game', 'FLOP', 2, 0.0, '{}', 'test_owner'),
            )
            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        trace_payload = json.dumps([
            {'layer': 'bluff_catch_override', 'rule_id': 'default',
             'fired': True, 'operation': 'override'},
        ])
        analysis = DecisionAnalysis(
            game_id='test_game',
            player_name='Hero',
            hand_number=1,
            phase='FLOP',
            intervention_trace_json=trace_payload,
        )
        analysis_id = repo.save_decision_analysis(analysis)
        assert analysis_id > 0

        read_back = repo.get_intervention_trace(analysis_id)
        assert read_back is not None
        assert len(read_back) == 1
        assert read_back[0]['layer'] == 'bluff_catch_override'

    def test_save_without_trace_returns_none(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        try:
            conn.execute(
                "INSERT INTO games (game_id, phase, num_players, pot_size, game_state_json, owner_id) VALUES (?, ?, ?, ?, ?, ?)",
                ('g2', 'FLOP', 2, 0.0, '{}', 'test_owner'),
            )
            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        analysis = DecisionAnalysis(
            game_id='g2', player_name='Hero',
            intervention_trace_json=None,
        )
        analysis_id = repo.save_decision_analysis(analysis)
        assert repo.get_intervention_trace(analysis_id) is None

    def test_get_intervention_traces_for_game_returns_all(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        try:
            conn.execute(
                "INSERT INTO games (game_id, phase, num_players, pot_size, game_state_json, owner_id) VALUES (?, ?, ?, ?, ?, ?)",
                ('multi_game', 'FLOP', 2, 0.0, '{}', 'test_owner'),
            )
            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        for i in range(3):
            payload = json.dumps([{'layer': 'personality', 'fired': i > 0,
                                  'operation': 'adjust' if i > 0 else 'no_op'}])
            analysis = DecisionAnalysis(
                game_id='multi_game', player_name='Hero',
                hand_number=i, phase='FLOP',
                intervention_trace_json=payload,
            )
            repo.save_decision_analysis(analysis)

        # Save one row without a trace — should be excluded from the query.
        repo.save_decision_analysis(DecisionAnalysis(
            game_id='multi_game', player_name='Hero',
            hand_number=99, phase='FLOP',
            intervention_trace_json=None,
        ))

        rows = repo.get_intervention_traces_for_game('multi_game')
        assert len(rows) == 3  # No-trace row excluded
        for row in rows:
            assert isinstance(row['trace'], list)
            assert row['hand_number'] in (0, 1, 2)

    def test_get_intervention_traces_for_game_filters_by_hand(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        try:
            conn.execute(
                "INSERT INTO games (game_id, phase, num_players, pot_size, game_state_json, owner_id) VALUES (?, ?, ?, ?, ?, ?)",
                ('hand_filter', 'FLOP', 2, 0.0, '{}', 'test_owner'),
            )
            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        for h in (1, 1, 2):
            repo.save_decision_analysis(DecisionAnalysis(
                game_id='hand_filter', player_name='Hero',
                hand_number=h, phase='FLOP',
                intervention_trace_json=json.dumps([{'layer': 'personality'}]),
            ))

        only_hand_1 = repo.get_intervention_traces_for_game(
            'hand_filter', hand_number=1,
        )
        assert len(only_hand_1) == 2
        assert all(r['hand_number'] == 1 for r in only_hand_1)

    def test_malformed_json_skipped_gracefully(self, tmp_db):
        """A malformed JSON row shouldn't crash the analysis script."""
        conn = sqlite3.connect(tmp_db)
        try:
            conn.execute(
                "INSERT INTO games (game_id, phase, num_players, pot_size, game_state_json, owner_id) VALUES (?, ?, ?, ?, ?, ?)",
                ('bad_json', 'FLOP', 2, 0.0, '{}', 'test_owner'),
            )
            conn.commit()
            conn.execute(
                "INSERT INTO player_decision_analysis "
                "(game_id, player_name, intervention_trace_json) "
                "VALUES (?, ?, ?)",
                ('bad_json', 'Hero', 'not valid json {'),
            )
            conn.commit()
        finally:
            conn.close()

        repo = DecisionAnalysisRepository(tmp_db)
        rows = repo.get_intervention_traces_for_game('bad_json')
        # Malformed row silently skipped.
        assert rows == []
