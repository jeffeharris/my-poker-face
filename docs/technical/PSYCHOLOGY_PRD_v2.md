# Poker AI Psychology System — Product Requirements Document (v2.1)

## 1. Overview

### Purpose

This document defines the **Poker AI Psychology System**. It produces AI poker players that feel **human, readable, and psychologically consistent** without requiring solver-level complexity.

The system separates **identity (anchors)** from **state (axes)** from **expression (output filtering)**.

### Core Design Principles

* Emotion is **visible**
* Strategic intent must be **inferred**
* Personality is **stable within a session**
* Behavior is **non-deterministic but constrained**
* Opponents are **readable and exploitable over time**

### Design Philosophy

This system prioritizes **simplification and clarity of purpose**. Each component has a single, well-defined responsibility:

* **Anchors** define WHO the player is (static identity)
* **Axes** define HOW they currently feel (dynamic state)
* **Expression** defines WHAT the opponent sees (filtered output)
* **Poker Face Zone** defines WHEN emotion is hidden (geometric membership test)

---

## 2. Goals and Non-Goals

### Goals

* Create AI opponents with distinct, persistent personalities
* Allow short-term emotional swings without identity drift
* Support player learning through readable emotional cues
* Enable believable adaptation to opponent behavior
* Integrate cleanly with LLM-driven decision making

### Non-Goals

* Perfect GTO play
* Full opponent range reconstruction
* Deterministic or solver-driven decisions
* Emotion as a direct decision driver

---

## 3. Conceptual Model

The system has three layers:

1. **Identity (Personality Anchors)** — who the player fundamentally is (static)
2. **State (Dynamic Axes)** — how they currently feel (dynamic)
3. **Expression** — how state is communicated outward (filtered)

This separation is a hard design constraint.

---

## 4. Dynamic State Axes

Three continuous axes that change during play and decay back toward anchor-defined baselines.

### 4.1 Confidence (0–1)

Belief in the correctness of one's reads and decisions.

**Impacts:**
* Bluff commitment
* Bluff-catching willingness
* Thin value betting
* Pressure application
* Effective looseness (via derived value)

**Moved by:** "Being wrong" events, filtered through **Ego** anchor

### 4.2 Composure (0–1)

Ability to regulate emotion under stress.

**Impacts:**
* Tilt resistance
* Decision consistency
* Decision noise/variance
* Effective aggression (via derived value)

**Moved by:** "Bad outcome" events, filtered through **Poise** anchor

### 4.3 Energy (0–1)

Engagement and intensity level. A volume knob for expression.

**Controls:**
* Table talk frequency
* Decision tempo
* Theatrics and presence

**Does NOT control:**
* Hand evaluation
* Strategic direction
* Bluff frequency
* Bet sizing intent

**Moved by:** Action/engagement events, filtered through **Energy Anchor**

---

## 5. Emotional Space

### 5.1 The 3D Emotional Space

Every hand updates a point in 3D space defined by:
* **Confidence** (0–1)
* **Composure** (0–1)
* **Energy** (0–1)

This point represents the player's current internal emotional state.

### 5.2 The 2D Quadrant Model

For determining emotional **labels**, we project onto Confidence × Composure:

```
                        COMPOSURE
                 Low       Mid       High
              ┌─────────┬─────────┬─────────┐
         High │OVERHEATED│         │COMMANDING│
              │  manic,  │         │ dominant,│
              │ volatile │         │ in control│
              ├─────────┤         ├─────────┤
CONFIDENCE Mid│         │ POKER   │         │
              │         │  FACE   │         │
              ├─────────┤  ZONE   ├─────────┤
         Low  │ SHAKEN  │         │ GUARDED │
              │desperate,│         │cautious, │
              │ spiraling│         │ defensive│
              └─────────┴─────────┴─────────┘
```

**Energy** affects the **intensity** of expression within each quadrant, not which quadrant.

### 5.3 Poker Face Zone

Poker Face is **not a default emotion**. It is a **3D volume** in emotional space that players move in and out of.

#### Zone Center (Universal)

The Poker Face zone has a **fixed center** for all players:
* **Composure**: High (~0.7–0.8)
* **Confidence**: Mid-high (~0.6–0.7)
* **Energy**: Low-mid (~0.3–0.5)

This center does NOT vary by personality. It represents the regulated, unreadable state.

#### Zone Size (Per Personality)

What varies is how **large** the zone is for each player:

| Anchor | Effect on Zone Size |
|--------|---------------------|
| **Poise** | High poise → larger zone (more stress tolerance) |
| **Ego** | Low ego → larger zone (less reactive to being wrong) |

High-poise, low-ego players have large poker face zones — they're hard to read.
Low-poise, high-ego players have small zones — they crack easily.

#### Zone Shape (Per Personality)

What also varies is the **shape** of the zone — which axis breaks poker face first:

| Anchor | Effect on Zone Shape |
|--------|----------------------|
| **Expressiveness** | High → zone is narrow on Energy axis (energy breaks the mask) |
| **Risk Identity** | Risk-seeking → narrow on Confidence axis; Risk-averse → narrow on Composure axis |

This creates personality-specific breaking points:
* A calm loud talker (high energy tolerance, inside zone)
* A silent but tilted player (low composure, outside zone)

#### What Does NOT Affect Zone Geometry

The poker face zone is **purely** determined by identity anchors. It does NOT depend on:
* Aggression
* Looseness
* Adaptation Bias
* Current opponent
* Current hand
* Recovery Rate

Poker face is emotional regulation, not strategy.

### 5.4 Anchor Baselines vs Zone Location

**Critical concept:** A player's anchor baselines (where their state naturally rests) may be **inside or outside** the poker face zone.

| Personality Type | Anchor Location | Natural State |
|------------------|-----------------|---------------|
| Naturally regulated | Anchors inside zone | Rests in poker face; events push out, recovery brings back in |
| Naturally expressive | Anchors outside zone | Rests in an emotional quadrant; poker face is rare or impossible |

**Examples:**
* **Batman**: Anchors inside zone (stoic baseline). Events push him out, recovery brings him back to poker face.
* **Gordon Ramsay**: Anchors outside zone (naturally intense/overheated). Even at baseline, he's expressive. Poker face may never happen.

**There is no universal neutral state.** Neutral is personality-specific.

### 5.5 Emotional Region Behaviors

| Region | Looseness | Aggression | Character |
|--------|-----------|------------|-----------|
| Poker Face | baseline | baseline | Controlled, unreadable |
| Commanding | — | + | Pressing advantage, confident |
| Overheated | + | ++ | Forcing action, volatile |
| Guarded | − | — | Waiting, defensive |
| Shaken | ±* | ±* | Erratic, desperate |

*Shaken behavior depends on Risk Identity anchor (passive collapse vs manic desperation)

---

## 6. Play Style Axes (Derived Values)

Two axes that define strategic tendencies. **Derived** from anchors + emotional state modifiers.

### 6.1 Effective Aggression

```
effective_aggression = baseline_aggression + aggression_modifier(confidence, composure)
```

Preference for betting/raising vs checking/calling.

* Controls action **frequency**, not sizing
* Baseline from **Baseline Aggression** anchor
* Modified by current Confidence and Composure

### 6.2 Effective Looseness

```
effective_looseness = baseline_looseness + looseness_modifier(confidence, composure)
```

Width of preflop comfort ranges by position.

* Controls **which hands** to play (maps to % of top hands by position)
* Baseline from **Baseline Looseness** anchor
* Modified by current Confidence and Composure

**Ranges are never directly visible** and must be inferred by the human player through observed actions.

### 6.3 Modifier Functions

The emotional state modifiers adjust play style based on Confidence and Composure:

```
aggression_mod = (confidence - 0.5) × 0.3 + (0.5 - composure) × 0.2
looseness_mod = (confidence - 0.5) × 0.2 + (0.5 - composure) × 0.15
```

Clamped to ±0.20 (±0.30 when Shaken). See Section 18.1 for full details including the Shaken gate.

| Quadrant | Looseness Modifier | Aggression Modifier |
|----------|-------------------|---------------------|
| Poker Face | ~0 | ~0 |
| Commanding | − | + |
| Overheated | + | ++ |
| Guarded | − | − |
| Shaken | ± (Risk Identity) | ± (Risk Identity) |

**Important:** Emotional modifiers shift the cutoff but CANNOT bypass position clamps. A tilted Maniac plays their maximum allowed range, not 100% of hands. See Section 18.4 for looseness semantics.

---

## 7. Personality Anchors (Static Per Session)

