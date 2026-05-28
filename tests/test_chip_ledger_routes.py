"""Route-level tests for the admin chip-ledger endpoints.

`compute_audit` and the repo APIs have their own unit tests; this file
covers the thin route layer:

  * `?sandbox_id=` plumbing on `/api/admin/chip-ledger/audit` and
    `/recent` (default = cross-sandbox, scoped value = per-sandbox).
  * `/api/admin/sandboxes` returns the live sandbox list.
  * Admin permission gating returns 401/403 for unauth/non-admin.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask_app import create_app
from poker.repositories import create_repos


def _mock_authorization_service(user=None, has_admin_permission=False):
    authz = MagicMock()
    authz.auth_manager.get_current_user.return_value = user
    authz.has_permission.return_value = has_admin_permission
    return authz


class TestChipLedgerRoutesSandboxScoping(unittest.TestCase):
    """Verify the sandbox filter wires through to compute_audit + repo."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.repos = create_repos(self.test_db.name)

        # Seed two sandboxes with distinct ledger rows.
        ledger_repo = self.repos['chip_ledger_repo']
        ledger_repo.record(
            source='central_bank',
            sink='ai:zeus',
            amount=2000,
            reason='ai_seed',
            sandbox_id='sb1',
        )
        ledger_repo.record(
            source='central_bank',
            sink='ai:hera',
            amount=1500,
            reason='ai_seed',
            sandbox_id='sb2',
        )
        sandbox_repo = self.repos['sandbox_repo']
        self.sb1 = sandbox_repo.create('owner_alice', name='Sandbox One')
        self.sb2 = sandbox_repo.create('owner_bob', name='Sandbox Two')

        def mock_init_persistence():
            import flask_app.extensions as ext

            for key in (
                'game_repo',
                'user_repo',
                'settings_repo',
                'personality_repo',
                'experiment_repo',
                'prompt_capture_repo',
                'decision_analysis_repo',
                'prompt_preset_repo',
                'capture_label_repo',
                'replay_experiment_repo',
                'llm_repo',
                'guest_tracking_repo',
                'hand_history_repo',
                'tournament_repo',
                'coach_repo',
                'relationship_repo',
                'bankroll_repo',
                'cash_table_repo',
                'chip_ledger_repo',
                'stake_repo',
                'sandbox_repo',
            ):
                setattr(ext, key, self.repos[key])
            ext.persistence_db_path = self.repos['db_path']

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()
        # The route module now reads these globals live via `extensions.X`,
        # so patch the canonical `flask_app.extensions` names. Re-stamp them
        # per test so each tempdb's repos drive the handlers (mirrors the
        # pattern in test_admin_experiment_route_auth.py).
        self._route_patches = [
            patch(f'flask_app.extensions.{name}', self.repos[key])
            for name, key in (
                ('chip_ledger_repo', 'chip_ledger_repo'),
                ('bankroll_repo', 'bankroll_repo'),
                ('cash_table_repo', 'cash_table_repo'),
                ('stake_repo', 'stake_repo'),
                ('sandbox_repo', 'sandbox_repo'),
            )
        ]
        self._route_patches.append(
            patch(
                'flask_app.extensions.persistence_db_path',
                self.repos['db_path'],
            )
        )
        for p in self._route_patches:
            p.start()
        self.addCleanup(self._stop_route_patches)

    def _stop_route_patches(self):
        for p in self._route_patches:
            p.stop()

    def tearDown(self):
        for repo in self.repos.values():
            if hasattr(repo, 'close'):
                repo.close()
        os.unlink(self.test_db.name)

    @staticmethod
    def _admin_patch():
        return patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(
                user={'id': 'admin-1', 'name': 'Admin'},
                has_admin_permission=True,
            ),
        )

    def test_audit_default_aggregates_across_sandboxes(self):
        with self._admin_patch():
            response = self.client.get('/api/admin/chip-ledger/audit')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        # Both sandboxes' creations rolled up.
        self.assertEqual(data['ledger_totals']['chips_created'], 3500)

    def test_audit_with_sandbox_id_scopes_to_one(self):
        """Verifies the route propagates `sandbox_id` to compute_audit."""
        with self._admin_patch():
            response = self.client.get(
                '/api/admin/chip-ledger/audit?sandbox_id=sb1',
            )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['ledger_totals']['chips_created'], 2000)

        with self._admin_patch():
            response = self.client.get(
                '/api/admin/chip-ledger/audit?sandbox_id=sb2',
            )
        data = response.get_json()
        self.assertEqual(data['ledger_totals']['chips_created'], 1500)

    def test_audit_empty_sandbox_id_treated_as_cross_sandbox(self):
        with self._admin_patch():
            response = self.client.get('/api/admin/chip-ledger/audit?sandbox_id=')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['ledger_totals']['chips_created'], 3500)

    def test_audit_unknown_sandbox_returns_empty_payload(self):
        with self._admin_patch():
            response = self.client.get(
                '/api/admin/chip-ledger/audit?sandbox_id=does-not-exist',
            )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['ledger_totals']['chips_created'], 0)
        self.assertEqual(data['ledger_totals']['chips_destroyed'], 0)

    def test_recent_default_lists_all_sandboxes(self):
        with self._admin_patch():
            response = self.client.get('/api/admin/chip-ledger/recent')
        self.assertEqual(response.status_code, 200)
        amounts = {e['amount'] for e in response.get_json()['entries']}
        self.assertEqual(amounts, {2000, 1500})

    def test_recent_with_sandbox_id_filters(self):
        with self._admin_patch():
            response = self.client.get('/api/admin/chip-ledger/recent?sandbox_id=sb1')
        self.assertEqual(response.status_code, 200)
        entries = response.get_json()['entries']
        self.assertEqual([e['amount'] for e in entries], [2000])


