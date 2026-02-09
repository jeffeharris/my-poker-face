"""
Tests for new psychology events: disciplined_fold, modified card_dead_5,
and short_stack_survival.
"""

import unittest
from unittest.mock import patch
from poker.pressure_detector import PressureEventDetector
from poker.player_psychology import PlayerPsychology
from poker.poker_game import PokerGameState, Player
from core.card import Card


class TestCardDead5Modified(unittest.TestCase):
    """Tests for modified card_dead_5 deltas (now includes confidence tax + composure boost)."""

    def _make_psych(self, ego=0.5, poise=0.5):
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': ego,
                'poise': poise, 'expressiveness': 0.5, 'risk_identity': 0.5,
                'adaptation_bias': 0.5, 'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        return PlayerPsychology.from_personality_config('TestPlayer', config)

    def test_card_dead_5_deltas(self):
        """card_dead_5 should decrease confidence, increase composure, decrease energy."""
        psych = self._make_psych()
        initial_conf = psych.confidence
        initial_comp = psych.composure
        initial_energy = psych.energy

        psych.apply_pressure_event('card_dead_5')

        # Confidence should decrease
        self.assertLess(psych.confidence, initial_conf)
        # Composure should increase
        self.assertGreater(psych.composure, initial_comp)
        # Energy should decrease
        self.assertLess(psych.energy, initial_energy)

    def test_card_dead_5_in_resolve_hand_events(self):
        """card_dead_5 should be applied as a pressure event in resolve_hand_events."""
        psych = self._make_psych()
        initial_conf = psych.confidence
        initial_comp = psych.composure

        result = psych.resolve_hand_events(['win', 'card_dead_5'])

        self.assertIn('card_dead_5', result['events_applied'])
        self.assertIn('win', result['events_applied'])

    def test_card_dead_5_raw_impacts(self):
        """Verify the raw delta values are correct."""
        psych = self._make_psych()
        impacts = psych._get_pressure_impacts('card_dead_5')

        self.assertAlmostEqual(impacts['confidence'], -0.03)
        self.assertAlmostEqual(impacts['composure'], 0.03)
        self.assertAlmostEqual(impacts['energy'], -0.10)


