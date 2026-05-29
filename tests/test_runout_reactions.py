"""Unit tests for the per-card run-out reaction schedule (Phase 2 director input).

These pin the *structure* of the schedule — the ordered per-card ``steps`` the
mobile ``useRunoutDirector`` walks, the unchanged street-granular
``reactions_by_phase``, and the no-card socket payload. Equity is mocked so the
per-card branching logic is tested deterministically, independent of eval7's
Monte Carlo variance.
"""

from unittest.mock import patch

from poker.poker_game import Card, Player, PokerGameState
from poker.runout_reactions import (
    RunoutStep,
    compute_runout_reactions,
    runout_schedule_payload,
)


class _FakeEquityResult:
    def __init__(self, equities):
        self.equities = equities


class _FakeCalculator:
    """Returns controlled equities keyed by the number of board cards, so each
    run-out step has a known equity delta. Alice = pocket aces, Bob = trash."""

    # board length -> {name: equity}
    _BY_BOARD_LEN = {
        0: {'Alice': 0.85, 'Bob': 0.15},  # INITIAL: Alice smug, Bob nervous
        1: {'Alice': 0.50, 'Bob': 0.50},  # flop card 1: Alice -0.35 angry, Bob +0.35 elated
        2: {'Alice': 0.90, 'Bob': 0.10},  # flop card 2: Alice +0.40 elated, Bob -0.40 angry
        3: {'Alice': 0.92, 'Bob': 0.08},  # flop card 3: +0.02 — below threshold, no reaction
        4: {'Alice': 0.95, 'Bob': 0.05},  # turn: +0.03 — below threshold
        5: {'Alice': 1.00, 'Bob': 0.00},  # river: +0.05 — below threshold
    }

    def __init__(self, monte_carlo_iterations=2000):
        self.monte_carlo_iterations = monte_carlo_iterations

    def calculate_equity(self, players_hands, board):
        return _FakeEquityResult(dict(self._BY_BOARD_LEN[len(board)]))


def _all_in_preflop_state():
    """Two AI all-in preflop + one folded human, with a deterministic 5-card runout."""
    alice = Player(name='Alice', stack=0, is_human=False, is_all_in=True,
                   hand=(Card('A', 'spades'), Card('A', 'diamonds')))
    bob = Player(name='Bob', stack=0, is_human=False, is_all_in=True,
                 hand=(Card('7', 'clubs'), Card('2', 'diamonds')))
    human = Player(name='Human', stack=100, is_human=True, is_folded=True,
                   hand=(Card('5', 'hearts'), Card('6', 'hearts')))
    # Top 5 of the deck become flop/turn/river (cards themselves are irrelevant —
    # equity is mocked — but the deck must hold ≥5 so all streets are scheduled).
    deck = (
        Card('K', 'spades'), Card('Q', 'diamonds'), Card('J', 'clubs'),
        Card('3', 'hearts'), Card('4', 'spades'),
    )
    return PokerGameState(players=(alice, bob, human), deck=deck, community_cards=())


def test_steps_are_per_card_and_ordered():
    state = _all_in_preflop_state()
    with patch('poker.runout_reactions.EquityCalculator', _FakeCalculator):
        schedule = compute_runout_reactions(state, ai_controllers={})

    # INITIAL → flop x3 → TURN → RIVER → SHOWDOWN
    assert [(s.phase, s.card_index) for s in schedule.steps] == [
        ('INITIAL', 0),
        ('FLOP', 0), ('FLOP', 1), ('FLOP', 2),
        ('TURN', 0),
        ('RIVER', 0),
        ('SHOWDOWN', 0),
    ]


def test_per_card_reactions_track_each_flop_card():
    state = _all_in_preflop_state()
    with patch('poker.runout_reactions.EquityCalculator', _FakeCalculator):
        schedule = compute_runout_reactions(state, ai_controllers={})

    by_key = {(s.phase, s.card_index): s for s in schedule.steps}

    def emo(step):
        return {r.player_name: r.emotion for r in step.reactions}

    # Absolute-equity read at reveal
    assert emo(by_key[('INITIAL', 0)]) == {'Alice': 'smug', 'Bob': 'nervous'}
    # Flop card 1: Alice craters (-0.35), Bob spikes (+0.35)
    assert emo(by_key[('FLOP', 0)]) == {'Alice': 'angry', 'Bob': 'elated'}
    # Flop card 2: the swing reverses
    assert emo(by_key[('FLOP', 1)]) == {'Alice': 'elated', 'Bob': 'angry'}
    # Flop card 3: tiny move — nobody reacts (this is the per-card payoff:
    # a card that moves nothing stays flat instead of riding the street delta)
    assert by_key[('FLOP', 2)].reactions == []
    # Showdown: locked up
    assert emo(by_key[('SHOWDOWN', 0)]) == {'Alice': 'elated', 'Bob': 'angry'}


def test_legacy_street_view_collapses_the_flop():
    """reactions_by_phase stays street-granular (start→end-of-flop delta). Here the
    flop nets only +0.07 for Alice, below threshold — so the volatile mid-flop swing
    the per-card steps captured is (correctly) absent from the legacy street view."""
    state = _all_in_preflop_state()
    with patch('poker.runout_reactions.EquityCalculator', _FakeCalculator):
        schedule = compute_runout_reactions(state, ai_controllers={})

    assert 'FLOP' not in schedule.reactions_by_phase  # net street delta < threshold
    assert 'INITIAL' in schedule.reactions_by_phase
    assert 'SHOWDOWN' in schedule.reactions_by_phase


def test_payload_carries_no_cards_and_excludes_human():
    state = _all_in_preflop_state()
    with patch('poker.runout_reactions.EquityCalculator', _FakeCalculator):
        schedule = compute_runout_reactions(state, ai_controllers={})
    payload = runout_schedule_payload(schedule)

    assert list(payload.keys()) == ['steps']
    serialized = repr(payload)
    # No board card ever appears in the payload (no spoiler/cheat surface).
    for card in ('Ks', 'Qd', 'Jc', '3h', '4s'):
        assert card not in serialized
    # The human is never a reaction subject.
    for step in payload['steps']:
        names = {r['player_name'] for r in step['reactions']}
        assert 'Human' not in names
        for r in step['reactions']:
            assert set(r.keys()) == {'player_name', 'emotion'}


def test_no_active_ai_returns_empty_schedule():
    """All-in between two humans (or one AI) → nothing to react with."""
    h1 = Player(name='H1', stack=0, is_human=True, hand=(Card('A', 's'), Card('K', 's')))
    h2 = Player(name='H2', stack=0, is_human=True, hand=(Card('Q', 'h'), Card('Q', 'd')))
    deck = (Card('2', 'c'), Card('3', 'c'), Card('4', 'c'), Card('5', 'c'), Card('6', 'c'))
    state = PokerGameState(players=(h1, h2), deck=deck, community_cards=())
    schedule = compute_runout_reactions(state, ai_controllers={})
    assert schedule.steps == []
    assert schedule.reactions_by_phase == {}