class TestAdminSandboxList(unittest.TestCase):
    """`/api/admin/sandboxes` returns the live sandbox list."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        self.repos = create_repos(self.test_db.name)

        sandbox_repo = self.repos['sandbox_repo']
        self.sb1 = sandbox_repo.create('owner_alice', name='Alpha')
        import time

        time.sleep(0.01)
        self.sb2 = sandbox_repo.create('owner_bob', name='Beta')  # newer

        def mock_init_persistence():
            import flask_app.extensions as ext

            for key in (
                'game_repo',
                'user_repo',
                'settings_repo',
                'personality_repo',
                'experiment_repo',
                'prompt_capture_repo',
                'decision_analysis_repo',
                'prompt_preset_repo',
                'capture_label_repo',
                'replay_experiment_repo',
                'llm_repo',
                'guest_tracking_repo',
                'hand_history_repo',
                'tournament_repo',
                'coach_repo',
                'relationship_repo',
                'bankroll_repo',
                'cash_table_repo',
                'chip_ledger_repo',
                'stake_repo',
                'sandbox_repo',
            ):
                setattr(ext, key, self.repos[key])
            ext.persistence_db_path = self.repos['db_path']

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()
        # See note in TestChipLedgerRoutesSandboxScoping.setUp on why
        # the route module's repo names need explicit per-test rebinding.
        # `list_sandboxes` orders by freshest net-worth snapshot, so bind
        # holdings_snapshots_repo to this test's (empty) repo too — otherwise
        # the route's stale import-time binding could point at another test's
        # populated DB and perturb the ordering.
        self._route_patches = [
            patch(
                'flask_app.extensions.sandbox_repo',
                self.repos['sandbox_repo'],
            ),
            patch(
                'flask_app.extensions.holdings_snapshots_repo',
                self.repos['holdings_snapshots_repo'],
            ),
        ]
        for p in self._route_patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        for repo in self.repos.values():
            if hasattr(repo, 'close'):
                repo.close()
        os.unlink(self.test_db.name)

    def test_admin_can_list_sandboxes(self):
        with patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(
                user={'id': 'admin-1'},
                has_admin_permission=True,
            ),
        ):
            response = self.client.get('/api/admin/sandboxes')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        # The route orders by (freshest net-worth snapshot, created_at) DESC so
        # the admin panel defaults to the liveliest sandbox. With no snapshots
        # here, that collapses to newest-created-first: Beta (newer) then Alpha.
        ids = [s['sandbox_id'] for s in payload['sandboxes']]
        self.assertEqual(ids, [self.sb2.sandbox_id, self.sb1.sandbox_id])
        names = [s['name'] for s in payload['sandboxes']]
        self.assertEqual(names, ['Beta', 'Alpha'])

    def test_unauth_user_gets_401(self):
        with patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(user=None, has_admin_permission=False),
        ):
            response = self.client.get('/api/admin/sandboxes')
        self.assertEqual(response.status_code, 401)

    def test_non_admin_gets_403(self):
        with patch(
            'poker.authorization.authorization_service',
            _mock_authorization_service(
                user={'id': 'user-1'},
                has_admin_permission=False,
            ),
        ):
            response = self.client.get('/api/admin/sandboxes')
        self.assertEqual(response.status_code, 403)
