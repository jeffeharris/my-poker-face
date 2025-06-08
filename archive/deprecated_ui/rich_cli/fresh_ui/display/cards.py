"""Card rendering utilities for Rich display"""

from rich.panel import Panel
from rich.text import Text
from rich.columns import Columns
from typing import List, Dict, Optional

# Unicode card suits
SUITS = {
    'Spades': '♠',
    'Hearts': '♥',
    'Diamonds': '♦',
    'Clubs': '♣'
}

# Suit colors
SUIT_COLORS = {
    'Spades': 'white',
    'Hearts': 'red',
    'Diamonds': 'red',
    'Clubs': 'white'
}


def render_card(card, hidden: bool = False) -> Panel:
    """Render a single card as a Rich Panel"""
    if hidden:
        content = Text("?", justify="center", style="bold cyan")
        return Panel(content, width=7, height=5, style="cyan")
    
    # Handle both Card objects and dictionaries
    if hasattr(card, 'rank'):
        rank = card.rank
        suit = card.suit
    else:
        rank = card.get('rank', '?')
        suit = card.get('suit', 'Spades')
    
    suit_symbol = SUITS.get(suit, '?')
    color = SUIT_COLORS.get(suit, 'white')
    
    # Create card content
    content = Text(f"{rank}\n{suit_symbol}", style=f"bold {color}", justify="center")
    return Panel(content, width=7, height=5, style=color)


def render_hand(cards: List[Dict], hidden: bool = False) -> Columns:
    """Render a poker hand as columns of cards"""
    if not cards:
        return Columns([Panel("No cards", width=7, height=5, style="dim")])
    
    card_panels = [render_card(card, hidden) for card in cards]
    return Columns(card_panels, padding=1)


def render_community_cards(cards: List[Dict], max_cards: int = 5) -> Columns:
    """Render community cards with placeholders for undealt cards"""
    panels = []
    
    # Add revealed cards
    for card in cards:
        panels.append(render_card(card))
    
    # Add placeholders for remaining cards
    for _ in range(len(cards), max_cards):
        placeholder = Panel("", width=7, height=5, style="dim white")
        panels.append(placeholder)
    
    return Columns(panels, padding=1)


def card_to_string(card) -> str:
    """Convert card dict/object to string representation"""
    # Handle both Card objects and dictionaries
    if hasattr(card, 'rank'):
        rank = card.rank
        suit = card.suit
    else:
        rank = card.get('rank', '?')
        suit = card.get('suit', 'Spades')
    
    suit_symbol = SUITS.get(suit, '?')
    return f"{rank}{suit_symbol}"


def hand_to_string(cards: List[Dict]) -> str:
    """Convert hand to string representation"""
    return " ".join(card_to_string(card) for card in cards)