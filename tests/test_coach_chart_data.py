#!/usr/bin/env python3
"""Unit tests for chart-leak context reconstruction (pure — no DB).

Covers the two provenance paths: exact stored node_key (capture-forward) and
cost-to-call backfill, plus the not-gradeable exclusions.
"""

from flask_app.services import coach_chart_data
from flask_app.services.coach_chart_data import (
    get_owner_chart_leak_set,
    infer_scenario,
    position_label,
    reconstruct_context,
)


class TestPositionLabel:
    def test_engine_keys_map_to_6max(self):
        assert position_label('button') == 'BTN'
        assert position_label('under_the_gun') == 'UTG'
        assert position_label('small_blind_player') == 'SB'
        assert position_label('big_blind_player') == 'BB'

    def test_unmappable(self):
        assert position_label('garbage') is None
        assert position_label(None) is None


class TestInferScenario:
    def test_folded_to_hero_is_rfi(self):
        # Non-blind facing exactly one big blind = open opportunity.
        assert infer_scenario('BTN', cost_to_call=100, bb=100) == 'rfi'

    def test_facing_a_raise_is_vs_open(self):
        assert infer_scenario('CO', cost_to_call=250, bb=100) == 'vs_open'

    def test_big_reraise_is_vs_3bet(self):
        assert infer_scenario('UTG', cost_to_call=600, bb=100) == 'vs_3bet'

    def test_bb_free_check_is_not_gradeable(self):
        # BB with nothing to call just checks — no decision.
        assert infer_scenario('BB', cost_to_call=0, bb=100) is None

    def test_sb_complete_is_rfi(self):
        # SB facing only the BB (cost = BB - SB) is an open/complete spot.
        assert infer_scenario('SB', cost_to_call=50, bb=100) == 'rfi'

    def test_no_blind_is_none(self):
        assert infer_scenario('BTN', cost_to_call=100, bb=0) is None


class TestReconstructContext:
    def test_prefers_stored_node_key(self):
        # Capture-forward: exact opener + vs_3bet that backfill can't infer.
        row = {
            'canon': 'KJs',
            'preflop_node_key': 'vs_3bet|CO|BTN|KJs',
            'num_opponents': 5,
            'player_stack': 2000,
            'action_taken': 'fold',
        }
        ctx = reconstruct_context(row, bb=100)
        assert ctx['scenario'] == 'vs_3bet'
        assert ctx['position'] == 'CO'
        assert ctx['opener'] == 'BTN'
        assert ctx['hand'] == 'KJs'
        assert ctx['num_players'] == 6
        assert ctx['effective_stack_bb'] == 20  # 2000 / 100
        assert ctx['action'] == 'fold'

    def test_backfill_rfi(self):
        row = {
            'player_hand_canonical': '72o',
            'player_position': 'under_the_gun',
            'cost_to_call': 100,
            'player_stack': 5000,
            'num_opponents': 5,
            'action_taken': 'fold',
        }
        ctx = reconstruct_context(row, bb=100)
        assert ctx['scenario'] == 'rfi'
        assert ctx['position'] == 'UTG'
        assert ctx['opener'] is None  # unknown on backfill → grader averages
        assert ctx['hand'] == '72o'

    def test_backfill_bb_check_excluded(self):
        row = {
            'player_hand_canonical': '72o',
            'player_position': 'big_blind_player',
            'cost_to_call': 0,
            'player_stack': 5000,
            'num_opponents': 3,
            'action_taken': 'check',
        }
        assert reconstruct_context(row, bb=100) is None

    def test_unmappable_position_excluded(self):
        row = {
            'player_hand_canonical': 'AA',
            'player_position': 'mystery_seat',
            'cost_to_call': 100,
            'action_taken': 'raise',
        }
        assert reconstruct_context(row, bb=100) is None

    def test_missing_hand_excluded(self):
        row = {'player_position': 'button', 'cost_to_call': 100, 'action_taken': 'raise'}
        assert reconstruct_context(row, bb=100) is None


class TestLiveLeakSet:
    """get_owner_chart_leak_set builds the (confirmed-only) live-recall lookup.

    Uses the real solver charts (available in-container) with synthetic
    decisions, monkeypatching only the DB loader.
    """

    def _patch(self, monkeypatch, decisions):
        monkeypatch.setattr(
            coach_chart_data, 'load_owner_chart_decisions', lambda *a, **k: decisions
        )

    def test_confirmed_limp_lands_in_by_spot(self, monkeypatch):
        # 8 open-limps from the SB (chart raises-or-folds) → confirmed limp leak.
        decisions = [
            {
                'hand': 'KQs', 'position': 'SB', 'scenario': 'rfi', 'opener': None,
                'effective_stack_bb': 50, 'num_players': 6, 'action': 'call',
            }
            for _ in range(8)
        ]
        self._patch(monkeypatch, decisions)
        leak_set = get_owner_chart_leak_set('db', 'owner')
        assert ('rfi', 'SB') in leak_set['by_spot']
        assert leak_set['by_spot'][('rfi', 'SB')]['kind'] == 'limp'
        assert leak_set['by_spot'][('rfi', 'SB')]['status'] == 'confirmed'

    def test_watching_excluded_when_confirmed_only(self, monkeypatch):
        # 5 plays → eligible but watching tier (< CONFIRM_MIN_SEEN=6) → excluded
        # from the live (confirmed-only) set.
        decisions = [
            {
                'hand': 'KQs', 'position': 'SB', 'scenario': 'rfi', 'opener': None,
                'effective_stack_bb': 50, 'num_players': 6, 'action': 'call',
            }
            for _ in range(5)
        ]
        self._patch(monkeypatch, decisions)
        leak_set = get_owner_chart_leak_set('db', 'owner', confirmed_only=True)
        assert leak_set['by_spot'] == {}
        # ...but present when watching is allowed.
        loose = get_owner_chart_leak_set('db', 'owner', confirmed_only=False)
        assert ('rfi', 'SB') in loose['by_spot']

    def test_recall_scopes_to_recent_form(self, monkeypatch):
        # Old hands limp KQs from the SB (a leak); recent hands raise it (fixed).
        def dec(action, created, n):
            return [
                {
                    'hand': 'KQs', 'position': 'SB', 'scenario': 'rfi', 'opener': None,
                    'effective_stack_bb': 50, 'num_players': 6, 'action': action,
                    'created_at': f'2026-{created} 00:00:00', 'hand_number': i,
                }
                for i in range(n)
            ]

        self._patch(monkeypatch, dec('call', '01-01', 8) + dec('raise', '06-01', 8))
        # Recall reads the recent window (the 8 raises) → the limp no longer nudges.
        recent = get_owner_chart_leak_set('db', 'owner', recent_hands=8)
        assert ('rfi', 'SB') not in recent['by_spot']
        # All-time still sees the old limps as a leak.
        all_time = get_owner_chart_leak_set('db', 'owner', recent_hands=None)
        assert ('rfi', 'SB') in all_time['by_spot']
