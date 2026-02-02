# Coach Progression System – Requirements

## Document Purpose

Requirements for evolving the existing AI poker coach into an adaptive progression system. This builds on the current `CoachEngine` + `CoachAssistant` architecture — not a replacement, but a new intelligence layer between the stats engine and the LLM voice.

**Status**: Draft for review
**Scope**: Behavior, data contracts, progression logic, integration with existing coach
**Out of scope**: UI polish, solver integration, detailed algorithms

---

## 1. Product Context

### 1.1 Game Format

- Texas Hold'em, 6-max table
- Single buy-in, no rebuys, winner-take-all
- Strategy target: **cash-game fundamentals** (no ICM)
- **Single human player per game** — one coachee, remaining seats are AI players

### 1.2 Target Audience

- Beginner to intermediate players
- Casual / learning-oriented
- Not professional or solver-driven players

### 1.3 Coach Positioning

The coach is a **caddy**, not a tutor. A caddy gives you the yardage and the lie — you choose the club.

**Core principle**: The coach teaches a **thinking process**, not memorization or charts. It progressively makes itself unnecessary.

**Pedagogical honesty**: The coach teaches a simplified, heuristic-based strategy — not GTO or solver-optimal play. Actions are evaluated against these heuristics (e.g., "don't limp" is always "correct" even though profitable open-limps exist). This is a deliberate choice: the target audience benefits more from consistent fundamentals than from understanding exceptions. Advanced players who have internalized these fundamentals will progress through to `Automatic` quickly and encounter less coaching interference.

### 1.4 Access Control

The coach is a premium feature gated behind RBAC:

- **Guests**: No access
- **Registered users**: Full access (`can_access_coach` permission)
- **Admins**: Full access

Implementation: `@require_permission('can_access_coach')` decorator on coach routes (currently ungated), following the existing pattern in `poker/authorization.py`. New permission added via DB migration and assigned to `user` and `admin` groups.

**Pre-RBAC**: Until Milestone 5 ships, the coach is available to all users (including guests). This is intentional — progression tracking still requires a logged-in user, but the coach interaction itself is ungated during development.

---

## 2. What Exists Today

The progression system builds on a working foundation:

### 2.1 Coach Engine (`flask_app/services/coach_engine.py`)

`compute_coaching_data()` returns a complete stats snapshot:
- Equity (vs ranges + vs random), pot odds, required equity, EV
- Hand strength (preflop classification via `classify_preflop_hand()`, postflop via `HandEvaluator`)
- Outs count and cards
- Opponent stats (VPIP, PFR, aggression, style via `OpponentModelManager`)
- Optimal action recommendation (via `DecisionAnalyzer.determine_optimal_action()`)

**This stays as-is.** The progression system consumes it.

### 2.2 Coach Assistant (`flask_app/services/coach_assistant.py`)

`CoachAssistant` wraps the `Assistant` LLM class with poker coaching prompts:
- `ask()` — answer questions with stats context
- `get_proactive_tip()` — 1-2 sentence tip
- `review_hand()` — post-hand analysis

Single system prompt, single mode of interaction. **This gets extended** — the system prompt becomes dynamic based on skill state and coaching mode.

### 2.3 Coach Routes (`flask_app/routes/coach_routes.py`)

- `GET /api/coach/<game_id>/stats` — fetch coaching data
- `POST /api/coach/<game_id>/ask` — ask question or get proactive tip
- `POST /api/coach/<game_id>/hand-review` — review last hand
- `GET|POST /api/coach/<game_id>/config` — load/save mode (proactive/reactive/off)

**These get extended** with progression endpoints and enhanced payloads.

### 2.4 Decision Analyzer (`poker/decision_analyzer.py`)

Already evaluates decisions with:
- `decision_quality`: "correct", "mistake", "marginal", "unknown"
- `optimal_action`, `ev_call`, `required_equity`, `ev_lost`
- `hand_rank`, `relative_strength`
- Equity vs ranges (2000 Monte Carlo iterations)

**This is the evaluation foundation.** The skill evaluator wraps it with skill-specific logic.

### 2.5 Frontend (`react/react/src/`)

- `useCoach.ts` — hook managing stats, messages, modes, hand reviews
- `CoachPanel.tsx` — interactive panel with message history and stats
- `CoachBubble.tsx` — proactive tip overlay
- `StatsBar.tsx` — equity/odds visualization

