"""Tests for the Phase 2 dossier scouting gate (the grind).

Covers the derived unlock schedule and the response redaction: below the
floor everything earnable is classified; items unlock as observed hands
cross their thresholds; always-free sections are never touched.
"""

from flask_app.services.dossier_scouting import (
    FLOOR_HANDS,
    INFORMANT_SECTIONS,
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
        'deeper_reads': {
            'fold_to_cbet': 0.6,
            'cbet_attempt_rate': 0.7,
            'barrel_frequency': 0.4,
            'third_barrel_frequency': 0.3,
            'all_in_frequency': 0.05,
            'aggression_factor_postflop': 2.5,
            'equity_when_betting': 0.65,
            'equity_when_raising': 0.72,
            'equity_when_calling': 0.55,
            'lifetime': True,
        },
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

    # Earnable reads gone — observation collapses to None when fully redacted.
    assert resp['observation'] is None
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


# --- Informant (Phase 3) ----------------------------------------------------

# --- Tier-2 deep reads (B1) -------------------------------------------------

def test_deep_reads_locked_until_their_tiers():
    resp = _full_response()
    # 250 hands: fold_to_cbet (220) unlocked; c-bet (260) + the rest still not.
    apply_scouting_gate(resp, hands_observed=250)
    assert resp['deeper_reads']['fold_to_cbet'] == 0.6
    assert resp['deeper_reads']['cbet_attempt_rate'] is None
    assert resp['deeper_reads']['barrel_frequency'] is None
    assert resp['deeper_reads']['equity_when_betting'] is None
    s = resp['scouting']
    assert 'fold_to_cbet' in s['unlocked']
    assert 'cbet_pct' not in s['unlocked']


def test_deep_reads_below_floor_collapse_to_none():
    resp = _full_response()
    apply_scouting_gate(resp, hands_observed=5)
    # Every deep field locked → the block collapses (client renders nothing).
    assert resp['deeper_reads'] is None


def test_deep_reads_full_unlock_keeps_everything():
    resp = _full_response()
    apply_scouting_gate(resp, hands_observed=10_000)
    dr = resp['deeper_reads']
    assert dr['fold_to_cbet'] == 0.6
    assert dr['cbet_attempt_rate'] == 0.7
    assert dr['barrel_frequency'] == 0.4
    assert dr['third_barrel_frequency'] == 0.3
    assert dr['aggression_factor_postflop'] == 2.5
    assert dr['equity_when_betting'] == 0.65


def test_barrel_tier_gates_both_barrel_fields():
    resp = _full_response()
    # 400 unlocks 'barrel' (both turn + river barrel); 480 polarization not yet.
    apply_scouting_gate(resp, hands_observed=400)
    assert resp['deeper_reads']['barrel_frequency'] == 0.4
    assert resp['deeper_reads']['third_barrel_frequency'] == 0.3
    assert resp['deeper_reads']['equity_when_betting'] is None


def test_informant_deep_reads_section_unlocks_all_deep_items():
    s = compute_scouting(0, purchased_sections={'deep_reads'})
    for item in INFORMANT_SECTIONS['deep_reads']['items']:
        assert item in s['unlocked']
    assert 'deep_reads' not in {o['id'] for o in s['informant_offers']}


def test_informant_section_unlocks_items_bypassing_floor():
    # 0 hands (below floor), but bought the 'read' section.
    s = compute_scouting(0, purchased_sections={'read'})
    for item in INFORMANT_SECTIONS['read']['items']:
        assert item in s['unlocked']
    # The bought section is no longer offered; others still are.
    offer_ids = {o['id'] for o in s['informant_offers']}
    assert 'read' not in offer_ids
    assert 'track_record' in offer_ids


def test_informant_offers_exclude_grind_unlocked_sections():
    # Enough hands to grind-unlock the whole 'read' section (max threshold 60).
    s = compute_scouting(60)
    offer_ids = {o['id'] for o in s['informant_offers']}
    assert 'read' not in offer_ids  # fully unlocked by grind, nothing to sell
    assert 'track_record' in offer_ids  # still locked (needs 100+)


def test_gate_respects_purchased_section():
    resp = _full_response()
    # Below floor, but bought 'track_record' — its reads survive the gate.
    apply_scouting_gate(resp, hands_observed=5, purchased_sections={'track_record'})
    assert resp['cash_pair_stats'] is not None
    assert resp['pressure_summary'] is not None
    assert resp['memorable_hands']
    # Un-bought, un-grinded reads still redacted.
    assert resp['observation'] is None
    assert resp['ai_bankroll'] is None
