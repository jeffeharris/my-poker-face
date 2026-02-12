---
purpose: Decision matrix and emotional window mechanic for bounded option generation
type: design
created: 2026-02-12
last_updated: 2026-02-12
---

# Bounded Options Decision Framework

## Philosophy

LLMs can't play poker, but they can choose from a menu. The rule engine writes
a "choose your adventure" page for each decision point. The LLM picks an option
based on personality and narrative framing. Every option on the page should be
defensible — the kind of choice a real player might make.

Non-determinism is the goal. Absurdity is the enemy.

## Architecture Layers

Options are built through a layered system where each layer modifies the result
of the previous one:

```
Case Matrix (cost × strength)        → base options for this situation
  + Position modifier                 → adjust CHECK labeling/availability
    + Play Style (OptionProfile)      → shift band thresholds, add/remove options
      + Emotional Window Shift        → slide option window along passive↔aggressive
        + Math Blocking (always last) → hard guardrails, never overridden
```

Math blocking is the final gate. No emotional state or play style can override it.

## Decision Matrix

### Axis 1: Cost to Act

| State | Meaning |
|-------|---------|
| **Free** | cost_to_call = 0, CHECK available |
| **Facing bet** | cost_to_call > 0, must pay to continue |

### Axis 2: Hand Strength Bands

When free to act, bands are absolute equity:

| Band | Equity | Meaning |
|------|--------|---------|
| Monster | 90%+ | Nuts or near-nuts |
| Strong | 65-90% | Clear value hand |
| Decent | 40-65% | Playable, not strong |
| Weak | <40% | Marginal or trash |

When facing a bet, bands are relative to required equity:

| Band | Equity vs Required | Meaning |
|------|-------------------|---------|
| Monster | 90%+ absolute | Nuts or near-nuts |
| Crushing | >1.7× required | Way ahead |
| Profitable | 1.0-1.7× required | Above pot odds |
| Marginal | 0.85-1.0× required | Borderline |
| Weak | <0.85× required | Below pot odds |
| Dead | <5% absolute | Drawing dead |

### Free to Act Cases

#### F1: Monster (90%+)

The player has the nuts or near-nuts. Must extract value.

| Option | EV | Default | IP Modifier | OOP Modifier |
|--------|----|---------|-------------|--------------|
| RAISE (value sizes) | +EV | Always included | Yes | Yes |
| ALL-IN | +EV | If short stacked | Yes | Yes |
| CHECK (trap) | marginal | — | Add (trapping is viable) | Omit (risks free card) |

**Key rule:** Out of position, CHECK should not appear for monsters.
In position, CHECK is a legitimate trap since opponent showed weakness.

#### F2: Strong (65-90%)

The player has a strong hand. Should usually bet for value.

| Option | EV | Default | IP Modifier | OOP Modifier |
|--------|----|---------|-------------|--------------|
| RAISE (value sizes) | +EV | Always included | Yes | Yes |
| CHECK | neutral | Included | neutral (pot control ok) | marginal (missing value) |

**Key rule:** This is the case that's broken today. The LLM defaults to CHECK
because it's safe. OOP, CHECK should be labeled negatively or removed for
aggressive profiles.

#### F3: Decent (40-65%)

Playable hand, not strong enough for confident value betting.

| Option | EV | Default |
|--------|----|---------|
| CHECK | neutral | Always included |
| RAISE (small probe) | neutral | Included |
| RAISE (bluff) | -EV | LAG profiles only |

#### F4: Weak (<40%)

Weak hand. Take the free card.

| Option | EV | Default |
|--------|----|---------|
| CHECK | neutral | Always included |
| RAISE (bluff) | -EV | LAG profiles only (bluff_frequency > 0) |

### Facing a Bet Cases

#### B1: Monster (90%+)

| Option | EV | Blocking |
|--------|----|----------|
| RAISE (value) | +EV | FOLD blocked |
| ALL-IN | +EV | FOLD blocked |
| CALL | +EV | Available (slowplay/trap) |

#### B2: Crushing (>1.7× required)

