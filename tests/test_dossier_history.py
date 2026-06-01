"""Tests for "the history" rivalry read (build_relationship_history) + the
repo aggregate it consumes (load_relationship_history)."""

import sqlite3

import pytest

from flask_app.services.dossier_history import (
    CLASH_EVENTS,
    build_relationship_history,
)
from poker.repositories.game_repository import GameRepository
from poker.repositories.schema_manager import SchemaManager


# ── Service: build_relationship_history (pure) ──────────────────────────────

def test_bad_blood_headline_and_split():
    hist = {
        'counts': {
            'cooler': 2, 'bad_beat': 1, 'hero_call': 1,
            'chat_trash_talk': 3, 'chat_props': 1,
        },
        'defining': {'event': 'cooler', 'impact_score': 0.92,
                     'narrative': 'kings into aces, all-in flop'},
    }
    out = build_relationship_history(hist)
    assert out['line'].lower().startswith('bad blood')
    # Clash and banter bucketed + ordered by count.
    clash = {c['event']: c['count'] for c in out['clash']}
    assert clash == {'cooler': 2, 'bad_beat': 1, 'hero_call': 1}
    assert out['clash'][0]['event'] == 'cooler'  # highest count first
    banter = {c['event']: c['count'] for c in out['banter']}
    assert banter == {'chat_trash_talk': 3, 'chat_props': 1}
    # Defining hand gets a pretty label.
    assert out['defining']['label'] == 'cooler'
    assert out['defining']['impact_score'] == 0.92


def test_single_scar_headline():
    out = build_relationship_history({'counts': {'bad_beat': 1}, 'defining': None})
    assert 'scar' in out['line'].lower()


def test_your_moments_headline():
    out = build_relationship_history({'counts': {'hero_call': 2}, 'defining': None})
    assert 'number' in out['line'].lower()


def test_neutral_history_headline():
    out = build_relationship_history({'counts': {'big_win': 1}, 'defining': None})
    assert 'nothing decisive' in out['line'].lower()


def test_empty_history_is_none():
    assert build_relationship_history(None) is None
    assert build_relationship_history({'counts': {}}) is None
    # Counts present but neither clash nor banter (unknown type only).
    assert build_relationship_history({'counts': {'mystery': 2}}) is None


# ── Repo: load_relationship_history ─────────────────────────────────────────

@pytest.fixture
def repo(db_path):
    SchemaManager(db_path).ensure_schema()
    r = GameRepository(db_path)
    yield r, db_path
    r.close()


def _seed(db_path, game_id, owner_id, owner_name, opponent, events):
    """events: list of (memory_type, impact_score, narrative)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO games (game_id, phase, num_players, "
            "pot_size, game_state_json, owner_id, owner_name) "
            "VALUES (?, 'PRE_FLOP', 2, 0, '{}', ?, ?)",
            (game_id, owner_id, owner_name),
        )
        for i, (mt, impact, narr) in enumerate(events):
            conn.execute(
                "INSERT INTO memorable_hands (observer_name, opponent_name, "
                "hand_id, game_id, memory_type, impact_score, narrative) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (owner_name, opponent, i, game_id, mt, impact, narr),
            )
        conn.commit()
    finally:
        conn.close()


def test_load_relationship_history_counts_and_defining(repo):
    r, db_path = repo
    _seed(db_path, "g1", "jeff", "Jeff", "Greg", [
        ('cooler', 0.9, 'kings into aces'),
        ('cooler', 0.6, 'sets cracked'),
        ('bad_beat', 0.95, 'rivered two-outer'),
        ('chat_trash_talk', 0.1, ''),
    ])

    hist = r.load_relationship_history("jeff", "Greg", CLASH_EVENTS)
    assert hist['counts'] == {'cooler': 2, 'bad_beat': 1, 'chat_trash_talk': 1}
    # Defining = highest-impact CLASH hand (bad_beat 0.95 > cooler 0.9).
    assert hist['defining']['event'] == 'bad_beat'
    assert hist['defining']['impact_score'] == 0.95


def test_load_relationship_history_scoped_to_owner(repo):
    r, db_path = repo
    _seed(db_path, "g1", "jeff", "Jeff", "Greg", [('cooler', 0.9, '')])
    _seed(db_path, "g2", "alice", "Alice", "Greg", [('bad_beat', 0.9, '')])
    # Only Jeff's history vs Greg.
    hist = r.load_relationship_history("jeff", "Greg", CLASH_EVENTS)
    assert hist['counts'] == {'cooler': 1}


def test_load_relationship_history_empty(repo):
    r, _ = repo
    hist = r.load_relationship_history("jeff", "Nobody", CLASH_EVENTS)
    assert hist['counts'] == {}
    assert hist['defining'] is None
