"""Tests for the Socket.IO event rate limiter (per-caller keying + idle pruning)."""

from unittest.mock import MagicMock, patch

import pytest

from flask_app import socket_rate_limit as srl

pytestmark = pytest.mark.flask


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset the module-global limiter state around each test."""
    srl._call_log.clear()
    srl._last_sweep = 0.0
    yield
    srl._call_log.clear()
    srl._last_sweep = 0.0


class TestResolveCallerId:
    def test_authenticated_user_keyed_on_id(self):
        auth = MagicMock()
        auth.get_current_user.return_value = {'id': 'user-42'}
        with patch.object(srl.extensions, 'auth_manager', auth):
            assert srl._resolve_caller_id() == 'user:user-42'

    def test_anonymous_keyed_on_sid_not_shared_bucket(self):
        """Two unauthenticated sockets get distinct sid-based keys."""
        auth = MagicMock()
        auth.get_current_user.return_value = None
        with patch.object(srl.extensions, 'auth_manager', auth):
            with patch.object(srl, 'request', MagicMock(sid='sid-A')):
                a = srl._resolve_caller_id()
            with patch.object(srl, 'request', MagicMock(sid='sid-B')):
                b = srl._resolve_caller_id()
        assert a == 'sid:sid-A'
        assert b == 'sid:sid-B'
        assert a != b

    def test_falls_back_to_anonymous_without_sid(self):
        auth = MagicMock()
        auth.get_current_user.return_value = None
        with patch.object(srl.extensions, 'auth_manager', auth):
            with patch.object(srl, 'request', MagicMock(spec=[])):  # no .sid attr
                assert srl._resolve_caller_id() == 'anonymous'


class TestMaybeSweep:
    def test_prunes_idle_keys_keeps_fresh(self):
        now = 10_000.0
        # An idle key whose newest timestamp is well past the max age.
        srl._call_log[('join_game', 'user:old')] = [now - srl._SWEEP_MAX_AGE_SECONDS - 1]
        # A fresh key seen just now.
        srl._call_log[('join_game', 'user:new')] = [now - 1]
        srl._last_sweep = 0.0  # force the sweep to run

        srl._maybe_sweep(now)

        assert ('join_game', 'user:old') not in srl._call_log
        assert ('join_game', 'user:new') in srl._call_log

    def test_rate_limited_throttles_between_runs(self):
        now = 10_000.0
        srl._last_sweep = now  # just swept
        srl._call_log[('e', 'user:old')] = [0.0]  # ancient, would be pruned if swept
        srl._maybe_sweep(now + 1)  # within the throttle interval
        assert ('e', 'user:old') in srl._call_log  # not swept yet


class TestSocketRateLimitDecorator:
    def _wrap(self, max_calls, window):
        calls = {'n': 0}

        @srl.socket_rate_limit(max_calls=max_calls, window_seconds=window)
        def handler(_data):
            calls['n'] += 1

        return handler, calls

    def test_skips_outside_request_context(self):
        handler, calls = self._wrap(1, 10)
        with patch.object(srl, 'has_request_context', return_value=False):
            handler('x')
            handler('x')  # would exceed the cap if limiting were active
        assert calls['n'] == 2  # not limited outside a request

    def test_drops_over_limit_and_emits_rate_limited(self):
        handler, calls = self._wrap(2, 10)
        emit = MagicMock()
        with (
            patch.object(srl, 'has_request_context', return_value=True),
            patch.object(srl, '_resolve_caller_id', return_value='user:x'),
            patch.object(srl, 'emit', emit),
        ):
            handler('a')
            handler('b')
            handler('c')  # third exceeds max_calls=2
        assert calls['n'] == 2
        emit.assert_called_once()
        assert emit.call_args[0][0] == 'rate_limited'

    def test_distinct_callers_do_not_share_a_bucket(self):
        handler, calls = self._wrap(1, 10)
        emit = MagicMock()
        with (
            patch.object(srl, 'has_request_context', return_value=True),
            patch.object(srl, 'emit', emit),
        ):
            with patch.object(srl, '_resolve_caller_id', return_value='sid:A'):
                handler('a')  # A's first — allowed
            with patch.object(srl, '_resolve_caller_id', return_value='sid:B'):
                handler('b')  # B's first — allowed (separate bucket)
        assert calls['n'] == 2
        emit.assert_not_called()
