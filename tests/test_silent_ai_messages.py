"""
Test that silent AI players don't send empty messages.
"""
import unittest
from unittest.mock import Mock, patch
import json

from console_app.ui_console import display_ai_player_action
from flask_app.ui_web import handle_ai_action


class TestSilentAIMessages(unittest.TestCase):
    """Test that AI players who don't speak don't send '...' messages."""
    
    def test_console_ui_handles_missing_persona_response(self):
        """Test console UI doesn't show message when AI is quiet."""
        # AI response without persona_response (quiet player)
        response_dict = {
            'action': 'fold',
            'raise_to': 0,
            'inner_monologue': 'Bad hand, better fold'
            # No persona_response or physical
        }
        
        # Capture printed output
        with patch('builtins.print') as mock_print:
            display_ai_player_action("Silent Bob", response_dict)
            
            # Check what was printed
            calls = [str(call) for call in mock_print.call_args_list]
            
            # Should print the action
            self.assertTrue(any("Silent Bob chose to fold" in str(call) for call in calls))
            
            # Should NOT print empty quotes or "..."
            self.assertFalse(any('""' in str(call) for call in calls))
            self.assertFalse(any('"..."' in str(call) for call in calls))
            self.assertFalse(any('...' in str(call) for call in calls))
    
    def test_console_ui_shows_message_when_ai_speaks(self):
        """Test console UI shows message when AI actually speaks."""
        # AI response with persona_response (chatty player)
        response_dict = {
            'action': 'raise',
            'raise_to': 100,
            'inner_monologue': 'Great hand!',
            'persona_response': 'Time to turn up the heat!',
            'physical': 'smirks confidently'
        }
        
        # Capture printed output
        with patch('builtins.print') as mock_print:
            display_ai_player_action("Gordon Ramsay", response_dict)
            
            # Check what was printed
            calls = [str(call) for call in mock_print.call_args_list]
            
            # Should print the action and message
            self.assertTrue(any("Gordon Ramsay chose to raise by 100" in str(call) for call in calls))
            self.assertTrue(any('"Time to turn up the heat!"' in str(call) for call in calls))
            self.assertTrue(any('smirks confidently' in str(call) for call in calls))
    
    def test_console_ui_ignores_ellipsis_responses(self):
        """Test console UI doesn't show '...' responses."""
        # AI response with '...' (old behavior)
        response_dict = {
            'action': 'check',
            'raise_to': 0,
            'persona_response': '...',
            'physical': '...'
        }
        
        # Capture printed output
        with patch('builtins.print') as mock_print:
            display_ai_player_action("Quiet Player", response_dict)
            
            # Check what was printed
            calls = [str(call) for call in mock_print.call_args_list]
            
            # Should print the action
            self.assertTrue(any("Quiet Player chose to check" in str(call) for call in calls))
            
            # Should NOT print the ellipsis
            self.assertFalse(any('"..."' in str(call) for call in calls))
            self.assertFalse(any('...' in str(call) for call in calls))


if __name__ == '__main__':
    unittest.main()