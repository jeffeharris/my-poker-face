"""Auth + serving + validation for the chart-opportunity census admin route.

All assertions exercise paths that short-circuit BEFORE the LLM call (auth,
missing artifact, artifact serving, /ask input validation), so no API key or
network is needed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import flask_app.routes.chart_census_routes as cc
from flask_app import create_app

pytestmark = pytest.mark.flask

SAMPLE = {
    'meta': {'total_preflop_decisions': 3},
    'spot_census': {},
    'money_census': {},
    'archetype_matrix': {},
    'fallthrough_audit': {'classes': []},
}


def _authz(user=None, admin=False):
    a = MagicMock()
    a.auth_manager.get_current_user.return_value = user
    a.has_permission.return_value = admin
    return a


def _auth(user, admin):
    return patch('poker.authorization.authorization_service', _authz(user, admin))


@pytest.fixture(scope='module')
def client():
    app = create_app()
    app.testing = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def artifact(tmp_path):
    """Patch the route to serve a known artifact file."""
    p = tmp_path / 'chart_census.json'
    p.write_text(json.dumps(SAMPLE))
    with patch.object(cc, '_artifact_path', return_value=str(p)):
        yield p


# ── GET /api/admin/chart-census ──────────────────────────────────────────


def test_get_requires_authentication(client):
    with _auth(None, False):
        r = client.get('/api/admin/chart-census')
    assert r.status_code == 401


def test_get_forbids_non_admin(client):
    with _auth({'id': 'u1'}, False):
        r = client.get('/api/admin/chart-census')
    assert r.status_code == 403


def test_get_404_when_no_artifact(client):
    with _auth({'id': 'a1'}, True), patch.object(cc, '_artifact_path', return_value=None):
        r = client.get('/api/admin/chart-census')
    assert r.status_code == 404
    assert r.get_json()['error'] == 'no_artifact'


def test_get_serves_artifact_with_generated_at(client, artifact):
    with _auth({'id': 'a1'}, True):
        r = client.get('/api/admin/chart-census')
    assert r.status_code == 200
    body = r.get_json()
    assert body['meta']['total_preflop_decisions'] == 3
    assert '_generated_at' in body


# ── POST /api/admin/chart-census/ask ─────────────────────────────────────


def test_ask_requires_admin(client, artifact):
    with _auth({'id': 'u1'}, False):
        r = client.post(
            '/api/admin/chart-census/ask',
            json={'messages': [{'role': 'user', 'content': 'hi'}]},
        )
    assert r.status_code == 403


def test_ask_404_when_no_artifact(client):
    with _auth({'id': 'a1'}, True), patch.object(cc, '_artifact_path', return_value=None):
        r = client.post(
            '/api/admin/chart-census/ask',
            json={'messages': [{'role': 'user', 'content': 'hi'}]},
        )
    assert r.status_code == 404


def test_ask_rejects_empty_messages(client, artifact):
    with _auth({'id': 'a1'}, True):
        r = client.post('/api/admin/chart-census/ask', json={'messages': []})
    assert r.status_code == 400


def test_ask_requires_last_turn_to_be_user(client, artifact):
    with _auth({'id': 'a1'}, True):
        r = client.post(
            '/api/admin/chart-census/ask',
            json={'messages': [{'role': 'assistant', 'content': 'hi'}]},
        )
    assert r.status_code == 400