class TestDisciplinedFoldDetection(unittest.TestCase):
    """Tests for disciplined_fold event detection in PressureEventDetector."""

    def setUp(self):
        self.detector = PressureEventDetector()

    def _make_game_state(self, community_cards=None, pot_total=500,
                         player_stack=1000, player_bet=100, opponent_bet=200,
                         player_hand=None):
        """Create a game state for testing fold detection.

        highest_bet is derived from player bets (it's a property on PokerGameState).
        Set opponent_bet > player_bet to simulate a bet the player must call.
        """
        if community_cards is None:
            # Turn: 4 community cards
            community_cards = (
                Card('A', 'hearts'), Card('K', 'hearts'),
                Card('Q', 'spades'), Card('7', 'diamonds'),
            )
        if player_hand is None:
            # Decent hand: pair of jacks
            player_hand = (Card('J', 'hearts'), Card('J', 'diamonds'))

        player = Player(
            name='TestPlayer', stack=player_stack, is_human=False,
            hand=player_hand, bet=player_bet,
        )
        opponent = Player(
            name='Opponent', stack=1000, is_human=False,
            hand=(Card('2', 'clubs'), Card('3', 'clubs')), bet=opponent_bet,
        )

        return PokerGameState(
            players=(player, opponent),
            deck=(),
            community_cards=community_cards,
            pot={'total': pot_total, 'main': pot_total},
            current_ante=100,
        )

    @patch.object(PressureEventDetector, '_calculate_fold_equity', return_value=0.35)
    def test_disciplined_fold_detected_on_turn(self, mock_equity):
        """Disciplined fold fires when folding on turn with decent equity in significant pot."""
        game_state = self._make_game_state()

        events = self.detector.detect_action_events(
            game_state, 'TestPlayer', 'fold', hand_number=5
        )

        event_names = [e[0] for e in events]
        self.assertIn('disciplined_fold', event_names)

    @patch.object(PressureEventDetector, '_calculate_fold_equity', return_value=0.35)
    def test_disciplined_fold_detected_on_river(self, mock_equity):
        """Disciplined fold fires on river too."""
        community_cards = (
            Card('A', 'hearts'), Card('K', 'hearts'),
            Card('Q', 'spades'), Card('7', 'diamonds'), Card('2', 'clubs'),
        )
        game_state = self._make_game_state(community_cards=community_cards)

        events = self.detector.detect_action_events(
            game_state, 'TestPlayer', 'fold', hand_number=5
        )

        event_names = [e[0] for e in events]
        self.assertIn('disciplined_fold', event_names)

    @patch.object(PressureEventDetector, '_calculate_fold_equity', return_value=0.35)
    def test_no_disciplined_fold_preflop(self, mock_equity):
        """Disciplined fold does NOT fire preflop (no community cards)."""
        game_state = self._make_game_state(community_cards=())

        events = self.detector.detect_action_events(
            game_state, 'TestPlayer', 'fold', hand_number=5
        )

        event_names = [e[0] for e in events]
        self.assertNotIn('disciplined_fold', event_names)

    @patch.object(PressureEventDetector, '_calculate_fold_equity', return_value=0.35)
    def test_no_disciplined_fold_on_flop(self, mock_equity):
        """Disciplined fold does NOT fire on flop (only 3 community cards)."""
        community_cards = (
            Card('A', 'hearts'), Card('K', 'hearts'), Card('Q', 'spades'),
        )
        game_state = self._make_game_state(community_cards=community_cards)

        events = self.detector.detect_action_events(
            game_state, 'TestPlayer', 'fold', hand_number=5
        )

        event_names = [e[0] for e in events]
        self.assertNotIn('disciplined_fold', event_names)

    @patch.object(PressureEventDetector, '_calculate_fold_equity', return_value=0.15)
    def test_no_disciplined_fold_low_equity(self, mock_equity):
        """No disciplined fold when equity is below threshold (< 0.25)."""
        game_state = self._make_game_state()

        events = self.detector.detect_action_events(
            game_state, 'TestPlayer', 'fold', hand_number=5
        )

        event_names = [e[0] for e in events]
        self.assertNotIn('disciplined_fold', event_names)

    @patch.object(PressureEventDetector, '_calculate_fold_equity', return_value=0.35)
    def test_no_disciplined_fold_tiny_pot(self, mock_equity):
        """No disciplined fold when pot is insignificant (pot/stack < 0.15)."""
        # pot_total=100, player_stack=1000 => pot_significance = 0.1 < 0.15
        game_state = self._make_game_state(pot_total=100)

        events = self.detector.detect_action_events(
            game_state, 'TestPlayer', 'fold', hand_number=5
        )

        event_names = [e[0] for e in events]
        self.assertNotIn('disciplined_fold', event_names)

    @patch.object(PressureEventDetector, '_calculate_fold_equity', return_value=0.35)
    def test_no_disciplined_fold_no_bet_to_call(self, mock_equity):
        """No disciplined fold when there's no bet to call (checking is free)."""
        game_state = self._make_game_state(opponent_bet=100, player_bet=100)

        events = self.detector.detect_action_events(
            game_state, 'TestPlayer', 'fold', hand_number=5
        )

        event_names = [e[0] for e in events]
        self.assertNotIn('disciplined_fold', event_names)

    @patch.object(PressureEventDetector, '_calculate_fold_equity', return_value=0.35)
    def test_disciplined_fold_cooldown(self, mock_equity):
        """Disciplined fold respects cooldown (once per 2 hands)."""
        game_state = self._make_game_state()

        # First fire should work
        events1 = self.detector.detect_action_events(
            game_state, 'TestPlayer', 'fold', hand_number=5
        )
        self.assertIn('disciplined_fold', [e[0] for e in events1])

        # Same hand: should NOT fire
        events2 = self.detector.detect_action_events(
            game_state, 'TestPlayer', 'fold', hand_number=5
        )
        self.assertNotIn('disciplined_fold', [e[0] for e in events2])

        # Next hand (hand 6): should NOT fire (cooldown = 2)
        events3 = self.detector.detect_action_events(
            game_state, 'TestPlayer', 'fold', hand_number=6
        )
        self.assertNotIn('disciplined_fold', [e[0] for e in events3])

        # Hand 7: should fire again (cooldown expired)
        events4 = self.detector.detect_action_events(
            game_state, 'TestPlayer', 'fold', hand_number=7
        )
        self.assertIn('disciplined_fold', [e[0] for e in events4])

    def test_disciplined_fold_not_on_non_fold_action(self):
        """Disciplined fold only fires on fold action."""
        game_state = self._make_game_state()

        events = self.detector.detect_action_events(
            game_state, 'TestPlayer', 'call', hand_number=5
        )

        event_names = [e[0] for e in events]
        self.assertNotIn('disciplined_fold', event_names)


