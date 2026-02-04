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
