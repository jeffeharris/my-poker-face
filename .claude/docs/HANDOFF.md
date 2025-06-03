# Session Handoff - Fresh UI Build

## For Next Claude Session

### 1. Git Worktree Setup
```bash
# Create worktree for fresh UI
git worktree add ../my-poker-face-fresh-ui feature/fresh-rich-ui

# In the new worktree
cd ../my-poker-face-fresh-ui
```

### 2. Starting Context
"I want to build a fresh Rich CLI interface for the poker game. The poker engine in `poker/` is solid and should not be modified. The AI personality system with prompt management is working great. I need a new, clean UI that makes the game fun to play. Start by reading FRESH_START.md for the plan."

### 3. Key Constraints
- **Don't modify anything in `poker/` directory** (engine is good)
- **Use Rich library** for beautiful terminal UI
- **Focus on fun** - personality showcases, good pacing, reveals
- **30 second rule** - playable within 30 seconds of starting

### 4. First Sprint Goals (3-4 hours)
1. Create `fresh_ui/` directory structure
2. Basic menu system with personality selection
3. Game loop with Rich tables/panels
4. AI thinking animations
5. One full playable hand

### 5. Success Metrics
- Can play a full hand without crashes
- AI personalities are entertaining
- Want to immediately play another hand
- Clean, professional appearance

### 6. Technical Notes
- Use `poker.poker_game`, `poker.poker_state_machine` as-is
- Use `poker.controllers.AIPlayerController` for AI decisions  
- Keep UI state separate from game state
- Make it easy to add features later

### 7. Personality Showcase Priority
The AI personalities are the star of the show:
- Show personality cards during selection
- Display traits that affect gameplay  
- "Thinking" messages match personality
- Reveal bluffs dramatically

### 8. Dependencies
```bash
pip install rich textual blessed
```

## Files to Read First
1. `FRESH_START.md` - The plan
2. `poker/personalities.json` - Available personalities
3. `poker/prompt_manager.py` - How personalities work
4. `tests/test_prompt_management.py` - Personality examples

## Example Session Start
```
$ python -m fresh_ui

╔══════════════════════════════════════╗
║        MY POKER FACE 🎰              ║
║   Celebrity Poker with Attitude!     ║
╚══════════════════════════════════════╝

Enter your name: Jeff

Choose your opponents:
┌─────────────────────────────────────┐
│ 1. 🎭 Eeyore                        │
│    "Oh bother, another game..."     │
│    Style: Tight & Pessimistic       │
├─────────────────────────────────────┤
│ 2. 🎭 Donald Trump                  │
│    "I'm the best poker player!"     │
│    Style: Aggressive Bluffer        │
├─────────────────────────────────────┤
│ 3. 🎭 Gordon Ramsay                 │
│    "This game is RAW!"              │
│    Style: Confrontational           │
└─────────────────────────────────────┘

Select 2 opponents (e.g., 1,3): _
```

---

Good luck! Make it fun! 🎉