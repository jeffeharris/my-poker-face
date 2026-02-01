"""Tests for TournamentRepository."""
import pytest
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_repository import TournamentRepository


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    SchemaManager(db_path).ensure_schema()
    r = TournamentRepository(db_path)
    yield r
    r.close()


def _make_result(game_id='game1', owner_id='owner1'):
    """Helper to build a tournament result dict."""
    return {
        'winner_name': 'Alice',
        'total_hands': 50,
        'biggest_pot': 500,
        'starting_player_count': 4,
        'human_player_name': 'Alice',
        'human_finishing_position': 1,
        'started_at': '2025-01-01T00:00:00',
        'owner_id': owner_id,
        'standings': [
            {
                'player_name': 'Alice',
                'is_human': True,
                'finishing_position': 1,
                'eliminated_by': None,
                'eliminated_at_hand': None,
                'final_stack': 4000,
                'hands_won': 20,
                'hands_played': 50,
                'times_eliminated': 0,
                'all_in_wins': 2,
                'all_in_losses': 0,
            },
            {
                'player_name': 'Bot1',
                'is_human': False,
                'finishing_position': 2,
                'eliminated_by': 'Alice',
                'eliminated_at_hand': 45,
                'final_stack': 0,
                'hands_won': 10,
                'hands_played': 45,
                'times_eliminated': 1,
                'all_in_wins': 1,
                'all_in_losses': 1,
            },
        ],
    }


class TestSaveAndGetTournamentResult:
    def test_save_and_get_round_trip(self, repo):
        result = _make_result()
        repo.save_tournament_result('game1', result)

        loaded = repo.get_tournament_result('game1')
        assert loaded is not None
        assert loaded['winner_name'] == 'Alice'
        assert loaded['total_hands'] == 50
        assert loaded['biggest_pot'] == 500
        assert len(loaded['standings']) == 2
        assert loaded['standings'][0]['player_name'] == 'Alice'
        assert loaded['standings'][0]['is_human'] is True

    def test_get_nonexistent_returns_none(self, repo):
        assert repo.get_tournament_result('nonexistent') is None

    def test_standings_ordered_by_position(self, repo):
        repo.save_tournament_result('game1', _make_result())
        loaded = repo.get_tournament_result('game1')
        positions = [s['finishing_position'] for s in loaded['standings']]
        assert positions == sorted(positions)


class TestCareerStats:
    def test_new_player_career_stats(self, repo):
        result = _make_result()
        repo.save_tournament_result('game1', result)
        repo.update_career_stats('owner1', 'Alice', result)

        stats = repo.get_career_stats('owner1')
        assert stats is not None
        assert stats['games_played'] == 1
        assert stats['games_won'] == 1
        assert stats['best_finish'] == 1
        assert stats['worst_finish'] == 1
        assert stats['total_eliminations'] == 1  # Alice eliminated Bot1

    def test_update_existing_career_stats(self, repo):
        # First tournament: Alice wins (position 1)
        result1 = _make_result(game_id='game1')
        repo.save_tournament_result('game1', result1)
        repo.update_career_stats('owner1', 'Alice', result1)

        # Second tournament: Alice finishes 3rd
        result2 = _make_result(game_id='game2')
        result2['standings'][0]['finishing_position'] = 3
        result2['standings'][0]['eliminated_by'] = 'Bot1'
        repo.save_tournament_result('game2', result2)
        repo.update_career_stats('owner1', 'Alice', result2)

        stats = repo.get_career_stats('owner1')
        assert stats['games_played'] == 2
        assert stats['games_won'] == 1
        assert stats['best_finish'] == 1
        assert stats['worst_finish'] == 3

    def test_get_career_stats_nonexistent(self, repo):
        assert repo.get_career_stats('nonexistent') is None

    def test_player_not_in_standings_is_skipped(self, repo):
        result = _make_result()
        # Should not crash; just logs warning and returns
        repo.update_career_stats('owner1', 'NonexistentPlayer', result)
        assert repo.get_career_stats('owner1') is None


class TestTournamentHistory:
    def test_get_history(self, repo):
        repo.save_tournament_result('game1', _make_result(game_id='game1'))
        history = repo.get_tournament_history('owner1')
        assert len(history) == 1
        assert history[0]['game_id'] == 'game1'
        assert history[0]['winner_name'] == 'Alice'
        assert history[0]['your_position'] == 1

    def test_empty_history(self, repo):
        assert repo.get_tournament_history('nobody') == []


class TestEliminatedPersonalities:
    def test_get_eliminated(self, repo):
        repo.save_tournament_result('game1', _make_result())

        eliminated = repo.get_eliminated_personalities('owner1')
        assert len(eliminated) == 1
        assert eliminated[0]['name'] == 'Bot1'
        assert eliminated[0]['times_eliminated'] == 1

    def test_empty_when_no_eliminations(self, repo):
        assert repo.get_eliminated_personalities('nobody') == []
