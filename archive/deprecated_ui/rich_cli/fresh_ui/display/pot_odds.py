"""Pot odds calculator for Rich display"""

from rich.panel import Panel
from rich.text import Text

def calculate_pot_odds(pot_size: int, call_amount: int) -> tuple:
    """Calculate pot odds and return percentage"""
    if call_amount == 0:
        return 0, "N/A"
    
    total_pot = pot_size + call_amount
    odds_percent = (call_amount / total_pot) * 100
    
    # Simple evaluation
    if odds_percent < 20:
        evaluation = "Excellent"
        color = "green"
    elif odds_percent < 33:
        evaluation = "Good"
        color = "yellow"
    else:
        evaluation = "Poor"
        color = "red"
    
    return odds_percent, evaluation, color

def render_pot_odds(pot_size: int, call_amount: int) -> Panel:
    """Render pot odds panel"""
    if call_amount == 0:
        return Panel("[dim]No bet to call[/]", title="Pot Odds", style="dim", width=25)
    
    odds_pct, evaluation, color = calculate_pot_odds(pot_size, call_amount)
    
    content = f"Pot: ${pot_size}\n"
    content += f"To Call: ${call_amount}\n"
    content += f"Odds: {odds_pct:.1f}%\n"
    content += f"[{color}]{evaluation}[/{color}]"
    
    return Panel(content, title="📊 Pot Odds", style=color, width=25)