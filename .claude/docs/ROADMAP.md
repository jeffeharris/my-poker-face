# My Poker Face - Roadmap to Core Working Game

## Current State (Reality Check)
- ✅ Core poker engine works (state machine, hand evaluation)
- ✅ AI personalities are amazing with the new prompt system
- ❌ Can't actually start and play a game easily
- ❌ Web and console UIs are both broken/incomplete
- ❌ Too many half-implemented features

## Goal: Minimum Playable Game (MPG)
**One working path where a user can play a complete, fun game of poker against AI opponents.**

## Phase 1: Pick ONE Interface (Week 1)
Choose ONE to focus on:
- [ ] **Option A: Console** - Simpler, works now, good for testing
- [ ] **Option B: Web** - Better UX potential, but needs more work

**Recommendation: Start with Console, it's closer to working**

## Phase 2: Fix Core Flow (Week 1-2)

### Console Path:
1. **Game Setup**
   - [ ] Remove hardcoded "Jeff"
   - [ ] Simple menu: Enter name → Choose opponents → Start
   - [ ] Pre-made opponent sets: "Easy", "Medium", "Chaos"

2. **Game Loop**
   - [ ] Fix the main game loop to handle multiple hands
   - [ ] Clear display of game state each turn
   - [ ] Proper hand endings with winner reveal

3. **Polish**
   - [ ] Show AI cards after folding
   - [ ] Better formatting (use card ASCII art)
   - [ ] "Press Enter to continue" flow

### Web Path (if chosen):
1. **Fix Routes**
   - [ ] `/` - Working home page
   - [ ] `/setup` - Game configuration 
   - [ ] `/game` - Actual game with SocketIO

2. **Simplify**
   - [ ] No rooms/multiplayer yet
   - [ ] Just you vs AI
   - [ ] Basic UI that works

## Phase 3: Make It Fun (Week 2-3)

### Essential Fun Features:
1. **AI Personality Showcase**
   - [ ] Show personality cards during setup
   - [ ] Display traits that affect gameplay
   - [ ] Fun descriptions/catchphrases

2. **Game Feedback**
   - [ ] "Gordon is thinking..." messages
   - [ ] Reveal bluffs: "Trump was bluffing!"
   - [ ] Win/loss summaries

3. **Quick Restart**
   - [ ] "Play again" with same opponents
   - [ ] Running chip count across games
   - [ ] Simple stats: Hands won/lost

## Phase 4: Polish & Ship (Week 3-4)

### Must Have:
- [ ] README with clear instructions
- [ ] Error handling (API failures, etc.)
- [ ] At least 3 working AI personality sets
- [ ] One complete game path that always works

### Nice to Have:
- [ ] Achievements
- [ ] More personalities
- [ ] Sound effects
- [ ] Better UI

## What We're NOT Doing (Yet)
- ❌ Multiplayer
- ❌ Tournament mode
- ❌ Complex betting structures
- ❌ Hand history database
- ❌ AI learning/adaptation
- ❌ Multiple game types

## Success Metrics
1. **Can a new user play within 30 seconds?**
2. **Do they laugh at the AI personalities?**
3. **Do they want to play again?**

## Next Steps
1. **Decide**: Console or Web?
2. **Create** `GAMEPLAN.md` for the chosen path
3. **Branch**: `feature/core-game-loop`
4. **Focus**: One feature at a time

---

## Quick Decision Framework

### Choose Console If:
- Want something working TODAY
- Testing AI is priority
- OK with basic UI

### Choose Web If:
- Need pretty UI
- Want to share with friends
- OK with 2x more work

**My Recommendation: Console first, then port to web once it's fun**