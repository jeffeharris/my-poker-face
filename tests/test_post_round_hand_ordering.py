"""Regression tests for the post-round chat data path.

Two distinct guarantees live here:

1. ``memory_manager.on_hand_complete`` runs BEFORE the
   ``winner_announcement`` socketio emit, so that
   ``memory_manager.hand_recorder.completed_hands[-1]`` is the just-finished
   hand when the client requests post-round chat suggestions. (Previously
   the psychology pipeline ran between the emit and the hand recording,
   leaving ``completed_hands`` stale for several seconds — clients that
   clicked a chat tone in that window got prompts about hand N-1.)

2. ``format_hand_context_for_prompt`` produces the rich narrator-driven
   output (street-by-street recap with "You" substitution + hand breakdown)
   when a RecordedHand is supplied — much easier for the LLM to comprehend
   than the prior OUTCOME/CARDS/TIMELINE block.
"""

import unittest
from unittest.mock import MagicMock, patch

from core.card import Card
from poker.memory.memory_manager import AIMemoryManager
from poker.poker_game import Player, PokerGameState


class TestPostRoundHandOrdering(unittest.TestCase):
    """Ensure hand is recorded before winner emit."""

    def _build_game_state(self) -> PokerGameState:
        """Heads-up showdown: Alice (AsKh) beats Bob (2c3d) on a dry board."""
        alice = Player(
            name='Alice',
            stack=970,
            is_human=True,
            bet=30,
            hand=(Card('A', 'Spades'), Card('K', 'Hearts')),
            is_folded=False,
        )
        bob = Player(
            name='Bob',
            stack=970,
            is_human=False,
            bet=30,
            hand=(Card('2', 'Clubs'), Card('3', 'Diamonds')),
            is_folded=False,
        )
        return PokerGameState(
            deck=(),
            players=(alice, bob),
            community_cards=(
                Card('7', 'Diamonds'),
                Card('8', 'Clubs'),
                Card('9', 'Spades'),
                Card('Q', 'Hearts'),
                Card('2', 'Spades'),
            ),
            pot={'total': 60, 'Alice': 30, 'Bob': 30},
            current_ante=10,
        )

    def _build_memory_manager(
        self, game_id: str, game_state: PokerGameState, hand_number: int
    ) -> AIMemoryManager:
        """Memory manager with an in-progress hand carrying hole cards.

        on_hand_start reads hole cards directly off ``player.hand`` and seeds
        the recorder's current_hand, which is what the equity tracker and
        on_hand_complete both read from.
        """
        mm = AIMemoryManager(game_id)
        # Pre-existing completed hand (hand_number - 1) — proves that before
        # the fix, completed_hands[-1] would be the prior hand at emit time.
        if hand_number > 1:
            prior_state = self._build_game_state()
            mm.on_hand_start(prior_state, hand_number=hand_number - 1)
            mm.hand_recorder.complete_hand(
                winner_info={
                    'pot_breakdown': [
                        {'winners': [{'name': 'Bob', 'amount': 100}], 'hand_name': 'High Card'}
                    ],
                    'hand_name': 'High Card',
                    'hand_rank': 10,
                },
                game_state=prior_state,
            )
        # Start the current hand
        mm.on_hand_start(game_state, hand_number=hand_number)
        return mm

    def test_hand_is_recorded_before_winner_announcement_emit(self):
        """At the moment 'winner_announcement' is emitted, the just-finished
        hand must already be in completed_hands."""
        from flask_app.handlers import game_handler

        game_id = 'test-game'
        hand_number = 37
        game_state = self._build_game_state()
        mm = self._build_memory_manager(game_id, game_state, hand_number)

        state_machine = MagicMock()
        state_machine.game_state = game_state
        state_machine.current_phase = None
        state_machine._state_machine = MagicMock()

        game_data = {
            'memory_manager': mm,
            'ai_controllers': {},  # skip psychology pipeline path
            'state_machine': state_machine,
            'owner_id': '',
            'hand_start_stacks': {},
            'short_stack_players': set(),
            'last_announced_phase': None,
        }

        # Snapshot of completed_hands at the moment of the winner_announcement emit.
        captured = {'hand_numbers_at_emit': None}

        def capture_emit(event, data=None, **_kwargs):
            if event == 'winner_announcement':
                captured['hand_numbers_at_emit'] = [
                    h.hand_number for h in mm.hand_recorder.completed_hands
                ]

        # Patch everything that isn't the ordering we care about.
        patches = [
            patch.object(
                game_handler,
                'socketio',
                MagicMock(emit=capture_emit, start_background_task=MagicMock(), sleep=MagicMock()),
            ),
            patch.object(game_handler, 'send_message', MagicMock()),
            patch.object(game_handler, 'hand_history_repo', MagicMock()),
            patch.object(game_handler, 'game_repo', MagicMock()),
            patch.object(game_handler, 'event_repository', MagicMock()),
            patch.object(game_handler, 'coach_repo', MagicMock()),
            patch.object(game_handler, 'update_and_emit_game_state', MagicMock()),
            patch.object(game_handler.game_state_service, 'set_game', MagicMock()),
            patch.object(
                game_handler.game_state_service,
                'get_game_owner_info',
                MagicMock(return_value=('', '')),
            ),
            patch.object(
                game_handler,
                'config',
                MagicMock(
                    ENABLE_AI_COMMENTARY=False,
                    ANIMATION_SPEED=0,
                ),
            ),
        ]
        for p in patches:
            p.start()
        try:
            game_handler.handle_evaluating_hand_phase(game_id, game_data, state_machine, game_state)
        finally:
            for p in patches:
                p.stop()

        # The winner_announcement must have been emitted at least once.
        self.assertIsNotNone(
            captured['hand_numbers_at_emit'],
            "winner_announcement was never emitted",
        )
        # And the current hand must be in completed_hands at that moment.
        self.assertIn(
            hand_number,
            captured['hand_numbers_at_emit'],
            f"hand {hand_number} was not recorded before winner_announcement; "
            f"completed_hands at emit time: {captured['hand_numbers_at_emit']}. "
            f"This is the bug where post-round chat sees stale hand data.",
        )


