"""Hand strength indicator for Rich display"""

from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn
from rich.text import Text
from typing import List, Tuple

def get_hand_rank_name(rank: int) -> str:
    """Convert hand rank to readable name"""
    hand_names = {
        10: "Royal Flush",
        9: "Straight Flush", 
        8: "Four of a Kind",
        7: "Full House",
        6: "Flush",
        5: "Straight",
        4: "Three of a Kind",
        3: "Two Pair",
        2: "One Pair",
        1: "High Card"
    }
    return hand_names.get(rank, "Unknown")

def evaluate_hand_strength(cards: List, community_cards: List) -> Tuple[str, int]:
    """Simple hand evaluation for display purposes"""
    # This is a placeholder - would integrate with actual hand evaluator
    # For now, return mock data
    return "Pair of Kings", 3

def render_hand_strength(player_cards: List, community_cards: List) -> Panel:
    """Render a hand strength indicator"""
    if not player_cards:
        return Panel("[dim]No cards[/]", title="Hand Strength", style="dim")
    
    # Get hand evaluation
    hand_name, strength = evaluate_hand_strength(player_cards, community_cards)
    
    # Create strength bar
    strength_text = Text()
    strength_bar = "●" * strength + "○" * (10 - strength)
    
    # Color based on strength
    if strength >= 7:
        color = "green"
        label = "Strong"
    elif strength >= 4:
        color = "yellow"
        label = "Medium"
    else:
        color = "red"
        label = "Weak"
    
    content = f"[bold]{hand_name}[/bold]\n"
    content += f"[{color}]{strength_bar}[/{color}] {label}"
    
    return Panel(content, title="Hand Strength", style=color, width=30)