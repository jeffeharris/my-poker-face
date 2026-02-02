# Coach Progression System - Milestone 1 Architecture

## Overview

This document describes the architecture for Milestone 1 (Skill-Aware Coaching) of the Coach Progression System. It builds on the existing `CoachEngine` + `CoachAssistant` architecture as an intelligence layer between the stats engine and the LLM voice.

**Milestone 1 delivers**: Gate 1 skills (3 preflop skills), situation classification, skill evaluation, player model persistence, self-reported starting level, and adaptive coaching prompts.

---

## 1. New Modules

### 1.1 `flask_app/services/skill_definitions.py` — Skill Registry

Code-driven skill and gate definitions. Single source of truth.

```python
@dataclass(frozen=True)
class EvidenceRules:
    min_opportunities: int      # Min chances before advancement
    advancement_threshold: float  # Success rate to advance (e.g., 0.75)
    regression_threshold: float   # Fall below this to regress (e.g., 0.60)
    window_size: int = 50        # Rolling window

@dataclass(frozen=True)
class SkillDefinition:
    id: str                      # "fold_trash_hands"
    name: str                    # "Fold Trash Hands"
    gate: int                    # 1
    description: str             # Lesson summary for introductions
    trigger_phase: str | tuple   # 'PRE_FLOP'
    evidence_rules: EvidenceRules
    depends_on: tuple[str, ...] = ()

@dataclass(frozen=True)
class GateDefinition:
    gate: int
    name: str
    skill_ids: tuple[str, ...]
```

**Gate 1 Skills** (Milestone 1 scope):

| Skill | Trigger | Target Behavior | Advancement | Regression |
|-------|---------|----------------|-------------|------------|
| `fold_trash_hands` | Preflop, bottom ~65% hands | Fold trash | >= 12 opps, >= 75% correct | < 60% in window |
| `position_matters` | Preflop, any decision | Tighter EP, wider LP | >= 20 opps, >= 70% correct | EP VPIP > LP VPIP |
| `raise_or_fold` | Preflop, unopened pot, entering | Raise don't limp | >= 10 opps, >= 80% correct | Limp rate > 10% |

**Registries**: `ALL_SKILLS: dict[str, SkillDefinition]`, `ALL_GATES: dict[int, GateDefinition]`, `get_skills_for_gate(gate) -> list`, `get_skill_by_id(id) -> SkillDefinition`.

### 1.2 `flask_app/services/situation_classifier.py` — Situation Tagger

Rule-based, deterministic classifier. Tags each decision point with relevant skill context.

```python
@dataclass(frozen=True)
class SituationClassification:
    relevant_skills: tuple[str, ...]   # All skill IDs that trigger
    primary_skill: str | None          # Least-progressed skill in current gate
    situation_tags: tuple[str, ...]    # Descriptive tags for LLM context
    confidence: float                  # 1.0 for rule-based

class SituationClassifier:
    def classify(
        self,
        coaching_data: dict,           # From compute_coaching_data()
        unlocked_gates: set[int],
        skill_states: dict[str, PlayerSkillState],
    ) -> SituationClassification
```

**Classification logic**:
1. Build trigger context from coaching_data (phase, position, hand percentile, pot state)
2. Check each unlocked gate's skill triggers against context
3. Filter to triggered skills only
4. Select primary skill = least-progressed in current gate
5. Generate situation tags (phase, hand quality, position, pot state)

**Hand percentile extraction**: Parses existing `classify_preflop_hand()` output (e.g., "72o - Unconnected cards, Bottom 10%") to extract tier (Top 3%, Top 10%, Top 20%, Top 35%, Below average, Bottom 25%, Bottom 10%).

**Priority rule**: Within current gate, least-progressed skill wins. Future gate skills are never evaluated.

### 1.3 `flask_app/services/skill_evaluator.py` — Action Evaluator

Evaluates player actions against skill targets. Wraps `DecisionAnalyzer` output.

```python
@dataclass(frozen=True)
class SkillEvaluation:
    skill_id: str
    action_taken: str              # fold, call, raise, check, all_in
    evaluation: str                # "correct" | "incorrect" | "marginal"
    confidence: float
    reasoning: str                 # Human-readable for debugging
    coaching_data: dict            # Stats snapshot

class SkillEvaluator:
    def evaluate(
        self,
        skill_id: str,
        action_taken: str,
        coaching_data: dict,
        decision_analysis: DecisionAnalysis | None = None,
    ) -> SkillEvaluation
```

