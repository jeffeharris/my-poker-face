"""Tests for the Phase 2 dossier scouting gate (the grind).

Covers the derived unlock schedule and the response redaction: below the
floor everything earnable is classified; items unlock as observed hands
cross their thresholds; always-free sections are never touched.
"""

from flask_app.services.dossier_scouting import (
    FLOOR_HANDS,
    SCOUTING_SCHEDULE,
    apply_scouting_gate,
    compute_scouting,
)


def _full_response():
    """A dossier response with every gateable field populated."""
    return {
        'personality_id': 'greg',
        'personality': {
            'name': 'Greg',
            'attitude': 'smug',
            'anchors': {
                'aggression': 0.7, 'looseness': 0.6, 'poise': 0.4,
                'expressiveness': 0.5, 'risk': 0.8,
            },
        },
        'emotion': 'cocky',
        'observation': {
            'hands_observed': 200, 'vpip': 0.3, 'pfr': 0.2,
            'aggression_factor': 1.25, 'play_style': 'loose-aggressive',
        },
        'pressure_summary': {'total_events': 5, 'signature_move': 'check-raise'},
        'ai_bankroll': 5000,
        'stake_summary': {
            'as_borrower': {'carry_count': 1, 'total_carried': 200},
            'as_staker': {'carry_count': 0, 'total_owed_to_them': 0},
        },
        'relationship': {'heat': 0.5, 'respect': 0.6, 'likability': 0.4},
        'cash_pair_stats': {'cumulative_pnl': 1500, 'hands_played_cash': 80},
        'memorable_hands': [{'hand_id': 1, 'narrative': 'big bluff'}],
        'note': 'bluffs the river',
    }


def test_floor_equals_lowest_threshold():
    assert FLOOR_HANDS == min(t for _, _, t in SCOUTING_SCHEDULE)


def test_below_floor_locks_everything_earnable():
    s = compute_scouting(10)
    assert s['floor_met'] is False
    assert s['unlocked'] == []
    assert {e['id'] for e in s['locked']} == {i for i, _, _ in SCOUTING_SCHEDULE}


def test_floor_unlocks_first_reads():
    s = compute_scouting(25)
    assert s['floor_met'] is True
    assert 'play_style' in s['unlocked']
    assert 'vpip' in s['unlocked']
    assert 'pfr' not in s['unlocked']          # 40
    assert 'aggression_factor' not in s['unlocked']  # 60


def test_progressive_unlock():
    assert 'pfr' in compute_scouting(40)['unlocked']
    assert 'aggression_factor' in compute_scouting(60)['unlocked']
    assert 'behavioral_index' in compute_scouting(80)['unlocked']
    full = compute_scouting(10_000)
    assert full['locked'] == []                # everything earned eventually


def test_gate_below_floor_redacts_all_earnable():
    resp = _full_response()
    apply_scouting_gate(resp, hands_observed=5)

    # Earnable reads gone.
    assert resp['observation']['vpip'] is None
    assert resp['observation']['play_style'] is None
    assert all(v is None for v in resp['personality']['anchors'].values())
    assert resp['cash_pair_stats'] is None
    assert resp['pressure_summary'] is None
    assert resp['memorable_hands'] == []
    assert resp['ai_bankroll'] is None
    assert resp['stake_summary']['as_borrower']['total_carried'] == 0

    # Always-free reads untouched.
    assert resp['personality']['attitude'] == 'smug'
    assert resp['relationship']['heat'] == 0.5
    assert resp['note'] == 'bluffs the river'
    assert resp['emotion'] == 'cocky'

    assert resp['scouting']['floor_met'] is False


def test_gate_partial_unlock_reveals_some_redacts_rest():
    resp = _full_response()
    apply_scouting_gate(resp, hands_observed=50)  # >=25,40; <60,80,100...

    # Unlocked: play_style, vpip, pfr
    assert resp['observation']['play_style'] == 'loose-aggressive'
    assert resp['observation']['vpip'] == 0.3
    assert resp['observation']['pfr'] == 0.2
    # Still locked: aggression_factor, behavioral_index, track_record, ...
    assert resp['observation']['aggression_factor'] is None
    assert all(v is None for v in resp['personality']['anchors'].values())
    assert resp['cash_pair_stats'] is None
    assert resp['memorable_hands'] == []

    s = resp['scouting']
    assert s['floor_met'] is True
    assert 'pfr' in s['unlocked'] and 'aggression_factor' not in s['unlocked']


def test_gate_full_unlock_keeps_everything():
    resp = _full_response()
    apply_scouting_gate(resp, hands_observed=10_000)
    assert resp['observation']['aggression_factor'] == 1.25
    assert resp['personality']['anchors']['aggression'] == 0.7
    assert resp['cash_pair_stats']['cumulative_pnl'] == 1500
    assert resp['memorable_hands']
    assert resp['ai_bankroll'] == 5000
    assert resp['scouting']['locked'] == []
