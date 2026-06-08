"""Tests for CaptureLabelRepository (labels keyed on the decision spine)."""

import pytest

from poker.repositories.capture_label_repository import CaptureLabelRepository
from poker.repositories.decision_analysis_repository import DecisionAnalysisRepository
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
        'call_type': 'player_decision',
    }
    base.update(overrides)
    return base


def _make_decision(**overrides):
    """Minimal player_decision_analysis row dict."""
    base = {
        'game_id': 'g1',
        'player_name': 'TestPlayer',
        'hand_number': 1,
        'phase': 'PRE_FLOP',
        'action_taken': 'fold',
    }
    base.update(overrides)
    return base


@pytest.fixture
def prompt_capture_repo(db_path):
    r = PromptCaptureRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def decision_repo(db_path):
    r = DecisionAnalysisRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def repo(db_path, prompt_capture_repo):
    r = CaptureLabelRepository(db_path, prompt_capture_repo=prompt_capture_repo)
    yield r
    r.close()


class TestDecisionLabels:
    @pytest.fixture
    def decision_id(self, decision_repo):
        return decision_repo.save_decision_analysis(_make_decision())

    def test_add_and_get_labels(self, repo, decision_id):
        added = repo.add_labels(decision_id, ['interesting', 'mistake'])
        assert added == ['interesting', 'mistake']

        labels = repo.get_labels(decision_id)
        assert len(labels) == 2
        label_names = [l['label'] for l in labels]
        assert 'interesting' in label_names
        assert 'mistake' in label_names

    def test_add_duplicate_labels(self, repo, decision_id):
        repo.add_labels(decision_id, ['tag1'])
        added = repo.add_labels(decision_id, ['tag1', 'tag2'])
        assert added == ['tag2']  # tag1 was already there

    def test_add_empty_labels(self, repo, decision_id):
        added = repo.add_labels(decision_id, ['', '  '])
        assert added == []

    def test_remove_labels(self, repo, decision_id):
        repo.add_labels(decision_id, ['a', 'b', 'c'])
        removed = repo.remove_labels(decision_id, ['a', 'c'])
        assert removed == 2

        labels = repo.get_labels(decision_id)
        assert len(labels) == 1
        assert labels[0]['label'] == 'b'

    def test_get_labels_for_decisions_batch(self, repo, decision_repo, decision_id):
        did2 = decision_repo.save_decision_analysis(_make_decision(action_taken='call'))
        repo.add_labels(decision_id, ['a', 'b'])
        repo.add_labels(did2, ['c'])

        batch = repo.get_labels_for_decisions([decision_id, did2])
        assert {l['label'] for l in batch[decision_id]} == {'a', 'b'}
        assert {l['label'] for l in batch[did2]} == {'c'}

    def test_list_all_labels(self, repo, decision_repo, decision_id):
        did2 = decision_repo.save_decision_analysis(_make_decision(game_id='g2'))
        repo.add_labels(decision_id, ['tag1', 'tag2'])
        repo.add_labels(did2, ['tag1'])

        all_labels = repo.list_all_labels()
        label_map = {l['name']: l['count'] for l in all_labels}
        assert label_map['tag1'] == 2
        assert label_map['tag2'] == 1

    def test_get_label_stats(self, repo, decision_id):
        repo.add_labels(decision_id, ['interesting'])
        stats = repo.get_label_stats(game_id='g1')
        assert stats['interesting'] == 1

    def test_auto_labels_short_stack_fold(self, repo, decision_id):
        labels = repo.compute_and_store_auto_labels(
            decision_id,
            {'action_taken': 'fold', 'stack_bb': 2.0, 'pot_odds': 3.0},
        )
        assert 'short_stack_fold' in labels

        stored = [l['label'] for l in repo.get_labels(decision_id)]
        assert 'short_stack_fold' in stored

    def test_auto_labels_suspicious_fold(self, repo, decision_id):
        labels = repo.compute_and_store_auto_labels(
            decision_id,
            {'action_taken': 'fold', 'stack_bb': 10.0, 'pot_odds': 6.0},
        )
        assert 'suspicious_fold' in labels

    def test_auto_labels_none(self, repo, decision_id):
        labels = repo.compute_and_store_auto_labels(
            decision_id,
            {'action_taken': 'call', 'stack_bb': 20.0},
        )
        assert labels == []

    def test_bulk_add_labels(self, repo, decision_repo):
        did1 = decision_repo.save_decision_analysis(_make_decision(action_taken='fold'))
        did2 = decision_repo.save_decision_analysis(_make_decision(action_taken='call'))

        result = repo.bulk_add_labels([did1, did2], ['tag1', 'tag2'])
        assert result['captures_affected'] == 2
        assert result['labels_added'] == 4

    def test_bulk_remove_labels(self, repo, decision_repo):
        did1 = decision_repo.save_decision_analysis(_make_decision(action_taken='fold'))
        did2 = decision_repo.save_decision_analysis(_make_decision(action_taken='call'))
        repo.bulk_add_labels([did1, did2], ['tag1', 'tag2'])

        result = repo.bulk_remove_labels([did1, did2], ['tag1'])
        assert result['labels_removed'] == 2

    def test_bulk_operations_empty_input(self, repo):
        assert repo.bulk_add_labels([], ['tag1'])['labels_added'] == 0
        assert repo.bulk_remove_labels([], ['tag1'])['labels_removed'] == 0