**Per-skill evaluation functions** (dispatched by skill_id):

- `_eval_fold_trash`: Preflop + trash hand? fold = correct, else incorrect
- `_eval_position_matters`: Check hand quality vs position-appropriate range. EP + weak hand + fold = correct. LP + playable + raise = correct.
- `_eval_raise_or_fold`: Entering unopened pot? raise = correct, call (limp) = incorrect, fold = marginal (not tracked)

**Design**: Each skill has a dedicated evaluator method. New skills added by implementing a new method and registering it. `DecisionAnalysis` from `DecisionAnalyzer` is consumed but not modified.

### 1.4 `flask_app/services/coach_progression.py` — Progression Engine

Manages skill state machine, gate logic, coaching decisions.

```python
class SkillState(str, Enum):
    INTRODUCED = "introduced"
    PRACTICING = "practicing"
    RELIABLE = "reliable"
    AUTOMATIC = "automatic"

class CoachingMode(str, Enum):
    LEARN = "learn"
    COMPETE = "compete"
    REVIEW = "review"
    SILENT = "silent"

@dataclass
class PlayerSkillState:
    skill_id: str
    state: SkillState
    total_opportunities: int
    total_correct: int
    window_opportunities: int
    window_correct: int
    window_size: int = 50
    introduced_at: datetime | None
    last_evaluated_at: datetime | None
    last_state_change_at: datetime | None

@dataclass(frozen=True)
class CoachingDecision:
    should_coach: bool
    mode: CoachingMode
    primary_skill: str | None
    relevant_skills: tuple[str, ...]
    cadence_reason: str
    situation_tags: tuple[str, ...]

class CoachProgressionService:
    def __init__(self, persistence)

    def get_player_state(self, user_id: str) -> dict
    def initialize_player(self, user_id: str, level: str) -> dict
    def get_coaching_decision(self, user_id, coaching_data, session_memory) -> CoachingDecision
    def evaluate_and_update(self, user_id, action, coaching_data, classification) -> list[SkillEvaluation]
```

**State transitions**:
- Introduced -> Practicing: >= 3 opportunities (automatic after a few hands)
- Practicing -> Reliable: >= min_opportunities, >= advancement_threshold correct in window
- Reliable -> Automatic: >= 30 opportunities, >= 85% correct in window
- Regression: Reliable -> Practicing if window_accuracy < regression_threshold
- Regression: Automatic -> Reliable if window_accuracy < 70%

**Coaching decision logic** (maps skill state to coaching mode):

| Skill State | Mode | Cadence | Should Coach |
|------------|------|---------|-------------|
| Introduced | Learn | Every relevant action | Yes |
| Practicing (accuracy < 60%) | Learn | Once per hand | Yes |
| Practicing (accuracy >= 60%) | Compete | Once per hand | Yes |
| Reliable | Compete | Only on deviation | No (reactive) |
| Automatic | Silent | Tracking only | No |

**Session memory**: In-memory dict in `game_data`, tracks `coached_skills_this_hand` (set), cleared on hand boundary. Prevents duplicate coaching within a hand.

**Gate unlock**: Gate N+1 unlocks when all Gate N skills reach Reliable or Automatic.

**Player initialization by self-reported level**:

| Level | Gate 1 | Gate 2 | Gate 3 |
|-------|--------|--------|--------|
| Beginner | Introduced | Locked | Locked |
| Intermediate | Practicing | Introduced | Locked |
| Experienced | Reliable | Practicing | Introduced |

---

## 2. Modified Modules

### 2.1 `flask_app/services/coach_engine.py`

**Change**: Add wrapper function that calls existing `compute_coaching_data()` then runs situation classification.

