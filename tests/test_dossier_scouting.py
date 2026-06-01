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
            'limp_rate': 0.25,
            'showdown_win_rate': 0.52,
            'sizing_polarization_score': 0.3,
            'fold_to_big_bet': 0.65,
            'equity_when_betting': 0.65,
            'equity_when_raising': 0.72,
            'equity_when_calling': 0.55,
            'lifetime': True,
        },
        'the_read': [
            {'pattern': 'hyper_passive', 'text': 'value-bet thin, stop bluffing',
             'intensity': 0.8},
        ],
        'archetype': {'id': 'pure_station', 'label': 'Calling Station'},
        'temperament': {
            'tilt_score': 0.7, 'tilt_label': 'On tilt',
            'poise': 0.3, 'expressiveness': 0.8,
            'lines': ['Rattles easily — keep the pressure on.'],
        },
        'field_position': {
            'vpip_pct': 77, 'vpip_label': 'Looser than 77% of the field',
            'af_pct': 60, 'af_label': 'More aggressive than 60% of the field',
        },
        'relationship_history': {
            'line': "Bad blood.",
            'defining': {'event': 'cooler', 'label': 'cooler',
                         'impact_score': 0.92, 'narrative': 'kings into aces'},
            'clash': [{'event': 'cooler', 'label': 'cooler', 'count': 2}],
            'banter': [],
        },
    }


# A counts dict with every opportunity denominator saturated — used wherever a
# test wants "fully scouted" so the Tier-2 sample gates are satisfied too.
def _maxed(hands=10_000):
    return {
        'hands_observed': hands,
        'cbet_faced_count': 100, 'postflop_seen_as_pfr_count': 100,
        'postflop_bet_raise_count': 100, 'postflop_call_count': 100,
        'barrel_opportunity_count': 100, 'equity_betting_count': 100,
        'equity_raising_count': 100, 'equity_calling_count': 100,
        'preflop_open_opportunities': 100, 'showdowns_seen': 100,
        'big_bet_faced_count': 100, 'equity_betting_big_count': 100,
        'equity_betting_small_count': 100,
    }


def test_floor_equals_lowest_threshold():
    assert FLOOR_HANDS == min(tier.hands for tier in SCOUTING_SCHEDULE)


def test_below_floor_locks_everything_earnable():
    s = compute_scouting(10)
    assert s['floor_met'] is False
    assert s['unlocked'] == []
    assert {e['id'] for e in s['locked']} == {tier.id for tier in SCOUTING_SCHEDULE}


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
    # Everything earned eventually — needs both the hand floor AND the Tier-2
    # opportunity counts saturated.
    full = compute_scouting(_maxed())
    assert full['locked'] == []


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
    # _maxed(): hand floor + Tier-2 opportunity samples all satisfied.
    apply_scouting_gate(resp, _maxed())
    assert resp['observation']['aggression_factor'] == 1.25
    assert resp['personality']['anchors']['aggression'] == 0.7
    assert resp['cash_pair_stats']['cumulative_pnl'] == 1500
    assert resp['memorable_hands']
    assert resp['ai_bankroll'] == 5000
    assert resp['scouting']['locked'] == []


# --- Informant (Phase 3) ----------------------------------------------------

# --- Tier-2 deep reads (B1) — HYBRID gate (hands AND opportunity samples) ----

def test_deep_reads_hybrid_unlock_needs_hand_floor_and_samples():
    resp = _full_response()
    # Hand floor met for fold_to_cbet (180) with 20 c-bets faced → unlocked;
    # c-bet% has hand floor met but too few flops-as-raiser → still locked.
    apply_scouting_gate(resp, {
        'hands_observed': 250,
        'cbet_faced_count': 25,
        'postflop_seen_as_pfr_count': 5,
    })
    assert resp['deeper_reads']['fold_to_cbet'] == 0.6
    assert resp['deeper_reads']['cbet_attempt_rate'] is None
    assert resp['deeper_reads']['barrel_frequency'] is None
    s = resp['scouting']
    assert 'fold_to_cbet' in s['unlocked']
    assert 'cbet_pct' not in s['unlocked']


def test_high_hands_low_samples_stays_locked():
    """The honesty fix: 300 hands but only 4 c-bets faced → fold_to_cbet stays
    locked (the stat would be noise). The locked descriptor carries the
    opportunity progress for the UI."""
    s = compute_scouting({'hands_observed': 300, 'cbet_faced_count': 4})
    assert 'fold_to_cbet' not in s['unlocked']
    lock = next(l for l in s['locked'] if l['id'] == 'fold_to_cbet')
    assert lock['sample_min'] == 20
    assert lock['samples_observed'] == 4
    assert lock['sample_noun'] == 'c-bets faced'


def test_samples_met_but_hand_floor_not_stays_locked():
    # Plenty of c-bet samples but under the 180-hand floor → still locked.
    s = compute_scouting({'hands_observed': 100, 'cbet_faced_count': 50})
    assert 'fold_to_cbet' not in s['unlocked']


