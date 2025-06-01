#!/usr/bin/env python3
"""Improved poker game with better UI"""

import time
from collections import deque
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.layout import Layout
from rich.align import Align

from poker.poker_game import initialize_game_state, play_turn, advance_to_next_active_player
from poker.poker_state_machine import PokerStateMachine
from fresh_ui.display.cards import render_hand, render_community_cards
from fresh_ui.display.pot_odds import render_pot_odds
from fresh_ui.utils.mock_ai import MockAIController

console = Console()
chat_messages = deque(maxlen=10)
action_history = deque(maxlen=5)

def add_chat(sender, message, color="white"):
    """Add message to chat"""
    timestamp = time.strftime("%H:%M")
    chat_messages.append(f"[dim]{timestamp}[/] [{color}]{sender}:[/] {message}")

def add_action(player, action, amount=0):
    """Add action to history with icon"""
    action_icons = {
        'fold': '📂',
        'call': '📞', 
        'raise': '💰',
        'check': '✓',
        'all-in': '🎯'
    }
    icon = action_icons.get(action, '•')
    if amount > 0:
        action_history.append(f"{icon} {player}: ${amount}")
    else:
        action_history.append(f"{icon} {player}")

def render_player_position(player, is_current, position=""):
    """Render a player's position at the table"""
    status_icon = "🎯" if is_current else ("💤" if player.is_folded else "⭕")
    
    content = f"{status_icon} {player.name}\n"
    content += f"💰 ${player.stack}\n"
    if player.bet > 0:
        content += f"Bet: ${player.bet}"
    
    style = "yellow" if is_current else ("dim" if player.is_folded else "white")
    return Panel(content, style=style, width=20, height=5)

def show_improved_game_state(state_machine):
    """Display improved game state"""
    console.clear()
    game_state = state_machine.game_state
    
    # Create main layout
    layout = Layout()
    
    # Header
    header_text = f"🎰 MY POKER FACE | 💰 Pot: ${game_state.pot['total']} | 🎯 {state_machine.current_phase.name}"
    header = Panel(header_text, style="bold cyan")
    
    # Poker table visualization
    table_layout = Layout()
    table_layout.split_column(
        Layout(name="top_player", size=5),
        Layout(name="middle_section", size=10),
        Layout(name="bottom_player", size=5)
    )
    
    # Position players around the table
    players = game_state.players
    if len(players) >= 3:
        # Top player (opponent 2)
        top_player = render_player_position(
            players[2], 
            game_state.current_player_idx == 2,
            "top"
        )
        table_layout["top_player"].update(Align.center(top_player))
        
        # Middle section with cards and side player
        middle_layout = Layout()
        middle_layout.split_row(
            Layout(render_player_position(
                players[1],
                game_state.current_player_idx == 1,
                "left"
            ), size=20),
            Layout(name="cards_area"),
            Layout(name="info_panel", size=30)
        )
        
        # Community cards in the center
        if game_state.community_cards:
            cards = render_community_cards(list(game_state.community_cards))
            community_panel = Panel(cards, title="🃏 Community Cards", style="green")
        else:
            community_panel = Panel("[dim]Dealing...[/]", title="🃏 Community Cards", style="green")
        
        middle_layout["cards_area"].update(Align.center(community_panel))
        
        # Info panel on the right
        info_layout = Layout()
        info_layout.split_column(
            Layout(name="pot_odds", size=6),
            Layout(name="hand_info", size=4)
        )
        
        # Pot odds for human player
        human = players[0]
        if human.is_human and not human.is_folded:
            to_call = max(0, game_state.highest_bet - human.bet)
            pot_odds_panel = render_pot_odds(game_state.pot['total'], to_call)
            info_layout["pot_odds"].update(pot_odds_panel)
        
        middle_layout["info_panel"].update(info_layout)
        table_layout["middle_section"].update(middle_layout)
        
        # Bottom player (you)
        bottom_player = render_player_position(
            players[0],
            game_state.current_player_idx == 0,
            "bottom"
        )
        table_layout["bottom_player"].update(Align.center(bottom_player))
    
    # Your cards
    human = next((p for p in game_state.players if p.is_human), None)
    if human and human.hand and not human.is_folded:
        cards = render_hand(list(human.hand))
        your_cards = Panel(cards, title=f"🎴 Your Hand", style="blue")
    else:
        your_cards = Panel("[dim]No cards[/]", title="🎴 Your Hand", style="dim")
    
    # Side panels
    action_text = "\n".join(action_history) if action_history else "[dim]No actions yet[/]"
    action_panel = Panel(action_text, title="⚡ Actions", style="yellow", height=6)
    
    chat_text = "\n".join(chat_messages) if chat_messages else "[dim]No messages yet[/]"
    chat_panel = Panel(chat_text, title="💬 Chat", style="cyan", height=10)
    
    # Build main layout
    layout.split_column(
        Layout(header, size=3),
        Layout(name="main", size=20),
        Layout(your_cards, size=8)
    )
    
    layout["main"].split_row(
        Layout(table_layout, ratio=2),
        Layout(name="sidebar", ratio=1)
    )
    
    layout["main"]["sidebar"].split_column(
        action_panel,
        chat_panel
    )
    
    console.print(layout, height=35)