```python
def compute_coaching_data_with_progression(
    game_id: str, player_name: str, user_id: str,
    game_data: dict | None = None
) -> dict | None:
    """Compute coaching data + situation classification + coaching decision."""
    coaching_data = compute_coaching_data(game_id, player_name, game_data)
    if not coaching_data:
        return None

    service = CoachProgressionService(persistence)
    player_state = service.get_player_state(user_id)

    # Skip if no profile (hasn't onboarded)
    if not player_state.get('profile'):
        return coaching_data

    unlocked_gates = {g for g, gp in player_state['gate_progress'].items() if gp.unlocked}
    classification = SituationClassifier().classify(
        coaching_data, unlocked_gates, player_state['skill_states']
    )

    session_memory = game_data.get('coach_session_memory', {}) if game_data else {}
    coaching_decision = service.get_coaching_decision(user_id, coaching_data, session_memory)

    coaching_data['classification'] = classification
    coaching_data['coaching_decision'] = coaching_decision
    coaching_data['player_skill_states'] = player_state['skill_states']
    return coaching_data
```

**Existing `compute_coaching_data()` is unchanged.** The new function wraps it.

### 2.2 `flask_app/services/coach_assistant.py`

**Change**: Add mode-specific system prompt templates. Modify constructor to accept mode + skill context. Add factory function for mode-aware creation.

**New prompt templates** (added alongside existing `COACH_SYSTEM_PROMPT`):

```
LEARN_MODE_PROMPT: Prescriptive. "Explain concepts, recommend actions with reasoning."
  - Includes: skill name, lesson summary
  - Style: Direct, educational

COMPETE_MODE_PROMPT: Descriptive. "Surface stats and reads, let player decide."
  - Includes: skill name
  - Style: Analytical, brief

REVIEW_MODE_PROMPT: Reflective. "Analyze hand, reference skills, highlight patterns."
  - Includes: skill names reviewed
  - Style: Analytical, constructive
```

**Constructor change**:
```python
def __init__(self, game_id, owner_id, player_name='',
             mode: str | None = None,
             skill_context: dict | None = None):
    # Select system prompt based on mode
    # Fall back to existing COACH_SYSTEM_PROMPT if no mode
```

**New factory**:
```python
def get_or_create_coach_with_mode(game_data, game_id, player_name, mode, skill_context):
    # Creates new CoachAssistant when mode changes
    # Caches in game_data keyed by mode
```

**Design decision**: New `CoachAssistant` instance per mode change. This keeps conversation history clean within a mode. Acceptable tradeoff since mode changes happen at state transitions.

### 2.3 `flask_app/routes/coach_routes.py`

**Changes**:
1. Add `@require_permission('can_access_coach')` to all existing routes
2. Modify `/stats` to return classification + coaching decision
3. Modify `/ask` (proactive tip) to use skill-aware coaching decision
4. Add new endpoints

**New endpoints**:

```
GET  /api/coach/<game_id>/progression
  → Returns full player progression state (skill states, gates, profile)

POST /api/coach/<game_id>/onboarding
  → Body: { "level": "beginner" | "intermediate" | "experienced" }
  → Initializes player skill states based on level
  → Returns initial progression state
```

**Modified `/ask` flow** (proactive tip path):
1. Compute coaching data with progression
2. Get coaching decision from progression service
3. If `should_coach = false`, return `{ answer: null, coaching_decision }`
4. If `should_coach = true`, build skill context, create mode-aware coach, generate tip
5. Update session memory (mark skill as coached this hand)
6. Return `{ answer, stats, coaching_decision }`

### 2.4 `poker/persistence.py`

**Change**: Add migration v63 with 3 new tables + `can_access_coach` permission.

**New tables**:

```sql
CREATE TABLE player_skill_progress (
    user_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    state TEXT NOT NULL,          -- 'introduced'|'practicing'|'reliable'|'automatic'
    total_opportunities INTEGER DEFAULT 0,
    total_correct INTEGER DEFAULT 0,
    window_opportunities INTEGER DEFAULT 0,
    window_correct INTEGER DEFAULT 0,
    window_size INTEGER DEFAULT 50,
    introduced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_evaluated_at TIMESTAMP,
    last_state_change_at TIMESTAMP,
    PRIMARY KEY (user_id, skill_id)
);

CREATE TABLE player_gate_progress (
    user_id TEXT NOT NULL,
    gate INTEGER NOT NULL,
    unlocked BOOLEAN DEFAULT 0,
    unlocked_at TIMESTAMP,
    PRIMARY KEY (user_id, gate)
);

CREATE TABLE player_coach_profile (
    user_id TEXT PRIMARY KEY,
    self_reported_level TEXT NOT NULL,
    effective_level TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Permission migration** (part of v63):
- Insert `can_access_coach` into `permissions` table
- Assign to `user` and `admin` groups via `group_permissions`

**New persistence methods**:
- `save_skill_state(user_id, skill_state)` / `load_skill_state(user_id, skill_id)` / `load_all_skill_states(user_id)`
- `save_gate_progress(user_id, gate_progress)` / `load_gate_progress(user_id)`
- `save_coach_profile(user_id, level, effective_level)` / `load_coach_profile(user_id)`

### 2.5 `poker/memory/opponent_model.py`

**Change**: Remove self-tracking guard to allow human player self-observation.

```python
# BEFORE:
def observe_action(self, observer, opponent, ...):
    if observer == opponent:
        return  # Don't model yourself

