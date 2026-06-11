"""Routing tests for TieredBotController._try_push_fold_lookup.

Verifies the dispatch added when the HU-only gate was lifted:
  - HU (num_seated == 2) short stacks  -> HU chart (unchanged behavior).
  - Multi-way (num_seated > 2) short stacks -> 6max chart.
  - Above 15 BB -> None (deep-stack table takes over).
  - Multi-way facing an all-in -> caller table; unopened -> jam chart.

Tests call `_try_push_fold_lookup` directly with minimal game-state stubs
holding only the fields the method reads, so they don't depend on the
full decision pipeline.
"""

from __future__ import annotations

import random
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from poker.strategy import push_fold
from poker.tiered_bot_controller import TieredBotController


@pytest.fixture(autouse=True)
def _reset_chart_cache():
    push_fold.reset_chart_cache()


def _player(name, stack=1000, bet=0, folded=False):
    return SimpleNamespace(
        name=name,
        stack=stack,
        bet=bet,
        is_folded=folded,
    )


def _controller():
    with patch(
        'poker.tiered_bot_controller.AIPlayerController.__init__',
        return_value=None,
    ):
        c = TieredBotController.__new__(TieredBotController)
    c.player_name = 'Hero'
    c.debug_logging = False
    c.rng = random.Random(42)
    return c


# ── HU stub: needs small_blind_idx / big_blind_idx ──────────────────────────


def _hu_state(hero_pos='SB', hero_stack_bb=10, hand_open=True, big_blind=100):
    """2-handed state. hero_pos in {'SB','BB'}. SB=idx0, BB=idx1.

    When hero is BB and hand_open is True, the SB is all-in (jam to face)."""
    bb = big_blind
    sb = _player('Hero' if hero_pos == 'SB' else 'Villain', stack=hero_stack_bb * bb, bet=bb // 2)
    bb_p = _player('Hero' if hero_pos == 'BB' else 'Villain', stack=hero_stack_bb * bb, bet=bb)
    if hero_pos == 'BB' and hand_open:
        # SB shoves all-in
        sb.stack = 0
        sb.bet = hero_stack_bb * bb
    players = [sb, bb_p]
    return SimpleNamespace(
        players=players,
        current_ante=bb,
        small_blind_idx=0,
        big_blind_idx=1,
        raises_this_round=1 if (hero_pos == 'BB' and hand_open) else 0,
    )


# ── Multi-way stub: needs table_positions for get_6max_position ─────────────

_POS_KEY = {
    'UTG': 'under_the_gun',
    'HJ': 'middle_position_1',
    'CO': 'cutoff',
    'BTN': 'button',
    'SB': 'small_blind_player',
    'BB': 'big_blind_player',
}


def _6max_state(
    hero_pos='UTG',
    hero_idx=0,
    hero_stack_bb=10,
    big_blind=100,
    jammer_pos=None,
    raises=0,
    num_players=6,
    opener_pos=None,
    opener_bet_bb=2.2,
    extra_caller_pos=None,
):
    """num_players-handed state. Hero at hero_idx with 6-max label hero_pos.

    If jammer_pos is set, the player at that position is all-in (a jam to
    face) and raises defaults to 1.

    If opener_pos is set, that player makes a LIVE (non-all-in) open to
    opener_bet_bb BB (stack reduced but > 0) and raises defaults to 1 — the
    reshove spot. extra_caller_pos optionally adds a cold-caller who flats the
    open (a multiway reshove the v1 table doesn't model).
    """
    bb = big_blind
    names = [f'P{i}' for i in range(num_players)]
    names[hero_idx] = 'Hero'
    players = [_player(n, stack=hero_stack_bb * bb, bet=0) for n in names]

    # Position labels in seat order, sized to the table. Short-handed tables
    # drop early positions (3-handed = BTN/SB/BB; 6-handed = full ring).
    _ORDER_BY_N = {
        2: ['SB', 'BB'],
        3: ['BTN', 'SB', 'BB'],
        4: ['CO', 'BTN', 'SB', 'BB'],
        5: ['HJ', 'CO', 'BTN', 'SB', 'BB'],
        6: ['UTG', 'HJ', 'CO', 'BTN', 'SB', 'BB'],
    }
    order = _ORDER_BY_N[num_players]
    # Put hero's label at hero_idx; distribute the rest around.
    labels = list(order)
    # Ensure hero_pos sits at hero_idx by swapping.
    if labels[hero_idx] != hero_pos:
        j = labels.index(hero_pos)
        labels[hero_idx], labels[j] = labels[j], labels[hero_idx]

    table_positions = {}
    for i, label in enumerate(labels):
        table_positions[_POS_KEY[label]] = players[i].name

    # Post blinds.
    for i, label in enumerate(labels):
        if label == 'SB':
            players[i].bet = bb // 2
        elif label == 'BB':
            players[i].bet = bb

    effective_raises = raises
    if jammer_pos is not None:
        j = labels.index(jammer_pos)
        players[j].stack = 0
        players[j].bet = hero_stack_bb * bb
        effective_raises = max(raises, 1)

    if opener_pos is not None:
        o = labels.index(opener_pos)
        open_to = int(round(opener_bet_bb * bb))
        players[o].bet = open_to
        players[o].stack = hero_stack_bb * bb - open_to  # live raise, stack > 0
        effective_raises = max(effective_raises, 1)
        if extra_caller_pos is not None:
            c = labels.index(extra_caller_pos)
            players[c].bet = open_to  # flats the open (no extra raise)
            players[c].stack = hero_stack_bb * bb - open_to

    return SimpleNamespace(
        players=players,
        current_ante=bb,
        table_positions=table_positions,
        raises_this_round=effective_raises,
    )


# ── HU routing (unchanged) ──────────────────────────────────────────────────


class TestHURouting:
    def test_hu_sb_short_jams_aa(self):
        gs = _hu_state(hero_pos='SB', hero_stack_bb=10)
        c = _controller()
        action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=2)
        assert action == 'jam'

    def test_hu_bb_facing_jam_calls_aa(self):
        gs = _hu_state(hero_pos='BB', hero_stack_bb=10, hand_open=True)
        c = _controller()
        action = c._try_push_fold_lookup('AA', gs, player_idx=1, num_seated=2)
        assert action == 'call'

    def test_hu_above_threshold_returns_none(self):
        gs = _hu_state(hero_pos='SB', hero_stack_bb=30)
        c = _controller()
        action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=2)
        assert action is None


