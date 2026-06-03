"""single_table_hand_boundary: the per-hand glue that folds a finished hand into
the session field, feeds per-elimination beats, and ends the game at the human's
terminal moment (step 3B). Deterministic — stubs sockets/repos."""

import pytest

from tournament.session import TournamentSession


class _P:
    def __init__(self, name, stack, is_human=False):
        self.name, self.stack, self.is_human = name, stack, is_human


class _GS:
    def __init__(self, players):
        self.players = players


class _FakeRepo:
    def __init__(self):
        self.saved, self.career = [], []

    def save_tournament_result(self, gid, result):
        self.saved.append((gid, result))

    def update_career_stats(self, owner_id, name, result):
        self.career.append((owner_id, name, result))


@pytest.fixture
def env(monkeypatch):
    import flask_app.extensions as ext
    import flask_app.handlers.game_handler as gh
    from flask_app.services import game_state_service

    repo = _FakeRepo()
    monkeypatch.setattr(ext, 'tournament_repo', repo, raising=False)
    monkeypatch.setattr(ext, 'socketio', None, raising=False)
    monkeypatch.setattr(ext, 'tournament_session_repo', None, raising=False)
    monkeypatch.setattr(gh, 'send_message', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(
        game_state_service, 'get_game_owner_info', lambda gid: ('owner-x', 'Owner'), raising=False
    )
    return repo


def _session():
    entries = {'Jeff': 'human', 'Gordon': 'TAG', 'Bob': 'LAG'}
    return TournamentSession.for_single_table(
        entries=entries, human_id='Jeff', starting_stack=10000, seed=1
    )


def _boundary(game_data, players, winners):
    from flask_app.handlers.single_table_tournament import single_table_hand_boundary

    return single_table_hand_boundary('g1', game_data, _GS(players), winners, None)


def test_ai_busts_human_survives_is_not_terminal(env):
    gd = {'tournament_session': _session(), 'owner_id': 'owner-x'}
    stop = _boundary(
        gd,
        [_P('Jeff', 10000, True), _P('Gordon', 20000), _P('Bob', 0)],
        ['Gordon'],
    )
    assert stop is False
    s = gd['tournament_session']
    assert [e.player_id for e in s.field.eliminations] == ['Bob']
    assert env.saved == [] and env.career == []  # not finalized yet


def test_human_bust_is_terminal_and_records_stats(env):
    gd = {'tournament_session': _session(), 'owner_id': 'owner-x', 'tournament_biggest_pot': 5000}
    # Jeff busts 3rd while two remain.
    stop = _boundary(
        gd,
        [_P('Jeff', 0, True), _P('Gordon', 12000), _P('Bob', 18000)],
        ['Bob'],
    )
    assert stop is True
    assert len(env.career) == 1
    _, name, result = env.career[0]
    assert name == 'Jeff'
    assert result['human_finishing_position'] == 3


def test_human_wins_heads_up_is_terminal(env):
    s = _session()
    # First Bob busts (Jeff survives), then Jeff wins heads-up.
    gd = {'tournament_session': s, 'owner_id': 'owner-x'}
    assert (
        _boundary(gd, [_P('Jeff', 15000, True), _P('Gordon', 15000), _P('Bob', 0)], ['Jeff'])
        is False
    )
    stop = _boundary(gd, [_P('Jeff', 30000, True), _P('Gordon', 0)], ['Jeff'])
    assert stop is True
    assert s.winner() == 'Jeff'
    assert len(env.career) == 1
    assert env.career[0][2]['human_finishing_position'] == 1