**These evolve** to support new coaching modes and progression-aware display.

---

## 3. Goals and Non-Goals

### 3.1 Goals

1. Teach poker incrementally through play
2. Improve decision quality without overwhelming the player
3. Provide clear skill-based progression via gates
4. Adapt coaching language and depth to player skill level
5. Detect and respond to skill regression
6. Build iteratively — each milestone delivers standalone value

### 3.2 Non-Goals (v1)

- Solver trees or mixed strategies
- Tournament / ICM strategy
- Quizzes, lessons, or required reading
- Professional-level poker education
- Situation classifier integration with personality elasticity system (flagged as future extension — see §12)

---

## 4. Delivery Milestones

Each milestone is independently valuable and shippable.

### Milestone 1: Skill-Aware Coaching (Core Loop)

**Capability**: The coach knows what skills exist, classifies situations, evaluates player actions, and adapts its coaching based on skill state. Player progression is tracked and persisted across sessions.

**What ships**:
- Skill definitions (Gate 1: 3 preflop skills)
- Situation classifier (rule-based, preflop only)
- Skill evaluator (wraps DecisionAnalyzer)
- Player model persistence (new tables)
- Self-reported starting level (beginner/intermediate/experienced)
- `CoachAssistant` system prompt adapts to skill state (prescriptive vs descriptive)
- Existing coach modes (proactive/reactive/off) continue working, but proactive tips become skill-aware

**Fidelity**: Gate 1 skills only. Basic skill state tracking. LLM prompt changes are the primary output difference the player sees.

### Milestone 2: Progression and Gating

**Capability**: Skills advance through states (Introduced → Practicing → Reliable → Automatic). Gates unlock when previous gate skills are mastered. Skills can regress. Coaching cadence adjusts per-skill.

**What ships**:
- Skill state machine (advancement + regression logic)
- Gate system with dependencies
- Gate 2 skills (3 post-flop skills)
- Per-skill coaching cadence (Learn → Compete → Review emerges from state)
- Session memory for repetition avoidance
- Silent downgrade if self-reported level doesn't match observed play
- API endpoint for progression data

**Fidelity**: Gates 1-2 with full progression loop. Coach behavior visibly changes as player improves.

### Milestone 3: Deeper Skills and Review Enhancement

**Capability**: Gate 3-4 skills, enhanced hand review with skill context, player explanation support.

**What ships**:
- Gate 3 skills (pressure recognition, multi-street)
- Gate 4 skills (multi-street thinking, bet sizing)
- Hand review enhanced with skill-specific focus
- Player can explain reasoning in review, coach responds with stats context
- Marginal band tuning based on playtesting data from M1-M2

**Fidelity**: Full skill tree (Gates 1-4). Coach reviews reference specific skills and track improvement.

### Milestone 4: Frontend Evolution

**Capability**: Coach UI adapts to progression. New coaching modes visible in UI. Progression indicators.

**What ships**:
- Frontend coaching mode reflects skill-driven cadence
- Progression indicators (current gate, skill status)
- Enhanced CoachPanel with skill context
- Coach bubble adapts content to mode (stat line in Compete, nudge in Learn)
- Skill introduction cards when new skills activate

**Fidelity**: Full frontend integration. Coach feels like it's growing with the player.

### Milestone 5: RBAC and Polish

**Capability**: Coach gated behind permissions. Metrics instrumented. Production hardening.

**What ships**:
- `can_access_coach` permission, migration, route decorators
- Metrics instrumentation (retention, skill advancement rates, coach interaction)
- Threshold tuning from production data
- Edge case handling from playtesting feedback

---

## 5. Coaching Modes

Three modes forming a spectrum of involvement. Mode is **per-skill** — the overall coaching behavior in a hand is an emergent property of where the player's active skills sit.

### 5.1 Learn Mode

- **When**: Skill state is `Introduced` or early `Practicing`
- **Cadence**: Per action at relevant decision points
- **Style**: Prescriptive — recommends specific actions with reasoning
- **Coach prompt framing**: Explain concepts, be direct about what to do and why
- **Example**: "This hand (7-2 offsuit) is too weak to play from early position. Fold hands like this and wait for better spots."

### 5.2 Compete Mode

