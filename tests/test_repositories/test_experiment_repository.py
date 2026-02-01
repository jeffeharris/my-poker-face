"""Tests for ExperimentRepository — Part 1 (captures, decisions, presets, labels)."""
import pytest
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.experiment_repository import ExperimentRepository


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
def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    SchemaManager(db_path).ensure_schema()
    r = ExperimentRepository(db_path)
    yield r
    r.close()


# ==================== Prompt Captures ====================

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


# ==================== Decision Analysis ====================

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

    def test_get_decision_analysis_by_capture(self, repo):
        cid = repo.save_prompt_capture(_make_capture(game_id='g1'))
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


# ==================== Prompt Presets ====================

class TestPromptPresets:
    def test_create_and_get_preset(self, repo):
        pid = repo.create_prompt_preset(
            name='test-preset',
            description='A test preset',
            prompt_config={'hand_analysis': True},
            guidance_injection='Be extra careful',
        )
        assert pid > 0

        loaded = repo.get_prompt_preset(pid)
        assert loaded is not None
        assert loaded['name'] == 'test-preset'
        assert loaded['prompt_config'] == {'hand_analysis': True}
        assert loaded['guidance_injection'] == 'Be extra careful'

    def test_get_preset_not_found(self, repo):
        assert repo.get_prompt_preset(9999) is None

    def test_get_preset_by_name(self, repo):
        repo.create_prompt_preset(name='named-preset')
        loaded = repo.get_prompt_preset_by_name('named-preset')
        assert loaded is not None
        assert loaded['name'] == 'named-preset'

    def test_list_presets(self, repo):
        # Count existing system presets first
        existing = repo.list_prompt_presets()
        existing_count = len(existing)

        repo.create_prompt_preset(name='preset-1')
        repo.create_prompt_preset(name='preset-2')

        presets = repo.list_prompt_presets()
        assert len(presets) == existing_count + 2

    def test_update_preset(self, repo):
        pid = repo.create_prompt_preset(name='orig')
        updated = repo.update_prompt_preset(pid, name='renamed', description='updated desc')
        assert updated is True

        loaded = repo.get_prompt_preset(pid)
        assert loaded['name'] == 'renamed'
        assert loaded['description'] == 'updated desc'

    def test_update_preset_not_found(self, repo):
        assert repo.update_prompt_preset(9999, name='nope') is False

    def test_delete_preset(self, repo):
        pid = repo.create_prompt_preset(name='to-delete')
        assert repo.delete_prompt_preset(pid) is True
        assert repo.get_prompt_preset(pid) is None

    def test_delete_preset_not_found(self, repo):
        assert repo.delete_prompt_preset(9999) is False

    def test_duplicate_name_raises(self, repo):
        repo.create_prompt_preset(name='unique')
        with pytest.raises(ValueError, match="already exists"):
            repo.create_prompt_preset(name='unique')


# ==================== Capture Labels ====================

class TestCaptureLabels:
    @pytest.fixture
    def capture_id(self, repo):
        return repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='fold'))

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

    def test_list_all_labels(self, repo, capture_id):
        cid2 = repo.save_prompt_capture(_make_capture(game_id='g2'))
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

    def test_search_captures_with_labels(self, repo):
        cid1 = repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='fold'))
        cid2 = repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='call'))
        repo.add_capture_labels(cid1, ['mistake'])
        repo.add_capture_labels(cid2, ['good'])

        result = repo.search_captures_with_labels(['mistake'])
        assert result['total'] == 1
        assert result['captures'][0]['id'] == cid1

    def test_search_captures_with_labels_match_all(self, repo):
        cid1 = repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='fold'))
        cid2 = repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='call'))
        repo.add_capture_labels(cid1, ['a', 'b'])
        repo.add_capture_labels(cid2, ['a'])

        result = repo.search_captures_with_labels(['a', 'b'], match_all=True)
        assert result['total'] == 1
        assert result['captures'][0]['id'] == cid1

    def test_search_captures_empty_labels_falls_back(self, repo):
        repo.save_prompt_capture(_make_capture(game_id='g1'))
        result = repo.search_captures_with_labels([])
        assert result['total'] == 1

    def test_bulk_add_capture_labels(self, repo):
        cid1 = repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='fold'))
        cid2 = repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='call'))

        result = repo.bulk_add_capture_labels([cid1, cid2], ['tag1', 'tag2'])
        assert result['captures_affected'] == 2
        assert result['labels_added'] == 4

    def test_bulk_remove_capture_labels(self, repo):
        cid1 = repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='fold'))
        cid2 = repo.save_prompt_capture(_make_capture(game_id='g1', action_taken='call'))
        repo.bulk_add_capture_labels([cid1, cid2], ['tag1', 'tag2'])

        result = repo.bulk_remove_capture_labels([cid1, cid2], ['tag1'])
        assert result['labels_removed'] == 2

    def test_bulk_operations_empty_input(self, repo):
        result = repo.bulk_add_capture_labels([], ['tag1'])
        assert result['labels_added'] == 0

        result = repo.bulk_remove_capture_labels([], ['tag1'])
        assert result['labels_removed'] == 0
