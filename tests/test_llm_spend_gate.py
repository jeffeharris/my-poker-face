#!/usr/bin/env python3
"""Tests for the PRH-2 spend gate — the *enforcement* side of the kill-switch.

Two layers:
  * SpendGate logic (pure): disabled by default, arms on configure, global vs
    per-owner ceilings, global-checked-first, fail-open via the reader.
  * LLMClient integration: over budget short-circuits before any provider
    dispatch (returns a failed response); disabled budget passes everything.
"""

import unittest
from unittest.mock import MagicMock, patch

from core.llm.budget import (
    SpendGate,
    classify_shed,
    configure_spend_limits,
    get_spend_gate,
)
from core.llm.client import LLMClient
from core.llm.tracking import CallType


class _StubTracker:
    """Stand-in for UsageTracker.get_recent_spend with canned totals."""

    def __init__(self, global_spend=0.0, owner_spend=None):
        self.global_spend = global_spend
        self.owner_spend = owner_spend or {}
        self.queried = []

    def get_recent_spend(self, owner_id=None, window_hours=24):
        self.queried.append(owner_id)
        if owner_id is None:
            return self.global_spend
        return self.owner_spend.get(owner_id, 0.0)

    def record(self, *args, **kwargs):  # no-op for the allowed-path test
        pass


class TestSpendGateLogic(unittest.TestCase):
    def test_disabled_by_default(self):
        gate = SpendGate()
        self.assertFalse(gate.enabled)
        # Even with spend present, a disabled gate never blocks.
        self.assertIsNone(gate.over_budget_reason('alice', _StubTracker(global_spend=999.0)))

    def test_global_ceiling(self):
        gate = SpendGate()
        gate.configure(global_daily_budget_usd=1.00, per_owner_daily_budget_usd=0)
        self.assertTrue(gate.enabled)

        self.assertIsNone(gate.over_budget_reason(None, _StubTracker(global_spend=0.99)))
        # At or over the cap → blocked.
        self.assertIsNotNone(gate.over_budget_reason(None, _StubTracker(global_spend=1.00)))
        reason = gate.over_budget_reason(None, _StubTracker(global_spend=2.50))
        self.assertIn('global', reason)

    def test_per_owner_ceiling(self):
        gate = SpendGate()
        gate.configure(global_daily_budget_usd=0, per_owner_daily_budget_usd=0.50)

        # Owner over their cap → blocked; a different owner under → allowed.
        over = gate.over_budget_reason('alice', _StubTracker(owner_spend={'alice': 0.60}))
        self.assertIn("alice", over)
        self.assertIsNone(gate.over_budget_reason('bob', _StubTracker(owner_spend={'alice': 0.60})))

        # No owner_id → the per-owner layer can't apply.
        self.assertIsNone(gate.over_budget_reason(None, _StubTracker(owner_spend={'alice': 9.0})))

    def test_global_checked_before_owner(self):
        gate = SpendGate()
        gate.configure(global_daily_budget_usd=1.00, per_owner_daily_budget_usd=0.50)
        tracker = _StubTracker(global_spend=5.0, owner_spend={'alice': 0.0})

        reason = gate.over_budget_reason('alice', tracker)
        self.assertIn('global', reason)
        # Short-circuited on the global check — never queried the owner total.
        self.assertEqual(tracker.queried, [None])

    def test_fails_open_when_reader_reports_zero(self):
        # UsageTracker.get_recent_spend already swallows DB errors and returns
        # 0.0; the gate must then read that as under-budget and allow the call.
        gate = SpendGate()
        gate.configure(global_daily_budget_usd=0.01, per_owner_daily_budget_usd=0.01)
        self.assertIsNone(gate.over_budget_reason('alice', _StubTracker()))

    def test_configure_clamps_negative_to_disabled(self):
        gate = SpendGate()
        gate.configure(global_daily_budget_usd=-5, per_owner_daily_budget_usd=None)
        self.assertFalse(gate.enabled)

    def test_classify_shed(self):
        self.assertEqual(classify_shed(CallType.COMMENTARY), 'cosmetic')
        self.assertEqual(classify_shed(CallType.IMAGE_GENERATION), 'cosmetic')
        self.assertEqual(classify_shed(CallType.CHAT_SUGGESTION), 'cosmetic')
        self.assertEqual(classify_shed(CallType.PLAYER_DECISION), 'decision')
        self.assertEqual(classify_shed(CallType.EXPERIMENT_DESIGN), 'other')
        self.assertEqual(classify_shed(None), 'other')


