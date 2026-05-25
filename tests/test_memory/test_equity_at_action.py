"""Tests for Polarization Phase A equity-at-action tracking.

Covers the new fields on OpponentTendencies, the update_equity_at_action
method, and to_dict/from_dict round-trip. The showdown-replay integration
in memory_manager is exercised end-to-end via a fake recorded hand.
"""

from __future__ import annotations

import pytest

from poker.memory.opponent_model import OpponentTendencies


class TestUpdateEquityAtAction:
    def test_first_bet_observation_sets_mean(self):
        t = OpponentTendencies()
        t.update_equity_at_action('bet', 0.7)
        assert t._equity_betting_count == 1
        assert t._equity_betting_sum == pytest.approx(0.7)
        assert t.equity_when_betting_postflop == pytest.approx(0.7)

    def test_subsequent_observations_update_running_mean(self):
        t = OpponentTendencies()
        t.update_equity_at_action('bet', 0.7)
        t.update_equity_at_action('bet', 0.5)
        t.update_equity_at_action('bet', 0.6)
        assert t._equity_betting_count == 3
        assert t._equity_betting_sum == pytest.approx(1.8)
        assert t.equity_when_betting_postflop == pytest.approx(0.6)

    def test_raise_and_call_buckets_independent(self):
        t = OpponentTendencies()
        t.update_equity_at_action('raise', 0.85)
        t.update_equity_at_action('raise', 0.75)
        t.update_equity_at_action('call', 0.30)
        t.update_equity_at_action('call', 0.40)
        t.update_equity_at_action('call', 0.35)
        assert t.equity_when_raising_postflop == pytest.approx(0.80)
        assert t.equity_when_calling_postflop == pytest.approx(0.35)
        # bet bucket untouched
        assert t.equity_when_betting_postflop == pytest.approx(0.5)
        assert t._equity_betting_count == 0

    def test_other_actions_are_noops(self):
        t = OpponentTendencies()
        t.update_equity_at_action('fold', 0.3)
        t.update_equity_at_action('check', 0.4)
        t.update_equity_at_action('all_in', 0.9)
        t.update_equity_at_action('unknown_action', 0.5)
        assert t._equity_betting_count == 0
        assert t._equity_raising_count == 0
        assert t._equity_calling_count == 0

    def test_out_of_range_equity_silently_skipped(self):
        t = OpponentTendencies()
        t.update_equity_at_action('bet', -0.1)
        t.update_equity_at_action('bet', 1.5)
        t.update_equity_at_action(
            'raise', float('nan')
        )  # NaN: NaN > 1 evaluates False, NaN <= 1 also False
        # Implementation uses 0.0 <= x <= 1.0 — NaN comparisons return False
        # so NaN is silently dropped, same as out-of-range numbers.
        assert t._equity_betting_count == 0
        assert t._equity_raising_count == 0

    def test_polarized_pattern_emerges(self):
        """A CaseBot-like opponent: raises with strong, calls with weak.
        After 8 observations the gap should be obvious."""
        t = OpponentTendencies()
        # Raises with 0.80+ equity
        for eq in [0.82, 0.85, 0.78, 0.91]:
            t.update_equity_at_action('raise', eq)
        # Calls with 0.30-ish equity
        for eq in [0.32, 0.28, 0.41, 0.35]:
            t.update_equity_at_action('call', eq)

        raise_mean = t.equity_when_raising_postflop
        call_mean = t.equity_when_calling_postflop
        polarization = raise_mean - call_mean
        # Strong positive polarization → station signature confirmed
        assert polarization > 0.4

    def test_balanced_pattern(self):
        """A noisy opponent: raises and calls have similar equity
        distributions."""
        t = OpponentTendencies()
        for eq in [0.55, 0.42, 0.50, 0.48]:
            t.update_equity_at_action('raise', eq)
        for eq in [0.48, 0.52, 0.45, 0.50]:
            t.update_equity_at_action('call', eq)

        polarization = t.equity_when_raising_postflop - t.equity_when_calling_postflop
        # Near-zero polarization → no station signature
        assert abs(polarization) < 0.1


