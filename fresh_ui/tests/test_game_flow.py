"""Integration tests for complete game flow"""

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fresh_ui.utils.game_adapter_v2 import GameAdapterV2
from poker import PokerPhase


class TestGameFlow(unittest.TestCase):
    """Test complete game flow"""
    
    def test_game_initialization(self):
        """Test that game initializes properly"""
        adapter = GameAdapterV2.create_new_game(
            player_name="TestPlayer",
            ai_names=["AI1", "AI2"],
            use_mock_ai=True
        )
        
        # Check game state
        self.assertIsNotNone(adapter.game_state)
        self.assertEqual(len(adapter.game_state.players), 3)
        
        # Check players
        player_names = [p.name for p in adapter.game_state.players]
        self.assertIn("Jeff", player_names)  # Hardcoded human name
        self.assertIn("AI1", player_names)
        self.assertIn("AI2", player_names)
        
        # Check cards dealt
        for player in adapter.game_state.players:
            self.assertEqual(len(player.hand), 2)
        
        # Check phase
        self.assertEqual(adapter.state_machine.phase, PokerPhase.PRE_FLOP)
    
    def test_player_actions(self):
        """Test processing player actions"""
        adapter = GameAdapterV2.create_new_game(
            player_name="TestPlayer",
            ai_names=["AI1", "AI2"],
            use_mock_ai=True
        )
        
        # Get current player
        current = adapter.get_current_player()
        self.assertIsNotNone(current)
        
        # Test available actions
        actions = adapter.get_available_actions()
        self.assertIn('fold', actions)
        
        # Pre-flop, players usually need to call the big blind
        if 'check' in actions:
            # Process a check
            success, error = adapter.process_player_action(current.name, 'check')
        else:
            # Process a call
            self.assertIn('call', actions)
            success, error = adapter.process_player_action(current.name, 'call')
        
        self.assertTrue(success)
        self.assertIsNone(error)
        
        # Current player should have changed
        new_current = adapter.get_current_player()
        self.assertNotEqual(current.name, new_current.name)
    
    def test_ai_decisions(self):
        """Test AI makes decisions"""
        adapter = GameAdapterV2.create_new_game(
            player_name="TestPlayer",
            ai_names=["Gordon Ramsay", "Bob Ross"],
            use_mock_ai=True
        )
        
        # Skip human player
        human = adapter.get_human_player()
        if adapter.get_current_player() == human:
            adapter.process_player_action(human.name, 'check')
        
        # Get AI player
        current = adapter.get_current_player()
        self.assertFalse(current.is_human)
        
        # Get AI decision
        ai_controller = adapter.ai_controllers.get(current.name)
        self.assertIsNotNone(ai_controller)
        
        decision = ai_controller.decide_action([])
        self.assertIn('action', decision)
        self.assertIn('adding_to_pot', decision)
        self.assertIn('persona_response', decision)
    
    def test_betting_round_completion(self):
        """Test that betting rounds complete properly"""
        adapter = GameAdapterV2.create_new_game(
            player_name="TestPlayer",
            ai_names=["AI1", "AI2"],
            use_mock_ai=True
        )
        
        initial_phase = adapter.state_machine.phase
        
        # Have all players check
        for _ in range(3):  # 3 players
            current = adapter.get_current_player()
            if current:
                adapter.process_player_action(current.name, 'check')
        
        # Phase should have advanced
        self.assertNotEqual(adapter.state_machine.phase, initial_phase)
        
        # Community cards should be dealt
        if initial_phase == PokerPhase.PRE_FLOP:
            self.assertEqual(len(adapter.game_state.community_cards), 3)  # Flop
            self.assertEqual(adapter.state_machine.phase, PokerPhase.FLOP)
    
    def test_fold_reduces_active_players(self):
        """Test that folding reduces active players"""
        adapter = GameAdapterV2.create_new_game(
            player_name="TestPlayer", 
            ai_names=["AI1", "AI2"],
            use_mock_ai=True
        )
        
        # Fold first player
        current = adapter.get_current_player()
        adapter.process_player_action(current.name, 'fold')
        
        # Check player is folded
        folded_player = next(p for p in adapter.game_state.players if p.name == current.name)
        self.assertTrue(folded_player.is_folded)
        
        # Active players reduced
        active = [p for p in adapter.game_state.players if not p.is_folded]
        self.assertEqual(len(active), 2)
    
    def test_all_in_action(self):
        """Test all-in functionality"""
        adapter = GameAdapterV2.create_new_game(
            player_name="TestPlayer",
            ai_names=["AI1", "AI2"],
            use_mock_ai=True
        )
        
        current = adapter.get_current_player()
        initial_stack = current.stack
        
        # Go all in
        adapter.process_player_action(current.name, 'all-in')
        
        # Check player state
        player = next(p for p in adapter.game_state.players if p.name == current.name)
        self.assertEqual(player.stack, 0)
        self.assertTrue(player.is_all_in)
        self.assertEqual(player.bet, initial_stack)
        
        # Pot should increase
        self.assertEqual(adapter.game_state.pot, initial_stack)
    
    def test_hand_completion(self):
        """Test hand completes when only one player left"""
        adapter = GameAdapterV2.create_new_game(
            player_name="TestPlayer",
            ai_names=["AI1", "AI2"],
            use_mock_ai=True
        )
        
        # Have two players fold
        players = list(adapter.game_state.players)
        for i in range(2):
            current = adapter.get_current_player()
            adapter.process_player_action(current.name, 'fold')
        
        # Hand should be complete
        self.assertTrue(adapter.is_hand_complete())
        
        # Should have one winner
        winners = adapter.get_winners()
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0][1], adapter.game_state.pot['total'])


if __name__ == '__main__':
    unittest.main()