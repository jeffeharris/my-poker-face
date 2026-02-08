# System Architecture

This document provides a comprehensive overview of the My Poker Face application architecture.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        REACT FRONTEND                               │
│  (Zustand Store, Socket.IO, Components, Mobile-responsive UI)       │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ WebSocket + REST
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     FLASK API LAYER                                 │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────┐ │
│  │ Game Routes  │ │ Coach Routes │ │ Experiment   │ │ Admin      │ │
│  │              │ │              │ │ Routes       │ │ Routes     │ │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └─────┬──────┘ │
│         └────────────────┴────────────────┴───────────────┘        │
│                              │                                      │
│                    ┌─────────▼─────────┐                           │
│                    │  WebSocket        │                           │
│                    │  Game Handler     │                           │
│                    └─────────┬─────────┘                           │
└──────────────────────────────┼──────────────────────────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
┌───────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  GAME LOGIC   │    │   AI SYSTEM     │    │ COACHING SYSTEM │
│               │    │                 │    │                 │
│ • State       │◄──►│ • Controllers   │    │ • Skill Eval    │
│   Machine     │    │ • Prompt Mgr    │    │ • Progression   │
│ • Poker Game  │    │ • Personalities │    │ • Context Build │
│ • Betting     │    │ • Memory Mgr    │    │ • Coach Engine  │
│ • Hand Eval   │    │ • Psychology    │    │                 │
└───────┬───────┘    └────────┬────────┘    └────────┬────────┘
        │                     │                      │
        │                     ▼                      │
        │            ┌─────────────────┐             │
        │            │   LLM CLIENT    │             │
        │            │                 │             │
        │            │ • Multi-provider│             │
        │            │ • Usage Tracking│             │
        │            │ • Tool Calling  │             │
        │            └────────┬────────┘             │
        │                     │                      │
        └──────────┬──────────┴──────────┬───────────┘
                   ▼                     ▼
        ┌─────────────────────────────────────────┐
        │           PERSISTENCE LAYER             │
        │                                         │
        │  ┌─────────┐ ┌─────────┐ ┌───────────┐ │
        │  │ Game    │ │ Coach   │ │ Experiment│ │
        │  │ Repo    │ │ Repo    │ │ Repo      │ │
        │  └────┬────┘ └────┬────┘ └─────┬─────┘ │
        │       └───────────┴────────────┘       │
        │                   │                    │
        │           ┌───────▼───────┐            │
        │           │    SQLite     │            │
        │           │  (30+ tables) │            │
        │           └───────────────┘            │
        └─────────────────────────────────────────┘
```

---

## System Overview

| System | Location | Purpose |
|--------|----------|---------|
| Game Logic | `/poker/` | Core poker mechanics, state management |
| AI Integration | `/core/llm/` | Multi-provider LLM client, usage tracking |
| AI Personality | `/poker/` | Dynamic personalities, psychology, emotions |
| Memory & Learning | `/poker/memory/` | Opponent modeling, session memory |
| Coaching | `/flask_app/services/` | Skill evaluation, progression tracking |
| Web API | `/flask_app/` | REST + WebSocket API layer |
| Persistence | `/poker/repositories/` | Repository pattern, SQLite storage |
| Experiments | `/experiments/` | A/B testing, AI tournaments |
| Frontend | `/react/react/src/` | React UI, real-time updates |

---

## 1. Game Logic System

**Location:** `/poker/`

The game logic follows a functional architecture with immutable state management.

### Core Components

| File | Responsibility |
|------|----------------|
| `poker_game.py` | Immutable game state (`PokerGameState`, `Player` dataclasses) |
| `poker_state_machine.py` | Game flow control through 12 phases |
| `betting_context.py` | Betting round state, action validation |
| `controllers.py` | Player action abstraction (human + AI) |
| `hand_evaluator.py` | Poker hand ranking engine |

### Game Phases

```
INITIALIZING_GAME → WAITING_FOR_PLAYERS → HAND_STARTING → PRE_FLOP
       → FLOP → TURN → RIVER → SHOWDOWN → HAND_COMPLETE → GAME_OVER
