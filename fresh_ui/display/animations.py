"""Animation utilities for Rich display"""

import time
import random
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from rich.align import Align
from typing import List, Callable


def thinking_animation(console: Console, player_name: str, duration: float = 2.0):
    """Show a thinking animation for AI players"""
    thoughts = [
        "calculating odds...",
        "analyzing tells...",
        "considering options...",
        "evaluating hand strength...",
        "planning strategy...",
        "reading the table..."
    ]
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True
    ) as progress:
        thought = random.choice(thoughts)
        task = progress.add_task(f"[yellow]🎭 {player_name} is {thought}[/]", total=None)
        time.sleep(duration)


def dramatic_reveal(console: Console, text: str, delay: float = 0.5):
    """Dramatically reveal text with a delay"""
    console.print(f"\n[bold cyan]{'=' * 40}[/]")
    time.sleep(delay)
    console.print(Align.center(Text(text, style="bold yellow")))
    console.print(f"[bold cyan]{'=' * 40}[/]\n")
    time.sleep(delay)


def dealing_animation(console: Console, num_cards: int = 2):
    """Show card dealing animation"""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True
    ) as progress:
        task = progress.add_task("[cyan]Dealing cards...[/]", total=num_cards)
        for i in range(num_cards):
            time.sleep(0.3)
            progress.update(task, advance=1)


def pot_win_animation(console: Console, winner: str, amount: int):
    """Show pot winning animation"""
    frames = ["💰", "💵", "💸", "🤑", "🎉"]
    
    for frame in frames:
        console.print(f"\r{frame} [bold green]{winner} wins ${amount}![/]", end="")
        time.sleep(0.2)
    console.print()


def personality_intro(console: Console, name: str, tagline: str, style: str):
    """Show personality introduction animation"""
    console.print()
    with console.status(f"[bold yellow]🎭 {name} is entering the game...[/]"):
        time.sleep(1.5)
    
    console.print(f"[bold {style}]🎭 {name}[/]")
    console.print(f"[italic]\"{tagline}\"[/]")
    console.print(f"[dim]Playing style: {style}[/]")
    console.print()
    time.sleep(1)