"""Tests for the /api/game/<game_id>/relationships debug endpoint.

Confirms the route surfaces the relationship state the AI bots see
(after heat projection) plus recent memorable hands per pair. Includes
both directions of each pair — bilateral updates write both rows and
the debug view shows both so the operator can see how each side sees
the other.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

from flask_app import create_app
from flask_app.services import game_state_service
from poker.memory.opponent_model import OpponentModelManager, RelationshipState
from poker.memory.relationship_events import RelationshipEvent
from poker.repositories import create_repos
from poker.repositories.relationship_repository import RelationshipRepository
from poker.repositories.schema_manager import SchemaManager


def _mock_authorization_service(user=None, has_admin_permission=True):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class TestRelationshipsRoute(unittest.TestCase):
    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        SchemaManager(self.test_db.name).ensure_schema()
        self.repos = create_repos(self.test_db.name)

        def mock_init_persistence():
            import flask_app.extensions as ext
            for key, repo in self.repos.items():
                if key == 'db_path':
                    ext.persistence_db_path = repo
                    continue
                setattr(ext, key, repo)

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()

        # Auth bypass — every test in this class is exercising the
        # route, not the admin guard. Apply at setUp so each test
        # method doesn't have to repeat the patch.
        self._auth_ctx = patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(
                user={'id': 'admin', 'name': 'Admin'},
                has_admin_permission=True,
            ),
        )
        self._auth_ctx.start()

        # Build an OpponentModelManager wired to a real repo over the
        # tempdb — the route reads through this manager.
        self.relationship_repo = RelationshipRepository(self.test_db.name)
        self.opp_manager = OpponentModelManager(
            relationship_repo=self.relationship_repo,
        )
        self.opp_manager.register_player_id("alice", "alice_pid")
        self.opp_manager.register_player_id("bob", "bob_pid")

        # Seed an in-memory model so the route iterates the pair.
        # The route walks `manager.models[observer][opponent]`, so
        # both sides must exist as models for both directions to land
        # in the response.
        self.opp_manager.get_model("alice", "bob")
        self.opp_manager.get_model("bob", "alice")

        # Stash a fake game_data with the memory_manager into the
        # in-memory game store the route reads from.
        self.game_id = "test_game"
        fake_memory_manager = MagicMock()
        fake_memory_manager.get_opponent_model_manager.return_value = self.opp_manager
        game_state_service.set_game(self.game_id, {
            'memory_manager': fake_memory_manager,
        })

    def tearDown(self):
        self._auth_ctx.stop()
        game_state_service.games.pop(self.game_id, None)
        self.relationship_repo.close()
        for repo in self.repos.values():
            if hasattr(repo, 'close'):
                repo.close()
        os.unlink(self.test_db.name)

    def _seed(self, observer_id, opponent_id, **axes):
        now = datetime.utcnow()
        state = RelationshipState(
            last_seen=now, last_decay_tick=now, **axes,
        )
        self.relationship_repo.save_relationship_state(
            observer_id, opponent_id, state,
        )

    def test_game_not_in_memory_returns_404(self):
        response = self.client.get('/api/game/missing/relationships')
        self.assertEqual(response.status_code, 404)

    def test_no_memory_manager_returns_404(self):
        game_state_service.set_game('no_mm_game', {})
        try:
            response = self.client.get('/api/game/no_mm_game/relationships')
            self.assertEqual(response.status_code, 404)
        finally:
            game_state_service.games.pop('no_mm_game', None)

    def test_returns_pairs_with_labels(self):
        # alice → bob is heated (rival); bob → alice is friendly
        # (high respect + likability). Both pairs should land in the
        # response with their respective labels.
        self._seed("alice_pid", "bob_pid", heat=0.65, respect=0.4, likability=0.5)
        self._seed("bob_pid", "alice_pid", heat=0.0, respect=0.8, likability=0.85)

        response = self.client.get(f'/api/game/{self.game_id}/relationships')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data['game_id'], self.game_id)
        self.assertEqual(data['pair_count'], 2)
        pairs_by_direction = {
            (p['observer'], p['opponent']): p for p in data['pairs']
        }
        self.assertEqual(pairs_by_direction[('alice', 'bob')]['label'], 'rival')
        self.assertEqual(pairs_by_direction[('bob', 'alice')]['label'], 'friendly')

    def test_neutral_pair_included_with_label_none(self):
        # No seed call → repo returns None → route surfaces the pair
        # with defaults and label = None. Debug view shows the full
        # state, not just the filtered prompt view.
        response = self.client.get(f'/api/game/{self.game_id}/relationships')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        for pair in data['pairs']:
            self.assertIsNone(pair['label'])
            self.assertEqual(pair['heat'], 0.0)
            self.assertEqual(pair['respect'], 0.5)
            self.assertEqual(pair['likability'], 0.5)
            self.assertEqual(pair['memorable_hands'], [])

    def test_memorable_hands_surfaced(self):
        self._seed("alice_pid", "bob_pid", heat=0.65)
        model = self.opp_manager.get_model("alice", "bob")
        model.add_memorable_hand(
            hand_id=42,
            event=RelationshipEvent.BAD_BEAT,
            impact_score=0.9,
            narrative="bob bad-beat alice on hand 42",
            hand_summary="QQ vs KQ rivered",
        )

        response = self.client.get(f'/api/game/{self.game_id}/relationships')
        data = response.get_json()
        rival_pair = next(
            p for p in data['pairs']
            if p['observer'] == 'alice' and p['opponent'] == 'bob'
        )
        self.assertEqual(len(rival_pair['memorable_hands']), 1)
        self.assertEqual(rival_pair['memorable_hands'][0]['event'], 'bad_beat')
        self.assertIn('bad-beat alice', rival_pair['memorable_hands'][0]['narrative'])

    def test_db_fallback_when_game_not_in_memory(self):
        """Game evicted from memory but opponent_models persisted in DB
        — the DB fallback path reconstructs the response without needing
        the in-memory OpponentModelManager. Mirrors the production
        scenario where cash sessions get evicted between visits.
        """
        # Seed relationship_states for the pair.
        self._seed("alice_pid", "bob_pid", heat=0.65, respect=0.4, likability=0.5)
        # Persist opponent_models for a different game_id (not in memory).
        # save_opponent_models reads the manager's models dict + ids.
        evicted_game_id = 'evicted_game'
        # Attach a memorable hand on the in-memory model, then persist.
        model = self.opp_manager.get_model("alice", "bob")
        model.add_memorable_hand(
            hand_id=99,
            event=RelationshipEvent.BIG_LOSS,
            impact_score=0.85,
            narrative="alice lost big to bob on hand 99",
            hand_summary="set vs flush",
        )
        self.repos['game_repo'].save_opponent_models(
            evicted_game_id, self.opp_manager,
        )

        response = self.client.get(f'/api/game/{evicted_game_id}/relationships')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['source'], 'db')
        rival_pair = next(
            p for p in data['pairs']
            if p['observer'] == 'alice' and p['opponent'] == 'bob'
        )
        # Axis values came from the cross-session relationship_states row.
        self.assertEqual(rival_pair['label'], 'rival')
        # Memorable hand came from the per-game memorable_hands rows.
        self.assertEqual(len(rival_pair['memorable_hands']), 1)
        self.assertIn('lost big', rival_pair['memorable_hands'][0]['narrative'])

    def test_db_fallback_no_data_returns_404(self):
        """No opponent_models rows for the game → 404 with a clearer
        error so the operator can distinguish "game doesn't exist" from
        "game exists but no relationship data was captured."
        """
        response = self.client.get('/api/game/totally_unknown/relationships')
        self.assertEqual(response.status_code, 404)
        self.assertIn(
            'No relationship data',
            response.get_json().get('error', ''),
        )

    def test_in_memory_source_marker(self):
        """When the in-memory path serves the response, the `source`
        field reflects that — useful for the frontend to distinguish
        "live data" from "frozen at last persistence."
        """
        self._seed("alice_pid", "bob_pid", heat=0.65)
        response = self.client.get(f'/api/game/{self.game_id}/relationships')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['source'], 'memory')
