"""Tests for ReplayExperimentRepository."""
import pytest
from poker.repositories.prompt_capture_repository import PromptCaptureRepository
from poker.repositories.replay_experiment_repository import ReplayExperimentRepository


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
def prompt_capture_repo(db_path):
    r = PromptCaptureRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def repo(db_path):
    r = ReplayExperimentRepository(db_path)
    yield r
    r.close()


class TestReplayExperiments:
    @pytest.fixture
    def capture_ids(self, prompt_capture_repo):
        """Create some captures and return their IDs."""
        ids = []
        for i in range(3):
            cid = prompt_capture_repo.save_prompt_capture(_make_capture(
                game_id='game-1',
                player_name='Batman',
                hand_number=i,
                action_taken='call',
            ))
            ids.append(cid)
        return ids

    def test_create_replay_experiment(self, repo, capture_ids):
        variants = [
            {'label': 'gpt-4', 'model': 'gpt-4'},
            {'label': 'gpt-3.5', 'model': 'gpt-3.5-turbo'},
        ]
        exp_id = repo.create_replay_experiment(
            name='replay-test',
            capture_ids=capture_ids,
            variants=variants,
            description='Test replay',
            hypothesis='GPT-4 is better',
        )
        assert exp_id > 0

        exp = repo.get_replay_experiment(exp_id)
        assert exp is not None
        assert exp['capture_count'] == 3
        assert exp['variant_count'] == 2
        assert exp['results_total'] == 6  # 3 captures * 2 variants

    def test_get_replay_experiment_not_found(self, repo):
        assert repo.get_replay_experiment(9999) is None

    def test_add_replay_result(self, repo, capture_ids):
        variants = [{'label': 'v1', 'model': 'gpt-4'}]
        exp_id = repo.create_replay_experiment(
            name='result-test', capture_ids=capture_ids, variants=variants,
        )

        result_id = repo.add_replay_result(
            experiment_id=exp_id,
            capture_id=capture_ids[0],
            variant='v1',
            new_response='{"action": "fold"}',
            new_action='fold',
            provider='openai',
            model='gpt-4',
            latency_ms=200,
        )
        assert result_id > 0

    def test_get_replay_results(self, repo, capture_ids):
        variants = [{'label': 'v1', 'model': 'gpt-4'}]
        exp_id = repo.create_replay_experiment(
            name='results-test', capture_ids=capture_ids, variants=variants,
        )

        repo.add_replay_result(
            experiment_id=exp_id, capture_id=capture_ids[0], variant='v1',
            new_response='{"action": "fold"}', new_action='fold',
        )

        results = repo.get_replay_results(exp_id)
        assert results['total'] == 1
        assert len(results['results']) == 1

    def test_get_replay_results_summary(self, repo, capture_ids):
        variants = [{'label': 'v1', 'model': 'gpt-4'}]
        exp_id = repo.create_replay_experiment(
            name='summary-test', capture_ids=capture_ids, variants=variants,
        )

        repo.add_replay_result(
            experiment_id=exp_id, capture_id=capture_ids[0], variant='v1',
            new_response='{"action": "fold"}', new_action='fold',
        )

        summary = repo.get_replay_results_summary(exp_id)
        assert summary['overall']['total_results'] == 1
        assert 'v1' in summary['by_variant']

    def test_get_replay_experiment_captures(self, repo, capture_ids):
        variants = [{'label': 'v1', 'model': 'gpt-4'}]
        exp_id = repo.create_replay_experiment(
            name='captures-test', capture_ids=capture_ids, variants=variants,
        )

        captures = repo.get_replay_experiment_captures(exp_id)
        assert len(captures) == 3

    def test_list_replay_experiments(self, repo, capture_ids):
        variants = [{'label': 'v1', 'model': 'gpt-4'}]
        repo.create_replay_experiment(
            name='list-test', capture_ids=capture_ids, variants=variants,
        )

        result = repo.list_replay_experiments()
        assert result['total'] >= 1
        names = [e['name'] for e in result['experiments']]
        assert 'list-test' in names