- **When**: Skill state is late `Practicing` or `Reliable`
- **Cadence**: Sets the scene at the start of the action, then steps back
- **Style**: Descriptive — surfaces stats and reads, doesn't prescribe
- **Coach prompt framing**: Give the player information, let them decide
- **Example**: "You're on the button. Villain in the big blind has a VPIP of 45% and folds to raises 60% of the time."

### 5.3 Review Mode

- **When**: Skill state is `Reliable` approaching `Automatic`
- **Cadence**: Post-hand only
- **Style**: Reflective — explains patterns, highlights what went well or poorly
- **Coach prompt framing**: Analyze the hand, reference specific skills
- **Example**: "You called the river bet with middle pair against a tight player who'd bet every street. Their line usually means strength — folding saves 400 chips."

### 5.4 How This Evolves the Current System

Current modes (`proactive`/`reactive`/`off`) map to the new system:
- `proactive` → skill-driven Learn/Compete mode (cadence determined by progression)
- `reactive` → player can still ask questions anytime regardless of mode
- `off` → coach disabled, but still tracking progression silently

The config endpoint continues working. The `proactive` mode just gets smarter.

### 5.5 LLM Prompt Strategy

The `CoachAssistant` currently has a single static `COACH_SYSTEM_PROMPT`. This evolves:

**Template approach** for well-defined modes (Learn/Compete/Review system prompts are predefined templates, selected based on the coaching decision for this interaction).

**Dynamic composition** for the per-interaction context (skill focus, stats, opponent reads are injected into the selected template).

```
Template (mode-specific) + Dynamic context (skill + stats) = Final prompt
```

This avoids generating a novel system prompt per interaction while keeping the coaching voice consistent within a mode.

---

## 6. Skill System

### 6.1 Skill Definition

Each skill is defined in code (extensible by adding new definitions):

```python
@dataclass(frozen=True)
class SkillDefinition:
    id: str                          # e.g. "fold_trash_hands"
    name: str                        # e.g. "Fold Trash Hands"
    gate: int                        # Gate tier (1, 2, 3, ...)
    trigger: SituationTrigger        # When this skill is relevant
    target_behavior: str             # What the player should do
    evidence_rules: EvidenceRules    # How to detect correct/incorrect
    lesson_summary: str              # 2-3 sentence explanation for introductions
    depends_on: tuple[str, ...] = () # Skill IDs that must be Reliable first
```

### 6.2 Skill States

```
Introduced → Practicing → Reliable → Automatic
```

| State | Meaning | Coach Mode | Cadence |
|-------|---------|------------|---------|
| `Introduced` | Player has seen the concept | Learn | Every relevant action |
| `Practicing` | Player is working on it | Learn→Compete | Once per hand max |
| `Reliable` | Consistent correct behavior | Compete→Review | Only on deviation |
| `Automatic` | Skill is internalized | Silent | Tracking only |

**Progression**: Based on behavioral evidence over a rolling window. A skill advances when the player meets the evidence threshold (e.g., ≥75% correct over ≥12 opportunities).

**Regression**: If a `Reliable` or `Automatic` skill's windowed stats drop below a regression threshold, the skill moves back one state. Regression thresholds are more lenient than advancement — harder to lose a skill than to gain one. When a skill regresses, coaching intensity increases again automatically.

### 6.3 Skill State Persistence

```python
@dataclass
class PlayerSkillState:
    skill_id: str
    state: str                    # "introduced" | "practicing" | "reliable" | "automatic"
    total_opportunities: int      # Lifetime situation count
    total_correct: int            # Lifetime correct actions
    window_opportunities: int     # Recent window situation count
    window_correct: int           # Recent window correct actions
    window_size: int              # Configurable window size
    window_type: str              # "hands" | "opportunities"
    introduced_at: datetime
    last_evaluated_at: datetime
    last_state_change_at: datetime
```

Stored in new `player_skill_progress` table, keyed by `user_id` + `skill_id`.

---

## 7. Gate System

### 7.1 Gate Structure

Skills are organized into gated tiers. A gate opens when **the required number of skills in the previous gate reach at least `Reliable`** (configured per gate via `required_reliable`).

```
Gate 1: Fundamentals (Preflop Basics)
├── Fold Trash Hands
├── Position Matters
└── Raise or Fold (Don't Limp)

Gate 2: Post-Flop Basics (requires Gate 1)
├── Flop Connection (Fold When You Miss)
├── Bet When Strong (Value Betting)
└── Checking Is Allowed

Gate 3: Pressure Recognition (requires Gate 2)
├── Draws Need Price
├── Respect Big Bets
└── Have a Plan for the Hand

Gate 4: Multi-Street Thinking (requires Gate 3)
├── Don't Pay Off Double Barrels
├── Size Your Bets With Purpose
└── [Additional skills TBD]
```

