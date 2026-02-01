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


# ==================== Experiment Lifecycle (B4b) ====================

class TestExperimentLifecycle:
    def test_create_and_get_experiment(self, repo):
        config = {
            'name': 'test-exp',
            'description': 'A test experiment',
            'hypothesis': 'testing works',
            'tags': ['test'],
            'num_tournaments': 5,
        }
        exp_id = repo.create_experiment(config)
        assert exp_id > 0

        exp = repo.get_experiment(exp_id)
        assert exp is not None
        assert exp['name'] == 'test-exp'
        assert exp['description'] == 'A test experiment'
        assert exp['hypothesis'] == 'testing works'
        assert exp['tags'] == ['test']
        assert exp['status'] == 'running'  # Schema default is 'running'
        assert exp['config']['num_tournaments'] == 5

    def test_create_experiment_requires_name(self, repo):
        with pytest.raises(ValueError, match="name is required"):
            repo.create_experiment({})

    def test_get_experiment_not_found(self, repo):
        assert repo.get_experiment(9999) is None

    def test_get_experiment_by_name(self, repo):
        repo.create_experiment({'name': 'by-name-test'})
        exp = repo.get_experiment_by_name('by-name-test')
        assert exp is not None
        assert exp['name'] == 'by-name-test'

    def test_get_experiment_by_name_not_found(self, repo):
        assert repo.get_experiment_by_name('nonexistent') is None

    def test_complete_experiment(self, repo):
        exp_id = repo.create_experiment({'name': 'to-complete'})
        summary = {'total_hands': 100, 'winner': 'Batman'}
        repo.complete_experiment(exp_id, summary)

        exp = repo.get_experiment(exp_id)
        assert exp['status'] == 'completed'
        assert exp['completed_at'] is not None
        assert exp['summary'] == summary

    def test_link_game_to_experiment(self, repo):
        exp_id = repo.create_experiment({'name': 'link-test'})
        link_id = repo.link_game_to_experiment(
            exp_id, 'game-1', variant='control',
            variant_config={'model': 'gpt-4'}, tournament_number=1
        )
        assert link_id > 0

        games = repo.get_experiment_games(exp_id)
        assert len(games) == 1
        assert games[0]['game_id'] == 'game-1'
        assert games[0]['variant'] == 'control'
        assert games[0]['variant_config'] == {'model': 'gpt-4'}
        assert games[0]['tournament_number'] == 1

    def test_list_experiments(self, repo):
        repo.create_experiment({'name': 'exp-1'})
        repo.create_experiment({'name': 'exp-2'})

        experiments = repo.list_experiments()
        names = [e['name'] for e in experiments]
        assert 'exp-1' in names
        assert 'exp-2' in names

    def test_list_experiments_status_filter(self, repo):
        exp_id = repo.create_experiment({'name': 'completed-exp'})
        repo.update_experiment_status(exp_id, 'completed')

        completed = repo.list_experiments(status='completed')
        assert any(e['name'] == 'completed-exp' for e in completed)

        running = repo.list_experiments(status='running')
        assert not any(e['name'] == 'completed-exp' for e in running)

    def test_update_experiment_status(self, repo):
        exp_id = repo.create_experiment({'name': 'status-test'})
        repo.update_experiment_status(exp_id, 'running')

        exp = repo.get_experiment(exp_id)
        assert exp['status'] == 'running'

    def test_update_experiment_status_invalid(self, repo):
        exp_id = repo.create_experiment({'name': 'invalid-status'})
        with pytest.raises(ValueError, match="Invalid status"):
            repo.update_experiment_status(exp_id, 'bogus')

    def test_update_experiment_tags(self, repo):
        exp_id = repo.create_experiment({'name': 'tag-test'})
        repo.update_experiment_tags(exp_id, ['a', 'b'])

        exp = repo.get_experiment(exp_id)
        assert exp['tags'] == ['a', 'b']

    def test_mark_running_experiments_interrupted(self, repo):
        exp_id = repo.create_experiment({'name': 'running-1'})
        repo.update_experiment_status(exp_id, 'running')

        count = repo.mark_running_experiments_interrupted()
        assert count == 1

        exp = repo.get_experiment(exp_id)
        assert exp['status'] == 'interrupted'

    def test_get_incomplete_tournaments(self, repo):
        exp_id = repo.create_experiment({'name': 'incomplete-test'})
        repo.link_game_to_experiment(exp_id, 'game-1', variant='control', tournament_number=1)
        repo.link_game_to_experiment(exp_id, 'game-2', variant='control', tournament_number=2)

        incomplete = repo.get_incomplete_tournaments(exp_id)
        assert len(incomplete) == 2
        game_ids = [t['game_id'] for t in incomplete]
        assert 'game-1' in game_ids
        assert 'game-2' in game_ids

    def test_create_experiment_with_parent(self, repo):
        parent_id = repo.create_experiment({'name': 'parent-exp'})
        child_id = repo.create_experiment({'name': 'child-exp'}, parent_experiment_id=parent_id)

        child = repo.get_experiment(child_id)
        assert child['parent_experiment_id'] == parent_id