| Option | EV | Blocking |
|--------|----|----------|
| CALL | +EV | FOLD blocked (equity >> required) |
| RAISE (value) | +EV | — |

#### B3: Profitable (1.0-1.7× required)

| Option | EV | Notes |
|--------|----|-------|
| CALL | marginal to +EV | Profitable but not crushing |
| RAISE | depends on equity | If equity > raise_plus_ev threshold |
| FOLD | -EV | Available but labeled negatively |

#### B4: Marginal (0.85-1.0× required)

| Option | EV | Notes |
|--------|----|-------|
| CALL | marginal | "Close — your call" |
| FOLD | neutral | Neither good nor bad |

This is the personality-expression zone. TAG folds, calling station calls.

#### B5: Weak (<0.85× required)

| Option | EV | Notes |
|--------|----|-------|
| FOLD | +EV | Saves money |
| CALL | -EV | Available but unfavorable |
| RAISE (bluff) | -EV | LAG profiles only |

#### B6: Dead (<5%)

| Option | EV | Blocking |
|--------|----|----------|
| FOLD | +EV | CALL blocked |

### Stack Depth Overlay

Stack depth collapses the option space for short stacks:

| Depth | BB Range | Effect |
|-------|----------|--------|
| Deep | >30 BB | Full sizing range (small/medium/large raises) |
| Medium | 10-30 BB | 1-2 raise sizes. ALL-IN available for strong hands. |
| Short | <10 BB | Push/fold territory. F1-F2 → ALL-IN. B1-B3 → ALL-IN or FOLD. |

### Play Style Overlay

Play style (from `OptionProfile`) shifts thresholds and changes which options appear:

| Style | Band Shifts | Option Changes |
|-------|-------------|----------------|
| TAG | Tighter fold threshold, lower raise bar | F2: always RAISE, CHECK only as trap IP. B3: prefer RAISE over CALL. |
| LAG | Looser fold threshold, lowest raise bar | F3-F4: add bluff RAISE. B5: add bluff RAISE. More/bigger sizing options. |
| Tight passive | Tighter everything | F2: CHECK more acceptable. B3-B4: lean CALL over RAISE. |
| Loose passive | Looser call thresholds, wider marginal zone | B4-B5: CALL more available. Wider marginal zone. |

Play style is the character's baseline personality. It determines the *default*
option window for each case.

## Emotional Window Shift

### Concept

Emotional states slide the option window along the passive↔aggressive spectrum.
The full spectrum of possible options for any decision:

```
← passive                                              aggressive →
FOLD — CHECK — CALL — RAISE(small) — RAISE(med) — RAISE(large) — ALL-IN
```

The case matrix + play style produce a window of options from this spectrum.
Emotional states shift that window.

### Probabilistic Application

Emotional impairment is not deterministic. A tilted player doesn't *always*
lose sight of the fold button — sometimes they take a breath and see clearly.

Each decision rolls against the severity to determine whether the emotional
window shift applies:

| Severity | Impaired (shifted window) | Lucid (normal options) |
|----------|--------------------------|----------------------|
| None | 0% | 100% |
| Mild | 70% | 30% |
| Moderate | 85% | 15% |
| Extreme | 95% | 5% |

When the roll comes up "lucid," the player gets their normal case matrix
options with no emotional modification. This creates natural variability —
even an extremely tilted player occasionally makes the right call.

### Severity Levels

When the emotional shift *does* apply:

| Severity | Effect |
|----------|--------|
| None | No modification. Base options from case matrix. |
| Mild | **Add** one option on the extreme end (expand the window). |
| Moderate | **Add** one option on the extreme end + narrative framing nudge. |
| Extreme | **Add** one option on the extreme end AND **remove** one from the opposite end (shift the window). |

### Emotional State Directions

