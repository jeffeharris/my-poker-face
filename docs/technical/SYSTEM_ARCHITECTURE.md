---
purpose: High-level system structure — core components, data flow, and module layout
type: architecture
created: 2026-02-03
last_updated: 2026-06-03
---

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
        │           │ (schema v148) │            │
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

The psychology system is an **anchors + dynamic-axes** model. Static *anchors*
define who a player fundamentally is and never change during a session; dynamic
*axes* drift under game pressure and decay back toward the anchors. The earlier
"elastic 5-trait" model (`elasticity_manager.py`, `trait_converter.py`,
`ElasticPersonality`/`ElasticityManager`) has been **removed** — those files and
classes no longer exist.

> For the full model (axes, zones, decay, the family×quadrant emotion matrix),
> see [`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md) and
> [`PERSONALITY_ANCHORS.md`](PERSONALITY_ANCHORS.md). This section is only the
> architectural seam.

### Components

| File | Responsibility |
|------|----------------|
| `personalities.json` | 62 celebrity/character personalities (top key `personalities`) |
| `psychology_model.py` | Core frozen dataclasses + the emotion model (see below) |
| `player_psychology.py` | `PlayerPsychology` orchestrator: holds anchors, applies pressure, derives expression (`poker/player_psychology.py:212`) |
| `emotional_state.py` | Emotional axes state types |
| `moment_analyzer.py` | Drama detection (routine → climactic) |
| `pressure_detector.py` | Bad beats, coolers, streaks (detection-only) |

### Core types in `psychology_model.py`

| Type | Kind | Role |
|------|------|------|
| `PersonalityAnchors` | frozen dataclass (`:137`) | Static identity: `ego`, `poise`, `expressiveness`, `risk_identity`, `adaptation_bias`, `baseline_*` — gravity that pulls dynamic state back to baseline |
| `EmotionalAxes` | frozen dataclass (`:216`) | Dynamic state that drifts under pressure |
| `ComposureState` | frozen dataclass (`:266`) | Decision-quality / tilt state |
| `PokerFaceZone` | frozen dataclass (`:386`) | How much true emotion leaks vs. is masked |
| `EmotionalQuadrant` | Enum (`:97`) | Confidence × composure → `COMMANDING` / `OVERHEATED` / `GUARDED` / `SHAKEN` (the *internal* feeling) |
| `EmotionFamily` | Enum (`:114`) | Temperament family `COMPETITOR` / `FUN_LOVER` / `STOIC` / `ANXIOUS` — selects *how* a quadrant is expressed |
| `get_emotion_family(anchors)` | function (`:548`) | Maps anchors (ego/expressiveness) → an `EmotionFamily` |

The quadrant decides the internal feeling; the family decides the surface
emotion. Two players in the same `OVERHEATED` quadrant read differently — a
high-ego `COMPETITOR` looks angry while a low-ego `FUN_LOVER` looks
giddy/gleeful.

### Psychology Flow

```
Game Event → Pressure Detector → PlayerPsychology.apply_pressure_event()
                                        ↓
                          EmotionalAxes drift; anchors pull back toward baseline
                                        ↓
              get_emotion_family(anchors) × EmotionalQuadrant → surface emotion
                                        ↓
                              AI Decision / table-talk influenced
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

Blueprints are registered **without a `url_prefix`** (e.g.
`app.register_blueprint(game_bp)` at `flask_app/__init__.py:276`), so the full
path is whatever the `@route` decorator declares. `game_bp = Blueprint('game', __name__)`
(`flask_app/routes/game_routes.py:71`) — its routes are *not* under `/api/pokergame/*`.

| File | Representative endpoints |
|------|--------------------------|
| `routes/game_routes.py` | `POST /api/new-game` (`:1485`), `POST /api/game/<id>/action` (`:1833`), `GET /api/game-state/<id>` (`:619`), `GET /api/games` (`:539`) |
| `routes/cash_routes.py` | Cash-mode lifecycle, whereabouts, forgiveness asks |
| `routes/coach_routes.py` | Coaching features |
| `routes/experiment_routes.py` | A/B testing |
| `routes/personality_routes.py` | Personality CRUD |
| `routes/admin_dashboard_routes.py` | Admin / configuration |

> The legacy `/api/pokergame/*` prefix is gone — paths are flat under `/api/...`.

### WebSocket Handlers

| File | Responsibility |
|------|----------------|
| `handlers/game_handler.py` | Real-time game progression, AI decisions |
| `handlers/message_handler.py` | Chat and action messages |
| `handlers/avatar_handler.py` | Avatar URL resolution |

### Game Handler Flow (~4600 lines)

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

Repository pattern over a single SQLite database. `BaseRepository`
(`poker/repositories/base_repository.py:92`) provides connection handling; ~35
concrete repositories (game, cash, ledger, tournament, coach, experiment, LLM,
personality, presence, relationship, …) sit on top. Schema and forward
migrations are managed by `SchemaManager` (`schema_manager.py`,
`SCHEMA_VERSION = 148` at `:321`) — one `_migrate_vN_*` method per version,
~130 `CREATE TABLE` statements total.

> For the repository catalog, the migration mechanism, and the
> conventions around `BaseRepository`, see
> [`REPOSITORIES.md`](REPOSITORIES.md) — not re-described here.

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
    - Personality traits (anchors + current emotional axes)
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
| Personality | `personalities.json`, `psychology_model.py`, `player_psychology.py` |
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

- **Complexity**: Many interconnected systems (psychology, memory, coaching, cash economy)
- **SQLite scaling**: Fine for current use, but single-writer limitation for high concurrency
- **Large handlers**: `game_handler.py` (~4600 lines) handles many concerns
- **Cross-cutting concerns**: Memory updates, coaching, and psychology are interleaved

---

## Related Documentation

- [Game Vision](/docs/vision/GAME_VISION.md)
- [Feature Ideas](/docs/vision/FEATURE_IDEAS.md)
- [Psychology Overview](/docs/technical/PSYCHOLOGY_OVERVIEW.md)
- [Personality Anchors](/docs/technical/PERSONALITY_ANCHORS.md)
- [Repositories](/docs/technical/REPOSITORIES.md)
- [Rate Limiting](/docs/technical/RATE_LIMITING.md)
- [Scaling Blueprint](/docs/SCALING.md)
- [DevOps & Deployment](/docs/DEVOPS.md)