# AFTER:
def observe_action(self, observer, opponent, ...):
    # Self-tracking allowed (enables human player stats for coaching)
```

This gives us free VPIP/PFR/aggression tracking for the human player via existing infrastructure — used by `position_matters` skill evaluation.

### 2.6 Frontend (`useCoach.ts`, `CoachPanel.tsx`, `CoachBubble.tsx`)

**Minimal M1 changes** (full frontend evolution in M4):

**`types/coach.ts`**: Add optional progression types (`SkillState`, `SkillProgress`, `GateProgress`, `CoachingMode`, `SituationContext`). Extend `CoachStats` with optional `coaching_mode?`, `situation?`, `progression?` fields.

**`useCoach.ts`**: Add `progression` state, `fetchProgression()` callback (called when panel opens, not on every turn). Existing behavior unchanged — new fields are optional.

**`CoachBubble.tsx`**: Accept optional `coachingMode` prop. In Learn mode, show recommendation. In Compete mode, show equity. Fallback to current logic.

**`CoachPanel.tsx`**: Accept optional `progression` prop. Show gate number and skill focus in a header bar (low-fidelity for M1).

**New component**: `CoachOnboardingModal.tsx` — simple modal with 3 buttons (Beginner/Intermediate/Experienced), shown on first coach interaction.

---

## 3. Data Flow

### 3.1 Pre-Action (Player's Turn Begins)

```
Frontend: isPlayerTurn=true → GET /api/coach/<game_id>/stats
    ↓
coach_routes.coach_stats()
    ↓
compute_coaching_data()                    ← EXISTING (unchanged)
    → {phase, position, equity, hand_strength, pot_odds, ...}
    ↓
SituationClassifier.classify()             ← NEW
    → SituationClassification{relevant_skills, primary_skill, tags}
    ↓
CoachProgressionService.get_coaching_decision()  ← NEW
    → CoachingDecision{should_coach, mode, skill, cadence_reason}
    ↓
Return stats + classification + coaching_decision
    ↓
Frontend: POST /api/coach/<game_id>/ask {type: "proactive_tip"}
    ↓
If should_coach:
    → Build skill_context from SkillDefinition
    → Create CoachAssistant with mode-specific prompt
    → coach.get_proactive_tip(stats)
    → Update session_memory
    → Return {answer, coaching_decision}
Else:
    → Return {answer: null, coaching_decision}
```

### 3.2 Post-Action (Player Acts)

```
Player acts (fold/call/raise/check)
    ↓
Game engine processes action                ← EXISTING
    ↓
DecisionAnalyzer.analyze()                  ← EXISTING (if available)
    → DecisionAnalysis{decision_quality, optimal_action, equity, ...}
    ↓
CoachProgressionService.evaluate_and_update()  ← NEW
    For each relevant skill:
        ├─ SkillEvaluator.evaluate(skill_id, action, coaching_data)
        │   → SkillEvaluation{correct/incorrect/marginal}
        ├─ Update opportunity/correct counts
        ├─ Check advancement thresholds
        ├─ Check regression thresholds
        └─ Persist to player_skill_progress
    Check gate unlocks
    ↓
Clear session_memory.coached_skills_this_hand (on hand boundary)
```

### 3.3 Post-Hand (Review)

```
Hand ends → HAND_OVER phase
    ↓
Frontend: POST /api/coach/<game_id>/hand-review
    ↓
Load hand context from hand_recorder        ← EXISTING
Load skill states for this user             ← NEW
Build skill_context (skills evaluated this hand)
Create CoachAssistant with REVIEW_MODE_PROMPT
coach.review_hand(hand_text)
    ↓