class TestDisciplinedFoldPsychology(unittest.TestCase):
    """Tests for disciplined_fold delta application in PlayerPsychology."""

    def _make_psych(self, ego=0.5, poise=0.5):
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': ego,
                'poise': poise, 'expressiveness': 0.5, 'risk_identity': 0.5,
                'adaptation_bias': 0.5, 'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        return PlayerPsychology.from_personality_config('TestPlayer', config)

    def test_disciplined_fold_impacts(self):
        """Verify raw delta values for disciplined_fold."""
        psych = self._make_psych()
        impacts = psych._get_pressure_impacts('disciplined_fold')

        self.assertAlmostEqual(impacts['confidence'], -0.06)
        self.assertAlmostEqual(impacts['composure'], 0.12)
        self.assertAlmostEqual(impacts['energy'], -0.02)

    def test_disciplined_fold_apply(self):
        """Disciplined fold should decrease confidence, increase composure, decrease energy."""
        psych = self._make_psych()
        initial_conf = psych.confidence
        initial_comp = psych.composure
        initial_energy = psych.energy

        psych.apply_pressure_event('disciplined_fold')

        self.assertLess(psych.confidence, initial_conf)
        self.assertGreater(psych.composure, initial_comp)
        self.assertLess(psych.energy, initial_energy)


