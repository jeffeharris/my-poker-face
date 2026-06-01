#!/usr/bin/env python3
"""Unit tests for the proactive-coach prefetch coordination.

Covers the decision-signature keying and the cache hand-off (take_cached_tip)
without any LLM call — the part that's prone to subtle staleness/double-charge
bugs. The actual tip generation is the coach system's concern.
"""

import threading

from flask_app.services.coach_prefetch import (
    CACHE_KEY,
    decision_signature,
    take_cached_tip,
)


class _Player:
    def __init__(self, bet=0):
        self.bet = bet


class _GS:
    def __init__(self, *, idx=0, highest_bet=200, pot=500, board=(), bet=0):
        self.current_player = _Player(bet)
        self.current_player_idx = idx
        self.highest_bet = highest_bet
        self.pot = {'total': pot}
        self.community_cards = board


class _SM:
    def __init__(self, gs, phase='FLOP'):
        self.game_state = gs
        self.current_phase = phase


def _game_data(gs, phase='FLOP'):
    return {'state_machine': _SM(gs, phase), 'memory_manager': None}


def test_signature_is_stable_for_same_decision():
    gd = _game_data(_GS())
    assert decision_signature(gd) == decision_signature(gd)


def test_signature_changes_when_the_decision_changes():
    base = decision_signature(_game_data(_GS(highest_bet=200)))
    assert base != decision_signature(_game_data(_GS(highest_bet=600)))  # faces a bigger bet
    assert base != decision_signature(_game_data(_GS(board=('Kc', '7d', '2h'))))  # new street
    assert base != decision_signature(_game_data(_GS(), phase='TURN'))


def test_take_cached_tip_none_when_no_cache():
    assert take_cached_tip(_game_data(_GS())) is None


def test_take_cached_tip_returns_payload_on_signature_match():
    gd = _game_data(_GS())
    ev = threading.Event()
    ev.set()
    payload = {'answer': 'fold', 'coach_action': 'fold', 'coach_raise_to': None, 'stats': {}}
    gd[CACHE_KEY] = {'sig': decision_signature(gd), 'event': ev, 'payload': payload}
    assert take_cached_tip(gd) is payload


def test_take_cached_tip_none_on_signature_mismatch():
    gd = _game_data(_GS(highest_bet=200))
    ev = threading.Event()
    ev.set()
    # Cache entry was built for a different decision (different highest_bet).
    stale_sig = decision_signature(_game_data(_GS(highest_bet=600)))
    gd[CACHE_KEY] = {'sig': stale_sig, 'event': ev, 'payload': {'answer': 'stale'}}
    assert take_cached_tip(gd) is None


def test_take_cached_tip_waits_for_in_flight_then_returns():
    gd = _game_data(_GS())
    ev = threading.Event()  # not set — simulates an in-flight prefetch
    entry = {'sig': decision_signature(gd), 'event': ev, 'payload': None}
    gd[CACHE_KEY] = entry

    def _finish():
        entry['payload'] = {'answer': 'call'}
        ev.set()

    threading.Timer(0.05, _finish).start()
    result = take_cached_tip(gd, timeout=2.0)
    assert result == {'answer': 'call'}