Anchors define identity and do not change during a session (tournament). They act as gravity, pulling state back toward baseline.

### 7.1 Baseline Aggression (0–1)

Default betting/raising frequency.

* 0.0 = extremely passive (rarely bets/raises)
* 1.0 = extremely aggressive (constantly betting/raising)

### 7.2 Baseline Looseness (0–1)

Default hand range width.

* 0.0 = extremely tight (plays very few hands)
* 1.0 = extremely loose (plays many hands)

Maps to percentage of top hands by position:
* Position adjustments apply (tighter UTG, looser BTN)
* Exact mapping TBD during implementation

### 7.3 Ego (0–1)

How much "being wrong" events move **Confidence**.

* High ego (1.0): Getting bluffed, bad reads destroy confidence
* Low ego (0.0): Mistakes don't shake self-belief

**Affects:**
* Confidence sensitivity to outplay events
* Poker face zone size (low ego = larger zone)

### 7.4 Poise (0–1)

How much "bad outcome" events move **Composure**.

* High poise (1.0): Bad beats, coolers don't tilt
* Low poise (0.0): Variance causes emotional disruption

**Affects:**
* Composure sensitivity to luck-based events
* Poker face zone size (high poise = larger zone)

### 7.5 Expressiveness (0–1)

How much internal emotional state leaks through output.

**Controls:**
* Avatar emotion filtering (true emotion vs poker face display)
* Table talk content filtering (reveals feelings vs neutral)
* Poker face zone shape (narrows on Energy axis)

