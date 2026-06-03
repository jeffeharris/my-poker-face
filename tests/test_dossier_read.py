"""Tests for the dossier "the read" (Part B2).

`build_the_read` is a thin presentation layer over the tiered-bot exploitation
detectors. These tests drive it through the real lifetime→tendencies
reconstruction (`_tendencies_from_lifetime`, which sets the v125 deep + v126
preflop counters), so they also confirm the station/nit detectors actually
fire from durable dossier data (they gate on vpip_per_voluntary_opportunity,
which only derives once the v126 preflop opportunity counts are stored).
"""

from flask_app.routes.character_routes import _tendencies_from_lifetime
from flask_app.services.dossier_read import build_the_read


def _counts(**over):
    base = {
        'hands_observed': 0,
        'hands_dealt': 0,
        'vpip_count': 0,
        'pfr_count': 0,
        'bet_raise_count': 0,
        'call_count': 0,
        'showdowns_seen': 0,
        'showdowns_won': 0,
        'all_in_count': 0,
        'fold_to_cbet_count': 0,
        'cbet_faced_count': 0,
        'cbet_attempt_count': 0,
        'postflop_seen_as_pfr_count': 0,
        'barrel_count': 0,
        'barrel_opportunity_count': 0,
        'third_barrel_count': 0,
        'third_barrel_opportunity_count': 0,
        'postflop_bet_raise_count': 0,
        'postflop_call_count': 0,
        'equity_betting_count': 0,
        'equity_raising_count': 0,
        'equity_calling_count': 0,
        'equity_betting_sum': 0.0,
        'equity_raising_sum': 0.0,
        'equity_calling_sum': 0.0,
        'preflop_voluntary_action_count': 0,
        'preflop_voluntary_opportunities': 0,
        'preflop_open_raise_count': 0,
        'preflop_open_opportunities': 0,
    }
    base.update(over)
    return base


def _read(counts):
    t = _tendencies_from_lifetime(counts)
    assert t is not None
    return build_the_read(t)


def test_pure_station_read():
    # vpip_per_vol 0.9 (>0.70), AF 0.10 (<0.80), no jams → pure station.
    read = _read(
        _counts(
            hands_observed=120,
            hands_dealt=120,
            bet_raise_count=3,
            call_count=30,
            preflop_voluntary_action_count=90,
            preflop_voluntary_opportunities=100,
        )
    )
    assert read['archetype'] == {'id': 'pure_station', 'label': 'Calling Station'}
    patterns = {tip['pattern'] for tip in read['tips']}
    assert 'hyper_passive' in patterns
    # The station read tells you to stop bluffing.
    text = next(t['text'] for t in read['tips'] if t['pattern'] == 'hyper_passive')
    assert 'bluff' in text.lower()


def test_sticky_jammer_read():
    # Station ratios + a real all-in frequency (0.10 > 0.05) → sticky jammer.
    read = _read(
        _counts(
            hands_observed=120,
            hands_dealt=120,
            bet_raise_count=3,
            call_count=30,
            all_in_count=12,
            preflop_voluntary_action_count=90,
            preflop_voluntary_opportunities=100,
        )
    )
    assert read['archetype']['id'] == 'sticky_jammer'
    patterns = {tip['pattern'] for tip in read['tips']}
    assert {'hyper_passive', 'passive_with_jams'} <= patterns


def test_high_fold_to_cbet_read():
    # Foldy to c-bets (0.9, 20 samples), otherwise unremarkable → barrel tip,
    # no archetype badge.
    read = _read(
        _counts(
            hands_observed=80,
            hands_dealt=80,
            bet_raise_count=10,
            call_count=10,
            fold_to_cbet_count=18,
            cbet_faced_count=20,
            preflop_voluntary_action_count=50,
            preflop_voluntary_opportunities=100,
        )
    )
    assert read['archetype'] is None
    patterns = {tip['pattern'] for tip in read['tips']}
    assert 'high_fold_to_cbet' in patterns
    text = next(t['text'] for t in read['tips'] if t['pattern'] == 'high_fold_to_cbet')
    assert 'barrel' in text.lower()


def test_maniac_read():
    # AF 8.0 (>3.5) → hyper-aggressive maniac.
    read = _read(
        _counts(
            hands_observed=60,
            hands_dealt=60,
            bet_raise_count=40,
            call_count=5,
            preflop_voluntary_action_count=80,
            preflop_voluntary_opportunities=100,
        )
    )
    assert read['archetype'] == {'id': 'hyper_aggressive', 'label': 'Maniac'}
    assert any(t['pattern'] == 'hyper_aggressive' for t in read['tips'])


def test_balanced_player_has_no_read():
    # AF ~1.0, mid VPIP, no c-bet samples → nothing fires.
    read = _read(
        _counts(
            hands_observed=120,
            hands_dealt=120,
            bet_raise_count=10,
            call_count=10,
            preflop_voluntary_action_count=50,
            preflop_voluntary_opportunities=100,
        )
    )
    assert read['archetype'] is None
    assert read['tips'] == []


def test_cold_start_floor_suppresses_archetype():
    # Below MIN_HANDS_DEFAULT (15) the archetype classifier returns None even
    # on extreme ratios — no guessing on noise.
    read = _read(
        _counts(
            hands_observed=10,
            hands_dealt=10,
            bet_raise_count=1,
            call_count=12,
            preflop_voluntary_action_count=9,
            preflop_voluntary_opportunities=10,
        )
    )
    assert read['archetype'] is None


def test_intensity_attached_when_available():
    read = _read(
        _counts(
            hands_observed=80,
            hands_dealt=80,
            bet_raise_count=10,
            call_count=10,
            fold_to_cbet_count=19,
            cbet_faced_count=20,
            preflop_voluntary_action_count=50,
            preflop_voluntary_opportunities=100,
        )
    )
    tip = next(t for t in read['tips'] if t['pattern'] == 'high_fold_to_cbet')
    assert tip['intensity'] is not None
    assert 0.0 <= tip['intensity'] <= 1.0
