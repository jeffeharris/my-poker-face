"""Tests for CaptureLabelRepository."""
import pytest
from poker.repositories.prompt_capture_repository import PromptCaptureRepository
from poker.repositories.capture_label_repository import CaptureLabelRepository


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
def repo(db_path, prompt_capture_repo):
    r = CaptureLabelRepository(db_path, prompt_capture_repo=prompt_capture_repo)
    yield r
    r.close()


class TestCaptureLabels:
    @pytest.fixture
    def capture_id(self, prompt_capture_repo):
        return prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='fold'))

    def test_add_and_get_labels(self, repo, capture_id):
        added = repo.add_capture_labels(capture_id, ['interesting', 'mistake'])
        assert added == ['interesting', 'mistake']

        labels = repo.get_capture_labels(capture_id)
        assert len(labels) == 2
        label_names = [l['label'] for l in labels]
        assert 'interesting' in label_names
        assert 'mistake' in label_names

    def test_add_duplicate_labels(self, repo, capture_id):
        repo.add_capture_labels(capture_id, ['tag1'])
        added = repo.add_capture_labels(capture_id, ['tag1', 'tag2'])
        assert added == ['tag2']  # tag1 was already there

    def test_add_empty_labels(self, repo, capture_id):
        added = repo.add_capture_labels(capture_id, ['', '  '])
        assert added == []

    def test_remove_labels(self, repo, capture_id):
        repo.add_capture_labels(capture_id, ['a', 'b', 'c'])
        removed = repo.remove_capture_labels(capture_id, ['a', 'c'])
        assert removed == 2

        labels = repo.get_capture_labels(capture_id)
        assert len(labels) == 1
        assert labels[0]['label'] == 'b'

    def test_list_all_labels(self, repo, prompt_capture_repo, capture_id):
        cid2 = prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g2'))
        repo.add_capture_labels(capture_id, ['tag1', 'tag2'])
        repo.add_capture_labels(cid2, ['tag1'])

        all_labels = repo.list_all_labels()
        label_map = {l['name']: l['count'] for l in all_labels}
        assert label_map['tag1'] == 2
        assert label_map['tag2'] == 1

    def test_get_label_stats(self, repo, capture_id):
        repo.add_capture_labels(capture_id, ['interesting'])
        stats = repo.get_label_stats(game_id='g1')
        assert stats['interesting'] == 1

    def test_compute_and_store_auto_labels_short_stack_fold(self, repo, capture_id):
        labels = repo.compute_and_store_auto_labels(capture_id, {
            'action_taken': 'fold',
            'stack_bb': 2.0,
            'pot_odds': 3.0,
        })
        assert 'short_stack_fold' in labels

        stored = repo.get_capture_labels(capture_id)
        stored_names = [l['label'] for l in stored]
        assert 'short_stack_fold' in stored_names

    def test_compute_and_store_auto_labels_suspicious_fold(self, repo, capture_id):
        labels = repo.compute_and_store_auto_labels(capture_id, {
            'action_taken': 'fold',
            'stack_bb': 10.0,
            'pot_odds': 6.0,
        })
        assert 'suspicious_fold' in labels

    def test_compute_and_store_auto_labels_no_labels(self, repo, capture_id):
        labels = repo.compute_and_store_auto_labels(capture_id, {
            'action_taken': 'call',
            'stack_bb': 20.0,
        })
        assert labels == []

    def test_search_captures_with_labels(self, repo, prompt_capture_repo):
        cid1 = prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='fold'))
        cid2 = prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='call'))
        repo.add_capture_labels(cid1, ['mistake'])
        repo.add_capture_labels(cid2, ['good'])

        result = repo.search_captures_with_labels(['mistake'])
        assert result['total'] == 1
        assert result['captures'][0]['id'] == cid1

    def test_search_captures_with_labels_match_all(self, repo, prompt_capture_repo):
        cid1 = prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='fold'))
        cid2 = prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='call'))
        repo.add_capture_labels(cid1, ['a', 'b'])
        repo.add_capture_labels(cid2, ['a'])

        result = repo.search_captures_with_labels(['a', 'b'], match_all=True)
        assert result['total'] == 1
        assert result['captures'][0]['id'] == cid1

    def test_search_captures_empty_labels_falls_back(self, repo, prompt_capture_repo):
        prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g1'))
        result = repo.search_captures_with_labels([])
        assert result['total'] == 1

    def test_bulk_add_capture_labels(self, repo, prompt_capture_repo):
        cid1 = prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='fold'))
        cid2 = prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='call'))

        result = repo.bulk_add_capture_labels([cid1, cid2], ['tag1', 'tag2'])
        assert result['captures_affected'] == 2
        assert result['labels_added'] == 4

    def test_bulk_remove_capture_labels(self, repo, prompt_capture_repo):
        cid1 = prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='fold'))
        cid2 = prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='call'))
        repo.bulk_add_capture_labels([cid1, cid2], ['tag1', 'tag2'])

        result = repo.bulk_remove_capture_labels([cid1, cid2], ['tag1'])
        assert result['labels_removed'] == 2

    def test_bulk_operations_empty_input(self, repo):
        result = repo.bulk_add_capture_labels([], ['tag1'])
        assert result['labels_added'] == 0

        result = repo.bulk_remove_capture_labels([], ['tag1'])
        assert result['labels_removed'] == 0
