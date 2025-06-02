"""Main game runner for Rich CLI poker"""

import time
import sys
import logging
from rich.console import Console
from rich.live import Live
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fresh_ui.menus.main_menu import MainMenu
from fresh_ui.menus.personality_selector import PersonalitySelector
from fresh_ui.display.table import PokerTableDisplay
from fresh_ui.display.animations import (
    thinking_animation, dealing_animation, pot_win_animation,
    personality_intro, dramatic_reveal
)
from fresh_ui.utils.input_handler import InputHandler
from fresh_ui.utils.game_adapter_v2 import GameAdapterV2 as GameAdapter


class PokerGame:
    """Main game runner"""
    
    def __init__(self):
        self.console = Console()
        self.main_menu = MainMenu(self.console)
        self.personality_selector = PersonalitySelector(self.console)
        self.input_handler = InputHandler(self.console)
        self.table_display = PokerTableDisplay(self.console)
        self.game_adapter = None
        self.player_name = None
    
    def run(self):
        """Main game loop"""
        while True:
            try:
                menu_result = self.main_menu.show()
                
                if menu_result["action"] == "exit":
                    self.console.print("\n[cyan]Thanks for playing! 🎰[/]\n")
                    break
                
                elif menu_result["action"] == "view_personalities":
                    self.personality_selector.show_all_personalities()
                
                elif menu_result["action"] == "quick_game":
                    self.start_quick_game()
                
                elif menu_result["action"] == "choose_opponents":
                    self.start_custom_game()
                
            except KeyboardInterrupt:
                self.console.print("\n\n[yellow]Game interrupted. Returning to menu...[/]\n")
                time.sleep(1)
            except Exception as e:
                self.console.print(f"\n[red]Error: {e}[/]\n")
                time.sleep(2)
    
    def start_quick_game(self):
        """Start a quick game with random opponents"""
        # Get player name
        self.player_name = self.main_menu.get_player_name()
        
        # Select random opponents
        opponents = self.personality_selector.quick_select(2)
        time.sleep(2)
        
        # Start the game
        self.play_game(opponents)
    
    def start_custom_game(self):
        """Start a game with chosen opponents"""
        # Get player name
        self.player_name = self.main_menu.get_player_name()
        
        # Select opponents
        opponents = self.personality_selector.select_opponents(2)
        
        # Show selected opponents
        self.console.clear()
        self.console.print("[bold cyan]Your opponents:[/]\n")
        for name in opponents:
            data = self.personality_selector.personalities[name]
            personality_intro(
                self.console,
                name,
                data.get('verbal_tics', ['...'])[0],
                data.get('play_style', 'balanced')
            )
        
        self.input_handler.wait_for_continue("Press Enter to start the game...")
        
        # Start the game
        self.play_game(opponents)
    
    def play_game(self, opponent_names: list):
        """Play a poker game"""
        try:
            logger.info(f"Starting game with opponents: {opponent_names}")
            
            # Create game adapter with mock AI for testing
            self.game_adapter = GameAdapter.create_new_game(
                self.player_name,
                opponent_names,
                use_mock_ai=True  # Use mock AI to avoid OpenAI dependency
            )
            logger.info("Game adapter created successfully")
            logger.debug(f"Initial phase: {self.game_adapter.state_machine.phase}")
            logger.debug(f"Cards dealt: {all(len(p.hand) == 2 for p in self.game_adapter.game_state.players)}")
            
            # Play hands until someone is out or player quits
            hand_num = 1
            while True:
                self.console.clear()
                self.console.print(f"[bold cyan]Hand #{hand_num}[/]\n")
                
                # Deal and play hand
                self.play_hand()
                
                # Check if game is over
                active_players = [p for p in self.game_adapter.game_state.players 
                                if p.stack > 0]
                if len(active_players) <= 1:
                    self.show_game_over(active_players[0] if active_players else None)
                    break
                
                # Ask to continue
                self.console.print("\n[cyan]Ready for next hand?[/]")
                if self.input_handler.wait_for_continue("Press Enter to continue or Ctrl+C to quit..."):
                    break
                
                # Start new hand
                self.game_adapter.start_new_hand()
                hand_num += 1
                
        except Exception as e:
            logger.error(f"Error in play_game: {e}", exc_info=True)
            self.console.print(f"[red]Game error: {e}[/]")
            raise
    
    def play_hand(self):
        """Play a single hand"""
        # Show dealing animation
        dealing_animation(self.console, 2)
        
        # Main game loop
        while not self.game_adapter.is_hand_complete():
            # Update display
            self.update_table_display()
            
            # Get current player
            current_player = self.game_adapter.get_current_player()
            if not current_player:
                break
            
            if current_player.is_human:
                # Human turn
                self.handle_human_turn()
            else:
                # AI turn
                self.handle_ai_turn(current_player.name)
            
            # Small delay between actions
            time.sleep(0.5)
        
        # Show results
        self.show_hand_results()
    
    def handle_human_turn(self):
        """Handle human player's turn"""
        current_player = self.game_adapter.get_current_player()
        available_actions = self.game_adapter.get_available_actions()
        
        # Update display with available actions
        self.table_display.update_actions(available_actions)
        self.table_display.render()
        
        # Get action
        action, amount = self.input_handler.get_action(
            available_actions,
            self.game_adapter.game_state.highest_bet,
            current_player.stack,
            current_player.bet
        )
        
        # Confirm risky actions
        if not self.input_handler.confirm_action(action, amount):
            return self.handle_human_turn()
        
        # Process action
        self.game_adapter.process_player_action(current_player.name, action, amount)
    
    def handle_ai_turn(self, ai_name: str):
        """Handle AI player's turn"""
        # Show thinking animation
        self.table_display.show_thinking(ai_name)
        self.table_display.render()
        
        thinking_animation(self.console, ai_name, 1.5)
        
        # Get AI decision
        ai_controller = self.game_adapter.ai_controllers.get(ai_name)
        if not ai_controller:
            self.console.print(f"[red]No AI controller for {ai_name}[/]")
            return
            
        decision = ai_controller.decide_action([])
        action = decision['action']
        amount = decision['adding_to_pot']
        message = decision.get('persona_response', '')
        
        # Show AI's action with personality
        if message:
            self.table_display.show_ai_message(ai_name, message, action)
        else:
            self.table_display.show_ai_message(ai_name, f"I'll {action}", action)
        
        self.table_display.render()
        time.sleep(2)
        
        # Process action
        self.game_adapter.process_player_action(ai_name, action, amount)
    
    def update_table_display(self):
        """Update the table display with current game state"""
        state = self.game_adapter.game_state
        
        # Update all components
        self.table_display.update_header()
        self.table_display.update_pot_info(
            state.pot['total'],
            state.highest_bet,
            self.game_adapter.state_machine.phase.name
        )
        self.table_display.update_community_cards(state.community_cards)
        
        # Update AI players with last actions
        ai_players = []
        for player in state.players:
            if not player.is_human:
                player_dict = player.to_dict()
                # Add last action if available
                player_dict['last_action'] = ''
                ai_players.append(player_dict)
        
        current_player_name = None
        if state.current_player_index is not None:
            current_player_name = state.players[state.current_player_index].name
        
        self.table_display.update_ai_players(ai_players, current_player_name)
        
        # Update human player
        human_player = self.game_adapter.get_human_player()
        if human_player:
            self.table_display.update_player_hand(human_player.to_dict())
    
    def show_hand_results(self):
        """Show the results of the hand"""
        winners = self.game_adapter.get_winners()
        
        self.update_table_display()
        self.table_display.render()
        
        # Show dramatic reveal
        dramatic_reveal(self.console, "SHOWDOWN!", 0.5)
        
        # Show winners
        for winner, amount in winners:
            pot_win_animation(self.console, winner.name, amount)
        
        self.input_handler.wait_for_continue()
    
    def show_game_over(self, winner):
        """Show game over screen"""
        self.console.clear()
        
        if winner:
            dramatic_reveal(self.console, f"🎉 {winner.name} WINS THE GAME! 🎉", 1.0)
        else:
            dramatic_reveal(self.console, "Game Over!", 1.0)
        
        self.input_handler.wait_for_continue("Press Enter to return to menu...")


def main():
    """Entry point"""
    game = PokerGame()
    game.run()


if __name__ == "__main__":
    main()