### 7.2 End-State: All Skills Mastered

When a player reaches `Automatic` on all skills in the highest defined gate (currently Gate 4), the coach enters a **passive monitoring mode**: progression tracking continues (regression can still trigger), but proactive coaching stops entirely. The coach remains available for reactive questions and hand reviews. This is the intended graduation state — the coach has made itself unnecessary.

### 7.3 Design Constraints

- Skills within a gate **do not conflict**. If multiple skills trigger on the same action, they agree on what "correct" looks like.
- The gate system scopes evaluation complexity: Gate 1 = preflop heuristics only, Gate 2 adds single-street postflop, etc.
- All skills within a gate are tracked simultaneously. Coach focuses on the least-progressed skill.
- Skills in future (locked) gates are not evaluated or coached. No premature teaching.

### 7.4 Gate Persistence

Stored in `player_gate_progress` table, keyed by `user_id` + `gate`.

---

## 8. Initial Skill Set

### Gate 1: Fundamentals

**Skill 1 — Fold Trash Hands**
- **Trigger**: Preflop, player's action, unopened or limped pot
- **Target**: Fold bottom ~30% of hands (per existing `classify_preflop_hand()`)
- **Evidence**: Fold rate with weak hands
- **Advancement**: ≥12 opportunities, ≥75% correct
- **Regression**: Windowed correct rate drops below 60%

**Skill 2 — Position Matters**
- **Trigger**: Preflop, player's action, any situation
- **Target**: Tighter range from early position, wider from late position
- **Evidence**: VPIP by position (tracked via existing opponent model stats on the human player)
- **Advancement**: Position-adjusted VPIP within reasonable ranges over ≥20 hands
- **Regression**: Early position VPIP exceeds late position VPIP consistently