def _make_client(tracker):
    """Build an LLMClient with a fully mocked provider (no real API/keys)."""
    mock_provider = MagicMock()
    mock_provider.model = 'mock-model'
    mock_provider.provider_name = 'mock'
    mock_provider.image_model = 'mock-image-model'
    mock_provider.reasoning_effort = 'low'
    with patch.object(LLMClient, '_create_provider', return_value=mock_provider):
        client = LLMClient(tracker=tracker)
    return client, mock_provider


class TestSpendGateIntegration(unittest.TestCase):
    def tearDown(self):
        # The gate is a process-wide singleton — disarm it so we never leak an
        # armed budget into the rest of the suite.
        get_spend_gate().configure(0.0, 0.0)

    def test_over_budget_blocks_before_dispatch(self):
        tracker = _StubTracker(global_spend=10.0)
        client, provider = _make_client(tracker)
        configure_spend_limits(global_daily_budget_usd=1.0, per_owner_daily_budget_usd=0)

        resp = client.complete(
            [{'role': 'user', 'content': 'hi'}],
            call_type=CallType.PLAYER_DECISION,
            owner_id='alice',
        )

        self.assertEqual(resp.status, 'error')
        self.assertEqual(resp.error_code, 'budget_exceeded')
        self.assertEqual(resp.content, '')
        self.assertTrue(resp.is_error)
        provider.complete.assert_not_called()

    def test_disabled_budget_dispatches_normally(self):
        tracker = _StubTracker(global_spend=10.0)
        client, provider = _make_client(tracker)
        configure_spend_limits(0, 0)  # disabled

        provider.complete.return_value = 'RAW'
        provider.extract_usage.return_value = {
            'input_tokens': 5,
            'output_tokens': 3,
            'cached_tokens': 0,
            'reasoning_tokens': 0,
        }
        provider.extract_content.return_value = '{"ok": 1}'
        provider.extract_finish_reason.return_value = 'stop'
        provider.extract_request_id.return_value = 'req-1'
        provider.extract_tool_calls.return_value = None
        provider.extract_reasoning_content.return_value = None

        with patch('core.llm.client.capture_prompt'):
            resp = client.complete(
                [{'role': 'user', 'content': 'hi'}],
                call_type=CallType.PLAYER_DECISION,
                owner_id='alice',
            )

        self.assertEqual(resp.status, 'ok')
        self.assertEqual(resp.content, '{"ok": 1}')
        provider.complete.assert_called_once()

    def test_over_budget_blocks_image_generation(self):
        tracker = _StubTracker(global_spend=10.0)
        client, provider = _make_client(tracker)
        configure_spend_limits(global_daily_budget_usd=1.0, per_owner_daily_budget_usd=0)

        resp = client.generate_image('a portrait', owner_id='alice')

        self.assertEqual(resp.status, 'error')
        self.assertEqual(resp.error_code, 'budget_exceeded')
        self.assertEqual(resp.url, '')
        self.assertTrue(resp.is_error)
        provider.generate_image.assert_not_called()

    def test_per_owner_budget_blocks_only_offending_owner(self):
        tracker = _StubTracker(owner_spend={'alice': 5.0, 'bob': 0.0})
        client, provider = _make_client(tracker)
        configure_spend_limits(global_daily_budget_usd=0, per_owner_daily_budget_usd=1.0)

        blocked = client.complete(
            [{'role': 'user', 'content': 'hi'}],
            call_type=CallType.COMMENTARY,
            owner_id='alice',
        )
        self.assertEqual(blocked.error_code, 'budget_exceeded')
        provider.complete.assert_not_called()

        # bob is under his cap — his call dispatches.
        provider.complete.return_value = 'RAW'
        provider.extract_usage.return_value = {
            'input_tokens': 1,
            'output_tokens': 1,
            'cached_tokens': 0,
            'reasoning_tokens': 0,
        }
        provider.extract_content.return_value = '{"ok": 1}'
        provider.extract_finish_reason.return_value = 'stop'
        provider.extract_request_id.return_value = 'req'
        provider.extract_tool_calls.return_value = None
        provider.extract_reasoning_content.return_value = None

        with patch('core.llm.client.capture_prompt'):
            ok = client.complete(
                [{'role': 'user', 'content': 'hi'}],
                call_type=CallType.COMMENTARY,
                owner_id='bob',
            )
        self.assertEqual(ok.status, 'ok')
        provider.complete.assert_called_once()


if __name__ == '__main__':
    unittest.main()
