"""Tests for the dossier soft signals (B3 temperament + B4 field standing)."""

from flask_app.services.dossier_signals import build_temperament, field_position

# ── B3: temperament (the emotional read) ────────────────────────────────────


def test_rattly_and_tilting_reads():
    t = build_temperament(
        {'total_events': 10, 'tilt_score': 0.72},
        {'poise': 0.2, 'expressiveness': 0.85},
    )
    assert t['tilt_label'] == 'On tilt'
    assert t['tilt_score'] == 0.72
    joined = ' '.join(t['lines']).lower()
    assert 'rattles easily' in joined  # low poise
    assert 'runs hot' in joined  # high tilt
    assert 'table talk' in joined  # high expressiveness


def test_composed_stone_faced_read():
    t = build_temperament(
        {'total_events': 8, 'tilt_score': 0.1},
        {'poise': 0.85, 'expressiveness': 0.2},
    )
    assert t['tilt_label'] == 'Composed'
    joined = ' '.join(t['lines']).lower()
    assert 'hard to rattle' in joined
    assert 'stone-faced' in joined


def test_tilt_suppressed_below_min_events():
    # Fewer than MIN_TILT_EVENTS pressure events → no tilt gauge, but the
    # static anchors still produce a read.
    t = build_temperament(
        {'total_events': 1, 'tilt_score': 0.9},
        {'poise': 0.2, 'expressiveness': 0.5},
    )
    assert t['tilt_score'] is None
    assert t['tilt_label'] is None
    assert any('rattles easily' in line.lower() for line in t['lines'])


def test_temperament_none_when_nothing_to_say():
    assert build_temperament(None, None) is None
    assert build_temperament({'total_events': 0}, {}) is None


def test_temperament_anchors_only_no_pressure():
    t = build_temperament(None, {'poise': 0.5, 'expressiveness': 0.5})
    assert t is not None
    assert t['tilt_score'] is None
    assert t['poise'] == 0.5
    # Mid anchors hit no advice threshold — gauge only, no lines.
    assert t['lines'] == []


# ── B4: field-relative percentiles ──────────────────────────────────────────


def test_field_position_loose_and_aggressive():
    fp = field_position(0.45, 1.0)
    # 20 of 26 field VPIPs are below 0.45; 20 of 26 AFs below 1.0.
    assert fp['vpip_pct'] == 77
    assert fp['vpip_label'] == 'Looser than 77% of the field'
    assert fp['af_pct'] == 77
    assert 'more aggressive than 77%' in fp['af_label'].lower()


def test_field_position_tight_and_passive():
    fp = field_position(0.10, 0.50)
    # Below the tightest / most passive in the field → 0th percentile.
    assert fp['vpip_pct'] == 0
    assert fp['vpip_label'] == 'Tighter than 100% of the field'
    assert fp['af_pct'] == 0
    assert 'more passive than 100%' in fp['af_label'].lower()


def test_field_position_partial_inputs():
    assert field_position(0.30, None)['vpip_label']
    assert 'af_label' not in field_position(0.30, None)
    assert field_position(None, None) is None
