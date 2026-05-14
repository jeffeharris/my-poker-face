"""Regression tests for call-amount recording.

When a player calls, the LLM/UI passes raise_to=0 (since they're not raising).
Three call sites (AI handler, human play_action route, human socket handler)
previously passed that 0 straight to record_action_in_memory, so RecordedAction.amount
ended up as 0 for every call. Downstream consumers (opponent modeling, c-bet
detector, hand recap, decision analysis) need the actual call cost.

The fix normalizes the recorded amount at each call site:
    if action == 'call':
        record_amount = max(0, min(
            pre_action_state.highest_bet - current_player.bet,
            current_player.stack,
        ))

These tests verify:
  1. The normalization formula computes the right cost across edge cases
     (full call, partial blind call, all-in shove-or-fold call, already-matched).
  2. End-to-end: after record_action_in_memory runs with the normalized
     amount, the RecordedAction stored by the memory manager has the
     correct amount — not 0.
"""

from types import SimpleNamespace

import pytest

from flask_app.handlers.message_handler import record_action_in_memory
from poker.memory.memory_manager import AIMemoryManager


# ── Helpers ──────────────────────────────────────────────────────────────


def _normalize_call_amount(action, pre_action_highest_bet, player_bet, player_stack, raw_amount):
    """Re-implementation of the call-site normalization, used to exercise
    the formula. The real call sites have this inline (see the three
    record_action_in_memory call sites in flask_app/). If you change one,
    update the others — and this helper."""
    record_amount = raw_amount
    if action == 'call':
        record_amount = max(0, min(pre_action_highest_bet - player_bet, player_stack))
    return record_amount


def _player(name='Hero', bet=0, stack=10000, is_folded=False):
    return SimpleNamespace(name=name, bet=bet, stack=stack, is_folded=is_folded)


def _game_state(players, highest_bet=0, pot_total=0):
    return SimpleNamespace(
        players=players,
        highest_bet=highest_bet,
        pot={'total': pot_total},
    )


def _state_machine(phase_name='PRE_FLOP'):
    return SimpleNamespace(
        current_phase=SimpleNamespace(name=phase_name),
    )


def _make_memory_manager(players):
    """Fresh memory manager with a started hand so recorded actions
    flow into the in-progress hand."""
    mm = AIMemoryManager(game_id='test-call-amount')
    for p in players:
        mm.initialize_for_player(p.name)

    # HandHistoryRecorder.start_hand needs minimal player attrs
    gs_players = [
        SimpleNamespace(name=p.name, stack=p.stack, is_human=False, hand=None)
        for p in players
    ]
    gs = SimpleNamespace(players=gs_players, table_positions={})
    mm.on_hand_start(gs, hand_number=1)
    return mm


# ── Formula tests ───────────────────────────────────────────────────────


class TestCallAmountNormalization:
    """Direct tests of the normalization formula used at all three call sites."""

    def test_full_call_facing_200_with_zero_bet(self):
        """The headline case: facing a $200 bet with $0 already in → record $200."""
        amt = _normalize_call_amount(
            action='call',
            pre_action_highest_bet=200,
            player_bet=0,
            player_stack=10000,
            raw_amount=0,  # what the UI/LLM passes for call
        )
        assert amt == 200

    def test_blind_completing_call(self):
        """BB already in for $10, facing $30 raise → call cost is $20."""
        amt = _normalize_call_amount(
            action='call',
            pre_action_highest_bet=30,
            player_bet=10,
            player_stack=10000,
            raw_amount=0,
        )
        assert amt == 20

    def test_already_matched_yields_zero(self):
        """If the player's bet already equals highest_bet, a 'call' costs $0.
        (Edge case: shouldn't normally happen — they'd check — but guard against
        negative values from pathological inputs.)"""
        amt = _normalize_call_amount(
            action='call',
            pre_action_highest_bet=50,
            player_bet=50,
            player_stack=10000,
            raw_amount=0,
        )
        assert amt == 0

    def test_overbet_clamps_to_stack(self):
        """Player has $40 stack but faces a $200 bet → call costs $40, not $200."""
        amt = _normalize_call_amount(
            action='call',
            pre_action_highest_bet=200,
            player_bet=0,
            player_stack=40,
            raw_amount=0,
        )
        assert amt == 40

    def test_negative_clamped_to_zero(self):
        """Defensive: if player_bet somehow exceeds highest_bet, don't record
        a negative amount."""
        amt = _normalize_call_amount(
            action='call',
            pre_action_highest_bet=10,
            player_bet=50,  # bug condition, shouldn't happen
            player_stack=10000,
            raw_amount=0,
        )
        assert amt == 0

    def test_non_call_action_passes_amount_through(self):
        """For raise/bet/all_in/check/fold the existing amount is correct;
        only 'call' is normalized."""
        for action in ('raise', 'bet', 'all_in', 'check', 'fold'):
            amt = _normalize_call_amount(
                action=action,
                pre_action_highest_bet=200,
                player_bet=0,
                player_stack=10000,
                raw_amount=150,
            )
            assert amt == 150, f"{action} should pass amount through unchanged"


# ── End-to-end tests through record_action_in_memory ─────────────────────