**Skill 3 — Raise or Fold (Don't Limp)**
- **Trigger**: Preflop, unopened pot, player decides to enter
- **Target**: Raise instead of limp when entering a pot
- **Evidence**: Limp rate
- **Advancement**: ≤1 limp per 15 pot entries over ≥15 entries (est. ~50 hands)
- **Regression**: Limp rate exceeds 2 per 15 entries in window

### Gate 2: Post-Flop Basics

**Skill 4 — Flop Connection**
- **Trigger**: Flop, player's action, player has weak/no-pair hand
- **Target**: Fold when the flop misses (no pair, no draw)
- **Evidence**: Fold rate with air on flop
- **Advancement**: ≥8 opportunities, ≥70% fold rate (est. ~100 hands for 8 opps)
- **Regression**: Windowed fold rate drops below 55%

**Skill 5 — Bet When Strong**
- **Trigger**: Any post-flop street, player has top pair or better
- **Target**: Bet or raise for value
- **Evidence**: Bet frequency with strong hands
- **Advancement**: ≥70% bet frequency over ≥8 opportunities (est. ~100 hands)
- **Regression**: Bet frequency drops below 55% in window

**Skill 6 — Checking Is Allowed**
- **Trigger**: Any post-flop street, player has weak or marginal hand, can check
- **Target**: Check or fold rather than betting into strength with nothing
- **Evidence**: Check rate with weak hands when checking is available
- **Advancement**: ≥65% appropriate check/fold rate over ≥8 opportunities (est. ~100 hands)
- **Regression**: Drops below 50%

### Gate 3: Pressure Recognition

**Skill 7 — Draws Need Price**
- **Trigger**: Facing a bet with a draw (flush draw, straight draw)
- **Target**: Call only when pot odds justify it (using existing `required_equity`)
- **Evidence**: Correct call/fold decision based on pot odds vs draw equity
- **Advancement**: ≥70% correct over ≥6 opportunities (est. ~150 hands for 6 opps)
- **Regression**: Below 55% in window

**Skill 8 — Respect Big Bets**
- **Trigger**: Facing a bet ≥50% pot on turn or river with a medium-strength hand
- **Target**: Fold medium hands against significant aggression
- **Evidence**: Fold rate facing large bets with non-premium holdings
- **Advancement**: ≥65% correct over ≥6 opportunities (est. ~150 hands)
- **Regression**: Below 50% in window

**Skill 9 — Have a Plan for the Hand**
- **Trigger**: Player bets or raises on the flop
- **Target**: Consistent follow-through (don't bet flop then check-fold turn without reason)
- **Evidence**: Bet-then-check-fold frequency across streets
- **Advancement**: ≤25% bet-then-check-fold rate over ≥6 multi-street hands (est. ~150 hands)
- **Regression**: Rate exceeds 40% in window

### Gate 4: Multi-Street Thinking

**Skill 10 — Don't Pay Off Double Barrels**
- **Trigger**: Facing bets on both flop and turn with marginal hand
- **Target**: Recognize multi-street aggression as likely strength
- **Evidence**: Call-call frequency with marginal hands against multi-street bets
- **Advancement**: ≥60% correct fold rate over ≥5 opportunities (est. ~200 hands for 5 opps)
- **Regression**: Below 45% in window

**Skill 11 — Size Your Bets With Purpose**
- **Trigger**: Any voluntary bet or raise
- **Target**: Bet sizing proportional to pot
- **Evidence**: Bet size relative to pot, correlated with hand strength
- **Advancement**: ≥65% appropriately sized bets over ≥12 opportunities (est. ~50 hands)
- **Regression**: Below 50% in window

---

## 9. Situation Classifier

### 9.1 Purpose

Tags each player decision point with relevant skill context. Determines which skills apply to the current situation.

### 9.2 Inputs

All available from existing `compute_coaching_data()`:
- Street, position, hand strength, action context
- Opponent stats (VPIP, PFR, aggression)
- Pot odds, equity, outs, bet sizing
- Stack context

### 9.3 Output

```python
@dataclass(frozen=True)
class SituationClassification:
    relevant_skills: tuple[str, ...]  # All skill IDs that apply
    primary_skill: str | None         # Most relevant for current gate
    situation_tags: tuple[str, ...]   # Descriptive tags for LLM context
    confidence: float                 # How clearly this maps to a skill [0-1]
```

### 9.4 Rules

Rule-based, deterministic, testable. Each skill's trigger conditions are evaluated against game state. Priority logic:
- Within current gate: least-progressed skill wins
- Across gates: current gate skills take priority over completed-gate skills
- Future gate skills are never evaluated

### 9.5 Minimum Sample Size for Opponent-Dependent Skills

Some skills (e.g., "Respect Big Bets", "Position Matters") use opponent stats as classification inputs. These stats are unreliable early in a session when sample sizes are small. The classifier should require a **minimum of 10 observed hands** on an opponent before using their stats for skill evaluation. Below this threshold:

- The coach can still provide coaching but should acknowledge limited reads (e.g., "We don't have enough hands on this player yet to know their tendencies")
- Opponent-stat-dependent evaluations are labeled `marginal` (no progression effect) until the sample size is met
- Skills that don't depend on opponent stats (e.g., "Fold Trash Hands") are unaffected

### 9.6 Future Extension: Elasticity Integration

The situation classifier produces context that could enhance the `PressureEventDetector` → `ElasticityManager` pipeline for AI opponent behavior. Design the classifier as a standalone module that both systems can consume. **Not in v1 scope** — flagged for future work.

---

## 10. Evaluation Logic

### 10.1 Existing Foundation

`DecisionAnalyzer` already provides `decision_quality`, `optimal_action`, `ev_call`, `required_equity`. The coach progression system **wraps** this — it doesn't replace it.

### 10.2 Skill-Aware Evaluation

For each classified situation, check whether the player's action aligns with the skill's target behavior:

```python
@dataclass(frozen=True)
class SkillEvaluation:
    skill_id: str
    action_taken: str              # fold, call, raise, check, all_in
    evaluation: str                # "correct" | "incorrect" | "marginal"
    confidence: float              # How confident the evaluation is [0-1]
    coaching_data: dict            # Stats snapshot at decision time
```

### 10.3 Decision Labels

| Label | Meaning | Progression Effect |
|-------|---------|-------------------|
| `correct` | Action aligns with skill target | Counts toward advancement |
| `incorrect` | Action clearly violates skill target | Counts against; may trigger coaching |
| `marginal` | In the grey zone (~10% equity band) | Neutral — no progression effect |

The ~10% marginal band absorbs the natural variance from 2000-iteration Monte Carlo equity calculations. Situations falling within the marginal band are **not counted as opportunities** — they don't affect advancement or regression in either direction. This threshold is tunable; playtesting may reveal it should be wider for post-flop skills where evaluation confidence is lower.

### 10.4 Forced Action Exclusion

All-in situations, forced blind posts, and other situations where the player has no meaningful choice are **excluded from evaluation**. Only voluntary decisions count as opportunities. The situation classifier must detect and filter these before passing to the skill evaluator.

### 10.5 Hand Review Multi-Skill Prioritization

When a hand touches multiple skills across streets (e.g., preflop raise → flop value bet → turn fold), the hand review covers **all evaluated skills for the hand**, ordered by: (1) incorrect evaluations first, (2) then by skill progression state (least-progressed first). The coach keeps each skill's review brief (1-2 sentences per skill) to avoid overwhelming the player.

### 10.6 Player Explanation

During hand review, the player can explain reasoning. The LLM considers the explanation alongside stats. This happens in the existing `/api/coach/<game_id>/hand-review` flow with enhanced context (skill focus, progression state).

### 10.6 Skill Correlation

Some skills have correlated evidence. For example, folding trash hands (Skill 1) mechanically improves position stats (Skill 2) because the player enters fewer pots from early position. This is **intentional and by design** — the gate structure is sequenced so that foundational discipline (fold trash) naturally reinforces positional awareness. Each skill still has independent opportunity thresholds, so a player can't advance a skill without enough direct observations, but correlated improvement across skills within a gate is expected and desirable.

---

## 11. Player Model

### 11.1 New Tables

**`player_skill_progress`**
- `user_id`, `skill_id`, `state`
- `total_opportunities`, `total_correct`
- `window_opportunities`, `window_correct`, `window_size`
- `introduced_at`, `last_evaluated_at`, `last_state_change_at`

**`player_gate_progress`**
- `user_id`, `gate`, `unlocked`, `unlocked_at`

**`player_coach_profile`**
- `user_id`, `self_reported_level`, `effective_level`
- `created_at`, `updated_at`

`effective_level` starts equal to `self_reported_level` and is adjusted downward silently if observed play contradicts the self-report (see §11.4). It is **never adjusted upward** through this mechanism — upward progression happens through the normal skill advancement / gate unlock flow. The field serves as a readable summary of the player's current coaching intensity tier.

### 11.2 Existing Data Consumed

- **`player_decision_analysis`** table: Decision quality, equity, EV — primary evaluation input
- **Opponent model (in-memory)**: VPIP, PFR, aggression — used for situation classification
- **`hand_history`** table: Recorded hands for review context

### 11.3 Windowed Stats

Both lifetime and windowed stats tracked per skill. Window size is **per-skill** and configurable. Used for advancement, regression detection, and coaching intensity.

**Window sizing rationale**: The window must be large enough to contain the required number of opportunities for each skill. In a 6-max game, opportunity frequency varies dramatically by skill type:

| Skill Type | Est. Opportunities per 50 Hands | Recommended Window |
|-----------|--------------------------------|-------------------|
| Preflop (Skills 1-3) | 10-35 | 50 hands |
| Post-flop single street (Skills 4-6) | 4-7 | 100 hands |
| Situational post-flop (Skills 7-9) | 2-5 | 150 hands |
| Multi-street (Skills 10-11) | 2-5 | 200 hands |

These estimates assume typical play patterns. The key constraint: the window must be large enough that the skill's advancement threshold (minimum opportunities) is reachable within a single window. Exact values will be tuned via playtesting.

**Advancement uses opportunity-count thresholds, not hand-count thresholds.** A skill advances when the required number of opportunities have been observed with the required correctness rate, regardless of how many hands that spans.

**Two windowing strategies** based on opportunity frequency:

- **Frequent skills** (Gate 1, Skill 11): Use a hand-count window (e.g., last 50 hands). Opportunities are common enough that the window always contains sufficient samples.
- **Rare skills** (Gate 2-4 situational skills): Use an **opportunity-count window** directly (e.g., last 8 opportunities). This avoids the problem of a 50-hand window expiring before enough opportunities accumulate. The trade-off is that a rare skill's window may span many sessions, but this accurately reflects the player's recent behavior for that specific situation.

Each skill definition specifies which windowing strategy it uses. The `window_size` field in `PlayerSkillState` stores either a hand count or opportunity count depending on the skill's strategy, with a `window_type` discriminator (`"hands"` or `"opportunities"`).

### 11.4 Self-Reported Starting Level

At first coach interaction, player selects:

| Level | Initial Gate | Initial Skill States |
|-------|-------------|---------------------|
| **Beginner** | Gate 1 | All Gate 1 skills at `Introduced` |
| **Intermediate** | Gate 1+2 | Gate 1 at `Practicing`, Gate 2 at `Introduced` |
| **Experienced** | Gate 1-3 | Gate 1 at `Reliable`, Gate 2 at `Practicing`, Gate 3 at `Introduced` |

**Silent downgrade**: If observed play contradicts self-reported level, skills regress based on behavioral evidence. System never tells the player — just increases coaching intensity.

### 11.5 Existing Player Data

Existing players who have played before the progression system ships will start fresh — no backfill from historical `player_decision_analysis` data. Players who select "Experienced" at onboarding will have Gate 1 skills pre-set to `Reliable`, which effectively skips the early coaching they don't need. The self-reported level is the bootstrapping mechanism; historical data backfill is a possible future optimization but not worth the complexity for v1.

### 11.6 Skill Definition Versioning

When skill definitions change between deployments (e.g., threshold adjustments, new skills added):

- **Threshold changes**: Apply immediately to all players. Existing windowed stats are re-evaluated against new thresholds on next opportunity.
- **New skills added**: Appear in their gate at `Introduced` state for players who have that gate unlocked.
- **Gate completion is preserved**: If a player has passed a gate, they remain passed regardless of new skills added to that gate. New skills in a passed gate start at `Practicing` to reflect assumed competence.

---

## 12. Architecture Integration

### 12.1 New Modules

| Module | Responsibility |
|--------|---------------|
| `flask_app/services/coach_progression.py` | Skill state management, gate logic, progression/regression |
| `flask_app/services/situation_classifier.py` | Tags decision points with skill context |
| `flask_app/services/skill_evaluator.py` | Evaluates player actions against skill targets |
| `flask_app/services/skill_definitions.py` | Skill and gate definitions (code-driven, extensible) |

### 12.2 Modified Modules

| Module | Changes |
|--------|---------|
| `flask_app/services/coach_engine.py` | Add situation classification call after computing stats |
| `flask_app/services/coach_assistant.py` | Mode-specific system prompt templates, dynamic context injection |
| `flask_app/routes/coach_routes.py` | RBAC gating, progression endpoints, enhanced payloads |
| `poker/persistence.py` | New tables via migration |
| `react/react/src/hooks/useCoach.ts` | Consume progression data, mode-aware behavior |
| `react/react/src/components/mobile/CoachPanel.tsx` | Progression display, mode-aware content |

### 12.3 Data Flow

```
Player's turn begins
    ↓
compute_coaching_data()              ← EXISTING: stats engine (unchanged)
    ↓
SituationClassifier.classify()       ← NEW: which skills apply?
    ↓
CoachProgression.get_coaching_decision()  ← NEW: should we coach? what mode?
    ├─ Load player skill states
    ├─ Check cadence rules (state-driven)
    ├─ Check session memory (avoid repeats)
    └─ Return: action (nudge/stat/silent) + mode + skill context
    ↓
CoachAssistant.generate()            ← ENHANCED: mode-specific template + context
    ↓
Response sent to frontend
    ↓
Player acts
    ↓
SkillEvaluator.evaluate()            ← NEW: was action correct for active skills?
    ├─ Consume DecisionAnalyzer output (existing)
    ├─ Apply skill-specific evidence rules
    └─ Return: SkillEvaluation
    ↓
CoachProgression.update()            ← NEW: update skill states
    ├─ Update opportunity/correct counts
    ├─ Check advancement thresholds
    ├─ Check regression thresholds
    ├─ Check gate unlock conditions
    └─ Persist to player_skill_progress
```

**Timing rule**: Gate unlock checks happen at **hand end**, not mid-hand. If a player's action advances a skill to `Reliable` (completing a gate), the newly unlocked gate's skills become active starting with the **next hand**. This avoids mid-hand coaching mode shifts and keeps the coaching experience consistent within a hand.

---

## 13. Coaching Output

### 13.1 Output Types

| Type | Length | When | Mode |
|------|--------|------|------|
| **Nudge** | 1-2 sentences | Before player acts | Learn |
| **Stat Line** | Key stat or read | Before player acts | Compete |
| **Review Note** | 3-5 sentences | After hand | Compete, Review |
| **Skill Introduction** | 2-3 sentences + lesson summary | First time skill triggers | Learn |

### 13.2 Cadence Rules

Driven by skill state:
- **Introduced**: Coach speaks at every relevant decision point
- **Practicing**: At most once per hand for this skill
- **Reliable**: Only on deviation
- **Automatic**: Silent

Max output per hand: 2-3 nudges to avoid overwhelming the player.

### 13.3 Repetition Avoidance

Session memory (in-memory, per game session) tracks:
- Which skills have been coached this hand
- Which nudges have been given this session
- Repeat count per concept

After 3+ explanations of the same concept in a session, coach shortens to stat-only.

**Lifetime**: Session memory is tied to the game instance (created at game start, discarded at game end). It does not survive server restarts mid-game — if the server restarts, session memory resets but persisted skill state is unaffected. This is acceptable; the worst case is a repeated nudge.

---

## 14. Event Inputs

No new event system needed. The progression system hooks into existing state transitions:

| Event | Source | Coach Action |
|-------|--------|-------------|
| Player's turn begins | `awaiting_action=True` | Classify situation, determine coaching |
| Player acts | `play_turn()` | Evaluate action against active skills |
| Street advances | Phase transition | Update context |
| Hand ends | `HAND_OVER` phase | Run review, update progression |
| Session starts | Game creation | Load player model |

---

## 15. Testing

Tests should cover:
- Unit tests for skill evaluation rules (given situation + action, assert correct/incorrect/marginal)
- Unit tests for progression state machine (advancement, regression, gate unlocking)
- Unit tests for situation classifier (given game state, assert correct skill classification)
- Integration tests for the full coaching loop (action → evaluation → progression update)
- Details left to implementation

---

## 16. Metrics & Success Criteria

### Product Metrics
- Retention: coached vs uncoached players
- Hands per session: do coached players play more?
- Conversion: guests → registered users

### Learning Metrics
- Skill advancement rate per gate
- Regression frequency
- Per-skill difficulty (opportunities to master)

### Coach Quality Metrics
- Nudge dismiss rate (future UI)
- Coach mute rate
- Evaluation accuracy (spot-check marginals)

---

## 17. Risks & Mitigations

### Risk 1: Post-Flop Evaluation Accuracy
Gate system scopes the problem. Gate 1 is pure preflop. By Gate 3, the marginal band + player explanation + simpler heuristics handle most cases.

### Risk 2: Sample Size vs Progression Speed
Tune thresholds via playtesting. Self-reported level lets experienced players skip early grind.

### Risk 3: Situation Detection Overlap
Gate design prevents skill conflicts. Classifier prioritizes least-progressed skill in current gate.

### Risk 4: Coach Feels Robotic
Session memory + LLM language variation + natural cadence reduction. Coach shortens over time.

### Risk 5: Regression vs Variance
Regression thresholds are more lenient than advancement. Large enough windows absorb variance.

---

## 18. Future Extensions

### 18.1 Situation Classifier → Elasticity Integration
Shared classifier feeds both coach and `PressureEventDetector` → `ElasticityManager` pipeline for more nuanced AI opponent behavior.

### 18.2 Visible Skill Tree UI
Player-facing progression view: current gate, skill states, progress toward next gate.

### 18.3 Advanced Coach Modes
- **Caddy mode**: Pure stat surfacing for experienced players
- **Deep review**: Solver-backed explanations (optional, advanced)

### 18.4 Cross-Session Opponent Modeling
Persist opponent models across games for coach to reference historical performance.

---

## 19. Open Questions

1. **Window size tuning**: Per-skill windows defined (§11.3) but exact values need playtesting validation. The opportunity-frequency estimates are rough — real gameplay data may differ.
2. **Gate 4+ skills**: Multi-street skills need more design work and evaluation validation.
3. **Coach personality**: Should the coach have a customizable tone, or stay neutral?
4. **Evaluation edge cases**: Hero calls, semi-bluffs, stack-depth-dependent plays. Marginal band handles most, but may need refinement.
5. **LLM unavailability**: If the LLM is slow or down, the coaching flow breaks since proactive tips require an LLM call. Fallback behavior (silent? canned message?) is undefined. The existing system has this same gap.

---

**End of Document**