```

### Key Design Principles

- **Immutability**: All state objects are frozen dataclasses
- **Pure Functions**: State transitions create new instances, never mutate
- **Deterministic**: Optional random seeding for reproducible games

```python
# Example: Functional state update
updated_players = tuple(
    player.update(**kwargs) if i == player_idx else player
    for i, player in enumerate(self.players)
)
```

---

## 2. AI Integration System

**Location:** `/core/llm/`

Unified abstraction over multiple LLM providers with built-in usage tracking.

### Components

| File | Responsibility |
|------|----------------|
| `client.py` | `LLMClient` (stateless) and `Assistant` (stateful) |
| `config.py` | Model tiers, provider settings |
| `tracking.py` | `UsageTracker` for cost analysis |
| `providers/` | Provider implementations (OpenAI, Anthropic, etc.) |

### Supported Providers

- OpenAI
- Anthropic
- Groq
- DeepSeek
- Mistral
- Google
- xAI
- Pollinations
- Runware

### Model Tiers

| Tier | Usage |
|------|-------|
| Default | Personality generation, commentary |
| Fast | Chat suggestions, categorization |
| Image | Avatar generation |
| Assistant | Experiment design, analysis |

### Usage Example

```python
from core.llm import LLMClient, CallType

client = LLMClient(provider="openai", model="gpt-4")
response = client.complete(
    messages=[{"role": "user", "content": "What's your move?"}],
    json_format=True,
    call_type=CallType.PLAYER_DECISION,
    game_id="game_123"
)
```

---

## 3. AI Personality & Psychology System

**Location:** `/poker/`

Dynamic personality system where traits vary based on game state.

### Components

| File | Responsibility |
|------|----------------|
| `personalities.json` | 200+ celebrity/character personalities |
| `elasticity_manager.py` | Runtime 5-trait variance based on game events |
| `emotional_state.py` | Two-layer emotional model (baseline + spike) |
| `player_psychology.py` | Orchestrator: elastic traits, emotional state, composure |
| `trait_converter.py` | Auto-converts old 4-trait to new 5-trait format |
| `moment_analyzer.py` | Drama detection (routine → climactic) |
| `pressure_detector.py` | Bad beats, coolers, streaks (detection-only) |

### Personality Traits (5-Trait Poker-Native Model)

```json
{
  "play_style": "aggressive",
  "confidence": 0.8,
  "personality_traits": {
    "tightness": 0.5,      // Range selectivity (0=loose, 1=tight)
    "aggression": 0.8,     // Bet frequency (0=passive, 1=aggressive)
    "confidence": 0.7,     // Sizing/commitment (0=scared, 1=fearless)
    "composure": 0.8,      // Decision quality (0=tilted, 1=focused)
    "table_talk": 0.6      // Chat frequency (0=silent, 1=chatty)
  },
  "elasticity_config": {
    "trait_elasticity": {"tightness": 0.3, "aggression": 0.5, "composure": 0.4},
    "mood_elasticity": 0.4,
    "recovery_rate": 0.1
  }
}
```

> Note: Old 4-trait personalities (bluff_tendency, chattiness, emoji_usage) are auto-converted.

### Psychology Flow

```
Game Event → Pressure Detector → PlayerPsychology.apply_pressure_event()
                                        ↓
                              ElasticPersonality.apply_pressure_event()
                                        ↓
                              All 5 traits modified (incl. composure)
                                        ↓
                              AI Decision Influenced
```

---

## 4. Memory & Learning System

**Location:** `/poker/memory/`

Enables AI players to learn and adapt during gameplay.

### Components

| File | Responsibility |
|------|----------------|
| `memory_manager.py` | Central orchestration of all memory |
| `hand_history.py` | Complete hand records |
| `opponent_model.py` | Per-opponent statistics (VPIP, PFR, aggression) |
| `session_memory.py` | In-game strategic learnings |
| `commentary_generator.py` | Post-hand analysis generation |

### Opponent Modeling

```python
class OpponentModel:
    vpip: float          # Voluntarily Put $ In Pot
    pfr: float           # Pre-Flop Raise %
    aggression: float    # Aggression factor
    observed_ranges: []  # Hands shown down
    narratives: []       # Qualitative observations
