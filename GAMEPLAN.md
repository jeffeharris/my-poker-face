# Console Game - Implementation Plan

## Goal: Working Console Poker Game in 3 Days

### Day 1: Core Game Setup
**Branch: `feature/console-game-mvp`**

#### Task 1: Game Configuration (2 hours)
```python
# New file: poker/game_config.py
@dataclass
class GameConfig:
    player_name: str
    ai_opponents: List[str]
    starting_chips: int = 10000
    big_blind: int = 100
```

- [ ] Remove hardcoded "Jeff" from `initialize_game_state()`
- [ ] Create `setup_console_game()` function
- [ ] Menu system:
  ```
  Welcome to My Poker Face!
  
  Enter your name: Alice
  
  Choose your opponents:
  1. Easy Table (Bob Ross, Eeyore)
  2. Celebrity Showdown (Trump, Gordon, Batman)  
  3. Chaos Mode (Mime, Ace Ventura, Joker)
  4. Custom selection
  
  Your choice: 2
  ```

#### Task 2: Main Game Loop (3 hours)
- [ ] Create `console_game_loop()` in new file
- [ ] Handle multiple hands until someone busts
- [ ] Proper state transitions
- [ ] Clean display each turn

#### Task 3: Basic Testing (1 hour)
- [ ] Test full game flow
- [ ] Ensure AI personalities load correctly
- [ ] Handle edge cases (all-in, everyone folds, etc.)

### Day 2: User Experience

#### Task 1: Better Display (2 hours)
- [ ] ASCII art for cards
- [ ] Clear screen between actions
- [ ] Color support (optional)
- [ ] Status bar: `Pot: $450 | Your Stack: $9,850 | To Call: $100`

#### Task 2: AI Personality Integration (2 hours)
- [ ] Show personality intro when game starts
- [ ] Display "thinking" messages
- [ ] Show AI cards when they fold/lose
- [ ] Personality-specific win/loss messages

#### Task 3: Game Flow Polish (2 hours)
- [ ] Pause points that make sense
- [ ] "Show cards" option after folding
- [ ] Hand history (last 3 hands)
- [ ] Graceful exit option

### Day 3: Fun Factor & Polish

#### Task 1: Pre-game Personality Showcase (2 hours)
```
Your opponents tonight:

🎭 Donald Trump
   "I make the best poker plays, believe me!"
   Style: Aggressive bluffer
   Watch out for: Massive raises

🎭 Gordon Ramsay  
   "This poker game is RAW!"
   Style: Confrontational
   Watch out for: Intimidation tactics

🎭 Batman
   "I am the night... and I'm all-in"
   Style: Strategic and mysterious
   Watch out for: Calculated plays
```

#### Task 2: In-game Enhancements (2 hours)
- [ ] Bluff reveals: "Gordon was bluffing with 7-2!"
- [ ] Milestone messages: "You've won 5 hands!"
- [ ] Chip leader notifications
- [ ] AI reactions to big wins/losses

#### Task 3: End Game (2 hours)
- [ ] Tournament complete screen
- [ ] Statistics: Hands played, biggest pot, etc.
- [ ] "Play again?" with same or different opponents
- [ ] Save basic stats to file

## File Structure
```
poker/
  game_config.py      # New: Game configuration
  console_runner.py   # New: Main console game loop
  
console_app/
  ui_console.py      # Refactor: Just UI components
  menus.py          # New: Menu system
  display.py        # New: Card/table display
```

## Testing Checklist
- [ ] Can start game in < 30 seconds
- [ ] Can play 10 hands without crashes
- [ ] AI personalities feel different
- [ ] Want to play again after losing
- [ ] Clear what's happening at all times

## Code Patterns to Follow
```python
# Clear separation of concerns
game_config = setup_console_game()  # Get user input
game_state = create_game(game_config)  # Initialize game
result = run_game_loop(game_state)  # Play the game
show_results(result)  # Display winner

# Always show what's happening
print("Gordon is thinking...")
time.sleep(1)  # Brief pause for realism
print('Gordon says: "This is pathetic! I raise $500!"')
print("Gordon raises to $500")
```

## What Success Looks Like
By end of Day 3:
1. Video of full game session (5-10 minutes)
2. At least 3 people play it and have fun
3. No crashes or confusion
4. Players understand AI personalities
5. They want to play again

## Daily Checkpoints
- **Day 1 End**: Can play one full hand with chosen opponents
- **Day 2 End**: Full game works, looks decent
- **Day 3 End**: It's actually fun to play!

---

Ready to start? First task: Create the new branch and remove "Jeff"!