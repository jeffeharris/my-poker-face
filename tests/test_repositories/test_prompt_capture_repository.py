"""Tests for PromptCaptureRepository."""
import pytest
from poker.repositories.prompt_capture_repository import PromptCaptureRepository


def _make_capture(**overrides):
    """Create a valid capture dict with all required NOT NULL fields."""
    base = {
        'game_id': 'game-1',
        'player_name': 'TestPlayer',
        'hand_number': 1,
        'phase': 'PRE_FLOP',
        'action_taken': 'call',
        'system_prompt': 'You are a poker player.',
        'user_message': 'What do you do?',
        'ai_response': '{"action": "call"}',
    }
    base.update(overrides)
    return base


@pytest.fixture
def repo(db_path):
    r = PromptCaptureRepository(db_path)
    yield r
    r.close()


class TestPromptCaptures:
    def test_save_and_get_prompt_capture(self, repo):
        capture = _make_capture(
            game_id='game-1',
            player_name='Batman',
            pot_total=100.0,
            cost_to_call=20.0,
            pot_odds=5.0,
            player_stack=500.0,
            model='gpt-4',
            provider='openai',
            system_prompt='You are Batman.',
            latency_ms=150,
            input_tokens=100,
            output_tokens=20,
        )
        capture_id = repo.save_prompt_capture(capture)
        assert capture_id is not None
        assert capture_id > 0

        loaded = repo.get_prompt_capture(capture_id)
        assert loaded is not None
        assert loaded['game_id'] == 'game-1'
        assert loaded['player_name'] == 'Batman'
        assert loaded['action_taken'] == 'call'
        assert loaded['model'] == 'gpt-4'

    def test_get_prompt_capture_not_found(self, repo):
        assert repo.get_prompt_capture(9999) is None

    def test_list_prompt_captures(self, repo):
        for i in range(3):
            repo.save_prompt_capture(_make_capture(
                game_id='game-1',
                player_name='Batman',
                hand_number=i,
                action_taken='call' if i < 2 else 'fold',
            ))

        result = repo.list_prompt_captures(game_id='game-1')
        assert result['total'] == 3
        assert len(result['captures']) == 3

        result = repo.list_prompt_captures(action='fold')
        assert result['total'] == 1

    def test_list_prompt_captures_pagination(self, repo):
        for i in range(5):
            repo.save_prompt_capture(_make_capture(hand_number=i))

        result = repo.list_prompt_captures(limit=2, offset=0)
        assert result['total'] == 5
        assert len(result['captures']) == 2

    def test_get_prompt_capture_stats(self, repo):
        for action in ['call', 'call', 'fold', 'raise']:
            repo.save_prompt_capture(_make_capture(
                action_taken=action,
                pot_odds=6.0 if action == 'fold' else 2.0,
            ))

        stats = repo.get_prompt_capture_stats(game_id='game-1')
        assert stats['total'] == 4
        assert stats['by_action']['call'] == 2
        assert stats['by_action']['fold'] == 1
        assert stats['suspicious_folds'] == 1

    def test_update_prompt_capture_tags(self, repo):
        cid = repo.save_prompt_capture(_make_capture(game_id='g1'))
        repo.update_prompt_capture_tags(cid, ['interesting', 'mistake'], notes='review this')

        loaded = repo.get_prompt_capture(cid)
        assert loaded['tags'] == ['interesting', 'mistake']
        assert loaded['notes'] == 'review this'

    def test_delete_prompt_captures(self, repo):
        repo.save_prompt_capture(_make_capture(game_id='g1'))
        repo.save_prompt_capture(_make_capture(game_id='g2'))

        deleted = repo.delete_prompt_captures(game_id='g1')
        assert deleted == 1

        result = repo.list_prompt_captures()
        assert result['total'] == 1

    def test_cleanup_old_captures_zero_retention(self, repo):
        assert repo.cleanup_old_captures(0) == 0

    def test_list_playground_captures(self, repo):
        repo.save_prompt_capture(_make_capture(
            game_id='g1', provider='openai', model='gpt-4',
        ))
        result = repo.list_playground_captures()
        assert result['total'] == 1

    def test_get_playground_capture_stats(self, repo):
        repo.save_prompt_capture(_make_capture(game_id='g1', provider='openai'))
        stats = repo.get_playground_capture_stats()
        assert stats['total'] == 1
