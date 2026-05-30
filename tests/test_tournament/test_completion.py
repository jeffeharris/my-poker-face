"""Unified completion adapter: a TournamentSession -> the tracker-shaped result
dict that tournament_repo + the tournament_complete event consume (step 3)."""

import pytest

from flask_app.handlers.tournament_completion import build_completion_result, finalize_tournament
from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.session import TournamentSession


def _completed_session(field_size=6, table_size=6, seed=3):
    cfg = TournamentConfig(
        field_size=field_size, table_size=table_size, starting_stack=5000, seed=seed
    )
    s = TournamentSession(cfg, ai_resolver=FakeHandResolver())
    s.play_out()
    assert s.is_complete()
    return s


def test_result_shape_matches_tracker_contract():
    s = _completed_session()
    r = build_completion_result(s, game_id='g1', biggest_pot=4242, started_at='2026-05-30T00:00:00')

    # Keys the repo + tournament_complete event read.
    assert r['game_id'] == 'g1'
    assert r['winner_name'] == s.winner()
    assert r['biggest_pot'] == 4242
    assert r['starting_player_count'] == 6
    assert r['total_hands'] >= 1
    assert r['human_player_name'] == s.human_id
    assert r['started_at'] == '2026-05-30T00:00:00'


def test_standings_are_complete_and_ordered():
    s = _completed_session()
    r = build_completion_result(s, game_id='g1')
    standings = r['standings']

    # One row per entrant, ordered 1..N with no gaps.
    assert len(standings) == 6
    positions = [row['finishing_position'] for row in standings]
    assert positions == [1, 2, 3, 4, 5, 6]

    # Winner row: position 1, no eliminator.
    assert standings[0]['player_name'] == s.winner()
    assert standings[0]['finishing_position'] == 1
    assert standings[0]['eliminated_by'] is None

    # Exactly one human row, flagged.
    human_rows = [row for row in standings if row['is_human']]
    assert len(human_rows) == 1
    assert human_rows[0]['player_name'] == s.human_id


def test_human_finishing_position_matches_session_rank():
    s = _completed_session()
    r = build_completion_result(s, game_id='g1')
    assert r['human_finishing_position'] == s.human_rank()


def test_eliminated_by_attribution_present():
    s = _completed_session()
    r = build_completion_result(s, game_id='g1')
    # Every non-winner row carries the eliminator the field attributed.
    elim_by = {e.player_id: e.eliminator for e in s.field.eliminations}
    for row in r['standings']:
        if row['finishing_position'] == 1:
            continue
        assert row['eliminated_by'] == elim_by[row['player_name']]


class _FakeRepo:
    def __init__(self):
        self.saved = []
        self.career = []

    def save_tournament_result(self, game_id, result):
        self.saved.append((game_id, result))

    def update_career_stats(self, owner_id, player_name, result):
        self.career.append((owner_id, player_name, result))


@pytest.fixture
def finalize_env(monkeypatch):
    import flask_app.extensions as ext
    from flask_app.services import game_state_service

    repo = _FakeRepo()
    monkeypatch.setattr(ext, 'tournament_repo', repo, raising=False)
    monkeypatch.setattr(ext, 'socketio', None, raising=False)
    monkeypatch.setattr(
        game_state_service, 'get_game_owner_info', lambda gid: ('owner-x', 'Owner'), raising=False
    )
    return repo


def test_finalize_persists_result_and_career_stats(finalize_env):
    s = _completed_session()
    game_data = {'tournament_session': s, 'tournament_biggest_pot': 9000}

    did = finalize_tournament('g1', game_data, emit=False)

    assert did is True
    assert game_data['tournament_finalized'] is True
    assert len(finalize_env.saved) == 1
    gid, result = finalize_env.saved[0]
    assert gid == 'g1' and result['owner_id'] == 'owner-x' and result['biggest_pot'] == 9000
    assert len(finalize_env.career) == 1
    owner_id, player_name, _ = finalize_env.career[0]
    assert owner_id == 'owner-x' and player_name == s.human_id


def test_finalize_is_idempotent(finalize_env):
    s = _completed_session()
    game_data = {'tournament_session': s}
    assert finalize_tournament('g1', game_data, emit=False) is True
    assert finalize_tournament('g1', game_data, emit=False) is False  # guarded
    assert len(finalize_env.saved) == 1


def test_finalize_noop_when_human_still_in_and_incomplete(finalize_env):
    cfg = TournamentConfig(field_size=6, table_size=6, starting_stack=5000, seed=3)
    s = TournamentSession(cfg, ai_resolver=FakeHandResolver())  # not played out
    assert finalize_tournament('g1', {'tournament_session': s}, emit=False) is False
    assert finalize_env.saved == []


def test_finalize_fires_when_human_busts_before_field_completes(finalize_env):
    """A multi-table human who busts mid-field (HUMAN_OUT, not yet complete) must
    still get career stats recorded — the analog of the single-table tracker
    saving on human elimination."""
    cfg = TournamentConfig(field_size=6, table_size=3, starting_stack=5000, seed=3)
    s = TournamentSession(cfg, ai_resolver=FakeHandResolver())
    # Bust just the human; 5 players remain -> human_out but not complete.
    s.field.record_eliminations(
        [(s.human_id, s.field.stacks[s.human_id])], round_index=0, eliminators={}
    )
    assert s.human_out and not s.is_complete()

    did = finalize_tournament('g1', {'tournament_session': s}, emit=False)

    assert did is True
    assert len(finalize_env.career) == 1
    _, player_name, result = finalize_env.career[0]
    assert player_name == s.human_id
    assert result['winner_name'] is None  # field not finished yet
    assert result['human_finishing_position'] == s.human_rank()


def test_winner_is_human_path():
    # Seed where P01 (the default human) wins, to exercise the is_human winner row.
    s = None
    for seed in range(1, 40):
        cand = _completed_session(seed=seed)
        if cand.winner() == cand.human_id:
            s = cand
            break
    assert s is not None, "no seed produced a human winner in range"
    r = build_completion_result(s, game_id='g1')
    assert r['human_finishing_position'] == 1
    assert r['standings'][0]['is_human'] is True