# ── Multi-way routing (new) ─────────────────────────────────────────────────


class TestMultiwayRouting:
    def test_6max_utg_short_jams_aa(self):
        gs = _6max_state(hero_pos='UTG', hero_idx=0, hero_stack_bb=10)
        c = _controller()
        action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=6)
        assert action == 'jam'

    def test_6max_utg_folds_marginal(self):
        # A6o jams from SB but folds from UTG at 10 BB.
        gs = _6max_state(hero_pos='UTG', hero_idx=0, hero_stack_bb=10)
        c = _controller()
        action = c._try_push_fold_lookup('A6o', gs, player_idx=0, num_seated=6)
        assert action == 'fold'

    def test_6max_sb_jams_wider(self):
        gs = _6max_state(hero_pos='SB', hero_idx=0, hero_stack_bb=10)
        c = _controller()
        action = c._try_push_fold_lookup('A6o', gs, player_idx=0, num_seated=6)
        assert action == 'jam'

    def test_6max_three_handed_routes_to_6max(self):
        gs = _6max_state(hero_pos='BTN', hero_idx=0, hero_stack_bb=8, num_players=3)
        c = _controller()
        action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=3)
        assert action == 'jam'

    def test_6max_above_threshold_returns_none(self):
        gs = _6max_state(hero_pos='BTN', hero_idx=0, hero_stack_bb=30)
        c = _controller()
        action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=6)
        assert action is None

    def test_6max_facing_sb_jam_calls(self):
        # Hero is BB at 8 BB facing an SB all-in.
        gs = _6max_state(hero_pos='BB', hero_idx=0, hero_stack_bb=8, jammer_pos='SB')
        c = _controller()
        action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=6)
        assert action == 'call'

    def test_6max_multiple_jammers_returns_none(self):
        # Two opponents already all-in (SB jam + a late re-jam) is a multi-way
        # all-in the single-jammer caller tables don't model. The router must
        # fall through (None) rather than apply a too-loose single-jammer range.
        gs = _6max_state(hero_pos='BB', hero_idx=0, hero_stack_bb=8, jammer_pos='SB')
        # Mark a second opponent (not hero, not the SB jammer) all-in too.
        for p in gs.players:
            if p.name != 'Hero' and p.bet <= gs.current_ante:
                p.stack = 0
                p.bet = 8 * gs.current_ante
                break
        n_allin = sum(1 for p in gs.players if p.name != 'Hero' and p.stack == 0)
        assert n_allin == 2, "test setup should leave exactly two all-in opponents"
        c = _controller()
        action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=6)
        assert action is None

    def test_6max_unopened_raise_present_returns_none(self):
        # A non-all-in raise sits in front of hero (the reshove spot). With the
        # PUSH_FOLD_6MAX_RESHOVE_ENABLED flag OFF (default), it falls through.
        gs = _6max_state(hero_pos='BTN', hero_idx=0, hero_stack_bb=10, raises=1)
        c = _controller()
        with patch('poker.tiered_bot_controller._reshove_6max_enabled', return_value=False):
            action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=6)
        assert action is None

    def test_6max_limped_pot_returns_none(self):
        # P1a: a limper (non-blind opponent matched the BB without raising — a
        # call doesn't bump raises_this_round) means hero isn't first-in, so the
        # unopened jam chart must NOT fire. Falls through.
        gs = _6max_state(hero_pos='BTN', hero_idx=0, hero_stack_bb=10)
        for p in gs.players:  # a non-blind, not-yet-acted opp has bet == 0
            if p.name != 'Hero' and p.bet == 0:
                p.bet = gs.current_ante  # limp (call the BB), stack stays full
                break
        assert gs.raises_this_round == 0, "a limp must not look like a raise"
        c = _controller()
        action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=6)
        assert action is None

    def test_6max_short_allin_under_live_raise_returns_none(self):
        # P1b: a short all-in (side-pot) sits UNDER a larger live raise. Hero is
        # facing the raise, not the jam, so the caller table doesn't apply.
        gs = _6max_state(hero_pos='BB', hero_idx=0, hero_stack_bb=10, raises=1)
        bb = gs.current_ante
        opps = [p for p in gs.players if p.name != 'Hero']
        opps[0].stack = 0
        opps[0].bet = 4 * bb  # short all-in
        opps[1].stack = 500 * bb  # live (NOT all-in)
        opps[1].bet = 10 * bb  # larger raise tops the all-in
        c = _controller()
        action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=6)
        assert action is None

    def test_6max_non_bb_hero_facing_jam_returns_none(self):
        # P2: the caller tables are BB-vs-jam only. A non-BB hero (CO) facing an
        # UTG jam has no caller table → fall through.
        gs = _6max_state(hero_pos='CO', hero_idx=0, hero_stack_bb=8, jammer_pos='UTG')
        c = _controller()
        action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=6)
        assert action is None

    def test_6max_bb_unopened_returns_none(self):
        # BB unopened (a walk) has no jam row → None (falls through).
        gs = _6max_state(hero_pos='BB', hero_idx=0, hero_stack_bb=10)
        c = _controller()
        action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=6)
        assert action is None


