#!/usr/bin/env python3
"""
Demo script showing the personality elasticity system in action.

This demonstrates how AI personalities change dynamically during gameplay
based on game events like wins, losses, and bluffs.
"""

import os
import json
from dotenv import load_dotenv

from poker.poker_game import initialize_game_state, play_turn, advance_to_next_active_player
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.controllers import AIPlayerController
from poker.elasticity_manager import ElasticityManager
from poker.pressure_detector import PressureEventDetector
from poker.utils import get_celebrities
from console_app.ui_console import display_ai_player_action, display_cards, display_hand_winner

load_dotenv()

# Configuration
NUM_AI_PLAYERS = 5
SHOW_PRESSURE_UPDATES = True


def display_pressure_status(elasticity_manager: ElasticityManager):
    """Display current pressure and mood for all players."""
    print("\n=== PERSONALITY STATUS ===")
    for name, personality in elasticity_manager.personalities.items():
        avg_pressure = sum(t.pressure for t in personality.traits.values()) / len(personality.traits)
        current_mood = personality.get_current_mood()
        
        # Get trait changes
        trait_changes = []
        for trait_name, trait in personality.traits.items():
            if abs(trait.value - trait.anchor) > 0.1:
                change = trait.value - trait.anchor
                direction = "↑" if change > 0 else "↓"
                trait_changes.append(f"{trait_name}{direction}")
        
        changes_str = ", ".join(trait_changes) if trait_changes else "stable"
        print(f"{name:15} | Mood: {current_mood:12} | Pressure: {avg_pressure:+.2f} | Changes: {changes_str}")
    print("========================\n")


def main():
    print("=== Poker Personality Elasticity Demo ===\n")
    
    # Initialize AI players
    ai_player_names = get_celebrities(shuffled=True)[:NUM_AI_PLAYERS]
    print(f"Players: {', '.join(ai_player_names)}\n")
    
    # Initialize game
    game_state = initialize_game_state(player_names=ai_player_names)
    state_machine = PokerStateMachine(game_state=game_state)
    
    # Initialize elasticity system
    elasticity_manager = ElasticityManager()
    pressure_detector = PressureEventDetector(elasticity_manager)
    
    # Create controllers and add players to elasticity manager
    controllers = {}
    for player in state_machine.game_state.players:
        controller = AIPlayerController(player.name, state_machine)
        controllers[player.name] = controller
        
        # Add to elasticity manager
        elasticity_manager.add_player(
            player.name,
            controller.ai_player.personality_config
        )
    
    # Track game messages
    game_messages = []
    hands_played = 0
    
    # Main game loop
    while len([p for p in state_machine.game_state.players if p.stack > 0]) > 1:
        # Progress game to next action
        state_machine = state_machine.run_until_player_action()
        
        # Check for hand transitions
        if state_machine.phase == PokerPhase.DEALING_CARDS:
            community_cards = state_machine.game_state.community_cards
            if len(community_cards) in [0, 3, 4]:  # Pre-flop, flop, turn
                display_cards(community_cards, f"{state_machine.phase} Cards")
        
        # Handle AI actions
        if state_machine.phase in [PokerPhase.PRE_FLOP, PokerPhase.FLOP, 
                                   PokerPhase.TURN, PokerPhase.RIVER]:
            current_player = state_machine.game_state.current_player
            if current_player and not state_machine.game_state.awaiting_action:
                continue
                
            controller = controllers.get(current_player.name)
            if controller:
                # Update AI mood before decision
                if hasattr(controller.ai_player, 'update_mood_from_elasticity'):
                    controller.ai_player.update_mood_from_elasticity()
                
                # Get AI decision
                response = controller.decide_action(game_messages)
                action = response.get('action', 'fold')
                amount = response.get('adding_to_pot', response.get('amount', 0))
                
                # Display AI action with fallback values
                display_response = {
                    'action': action,
                    'adding_to_pot': amount,
                    'persona_response': response.get('persona_response', '...'),
                    'physical': response.get('physical', '*makes a move*')
                }
                display_ai_player_action(current_player.name, display_response)
                
                # Update game message history
                if 'persona_response' in response:
                    game_messages.append({
                        'sender': current_player.name,
                        'content': response['persona_response']
                    })
                
                # Apply action to game state
                new_game_state = play_turn(state_machine.game_state, action, amount)
                new_game_state = advance_to_next_active_player(new_game_state)
                state_machine = state_machine.with_game_state(new_game_state)
                
                # Detect fold events
                if action == 'fold':
                    remaining = [p for p in new_game_state.players if not p.is_folded]
                    events = pressure_detector.detect_fold_events(
                        new_game_state, current_player, remaining
                    )
                    pressure_detector.apply_detected_events(events)
        
        # Handle showdown and winner evaluation
        elif state_machine.phase == PokerPhase.EVALUATING_HAND:
            from poker.poker_game import determine_winner
            
            winner_info = determine_winner(state_machine.game_state)
            display_hand_winner(winner_info)
            
            # Detect pressure events from showdown
            events = pressure_detector.detect_showdown_events(
                state_machine.game_state, winner_info
            )
            pressure_detector.apply_detected_events(events)
            
            # Show pressure status after significant events
            if SHOW_PRESSURE_UPDATES and events:
                print(f"\nPressure Events Triggered: {[e[0] for e in events]}")
                display_pressure_status(elasticity_manager)
            
            # Apply trait recovery
            pressure_detector.apply_recovery()
            
            hands_played += 1
            
            # Continue to next hand
            state_machine = state_machine.advance()
        
        # Handle hand over transition
        elif state_machine.phase == PokerPhase.HAND_OVER:
            # Check for eliminated players
            eliminated = [p.name for p in state_machine.game_state.players if p.stack <= 0]
            if eliminated:
                events = pressure_detector.detect_elimination_events(
                    state_machine.game_state, eliminated
                )
                pressure_detector.apply_detected_events(events)
                
                if SHOW_PRESSURE_UPDATES:
                    print(f"\nPlayers Eliminated: {eliminated}")
                    display_pressure_status(elasticity_manager)
            
            # Every 3 hands, show personality status
            if hands_played % 3 == 0:
                display_pressure_status(elasticity_manager)
            
            state_machine = state_machine.advance()
        
        else:
            # Advance through other phases
            state_machine = state_machine.advance()
    
    # Game over
    winner = next(p for p in state_machine.game_state.players if p.stack > 0)
    print(f"\n=== GAME OVER ===")
    print(f"Winner: {winner.name} with ${winner.stack}")
    print("\nFinal Personality States:")
    display_pressure_status(elasticity_manager)


if __name__ == '__main__':
    main()