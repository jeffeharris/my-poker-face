"""Tests for DecisionAnalysisRepository."""
import pytest
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
    }
    base.update(overrides)
    return base


@pytest.fixture
def repo(db_path):
    r = DecisionAnalysisRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def prompt_capture_repo(db_path):
    r = PromptCaptureRepository(db_path)
    yield r
    r.close()


class TestDecisionAnalysis:
    def _make_analysis(self, **overrides):
        base = {
            'game_id': 'game-1',
            'player_name': 'Batman',
            'hand_number': 1,
            'phase': 'PRE_FLOP',
            'action_taken': 'call',
            'decision_quality': 'correct',
            'ev_lost': 0.0,
            'equity': 0.55,
            'processing_time_ms': 100,
        }
        base.update(overrides)
        return base

    def test_save_and_get_decision_analysis(self, repo):
        analysis = self._make_analysis()
        aid = repo.save_decision_analysis(analysis)
        assert aid is not None

        loaded = repo.get_decision_analysis(aid)
        assert loaded is not None
        assert loaded['game_id'] == 'game-1'
        assert loaded['decision_quality'] == 'correct'

    def test_get_decision_analysis_not_found(self, repo):
        assert repo.get_decision_analysis(9999) is None

    def test_get_decision_analysis_by_request(self, repo):
        analysis = self._make_analysis(request_id='req-123')
        repo.save_decision_analysis(analysis)

        loaded = repo.get_decision_analysis_by_request('req-123')
        assert loaded is not None
        assert loaded['request_id'] == 'req-123'

    def test_get_decision_analysis_by_capture(self, repo, prompt_capture_repo):
        cid = prompt_capture_repo.save_prompt_capture(_make_capture(game_id='g1'))
        analysis = self._make_analysis(capture_id=cid)
        repo.save_decision_analysis(analysis)

        loaded = repo.get_decision_analysis_by_capture(cid)
        assert loaded is not None
        assert loaded['capture_id'] == cid

    def test_list_decision_analyses(self, repo):
        for quality in ['correct', 'correct', 'mistake']:
            repo.save_decision_analysis(self._make_analysis(decision_quality=quality))

        result = repo.list_decision_analyses()
        assert result['total'] == 3

        result = repo.list_decision_analyses(decision_quality='mistake')
        assert result['total'] == 1

    def test_get_decision_analysis_stats(self, repo):
        repo.save_decision_analysis(self._make_analysis(
            decision_quality='correct', action_taken='call', ev_lost=0.0, equity=0.6))
        repo.save_decision_analysis(self._make_analysis(
            decision_quality='mistake', action_taken='fold', ev_lost=2.5, equity=0.3))

        stats = repo.get_decision_analysis_stats()
        assert stats['total'] == 2
        assert stats['correct'] == 1
        assert stats['mistakes'] == 1
        assert stats['by_quality']['correct'] == 1
        assert stats['by_action']['call'] == 1