class TestReshoveRouting:
    """Reshove (jam over a single non-all-in open), flag-gated."""

    def _route(self, gs, hand='AA', hero_idx=0, num_seated=6, flag=True, fold_equity=True):
        # Chart-routing tests patch the fold-equity gate True so they exercise
        # the reshove chart independent of opponent reads (the gate has its own
        # tests below + in test_tiered_bot_exploitation).
        c = _controller()
        with (
            patch('poker.tiered_bot_controller._reshove_6max_enabled', return_value=flag),
            patch.object(
                TieredBotController, '_reshove_opener_fold_equity_ok', return_value=fold_equity
            ),
        ):
            return c._try_push_fold_lookup(hand, gs, player_idx=hero_idx, num_seated=num_seated)

    def test_reshove_premium_jams_with_flag_on(self):
        gs = _6max_state(hero_pos='BTN', hero_idx=0, hero_stack_bb=10, opener_pos='CO')
        assert self._route(gs, 'AA') == 'jam'

    def test_reshove_trash_folds_with_flag_on(self):
        gs = _6max_state(hero_pos='BTN', hero_idx=0, hero_stack_bb=10, opener_pos='CO')
        assert self._route(gs, '72o') == 'fold'

    def test_reshove_none_with_flag_off(self):
        # Flag off → the open falls through to the deep-stack / short_stack path.
        gs = _6max_state(hero_pos='BTN', hero_idx=0, hero_stack_bb=10, opener_pos='CO')
        assert self._route(gs, 'AA', flag=False) is None

    def test_reshove_bb_over_open_jams(self):
        # BB reshoving over an open is in scope (unlike the unopened chart).
        gs = _6max_state(hero_pos='BB', hero_idx=0, hero_stack_bb=10, opener_pos='BTN')
        assert self._route(gs, 'AA') == 'jam'

    def test_reshove_3bet_war_returns_none(self):
        # raises_this_round == 2 (a 3-bet already happened) → not a clean single
        # open → fall through even with the flag on.
        gs = _6max_state(hero_pos='BB', hero_idx=0, hero_stack_bb=12, opener_pos='CO', raises=2)
        assert self._route(gs, 'AA') is None

    def test_reshove_cold_caller_multiway_returns_none(self):
        # A cold-caller flatted the open → multiway reshove the v1 table doesn't
        # model → fall through.
        gs = _6max_state(
            hero_pos='BB', hero_idx=0, hero_stack_bb=10, opener_pos='CO', extra_caller_pos='BTN'
        )
        assert self._route(gs, 'AA') is None

    def test_reshove_no_fold_equity_returns_none(self):
        # Even a premium reshove is declined when the gate says the opener won't
        # fold (station/maniac / no read) — reshoving them is pure spew.
        gs = _6max_state(hero_pos='BTN', hero_idx=0, hero_stack_bb=10, opener_pos='CO')
        assert self._route(gs, 'AA', fold_equity=False) is None

    def test_reshove_no_opponent_model_declines(self):
        # A bare controller (no opponent_model_manager) has no read → the gate
        # defaults to False → reshove declined.
        gs = _6max_state(hero_pos='BTN', hero_idx=0, hero_stack_bb=10, opener_pos='CO')
        c = _controller()  # no opponent_model_manager attached
        with patch('poker.tiered_bot_controller._reshove_6max_enabled', return_value=True):
            assert c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=6) is None

    def test_reshove_action_6max_pure_detector(self):
        # The shared detector is controller-agnostic and flag-free.
        from poker.strategy.push_fold import reshove_action_6max

        gs = _6max_state(hero_pos='BTN', hero_idx=0, hero_stack_bb=10, opener_pos='CO')
        assert reshove_action_6max('AA', gs, 0, 6, big_blind=100, effective_stack_bb=10) == 'jam'
        assert reshove_action_6max('72o', gs, 0, 6, big_blind=100, effective_stack_bb=10) == 'fold'
        # An all-in in front is a caller-table spot, not a reshove → None.
        jam_gs = _6max_state(hero_pos='BB', hero_idx=0, hero_stack_bb=8, jammer_pos='SB')
        assert reshove_action_6max('AA', jam_gs, 0, 6, big_blind=100, effective_stack_bb=8) is None

    def test_reshove_fold_equity_gate_injected(self):
        # The detector honors the injected fold-equity predicate: same spot,
        # gate False → None, gate True → jam.
        from poker.strategy.push_fold import reshove_action_6max

        gs = _6max_state(hero_pos='BTN', hero_idx=0, hero_stack_bb=10, opener_pos='CO')
        assert (
            reshove_action_6max(
                'AA',
                gs,
                0,
                6,
                big_blind=100,
                effective_stack_bb=10,
                opener_fold_equity_ok=lambda oi: False,
            )
            is None
        )
        assert (
            reshove_action_6max(
                'AA',
                gs,
                0,
                6,
                big_blind=100,
                effective_stack_bb=10,
                opener_fold_equity_ok=lambda oi: True,
            )
            == 'jam'
        )


