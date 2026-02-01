"""Tests for HandHistoryRepository."""
import json
import pytest
from poker.repositories.hand_history_repository import HandHistoryRepository


@pytest.fixture
def repo(db_path):
    r = HandHistoryRepository(db_path)
    yield r
    r.close()


def _make_hand(game_id, hand_number, players=None, winners=None, actions=None,
               pot_size=100, was_showdown=False):
    """Helper to build a hand dict."""
    return {
        'game_id': game_id,
        'hand_number': hand_number,
        'players': players or [{'name': 'Alice'}, {'name': 'Bob'}],
        'hole_cards': {},
        'community_cards': [],
        'actions': actions or [],
        'winners': winners or [],
        'pot_size': pot_size,
        'was_showdown': was_showdown,
    }


class TestSaveAndLoadHandHistory:
    def test_save_and_load_round_trip(self, repo):
        hand = _make_hand('game1', 1)
        hand_id = repo.save_hand_history(hand)
        assert hand_id > 0

        loaded = repo.load_hand_history('game1')
        assert len(loaded) == 1
        assert loaded[0]['hand_number'] == 1
        assert loaded[0]['game_id'] == 'game1'

    def test_load_returns_chronological_order(self, repo):
        repo.save_hand_history(_make_hand('g1', 3))
        repo.save_hand_history(_make_hand('g1', 1))
        repo.save_hand_history(_make_hand('g1', 2))

        loaded = repo.load_hand_history('g1')
        numbers = [h['hand_number'] for h in loaded]
        assert numbers == [1, 2, 3]

    def test_load_with_limit(self, repo):
        for i in range(5):
            repo.save_hand_history(_make_hand('g1', i + 1))

        loaded = repo.load_hand_history('g1', limit=2)
        assert len(loaded) == 2
        # Should get most recent 2 (4, 5) in chronological order
        assert loaded[0]['hand_number'] == 4
        assert loaded[1]['hand_number'] == 5

    def test_load_empty(self, repo):
        assert repo.load_hand_history('nonexistent') == []


class TestHandCount:
    def test_returns_zero_for_no_hands(self, repo):
        assert repo.get_hand_count('empty_game') == 0

    def test_returns_max_hand_number(self, repo):
        repo.save_hand_history(_make_hand('g1', 5))
        repo.save_hand_history(_make_hand('g1', 3))
        assert repo.get_hand_count('g1') == 5


class TestDeleteHandHistory:
    def test_delete_removes_all_hands(self, repo):
        repo.save_hand_history(_make_hand('g1', 1))
        repo.save_hand_history(_make_hand('g1', 2))
        repo.delete_hand_history_for_game('g1')
        assert repo.load_hand_history('g1') == []


class TestHandCommentary:
    def test_save_and_get_reflections(self, repo):
        commentary = {
            'emotional_reaction': 'Feeling good',
            'strategic_reflection': 'Should bluff more',
            'opponent_observations': ['Alice is tight'],
            'key_insight': 'Bluffing works',
            'decision_plans': ['Bet big next time'],
        }
        repo.save_hand_commentary('g1', 1, 'Bot', commentary)

        reflections = repo.get_recent_reflections('g1', 'Bot', limit=5)
        assert len(reflections) == 1
        assert reflections[0]['strategic_reflection'] == 'Should bluff more'
        assert reflections[0]['key_insight'] == 'Bluffing works'

    def test_reflections_ordered_by_hand_number_desc(self, repo):
        for i in range(3):
            repo.save_hand_commentary('g1', i + 1, 'Bot', {
                'strategic_reflection': f'Reflection {i + 1}',
            })

        reflections = repo.get_recent_reflections('g1', 'Bot', limit=10)
        assert len(reflections) == 3
        assert reflections[0]['hand_number'] == 3
        assert reflections[2]['hand_number'] == 1


class TestSessionStats:
    def test_empty_game_returns_defaults(self, repo):
        stats = repo.get_session_stats('empty', 'Alice')
        assert stats['hands_played'] == 0
        assert stats['hands_won'] == 0
        assert stats['current_streak'] == 'neutral'

    def test_counts_wins_and_losses(self, repo):
        # Hand 1: Alice wins
        repo.save_hand_history(_make_hand(
            'g1', 1,
            players=[{'name': 'Alice'}, {'name': 'Bob'}],
            winners=[{'name': 'Alice', 'amount_won': 200}],
            actions=[
                {'player_name': 'Alice', 'action': 'call', 'amount': 50},
                {'player_name': 'Bob', 'action': 'call', 'amount': 50},
            ],
            pot_size=200
        ))
        # Hand 2: Alice folds
        repo.save_hand_history(_make_hand(
            'g1', 2,
            players=[{'name': 'Alice'}, {'name': 'Bob'}],
            winners=[{'name': 'Bob', 'amount_won': 100}],
            actions=[
                {'player_name': 'Alice', 'action': 'fold', 'amount': 10},
            ],
            pot_size=100
        ))

        stats = repo.get_session_stats('g1', 'Alice')
        assert stats['hands_played'] == 2
        assert stats['hands_won'] == 1
        assert stats['biggest_pot_won'] == 200

    def test_winning_streak(self, repo):
        for i in range(3):
            repo.save_hand_history(_make_hand(
                'g1', i + 1,
                players=[{'name': 'Alice'}],
                winners=[{'name': 'Alice', 'amount_won': 100}],
                pot_size=100
            ))

        stats = repo.get_session_stats('g1', 'Alice')
        assert stats['current_streak'] == 'winning'
        assert stats['streak_count'] == 3


class TestSessionContextForPrompt:
    def test_empty_returns_empty_string(self, repo):
        assert repo.get_session_context_for_prompt('empty', 'Alice') == ""

    def test_returns_formatted_context(self, repo):
        repo.save_hand_history(_make_hand(
            'g1', 1,
            players=[{'name': 'Alice'}],
            winners=[{'name': 'Alice', 'amount_won': 200}],
            pot_size=200
        ))

        context = repo.get_session_context_for_prompt('g1', 'Alice')
        assert 'Session:' in context
        assert '1/1' in context