class TestShortStackSurvivalDetection(unittest.TestCase):
    """Tests for short_stack_survival event detection in PressureEventDetector."""

    def setUp(self):
        self.detector = PressureEventDetector()

    def test_survival_fires_after_threshold_hands(self):
        """short_stack_survival fires after 3 hands of surviving while short."""
        short_stack = {'TestPlayer'}

        # Hands 1 and 2: should NOT fire
        events1 = self.detector.detect_short_stack_survival_events(short_stack, hand_number=1)
        self.assertEqual(events1, [])

        events2 = self.detector.detect_short_stack_survival_events(short_stack, hand_number=2)
        self.assertEqual(events2, [])

        # Hand 3: should fire (reached threshold)
        events3 = self.detector.detect_short_stack_survival_events(short_stack, hand_number=3)
        event_names = [e[0] for e in events3]
        self.assertIn('short_stack_survival', event_names)

    def test_survival_cooldown(self):
        """short_stack_survival respects 5-hand cooldown."""
        short_stack = {'TestPlayer'}

        # Burn through first 3 hands to trigger
        self.detector.detect_short_stack_survival_events(short_stack, hand_number=1)
        self.detector.detect_short_stack_survival_events(short_stack, hand_number=2)
        events3 = self.detector.detect_short_stack_survival_events(short_stack, hand_number=3)
        self.assertIn('short_stack_survival', [e[0] for e in events3])

        # Counter reset to 0 after firing. Hands 4-6: building again
        events4 = self.detector.detect_short_stack_survival_events(short_stack, hand_number=4)
        events5 = self.detector.detect_short_stack_survival_events(short_stack, hand_number=5)
        events6 = self.detector.detect_short_stack_survival_events(short_stack, hand_number=6)

        # Hand 6: counter is 3 again (threshold met), but hand 6 - 3 = 3 < 5 (cooldown)
        self.assertEqual(events6, [])

        # Hands 7-8: still cooling down
        events7 = self.detector.detect_short_stack_survival_events(short_stack, hand_number=7)
        events8 = self.detector.detect_short_stack_survival_events(short_stack, hand_number=8)
        # Hand 8: fire (3 + cooldown 5 = hand 8)
        self.assertIn('short_stack_survival', [e[0] for e in events8])

    def test_survival_resets_on_leaving_short_stack(self):
        """Counter resets when player is no longer short-stacked."""
        short_stack = {'TestPlayer'}

        # Build up 2 hands
        self.detector.detect_short_stack_survival_events(short_stack, hand_number=1)
        self.detector.detect_short_stack_survival_events(short_stack, hand_number=2)

        # Player leaves short-stack (e.g., doubled up)
        self.detector.detect_short_stack_survival_events(set(), hand_number=3)

        # Player becomes short again - counter should restart
        self.detector.detect_short_stack_survival_events(short_stack, hand_number=4)
        self.detector.detect_short_stack_survival_events(short_stack, hand_number=5)

        # Hand 6: only 2 consecutive, not 3
        events6 = self.detector.detect_short_stack_survival_events(short_stack, hand_number=6)
        self.assertIn('short_stack_survival', [e[0] for e in events6])

    def test_survival_resets_on_all_in(self):
        """Counter resets when player goes all-in (via detect_action_events)."""
        short_stack = {'TestPlayer'}

        # Build up 2 hands
        self.detector.detect_short_stack_survival_events(short_stack, hand_number=1)
        self.detector.detect_short_stack_survival_events(short_stack, hand_number=2)

        # Player goes all-in (detected by detect_action_events)
        game_state = PokerGameState(
            players=(Player(name='TestPlayer', stack=0, is_human=False),),
            deck=(),
        )
        self.detector.detect_action_events(
            game_state, 'TestPlayer', 'all_in', hand_number=2
        )

        # Counter should be reset - need 3 more hands
        self.detector.detect_short_stack_survival_events(short_stack, hand_number=3)
        events4 = self.detector.detect_short_stack_survival_events(short_stack, hand_number=4)
        self.assertEqual(events4, [])

    def test_multiple_players_tracked_independently(self):
        """Each player's survival is tracked independently."""
        # Player A short for 3 hands, Player B just started
        short_stack_ab = {'PlayerA', 'PlayerB'}

        self.detector.detect_short_stack_survival_events({'PlayerA'}, hand_number=1)
        self.detector.detect_short_stack_survival_events({'PlayerA'}, hand_number=2)

        # Hand 3: both players short, A fires, B at count 1
        events3 = self.detector.detect_short_stack_survival_events(short_stack_ab, hand_number=3)
        event_players = {}
        for name, players in events3:
            for p in players:
                event_players[p] = name
        self.assertIn('PlayerA', event_players)
        self.assertNotIn('PlayerB', event_players)


class TestShortStackSurvivalPsychology(unittest.TestCase):
    """Tests for short_stack_survival delta application in PlayerPsychology."""

    def _make_psych(self, ego=0.5, poise=0.5):
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': ego,
                'poise': poise, 'expressiveness': 0.5, 'risk_identity': 0.5,
                'adaptation_bias': 0.5, 'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        return PlayerPsychology.from_personality_config('TestPlayer', config)

    def test_short_stack_survival_impacts(self):
        """Verify raw delta values for short_stack_survival."""
        psych = self._make_psych()
        impacts = psych._get_pressure_impacts('short_stack_survival')

        self.assertAlmostEqual(impacts['confidence'], -0.04)
        self.assertAlmostEqual(impacts['composure'], 0.06)
        self.assertAlmostEqual(impacts['energy'], -0.05)

    def test_short_stack_survival_apply(self):
        """short_stack_survival should decrease confidence, increase composure, decrease energy."""
        psych = self._make_psych()
        initial_conf = psych.confidence
        initial_comp = psych.composure
        initial_energy = psych.energy

        psych.apply_pressure_event('short_stack_survival')

        self.assertLess(psych.confidence, initial_conf)
        self.assertGreater(psych.composure, initial_comp)
        self.assertLess(psych.energy, initial_energy)


if __name__ == '__main__':
    unittest.main()
