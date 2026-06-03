---
purpose: As-built architecture for the coach progression system — skills, gates, evidence tracking, and where the code actually lives
type: architecture
created: 2026-02-01
last_updated: 2026-06-03
---

# Coach Progression System - Architecture

## Overview

This document describes the as-built architecture of the Coach Progression System. It
was originally written as the Milestone 1 (Skill-Aware Coaching) plan; this revision
reframes it against the shipped code. It builds on the existing `CoachEngine` +
`CoachAssistant` architecture as an intelligence layer between the stats engine and the
LLM voice.

**What shipped**: all four gates are now defined (Gate 1 preflop, Gate 2 post-flop, Gate 3
pressure recognition, Gate 4 multi-street — 11 skills total, see
`flask_app/services/skill_definitions.py`), situation classification
(`situation_classifier.py`), skill evaluation (`skill_evaluator.py`), player model
persistence (`poker/repositories/coach_repository.py`), self-reported starting level
(the `/onboarding` route), and adaptive coaching prompts (`coach_assistant.py`).

> **Doc provenance**: the field signatures, the "modify `poker/persistence.py`" plan
> in §2.4, the `/evaluate-action` recommendation in §4, and the Milestone phasing in §6
> describe the *original plan*, not the current code. The drift is called out inline
> below; treat §6 as historical.

---

## 1. New Modules

### 1.1 `flask_app/services/skill_definitions.py` — Skill Registry

Code-driven skill and gate definitions. Single source of truth. (The original plan
placed `EvidenceRules`/`SkillState`/`PlayerSkillState` here; as built those shared,
dependency-free data structures live in **`poker/coach_models.py`**, imported by both
the services and the repository to avoid a circular import — see §1.0 below.)

**As-built signatures** (verified against `skill_definitions.py:21-47` and
`coach_models.py:47-58`):

```python
# poker/coach_models.py
@dataclass(frozen=True)
class EvidenceRules:
    min_opportunities: int             # Min opps before practicing -> reliable
    window_size: int = 20              # Rolling window (CLASS DEFAULT 20; shipped skills pass 30)
    advancement_threshold: float = 0.75
    regression_threshold: float = 0.60
    automatic_min_opps: int = 30       # Min opps for reliable -> automatic
    automatic_threshold: float = 0.85
    automatic_regression: float = 0.70
    introduced_min_opps: int = 3       # Min opps before introduced -> practicing

# flask_app/services/skill_definitions.py
@dataclass(frozen=True)
class SkillDefinition:
    skill_id: str                      # "fold_trash_hands"   (NOT `id`)
    name: str                          # "Fold Trash Hands"
    description: str                   # Lesson summary for introductions
    gate: int                          # 1
    evidence_rules: EvidenceRules
    phases: FrozenSet[str]             # frozenset({'PRE_FLOP'})  (NOT `trigger_phase`)
    tags: FrozenSet[str] = frozenset() # e.g. {'hand_selection','preflop'}  (replaces `depends_on`)

@dataclass(frozen=True)
class GateDefinition:
    gate_number: int                   # (NOT `gate`)
    name: str
    description: str
    skill_ids: Tuple[str, ...]
    required_reliable: int             # How many skills must be 'reliable' to unlock next gate
```

> **Field drift from the original plan**: `SkillDefinition.id → skill_id`,
> `trigger_phase → phases` (a frozenset, multi-phase), and there is no `depends_on` —
> dependency is implicit in the `gate` integer; `tags` carries descriptive categories
> instead. `GateDefinition.gate → gate_number`, plus `description` and `required_reliable`
> were added. `EvidenceRules.window_size` default is **20**, not 50 (every shipped
> post-flop skill overrides it to 30; the Gate 1 preflop skills use the default 20).
> No `EvidenceRules` advancement is a single `advancement_threshold` anymore — there are
> separate `automatic_*` thresholds for the reliable→automatic step.

### 1.0 `poker/coach_models.py` + `context_builder.py` (new since the plan)

Two modules exist that the original plan didn't anticipate:

- **`poker/coach_models.py`** — dependency-free shared enums/dataclasses
  (`SkillState`, `SKILL_STATE_ORDER`, `CoachingMode`, `EvidenceRules`,
  `PlayerSkillState`, `GateProgress`, `CoachingDecision`). Lives under `poker/` so the
  repository layer can import it without pulling in `flask_app.services`.
- **`flask_app/services/context_builder.py`** — `build_poker_context()` (the helper the
  plan placed inline in the classifier/engine).

**Gate 1 Skills**:

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

The enums and `PlayerSkillState`/`GateProgress`/`CoachingDecision` dataclasses live in
**`poker/coach_models.py`** (not here). As-built (`coach_models.py:17-123`):

