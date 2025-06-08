"""Main menu for the poker game"""

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.prompt import Prompt
from typing import Optional


ASCII_TITLE = """
╔╦╗╦ ╦  ╔═╗╔═╗╦╔═╔═╗╦═╗  ╔═╗╔═╗╔═╗╔═╗
║║║╚╦╝  ╠═╝║ ║╠╩╗║╣ ╠╦╝  ╠╣ ╠═╣║  ║╣ 
╩ ╩ ╩   ╩  ╚═╝╩ ╩╚═╝╩╚═  ╚  ╩ ╩╚═╝╚═╝
         Celebrity Poker with Attitude! 🎰
"""


class MainMenu:
    """Main menu handler"""
    
    def __init__(self, console: Console):
        self.console = console
    
    def show(self) -> dict:
        """Show main menu and get user choices"""
        self.console.clear()
        
        # Show title
        title_panel = Panel(
            Align.center(Text(ASCII_TITLE, style="bold cyan")),
            style="cyan",
            border_style="cyan"
        )
        self.console.print(title_panel)
        
        # Show options
        options_text = """
[1] Quick Game (2 random opponents)
[2] Choose Opponents
[3] View Personalities
[4] Exit

[dim]Tip: You can start playing in 30 seconds with Quick Game![/]
"""
        
        options_panel = Panel(
            options_text.strip(),
            title="[bold]Main Menu[/]",
            style="green"
        )
        self.console.print(options_panel)
        
        # Get choice
        choice = Prompt.ask(
            "Select option",
            choices=["1", "2", "3", "4"],
            default="1"
        )
        
        if choice == "1":
            return {"action": "quick_game"}
        elif choice == "2":
            return {"action": "choose_opponents"}
        elif choice == "3":
            return {"action": "view_personalities"}
        else:
            return {"action": "exit"}
    
    def get_player_name(self) -> str:
        """Get the player's name"""
        self.console.print("\n[bold cyan]Welcome to My Poker Face![/]\n")
        
        name = Prompt.ask(
            "[yellow]Enter your name[/]",
            default="Jeff"
        )
        
        self.console.print(f"\n[green]Welcome, {name}! Let's play some poker![/]\n")
        return name