**Does NOT control:**
* How much they talk (that's Energy)
* Actual betting patterns (behavioral, not presentational)

| Expressiveness | Avatar | Talk Content |
|----------------|--------|--------------|
| High | Shows true emotion | "UGH another bad beat!", "I KNEW it" |
| Low | Poker face unless extreme | "Raise", "Call", neutral chatter |

### 7.6 Risk Identity (0–1)

Preference for variance vs safety.

* High (1.0): Risk-seeking, gamblers
* Low (0.0): Risk-averse, grinders

**Affects:**
* Stack-off thresholds
* Desperation behavior when short
* Shaken-state direction (passive collapse vs manic gamble)
* Poker face zone shape (risk-seeking narrows on Confidence, risk-averse narrows on Composure)

### 7.7 Adaptation Bias (0–1)

Willingness to adjust strategy based on opponent observations.

**Controls:**
* How quickly opponent tendencies activate adjustments
* How strongly observations bias decisions
* How quickly adjustments decay

### 7.8 Energy Anchor (0–1)

Baseline energy level where the Energy axis naturally rests.

* High anchor (1.0): Naturally animated, engaged
* Low anchor (0.0): Naturally reserved, deliberate

### 7.9 Recovery Rate (0–1)

How fast all axes return toward their baseline anchors after events.

* Fast recovery (1.0): Emotional swings are brief
* Slow recovery (0.0): States persist longer

**Important:** Recovery Rate does NOT affect poker face zone size or shape. It only affects how quickly the state point moves back toward anchor baselines.

---

## 8. Expression System

Expression is what the human player observes. It is filtered through Expressiveness and amplified by Energy.

### 8.1 Formula

```
visible_emotion = internal_state × expressiveness × energy
```

### 8.2 Output Channels

| Channel | What it shows |
|---------|---------------|
| Avatar emotion | Filtered emotional state or poker face |
| Table talk | Emotional content or neutral |
| Tempo | Decision speed (energy-driven) |

### 8.3 Combinations

| Energy | Expressiveness | Result |
|--------|----------------|--------|
| High | High | Open book — constant emotional commentary |
| High | Low | Chatty but neutral — jokes, misdirection, no tells |
| Low | High | Quiet but revealing — rare speech is telling |
| Low | Low | Stone silent — reveals nothing |

### 8.4 Expression Filtering

Expression filtering applies in two places:

1. **In the prompt**: AI is told its expressiveness level to calibrate talk content
2. **At display time**: Avatar emotion and visible reactions are filtered

### 8.5 What Expression Does NOT Hide

Actual betting patterns are behavioral, not presentational. A player who always bets big when confident has an exploitable pattern regardless of expressiveness.

* **Expressiveness hides:** Avatar mood, emotional talk content
* **Expressiveness does NOT hide:** Bet sizing patterns, action frequencies

Low expressiveness means tells require detective work, not that tells don't exist.

---

## 9. Decision Architecture (LLM-Compatible)

### 9.1 Action Set Generation (FR)

* The engine **MUST** generate a finite set of legal actions for every decision point
* Actions: fold, check, call, raise, all-in
* If raise is legal, engine **MUST** generate legal raise size options
* LLM **MUST NOT** invent actions outside the provided set

### 9.2 Bet Sizing (FR)

* LLM response **MUST** include a `sizing_rationale` field before the amount
* System provides guidance on expected sizing based on personality and emotional state
* Raise amounts **MAY** be clamped to nearest legal size bucket (future enhancement)

### 9.3 Non-Deterministic Choice (FR)

* LLM selects stochastically from the provided option set
* Selection influenced by personality, tendencies, and psychology
* Engine constrains legality; LLM chooses based on character

---

## 10. Adaptation System

### 10.1 Opponent Models (FR)

Each AI player **MUST** maintain per-opponent models including:
* Observed statistics
* Interpreted tendencies
* AI-editable notes

### 10.2 Adaptation Effects (FR)

* Adaptation **MUST** be scoped to individual opponents
* Adaptation **MUST NOT** modify personality anchors
* Adaptation **MAY** apply temporary biases to:
  * Bluff vs value preference
  * Continuation thresholds
  * Bluff-catch willingness
  * Preflop range adjustments (bounded)

### 10.3 Adaptation Decay (FR)

* Biases **MUST** decay back to baseline
* Decay rate controlled by Adaptation Bias anchor

---

## 11. Multi-Way Pot Handling (FR)

* System **MUST** select exactly one focal opponent per decision:
  * Aggressor when facing a bet
  * Most likely caller when betting
* Other players summarized as aggregate pressure factors
* Adaptation biases apply only to focal opponent
* System **MUST NOT** attempt full multi-opponent range reconstruction

---

## 12. Emotional Dynamics

### 12.1 Event Impact

Game events modify Confidence, Composure, and Energy through sensitivity anchors:

| Event Type | Primary Target | Sensitivity Anchor |
|------------|----------------|-------------------|
| Being outplayed (bluff called, bad read) | Confidence | Ego |
| Bad outcomes (bad beat, cooler, variance) | Composure | Poise |
| Action/engagement | Energy | Energy Anchor |

### 12.2 Sensitivity Scaling

The sensitivity anchor acts as a **multiplier** on event impact:

```
confidence_change = base_event_impact × ego
composure_change = base_event_impact × (1 - poise)  # High poise = less impact
```

Note: Poise is inverted because high poise means LESS sensitivity to bad outcomes.

### 12.3 Tilt Distribution Targets (FR)

System **MUST** regulate dynamics to achieve realistic tilt frequency:

| Band | Target | Definition |
|------|--------|------------|
| Low | 70–85% | Baseline play, minor deviations |
| Medium | 10–20% | Noticeable influence, exploitable |
| High | 2–7% | Significant disruption, frequent mistakes |
| Full | 0–2% | Rare extreme breakdown |

**Constraints:**
* Full tilt **MUST** be rare, short-lived, event-driven
* Tilt **MUST NOT** be a fixed-duration timer
* Absent reinforcing events, decay **MUST** accelerate toward baseline

### 12.4 Recovery

* All axes decay toward their anchor baselines (NOT toward poker face zone)
* Recovery rate controlled by Recovery Rate anchor
* Strong "springs" at extreme values prevent negative/impossible states

---

## 13. Session Rules

* **Session = Tournament**
* Anchors **NEVER** change mid-session
* Emotional state changes freely during session
* Anchor modification only through manual settings (future: between-session evolution)

---

## 14. Emotion Label Projection

Emotion labels are outward projections for display and coaching. They do not drive decisions.

### 14.1 Selection Rule

1. Test if state point (Confidence, Composure, Energy) lies inside poker face zone volume
2. If inside → **Poker Face**
3. If outside → determine quadrant from Confidence × Composure, apply Energy intensity

### 14.2 Available Labels

Primary (quadrant-based):
* Poker Face, Commanding, Overheated, Guarded, Shaken

Extended (intensity/nuance based on Energy level):
* Confident, Smug, Nervous, Frustrated, Angry, Elated, Thinking

Label selection is deterministic from position in emotional space.

### 14.3 Label to Avatar Mapping

Labels map to avatar images/expressions. Energy affects intensity within the label:

| Label | Low Energy | High Energy |
|-------|------------|-------------|
| Commanding | Quietly confident | Triumphant |
| Overheated | Simmering | Explosive |
| Guarded | Withdrawn | Paranoid |
| Shaken | Defeated | Panicking |

---

## 15. Summary

### Identity Layer (Static Anchors)

| Anchor | Purpose |
|--------|---------|
| Baseline Aggression | Default bet/raise frequency |
| Baseline Looseness | Default hand range width |
| Ego | Confidence sensitivity to outplay events |
| Poise | Composure sensitivity to bad outcomes |
| Expressiveness | Emotional transparency, zone shape on Energy |
| Risk Identity | Variance tolerance, zone shape on Confidence/Composure |
| Adaptation Bias | Opponent adjustment rate |
| Energy Anchor | Baseline energy level |
| Recovery Rate | Axis decay speed |

### State Layer (Dynamic Axes)

| Axis | Range | Driven By |
|------|-------|-----------|
| Confidence | 0–1 | Events filtered through Ego |
| Composure | 0–1 | Events filtered through Poise |
| Energy | 0–1 | Engagement, decays to Energy Anchor |

### Derived Values

| Value | Formula |
|-------|---------|
| Effective Aggression | baseline_aggression + modifier(confidence, composure) |
| Effective Looseness | baseline_looseness + modifier(confidence, composure) |

### Expression Layer (Filtered Output)

| Channel | Filtering |
|---------|-----------|
| Avatar emotion | internal_state × expressiveness × energy |
| Table talk content | Emotional vs neutral based on expressiveness |
| Tempo | Energy-driven decision speed |

### Poker Face Zone

| Property | Determination |
|----------|---------------|
| Center | Universal (fixed for all players) |
| Size | Poise (larger), Ego (smaller when high) |
| Shape | Expressiveness (narrows Energy), Risk Identity (narrows Confidence or Composure) |

### Core Invariants

* Identity is stable (anchors never change mid-session)
* Emotion is dynamic (axes move freely)
* Expression is filtered and amplified
* Decisions are constrained but non-deterministic
* Poker face is a 3D zone, not a default state
* Recovery brings you to YOUR anchors, not to the zone

The result is AI poker that feels alive, readable, and strategically coherent.

---

## 16. Implementation Plan

### Phase Overview

Implementation follows **vertical slices** for early testing and feedback.

### Phase 1: Core Foundation

**Goal**: New data model + basic emotional states working

**Deliverables**:
- Anchor schema (9 anchors)
- Confidence + Composure axes (2 of 3 dynamic axes)
- Quadrant computation (Commanding, Overheated, Guarded, Shaken)
- Basic emotion label mapping
- Derived aggression/looseness from anchors + axes

**Testable**: AI players have emotional states that shift. Quadrant labels appear. Different personalities feel different.

**Skipped**: Energy axis, Poker Face zone, expression filtering

### Phase 2: Energy + Expression

**Goal**: Expression volume system working

**Deliverables**:
- Energy axis (3rd dynamic axis)
- Expression filtering formula
- Energy replaces table_talk trait
- Tempo/chattiness driven by Energy × Expressiveness

**Testable**: Some players naturally loud, others quiet. Energy affects how much they emote/talk.

### Phase 3: Poker Face Zone

**Goal**: 3D volume membership working

**Deliverables**:
- Zone geometry (center, size from Poise/Ego, shape from Expressiveness/Risk Identity)
- Membership test against current (Confidence, Composure, Energy)
- Poker Face label when inside zone
- Quadrant labels when outside zone

**Testable**: Batman shows poker face at baseline, Gordon Ramsay doesn't. Players enter/exit zone based on events.

### Phase 4: Event Sensitivity

**Goal**: Ego/Poise routing working

**Deliverables**:
- Pressure events routed through Ego → Confidence
- Pressure events routed through Poise → Composure
- Sensitivity scaling (high Ego = bigger Confidence swings)
- Recovery rate per personality

**Testable**: High-ego player cracks when bluff is called. High-poise player shrugs off bad beats.

### Phase 5: Integration + Polish

**Goal**: Full system integrated

**Deliverables**:
- Prompt injection with new psychology
- Composure effects (intrusive thoughts)
- UI updates for new emotion display
- personalities.json migration (or regeneration)
- Tech debt documentation

**Testable**: Full gameplay loop with new psychology system end-to-end.

### Phase Dependencies

```
Phase 1 (Core)
    ↓
Phase 2 (Energy) ← Can ship after Phase 1
    ↓
Phase 3 (Poker Face) ← Needs Energy axis
    ↓
Phase 4 (Events) ← Can run parallel with Phase 3
    ↓
Phase 5 (Integration)
```

---

## 17. Migration Notes

### What Gets Deleted

| Component | Reason |
|-----------|--------|
| 4D emotional model (valence, arousal, control, focus) | Replaced by 2D quadrants + Energy |
| `trait_converter.py` | No backward compatibility |
| `table_talk` trait | Replaced by Energy axis |
| Elastic traits for aggression/looseness | Now derived values |

### What Gets Kept

| Component | Notes |
|-----------|-------|
| Pressure event detection | Relabeled to PRD terminology |
| Intrusive thoughts system | Triggered by pressure source |
| Pressure source tracking | For intrusive thoughts |
| Emotional narration (LLM) | **Tech debt** - keep for downstream systems |
| Emotion → avatar mapping | Simplified to quadrants + intensity |

### Tech Debt

**Emotional Narration System**
- Location: `emotional_state.py` → `EmotionalStateGenerator`
- Issue: LLM call to generate `narrative` and `inner_voice` may be redundant now that emotion labels are computed
- Used by: Downstream display systems (identify during implementation)
- Recommendation: Evaluate if computed labels + personality verbal tics can replace LLM narration
- Priority: Low - functional but potentially unnecessary complexity

---

## 18. Design Decisions (Resolved)

### 18.1 Modifier Functions (Confidence/Composure → Aggression/Looseness)

**Normal states** (outside Shaken quadrant):

```
aggression_mod = (confidence - 0.5) × 0.3 + (0.5 - composure) × 0.2
looseness_mod = (confidence - 0.5) × 0.2 + (0.5 - composure) × 0.15

# Hard clamp to ±0.20 so baseline personality dominates
aggression_mod = clamp(aggression_mod, -0.20, +0.20)
looseness_mod = clamp(looseness_mod, -0.20, +0.20)
```

**Shaken gate** (confidence < 0.35 AND composure < 0.35):

When both axes are low, behavior splits based on Risk Identity:

```
shaken_intensity = (0.35 - confidence) + (0.35 - composure)  # 0 to 0.7

if risk_identity > 0.5:  # Risk-seeking → manic spew
    aggression_mod += shaken_intensity × 0.3
    looseness_mod += shaken_intensity × 0.3
else:  # Risk-averse → passive collapse
    aggression_mod -= shaken_intensity × 0.3
    looseness_mod -= shaken_intensity × 0.3

# Allow slightly wider range for Shaken (±0.30)
aggression_mod = clamp(aggression_mod, -0.30, +0.30)
looseness_mod = clamp(looseness_mod, -0.30, +0.30)
```

**Key insight:** Low composure ≠ always aggressive. The Shaken gate captures the human split between "spew" and "shell."

### 18.2 Poker Face Zone Geometry

**Shape:** Ellipsoid in 3D space

```
# Membership test
def is_in_poker_face_zone(conf, comp, energy, zone_params):
    c0, comp0, e0 = zone_params.center  # Universal center
    rc, rcomp, re = zone_params.radii   # Personality-shaped radii

    distance = ((conf - c0)/rc)² + ((comp - comp0)/rcomp)² + ((energy - e0)/re)²
    return distance <= 1.0
```

**Center (universal):**
- Confidence: ~0.65
- Composure: ~0.75
- Energy: ~0.4

**Radii (personality-shaped):**

| Radius | Base | Modified By |
|--------|------|-------------|
| rc (confidence) | 0.25 | Narrower for risk-seeking (Risk Identity) |
| rcomp (composure) | 0.25 | Larger for high Poise, narrower for risk-averse |
| re (energy) | 0.20 | Narrower for high Expressiveness |

Exact formulas TBD during Phase 3 implementation.

### 18.3 Sensitivity Scaling (Ego/Poise)

**Base formula:**

```
# Ego: high = more sensitive to being outplayed
confidence_change = base_impact × (floor + (1 - floor) × ego)

# Poise: high = less sensitive to bad outcomes
composure_change = base_impact × (floor + (1 - floor) × (1 - poise))
```

**Severity-based floors:**

| Event Severity | Floor | Example Events |
|----------------|-------|----------------|
| Minor | 0.20 | Small pot loss, routine fold |
| Normal | 0.30 | Standard win/loss |
| Major | 0.40 | Bad beat, big bluff called, cooler |

**Optional curve enhancement:**

```
# Linear (default)
sensitivity = floor + (1 - floor) × ego

# Squared (makes extremes more distinct)
sensitivity = floor + (1 - floor) × ego²
```

Start with linear. Add squared curve if extremes don't feel distinct enough during testing.

### 18.4 Looseness Semantics

**Core principle:** Looseness controls how deep into the hand-strength ordering a player is willing to go, adjusted by position.

Looseness does NOT mean:
- Play random trash
- Ignore position
- Deterministic cutoffs

#### The Looseness Flow

```
1. Baseline Looseness (anchor, 0–1)
   ↓
2. + Emotional modifier (from confidence/composure, see §18.1)
   ↓
3. = Effective Looseness (0–1 psychological tendency)
   ↓
4. Map to position range:
   range_pct = position_min + (position_max - position_min) × effective_looseness
   ↓
5. Clamp range_pct to [position_min, position_max]
   ↓
6. For hands near the cutoff → probabilistic (fuzzy edges)
```

**Key insight:** The 0–1 scale is the *personality* (internal state). The range % is the *behavior* (external action). We own the mapping.

#### Position-Adjusted Range Clamps

| Position | Min Range | Max Range |
|----------|-----------|-----------|
| Early (UTG, UTG+1) | 8% | 35% |
| Middle (MP, HJ) | 10% | 45% |
| Late (CO, BTN) | 15% | 65% |
| Blinds (SB, BB) | 12% | 55% |

#### Hard Rules

- Looseness MUST map to a position-adjusted hand-rank cutoff
- No personality MAY play 100% of hands in any position
- No personality MAY play less than 8% in any position
- Emotional modifiers shift the cutoff but CANNOT bypass clamps
- Clamps apply to the **output** (range %), not the **input** (looseness value)

#### Worked Examples

**Gordon Ramsay (Maniac, tilted)**
- Baseline Looseness: 0.80
- Gets tilted: modifier = +0.15
- Effective Looseness: 0.95
- BTN mapping: 15% + (65% - 15%) × 0.95 = 15% + 47.5% = 62.5%
- Within [15%, 65%] clamp → final range = 62.5%
- Hands ranked 50-62.5%: probabilistic play
- Hands ranked 63%+: rare spew via probability tail

**Batman (Nit, confident)**
- Baseline Looseness: 0.15
- Confident: modifier = +0.10
- Effective Looseness: 0.25
- BTN mapping: 15% + 50% × 0.25 = 27.5%
- Within clamps → final range = 27.5%
- Still tight, but slightly wider than baseline 20%

**Extreme case (theoretical)**
- Effective Looseness: 1.20 (pushed beyond 1.0 by extreme tilt)
- BTN mapping: 15% + 50% × 1.20 = 75%
- Exceeds 65% clamp → clamped to 65%
- Tilt pushes to maximum allowed, but realism preserved

#### Probabilistic Margins

The bottom ~10-15% of a player's range should be probabilistic, not deterministic:

```
# Example: Player's cutoff is 40% for this position
# Hands ranked 30-40% (bottom quarter of range):
#   - Play probability decreases linearly toward the cutoff
#   - Hand at 30% mark: ~90% play
#   - Hand at 35% mark: ~60% play
#   - Hand at 40% mark: ~30% play
#   - Hand at 45% mark: ~5% play (occasional spew)
```

This creates believable variance and harder-to-exploit edges.

#### Archetype Sanity Check

| Archetype | Baseline Looseness | EP Range | BTN Range |
|-----------|-------------------|----------|-----------|
| Nit | 0.15 | ~10% | ~22% |
| Solid reg | 0.35 | ~15% | ~33% |
| Loose reg | 0.55 | ~20% | ~43% |
| Maniac | 0.80 | ~28% | ~55% |

*Ranges computed via: `position_min + (position_max - position_min) × looseness`*

---

## 19. Open Questions

Items to resolve during implementation:

1. **Tilt distribution tuning**: Parameter values to achieve target tilt frequencies (70-85% baseline, 10-20% medium, etc.)
2. **Exact ellipsoid radius formulas**: How Poise, Ego, Expressiveness, Risk Identity map to rc, rcomp, re
3. **Energy event triggers**: What gameplay events move the Energy axis (action density, big moments, etc.)

---

## 20. Phase 1 Implementation Status

**Status**: ✅ COMPLETE (2026-02-05)

### What Was Implemented

| Deliverable | Status | Notes |
|-------------|--------|-------|
| Anchor schema (9 anchors) | ✅ | `PersonalityAnchors` dataclass in `player_psychology.py` |
| Confidence + Composure axes | ✅ | `EmotionalAxes` dataclass, dynamic during play |
| Energy axis (static) | ✅ | Energy = baseline_energy (static in Phase 1) |
| Quadrant computation | ✅ | `get_quadrant()` function, `EmotionalQuadrant` enum |
| Derived aggression/looseness | ✅ | `effective_aggression`, `effective_looseness` properties |
| Shaken gate logic | ✅ | `compute_modifiers()` with risk_identity split |
| Position clamps | ✅ | `POSITION_CLAMPS` in `range_guidance.py` |
| personalities.json migration | ✅ | All 50 personalities converted to 9-anchor schema |

### Files Modified

| File | Change |
|------|--------|
| `poker/player_psychology.py` | Added anchors, axes, quadrant, derived values; refactored PlayerPsychology |
| `poker/range_guidance.py` | Added `looseness_to_range_pct()`, position clamps |
| `poker/emotional_state.py` | Marked 4D model as deprecated |
| `poker/elasticity_manager.py` | Marked as deprecated, inlined helpers |
| `poker/personalities.json` | Regenerated with 9-anchor schema |
| `poker/poker_player.py` | Updated `get_personality_modifier()` for anchors |
| `flask_app/routes/debug_routes.py` | Returns quadrant + axes |
| `flask_app/handlers/game_handler.py` | Logs quadrant instead of valence |

### Files Deleted

| File | Reason |
|------|--------|
| `poker/trait_converter.py` | No backward compatibility needed per plan |

### Test Coverage

- 42 new unit tests in `tests/test_psychology_v2.py`
- All 1506 tests pass (including 42 new + existing)

### Backward Compatibility

The following properties are maintained for existing code:
- `PlayerPsychology.tightness` → returns `1 - effective_looseness`
- `PlayerPsychology.aggression` → returns `effective_aggression`
- `PlayerPsychology.confidence` → returns `axes.confidence`
- `PlayerPsychology.composure` → returns `axes.composure`
- `PlayerPsychology.table_talk` → returns `axes.energy`
- `PlayerPsychology.traits` → returns dict with legacy trait names

### What's Deferred to Later Phases

| Item | Phase | Notes |
|------|-------|-------|
| ~~Dynamic energy~~ | ~~Phase 2~~ | ✅ Complete - see §21 |
| ~~Expression filtering~~ | ~~Phase 2~~ | ✅ Complete - see §21 |
| ~~Poker Face zone~~ | ~~Phase 3~~ | ✅ Complete - see §22 |
| ~~Ego/Poise sensitivity scaling~~ | ~~Phase 4~~ | ✅ Complete - see §23 |
| UI updates | Phase 5 | Debug routes updated, main UI unchanged |

### Known Limitations

1. ~~**Energy is static**: Energy axis doesn't change during play (always = baseline_energy)~~ → Fixed in Phase 2
2. ~~**No Poker Face zone**: All players show quadrant emotions, no masking~~ → Fixed in Phase 3
3. ~~**Simplified sensitivity**: Event sensitivity uses basic multipliers, not full floor-based scaling from §18.3~~ → Fixed in Phase 4

---

## 21. Phase 2 Implementation Status

**Status**: ✅ COMPLETE (2026-02-05)

### What Was Implemented

| Deliverable | Status | Notes |
|-------------|--------|-------|
| Energy axis (dynamic) | ✅ | Energy changes via pressure events, recovers toward baseline |
| Expression filtering formula | ✅ | `visibility = expressiveness × energy` in `expression_filter.py` |
| Energy replaces table_talk | ✅ | `table_talk` property returns `axes.energy` |
| Tempo/chattiness guidance | ✅ | `get_tempo_guidance()`, `get_dramatic_sequence_guidance()` |
| Energy events | ✅ | engagement/disengagement events (all_in_moment, consecutive_folds, etc.) |
| Edge springs | ✅ | Push away from 0.15 and 0.85 extremes in `recover()` |

### Files Modified

| File | Change |
|------|--------|
| `poker/player_psychology.py` | Dynamic energy in `apply_pressure_event()`, edge springs in `recover()` |
| `poker/expression_filter.py` | New file with visibility calculation, emotion dampening |
| `poker/controllers.py` | Expression guidance injection |

---

## 22. Phase 3 Implementation Status

**Status**: ✅ COMPLETE (2026-02-05)

### What Was Implemented

| Deliverable | Status | Notes |
|-------------|--------|-------|
| Zone geometry | ✅ | `PokerFaceZone` class with center (0.65, 0.75, 0.40) |
| Size from Poise/Ego | ✅ | `create_poker_face_zone()` adjusts radii based on anchors |
| Shape from Expressiveness/Risk Identity | ✅ | Asymmetric narrowing based on traits |
| Membership test | ✅ | `is_in_poker_face_zone()`, `zone_distance` property |
| Poker Face label when inside zone | ✅ | `get_display_emotion()` returns "poker_face" when in zone |
| Quadrant labels when outside zone | ✅ | Falls through to quadrant-based emotion |

### Files Modified

| File | Change |
|------|--------|
| `poker/player_psychology.py` | `PokerFaceZone` class, `create_poker_face_zone()`, zone membership methods |

### Testable Behaviors

- Batman (high poise, low ego) → large poker face zone, often shows poker face
- Gordon Ramsay (low poise, high expressiveness) → small zone, rarely in poker face
- Players enter/exit zone based on game events pushing confidence/composure/energy

---

## 23. Phase 4 Implementation Status

**Status**: ✅ COMPLETE (2026-02-05)

### What Was Implemented

| Deliverable | Status | Notes |
|-------------|--------|-------|
| Ego → Confidence routing | ✅ | `_calculate_sensitivity(self.anchors.ego, floor)` in `apply_pressure_event()` |
| Poise → Composure routing | ✅ | `_calculate_sensitivity(1.0 - self.anchors.poise, floor)` - inverted for high poise = less sensitive |
| Sensitivity scaling | ✅ | Severity-based floors: minor=0.20, normal=0.30, major=0.40 via `_get_severity_floor()` |
| Recovery rate per personality | ✅ | Uses `anchors.recovery_rate` in `recover()` method |
| Asymmetric recovery | ✅ | Below baseline: sticky (0.6 + 0.4×current), Above baseline: slow decay (0.8) |

### Key Functions Added

| Function | Purpose |
|----------|---------|
| `_get_severity_floor(event_name)` | Returns 0.20/0.30/0.40 based on event severity |
| `_calculate_sensitivity(anchor, floor)` | `floor + (1 - floor) × anchor` |
| `EVENT_SEVERITY` dict | Maps events to 'minor', 'normal', 'major' |
| `RECOVERY_*` constants | Asymmetric recovery parameters |

### Testable Behaviors

- High-ego player (ego=0.8) loses ~70% more confidence when bluff is called vs low-ego (ego=0.2)
- High-poise player (poise=0.8) loses ~70% less composure on bad beats vs low-poise (poise=0.2)
- Major events (bad_beat, got_sucked_out) have higher minimum impact than minor events (fold, small_loss)
- Tilted players (below baseline) recover slower; confident players (above baseline) stay confident longer

---

## 24. Revised Phase Plan (Phases 5-9)

The original Phase 5 (Integration + Polish) has been expanded into multiple phases to incorporate the Zone Benefits System from `PSYCHOLOGY_ZONES_MODEL.md`.

### Phase 5: Zone Detection System

**Goal**: Detect which zones (sweet spots + penalties) a player is in, with blending

**Existing Infrastructure**:
- `apply_composure_effects()` in `player_psychology.py` - composure-based tilt (Tilted zone only)
- `_inject_intrusive_thoughts()` - intrusive thoughts for low composure
- `_degrade_strategic_info()` - removes strategic phrases when tilted
- `INTRUSIVE_THOUGHTS` dict - thoughts by pressure source
- `PromptConfig.tilt_effects` - toggle for the whole system

**New Deliverables**:
- `get_zone_strengths(confidence, composure)` function returning two-layer dict:
  - `sweet_spots`: dict of {zone_name: strength} (normalized to sum=1.0)
  - `penalties`: dict of {zone_name: strength} (raw, can stack)
- Sweet spot detection (Poker Face, Guarded, Commanding, Aggro) with circular geometry
- Penalty zone detection (Tilted, Shaken, Overheated, Overconfident, Detached) with edge geometry
- Energy manifestation helper (`get_zone_manifestation(energy)`)
- `ZoneEffects` dataclass to hold computed effects

**Integration**:
- Zone detection called from `apply_tilt_effects()` (renamed to `apply_zone_effects()`)
- Replaces current composure-only thresholds with full 2D zone model

**Poker Face Zone Update**:
- Phase 3 implemented center at (0.65, 0.75, 0.40)
- Zones Model specifies center at (0.52, 0.72, 0.45)
- **Decision**: Update to Zones Model values for consistency with other sweet spots
- Will require updating `PokerFaceZone` constants in `player_psychology.py`

**Zone Geometry Summary**:

| Zone | Type | Center (conf, comp) | Radius | Energy Role |
|------|------|---------------------|--------|-------------|
| Poker Face | 3D Ellipsoid | (0.52, 0.72) | 0.16 | Affects membership |
| Guarded | 2D Circle | (0.28, 0.72) | 0.15 | Affects manifestation only |
| Commanding | 2D Circle | (0.78, 0.78) | 0.14 | Affects manifestation only |
| Aggro | 2D Circle | (0.68, 0.48) | 0.12 | Affects manifestation only |

| Penalty Zone | Type | Boundary |
|--------------|------|----------|
| Tilted | Edge | Composure < 0.35 |
| Overconfident | Edge | Confidence > 0.90 |
| Shaken | Corner | Conf < 0.35 AND Comp < 0.35 |
| Overheated | Corner | Conf > 0.65 AND Comp < 0.35 |
| Detached | Corner | Conf < 0.35 AND Comp > 0.65 |

**Testable**: Given (confidence, composure, energy), system returns correct zone memberships with strengths.

**Key Design Reference**: See `PSYCHOLOGY_ZONES_MODEL.md` §Zone Detection (Pseudocode)

### Phase 6: Zone Benefits - Intrusive Thoughts

**Goal**: Penalty zones inject intrusive thoughts that disrupt decision-making

**Priority**: HIGH (makes AI feel human when tilted)

**Existing Infrastructure**:
- `INTRUSIVE_THOUGHTS` dict in `player_psychology.py` - thoughts by pressure source
- `_inject_intrusive_thoughts()` - injects "[What's running through your mind: ...]"
- `_add_composure_strategy()` - adds "[Current mindset: ...]" bad advice
- Currently only triggers on low composure (Tilted zone)

**New Deliverables**:
- Expand `INTRUSIVE_THOUGHTS` to include all penalty zones:
  - Existing: Tilted (low composure) - already has pressure-source-based thoughts
  - New: Shaken (low conf + low comp) - risk-identity split (spew vs collapse)
  - New: Overheated (high conf + low comp) - manic aggression thoughts
  - New: Overconfident (high conf) - dismiss opponent strength
  - New: Detached (low conf + high comp) - overly passive thoughts
- Energy manifestation variants (low energy vs high energy flavors)
- Probabilistic injection based on penalty intensity:
  - 0-25% intensity → 25% chance
  - 25-50% → 50% chance
  - 50-75% → 75% chance
  - 75%+ → 100% chance (cliff)

**Integration**:
- Modify `_inject_intrusive_thoughts()` to use zone detection, not just composure
- Add zone-aware thought selection

**Testable**: Tilted player's prompts contain intrusive thoughts. Overconfident player dismisses opponents. Frequency scales with zone intensity.

**Key Design Reference**: See `PSYCHOLOGY_ZONES_MODEL.md` §Penalty Zone Effects

### Phase 7: Zone Benefits - Information Filtering & Strategy Guidance

**Goal**: Zones control what information the AI sees and how it's framed

**Priority**: HIGH (different zones create different playstyles)

**Existing Infrastructure**:
- `PromptConfig` already has toggles for: `pot_odds`, `hand_strength`, `gto_equity`, `gto_verdict`, `mind_games`, etc.
- `_degrade_strategic_info()` removes strategic phrases for tilted players
- `guidance_injection` field for appending extra guidance
- Opponent stats available via session memory / opponent_intel
- Equity calculations available via equity_verdict_info

#### 7.1 Zone Strategy Architecture

Each zone has multiple **strategy variations** that can be selected probabilistically. This allows variety and tuning without code changes.

```python
@dataclass
class ZoneStrategy:
    """A strategy variation within a zone."""
    name: str                    # e.g., "heighten_awareness", "target_weak"
    weight: float                # Selection probability (0.0-1.0)
    template: str                # Guidance text with {placeholders}
    requires: list[str]          # Required context keys (skip if unavailable)
    min_strength: float = 0.25   # Minimum zone strength to activate

ZONE_STRATEGIES = {
    'aggro': [
        ZoneStrategy('heighten_awareness', 0.3,
            "[AGGRO MODE]\nWatch for signs of weakness. Nervous opponents fold to pressure.",
            requires=[]),
        ZoneStrategy('analyze_behavior', 0.4,
            "[AGGRO MODE]\n{opponent_analysis}\nExploit their patterns.",
            requires=['opponent_analysis']),
        ZoneStrategy('target_weak', 0.3,
            "[AGGRO MODE]\n{weak_player_note}\nAttack the vulnerability.",
            requires=['weak_player_note']),
    ],
    'poker_face': [...],
    'guarded': [...],
    'commanding': [...],
}
```

**Selection Logic**:
1. Filter strategies by `min_strength` (skip if zone strength too low)
2. Filter by `requires` (skip if required context unavailable)
3. Weighted random selection from remaining strategies
4. Render template with available context

#### 7.2 Zone Context Data

Context data passed to zone strategy templates:

| Context Key | Source | Used By |
|-------------|--------|---------|
| `opponent_stats` | Session memory | All zones |
| `opponent_displayed_emotion` | Their `get_display_emotion()` | Aggro, Commanding |
| `opponent_recent_talk` | Recent actions log | Aggro (reading nervousness) |
| `opponent_fold_pct` | Session stats | Aggro |
| `opponent_aggression_pct` | Session stats | Guarded (exploit passive traps) |
| `weak_player_note` | Computed: lowest composure or worst stats | Aggro |
| `opponent_analysis` | Computed summary | Aggro, Commanding |
| `equity_vs_ranges` | Equity calculator | Poker Face |
| `balance_reminder` | Static or computed | Poker Face |
| `trap_opportunity` | Board texture + opponent tendencies | Guarded |
| `leverage_note` | Stack-to-pot ratio | Commanding |

**Note**: Not all context is always available. Strategy selection filters by `requires`.

#### 7.3 Deliverables

**New Code**:
- `ZoneStrategy` dataclass in `player_psychology.py`
- `ZONE_STRATEGIES` dict with strategy pools per zone
- `select_zone_strategy(zone_name, strength, context)` function
- `build_zone_guidance(zone_effects, context)` returns rendered guidance string
- `ZoneContext` dataclass to hold available context data

**New YAML sections** in `decision.yaml`:
- `zone_poker_face`, `zone_guarded`, `zone_commanding`, `zone_aggro` (base templates)
- Strategy variations can override or extend base templates

**Integration Points**:
- `controllers.py`: Build `ZoneContext` from game state + memory
- `controllers.py`: Call `build_zone_guidance()` with zone effects + context
- `prompt_manager.py`: New `zone_context` parameter to `render_decision_prompt()`

#### 7.4 Gradual Activation

Zone effects scale with zone strength:

| Zone Strength | Effect Level | Strategy Selection |
|---------------|--------------|-------------------|
| 0-10% | None | No zone guidance |
| 10-25% | Minimal | Only `min_strength=0.1` strategies |
| 25-50% | Light | Standard strategies, short templates |
| 50-75% | Moderate | Full strategies, detailed templates |
| 75-100% | Full | Intense strategies, opponent-specific calls |

#### 7.5 Blending Multiple Zones

When in multiple sweet spots (e.g., 60% Poker Face + 40% Commanding):
1. Select strategy from primary zone (highest strength)
2. Optionally append secondary zone hint
3. Header shows blend: `[POKER FACE MODE | Commanding edge]`

**Testable**: Player in Aggro zone with opponent stats sees targeted guidance. Player without opponent data sees generic awareness prompt. Blended zones show combined header.

**Key Design Reference**: See `PSYCHOLOGY_ZONES_MODEL.md` §Sweet Spot Benefits

### Phase 8: Tone & Strategy Framing

**Goal**: Zones affect the tone and framing of advice, not just what's shown

**Priority**: MEDIUM (polish after core functionality)

**Deliverables**:
- Per-zone tone strings (Analytical, Patient, Assertive, Aggressive, etc.)
- Strategy override/bad advice injection for deep penalties
- Energy-based tempo guidance ("Take your time" vs "Quick, decisive action")
- Zone mode headers in prompts (e.g., `[POKER FACE MODE]`, `[AGGRO MODE - rattled]`)

**Testable**: Prompts have appropriate tone markers. Deep tilt prompts contain bad advice injection.

### Phase 9: Game Mode Integration

**Goal**: Psychology zones as a configurable layer on existing game modes

**Existing Infrastructure**:
- `PromptConfig` already has `tilt_effects` toggle
- `PromptConfig` has `emotional_state` toggle
- Game modes (casual, standard, pro, competitive) defined in `game_modes.yaml`
- Per-player prompt config already supported

**Approach**:
The psychology zone system is **not a new game mode**. It's a dynamic prompt modification layer that:
1. Reads player's (confidence, composure, energy) each decision
2. Computes zone strengths
3. Dynamically modifies prompt content based on zones
4. Operates within whatever game mode is selected

**New Deliverables**:
- New `PromptConfig` toggles:
  - `zone_sweet_spots: bool` - Enable sweet spot bonuses (Poker Face, Guarded, Commanding, Aggro)
  - `zone_penalties: bool` - Enable penalty effects (Tilted, Shaken, Overheated, Overconfident, Timid, Detached)
  - (Note: existing `tilt_effects` can become alias for `zone_penalties`)
- Update game_modes.yaml presets:
  - **casual**: Both enabled (full psychology)
  - **standard**: Both enabled
  - **pro**: Sweet spots only (`zone_penalties: false`) - harder AIs
  - **competitive**: Sweet spots only - hardest AIs
- UI indication when player is in a zone (optional visual)

**Testable**: Pro mode AIs don't tilt but still get zone bonuses. Casual mode AIs show full emotional range.

**Key Insight**: This is not "psychology mode" - psychology is always there via anchors. This just controls how much zones affect the **prompt**.

### Phase 10: Experiment & Tuning

**Goal**: Validate system behavior through AI tournaments

**Deliverables**:
- Experiment config for zone distribution measurement
- Metrics collection:
  - % time in each zone per personality
  - Zone transition frequency
  - Correlation between zone and decision quality (if measurable)
  - Tilt frequency vs target (70-85% baseline, 10-20% medium, 2-7% high, 0-2% full)
- Analysis scripts for experiment results
- Parameter tuning based on findings:
  - Zone radii
  - Penalty thresholds
  - Gravity strength
  - Recovery constants

**Testable**: Can run tournament, collect zone data, analyze results.

### Phase Dependencies (Revised)

```
Phase 1 (Core) ✅
    ↓
Phase 2 (Energy) ✅
    ↓
Phase 3 (Poker Face) ✅
    ↓
Phase 4 (Events) ✅
    ↓
Phase 5 (Zone Detection) ✅
    ↓
Phase 6 (Intrusive Thoughts) ✅
    ↓
Phase 7 (Zone Benefits) ✅
    ↓
Phase 8 (Tone & Framing) ✅  ← Added Timid zone
    ↓
Phase 5 (Zone Detection) ✅
    ↓
Phase 6 (Intrusive Thoughts) ✅
    ↓
Phase 7 (Info Filtering) ✅
    ↓
Phase 8 (Tone/Framing) ← Polish, can be deferred
    ↓
Phase 9 (Config Toggles) ✅
    ↓
Phase 10 (Experiments) ← Validation and tuning
```

### Critical Path

**Minimum viable zone system**: Phases 5 + 6 + 7 ✅ COMPLETE

This gives us:
- Zone detection with blending (Phase 5) ✅
- Intrusive thoughts for all penalties (Phase 6) ✅
- Information filtering for sweet spots (Phase 7) ✅

**What we already have** (from Phases 1-4):
- Dynamic emotional state (confidence, composure, energy) ✅
- Event sensitivity (Ego/Poise routing) ✅
- Basic tilt effects (composure-only, being replaced) ✅

**Remaining phases**:
- Phase 8: Tone/framing refinement (deferred - basic tone via zone headers exists)
- Phase 10: Experiment validation

---

## 25. Design Decisions: Zone Context & Opponent Awareness

### 25.1 Opponent Emotional Reads

Zones like Aggro benefit from knowing opponent emotional state. Sources of opponent emotion data:

| Source | What It Provides | Availability |
|--------|------------------|--------------|
| `get_display_emotion()` | Their avatar emotion (angry, confident, etc.) | ✅ Already exists |
| Recent table talk | What they said, tone, nervousness | ✅ In recent actions |
| Recent events | "Lost big pot", "got bluffed" | 🔄 Partial (pressure events are private) |
| Betting patterns | Erratic sizing = tilted? | ⚠️ Would need analysis |

**Initial Approach**: Use displayed emotion + table talk. Don't infer from betting patterns yet.

### 25.2 Strategy Selection Philosophy

Zone strategies are selected **probabilistically** to create variety:
- Same zone can produce different guidance each time
- Strategies filtered by available context (no "target weak player" if we don't know who's weak)
- Weights tunable via experiments

**Key Insight**: We don't need to decide exact strategy content now. The architecture supports adding/removing/reweighting strategies without code changes.

### 25.3 What Gets Tuned in Phase 10

| Parameter | Starting Value | Tuning Goal |
|-----------|----------------|-------------|
| Strategy weights | Equal (0.33 each) | Find which strategies produce best play |
| `min_strength` thresholds | 0.25 | When should zone effects kick in? |
| Context requirements | Conservative | Which strategies need which data? |
| Template intensity | Moderate | How directive should zone guidance be? |
| Opponent awareness depth | Minimal | How much opponent analysis to surface? |

### 25.4 Deferred Decisions

These decisions are explicitly deferred to Phase 10 tuning:

1. **How much opponent analysis?** Start minimal, add if helpful
2. **Strategy probability weights** Start equal, adjust based on results
3. **Template wording** Write initial versions, iterate based on AI behavior
4. **Cross-zone interactions** How do penalties + sweet spots combine?
5. **Energy manifestation details** Low vs high energy flavor text

---

## 26. Phase 5 Implementation Status

**Status**: ✅ COMPLETE (2026-02-05)

### What Was Implemented

| Deliverable | Status | Notes |
|-------------|--------|-------|
| `get_zone_effects()` function | ✅ | Returns `ZoneEffects` dataclass with sweet_spots, penalties, manifestation |
| `ZoneEffects` dataclass | ✅ | Frozen dataclass with properties: primary_sweet_spot, primary_penalty, total_penalty_strength, in_neutral_territory |
| Sweet spot detection (circular) | ✅ | `_detect_sweet_spots()` with cosine falloff, centers/radii match PRD |
| Penalty zone detection (edge) | ✅ | `_detect_penalty_zones()` with linear gradients from thresholds |
| Sweet spot normalization | ✅ | Normalized to sum=1.0 for blending |
| Penalty stacking | ✅ | Raw strengths, can exceed 1.0 when in multiple penalty zones |
| Energy manifestation | ✅ | `_get_zone_manifestation()` returns 'low_energy', 'balanced', 'high_energy' |
| Poker Face zone center update | ✅ | Updated to (0.52, 0.72, 0.45) per Zones Model |

### Key Functions Added

| Function | Location | Purpose |
|----------|----------|---------|
| `get_zone_effects()` | player_psychology.py:470-523 | Main entry point for zone detection |
| `_detect_sweet_spots()` | player_psychology.py:357-389 | Detect circular sweet spot zones |
| `_detect_penalty_zones()` | player_psychology.py:392-446 | Detect edge-based penalty zones |
| `_calculate_sweet_spot_strength()` | player_psychology.py:317-354 | Cosine falloff strength calculation |
| `_get_zone_manifestation()` | player_psychology.py:449-467 | Energy → manifestation mapping |

### Zone Constants

| Zone | Center (conf, comp) | Radius | Implemented |
|------|---------------------|--------|-------------|
| Poker Face | (0.52, 0.72) | 0.16 | ✅ |
| Guarded | (0.28, 0.72) | 0.15 | ✅ |
| Commanding | (0.78, 0.78) | 0.14 | ✅ |
| Aggro | (0.68, 0.48) | 0.12 | ✅ |

| Penalty Zone | Threshold | Implemented |
|--------------|-----------|-------------|
| Tilted | Composure < 0.35 | ✅ |
| Overconfident | Confidence > 0.90 | ✅ |
| Shaken | Conf < 0.35 AND Comp < 0.35 | ✅ |
| Overheated | Conf > 0.65 AND Comp < 0.35 | ✅ |
| Detached | Conf < 0.35 AND Comp > 0.65 | ✅ |

### Test Coverage

- `tests/test_psychology_zones.py` - Comprehensive unit tests
- Tests for: sweet spot strength at center/edge, detection at zone centers, penalty thresholds, zone blending, normalization, energy manifestation

---

## 27. Phase 6 Implementation Status

**Status**: ✅ COMPLETE (2026-02-05)

### What Was Implemented

| Deliverable | Status | Notes |
|-------------|--------|-------|
| Tilted zone thoughts (pressure-source) | ✅ | `INTRUSIVE_THOUGHTS` dict with bad_beat, bluff_called, big_loss, etc. |
| Shaken zone thoughts (risk-identity split) | ✅ | `SHAKEN_THOUGHTS` with 'risk_seeking' and 'risk_averse' variants |
| Overheated zone thoughts | ✅ | `OVERHEATED_THOUGHTS` - manic aggression |
| Overconfident zone thoughts | ✅ | `OVERCONFIDENT_THOUGHTS` - dismissive of opponents |
| Detached zone thoughts | ✅ | `DETACHED_THOUGHTS` - overly passive |
| Energy manifestation variants | ✅ | `ENERGY_THOUGHT_VARIANTS` dict with low/high energy flavors per zone |
| Probabilistic injection | ✅ | `_should_inject_thoughts()` with 10%/50%/75%/100% thresholds |
| Bad advice by zone | ✅ | `PENALTY_STRATEGY` dict with mild/moderate/severe tiers |
| Strategic info degradation | ✅ | `PHRASES_TO_REMOVE_BY_ZONE` dict per penalty zone |
| Main entry point | ✅ | `apply_zone_effects()` orchestrates all Phase 6 effects |

### Probabilistic Injection Thresholds

| Penalty Intensity | Probability | Implementation |
|-------------------|-------------|----------------|
| 0-25% | 10% | More conservative than PRD spec (25%) - reduces noise |
| 25-50% | 50% | ✅ Matches PRD |
| 50-75% | 75% | ✅ Matches PRD |
| 75%+ | 100% (cliff) | ✅ Matches PRD |

### Key Functions Added

| Function | Location | Purpose |
|----------|----------|---------|
| `apply_zone_effects()` | player_psychology.py:2073-2112 | Main entry point for zone-based prompt modifications |
| `_inject_zone_thoughts()` | player_psychology.py:2181-2226 | Probabilistic thought injection |
| `_get_zone_thoughts()` | player_psychology.py:2133-2179 | Zone-specific thought selection with energy variants |
| `_add_penalty_strategy()` | player_psychology.py:2228-2271 | Bad advice injection by tier |
| `_degrade_strategic_info_by_zone()` | player_psychology.py:2273-2313 | Strategic phrase removal |
| `_should_inject_thoughts()` | player_psychology.py:1273-1295 | Probabilistic threshold logic |

### Thought Collections

| Collection | Location | Contents |
|------------|----------|----------|
| `INTRUSIVE_THOUGHTS` | player_psychology.py:1044-1080 | Thoughts by pressure source (for Tilted zone) |
| `SHAKEN_THOUGHTS` | player_psychology.py:1083-1096 | Risk-identity split thoughts |
| `OVERHEATED_THOUGHTS` | player_psychology.py:1099-1105 | Manic aggression |
| `OVERCONFIDENT_THOUGHTS` | player_psychology.py:1108-1114 | Dismissive of opponents |
| `DETACHED_THOUGHTS` | player_psychology.py:1117-1122 | Overly passive |
| `ENERGY_THOUGHT_VARIANTS` | player_psychology.py:1125-1183 | Low/high energy flavors |
| `PENALTY_STRATEGY` | player_psychology.py:1186-1217 | Bad advice by zone and tier |
| `PHRASES_TO_REMOVE_BY_ZONE` | player_psychology.py:1237-1270 | Strategic phrases to remove |

### Test Coverage

- `tests/test_psychology_v2.py` lines 1864-2136
- Tests for: thought selection per zone, risk-identity split, energy variants, penalty strategy tiers, strategic degradation

---

## 28. Phase 7 Implementation Status

**Status**: ✅ COMPLETE (2026-02-05)

### What Was Implemented

| Deliverable | Status | Notes |
|-------------|--------|-------|
| `ZoneStrategy` dataclass | ✅ | Frozen dataclass with name, weight, template_key, requires, min_strength |
| `ZoneContext` dataclass | ✅ | Holds opponent_stats, opponent_displayed_emotion, equity_vs_ranges, etc. |
| `ZONE_STRATEGIES` dict | ✅ | Strategy pools for poker_face, guarded, commanding, aggro |
| `select_zone_strategy()` | ✅ | Filters by min_strength and requires, weighted random selection |
| `build_zone_guidance()` | ✅ | Renders template with context, adds secondary zone hint |
| YAML templates | ✅ | `decision.yaml` lines 89-152 with all zone templates |
| Controller integration | ✅ | `_build_zone_context()` and zone guidance in `_build_decision_prompt()` |
| Gradual activation | ✅ | 10% minimum for primary zone, 25% for strategy min_strength |
| Zone blending | ✅ | Secondary zone hint appended to header |

### Zone Strategy Configuration

| Zone | Strategy | Weight | Requires |
|------|----------|--------|----------|
| poker_face | gto_focus | 0.4 | - |
| poker_face | balance_reminder | 0.3 | - |
| poker_face | equity_analysis | 0.3 | equity_vs_ranges |
| guarded | trap_opportunity | 0.4 | - |
| guarded | patience_cue | 0.3 | - |
| guarded | pot_control | 0.3 | - |
| commanding | value_extraction | 0.4 | - |
| commanding | pressure_point | 0.3 | opponent_stats |
| commanding | initiative | 0.3 | - |
| aggro | heighten_awareness | 0.3 | - |
| aggro | analyze_behavior | 0.4 | opponent_analysis |
| aggro | target_weak | 0.3 | weak_player_note |

### Key Functions Added

| Function | Location | Purpose |
|----------|----------|---------|
| `select_zone_strategy()` | player_psychology.py:529-566 | Strategy selection with filtering |
| `build_zone_guidance()` | player_psychology.py:569-630 | Template rendering with blending |
| `_build_zone_context()` | controllers.py:1706-1762 | Context population from game state |

### YAML Templates Added

| Template Key | Zone | Description |
|--------------|------|-------------|
| `zone_poker_face_gto` | Poker Face | GTO-focused balanced play |
| `zone_poker_face_balance` | Poker Face | Balance reminder |
| `zone_poker_face_equity` | Poker Face | Equity analysis (requires context) |
| `zone_guarded_trap` | Guarded | Trap opportunity awareness |
| `zone_guarded_patience` | Guarded | Patience cue |
| `zone_guarded_control` | Guarded | Pot control guidance |
| `zone_commanding_value` | Commanding | Value extraction |
| `zone_commanding_pressure` | Commanding | Pressure point (requires context) |
| `zone_commanding_initiative` | Commanding | Initiative maintenance |
| `zone_aggro_awareness` | Aggro | Weakness awareness |
| `zone_aggro_analyze` | Aggro | Behavior analysis (requires context) |
| `zone_aggro_target` | Aggro | Target weak player (requires context) |

### Integration Points

| Location | Integration |
|----------|-------------|
| `controllers.py:1281-1300` | Zone guidance generation in `_build_decision_prompt()` |
| `controllers.py:1706-1762` | `_build_zone_context()` populates context from game state |
| `prompt_manager.py:481-483` | Zone guidance injection into rendered prompt |
| `prompt_config.py:59` | `zone_benefits` toggle |

### Test Coverage

- `tests/test_prompt_config.py` lines 332-376 - zone toggle tests
- Integration tested via controller code paths

---

## 29. Phase 8 Implementation Status

**Status**: ✅ COMPLETE (2026-02-05)

### What Was Implemented

| Deliverable | Status | Notes |
|-------------|--------|-------|
| Energy manifestation labels | ✅ | Per-zone labels (Measured, Running hot, Alert, Dominant, etc.) |
| Energy-variant zone templates | ✅ | 24 templates (_low/_high variants for all 12 zone strategies) |
| Energy label in zone headers | ✅ | `[POKER FACE MODE \| Running hot]` format |
| Penalty bad advice energy flavor | ✅ | High energy: exclamation marks, Low energy: withdrawn flavor |
| **Timid penalty zone** | ✅ | New left edge zone (conf < 0.10) mirroring Overconfident |

### Energy Manifestation Labels

Each zone has distinct labels for low/high energy:

| Zone | Low Energy | High Energy |
|------|------------|-------------|
| Poker Face | Measured | Running hot |
| Guarded | Measured | Alert |
| Commanding | Composed | Dominant |
| Aggro | Watchful | Hunting |

### Timid Zone Addition

New penalty zone added as the left edge (mirror of Overconfident):

- **Threshold**: Confidence < 0.10
- **Psychology**: Scared money, over-respects opponents, can't pull trigger
- **Bad advice**: "They probably have you beat", "Fold - they always have it"
- **Removes phrases**: "you have the best hand", "value bet", "extract value"
- **Energy variants**: Low ("Just fold. It's safer.") / High ("They have it! It's a trap!")

### Files Modified

| File | Change |
|------|--------|
| `poker/player_psychology.py` | Added ENERGY_MANIFESTATION_LABELS, PENALTY_TIMID_THRESHOLD, TIMID_THOUGHTS, updated _detect_penalty_zones(), _get_zone_thoughts(), _add_penalty_strategy(), PENALTY_STRATEGY, PHRASES_TO_REMOVE_BY_ZONE, ENERGY_THOUGHT_VARIANTS |
| `poker/prompts/decision.yaml` | Added 24 energy-variant templates (zone_*_low, zone_*_high) |
| `docs/technical/PSYCHOLOGY_ZONES_MODEL.md` | Updated zone diagram, penalty zones table, Timid zone documentation |

### Test Coverage

- All 176 psychology tests pass
- Demo script: `experiments/phase8_tone_framing_demo.py`

---

## 30. Phase 9 Implementation Status

**Status**: ✅ COMPLETE (2026-02-05)

### What Was Implemented

| Deliverable | Status | Notes |
|-------------|--------|-------|
| `zone_benefits` toggle | ✅ | `prompt_config.py:59` - defaults to True |
| YAML presets updated | ✅ | `config/game_modes.yaml` with tilt_effects configuration |
| Pro mode configuration | ✅ | `tilt_effects: false` for harder AIs |
| Factory method fallbacks | ✅ | `PromptConfig.pro()` etc. for non-YAML environments |
| Backward compatibility | ✅ | Old saved games without zone toggles default to True |

### Game Mode Psychology Configuration

| Mode | zone_benefits | tilt_effects | Description |
|------|--------------|--------------|-------------|
| Casual | ✅ (default) | ✅ (default) | Full psychology - AI players tilt and get zone bonuses |
| Standard | ✅ (default) | ✅ (default) | Full psychology with GTO awareness |
| Pro | ✅ (default) | ❌ (explicit) | Harder AIs - no tilt effects, still get sweet spot bonuses |
| Competitive | ✅ (default) | ✅ (default) | Full psychology with GTO guidance |

### Design Decision

The PRD originally specified separate `zone_sweet_spots` and `zone_penalties` toggles. Implementation simplified this to:
- `zone_benefits` - Controls sweet spot guidance (Phase 7)
- `tilt_effects` - Controls penalty zone effects (Phase 6)

This matches the existing `tilt_effects` toggle while adding `zone_benefits` for Phase 7 features. The separation allows Pro mode to disable penalty effects while keeping sweet spot bonuses.

### Files Modified

| File | Change |
|------|--------|
| `poker/prompt_config.py` | Added `zone_benefits: bool = True` toggle |
| `config/game_modes.yaml` | Pro mode sets `tilt_effects: false` |

### Test Coverage

- `tests/test_prompt_config.py` lines 332-376
- Tests for: default toggles enabled, casual/standard/pro/competitive modes, backward compatibility