class TestSerializationRoundTrip:
    def test_to_dict_includes_equity_fields(self):
        t = OpponentTendencies()
        t.update_equity_at_action('raise', 0.8)
        d = t.to_dict()
        assert 'equity_when_raising_postflop' in d
        assert '_equity_raising_count' in d
        assert '_equity_raising_sum' in d
        assert d['_equity_raising_count'] == 1
        assert d['_equity_raising_sum'] == pytest.approx(0.8)

    def test_from_dict_restores_equity_fields(self):
        t = OpponentTendencies()
        t.update_equity_at_action('bet', 0.7)
        t.update_equity_at_action('raise', 0.85)
        t.update_equity_at_action('call', 0.35)
        t.update_equity_at_action('call', 0.45)

        restored = OpponentTendencies.from_dict(t.to_dict())

        assert restored.equity_when_betting_postflop == pytest.approx(0.7)
        assert restored.equity_when_raising_postflop == pytest.approx(0.85)
        assert restored.equity_when_calling_postflop == pytest.approx(0.40)
        assert restored._equity_betting_count == 1
        assert restored._equity_raising_count == 1
        assert restored._equity_calling_count == 2

    def test_from_dict_legacy_without_equity_fields(self):
        """Older snapshots predate Phase A. from_dict must accept the
        absence and default to neutral 0.5 / 0 counts."""
        legacy = {
            'hands_observed': 5,
            'vpip': 0.4,
            'pfr': 0.2,
            'aggression_factor': 1.5,
        }
        t = OpponentTendencies.from_dict(legacy)
        assert t.equity_when_betting_postflop == 0.5
        assert t.equity_when_raising_postflop == 0.5
        assert t.equity_when_calling_postflop == 0.5
        assert t._equity_betting_count == 0
        assert t._equity_raising_count == 0
        assert t._equity_calling_count == 0