def main():
    console.print("[cyan]Welcome to My Poker Face - Improved UI![/]\n")
    
    # Setup
    ai_names = ["Gordon Ramsay", "Bob Ross"]
    state_machine = PokerStateMachine(initialize_game_state(ai_names))
    
    ai_controllers = {}
    for name in ai_names:
        ai_controllers[name] = MockAIController(name, state_machine)
    
    # Initial messages
    add_chat("System", "Welcome to the improved UI!", "green")
    add_chat("Gordon", "This UI is RAW! But in a good way!", "red")
    add_chat("Bob", "Happy little cards everywhere...", "blue")
    
    # Game loop
    while True:
        # Advance game
        state_machine.run_until_player_action()
        
        # Show state
        show_improved_game_state(state_machine)
        
        # Check phase
        if state_machine.current_phase.name == "HAND_OVER":
            add_chat("System", "Hand complete! 🎉", "green")
            show_improved_game_state(state_machine)
            Prompt.ask("\n[yellow]Press Enter for next hand[/yellow]", default="")
            continue
            
        # Get current player
        current = state_machine.game_state.current_player
        
        if current.is_human:
            # Human turn with quick keys
            to_call = max(0, state_machine.game_state.highest_bet - current.bet)
            
            # Build choices
            choices = []
            choice_map = {}
            
            if to_call > 0:
                choices.append(f"[C] Call ${to_call}")
                choice_map["c"] = "call"
                choice_map["1"] = "call"
            else:
                choices.append(f"[C] Check")
                choice_map["c"] = "check"
                choice_map["1"] = "check"
            
            choices.append(f"[F] Fold")
            choice_map["f"] = "fold"
            choice_map["2"] = "fold"
            
            if current.stack > 0:
                min_raise = state_machine.game_state.highest_bet * 2 if state_machine.game_state.highest_bet > 0 else 50
                choices.append(f"[R] Raise (min ${min_raise})")
                choice_map["r"] = "raise"
                choice_map["3"] = "raise"
            
            # Display choices
            console.print("\n[bold yellow]Your turn![/bold yellow]")
            for choice in choices:
                console.print(f"  {choice}")
            
            # Get input with quick keys
            selection = Prompt.ask(
                "\n[bold cyan]Action[/bold cyan]",
                default="c" if to_call == 0 else None
            ).lower()
            
            action = choice_map.get(selection)
            if not action:
                console.print("[red]Invalid selection![/red]")
                time.sleep(1)
                continue
            
            amount = 0
            if action == "raise":
                min_raise = state_machine.game_state.highest_bet * 2 if state_machine.game_state.highest_bet > 0 else 50
                amount = IntPrompt.ask(
                    f"Raise amount (min ${min_raise})",
                    default=min_raise
                )
                amount = max(min_raise, min(amount, current.stack))
            
            add_action("You", action, amount)
        else:
            # AI turn
            time.sleep(1)
            response = ai_controllers[current.name].decide_action([])
            action = response['action']
            amount = response['adding_to_pot']
            message = response.get('persona_response', '')
            
            add_chat(current.name, message, "cyan")
            add_action(current.name, action, amount)
            
            show_improved_game_state(state_machine)
            time.sleep(2)
        
        # Process action
        state_machine.game_state = play_turn(state_machine.game_state, action, amount)
        state_machine.game_state = advance_to_next_active_player(state_machine.game_state)

if __name__ == "__main__":
    import sys
    try:
        if not sys.stdin.isatty():
            console.print("[red]This game requires an interactive terminal![/red]")
            sys.exit(1)
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Thanks for playing![/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()