# ── Snapshot flag wiring sanity (HU path still sets push_fold_routed) ───────


class TestSnapshotInterplay:
    def test_hu_and_6max_dispatch_distinct(self):
        """Same hero hand+depth: HU SB jams (wide), UTG 6max folds A8o."""
        c = _controller()
        hu = c._try_push_fold_lookup(
            'A8o',
            _hu_state(hero_pos='SB', hero_stack_bb=12),
            player_idx=0,
            num_seated=2,
        )
        six = c._try_push_fold_lookup(
            'A8o',
            _6max_state(hero_pos='UTG', hero_idx=0, hero_stack_bb=12),
            player_idx=0,
            num_seated=6,
        )
        assert hu == 'jam'
        assert six == 'fold'


# ── push_fold_nash opt-in gate ──────────────────────────────────────────────


class TestPushFoldNashGate:
    """The Nash charts are opt-in per persona (`push_fold_nash`). Only blessed
    'skilled' characters use them; everyone else falls through (returns None)
    so the donors keep their leaky short game. __new__-built instances (sims /
    tests) never set the attribute, so the gate defaults to firing."""

    def test_missing_flag_defaults_to_firing(self):
        # _controller() bypasses __init__, so push_fold_nash_enabled is unset.
        c = _controller()
        assert not hasattr(c, 'push_fold_nash_enabled')
        action = c._try_push_fold_lookup('AA', _hu_state('SB', 10), player_idx=0, num_seated=2)
        assert action == 'jam'

    def test_enabled_fires(self):
        c = _controller()
        c.push_fold_nash_enabled = True
        action = c._try_push_fold_lookup('AA', _hu_state('SB', 10), player_idx=0, num_seated=2)
        assert action == 'jam'

    def test_disabled_falls_through_hu(self):
        c = _controller()
        c.push_fold_nash_enabled = False
        action = c._try_push_fold_lookup('AA', _hu_state('SB', 10), player_idx=0, num_seated=2)
        assert action is None

    def test_disabled_falls_through_6max(self):
        c = _controller()
        c.push_fold_nash_enabled = False
        gs = _6max_state(hero_pos='UTG', hero_idx=0, hero_stack_bb=10)
        action = c._try_push_fold_lookup('AA', gs, player_idx=0, num_seated=6)
        assert action is None
