"""Tests for Phase 7.5 config loader (poker/strategy/phase_7_5_config.py).

Verifies the YAML file parses into the typed dataclasses correctly
and that the module-level CONFIG singleton has expected values from
the on-disk defaults.
"""

import pytest

from poker.strategy import phase_7_5_config as cfg


def test_module_level_config_loads():
    """Import-time load produces a valid Phase75Config."""
    assert cfg.CONFIG is not None
    assert isinstance(cfg.CONFIG, cfg.Phase75Config)


def test_exploitation_clamps_v1_placeholders():
    c = cfg.CONFIG.exploitation_clamps
    assert c.default_max_total_shift == 0.4
    assert c.medium_max_total_shift == 0.6
    assert c.extreme_max_total_shift == 0.8


def test_sample_thresholds_v1_placeholders():
    s = cfg.CONFIG.sample_thresholds
    assert s.medium_min_opportunities == 60
    # 2026-05-23: dropped 120 → 40 (see config yaml comment)
    assert s.extreme_min_opportunities == 40


def test_signal_thresholds_v1_placeholders():
    s = cfg.CONFIG.signal_thresholds
    assert s.medium_af_postflop == 4.0
    # 2026-05-23: dropped 6.0 → 4.5 (see config yaml comment)
    assert s.extreme_af_postflop == 4.5
    assert s.medium_all_in_per_facing_bet == 0.15
    assert s.extreme_all_in_per_facing_bet == 0.30
    assert s.medium_postflop_jam_open_rate == 0.10
    assert s.extreme_postflop_jam_open_rate == 0.20


def test_tier_decay_v1_placeholders():
    d = cfg.CONFIG.tier_decay
    assert d.window_size == 50
    assert d.require_recent_window_full == 30


def test_benchmark_prior_default_off():
    """Production default MUST be off — only experiments flip it on."""
    p = cfg.CONFIG.benchmark_prior
    assert p.enabled is False
    assert 'ManiacBot' in p.confirmed_extreme_archetypes


def test_bluff_catch_sizing_complete():
    """All seven cells of the call-prob matrix are populated."""
    s = cfg.CONFIG.bluff_catch.sizing
    # medium_made — descending across bet/pot bands
    assert s.medium_made_le_50_pct > s.medium_made_le_100_pct
    assert s.medium_made_le_100_pct > s.medium_made_le_200_pct
    assert s.medium_made_le_200_pct > s.medium_made_gt_200_pct
    # weak_made — descending across bet/pot bands
    assert s.weak_made_le_33_pct > s.weak_made_le_67_pct
    assert s.weak_made_le_67_pct > s.weak_made_gt_67_pct


def test_bluff_catch_dampener_caps():
    """All dampener multipliers in (0, 1]."""
    d = cfg.CONFIG.bluff_catch.dampener
    for value in (
        d.street_river,
        d.street_turn,
        d.street_flop,
        d.dangerous_texture_mult,
        d.weak_made_on_paired_mult,
    ):
        assert 0 < value <= 1
    # River is harsher than turn is harsher than flop.
    assert d.street_river < d.street_turn
    assert d.street_turn < d.street_flop or d.street_turn == d.street_flop


def test_reload_for_testing_replaces_singleton():
    """Fixtures can override config without touching the YAML file."""
    original = cfg.CONFIG
    custom = {
        'exploitation_clamps': {
            'default_max_total_shift': 0.1,
            'medium_max_total_shift': 0.2,
            'extreme_max_total_shift': 0.3,
        },
        'sample_thresholds': {
            'medium_min_opportunities': 10,
            'extreme_min_opportunities': 20,
        },
        'signal_thresholds': {
            'medium_af_postflop': 1.0,
            'extreme_af_postflop': 2.0,
            'medium_all_in_per_facing_bet': 0.01,
            'extreme_all_in_per_facing_bet': 0.02,
            'medium_postflop_jam_open_rate': 0.01,
            'extreme_postflop_jam_open_rate': 0.02,
        },
        'tier_decay': {'window_size': 5, 'require_recent_window_full': 3},
        'benchmark_prior': {
            'enabled': True,
            'confirmed_extreme_archetypes': ['TestBot'],
        },
        'bluff_catch': {
            'medium_made': {
                'cap_le_50_pct': 0.9,
                'cap_le_100_pct': 0.7,
                'cap_le_200_pct': 0.4,
                'cap_gt_200_pct': 0.1,
            },
            'weak_made': {
                'cap_le_33_pct': 0.6,
                'cap_le_67_pct': 0.3,
                'cap_gt_67_pct': 0.05,
            },
            'dampener': {
                'street_river': 0.5,
                'street_turn': 0.8,
                'street_flop': 1.0,
                'dangerous_texture_mult': 0.4,
                'weak_made_on_paired_mult': 0.4,
            },
        },
    }
    try:
        cfg.reload_for_testing(custom)
        assert cfg.CONFIG.exploitation_clamps.default_max_total_shift == 0.1
        assert cfg.CONFIG.benchmark_prior.enabled is True
        assert cfg.CONFIG.benchmark_prior.confirmed_extreme_archetypes == ('TestBot',)
    finally:
        cfg.reset_for_testing()
    # Original singleton restored.
    assert cfg.CONFIG.exploitation_clamps.default_max_total_shift == 0.4
    assert cfg.CONFIG.benchmark_prior.enabled is False
