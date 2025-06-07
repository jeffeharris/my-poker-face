# My Poker Face - Game Vision

## Vision Statement

My Poker Face reimagines poker as a living, breathing social experience where AI personalities don't just play cards—they evolve, form relationships, and create emergent narratives. This isn't about building a poker simulator that calculates optimal plays; it's about capturing the soul of poker: the psychology, the drama, the bluffs, the friendships, and the rivalries that make each hand a story worth telling.

We're creating a game where winning isn't just about the money—it's about the moments. Where successfully reading an opponent's mood, making a friend, or pulling off an epic bluff against a rival brings as much satisfaction as taking down a big pot. Where each AI opponent is a fully-realized character with dynamic emotions, evolving relationships, and their own narrative arc.

## Core Design Philosophy

### 1. **Drama Over Mathematics**
While respecting poker fundamentals, we prioritize creating memorable moments over optimal play. The best hand doesn't always win—sometimes the best story does.

### 2. **Emergent Personalities**
AI opponents aren't static. They have moods that shift, traits that evolve, and memories that accumulate. Today's timid Eeyore might become tomorrow's aggressive player after a string of wins.

### 3. **Living World**
The poker table exists within a larger ecosystem. Personalities remember past encounters, form opinions about each other, and carry grudges or friendships across games.

### 4. **Player Agency Beyond Cards**
Success comes not just from playing cards well, but from reading personalities, building relationships, and manipulating the social dynamics at the table.

---

## Current Technical Architecture

### Frontend: React with TypeScript
- **Modern UI**: Responsive poker table visualization
- **Real-time Updates**: Socket.IO for live game state
- **Component-Based**: Modular, maintainable code
- **Mobile-Ready**: Works on all devices

### Backend: Flask API
- **Pure API**: JSON endpoints, no templates
- **WebSocket Support**: Real-time multiplayer
- **Persistence**: SQLite with automatic saves
- **AI Integration**: OpenAI API for personalities

### Deployment: Docker Compose
- **Multi-Service**: Frontend, backend, Redis
- **Development-Friendly**: Hot reloading, volume mounts
- **Production-Ready**: Nginx, health checks
- **Easy Setup**: Single command deployment

### Core Engine: Immutable State Machine
- **Functional Programming**: No side effects
- **Predictable**: Explicit state transitions
- **Testable**: Pure functions throughout
- **Extensible**: Clear separation of concerns

---

## Feature Roadmap

### Phase 1: Dynamic Personality System ✅ COMPLETED

#### Trait Elasticity Framework ✅
Every personality trait has elasticity—how much it can change based on events:

```json
"elasticity_config": {
    "trait_elasticity": {
        "bluff_tendency": 0.3,
        "aggression": 0.2,
        "chattiness": 0.4,
        "emoji_usage": 0.5
    },
    "mood_elasticity": 0.4,
    "recovery_rate": 0.1
}
```

**Implemented Features:**
- ✅ ElasticityManager handles all trait changes
- ✅ PressureEventDetector identifies game events
- ✅ Per-personality elasticity configuration
- ✅ Automatic trait recovery toward baseline
- ✅ Full persistence support

#### Mood System ✅
- ✅ Dynamic mood based on current trait values
- ✅ Mood vocabulary specific to each personality
- ✅ Affects AI decision-making and chat responses
- Visual indicators (facial expressions, color changes)

### Phase 2: Social Dynamics

#### Chat as Gameplay
- Build rapport meters with each player
- Different phrases work better with different personalities
- Chat during other players' turns affects mood
- "Social engineering" as a valid strategy

#### Relationship System
- Long-term memories stored as markdown files
- Personalities remember past games
- Friendships and rivalries develop naturally
- Relationships affect gameplay (friends might soft-play, rivals might target each other)

### Phase 3: Player Evolution

#### XP from Moments, Not Just Money
- **Successful bluff**: +XP (rewards creativity)
- **Correct read**: +XP (called someone's bluff)
- **Building rapport**: +XP (improved someone's mood)
- **Surviving elimination**: +XP (resilience)
- **Making a friend**: +XP (social success)
- **Creating drama**: +XP (triggered a rivalry)

#### Progressive Unlocks
1. **Level 1**: Basic poker
2. **Level 3**: See physical tells
3. **Level 5**: Mood visibility unlocked
4. **Level 7**: Chat influence increased
5. **Level 10**: Occasional inner monologue access
6. **Level 15**: Advanced tell reading
7. **Level 20**: Full emotional state visibility

### Phase 4: Tournament Mode

#### Narrative Engine
- Simulate other tables using probability + personality matchups
- Generate "news flashes" from other tables
- Create emergent storylines
- Use actual dealt cards to weight realistic outcomes
- Build tension with position updates

#### Two-Table Start
- Begin with manageable 2-table tournaments
- Perfect the simulation system
- Expand to larger tournaments later

### Phase 5: Advanced Features

#### Personality Mixer
- Combine two personalities: "Sherlock + Gordon Ramsay"
- Create unique hybrid behaviors
- Quick win feature for player creativity

#### Multi-Model AI Support
- Different AI providers for different personalities
- Claude vs GPT vs Llama thinking styles
- Unique "tells" based on model characteristics

#### Difficulty Innovations
- **Harder**: AI gets probability calculations, hand analysis
- **Easier**: Information presented with typos, distractions
- **Dynamic**: Difficulty adjusts based on player skill

#### Visual Personality System
- Pre-generated emotional state images
- Tagged with attributes (winning, losing, bluffing, frustrated)
- Display based on current state + recent actions
- Batch generate for efficiency

#### Poker Coach Mode
- Contextual advice based on skill level
- Beginner: Simple explanations
- Intermediate: Odds and strategy
- Expert: Advanced concepts only

---

## Game Modes

### Quick Cash Game (30 minutes)
- 20-30 hands
- Fixed buy-ins
- Focus on personality interactions

### Tournament Mode (1 hour)
- 50-100 hands
- Rising blinds
- Narrative events from other tables
- Elimination drama

### Story Mode (Future)
- Chapters of 10-15 hands
- Persistent world state
- Long-term character arcs
- Multiple "worlds" with different goals

---

## Technical Considerations

### Non-Determinism as Feature
- Embrace AI unpredictability
- Design systems that guide rather than control
- Create boundaries, let AI fill the space
- Learn to read personalities, not memorize patterns

### Performance Optimizations
- Pre-generate personality images
- Cache common AI responses
- Efficient state management for tournaments
- Background simulation for other tables

---

## Success Metrics

### Engagement Over Optimization
- Time spent in chat/social features
- Variety of strategies employed
- Story moments created per session
- Personality relationship changes

### Player Progression
- Skills unlocked
- Personalities befriended
- Rivalries created
- Dramatic moments experienced

---

## Future Horizons

### Sandbox Features
- Tournament director mode
- Custom personality creation
- Narrative scripting tools
- Community-shared personalities

### Competitive Seasons
- Themed personality sets
- Seasonal narratives
- Leaderboards for drama created
- Awards for best bluff, best read, best rivalry

### AI Commentary System
- Dynamic play-by-play
- Personality-aware commentary
- Highlight reel generation
- Post-game analysis

---

## Design Principles

1. **Every feature should create stories**
2. **Respect poker while transcending it**
3. **Reward emotional intelligence**
4. **Make losing fun through narrative**
5. **Celebrate the unexpected**

This is poker as we've always imagined it could be—where the cards are just the beginning of the story.