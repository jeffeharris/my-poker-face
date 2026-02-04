# Poker AI Psychology System — Product Requirements Document (v2)

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
2. **State (Behavioral Axes)** — how they currently feel (dynamic)
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

### 4.2 Composure (0–1)

Ability to regulate emotion under stress.

**Impacts:**
* Tilt resistance
* Decision consistency
* Decision noise/variance

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

---

## 5. Emotional Space

Confidence and Composure form a 2D emotional space with named regions.

```
                        COMPOSURE
                 Low       Mid       High
              ┌─────────┬─────────┬─────────┐
         High │OVERHEATED│         │COMMANDING
              │  manic,  │         │ dominant,│
              │ volatile │         │ in control
              ├─────────┤         ├─────────┤
CONFIDENCE Mid│         │ POKER   │         │
              │         │  FACE   │         │
              ├─────────┤         ├─────────┤
         Low  │ SHAKEN  │         │ GUARDED │
              │desperate,│         │cautious, │
              │ spiraling│         │ defensive│
              └─────────┴─────────┴─────────┘
```

### 5.1 Poker Face Zone

Poker Face is **not a default emotion**. It is a centered, regulated zone in emotional space.

**Key properties:**
* Universal location (center of space)
* Size varies per player (some large, some small/fragile)
* Some players' anchors fall outside the poker face zone
* There is no universal neutral state

**When inside the zone:**
* Emotional leakage is suppressed
* Behavior appears calm and unreadable

**When outside the zone:**
* Player is in one of the four emotional quadrants
* Emotion is visible based on expressiveness

### 5.2 Emotional Region Behaviors

| Region | Looseness | Aggression | Character |
|--------|-----------|------------|-----------|
| Poker Face | baseline | baseline | Controlled, unreadable |
| Commanding | — | + | Pressing advantage, confident |
| Overheated | + | ++ | Forcing action, volatile |
| Guarded | − | — | Waiting, defensive |
| Shaken | ±* | ±* | Erratic, desperate |

*Shaken behavior depends on Risk Identity anchor (passive collapse vs manic desperation)

---

## 6. Play Style Axes

Two axes that define strategic tendencies. Baseline from anchors, modified by emotional state.

### 6.1 Aggression (0–1)

Preference for betting/raising vs checking/calling.

* Controls action **frequency**, not sizing
* Modified by emotional state

### 6.2 Looseness (0–1)

Width of preflop comfort ranges by position.

* Controls **which hands** to play
* Modified by emotional state

**Ranges are never directly visible** and must be inferred by the human player through observed actions.

---

## 7. Personality Anchors (Static Per Session)

Anchors define identity and do not change during a session (tournament). They act as gravity, pulling state back toward baseline.

### 7.1 Baseline Style

* Default aggression
* Default looseness
* Positional comfort expectations

### 7.2 Ego

How much "being wrong" events move confidence.

* High ego: getting bluffed, bad reads destroy confidence
* Low ego: mistakes don't shake self-belief

**Affects:** Confidence sensitivity to outplay events

### 7.3 Poise

How much "bad outcome" events move composure.

* High poise: bad beats, coolers don't tilt
* Low poise: variance causes emotional disruption

**Affects:** Composure sensitivity to luck-based events

### 7.4 Expressiveness

How much internal emotional state leaks through output.

**Controls:**
* Avatar emotion filtering (true emotion vs poker face display)
* Table talk content filtering (reveals feelings vs neutral)

**Does NOT control:**
* How much they talk (that's Energy)
* Actual betting patterns (behavioral, not presentational)

| Expressiveness | Avatar | Talk Content |
|----------------|--------|--------------|
| High | Shows true emotion | "UGH another bad beat!", "I KNEW it" |
| Low | Poker face unless extreme | "Raise", "Call", neutral chatter |

### 7.5 Risk Identity

Preference for variance vs safety.

**Affects:**
* Stack-off thresholds
* Desperation behavior when short
* Shaken-state direction (passive collapse vs manic gamble)

### 7.6 Adaptation Bias

Willingness to adjust strategy based on opponent observations.

**Controls:**
* How quickly opponent tendencies activate adjustments
* How strongly observations bias decisions
* How quickly adjustments decay

### 7.7 Energy Anchor

Baseline energy level (high vs low energy person).

* High anchor: naturally animated, engaged
* Low anchor: naturally reserved, deliberate

### 7.8 Recovery Rate

How fast all axes return toward baseline after events.

* Fast recovery: emotional swings are brief
* Slow recovery: states persist longer

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

### 8.4 What Expression Does NOT Hide

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

Game events modify Confidence, Composure, and Energy:

| Event Type | Primary Target | Sensitivity Anchor |
|------------|----------------|-------------------|
| Being outplayed | Confidence | Ego |
| Bad outcomes | Composure | Poise |
| Action/engagement | Energy | Energy Anchor |

### 12.2 Tilt Distribution Targets (FR)

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

### 12.3 Recovery

* All axes decay toward anchor baselines
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

1. If state lies inside poker face zone → **Poker Face**
2. Otherwise → dominant emotion based on quadrant and intensity

### 14.2 Available Labels

Primary (quadrant-based):
* Poker Face, Commanding, Overheated, Guarded, Shaken

Extended (intensity/nuance):
* Confident, Smug, Nervous, Frustrated, Angry, Elated, Thinking

Label selection is deterministic from position in emotional space.

---

## 15. Summary

### Identity Layer (Static)
* Baseline Style
* Ego (confidence sensitivity)
* Poise (composure sensitivity)
* Expressiveness (emotional transparency)
* Risk Identity (variance tolerance)
* Adaptation Bias (learning rate)
* Energy Anchor (baseline energy)
* Recovery Rate

### State Layer (Dynamic)
* Confidence (0–1)
* Composure (0–1)
* Energy (0–1)
* Aggression (modified by emotional state)
* Looseness (modified by emotional state)

### Expression Layer (Filtered Output)
* Avatar emotion
* Table talk content
* Tempo

### Core Invariants
* Identity is stable
* Emotion is dynamic
* Expression is filtered and amplified
* Decisions are constrained but non-deterministic
* Poker face is a zone, not a default

The result is AI poker that feels alive, readable, and strategically coherent.
