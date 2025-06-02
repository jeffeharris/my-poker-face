"""Table display components for the poker game"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.text import Text
from rich.align import Align
from typing import Dict, List, Optional
from .cards import render_community_cards, render_hand, hand_to_string


class PokerTableDisplay:
    """Manages the poker table display using Rich"""
    
    def __init__(self, console: Console):
        self.console = console
        self.layout = Layout()
        self._setup_layout()
    
    def _setup_layout(self):
        """Setup the table layout structure"""
        self.layout.split_column(
            Layout(name="header", size=3),
            Layout(name="table", size=20),
            Layout(name="player_area", size=10),
            Layout(name="actions", size=3)
        )
        
        # Split table area
        self.layout["table"].split_column(
            Layout(name="pot_info", size=3),
            Layout(name="community_cards", size=7),
            Layout(name="ai_players", size=10)
        )
    
    def update_header(self, title: str = "MY POKER FACE 🎰"):
        """Update the header"""
        header = Panel(
            Align.center(Text(title, style="bold cyan")),
            style="cyan",
            border_style="cyan"
        )
        self.layout["header"].update(header)
    
    def update_pot_info(self, pot: int, current_bet: int, phase: str):
        """Update pot and betting information"""
        info = Table.grid(expand=True)
        info.add_column(justify="center")
        info.add_row(f"[bold yellow]POT: ${pot}[/]  |  [bold]TO CALL: ${current_bet}[/]  |  [bold cyan]{phase}[/]")
        
        self.layout["pot_info"].update(Panel(info, style="yellow"))
    
    def update_community_cards(self, cards: List[Dict]):
        """Update community cards display"""
        cards_display = Align.center(render_community_cards(cards))
        self.layout["community_cards"].update(
            Panel(cards_display, title="Community Cards", style="green")
        )
    
    def update_ai_players(self, players: List[Dict], current_player: Optional[str] = None):
        """Update AI players display"""
        players_table = Table(expand=True, show_header=True)
        players_table.add_column("Player", style="cyan")
        players_table.add_column("Stack", style="green")
        players_table.add_column("Bet", style="yellow")
        players_table.add_column("Status", style="white")
        players_table.add_column("Action", style="magenta")
        
        for player in players:
            if player['is_human']:
                continue
                
            name = player['name']
            if name == current_player:
                name = f"➤ {name}"
            
            status = "FOLDED" if player['is_folded'] else "ALL IN" if player['is_all_in'] else "Active"
            action = player.get('last_action', '')
            
            players_table.add_row(
                name,
                f"${player['stack']}",
                f"${player['bet']}",
                status,
                action
            )
        
        self.layout["ai_players"].update(
            Panel(players_table, title="Opponents", style="cyan")
        )
    
    def update_player_hand(self, player: Dict, show_cards: bool = True):
        """Update human player's hand display"""
        if player['is_human']:
            hand_display = render_hand(player['hand'], hidden=not show_cards)
            hand_info = f"[bold]Your Hand[/]  |  Stack: ${player['stack']}  |  Bet: ${player['bet']}"
            
            player_panel = Panel(
                Align.center(hand_display),
                title=hand_info,
                style="bold blue"
            )
            self.layout["player_area"].update(player_panel)
    
    def update_actions(self, available_actions: List[str], message: str = ""):
        """Update available actions"""
        if message:
            action_text = f"[yellow]{message}[/]\n"
        else:
            action_text = ""
            
        if available_actions:
            action_text += "Actions: " + "  ".join(f"[bold cyan][{a[0].upper()}]{a[1:]}[/]" for a in available_actions)
        
        self.layout["actions"].update(
            Panel(action_text, style="cyan")
        )
    
    def show_thinking(self, player_name: str, message: str = "Thinking..."):
        """Show AI thinking animation"""
        thinking_panel = Panel(
            f"[bold yellow]🎭 {player_name}:[/] [italic]{message}[/]",
            style="yellow"
        )
        self.layout["actions"].update(thinking_panel)
    
    def show_ai_message(self, player_name: str, message: str, action: Optional[str] = None):
        """Show AI player's message/taunt"""
        if action:
            text = f"[bold yellow]🎭 {player_name}[/] {action}: [italic]\"{message}\"[/]"
        else:
            text = f"[bold yellow]🎭 {player_name}:[/] [italic]\"{message}\"[/]"
        
        message_panel = Panel(text, style="yellow")
        self.layout["actions"].update(message_panel)
    
    def render(self):
        """Render the entire table layout"""
        self.console.clear()
        self.console.print(self.layout)