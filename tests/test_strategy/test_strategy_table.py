"""Tests for poker.strategy.strategy_table."""

import json
import os
import tempfile

import pytest

from poker.strategy.nodes import PreflopNode
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.strategy_table import (
    StrategyTable,
    load_strategy_table,
    _conservative_default,
    _is_action_legal,
    _mask_and_renormalize,
    _parse_json_to_preflop_data,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_JSON = {
    "rfi": {
        "UTG": {
            "AKs": {"raise_2.5bb": 1.0},
            "72o": {"fold": 1.0},
        },
        "BTN": {
            "AKs": {"raise_2.5bb": 0.8, "raise_3bb": 0.2},
        },
    },
    "vs_open": {
        "BB_vs_UTG": {
            "AKs": {"call": 0.4, "raise_3x": 0.6},
            "72o": {"fold": 1.0},
        },
    },
    "vs_3bet": {
        "UTG_vs_HJ": {
            "AA": {"raise_4x": 0.5, "call": 0.5},
        },
    },
    "vs_4bet": {
        "HJ_vs_UTG": {
            "AA": {"jam": 0.7, "call": 0.3},
        },
    },
}


@pytest.fixture
def sample_json_path(tmp_path):
    """Write SAMPLE_JSON to a temp file and return its path."""
    path = tmp_path / "test_preflop.json"
    path.write_text(json.dumps(SAMPLE_JSON))
    return str(path)


@pytest.fixture
def table(sample_json_path):
    """Load a StrategyTable from the sample fixture."""
    return load_strategy_table(sample_json_path)


# ---------------------------------------------------------------------------
# Loading from JSON
# ---------------------------------------------------------------------------

class TestLoadStrategyTable:
    def test_loads_correct_entry_count(self, table):
        # 2 UTG + 1 BTN (rfi) + 2 vs_open + 1 vs_3bet + 1 vs_4bet = 7
        assert table.size == 7

    def test_rfi_entry_parsed(self, table):
        node = PreflopNode(hand='AKs', position='UTG', scenario='rfi', opener_position='')
        profile = table.lookup_preflop(node)
        assert profile is not None
        assert profile.action_probabilities == {'raise_2.5bb': 1.0}

    def test_vs_open_entry_parsed(self, table):
        node = PreflopNode(hand='AKs', position='BB', scenario='vs_open', opener_position='UTG')
        profile = table.lookup_preflop(node)
        assert profile is not None
        assert profile.action_probabilities == {'call': 0.4, 'raise_3x': 0.6}

    def test_vs_3bet_entry_parsed(self, table):
        node = PreflopNode(hand='AA', position='UTG', scenario='vs_3bet', opener_position='HJ')
        profile = table.lookup_preflop(node)
        assert profile is not None
        assert profile.action_probabilities == {'raise_4x': 0.5, 'call': 0.5}

    def test_vs_4bet_entry_parsed(self, table):
        node = PreflopNode(hand='AA', position='HJ', scenario='vs_4bet', opener_position='UTG')
        profile = table.lookup_preflop(node)
        assert profile is not None
        assert profile.action_probabilities == {'jam': 0.7, 'call': 0.3}


# ---------------------------------------------------------------------------
# Exact Lookup
# ---------------------------------------------------------------------------

class TestLookupPreflop:
    def test_known_node_returns_profile(self, table):
        node = PreflopNode(hand='72o', position='UTG', scenario='rfi', opener_position='')
        profile = table.lookup_preflop(node)
        assert profile is not None
        assert profile.action_probabilities == {'fold': 1.0}

    def test_unknown_node_returns_none(self, table):
        node = PreflopNode(hand='QJs', position='CO', scenario='rfi', opener_position='')
        assert table.lookup_preflop(node) is None


# ---------------------------------------------------------------------------
# Fallback When Key Is Missing
# ---------------------------------------------------------------------------

class TestFallbackMissing:
    def test_missing_key_folds(self, table):
        node = PreflopNode(hand='QJs', position='CO', scenario='rfi', opener_position='')
        result = table.lookup_with_fallback(node, ['fold', 'call', 'raise'])
        assert result.action_probabilities == {'fold': 1.0}

    def test_missing_key_checks_when_bb(self, table):
        node = PreflopNode(hand='QJs', position='CO', scenario='rfi', opener_position='')
        result = table.lookup_with_fallback(node, ['check', 'raise'])
        assert result.action_probabilities == {'check': 1.0}


# ---------------------------------------------------------------------------
# Legal Action Masking
# ---------------------------------------------------------------------------

class TestLegalActionMasking:
    def test_raise_actions_legal_with_raise(self):
        assert _is_action_legal('raise_2.5bb', ['fold', 'call', 'raise']) is True
        assert _is_action_legal('raise_3bb', ['fold', 'call', 'raise']) is True
        assert _is_action_legal('raise_3x', ['fold', 'call', 'raise']) is True
        assert _is_action_legal('raise_4x', ['fold', 'call', 'raise']) is True
        assert _is_action_legal('raise_2.2x', ['fold', 'call', 'raise']) is True

    def test_raise_actions_legal_with_all_in(self):
        assert _is_action_legal('raise_2.5bb', ['fold', 'call', 'all_in']) is True

    def test_raise_actions_illegal_without_raise_or_all_in(self):
        assert _is_action_legal('raise_2.5bb', ['fold', 'call']) is False

    def test_jam_legal_with_all_in(self):
        assert _is_action_legal('jam', ['fold', 'call', 'all_in']) is True

    def test_jam_illegal_without_all_in(self):
        assert _is_action_legal('jam', ['fold', 'call', 'raise']) is False

    def test_direct_actions(self):
        assert _is_action_legal('fold', ['fold', 'call']) is True
        assert _is_action_legal('check', ['check', 'raise']) is True
        assert _is_action_legal('call', ['fold', 'call']) is True
        assert _is_action_legal('fold', ['check', 'raise']) is False

    def test_mask_removes_illegal_and_renormalizes(self, table):
        # BTN AKs: raise_2.5bb=0.8, raise_3bb=0.2 — both are raise actions
        node = PreflopNode(hand='AKs', position='BTN', scenario='rfi', opener_position='')
        # Legal actions don't include raise or all_in → both masked out
        result = table.lookup_with_fallback(node, ['fold', 'call'])
        # Should fall back to conservative default (fold)
        assert result.action_probabilities == {'fold': 1.0}

    def test_mask_partial_removal_renormalizes(self):
        profile = StrategyProfile(action_probabilities={
            'fold': 0.3, 'call': 0.3, 'raise_3x': 0.4,
        })
        masked = _mask_and_renormalize(profile, ['fold', 'call'])
        assert masked is not None
        assert 'raise_3x' not in masked.action_probabilities
        assert abs(masked.action_probabilities['fold'] - 0.5) < 1e-9
        assert abs(masked.action_probabilities['call'] - 0.5) < 1e-9

    def test_lookup_with_fallback_masks_and_renormalizes(self, table):
        # vs_4bet HJ_vs_UTG AA: jam=0.7, call=0.3
        # Legal: fold, call (no all_in → jam is masked)
        node = PreflopNode(hand='AA', position='HJ', scenario='vs_4bet', opener_position='UTG')
        result = table.lookup_with_fallback(node, ['fold', 'call'])
        assert result.action_probabilities == {'call': 1.0}


# ---------------------------------------------------------------------------
# Conservative Default
# ---------------------------------------------------------------------------

class TestConservativeDefault:
    def test_fold_when_no_check(self):
        result = _conservative_default(['fold', 'call', 'raise'])
        assert result.action_probabilities == {'fold': 1.0}

    def test_check_when_check_available(self):
        result = _conservative_default(['check', 'raise'])
        assert result.action_probabilities == {'check': 1.0}

    def test_check_preferred_over_fold(self):
        result = _conservative_default(['fold', 'check', 'raise'])
        assert result.action_probabilities == {'check': 1.0}


# ---------------------------------------------------------------------------
# Edge Case: All Strategy Actions Illegal
# ---------------------------------------------------------------------------

class TestAllActionsIllegal:
    def test_all_illegal_returns_conservative_default(self):
        # Profile has only raise actions, but legal actions have no raise/all_in
        profile = StrategyProfile(action_probabilities={
            'raise_2.5bb': 0.5, 'raise_3bb': 0.3, 'jam': 0.2,
        })
        masked = _mask_and_renormalize(profile, ['fold', 'call'])
        assert masked is None

    def test_lookup_with_fallback_all_illegal_folds(self, table):
        # BTN AKs: raise_2.5bb=0.8, raise_3bb=0.2 — only raise actions
        node = PreflopNode(hand='AKs', position='BTN', scenario='rfi', opener_position='')
        result = table.lookup_with_fallback(node, ['fold', 'check'])
        # All strategy actions are illegal for these legal_actions → conservative default
        assert result.action_probabilities == {'check': 1.0}


# ---------------------------------------------------------------------------
# Parse Helpers
# ---------------------------------------------------------------------------

class TestParseHelpers:
    def test_parse_json_rfi_keys(self):
        data = _parse_json_to_preflop_data(SAMPLE_JSON)
        assert 'rfi|UTG||AKs' in data
        assert 'rfi|UTG||72o' in data
        assert 'rfi|BTN||AKs' in data

    def test_parse_json_vs_open_keys(self):
        data = _parse_json_to_preflop_data(SAMPLE_JSON)
        assert 'vs_open|BB|UTG|AKs' in data
        assert 'vs_open|BB|UTG|72o' in data

    def test_parse_json_vs_3bet_keys(self):
        data = _parse_json_to_preflop_data(SAMPLE_JSON)
        assert 'vs_3bet|UTG|HJ|AA' in data

    def test_parse_json_vs_4bet_keys(self):
        data = _parse_json_to_preflop_data(SAMPLE_JSON)
        assert 'vs_4bet|HJ|UTG|AA' in data

    def test_empty_json(self):
        data = _parse_json_to_preflop_data({})
        assert data == {}