Return {review, skill_focus}
```

---

## 4. Post-Action Hook Integration

**Where does the post-action evaluation hook go?**

The evaluation must happen after the human player acts but before the next turn advances. Two options explored:

**Option A: In coach_routes as a side-effect of `/ask`** — The frontend already calls `/ask` for proactive tips. We could add a "report action" endpoint that the frontend calls after the player acts. This keeps the progression system fully within the coach service layer.

**Option B: In the game action handler** — Hook into the game's `play_turn()` flow in `flask_app/routes/game_routes.py` or `flask_app/services/game_state_service.py`. This is more architecturally correct (evaluation happens at the source of truth).

**Recommended: Option A** — New endpoint `POST /api/coach/<game_id>/evaluate-action`:
```
Body: { "action": "fold", "hand_number": 42 }
```
Frontend calls this after submitting an action. This keeps the coach system decoupled from the game engine — no modifications to game flow. The endpoint:
1. Loads the coaching_data snapshot (stored in session memory at pre-action time)
2. Runs evaluation against classified skills
3. Updates skill progress
4. Returns updated skill states

**Alternative considered**: We could also call evaluate in the existing `/hand-review` endpoint after each hand completes. Tradeoff: delays evaluation but simplifies frontend integration. We'll use the explicit endpoint for M1 and can optimize later.

---

## 5. Key Design Decisions

### 5.1 Functional Core
All new data structures use frozen dataclasses (`SkillDefinition`, `SituationClassification`, `SkillEvaluation`, `CoachingDecision`). State updates create new instances. Aligns with existing codebase patterns.

### 5.2 DecisionAnalyzer Untouched
The `DecisionAnalyzer` is consumed by `SkillEvaluator`, never modified. It serves both AI monitoring and human coaching — wrapping prevents cross-contamination.

### 5.3 Mode-Specific CoachAssistant Instances
New `CoachAssistant` instance per mode change (not mid-conversation prompt swap). The `Assistant` class maintains conversation history — changing system prompt mid-conversation causes inconsistency. Mode changes are rare (state transitions), so this is acceptable.

### 5.4 Session Memory in game_data (In-Memory)
`coached_skills_this_hand` stored in `game_data['coach_session_memory']`. No DB persistence needed — resets between sessions by design. Cleared on hand boundary.

### 5.5 Self-Tracking via OpponentModelManager
Removing the self-tracking guard in `OpponentModelManager` gives free VPIP/PFR/aggression tracking for human players. The model was designed for this exact type of tracking — it just excluded self-observation. Used by `position_matters` skill evaluation.

### 5.6 RBAC via DB Migration
`can_access_coach` permission added in migration v63. Assigned to `user` and `admin` groups. Routes decorated with `@require_permission('can_access_coach')`. Guests get 403.

### 5.7 Window Stats Management
Rolling window maintained by proportional trimming when window_opportunities exceeds window_size. This preserves accuracy without needing to store individual decisions:
```python
if window_opportunities > window_size:
    ratio = window_correct / window_opportunities
    window_opportunities = window_size
    window_correct = int(window_size * ratio)