class TestCaptureBridge:
    """The Prompt Playground works in capture-id space; labels resolve through
    the decision's capture_id."""

    def _capture_and_decision(self, prompt_capture_repo, decision_repo, **cap):
        cid = prompt_capture_repo.save_prompt_capture(_make_capture(**cap))
        did = decision_repo.save_decision_analysis(_make_decision(capture_id=cid))
        return cid, did

    def test_decision_id_for_capture(self, repo, prompt_capture_repo, decision_repo):
        cid, did = self._capture_and_decision(prompt_capture_repo, decision_repo)
        assert repo.decision_id_for_capture(cid) == did
        assert repo.decision_id_for_capture(999999) is None

    def test_get_labels_by_capture(self, repo, prompt_capture_repo, decision_repo):
        cid, did = self._capture_and_decision(prompt_capture_repo, decision_repo)
        repo.add_labels(did, ['mistake'])
        labels = [l['label'] for l in repo.get_labels_by_capture(cid)]
        assert labels == ['mistake']

    def test_search_captures_with_labels(self, repo, prompt_capture_repo, decision_repo):
        cid1, did1 = self._capture_and_decision(
            prompt_capture_repo, decision_repo, game_id='g1', action_taken='fold'
        )
        cid2, did2 = self._capture_and_decision(
            prompt_capture_repo, decision_repo, game_id='g1', action_taken='call'
        )
        repo.add_labels(did1, ['mistake'])
        repo.add_labels(did2, ['good'])

        result = repo.search_captures_with_labels(['mistake'])
        assert result['total'] == 1
        assert result['captures'][0]['id'] == cid1
        assert [l['label'] for l in result['captures'][0]['labels']] == ['mistake']

    def test_search_captures_with_labels_match_all(self, repo, prompt_capture_repo, decision_repo):
        cid1, did1 = self._capture_and_decision(
            prompt_capture_repo, decision_repo, game_id='g1', action_taken='fold'
        )
        cid2, did2 = self._capture_and_decision(
            prompt_capture_repo, decision_repo, game_id='g1', action_taken='call'
        )
        repo.add_labels(did1, ['a', 'b'])
        repo.add_labels(did2, ['a'])

        result = repo.search_captures_with_labels(['a', 'b'], match_all=True)
        assert result['total'] == 1
        assert result['captures'][0]['id'] == cid1

    def test_search_captures_empty_labels_falls_back(self, repo, prompt_capture_repo):
        prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g1'))
        result = repo.search_captures_with_labels([])
        assert result['total'] == 1