class TestShowdownReplay:
    """End-to-end: a recorded hand with revealed cards triggers
    _record_showdown_equity_at_actions in memory_manager and the
    resulting OpponentModels carry equity-at-action means.
    """

    def test_showdown_records_equity_for_postflop_actions(self):
        from poker.memory.hand_history import PlayerHandInfo, RecordedAction, RecordedHand
        from poker.memory.memory_manager import AIMemoryManager

        mgr = AIMemoryManager(game_id="test_game", db_path=None)
        mgr.initialize_for_player("Alice")
        mgr.initialize_for_player("Bob")

        from datetime import datetime

        # Construct a recorded hand where Bob raises on a wet flop with
        # a strong hand (set on a low board → very high equity).
        recorded = RecordedHand(
            game_id="test_game",
            hand_number=1,
            timestamp=datetime.now(),
            players=(
                PlayerHandInfo(name="Alice", starting_stack=10000, position="BTN", is_human=False),
                PlayerHandInfo(name="Bob", starting_stack=10000, position="BB", is_human=False),
            ),
            hole_cards={
                "Alice": ["7h", "2d"],  # garbage
                "Bob": ["Kh", "Kd"],  # pocket kings
            },
            community_cards=("3s", "8c", "Kc", "2h", "9d"),
            actions=(
                RecordedAction(
                    player_name="Alice", action="call", amount=100, phase="PRE_FLOP", pot_after=200
                ),
                RecordedAction(
                    player_name="Bob", action="check", amount=0, phase="PRE_FLOP", pot_after=200
                ),
                # Flop: K83 — Bob has top set
                RecordedAction(
                    player_name="Bob", action="bet", amount=150, phase="FLOP", pot_after=350
                ),
                RecordedAction(
                    player_name="Alice", action="call", amount=150, phase="FLOP", pot_after=500
                ),
                # Turn: K832 — Bob still nuts
                RecordedAction(
                    player_name="Bob", action="bet", amount=300, phase="TURN", pot_after=800
                ),
                RecordedAction(
                    player_name="Alice", action="call", amount=300, phase="TURN", pot_after=1100
                ),
                # River
                RecordedAction(
                    player_name="Bob", action="check", amount=0, phase="RIVER", pot_after=1100
                ),
                RecordedAction(
                    player_name="Alice", action="check", amount=0, phase="RIVER", pot_after=1100
                ),
            ),
            winners=(),  # not used by the replay path
            pot_size=1100,
            was_showdown=True,
            community_cards_by_phase={
                "FLOP": ["3s", "8c", "Kc"],
                "TURN": ["3s", "8c", "Kc", "2h"],
                "RIVER": ["3s", "8c", "Kc", "2h", "9d"],
            },
        )

        # Direct call to the replay helper (bypassing full _handle_hand_complete)
        mgr._record_showdown_equity_at_actions(recorded)

        # Alice's model of Bob should have Bob's bet equity recorded
        alice_view_of_bob = mgr.opponent_model_manager.get_model("Alice", "Bob")
        # Two postflop bets by Bob (flop + turn) with kings full / top set
        # against Alice's garbage → very high equity each
        assert alice_view_of_bob.tendencies._equity_betting_count == 2
        assert alice_view_of_bob.tendencies.equity_when_betting_postflop > 0.85

        # Alice's two calls were also recorded with much lower equity
        # than Bob's bets — that's the polarization signature.
        bob_view_of_alice = mgr.opponent_model_manager.get_model("Bob", "Alice")
        assert bob_view_of_alice.tendencies._equity_calling_count == 2
        alice_call_eq = bob_view_of_alice.tendencies.equity_when_calling_postflop
        bob_bet_eq = alice_view_of_bob.tendencies.equity_when_betting_postflop
        # The absolute level isn't the point — the polarization gap is.
        # Bob (kings full → top set) bets with ~0.9 equity. Alice
        # (garbage with no draw on a paired board) calls with ~0.35.
        # Either way, the gap is substantial.
        assert bob_bet_eq - alice_call_eq > 0.40, (
            f"Polarization gap too small: bob_bet_eq={bob_bet_eq:.3f}, "
            f"alice_call_eq={alice_call_eq:.3f}"
        )

    def test_showdown_with_folded_players_skips_them(self):
        from poker.memory.hand_history import PlayerHandInfo, RecordedAction, RecordedHand
        from poker.memory.memory_manager import AIMemoryManager

        mgr = AIMemoryManager(game_id="test_game", db_path=None)
        mgr.initialize_for_player("Alice")
        mgr.initialize_for_player("Bob")

        from datetime import datetime

        recorded = RecordedHand(
            game_id="test_game",
            hand_number=1,
            timestamp=datetime.now(),
            players=(
                PlayerHandInfo(name="Alice", starting_stack=10000, position="BTN", is_human=False),
                PlayerHandInfo(name="Bob", starting_stack=10000, position="BB", is_human=False),
            ),
            hole_cards={"Alice": ["Ah", "As"]},  # Bob folded, cards unknown
            community_cards=("3s", "8c", "Kc"),
            actions=(
                RecordedAction(
                    player_name="Alice", action="raise", amount=300, phase="PRE_FLOP", pot_after=400
                ),
                RecordedAction(
                    player_name="Bob", action="fold", amount=0, phase="PRE_FLOP", pot_after=400
                ),
            ),
            winners=(),
            pot_size=400,
            was_showdown=False,  # no showdown — Bob folded
            community_cards_by_phase={"FLOP": ["3s", "8c", "Kc"]},
        )

        mgr._record_showdown_equity_at_actions(recorded)

        # No bet/raise/call by Alice postflop, and no equity tracking
        # should be triggered.
        alice_view_of_bob = mgr.opponent_model_manager.get_model("Alice", "Bob")
        bob_view_of_alice = mgr.opponent_model_manager.get_model("Bob", "Alice")
        assert alice_view_of_bob.tendencies._equity_betting_count == 0
        assert bob_view_of_alice.tendencies._equity_betting_count == 0
