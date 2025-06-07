"""Personality selection menu"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.columns import Columns
from typing import List, Dict
import json
import random


class PersonalitySelector:
    """Handle personality selection for AI players"""
    
    def __init__(self, console: Console, personalities_path: str = "poker/personalities.json"):
        self.console = console
        self.personalities = self._load_personalities(personalities_path)
    
    def _load_personalities(self, path: str) -> Dict:
        """Load personalities from JSON file"""
        try:
            with open(path, 'r') as f:
                data = json.load(f)
                return data.get('personalities', {})
        except Exception as e:
            self.console.print(f"[red]Error loading personalities: {e}[/]")
            return {}
    
    def _create_personality_card(self, name: str, data: Dict) -> Panel:
        """Create a personality showcase card"""
        traits = data.get('personality_traits', {})
        
        # Build trait indicators
        bluff_level = "🎭" * int(traits.get('bluff_tendency', 0.5) * 5)
        aggro_level = "🔥" * int(traits.get('aggression', 0.5) * 5)
        chat_level = "💬" * int(traits.get('chattiness', 0.5) * 5)
        
        # Get a random verbal tic
        tics = data.get('verbal_tics', ["..."])
        random_tic = random.choice(tics) if tics else "..."
        
        content = f"""[bold cyan]{name}[/]
[italic]"{random_tic}"[/]

[yellow]Style:[/] {data.get('play_style', 'unknown')}
[yellow]Bluff:[/] {bluff_level}
[yellow]Aggro:[/] {aggro_level}  
[yellow]Chatty:[/] {chat_level}"""
        
        return Panel(content, style="cyan", width=30)
    
    def show_all_personalities(self):
        """Display all available personalities"""
        self.console.clear()
        self.console.print("[bold cyan]Available Personalities[/]\n")
        
        # Create cards for each personality
        cards = []
        for name, data in self.personalities.items():
            cards.append(self._create_personality_card(name, data))
        
        # Display in columns
        self.console.print(Columns(cards, equal=True, expand=True))
        
        self.console.print("\n[dim]Press Enter to continue...[/]")
        input()
    
    def select_opponents(self, num_opponents: int = 2) -> List[str]:
        """Let user select opponents"""
        self.console.clear()
        self.console.print("[bold cyan]Choose Your Opponents[/]\n")
        
        # Create selection table
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", width=3)
        table.add_column("Name", style="cyan", width=15)
        table.add_column("Play Style", style="yellow")
        table.add_column("Signature Quote", style="italic")
        
        personality_list = list(self.personalities.items())
        for idx, (name, data) in enumerate(personality_list, 1):
            tic = data.get('verbal_tics', ["..."])[0]
            table.add_row(
                str(idx),
                name,
                data.get('play_style', 'unknown'),
                f'"{tic}"'
            )
        
        self.console.print(table)
        
        # Get selections
        selected = []
        while len(selected) < num_opponents:
            remaining = num_opponents - len(selected)
            prompt_text = f"\nSelect opponent {len(selected) + 1} of {num_opponents}"
            if remaining > 1:
                prompt_text += f" (or comma-separated list)"
            
            choice = Prompt.ask(prompt_text)
            
            # Handle comma-separated input
            choices = [c.strip() for c in choice.split(',')]
            
            for c in choices:
                if len(selected) >= num_opponents:
                    break
                    
                try:
                    idx = int(c) - 1
                    if 0 <= idx < len(personality_list):
                        selected_name = personality_list[idx][0]
                        if selected_name not in selected:
                            selected.append(selected_name)
                            self.console.print(f"[green]✓ Added {selected_name}[/]")
                        else:
                            self.console.print(f"[yellow]Already selected {selected_name}[/]")
                    else:
                        self.console.print(f"[red]Invalid selection: {c}[/]")
                except ValueError:
                    self.console.print(f"[red]Please enter a number[/]")
        
        return selected
    
    def quick_select(self, num_opponents: int = 2) -> List[str]:
        """Quickly select random opponents"""
        available = list(self.personalities.keys())
        if len(available) < num_opponents:
            return available
        
        selected = random.sample(available, num_opponents)
        
        # Show who was selected
        self.console.print("\n[bold cyan]Your opponents for this game:[/]\n")
        for name in selected:
            data = self.personalities[name]
            self.console.print(f"🎭 [bold]{name}[/] - {data.get('play_style', 'unknown')}")
        
        self.console.print("\n[dim]Starting game in 2 seconds...[/]")
        return selected