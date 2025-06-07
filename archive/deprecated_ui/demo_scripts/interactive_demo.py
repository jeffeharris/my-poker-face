#!/usr/bin/env python3
"""Interactive demo to showcase AI personalities playing poker."""

import os
from dotenv import load_dotenv

# Load environment variables (override=True to use .env over shell variables)
load_dotenv(override=True)

# Now import the poker modules
from poker.poker_game import PokerGameState, Player, setup_hand
from poker.poker_state_machine import PokerStateMachine, GamePhase
from poker.controllers import AIPlayerController
from core.card import Card

def create_simple_game():
    """Create a simple 4-player AI game."""
    players = [
        Player(name="Eeyore", stack=10000, is_human=False),
        Player(name="Donald Trump", stack=10000, is_human=False),
        Player(name="Gordon Ramsay", stack=10000, is_human=False),
        Player(name="Bob Ross", stack=10000, is_human=False),
    ]
    
    game_state = PokerGameState(players=tuple(players))
    return game_state

def display_cards(cards):
    """Display cards in a readable format."""
    return ' '.join([f"{c['rank']}{c['suit']}" for c in cards])

def main():
    print("🎰 Interactive AI Poker Demo 🎰")
    print("Watch how different personalities play!\n")
    
    # Create game
    game_state = create_simple_game()
    state_machine = PokerStateMachine(game_state)
    
    # Setup hand
    print("Setting up a new hand...")
    # Advance state machine until we need player action
    state_machine.run_until_player_action()
    
    # Create AI controllers
    ai_controllers = {}
    for player in game_state.players:
        ai_controllers[player.name] = AIPlayerController(
            player.name, state_machine, ai_temp=0.7
        )
    
    print("\nStarting positions:")
    for i, player in enumerate(state_machine.game_state.players):
        role = ""
        if i == state_machine.game_state.current_dealer_idx:
            role = " (Dealer)"
        elif i == state_machine.game_state.small_blind_idx:
            role = " (Small Blind)"
        elif i == state_machine.game_state.big_blind_idx:
            role = " (Big Blind)"
        print(f"  {player.name}: ${player.stack}{role}")
    
    print(f"\nPot: ${state_machine.game_state.pot['total']}")
    print("\nPress Enter to see each action...")
    
    # Track messages
    messages = []
    
    # Play one betting round
    actions = 0
    while actions < 20 and state_machine.game_state.awaiting_action:
        input()  # Wait for user
        
        current = state_machine.game_state.current_player
        if not current:
            break
            
        print(f"\n{'='*50}")
        print(f"{current.name}'s turn (${current.stack})")
        
        # Show current situation
        if state_machine.game_state.community_cards:
            print(f"Community: {display_cards(state_machine.game_state.community_cards)}")
        print(f"Pot: ${state_machine.game_state.pot['total']}")
        print(f"To call: ${state_machine.game_state.highest_bet - current.bet}")
        
        # Get AI decision
        controller = ai_controllers[current.name]
        
        try:
            response = controller.decide_action(messages)
            
            # Show personality
            ai = controller.ai_player
            print(f"\nPersonality: {ai.personality_config['play_style']}")
            print(f"Traits: Bluff {ai.personality_config['personality_traits']['bluff_tendency']:.0%}, "
                  f"Aggression {ai.personality_config['personality_traits']['aggression']:.0%}")
            
            # Show decision
            action = response.get('action', 'fold')
            amount = response.get('adding_to_pot', 0)
            
            print(f"\nDecision: {action.upper()}" + (f" ${amount}" if amount > 0 else ""))
            print(f"Says: \"{response.get('persona_response', '...')}\"")
            
            if response.get('physical'):
                print(f"*{', '.join(response.get('physical', []))}*")
            
            # Add to messages
            if response.get('persona_response'):
                messages.append({
                    'sender': current.name,
                    'content': response['persona_response']
                })
            
            # Process action
            if action == 'fold':
                state_machine.process_fold()
            elif action == 'check':
                state_machine.process_check()
            elif action == 'call':
                state_machine.process_call()
            elif action == 'raise':
                state_machine.process_raise(amount)
            elif action == 'all_in':
                state_machine.process_all_in()
                
        except Exception as e:
            print(f"Error: {e}")
            state_machine.process_fold()
        
        actions += 1
    
    print(f"\n{'='*50}")
    print("Betting round complete!")
    print(f"Final pot: ${state_machine.game_state.pot['total']}")
    
    # Show remaining players
    active = [p for p in state_machine.game_state.players if not p.is_folded]
    print(f"\nPlayers still in: {len(active)}")
    for player in active:
        print(f"  {player.name}")

if __name__ == "__main__":
    main()