# Feature Ideas & Brainstorming

## Tournament Narrative System

### Overview
Create a living tournament where other tables exist and generate storylines that affect the player's experience.

### Implementation Ideas
- **Probability-Based Simulation**: Use actual dealt cards to weight outcomes at other tables
- **Personality Matchups**: Aggressive vs passive players create predictable dynamics
- **News Flash System**: 
  ```
  "BREAKING: Sherlock Holmes just eliminated Batman with pocket aces!"
  "Table 3 Update: The aggressive players are dominating"
  "You're in 15th place, but Trump just busted out at Table 2!"
  ```
- **Emergent Narratives**: Track "storylines" across the tournament
  - Rivalry developing between two AIs
  - Underdog making a comeback
  - Aggressive player on a rampage

### Player Impact
- Your reputation affects how news about you spreads
- Other tables might "hear" about your big bluffs
- Build tournament-wide reputation

---

## Evolution & Progression System

### XP from Drama
Instead of just winning money, gain experience from creating memorable moments:

```python
xp_events = {
    "successful_bluff": 50,          # Pulled off a bluff
    "correct_read": 40,              # Called someone's bluff correctly  
    "rapport_building": 30,          # Improved someone's mood via chat
    "survival": 20,                  # Survived elimination when short-stacked
    "friendship_formed": 100,        # AI personality now likes you
    "rivalry_created": 80,           # Started a feud
    "dramatic_comeback": 150,        # Won after being down to <10% chips
    "table_captain": 60,            # Controlled table dynamics for 5+ hands
    "mood_manipulator": 70,         # Changed 3+ players' moods in one session
}
```

### Skill Unlocks
Progressive abilities that enhance the social poker experience:

1. **Tell Detection** (Levels 1-5)
   - Level 1: See obvious physical tells
   - Level 3: Notice betting patterns
   - Level 5: Subtle mood indicators

2. **Mind Reading** (Levels 5-10)
   - Level 5: Occasional inner thought glimpses
   - Level 7: See stress levels
   - Level 10: Full emotional state access

3. **Social Engineering** (Levels 3-15)
   - Level 3: Basic chat influence
   - Level 8: Rapport building accelerated
   - Level 15: Can trigger mood swings

4. **Cheating Skills** (Secret Unlocks)
   - Card marking (risky, might get caught)
   - Chip sliding (small advantages)
   - Partnership signals (coordinate with friendly AI)

---

## Chat & Social Dynamics

### The Chat Problem
Typing while playing poker is challenging. Solutions:

1. **Quick Chat Wheel**
   ```
   [Friendly] → "Nice hand!" / "Good luck everyone" / "Love the energy!"
   [Taunting] → "That's all?" / "Scared money" / "I can see your tell"
   [Strategic] → "Big hand here" / "Just warming up" / "Interesting bet"
   ```

2. **Emoji Reaction System**
   - Quick emotional responses
   - Different personalities respond to different emojis
   - Can build "emoji combos" for effects

3. **Voice Recognition** (Future)
   - Talk naturally to AI players
   - They respond to tone and emotion
   - More immersive experience

4. **Auto-Pause on Chat**
   - Game slows when someone types
   - "Thinking time" increases
   - Prevents rushed decisions

### Dynamic Conversation System (Enhanced) ✅ IMPLEMENTED
Building on prompt improvements, AI conversation becomes more natural (implemented via elasticity system and prompt config):

1. **Chattiness-Based Speaking**
   - Low chattiness (0.0-0.3): Mostly silent, speaks only when necessary
   - Medium chattiness (0.4-0.6): Comments on big moments
   - High chattiness (0.7-1.0): Regular table talk and banter
   - Context modifiers: Big pots, direct addresses, table silence

2. **Trait-Influenced Language**
   - Aggression affects word strength ("maybe" vs "definitely")
   - Bluff tendency affects certainty expressions
   - Current mood colors vocabulary choices
   - Personality maintains voice while traits add variation

### Rapport Building Mechanics