```

### Range Tracking

**Location:** `/poker/hand_ranges.py`

Position-based opening ranges with fallback hierarchy:
1. In-game observed stats
2. Position-based standard ranges
3. Default wide range

---

## 5. Coaching System

**Location:** `/flask_app/services/`

Skill-based coaching with progression tracking.

### Components

| File | Responsibility |
|------|----------------|
| `coach_engine.py` | Main coaching logic, skill gap analysis |
| `coach_assistant.py` | Mode-aware coaching (proactive/reactive/review) |
| `skill_definitions.py` | Skill taxonomy and gates |
| `skill_evaluator.py` | Automated decision assessment |
| `coach_progression.py` | Skill state and progression tracking |

### Skill Progression

```
Skill State: learning → practicing → mastered
     ↓
Gate Progress: skills unlocked at each gate level
     ↓
Spaced Repetition: reinforcement of learned skills
```

### Coaching Modes

| Mode | Behavior |
|------|----------|
| Proactive | Offers tips before decisions |
| Reactive | Responds to player questions |
| Review | Post-hand analysis |

---

## 6. Web API Layer

**Location:** `/flask_app/`

Flask backend with REST and WebSocket support.

### Route Modules

| File | Endpoints |
|------|-----------|
| `routes/game_routes.py` | `/api/pokergame/*` - Game lifecycle |
| `routes/coach_routes.py` | `/api/coach/*` - Coaching features |
| `routes/experiment_routes.py` | `/api/experiments/*` - A/B testing |
| `routes/personality_routes.py` | `/api/personalities/*` - CRUD |
| `routes/admin_dashboard_routes.py` | `/api/admin/*` - Configuration |

### WebSocket Handlers

| File | Responsibility |
|------|----------------|
| `handlers/game_handler.py` | Real-time game progression, AI decisions |
| `handlers/message_handler.py` | Chat and action messages |
| `handlers/avatar_handler.py` | Avatar URL resolution |

### Game Handler Flow (~2000 lines)

```
Action Received → Validation → State Update → AI Turn (if applicable)
       ↓                                              ↓
  Persistence                                   LLM Call
       ↓                                              ↓
  WebSocket Emit ←────────────────────────── Response Processing
       ↓
  Memory Update → Coaching Eval → Psychology Update
```

---

## 7. Persistence Layer

**Location:** `/poker/repositories/`

Repository pattern with SQLite backend.

### Repositories

| Repository | Purpose |
|------------|---------|
| `game_repository.py` | Game state, hands, players |
| `coach_repository.py` | Skills, progression, evaluations |
| `experiment_repository.py` | Experiments, variants, results |
| `llm_repository.py` | API usage, token tracking |
| `personality_repository.py` | Personality CRUD |
| `hand_history_repository.py` | Hand records |

### Schema

**Location:** `poker/repositories/schema_manager.py` (~3000 lines)

30+ tables including:
- `games`, `players`, `hands`
- `coach_profiles`, `skill_states`, `hand_evaluations`
- `experiments`, `experiment_variants`, `experiment_games`
- `api_usage`, `prompt_captures`

### Database Paths

| Environment | Path |
|-------------|------|
| Docker (Flask) | `/app/data/poker_games.db` |
| Local dev | `./poker_games.db` |

---

## 8. Experiment System

**Location:** `/experiments/`

Infrastructure for A/B testing AI configurations at scale.

### Components

| File | Responsibility |
|------|----------------|
| `run_ai_tournament.py` | Main experiment engine |
| `variant_config.py` | Variant configuration (models, prompts, personalities) |
| `pause_coordinator.py` | Cross-thread pause/resume |
| `resume_stalled.py` | Stall detection and recovery |

### Experiment Flow

```
Experiment Config → Variants Created → Games Executed (parallel)
        ↓                                      ↓
   Database Log                         Heartbeat Tracking
        ↓                                      ↓
   Results Aggregation ←──────────── Stall Detection/Recovery
```

### Variant Configuration

```python
@dataclass
class VariantConfig:
    personalities: List[str]
    provider: str
    model: str
    prompt_config: PromptConfig
```

---

## 9. React Frontend

**Location:** `/react/react/src/`

Modern React application with real-time updates.

### Tech Stack

- React 18 + TypeScript
- Vite (build tool)
- Zustand (state management)
- Socket.IO (real-time)
- Tailwind CSS (styling)
- Framer Motion (animations)

### State Management

```
Zustand Store (gameStore.ts)
        ↓
usePokerGame Hook (socket lifecycle, actions)
        ↓
Components (granular selectors)
```

### Component Structure

```
src/
├── components/
│   ├── game/           # Core game UI
│   │   ├── GamePage.tsx
│   │   ├── PokerTable/
│   │   ├── ActionButtons/
│   │   └── ActivityFeed/
│   ├── mobile/         # Mobile-specific
│   ├── admin/          # Admin dashboard
│   ├── chat/           # Chat system
│   └── shared/         # Reusable UI
├── hooks/              # Custom hooks
├── types/              # TypeScript definitions
└── utils/              # API, formatters, etc.
```

---

## Data Flow: Complete AI Turn

```
1.  Frontend: Player submits action
2.  WebSocket: Action sent to server
3.  Game Handler: Validates action
4.  State Machine: Advances game phase
5.  AI Controller: Triggered for AI player
6.  Prompt Manager: Builds context
    - Game state
    - Personality traits (with elasticity)
    - Memory (opponent models, session)
    - Coaching context
7.  LLM Client: Calls provider
8.  Response Validator: Validates JSON structure
9.  AI Resilience: Fallback if invalid
10. Game State: Updated (immutable)
11. Repositories: Persist changes
12. Memory Systems: Update opponent models
13. Coaching: Evaluate decision quality
14. Psychology: Adjust emotions/tilt
15. WebSocket: Emit to frontend
16. Frontend: Re-render with new state
```

---

## Architectural Patterns

| Pattern | Implementation |
|---------|----------------|
| **Immutability** | Frozen dataclasses, tuple comprehensions |
| **Functional Core** | Pure functions for game logic |
| **Repository** | All DB access via typed repositories |
| **Provider** | Multi-provider LLM with registry |
| **Strategy** | Fallback strategies, position-based ranges |
| **Observer** | WebSocket events, memory feeding |
| **Adapter** | `game_adapter.py` bridges Flask ↔ poker module |

---

## Key Files by Responsibility

| Responsibility | Key Files |
|----------------|-----------|
| Game Logic | `poker_game.py`, `poker_state_machine.py`, `controllers.py` |
| AI Decisions | `controllers.py` (AIPlayerController), `core/llm/client.py` |
| Personality | `personalities.json`, `elasticity_manager.py` |
| Prompts | `prompt_manager.py`, `prompts/*.yaml` |
| Memory | `memory_manager.py`, `opponent_model.py`, `session_memory.py` |
| Coaching | `coach_engine.py`, `skill_evaluator.py`, `coach_progression.py` |
| Database | `schema_manager.py`, `game_repository.py` |
| API | `game_routes.py`, `handlers/game_handler.py` |
| Frontend | `gameStore.ts`, `usePokerGame.ts`, `components/game/` |
| Experiments | `run_ai_tournament.py`, `variant_config.py` |

---

## Strengths

- **Clean separation**: Game logic is pure and testable, isolated from I/O
- **Multi-provider LLM**: Easy to swap/compare models
- **Comprehensive tracking**: Every AI call, decision, and cost is logged
- **Experiment-ready**: Built-in A/B testing infrastructure
- **Resilient AI**: Fallback strategies when LLMs fail
- **Dynamic personalities**: Traits adapt to game pressure

## Areas for Consideration

- **Complexity**: Many interconnected systems (psychology, memory, coaching, elasticity)
- **SQLite scaling**: Fine for current use, but single-writer limitation for high concurrency
- **Large handlers**: `game_handler.py` (~2000 lines) handles many concerns
- **Cross-cutting concerns**: Memory updates, coaching, and psychology are interleaved

---

## Related Documentation

- [Game Vision](/docs/vision/GAME_VISION.md)
- [Feature Ideas](/docs/vision/FEATURE_IDEAS.md)
- [Psychology Overview](/docs/technical/PSYCHOLOGY_OVERVIEW.md)
- [Scaling Guide](/docs/technical/SCALING.md)
- [DevOps & Deployment](/docs/DEVOPS.md)
