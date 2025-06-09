"""Input handling for the poker game"""

from rich.console import Console
from rich.prompt import Prompt, IntPrompt
from typing import List, Optional, Tuple


class InputHandler:
    """Handle user input for poker actions"""
    
    def __init__(self, console: Console):
        self.console = console
        
        # Action mappings
        self.action_keys = {
            'f': 'fold',
            'c': 'call', 
            'r': 'raise',
            'a': 'all_in',
            'ch': 'check'
        }
    
    def get_action(self, available_actions: List[str], current_bet: int = 0, 
                   player_stack: int = 0, player_bet: int = 0) -> Tuple[str, int]:
        """Get player action and amount"""
        
        # Build prompt based on available actions
        action_map = {}
        prompt_parts = []
        
        for action in available_actions:
            if action == 'fold':
                action_map['f'] = 'fold'
                prompt_parts.append("[F]old")
            elif action == 'call':
                call_amount = current_bet - player_bet
                action_map['c'] = 'call'
                prompt_parts.append(f"[C]all ${call_amount}")
            elif action == 'raise':
                action_map['r'] = 'raise'
                prompt_parts.append("[R]aise")
            elif action == 'all_in':
                action_map['a'] = 'all_in'
                prompt_parts.append(f"[A]ll-in ${player_stack}")
            elif action == 'check':
                action_map['ch'] = 'check'
                prompt_parts.append("[Ch]eck")
        
        # Show available actions
        self.console.print(f"\n[cyan]Available actions: {' | '.join(prompt_parts)}[/]")
        
        while True:
            choice = Prompt.ask("Your action").lower()
            
            # Check if it's a valid action
            if choice in action_map:
                action = action_map[choice]
                
                # Handle raise amount
                if action == 'raise':
                    min_raise = current_bet * 2
                    max_raise = player_stack + player_bet
                    
                    self.console.print(f"[yellow]Minimum raise: ${min_raise}[/]")
                    self.console.print(f"[yellow]Maximum raise: ${max_raise} (all-in)[/]")
                    
                    amount = IntPrompt.ask(
                        "Raise amount",
                        default=min_raise
                    )
                    
                    if amount < min_raise:
                        self.console.print(f"[red]Raise must be at least ${min_raise}[/]")
                        continue
                    elif amount >= max_raise:
                        # Convert to all-in
                        return 'all_in', player_stack
                    else:
                        return 'raise', amount - player_bet
                
                elif action == 'call':
                    return 'call', current_bet - player_bet
                
                elif action == 'all_in':
                    return 'all_in', player_stack
                
                else:  # fold or check
                    return action, 0
            
            else:
                # Try to match partial input
                matches = [k for k in action_map.keys() if k.startswith(choice)]
                if len(matches) == 1:
                    choice = matches[0]
                    continue
                else:
                    self.console.print("[red]Invalid action. Please try again.[/]")
    
    def confirm_action(self, action: str, amount: int = 0) -> bool:
        """Confirm important actions"""
        if action in ['all_in', 'fold']:
            if action == 'all_in':
                prompt = f"[yellow]Confirm going all-in for ${amount}?[/]"
            else:
                prompt = "[yellow]Confirm folding?[/]"
            
            return Prompt.ask(prompt, choices=["y", "n"], default="y") == "y"
        
        return True
    
    def wait_for_continue(self, message: str = "Press Enter to continue..."):
        """Wait for user to continue"""
        self.console.print(f"\n[dim]{message}[/]")
        input()