```

---

## 6. Implementation Sequence

### Phase 1: Core Infrastructure
**Create**: `skill_definitions.py`, `coach_progression.py`
**Modify**: `persistence.py` (migration v63, CRUD methods, SCHEMA_VERSION bump)

- Define Gate 1 skills with evidence rules
- Implement PlayerSkillState, GateProgress, SkillState enums
- Implement CoachProgressionService core: get_player_state, initialize_player, update_skill_progress, check_state_transitions, check_gate_unlocks
- Write migration v63 (3 tables + can_access_coach permission)
- Add persistence CRUD methods
- Tests: state transitions, gate unlocks, persistence round-trip

### Phase 2: Classification & Evaluation
**Create**: `situation_classifier.py`, `skill_evaluator.py`

- Implement SituationClassifier with Gate 1 preflop rules
- Implement SkillEvaluator with per-skill evaluation functions
- Parse hand percentile from classify_preflop_hand() output
- Tests: classifier with various preflop situations, evaluator correct/incorrect/marginal

### Phase 3: Coach Integration
**Modify**: `coach_engine.py`, `coach_assistant.py`, `opponent_model.py`

- Add compute_coaching_data_with_progression wrapper
- Add mode-specific prompt templates (Learn/Compete/Review)
- Modify CoachAssistant constructor for mode + skill_context
- Add get_or_create_coach_with_mode factory
- Remove self-tracking guard in OpponentModelManager
- Tests: stats -> classification -> coaching decision -> mode-specific prompt

### Phase 4: API & Routes
**Modify**: `coach_routes.py`

- Add @require_permission('can_access_coach') to all routes
- Modify /stats to return classification + coaching decision
- Modify /ask to use skill-aware coaching decision
- Add GET /progression endpoint
- Add POST /onboarding endpoint
- Add POST /evaluate-action endpoint
- Tests: E2E flow (onboarding -> stats -> ask -> evaluate -> progression)

### Phase 5: Frontend (Minimal M1)
**Modify**: `types/coach.ts`, `useCoach.ts`, `CoachBubble.tsx`, `CoachPanel.tsx`
**Create**: `CoachOnboardingModal.tsx`

- Add progression types (optional extensions)
- Add progression state + fetch to useCoach hook
- Add mode-aware bubble content
- Add progression header to panel
- Add onboarding modal
- Tests: components render with/without progression data

### Phase 6: Testing & Validation

- Integration test: full session lifecycle (onboard -> play hands -> skill advancement)
- Regression test: existing coach features unchanged
- Manual playtest: complete session as beginner, verify coaching adapts
- Performance validation: no latency regression

---

## 7. File Summary

### New Files
| File | Purpose |
|------|---------|
| `flask_app/services/skill_definitions.py` | Gate 1 skill definitions, registries |
| `flask_app/services/situation_classifier.py` | Rule-based situation tagger |
| `flask_app/services/skill_evaluator.py` | Action evaluation against skill targets |
| `flask_app/services/coach_progression.py` | State machine, gates, coaching decisions |
| `react/react/src/components/mobile/CoachOnboardingModal.tsx` | First-time level selection |
| `tests/test_skill_definitions.py` | Skill trigger + evaluation tests |
| `tests/test_situation_classifier.py` | Classification logic tests |
| `tests/test_skill_evaluator.py` | Evaluation logic tests |
| `tests/test_coach_progression.py` | State machine + gate tests |

### Modified Files
| File | Changes |
|------|---------|
| `flask_app/services/coach_engine.py` | Add `compute_coaching_data_with_progression()` wrapper |
| `flask_app/services/coach_assistant.py` | Mode-specific prompts, modified constructor, new factory |
| `flask_app/routes/coach_routes.py` | RBAC decorators, modified endpoints, new endpoints |
| `poker/persistence.py` | Migration v63 (3 tables + permission), CRUD methods |
| `poker/memory/opponent_model.py` | Remove self-tracking guard |
| `react/react/src/types/coach.ts` | Add progression types |
| `react/react/src/hooks/useCoach.ts` | Add progression state + fetch |
| `react/react/src/components/mobile/CoachBubble.tsx` | Mode-aware stat display |
| `react/react/src/components/mobile/CoachPanel.tsx` | Progression header |
| `react/react/src/components/mobile/MobilePokerTable.tsx` | Pass new props |

### Unchanged Files
| File | Why |
|------|-----|
| `flask_app/services/coach_engine.py` (`compute_coaching_data`) | Existing function untouched, new wrapper added |
| `poker/decision_analyzer.py` | Consumed by SkillEvaluator, never modified |
| `react/react/src/components/mobile/StatsBar.tsx` | Displays backend stats, unaware of progression |
| `react/react/src/components/mobile/CoachButton.tsx` | No change needed |

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Hand percentile parsing fragile | Extract from `classify_preflop_hand()` output. Add fallback for unrecognized format. Consider adding structured return. |
| Window stats accuracy with proportional trimming | Acceptable for M1 thresholds. M2 can switch to actual rolling window (store last N decisions). |
| Self-tracking changes OpponentModelManager behavior | Only removes an early-return guard. Self-models stored in separate key. All existing AI-observes-AI paths unaffected. |
| LLM prompt quality varies by mode | Templates are well-defined. Manual playtest each mode. Fallback to default prompt if mode unrecognized. |
| Post-action evaluation timing | Frontend calls /evaluate-action after submitting action. If call fails, evaluation skipped (non-blocking). |
| Performance (classification on every /stats) | Classification is rule-based O(3) for Gate 1. Cache in game_data with hand_number TTL. |