```python
class SkillState(str, Enum):       # introduced | practicing | reliable | automatic
class CoachingMode(str, Enum):     # learn | compete | silent  (no "teaching"/"review" enum value)

@dataclass(frozen=True)            # frozen, unlike the plan's mutable version
class PlayerSkillState:
    skill_id: str
    state: SkillState = SkillState.INTRODUCED
    total_opportunities: int = 0
    total_correct: int = 0
    window_opportunities: int = 0
    window_correct: int = 0        # denormalized from window_decisions for read efficiency
    window_decisions: tuple = ()   # the actual rolling window (replaces the plan's window_size field)
    streak_correct: int = 0
    streak_incorrect: int = 0
    last_evaluated_at: Optional[str] = None
    first_seen_at: Optional[str] = None
    # window_accuracy / total_accuracy are computed @property

@dataclass(frozen=True)
class CoachingDecision:
    mode: CoachingMode
    primary_skill_id: Optional[str] = None    # (NOT `primary_skill`; no `should_coach`/`cadence_reason`)
    relevant_skill_ids: tuple = ()
    coaching_prompt: str = ''
    situation_tags: tuple = ()
```

The service itself is `flask_app/services/coach_progression.py`:

```python
class CoachProgressionService:
    def __init__(self, coach_repo)   # takes the coach repository, not a generic `persistence`

    def get_player_state(self, user_id: str) -> dict
    def get_or_initialize_player(self, user_id: str) -> dict
    def initialize_player(self, user_id: str, level: str) -> dict
    def update_player_level(self, user_id: str, level: str) -> dict
    # ...plus the evaluation/advancement methods
```

> The plan's `PlayerSkillState.window_size` field does not exist; the window is a
> `window_decisions` tuple with a denormalized `window_correct` count. `CoachingDecision`
> dropped `should_coach`/`cadence_reason` and renamed `primary_skill → primary_skill_id`.

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

### 2.4 Persistence — `poker/repositories/coach_repository.py` (NOT `poker/persistence.py`)

> **As-built correction**: `poker/persistence.py` no longer exists. Persistence was
> split into per-domain repositories under `poker/repositories/`. Coach state lives in
> **`coach_repository.py`** (`CoachRepository`), and the schema/migrations are owned by
> **`schema_manager.py`** (`SCHEMA_VERSION = 140` as of this revision). The coach tables
> were indeed added in **migration v63** — that part of the plan held — but the file the
> plan names to edit is gone. Two later migrations extended the profile table:
> **v68** added `onboarding_completed_at` and **v70** added a `range_targets` JSON column
> to `player_coach_profile` (`schema_manager.py:1874-1882`, `4444-4516`).

**Tables added in migration v63** (schema as built — note `player_coach_profile` gained
`onboarding_completed_at` in v68 and `range_targets` in v70):

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
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    onboarding_completed_at TEXT,        -- added v68
    range_targets TEXT DEFAULT NULL      -- added v70 (JSON: position -> target %)
);
```

**Permission migration** (part of v63):
- Insert `can_access_coach` into `permissions` table
- Assign to `user` and `admin` groups via `group_permissions`

**Persistence methods** live on `CoachRepository` (`coach_repository.py`), e.g.
`save_coach_profile(..., onboarding_completed_at=, range_targets=)` / `load_coach_profile`,
the per-skill skill-state CRUD, and `save_gate_progress` / `load_gate_progress`. There
are also instrumentation methods used by the metrics routes and the live tip log:
`record_tip`, `get_tip_effectiveness`, `get_profile_stats`, `get_skill_distribution`,
`get_skill_advancement_stats`.

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

> **As-built correction**: the `POST /api/coach/<game_id>/evaluate-action` endpoint
> recommended below (Option A) was **never shipped** — there is no such route in
> `coach_routes.py`. Evaluation is folded into the existing flow instead:
> `compute_coaching_data_with_progression()` (in `coach_engine.py`) attaches the
> classification + coaching decision on the read path, and skill evaluations are
> restored/recorded through `restore_session_memory()` and surfaced in `/hand-review`
> (which reads `SessionMemory.get_hand_evaluations()`). The shipped routes are listed in
> COACH_SYSTEM.md §8. The discussion below is **historical design rationale**.

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

> **Historical** — this is the original build plan. It shipped (with the file/field
> corrections noted in §1–§4): persistence landed in `poker/repositories/` not
> `persistence.py`, shared dataclasses landed in `poker/coach_models.py`, and
> `/evaluate-action` was not built. The `persistence.py` references below are the
> planned target, not the current code.

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

> **Historical plan.** As built, add `poker/coach_models.py` (shared dataclasses) and
> `flask_app/services/context_builder.py` to New Files, and read `poker/persistence.py`
> below as `poker/repositories/coach_repository.py` + `schema_manager.py`. The
> `/evaluate-action` endpoint listed under "new endpoints" was not shipped.

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
