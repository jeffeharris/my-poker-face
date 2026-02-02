"""Tests for ExperimentRepository (lifecycle, chat sessions)."""
import pytest
from poker.repositories.experiment_repository import ExperimentRepository
from poker.repositories.game_repository import GameRepository


@pytest.fixture
def repo(db_path):
    game_repo = GameRepository(db_path)
    r = ExperimentRepository(db_path, game_repo=game_repo)
    yield r
    r.close()
    game_repo.close()


# ==================== Experiment Lifecycle ====================

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


# ==================== Chat Sessions ====================

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