class TestRecordedActionAmount:
    """Verify that after record_action_in_memory runs with the normalized
    amount, the RecordedAction stored on the memory manager carries the
    correct amount (and is NOT 0 for a call)."""

    def test_call_records_cost_not_zero(self):
        """Headline regression: a player facing $200 calling for $200 should
        have RecordedAction.amount == 200, not 0."""
        hero = _player('Hero', bet=0, stack=10000)
        villain = _player('Villain', bet=0, stack=10000)
        mm = _make_memory_manager([hero, villain])

        # Pre-action: highest bet is $200 (villain raised earlier this street)
        pre_action_state = _game_state([hero, villain], highest_bet=200)

        # Post-action state (mimics what play_turn would return). The pot
        # total is what's stored on RecordedAction.pot_after; the call
        # amount is what we care about.
        post_state = _game_state(
            [_player('Hero', bet=200, stack=9800), villain],
            highest_bet=200,
            pot_total=400,
        )

        # Mimic the call-site normalization
        record_amount = _normalize_call_amount(
            action='call',
            pre_action_highest_bet=pre_action_state.highest_bet,
            player_bet=hero.bet,
            player_stack=hero.stack,
            raw_amount=0,  # what the UI/LLM passes
        )

        record_action_in_memory(
            game_data={'memory_manager': mm},
            player_name='Hero',
            action='call',
            amount=record_amount,
            game_state=post_state,
            state_machine=_state_machine('PRE_FLOP'),
        )

        actions = mm.hand_recorder.current_hand.actions
        assert len(actions) == 1
        action = actions[0]
        assert action.player_name == 'Hero'
        assert action.action == 'call'
        assert action.amount == 200, (
            f"call should record true cost ($200), got ${action.amount}. "
            "Recording bug regressed — see call sites in "
            "flask_app/handlers/game_handler.py and flask_app/routes/game_routes.py"
        )

    def test_call_completing_blind_records_increment(self):
        """SB completing to a $30 raise: SB has $5 in, call cost is $25."""
        sb = _player('SB', bet=5, stack=10000)
        bb = _player('BB', bet=10, stack=10000)
        utg = _player('UTG', bet=30, stack=10000)
        mm = _make_memory_manager([sb, bb, utg])

        pre_action_state = _game_state([sb, bb, utg], highest_bet=30)
        post_state = _game_state([sb, bb, utg], highest_bet=30, pot_total=65)

        record_amount = _normalize_call_amount(
            action='call',
            pre_action_highest_bet=pre_action_state.highest_bet,
            player_bet=sb.bet,
            player_stack=sb.stack,
            raw_amount=0,
        )

        record_action_in_memory(
            game_data={'memory_manager': mm},
            player_name='SB',
            action='call',
            amount=record_amount,
            game_state=post_state,
            state_machine=_state_machine('PRE_FLOP'),
        )

        action = mm.hand_recorder.current_hand.actions[0]
        assert action.amount == 25

    def test_call_clamped_to_stack(self):
        """Short stack calling an overbet records the stack-sized call cost."""
        shortie = _player('Shortie', bet=0, stack=40)
        villain = _player('Villain', bet=200, stack=10000)
        mm = _make_memory_manager([shortie, villain])

        pre_action_state = _game_state([shortie, villain], highest_bet=200)
        post_state = _game_state([shortie, villain], highest_bet=200, pot_total=240)

        record_amount = _normalize_call_amount(
            action='call',
            pre_action_highest_bet=pre_action_state.highest_bet,
            player_bet=shortie.bet,
            player_stack=shortie.stack,
            raw_amount=0,
        )

        record_action_in_memory(
            game_data={'memory_manager': mm},
            player_name='Shortie',
            action='call',
            amount=record_amount,
            game_state=post_state,
            state_machine=_state_machine('PRE_FLOP'),
        )

        action = mm.hand_recorder.current_hand.actions[0]
        assert action.amount == 40

    def test_raise_amount_passthrough_unchanged(self):
        """Non-call actions (here: raise) should record the raw amount
        unchanged — the fix should only touch the 'call' branch."""
        hero = _player('Hero', bet=0, stack=10000)
        villain = _player('Villain', bet=0, stack=10000)
        mm = _make_memory_manager([hero, villain])

        post_state = _game_state(
            [_player('Hero', bet=100, stack=9900), villain],
            highest_bet=100,
            pot_total=100,
        )

        record_amount = _normalize_call_amount(
            action='raise',
            pre_action_highest_bet=0,
            player_bet=0,
            player_stack=10000,
            raw_amount=100,
        )

        record_action_in_memory(
            game_data={'memory_manager': mm},
            player_name='Hero',
            action='raise',
            amount=record_amount,
            game_state=post_state,
            state_machine=_state_machine('PRE_FLOP'),
        )

        action = mm.hand_recorder.current_hand.actions[0]
        assert action.action == 'raise'
        assert action.amount == 100

    def test_fold_records_zero(self):
        """Folds keep amount=0, regardless of pre-action state."""
        hero = _player('Hero', bet=0, stack=10000)
        villain = _player('Villain', bet=0, stack=10000)
        mm = _make_memory_manager([hero, villain])

        post_state = _game_state(
            [_player('Hero', bet=0, stack=10000, is_folded=True), villain],
            highest_bet=200,
            pot_total=200,
        )

        record_amount = _normalize_call_amount(
            action='fold',
            pre_action_highest_bet=200,
            player_bet=0,
            player_stack=10000,
            raw_amount=0,
        )

        record_action_in_memory(
            game_data={'memory_manager': mm},
            player_name='Hero',
            action='fold',
            amount=record_amount,
            game_state=post_state,
            state_machine=_state_machine('PRE_FLOP'),
        )

        action = mm.hand_recorder.current_hand.actions[0]
        assert action.action == 'fold'
        assert action.amount == 0