class TestFormatHandContextRich(unittest.TestCase):
    """When a RecordedHand is provided, the formatter produces the rich
    narrator-driven output that the LLM comprehends best."""

    def _build_recorded_hand(self):
        from poker.memory.hand_history import HandInProgress

        # Heads-up: Alice (AsKh) loses to Bob (Qc Qs) on board 7d 8c 9s Qh 2s.
        # Bob makes a set of queens.
        hand = HandInProgress(game_id='g1', hand_number=37)
        hand.add_player(name='Alice', starting_stack=1000, position='button', is_human=True)
        hand.add_player(
            name='Bob', starting_stack=1000, position='big_blind_player', is_human=False
        )
        hand.set_hole_cards('Alice', ['As', 'Kh'])
        hand.set_hole_cards('Bob', ['Qc', 'Qs'])
        hand.add_community_cards('FLOP', ['7d', '8c', '9s'])
        hand.add_community_cards('TURN', ['Qh'])
        hand.add_community_cards('RIVER', ['2s'])
        hand.record_action('Alice', 'raise', 30, 'PRE_FLOP', 60)
        hand.record_action('Bob', 'call', 20, 'PRE_FLOP', 60)
        hand.record_action('Bob', 'check', 0, 'FLOP', 60)
        hand.record_action('Alice', 'raise', 40, 'FLOP', 100)
        hand.record_action('Bob', 'call', 40, 'FLOP', 140)
        from poker.memory.hand_history import WinnerInfo

        return hand.complete(
            winners=[
                WinnerInfo(name='Bob', amount_won=140, hand_name='Three of a Kind', hand_rank=4)
            ],
            pot_size=140,
            was_showdown=True,
        )

    def test_rich_format_uses_narrator_recap_and_breakdown(self):
        from flask_app.utils.hand_context import (
            build_hand_context_from_recorded_hand,
            format_hand_context_for_prompt,
        )

        hand = self._build_recorded_hand()
        context = build_hand_context_from_recorded_hand(hand, 'Alice')
        text = format_hand_context_for_prompt(
            context,
            'Alice',
            recorded_hand=hand,
            big_blind=10,
        )

        # Outcome prefix kept for quick LLM signal.
        self.assertIn('OUTCOME: You LOST', text)
        # Narrator recap structure ("You" substitution, RESULT, SHOWDOWN).
        self.assertIn('HAND #37 RECAP', text)
        self.assertIn('You', text)  # perspective substitution active
        self.assertIn('RESULT: Bob won', text)
        self.assertIn('SHOWDOWN', text)
        # Hand breakdown explains the player's cards.
        self.assertIn('YOUR HAND BREAKDOWN', text)

    def test_legacy_format_when_no_recorded_hand(self):
        """Callers that don't pass recorded_hand still get the old format."""
        from flask_app.utils.hand_context import format_hand_context_for_prompt

        context = {
            'outcome': 'LOST_SHOWDOWN',
            'player_cards': ['As', 'Kh'],
            'player_hand_name': None,
            'opponent_name': 'Bob',
            'opponent_cards': ['Qc', 'Qs'],
            'opponent_hand_name': 'Three of a Kind',
            'community_cards': ['7d', '8c', '9s', 'Qh', '2s'],
            'timeline': 'PRE_FLOP:\n  You raised to 30.',
            'pot_size': 140,
        }
        text = format_hand_context_for_prompt(context, 'Alice')
        self.assertIn('OUTCOME: You LOST', text)
        self.assertIn('YOUR CARDS: As, Kh', text)
        self.assertIn('OPPONENT: Bob', text)
        self.assertNotIn('HAND #', text)  # no narrator recap


if __name__ == '__main__':
    unittest.main()
