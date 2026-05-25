"""Tests for scripts/polarization_report.py.

Focused unit tests on the pure helpers; the script's stdout-printing
main() isn't exhaustively tested, just smoke-tested via subprocess
with a fixture DB.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "polarization_report.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("polarization_report", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


@pytest.fixture
def seeded_db(tmp_path):
    """Build a minimal SQLite database with opponent_models rows that
    have tendencies_json blobs carrying equity-at-action data."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE opponent_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT,
            observer_name TEXT NOT NULL,
            opponent_name TEXT NOT NULL,
            tendencies_json TEXT
        )
        """
    )

    # CaseBot-style polarized opponent — high raise equity, low call equity
    polarized = {
        'hands_observed': 50,
        'equity_when_raising_postflop': 0.80,
        'equity_when_calling_postflop': 0.32,
        '_equity_raising_count': 12,
        '_equity_calling_count': 30,
        '_equity_betting_count': 8,
    }
    # LAG-style noisy opponent — raise and call have similar equity
    noisy = {
        'hands_observed': 50,
        'equity_when_raising_postflop': 0.48,
        'equity_when_calling_postflop': 0.45,
        '_equity_raising_count': 18,
        '_equity_calling_count': 22,
        '_equity_betting_count': 10,
    }
    # Bluffer — raises with weak hands, calls with strong
    bluffer = {
        'hands_observed': 40,
        'equity_when_raising_postflop': 0.30,
        'equity_when_calling_postflop': 0.55,
        '_equity_raising_count': 14,
        '_equity_calling_count': 15,
        '_equity_betting_count': 6,
    }
    # Insufficient sample
    thin = {
        'hands_observed': 5,
        'equity_when_raising_postflop': 0.80,
        'equity_when_calling_postflop': 0.30,
        '_equity_raising_count': 2,
        '_equity_calling_count': 1,
        '_equity_betting_count': 0,
    }

    conn.executemany(
        "INSERT INTO opponent_models (game_id, observer_name, opponent_name, "
        "tendencies_json) VALUES (?, ?, ?, ?)",
        [
            ("game_a", "TAG", "CaseBot", json.dumps(polarized)),
            ("game_a", "TAG", "LAG_Bot", json.dumps(noisy)),
            ("game_a", "TAG", "BluffMaster", json.dumps(bluffer)),
            ("game_a", "TAG", "FreshGuest", json.dumps(thin)),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


class TestLoadPairRows:
    def test_loads_all_rows(self, script, seeded_db):
        rows = script._load_pair_rows(seeded_db)
        assert len(rows) == 4

    def test_filter_by_game_id(self, script, seeded_db):
        rows = script._load_pair_rows(seeded_db, game_id="game_a")
        assert len(rows) == 4
        rows = script._load_pair_rows(seeded_db, game_id="game_nonexistent")
        assert len(rows) == 0

    def test_returned_row_shape(self, script, seeded_db):
        rows = script._load_pair_rows(seeded_db)
        casebot_row = next(r for r in rows if r['opponent'] == 'CaseBot')
        assert casebot_row['observer'] == 'TAG'
        assert casebot_row['eq_raise_mean'] == pytest.approx(0.80)
        assert casebot_row['eq_call_mean'] == pytest.approx(0.32)
        assert casebot_row['n_raise'] == 12
        assert casebot_row['n_call'] == 30


class TestPolarizationLabel:
    def test_polarized_label(self, script):
        row = {'eq_raise_mean': 0.80, 'eq_call_mean': 0.32}
        assert script._polarization(row) == pytest.approx(0.48)
        label = script._label(script._polarization(row), has_min_sample=True)
        assert 'POLARIZED' in label

    def test_bluffer_label(self, script):
        row = {'eq_raise_mean': 0.30, 'eq_call_mean': 0.55}
        label = script._label(script._polarization(row), has_min_sample=True)
        assert 'BLUFFER' in label

    def test_balanced_label(self, script):
        row = {'eq_raise_mean': 0.48, 'eq_call_mean': 0.45}
        label = script._label(script._polarization(row), has_min_sample=True)
        assert 'noisy' in label or 'balanced' in label

    def test_insufficient_sample_label(self, script):
        row = {'eq_raise_mean': 0.80, 'eq_call_mean': 0.30}
        label = script._label(script._polarization(row), has_min_sample=False)
        assert 'insufficient' in label