# ==================== Chat Sessions (B4b) ====================

class TestChatSessions:
    def test_save_and_get_chat_session(self, repo):
        messages = [{'role': 'user', 'content': 'Hello'}]
        config = {'model': 'gpt-4'}
        repo.save_chat_session('session-1', 'owner-1', messages, config)

        session = repo.get_chat_session('session-1')
        assert session is not None
        assert session['session_id'] == 'session-1'
        assert session['messages'] == messages
        assert session['config'] == config

    def test_get_chat_session_not_found(self, repo):
        assert repo.get_chat_session('nonexistent') is None

    def test_save_chat_session_upsert(self, repo):
        repo.save_chat_session('session-1', 'owner-1', [{'role': 'user', 'content': 'v1'}], {})
        repo.save_chat_session('session-1', 'owner-1', [{'role': 'user', 'content': 'v2'}], {'updated': True})

        session = repo.get_chat_session('session-1')
        assert session['messages'][0]['content'] == 'v2'
        assert session['config'] == {'updated': True}

    def test_get_latest_chat_session(self, repo):
        repo.save_chat_session('s1', 'owner-1', [{'role': 'user', 'content': 'only session'}], {})

        latest = repo.get_latest_chat_session('owner-1')
        assert latest is not None
        assert latest['session_id'] == 's1'
        assert latest['messages'][0]['content'] == 'only session'

    def test_get_latest_chat_session_not_found(self, repo):
        assert repo.get_latest_chat_session('nonexistent-owner') is None

    def test_archive_chat_session(self, repo):
        repo.save_chat_session('s1', 'owner-1', [], {})
        repo.archive_chat_session('s1')

        # Archived sessions shouldn't show up in latest
        assert repo.get_latest_chat_session('owner-1') is None
        # But can still be retrieved directly
        assert repo.get_chat_session('s1') is not None

    def test_delete_chat_session(self, repo):
        repo.save_chat_session('s1', 'owner-1', [], {})
        repo.delete_chat_session('s1')
        assert repo.get_chat_session('s1') is None

    def test_save_and_get_experiment_design_chat(self, repo):
        exp_id = repo.create_experiment({'name': 'design-chat-test'})
        chat = [{'role': 'user', 'content': 'Design this'}, {'role': 'assistant', 'content': 'OK'}]
        repo.save_experiment_design_chat(exp_id, chat)

        loaded = repo.get_experiment_design_chat(exp_id)
        assert loaded == chat

    def test_get_experiment_design_chat_empty(self, repo):
        exp_id = repo.create_experiment({'name': 'no-chat'})
        assert repo.get_experiment_design_chat(exp_id) is None

    def test_save_and_get_experiment_assistant_chat(self, repo):
        exp_id = repo.create_experiment({'name': 'assistant-chat-test'})
        chat = [{'role': 'user', 'content': 'Query results'}]
        repo.save_experiment_assistant_chat(exp_id, chat)

        loaded = repo.get_experiment_assistant_chat(exp_id)
        assert loaded == chat


# ==================== Replay Experiments (B4b) ====================

class TestReplayExperiments:
    @pytest.fixture
    def capture_ids(self, repo):
        """Create some captures and return their IDs."""
        ids = []
        for i in range(3):
            cid = repo.save_prompt_capture(_make_capture(
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