```python
rapport_factors = {
    "compliment_play": +0.1,
    "sympathize_loss": +0.2,
    "share_interests": +0.3,      # "I love painting too, Bob!"
    "personality_match": +0.2,     # Some personalities click naturally
    "win_together": +0.1,          # Both beat another player
    "defend_from_bully": +0.4,    # Stand up for them
}
```

---

## Personality Mixer

### Quick Win Feature
Combine two personalities to create hybrids:

```python
def mix_personalities(p1, p2):
    return {
        "name": f"{p1['name']} + {p2['name']}",
        "play_style": merge_styles(p1, p2),
        "traits": average_traits(p1, p2),
        "verbal_tics": mix_lists(p1['verbal_tics'], p2['verbal_tics']),
        "special_ability": generate_combo_ability(p1, p2)
    }
```

### Example Combinations
- **Sherlock + Gordon Ramsay** = "The Deductive Chef"
  - Ultra-analytical but explosive when frustrated
  - "Elementary, you DONKEY! The flush draw was obvious!"

- **Eeyore + Trump** = "The Pessimistic Tycoon"  
  - Aggressive but expects to lose
  - "I'm raising BIGLY... but I'll probably lose anyway"

- **Bob Ross + Batman** = "The Peaceful Vigilante"
  - Calm but strategic
  - "Let's paint a happy little justice"

---

## Multi-Model AI Integration ✅ IMPLEMENTED

> **Status:** Core multi-provider support is complete. 7 providers supported: OpenAI, Anthropic, Groq, DeepSeek, Mistral, Google, xAI. See `core/llm/` for implementation.

### Different Thinking Styles
Each AI model has characteristic "tells":

```python
model_characteristics = {
    "claude": {
        "thinking_style": "analytical_verbose",
        "tell": "Considers multiple angles before acting",
        "strength": "Complex reasoning",
        "weakness": "Overthinking simple spots"
    },
    "gpt-4": {
        "thinking_style": "creative_confident", 
        "tell": "Quick decisive actions",
        "strength": "Adaptability",
        "weakness": "Occasional overconfidence"
    },
    "llama": {
        "thinking_style": "efficient_direct",
        "tell": "Straightforward patterns",
        "strength": "Consistency", 
        "weakness": "Predictability"
    }
}
```

---

## Visual Personality System

### Pre-Generated Emotional States
Batch generate character images for different states:

```
sherlock_holmes/
  ├── neutral.png
  ├── confident.png
  ├── bluffing.png
  ├── frustrated.png
  ├── victorious.png
  ├── defeated.png
  ├── thinking.png
  └── suspicious.png
```

### Dynamic Display Logic
```python
def get_character_image(personality, game_state):
    if personality.is_bluffing:
        return f"{personality.name}/bluffing.png"
    elif personality.just_won_big:
        return f"{personality.name}/victorious.png"
    elif personality.mood == "frustrated":
        return f"{personality.name}/frustrated.png"
    # etc...
```

### Micro-Expressions
Quick image flashes that reveal true emotions:
- Flash "nervous.png" for 200ms during a bluff
- Show "excited.png" briefly with a strong hand
- Player skill determines if they catch these tells

---

## Difficulty Innovation

### Information Distortion
Lower difficulties don't just play worse—they perceive worse:

**Expert Mode**:
```
"You have 7♥7♦. Board: K♠Q♠J♣. Pot: $534. To call: $100. 
Pot odds: 5.34:1. SPR: 8.2. Position: BTN"
```

**Beginner Mode**:
```
"You have a pair of sevens. Some high cards are out there. 
The pot is pretty big. Someone bet."
```

**Drunk Mode** (Special):
```
"You have... wait, sevens? Or sixes? The board is... 
colorful. Someone said something about money?"
```

---

## Long-Term Memory System ✅ PARTIALLY IMPLEMENTED

> **Status:** Working memory and short-term memory implemented via `SessionMemory` and `OpponentModelManager`. Long-term cross-session memory and markdown files not yet implemented. See `poker/memory/` for implementation.

### Enhanced Memory Architecture
Building on prompt improvements, implement multi-layered memory:

1. **Working Memory** (Current Hand)
   - Track betting patterns within the hand
   - Remember who showed strength/weakness
   - Maintain consistency with stated strategy
   - Influences immediate decisions