def test_scalar_input_keeps_sample_gated_tiers_locked():
    # Legacy scalar (no sample data) can't satisfy a Tier-2 gate, but the
    # hand-only tiers still unlock.
    s = compute_scouting(10_000)
    assert 'play_style' in s['unlocked']
    assert 'all_in_freq' in s['unlocked']      # hand-only Tier-2 tier
    assert 'fold_to_cbet' not in s['unlocked']  # needs c-bet samples


def test_deep_reads_below_floor_collapse_to_none():
    resp = _full_response()
    apply_scouting_gate(resp, hands_observed=5)
    # Every deep field locked → the block collapses (client renders nothing).
    assert resp['deeper_reads'] is None


def test_deep_reads_full_unlock_keeps_everything():
    resp = _full_response()
    apply_scouting_gate(resp, _maxed())
    dr = resp['deeper_reads']
    assert dr['fold_to_cbet'] == 0.6
    assert dr['cbet_attempt_rate'] == 0.7
    assert dr['barrel_frequency'] == 0.4
    assert dr['third_barrel_frequency'] == 0.3
    assert dr['aggression_factor_postflop'] == 2.5
    assert dr['limp_rate'] == 0.25
    assert dr['showdown_win_rate'] == 0.52
    assert dr['sizing_polarization_score'] == 0.3
    assert dr['fold_to_big_bet'] == 0.65
    assert dr['equity_when_betting'] == 0.65


def test_barrel_tier_gates_both_barrel_fields():
    resp = _full_response()
    # barrel: hand floor 220 + 12 barrel spots → both barrel fields unlock;
    # polarization needs equity samples (none here) → stays locked.
    apply_scouting_gate(resp, {
        'hands_observed': 400,
        'barrel_opportunity_count': 15,
    })
    assert resp['deeper_reads']['barrel_frequency'] == 0.4
    assert resp['deeper_reads']['third_barrel_frequency'] == 0.3
    assert resp['deeper_reads']['equity_when_betting'] is None


# --- B2 "the read" + archetype badge ----------------------------------------

def test_read_and_archetype_gate_by_tier():
    resp = _full_response()
    # 150 hands: archetype (120) unlocked, the read (200) still locked.
    apply_scouting_gate(resp, hands_observed=150)
    assert resp['archetype'] == {'id': 'pure_station', 'label': 'Calling Station'}
    assert resp['the_read'] == []
    s = resp['scouting']
    assert 'archetype_badge' in s['unlocked']
    assert 'the_read' not in s['unlocked']


def test_read_unlocks_at_its_tier():
    resp = _full_response()
    apply_scouting_gate(resp, hands_observed=250)
    assert resp['the_read'] and resp['the_read'][0]['pattern'] == 'hyper_passive'
    assert resp['archetype']['id'] == 'pure_station'


def test_read_below_floor_redacted():
    resp = _full_response()
    apply_scouting_gate(resp, hands_observed=5)
    assert resp['the_read'] == []
    assert resp['archetype'] is None


# --- B3 temperament + B4 field standing -------------------------------------

def test_temperament_and_field_gate_by_tier():
    resp = _full_response()
    # 95 hands: field standing (90) unlocked, temperament (100) still locked.
    apply_scouting_gate(resp, hands_observed=95)
    assert resp['field_position']['vpip_pct'] == 77
    assert resp['temperament'] is None
    s = resp['scouting']
    assert 'field_position' in s['unlocked']
    assert 'temperament' not in s['unlocked']


def test_temperament_unlocks_at_its_tier():
    resp = _full_response()
    apply_scouting_gate(resp, hands_observed=110)
    assert resp['temperament']['tilt_label'] == 'On tilt'
    assert resp['field_position']['af_pct'] == 60


def test_temperament_and_field_below_floor_redacted():
    resp = _full_response()
    apply_scouting_gate(resp, hands_observed=5)
    assert resp['temperament'] is None
    assert resp['field_position'] is None


def test_rivalry_gates_with_track_record_tier():
    resp = _full_response()
    # 130 hands: below the rivalry/memorable tier (140) → history redacted.
    apply_scouting_gate(resp, hands_observed=130)
    assert resp['relationship_history'] is None
    assert 'rivalry' not in resp['scouting']['unlocked']
    # 150 hands: unlocked.
    resp2 = _full_response()
    apply_scouting_gate(resp2, hands_observed=150)
    assert resp2['relationship_history']['line'] == 'Bad blood.'
    assert 'rivalry' in resp2['scouting']['unlocked']


def test_informant_track_record_section_unlocks_rivalry():
    s = compute_scouting(0, purchased_sections={'track_record'})
    assert 'rivalry' in s['unlocked']


def test_informant_tells_section_unlocks_items():
    s = compute_scouting(0, purchased_sections={'tells'})
    for item in INFORMANT_SECTIONS['tells']['items']:
        assert item in s['unlocked']


def test_informant_tactical_read_section_unlocks_read_items():
    s = compute_scouting(0, purchased_sections={'tactical_read'})
    for item in INFORMANT_SECTIONS['tactical_read']['items']:
        assert item in s['unlocked']
    assert 'tactical_read' not in {o['id'] for o in s['informant_offers']}


def test_informant_deep_reads_section_unlocks_all_deep_items():
    # The informant bypasses BOTH gates (hands and samples).
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