| State | Direction | Mild | Moderate | Extreme |
|-------|-----------|------|----------|---------|
| **Tilted** | → aggressive | Add larger raise / ALL-IN | Add aggressive + narrative nudge | Add aggressive + remove FOLD or CHECK |
| **Overconfident** | → aggressive | Add larger value bet | Add overbet + narrative nudge | Add overbet + remove FOLD |
| **Shaken** | ← passive | Add FOLD or CHECK where normally absent | Add passive + narrative nudge | Add passive + remove largest RAISE |
| **Dissociated** | ← passive | Add CHECK | Strip rationale detail | Add passive + remove RAISE options |

### Examples

**Case F2 (strong hand, free to act), normal options:**
```
1. CHECK       [neutral]
2. RAISE 8BB   [+EV]
3. RAISE 15BB  [+EV]
```

**Same case, mild tilt — add aggressive option:**
```
1. CHECK       [neutral]
2. RAISE 8BB   [+EV]
3. RAISE 15BB  [+EV]
4. RAISE 30BB  [+EV]        ← added: overbet
```

**Same case, extreme tilt — add aggressive, remove passive:**
```
1. RAISE 8BB   [+EV]
2. RAISE 15BB  [+EV]
3. RAISE 30BB  [+EV]        ← added: overbet
                              ← removed: CHECK
```

**Case B4 (marginal, facing bet), normal options:**
```
1. CALL        [marginal]
2. FOLD        [neutral]
```

**Same case, mild shaken — add passive option:**
```
1. FOLD        [neutral]     ← narrative: "Save your chips."
2. CALL        [marginal]
```
(No new option to add since FOLD is already the most passive.
Instead, narrative framing emphasizes folding.)

**Same case, extreme shaken — remove aggressive:**
```
1. FOLD        [neutral]
```
(CALL removed — the player can't bring themselves to put chips in.)

### Interaction with Math Blocking

Math blocking always wins. The emotional window shift is applied *before*
math blocking, so:

- Extreme tilt removes FOLD from the window → but math blocking re-removes
  FOLD for monsters anyway → no conflict
- Extreme tilt removes FOLD for a marginal hand → FOLD was correct → the player
  is genuinely impaired, this is intentional
- Extreme shaken removes RAISE for a strong hand → the player misses value →
  genuinely impaired
- Extreme shaken removes CALL → but math blocking prevents removing CALL when
  it's the only non-fold option → safety net

**Rule:** Math blocking cannot be overridden by emotional state. If blocking
says an option must exist (e.g., CALL when fold is blocked and no raise), it
stays regardless of emotional window.

### Narrative Framing

In addition to shifting the option window, emotional states modify the
rationale text on remaining options. This provides a secondary nudge:

| State | Narrative Effect |
|-------|-----------------|
| **Tilted** | Aggressive options framed as revenge/justice. Passive options framed as weakness. |
| **Overconfident** | Aggressive options framed as inevitability. Fold framed as inconceivable. |
| **Shaken** | Passive options framed as safety. Aggressive options framed with doubt. |
| **Dissociated** | Rationale stripped to bare minimum. Less information to reason with. |

Example rationale for the same RAISE option:

| State | Rationale |
|-------|-----------|
| Composed | "Value bet with strong hand" |
| Tilted | "They keep pushing you around. Push back." |
| Shaken | "Big bet... are you sure about this?" |
| Overconfident | "You're running hot. Press the advantage." |
| Dissociated | "Raise." |

## Summary

The bounded options system is a choose-your-own-adventure book where:

1. **The case matrix** writes the base page (which options exist)
2. **Position** adjusts CHECK availability for strong hands
3. **Play style** shifts the thresholds (what counts as "strong")
4. **Emotional state** slides the option window and colors the narrative
5. **Math blocking** is the final safety net (never overridden)

The LLM never needs to understand poker. It just picks from a menu where
every option is defensible — or, when emotionally impaired, picks from a
menu that's been deliberately skewed.

## Key Files

| File | Role |
|------|------|
| `poker/bounded_options.py` | Option generation, blocking logic, profiles |
| `poker/hybrid_ai_controller.py` | Lean prompt assembly, hand plan, profile selection |
| `poker/prompt_config.py` | Feature flags (lean_bounded, style_aware_options, hand_plan) |
| `config/game_modes.yaml` | Game mode presets |