2. **Short-Term Memory** (Current Session)
   - Recent 10 hands for tactical adjustments
   - Player-specific patterns and tendencies
   - Emotional context from last 5 minutes
   - Affects strategy and conversation

3. **Long-Term Memory** (Cross-Session)
   - Persistent relationship dynamics
   - Memorable moments and signature moves
   - Personality evolution tracking
   - Deep behavioral patterns

### Markdown Memory Files
Store relationship history in human-readable format:

`memories/player_vs_sherlock.md`:
```markdown
# Relationship with Sherlock Holmes

## Summary
- Games Played: 15
- Overall: Friendly Rivalry
- Trust Level: 6/10
- Respect: 8/10

## Notable Moments
- 2024-01-15: Called his massive bluff with ace-high
  - His response: "Impressive deduction. I underestimated you."
  - Relationship: +2 Respect
  
- 2024-01-20: Lost huge pot when he rivered a flush
  - Your response: "Well played"
  - His response: "Elementary, my dear friend."
  - Relationship: +1 Friendship

## Current Dynamic
Sherlock sees you as a worthy adversary. He's more likely to:
- Try complex bluffs against you
- Respect your big bets
- Engage in psychological warfare

## Pattern Recognition
- Bluffs more on ace-high boards
- Tightens up after losing big pots
- More talkative when holding strong hands
```

### Memory Decay Algorithm
- Tactical memories fade faster than emotional ones
- Positive experiences weighted by personality type
- Recent events have exponentially stronger influence
- Signature moments become permanent memories

---

## Additional Creative Ideas

### Show to Taunt Mechanic
When winning without showdown (everyone folds), the winner can optionally reveal their cards:
- **Risk**: Reveals information about your bluffing patterns
- **Reward**: Psychologically damages opponents who folded
- **Implementation**:
  - Button appears after winning without showdown
  - If shown as bluff: opponents get `intimidated_by_bluff` event (+0.05 tilt)
  - If shown as strong hand: opponents may feel validated for folding
- **Personality Impact**: Some personalities (Trump, Gordon Ramsay) would love to taunt; others (Buddha, Bob Ross) would rarely use it

### Poker Tournament Seasons
- **Wild West Season**: Cowboy personalities, saloon setting
- **Space Season**: Alien personalities, zero-gravity chips
- **Detective Season**: All mystery-solver personalities
- **Food Network Season**: Celebrity chef personalities

### Achievement System
Beyond standard achievements, drama-based ones:
- **"The Heartbreaker"**: Make Eeyore cry
- **"The Peacemaker"**: End a rivalry
- **"The Instigator"**: Start 3 rivalries in one session
- **"The Therapist"**: Improve everyone's mood
- **"The Villain"**: Become rivals with everyone

### Dynamic Music System
- Music changes based on table tension
- Each personality has theme music
- Rivalry triggers battle music
- Big pots create crescendos

### Poker Dreams
Between sessions, get "dreams" from your AI opponents:
- "Eeyore dreamed about the time you were nice to him"
- "Gordon Ramsay had nightmares about your bluff"
- Affects starting mood next session

### Table Positions Matter
- Sitting next to certain personalities affects gameplay
- "Bad seat" next to aggressive player
- "Good seat" next to passive player
- Can request seat changes (costs social capital)

### Prop Bets & Side Games
- Bet on which AI will bust first
- Guess another player's hand
- Predict mood changes
- Mini-games during other players' turns

### Commentary Personalities
Different commentators with different styles:
- **Joe Rogan**: "Oh! He's hurt! That river card just changed EVERYTHING!"
- **David Attenborough**: "Watch as the alpha player marks his territory with a substantial raise"
- **Gordon Ramsay**: "That fold was RAW! WHAT ARE YOU DOING?!"

---

## The Ultimate Vision

A poker game where every session tells a story. Where you don't just remember the hands you won, but the friends you made, the rivals you defeated, and the moments that made you laugh. Where the AI opponents feel so real that you find yourself thinking about them between sessions, wondering how Eeyore is doing or if Trump is still mad about that bluff.

This is poker as a living, breathing world of personalities, relationships, and endless dramatic possibilities.