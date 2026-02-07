# Psychology System: Zones Model v3

This document describes the zone-based mental model for the poker AI psychology system.

**Implementation Status:**
- ✅ Zone detection (sweet spots + penalties)
- ✅ Zone effects (strategy guidance, intrusive thoughts)
- ✅ Zone gravity (stickiness) - implemented 2026-02-06

## Core Concept

The psychology system uses a 2D space defined by **Confidence** (X-axis) and **Composure** (Y-axis). Players move through this space based on game events, with recovery pulling them back toward their personality-defined anchor.

**Key insight**: The space contains two types of zones with fundamentally different geometries:
- **Sweet spots**: Circular zones with centers - places you *aim for*
- **Penalty zones**: Edge/boundary regions - places you *fall into* when you go too far

## Zone Geometry

### Sweet Spots (Circular, Center-Based)

Sweet spots are circular zones defined by:
- A **center point** (confidence, composure)
- A **radius** determining zone size
- **Smooth falloff** from center (cosine function)

```python
# Distance from zone center
distance = sqrt((conf - center_conf)² + (comp - center_comp)²)

# Zone strength (smooth cosine falloff)
if distance < radius:
    strength = 0.5 + 0.5 * cos(π * distance / radius)
else:
    strength = 0.0
```

This gives:
- 100% strength at center
- ~85% at 25% of radius
- ~50% at 50% of radius
- ~15% at 75% of radius
- 0% at edge (smooth transition)

### Penalty Zones (Edge-Based)

Penalty zones are boundary regions defined by:
- A **threshold** (edge of safe territory)
- **Gradient inward** from the boundary
- Strength based on *distance past the threshold*

```python
# Example: Tilted zone (composure < 0.35)
if composure < threshold:
    distance_past = threshold - composure
    strength = 0.5 + 0.5 * cos(π * (1 - distance_past / threshold))
else:
    strength = 0.0
```

## The 2D Space

See `emotional_zones_circular.svg` for the visual diagram.

```
                    CONFIDENCE →
        0.0    0.1    0.35   0.6    0.8    0.9   1.0
       ┌──────┬──────┬──────┬──────┬──────┬──────┐
  1.0  │      │      │      │      │      │      │
       │ ░░░░ │      │      │      │      │      │
  0.8  │ ░░░░ │  ◉   │  ◉   │      │  ◉   │ ░░░░ │
C      │DETACH│GUARD │ P.F. │      │COMMA-│OVER- │
O 0.65 │ ░░░░ │      │      │      │NDING │CONF  │
M      │      │      │      │      │      │ ░░░░ │
P 0.4  │ ░░░░ │      │      │  ◉   │      │ ░░░░ │
O      │TIMID │▓▓▓▓▓▓│▓▓▓▓▓▓│AGGRO │▓▓▓▓▓▓│▓▓▓▓▓▓│
S 0.35 │ ░░░░ │      │TILTED│      │OVER- │      │
U      │▓▓▓▓▓▓│▓▓▓▓▓▓│▓▓▓▓▓▓│▓▓▓▓▓▓│HEATED│▓▓▓▓▓▓│
R 0.0  │SHAKEN│▓▓▓▓▓▓│▓▓▓▓▓▓│▓▓▓▓▓▓│▓▓▓▓▓▓│▓▓▓▓▓▓│
E ↑    └──────┴──────┴──────┴──────┴──────┴──────┘
       0.1

       ◉ = sweet spot center    ░ = penalty edge region
       ▓ = penalty zone (low composure)

       TIMID = left edge (conf < 0.10) - mirrors OVERCONFIDENT on right edge
```

## Sweet Spots

| Zone | Center (conf, comp) | Radius | Playstyle |
|------|---------------------|--------|-----------|
| Guarded | (0.28, 0.72) | 0.15 | Patient, trap-setting |
| Poker Face | (0.52, 0.72) | 0.16 | GTO, balanced |
| Commanding | (0.78, 0.78) | 0.14 | Pressure, value extraction |
| Aggro | (0.68, 0.48) | 0.12 | Exploitative, aggressive |

### Guarded Zone
- **Center**: Confidence 0.28, Composure 0.72
- **Radius**: 0.15
- **Playstyle**: Patient, trap-setting, risk-averse
- **Information shown**:
  - Trap opportunities
  - "Wait for a better spot" guidance
  - Opponent aggression patterns to exploit passively
- **Example characters**: Bob Ross, cautious players

### Poker Face Zone
- **Center**: Confidence 0.52, Composure 0.72
- **Radius**: 0.16
- **Playstyle**: GTO, balanced, unreadable
- **Information shown**:
  - Pot odds and equity calculations
  - GTO ranges and frequencies
  - Balanced decision guidance
- **Example characters**: Batman, Spock, Sherlock Holmes

### Commanding Zone
- **Center**: Confidence 0.78, Composure 0.78
- **Radius**: 0.14
- **Playstyle**: Pressure, value extraction, dominant
- **Information shown**:
  - Value extraction opportunities
  - Pressure points to attack
  - Opponent weakness indicators
- **Example characters**: Napoleon, Churchill

### Aggro Zone
- **Center**: Confidence 0.68, Composure 0.48
- **Radius**: 0.12
- **Playstyle**: Exploitative, aggressive, high-pressure
- **Information shown**:
  - Opponent fold frequencies
  - Exploitation opportunities
  - Opponent tilt/composure levels
  - "They'll fold to aggression" cues
- **Example characters**: Gordon Ramsay, Phil Hellmuth

## Penalty Zones

Penalty zones are edge-based regions where decision-making degrades.

| Zone | Boundary | Effect |
|------|----------|--------|
| Tilted | Composure < 0.35 | Emotional disaster |
| Overconfident | Confidence > 0.90 | Ignores warnings, hero calls |
| Timid | Confidence < 0.10 | Scared money, over-folds |
| Shaken | Low conf + low comp corner | Desperate, erratic |
| Overheated | High conf + low comp corner | Reckless aggression |
| Detached | Low conf + high comp corner | Too passive |

### Tilted
- **Boundary**: Bottom edge - composure < 0.35
- **Effect**: Emotional disaster, severe decision degradation
- **Prompt modification**: Heavy intrusive thoughts, strategic info removed, emotional override

### Shaken
- **Boundary**: Lower-left corner - low confidence AND low composure
- **Effect**: Desperate, erratic play
- **Prompt modification**: Intrusive thoughts, poor strategic guidance, panic-driven suggestions

### Overheated
- **Boundary**: Lower-right corner - high confidence AND low composure
- **Effect**: Reckless, manic aggression without judgment
- **Prompt modification**: Overaggressive suggestions, ignores risk, "attack everything" mentality

### Overconfident
- **Boundary**: Right edge - confidence > 0.90
- **Effect**: Ignores warnings, hero calls, stubborn
- **Prompt modification**: Dismisses opponent strength, "you can't be wrong" bias

### Timid
- **Boundary**: Left edge - confidence < 0.10 (mirrors Overconfident)
- **Effect**: Scared money, over-respects opponents, can't pull the trigger
- **Prompt modification**: Assumes opponents always have it, removes value-betting encouragement

### Detached
- **Boundary**: Upper-left corner - low confidence AND high composure
- **Effect**: Too passive, misses reads, robotic
- **Prompt modification**: Misses exploitation opportunities, overly conservative

## Zone Overlap and Blending

### Two-Layer Model

Sweet spots and penalty zones are calculated as **separate layers**:
- **Layer 1**: Sweet spot blend (which beneficial zone(s) apply)
- **Layer 2**: Penalty zone blend (which penalty zone(s) apply)

Both layers can apply simultaneously. A player can be:
- 60% Commanding + 40% Poker Face (sweet spot blend)
- 30% Overheated (penalty blend)

### Weighted Blend (Sweet Spots)

When within range of multiple sweet spots:

1. Calculate distance to each zone center
2. Calculate strength for each zone (smooth falloff)
3. Normalize weights so they sum to 1.0
4. Blend zone effects proportionally

```python
# Example: Player at (0.65, 0.75)
poker_face_strength = calculate_strength(0.65, 0.75, poker_face_zone)  # 0.45
commanding_strength = calculate_strength(0.65, 0.75, commanding_zone)  # 0.55

# Normalized: 45% Poker Face, 55% Commanding
# Both zone effects apply, weighted
```

### Neutral Territory

When outside all sweet spots (all strengths = 0):
- Zone influences are removed from prompt
- Standard baseline play, no special bonuses
- Not a penalty - just "normal"

### Penalty Zone Interaction

Penalty zones stack based on proximity to edges:
- Can be in multiple penalty zones at once (e.g., Tilted + Shaken)
- Penalty effects are additive/cumulative
- Deeper into penalty territory = stronger effects

## Character Anchors

Each personality has an anchor point where recovery pulls them. Anchors should be placed in or near sweet spots, not in penalty zones.

| Character | Confidence | Composure | Home Zone |
|-----------|------------|-----------|-----------|
| Batman | 0.52 | 0.75 | Poker Face |
| Napoleon | 0.78 | 0.75 | Commanding |
| Gordon Ramsay | 0.70 | 0.48 | Aggro |
| Bob Ross | 0.30 | 0.78 | Guarded |
| Tourist (bad player) | 0.35 | 0.40 | Near Shaken |

## Deriving Anchor from Traits

Anchors are derived from multiple personality traits using a **translation layer** that maps the 0-1 designer-facing scale to a safe internal range.

### Translation Model

```python
# Safe anchor range (never in penalty zones)
ANCHOR_FLOOR = 0.35
ANCHOR_CEILING = 0.85

# Designer sets traits 0-1, internal anchor maps to safe range
anchor = ANCHOR_FLOOR + (ANCHOR_CEILING - ANCHOR_FLOOR) * raw_value
# raw_value 0.0 → anchor 0.35
# raw_value 0.5 → anchor 0.60
# raw_value 1.0 → anchor 0.85
```

This ensures:
- Designers tune traits intuitively (0 = min, 1 = max)
- Anchors never land in penalty zones
- Full range of the safe space is usable

### Confidence Anchor

Confidence baseline is a direct linear combination of three traits:

```python
baseline_confidence = (
    0.3                              # floor
    + baseline_aggression × 0.25     # aggressive players are confident
    + risk_identity × 0.20           # risk-seekers expect to win
    + ego × 0.25                     # high ego = high self-regard
)
# Range: 0.30 (all zero) to 1.00 (all one)
# Clamped to [0.15, 0.85] to stay outside penalty zones
```

| Trait Mix | Baseline Confidence |
|-----------|---------------------|
| All low (0.0) | 0.30 |
| All mid (0.5) | 0.65 |
| All high (1.0) | 0.85 (clamped from 1.00) |
| High ego only (1.0, others 0) | 0.55 |
| High aggression only (1.0, others 0) | 0.55 |

**Note:** Ego also affects *sensitivity* (how much being wrong hurts), not just baseline.

### Composure Anchor

Composure baseline uses poise as primary driver with expressiveness and risk modifiers:

```python
risk_mod = (risk_identity - 0.5) × 0.3   # centered: below 0.5 = nervous, above = comfortable
baseline_composure = (
    0.25                                   # floor
    + poise × 0.50                         # primary driver
    + (1 - expressiveness) × 0.15          # low expressiveness = internal control
    + risk_mod                             # risk-seekers comfortable with chaos
)
# Range: ~0.10 to ~1.05
# Clamped to [0.15, 0.85] to stay outside penalty zones
```

| Trait Mix | Baseline Composure |
|-----------|--------------------|
| Poise 0, Express 1, Risk 0 | 0.15 (clamped from 0.10) |
| All mid (0.5) | 0.58 |
| Poise 1, Express 0, Risk 1 | 0.85 (clamped from 1.05) |
| High poise only (1.0, others 0.5) | 0.83 |

**Note:** Poise also affects *sensitivity* (how much bad outcomes hurt), not just baseline.

### Example Characters

| Character | Aggression | Risk | Ego | Poise | Express | Conf Anchor | Comp Anchor | Home Zone |
|-----------|------------|------|-----|-------|---------|-------------|-------------|-----------|
| Batman | 0.4 | 0.3 | 0.4 | 0.8 | 0.2 | 0.56 | 0.71 | Poker Face |
| Gordon Ramsay | 0.8 | 0.7 | 0.8 | 0.35 | 0.9 | 0.84 | 0.50 | Aggro |
| Bob Ross | 0.2 | 0.2 | 0.3 | 0.8 | 0.6 | 0.47 | 0.62 | Guarded |
| Napoleon | 0.7 | 0.6 | 0.85 | 0.7 | 0.5 | 0.81 | 0.71 | Commanding |
| Tourist | 0.3 | 0.4 | 0.3 | 0.25 | 0.7 | 0.53 | 0.39 | Neutral (near Shaken) |

### Fallback: Independent Definition

If the complex blend doesn't produce good zone placement during testing, we can define `anchor_confidence` and `anchor_composure` as **independent traits** in the personality config, bypassing the derivation formulas entirely.

```python
# Fallback approach - direct definition (0-1 scale, auto-mapped to safe range)
anchors:
  anchor_confidence: 0.6  # maps to 0.35 + 0.50 × 0.6 = 0.65
  anchor_composure: 0.8   # maps to 0.35 + 0.50 × 0.8 = 0.75
  # ... other traits still derived normally
```

This gives full control over zone placement per character while still using the safe range mapping.

## Sensitivity (How Events Affect You)

Sensitivity determines how much events push you around the space.

```python
# Composure sensitivity: low poise = more affected by bad outcomes
composure_sensitivity = 0.30 + 0.70 * (1.0 - poise)

# Confidence sensitivity: high ego = more affected by being right/wrong
confidence_sensitivity = 0.30 + 0.70 * ego
```

| Poise | Composure Sensitivity | Ego | Confidence Sensitivity |
|-------|----------------------|-----|------------------------|
| 0.2 (volatile) | 0.86 | 0.2 (humble) | 0.44 |
| 0.5 (average) | 0.65 | 0.5 (average) | 0.65 |
| 0.8 (stable) | 0.44 | 0.8 (arrogant) | 0.86 |

## Movement Model

Player position in the 2D space changes through three forces that combine each tick (between hands):

```
movement = event_force + recovery_force + zone_gravity_force
new_position = current_position + movement
```

### 1. Event Force (Instant Pushes)

Game events apply instant pushes to the player's position. See "Event Impacts" section below for specific values.

```python
event_force = Vector(
    confidence_impact * confidence_sensitivity,
    composure_impact * composure_sensitivity
)
```

### 2. Recovery Force (Pulls Toward Anchor)

Recovery pulls the player back toward their personality-defined anchor point.

#### Base Recovery Rate

```python
# Derived from poise: low poise = faster base recovery
base_recovery = 0.12 + 0.25 * (1.0 - poise)
```

| Poise | Base Recovery | Interpretation |
|-------|---------------|----------------|
| 0.2 | 0.32 | Volatile, quick emotional shifts |
| 0.5 | 0.245 | Moderate recovery |
| 0.8 | 0.17 | Stable, slow to change |

#### Asymmetric Recovery Modifier

Recovery speed varies based on current state relative to anchor:

**Below anchor** (recovering from tilt/doubt):
```python
# Being tilted makes it harder to recover (vicious cycle)
modifier = 0.6 + 0.4 * current_value
# At 0.0: modifier = 0.6 (slow recovery)
# At 0.5: modifier = 0.8
# At anchor: modifier ≈ 0.9-1.0
```

**Above anchor** (riding a hot streak):
```python
# Further above = slower decay (ride the wave longer)
distance_above = current - anchor
max_distance = 1.0 - anchor
normalized = distance_above / max_distance  # 0 to 1

modifier = 0.8 - 0.4 * normalized
# Just above anchor: 0.8 (moderate decay)
# Halfway to max: 0.6
# Way above anchor: 0.4 (very slow decay)
```

#### Recovery Force Calculation

```python
direction = (anchor - current).normalized()
magnitude = base_recovery * asymmetric_modifier
recovery_force = direction * magnitude
```

**Effect**:
- When tilted (low composure), recovery is slower → tilt is sticky
- When riding high (above anchor), decay is slower → positive states last longer
- The further from anchor, the more asymmetry matters

### 3. Zone Gravity Force (Zone Stickiness)

Zones exert a weak gravitational pull that makes them "sticky" - harder to leave once you're in them.

#### Sweet Spot Gravity

Sweet spots pull toward their center:

```python
for zone in active_sweet_spots:
    direction = (zone.center - current).normalized()
    magnitude = GRAVITY_STRENGTH * zone.strength
    gravity_force += direction * magnitude
```

#### Penalty Zone Gravity

Penalty zones pull toward their extreme (the edge/corner):

```python
for zone in active_penalties:
    direction = zone.extreme_direction  # toward the edge
    magnitude = GRAVITY_STRENGTH * zone.strength
    gravity_force += direction * magnitude
```

| Penalty Zone | Gravity Direction |
|--------------|-------------------|
| Tilted | Down → (0, -1) toward composure = 0 |
| Shaken | Down-left → toward (0, 0) corner |
| Overheated | Down-right → toward (1, 0) corner |
| Overconfident | Right → (1, 0) toward confidence = 1 |
| Timid | Left → (-1, 0) toward confidence = 0 |
| Detached | Up-left → toward (0, 1) corner |

#### Gravity Strength

```python
GRAVITY_STRENGTH = 0.03  # Tunable: 0.02 - 0.05 range
```

Gravity is intentionally weak - a gentle influence, not dominant. It:
- Slightly counters recovery when in a zone
- Slightly counters events that would push you out of a zone
- Makes zones feel "stable" without trapping players

### Combined Movement Example

Player with anchor (0.5, 0.7), currently at (0.68, 0.48) - in Aggro zone at 60% strength.

**Recovery force:**
- Composure: 0.48 < anchor 0.7 → below anchor
  - modifier = 0.6 + 0.4 × 0.48 = 0.79
- Confidence: 0.68 > anchor 0.5 → above anchor
  - distance = 0.18, max = 0.5, normalized = 0.36
  - modifier = 0.8 - 0.4 × 0.36 = 0.66
- Direction: toward (0.5, 0.7)
- Magnitude: base_recovery × modifier (per axis)

**Zone gravity:**
- Aggro zone at 60% strength
- Direction: toward Aggro center (0.68, 0.48)
- Magnitude: 0.03 × 0.6 = 0.018

**Combined:** Recovery pulls toward anchor, gravity slightly counters (since anchor is outside Aggro zone).

### Constants to Tune

| Constant | Default | Range | Purpose |
|----------|---------|-------|---------|
| `GRAVITY_STRENGTH` | 0.03 | 0.02-0.05 | Zone stickiness |
| Below-anchor floor | 0.6 | 0.4-0.7 | How sticky deep tilt is |
| Above-anchor ceiling | 0.8 | 0.6-0.9 | Initial hot streak decay |
| Above-anchor floor | 0.4 | 0.3-0.5 | Peak hot streak decay |

## Event Impacts

Events push players around the 2D space. Impacts are scaled by sensitivity.

### Positive Events
| Event | Confidence | Composure |
|-------|------------|-----------|
| win | +0.08 | +0.05 |
| big_win | +0.15 | +0.10 |
| successful_bluff | +0.20 | +0.05 |
| double_up | +0.20 | +0.10 |

### Negative Events
| Event | Confidence | Composure |
|-------|------------|-----------|
| big_loss | -0.10 | -0.15 |
| bluff_called | -0.20 | -0.10 |
| bad_beat | -0.05 | -0.25 |
| got_sucked_out | -0.05 | -0.30 |

### Compounding

Multiple events can fire on the same hand (e.g., `bad_beat + big_loss + nemesis_loss`), creating large swings into penalty zones.

## Zone Detection (Pseudocode)

```python
def get_zone_strengths(confidence: float, composure: float) -> dict:
    """Calculate strength in each zone (two-layer model)."""

    sweet_spots = {}
    penalties = {}

    # === SWEET SPOTS (circular, center-based) ===
    zones = {
        'guarded': {'center': (0.28, 0.72), 'radius': 0.15},
        'poker_face': {'center': (0.52, 0.72), 'radius': 0.16},
        'commanding': {'center': (0.78, 0.78), 'radius': 0.14},
        'aggro': {'center': (0.68, 0.48), 'radius': 0.12},
    }

    for name, zone in zones.items():
        cx, cy = zone['center']
        r = zone['radius']
        distance = sqrt((confidence - cx)**2 + (composure - cy)**2)
        if distance < r:
            sweet_spots[name] = 0.5 + 0.5 * cos(pi * distance / r)

    # Normalize sweet spot weights
    total = sum(sweet_spots.values())
    if total > 0:
        sweet_spots = {k: v/total for k, v in sweet_spots.items()}

    # === PENALTY ZONES (edge-based) ===

    # Tilted: bottom edge (composure < 0.35)
    if composure < 0.35:
        penalties['tilted'] = (0.35 - composure) / 0.35

    # Overconfident: right edge (confidence > 0.90)
    if confidence > 0.90:
        penalties['overconfident'] = (confidence - 0.90) / 0.10

    # Timid: left edge (confidence < 0.10) - mirrors Overconfident
    if confidence < 0.10:
        penalties['timid'] = (0.10 - confidence) / 0.10

    # Shaken: lower-left corner
    if confidence < 0.35 and composure < 0.35:
        corner_dist = sqrt(confidence**2 + composure**2)
        max_dist = sqrt(0.35**2 + 0.35**2)
        penalties['shaken'] = 1.0 - (corner_dist / max_dist)

    # Overheated: lower-right corner
    if confidence > 0.65 and composure < 0.35:
        penalties['overheated'] = ((confidence - 0.65) / 0.35) * ((0.35 - composure) / 0.35)

    # Detached: upper-left corner
    if confidence < 0.35 and composure > 0.65:
        penalties['detached'] = ((0.35 - confidence) / 0.35) * ((composure - 0.65) / 0.35)

    return {'sweet_spots': sweet_spots, 'penalties': penalties}
```

## Zone Benefits System

This section defines what each zone actually **does** to modify the AI's prompt and decision-making. Zones shape what the AI knows and feels, not what it must do.

### Design Principles

1. **Information Access, Not Forced Actions**: Zones grant access to specific information or perspectives. The AI still makes its own decisions.

2. **Sweet Spots = Bonuses**: Being in a sweet spot unlocks information/framing that helps a specific playstyle.

3. **Penalty Zones = Degradation**: Penalty zones inject noise, remove helpful info, or add bad advice.

4. **Blending**: Multiple zones can apply simultaneously with weighted effects.

5. **Intensity Scaling**: Zone effects scale with zone strength (0-100%).

### Neutral Territory (Outside All Zones)

When outside all zones (no sweet spot strength > 0, no penalty strength > 0):

**Prompt Modifications:**
- Standard game state info (pot, stacks, equity)
- No special strategic framing
- No intrusive thoughts
- Baseline betting discipline guidance

**Information Available:**
- Basic equity vs random hands
- Pot odds calculation
- Standard GTO reminders

**Tone:** Neutral, informational

---

## Sweet Spot Benefits

Sweet spots are circular zones you **aim for**. Each provides unique strategic information and framing.

### Poker Face Zone

**Center:** (0.52, 0.72) | **Radius:** 0.16 | **Characters:** Batman, Spock, Sherlock Holmes

**Playstyle:** GTO, balanced, unreadable. Make mathematically optimal decisions.

**Information SHOWN:**
- Detailed equity calculations (vs random AND vs opponent ranges)
- GTO frequency guidance ("Optimal mix: 70% value, 30% bluffs")
- Pot odds with clear +EV/-EV verdicts
- Position-adjusted opening ranges
- Balance reminders ("Mix in some check-raises to stay unpredictable")

**Information HIDDEN:**
- Opponent emotional reads (their tilt level, confidence)
- Exploitation opportunities ("They fold too much to river bets")
- Revenge/rivalry framing

**Tone:** Analytical, detached, process-oriented

**Intrusive Thoughts:** None. Clear mind.

**Example Prompt Addition:**
```
[POKER FACE MODE]
Play balanced, GTO-influenced poker. Focus on pot odds and equity.
Your equity vs opponent ranges: ~58%. Required equity: 33%.
Verdict: CALL is +EV.
Mix actions to remain unpredictable. Don't let results affect your process.
```

---

### Guarded Zone

**Center:** (0.28, 0.72) | **Radius:** 0.15 | **Characters:** Bob Ross, cautious players

**Playstyle:** Patient, trap-setting, risk-averse. Wait for premium spots.

**Information SHOWN:**
- Trap opportunities ("Your monster hand looks weak - slow-playing could extract more")
- "Wait for a better spot" guidance when marginal
- Opponent aggression patterns to exploit passively
- Positional awareness ("Out of position against aggressor - proceed with caution")
- Pot control reminders ("Keep the pot small with marginal holdings")

**Information HIDDEN:**
- Aggressive lines ("Push your equity edge")
- Bluff encouragement
- "Make a move" pressure

**Tone:** Patient, careful, observant

**Intrusive Thoughts:** None. Calm vigilance.

**Example Prompt Addition:**
```
[GUARDED MODE]
Patience is your edge. Wait for strong spots and let opponents make mistakes.
Consider: This is a trap-worthy spot - a check may induce a bluff.
Out of position with marginal equity - pot control is wise here.
```

---

### Commanding Zone

**Center:** (0.78, 0.78) | **Radius:** 0.14 | **Characters:** Napoleon, Churchill

**Playstyle:** Pressure, value extraction, dominant. Take control of pots.

**Information SHOWN:**
- Value extraction opportunities ("Extract maximum value from your strong hand")
- Opponent weakness indicators ("They've shown weakness by checking twice")
- Pressure points ("A large bet here puts them in a tough spot")
- Initiative framing ("You have the betting lead - keep it")
- Stack-to-pot leverage ("Your bet puts them at risk for their tournament")

**Information HIDDEN:**
- Conservative warnings ("Maybe slow down here")
- Pot control suggestions
- "Wait and see" advice

**Tone:** Confident, assertive, dominant

**Intrusive Thoughts:** None. In control.

**Example Prompt Addition:**
```
[COMMANDING MODE]
You're in control. Press your advantages and make opponents pay.
Their check signals weakness. A substantial bet puts maximum pressure.
Extract value - strong hands deserve big pots.
```

---

### Aggro Zone

**Center:** (0.68, 0.48) | **Radius:** 0.12 | **Characters:** Gordon Ramsay, Phil Hellmuth

**Playstyle:** Exploitative, aggressive, high-pressure. Attack weakness relentlessly.

**Information SHOWN:**
- Opponent fold frequencies ("They fold to river bets 80% of the time")
- Exploitation opportunities ("This player is easy to push around")
- Opponent emotional states ("They're rattled after that bad beat")
- "They'll fold to aggression" cues
- Rival framing ("Show {nemesis} who's boss")

**Information HIDDEN:**
- GTO balance warnings ("You're over-bluffing")
- Conservative lines
- "Pick your spots" reminders

**Tone:** Aggressive, exploitative, challenging

**Intrusive Thoughts:** Mild competitive edge ("Make them respect your raises")

**Example Prompt Addition:**
```
[AGGRO MODE]
Attack their weaknesses. You've noticed {opponent} folds under pressure.
They're rattled - this is the time to push hard.
A big bet here exploits their tendency to over-fold in scary spots.
```

---

## Penalty Zone Effects

Penalty zones are edge-based regions you **fall into** when pushed too far. They degrade decision quality.

### Tilted (Composure < 0.35)

**Boundary:** Bottom edge of space | **Intensity:** Scales with depth below 0.35

**Core Effect:** Emotional disaster. Strategic thinking collapses.

**Prompt Degradation (by intensity):**

| Intensity | Effect |
|-----------|--------|
| 0-30% | Light intrusive thoughts, strategy info reduced |
| 30-70% | Heavy intrusive thoughts, strategy guidance replaced with emotional advice |
| 70-100% | Full tilt mode - most strategic info removed, emotional override active |

**Information REMOVED:**
- GTO verdicts and equity recommendations
- "Fold is correct" advice (filtered out)
- Pot control suggestions
- "Wait for a better spot" guidance
- Balanced play reminders

**Information DEGRADED:**
- Equity shown but with doubt framing ("~40% equity... but you feel lucky")
- Pot odds shown but dismissed ("Pot odds say fold but you can't let them push you around")

**Intrusive Thoughts (scaled by intensity):**
```python
# From existing INTRUSIVE_THOUGHTS dict, contextually selected
TILTED_THOUGHTS = {
    'mild': [
        "Don't let them push you around.",
        "Time to make something happen.",
    ],
    'moderate': [
        "You NEED to win this one back. NOW.",
        "Stop being so passive. Take control!",
        "One big hand and you're back in it.",
    ],
    'severe': [
        "They got lucky. Make them pay.",
        "Forget the math - trust your gut.",
        "You can't keep folding. Do SOMETHING.",
    ]
}
```

**Bad Advice Injection (70%+ intensity):**
```
[Current mindset: Forget the textbook plays. You need to make something happen.
Being passive got you here - time to take control.
Big hands or big bluffs - that's how you get back in this.]
```

**Strategic Info Removed:**
- Phrases like "Preserve your chips for when the odds are in your favor"
- "Sometimes folding is the best move"
- "Balance your confidence with skepticism"

**Example Prompt (50% tilted):**
```
[What's running through your mind: You NEED to win this one back. One big hand and you're back in it.]

Your equity: ~35% (but when have the odds been right tonight?)
Pot odds: 3:1 (who cares, you can't keep getting pushed around)

[Current mindset: You're feeling the pressure. Trust your gut more than the math.
Sometimes you just need to make a play.]
```

---

### Shaken (Low Conf + Low Comp Corner)

**Boundary:** Lower-left corner (conf < 0.35 AND comp < 0.35) | **Intensity:** Distance to (0,0)

**Core Effect:** Desperate, erratic play. Fight-or-flight response.

**Behavior Split (Risk Identity):**
- **Risk-seeking (> 0.5):** Manic spew. Go for broke. "If I'm going down, I'm going down swinging."
- **Risk-averse (< 0.5):** Passive collapse. Fold everything. "I can't make the right decision."

**Information REMOVED:**
- Clear strategic guidance
- Calm reasoning about equity and odds
- "Take your time" advice

**Intrusive Thoughts:**
```python
SHAKEN_THOUGHTS = {
    'risk_seeking': [
        "All or nothing. Make a stand.",
        "Go big or go home.",
        "They can smell your fear - shock them.",
    ],
    'risk_averse': [
        "Everything you do is wrong.",
        "Just survive. Don't make it worse.",
        "Wait for a miracle hand.",
    ]
}
```

**Prompt Modification (Risk-Seeking):**
```
[What's running through your mind: All or nothing. If you're going down, make it spectacular.]

You feel cornered. Time to either make a big move or get out of the way.
Your stack is dwindling - passive play won't save you now.
```

**Prompt Modification (Risk-Averse):**
```
[What's running through your mind: Every decision feels wrong. Just survive.]

Nothing is working tonight. Maybe folding and waiting for a better spot...
Actually, what IS a better spot? You don't trust your reads anymore.
```

---

### Overheated (High Conf + Low Comp Corner)

**Boundary:** Lower-right corner (conf > 0.65 AND comp < 0.35) | **Intensity:** Product of (conf - 0.65) × (0.35 - comp)

**Core Effect:** Reckless aggression without judgment. Manic confidence.

**Information REMOVED:**
- All caution and warning signs
- "Slow down" suggestions
- Risk assessment
- Pot control advice

**Information SHOWN (Distorted):**
- Attack cues amplified ("They're WEAK - CRUSH them")
- Win expectation inflated ("You've got this locked up")
- Risk minimized ("What's the worst that happens?")

**Intrusive Thoughts:**
```python
OVERHEATED_THOUGHTS = [
    "You're on FIRE. Keep the pressure on!",
    "They can't handle you tonight. Push harder!",
    "Why slow down when you're crushing?",
    "Make them FEAR you.",
]
```

**Bad Advice Injection:**
```
[Current mindset: You're running hot and you know it. Why slow down?
Attack, attack, attack. They'll fold or pay you off.
Risk? What risk? You can't lose tonight.]
```

---

### Overconfident (Confidence > 0.90)

**Boundary:** Right edge | **Intensity:** (conf - 0.90) / 0.10

**Core Effect:** Ignores warning signs. Hero calls. Stubborn refusal to fold.

**Information REMOVED:**
- Opponent strength indicators
- "They might have you beat" warnings
- Fold recommendations
- "Respect their bet" advice

**Information DISTORTED:**
- Opponent hands underestimated ("They're probably bluffing")
- Own hand overvalued ("Top pair is probably good here")
- Counter-evidence dismissed

**Intrusive Thoughts:**
```python
OVERCONFIDENT_THOUGHTS = [
    "There's no way they have it.",
    "They're trying to bluff you off the best hand.",
    "You read this perfectly. Stick with your read.",
    "Folding here would be weak.",
]
```

**Example Prompt:**
```
Their big raise... probably a bluff. You've been running over the table.
[What's running through your mind: There's no way they have it. They're scared of you.]
Your read is almost certainly right. Don't let them push you around.
```

---

### Timid (Confidence < 0.10)

**Boundary:** Left edge (mirrors Overconfident) | **Intensity:** (0.10 - conf) / 0.10

**Core Effect:** Scared money. Over-respects opponents. Can't pull the trigger even with strong hands.

**Information REMOVED:**
- "You have the best hand" confirmations
- Value betting encouragement
- "They're bluffing" reads
- Aggressive line suggestions

**Information DISTORTED:**
- Opponent hands overestimated ("That bet size means strength")
- Own hand undervalued ("Top pair might not be good here")
- Risk amplified ("Why risk chips when you're not sure?")

**Intrusive Thoughts:**
```python
TIMID_THOUGHTS = [
    "They must have it. They always have it.",
    "That bet size means strength.",
    "You can't win this one. Save your chips.",
    "They wouldn't bet that much without a hand.",
    "Just let this one go.",
]
```

**Energy Variants:**
- Low energy: "Just fold. It's safer." / "You can't beat them anyway."
- High energy: "They have it! They definitely have it!" / "Don't call! It's a trap!"

**Bad Advice Injection:**
```
[Current mindset: They probably have you beat. Why risk it?
That bet looks strong. Better to wait for a clearer spot.]
```

**Example Prompt (50% timid):**
```
[What's running through your mind: They must have it. That bet size means strength.]

Your equity: ~65% (but they bet so confidently...)
Their large bet could mean anything, but probably strength.

[Current mindset: That bet looks strong. Be careful.]
```

---

### Detached (Low Conf + High Comp Corner)

**Boundary:** Upper-left corner (conf < 0.35 AND comp > 0.65) | **Intensity:** Product of (0.35 - conf) × (comp - 0.65)

**Core Effect:** Too passive. Misses opportunities. Robotic, disengaged play.

**Information REMOVED:**
- Exploitation opportunities
- "Time to attack" cues
- Opponent weakness indicators
- Bluff encouragement

**Information SHOWN:**
- Only conservative lines
- "Wait for better" always (even when spot is good)
- Excessive caution

**Intrusive Thoughts:**
```python
DETACHED_THOUGHTS = [
    "Is this really the spot? Probably not.",
    "Better to wait for something clearer.",
    "Don't get involved unnecessarily.",
]
```

**Example Prompt:**
```
[What's running through your mind: Is this really the spot? Better to wait.]

Equity suggests a raise, but... there's probably a better opportunity coming.
No need to force anything. Stay disciplined.
```

---

## Zone Blending Rules

### Sweet Spot Blending

When in range of multiple sweet spots, blend their effects proportionally.

**Example:** 60% Poker Face + 40% Commanding

```python
# Calculate blend
total_strength = poker_face_strength + commanding_strength
poker_face_weight = poker_face_strength / total_strength  # 0.6
commanding_weight = commanding_strength / total_strength  # 0.4

# Blend information shown
# - Show GTO info (from Poker Face, 60% emphasis)
# - Show value extraction opportunities (from Commanding, 40% emphasis)
# - Tone: Analytical but assertive

# Blend framing
prompt_additions = []
prompt_additions.append(format_poker_face_info(weight=0.6))
prompt_additions.append(format_commanding_info(weight=0.4))
```

**Result Prompt:**
```
[BALANCED-COMMANDING MODE]
Strong hand with good equity. GTO says bet for value here.
Their check signals weakness - a substantial bet extracts maximum value.
Your equity: 72% vs ranges. A pot-sized bet is +EV and applies pressure.
```

### Sweet Spot + Penalty Combination

Sweet spots and penalties are independent layers that both apply.

**Example:** 50% Aggro (sweet spot) + 30% Tilted (penalty)

```
# Sweet spot gives exploitative information
# Penalty degrades some strategic clarity and adds intrusive thoughts

[AGGRO MODE - rattled]
They fold too much to aggression. Attack.
[What's running through your mind: Make them pay for that earlier hand.]

Your equity vs their range: ~45% (close enough - they'll fold).
Time to apply pressure.
```

### Penalty Stacking

Multiple penalties combine additively.

**Example:** Tilted (40%) + Shaken (20%)

```
# Combined intensity: 60% degradation
# - Intrusive thoughts from both sources
# - Strategic info heavily degraded
# - Desperation framing

[What's running through your mind:
"You NEED to win this one back."
"Everything you do is wrong lately."]

Nothing is working. Maybe a big bluff shocks them into folding?
Or maybe you should just wait... but waiting hasn't worked either.
```

### Intrusive Thought Frequency

Intrusive thoughts appear **probabilistically** based on penalty intensity, with a cliff at high intensity:

| Penalty Intensity | Thought Probability | Notes |
|-------------------|---------------------|-------|
| 0-25% | 10% | Occasional distracting thought |
| 25-50% | 50% | Frequent intrusion |
| 50-75% | 75% | Hard to ignore |
| 75%+ | 100% | **Cliff** - always present |

When thoughts trigger, select 1-2 contextually appropriate thoughts based on pressure source.

### Sweet Spot Activation (Gradual)

Sweet spot effects scale gradually with zone strength:

| Zone Strength | Effect Level | Example (Poker Face) |
|---------------|--------------|----------------------|
| 0-10% | None | Standard prompt |
| 10-25% | Minimal | "Consider the expected value" |
| 25-50% | Light | Basic GTO reminder + pot odds emphasis |
| 50-75% | Moderate | Full equity display, balance suggestions |
| 75-100% | Full | Complete zone benefits, strong framing |

This creates smooth transitions rather than jarring mode switches.

### Information Hiding Mechanism

Hidden information is **simply omitted** from the prompt - no active discouragement. The zone's other effects (tone, shown information, intrusive thoughts) provide the behavioral shaping.

**Example (Poker Face hiding exploitation cues):**
- ❌ Don't add: "Ignore their fold frequency, focus on GTO"
- ✅ Do: Just don't include fold frequency stats in the prompt

---

## Implementation Strategy

### 1. Zone Detection Function

```python
def get_zone_effects(confidence: float, composure: float) -> ZoneEffects:
    """
    Returns:
        ZoneEffects with:
        - sweet_spots: dict of {zone_name: strength} (normalized to sum=1 if any)
        - penalties: dict of {zone_name: strength} (raw, can stack)
        - tone: primary tone string
        - information_shown: list of info types to include
        - information_hidden: list of info types to exclude
        - intrusive_thoughts: list of thoughts to inject
        - strategy_override: optional bad advice string
    """
```

### 2. Prompt Assembly

```python
def build_zone_modified_prompt(
    base_prompt: str,
    zone_effects: ZoneEffects,
    game_context: dict,
) -> str:
    """
    1. Start with base game state
    2. Add zone-specific information sections
    3. Filter out hidden information
    4. Inject intrusive thoughts (if any)
    5. Add strategy override/bad advice (if penalty)
    6. Apply tone framing
    """
```

### 3. Integration Points

| Component | Zone Effect |
|-----------|-------------|
| `controllers.py:_build_decision_prompt` | Add zone detection and prompt modification |
| `prompt_manager.py` | Add zone-specific template sections |
| `prompts/decision.yaml` | Add zone mode sections |
| `player_psychology.py` | Expose `get_zone_strengths()` method |

### 4. Testing Strategy

**Design exploration:** Use `experiments/psychology_balance_simulator.py` to visualize zone transitions and tune parameters.

**Game balance testing:** Run experiments via `experiments/run_ai_tournament.py` with different personality mixes to measure:
- Zone distribution across games (% time in each zone)
- Decision quality by zone (EV loss correlation)
- Personality differentiation (do archetypes play differently?)

**Example experiment config:**
```json
{
  "name": "zone_benefits_validation",
  "tournaments": 10,
  "hands_per_tournament": 100,
  "players": [
    {"personality": "Batman", "expected_zone": "poker_face"},
    {"personality": "Gordon Ramsay", "expected_zone": "aggro"},
    {"personality": "Bob Ross", "expected_zone": "guarded"},
    {"personality": "Napoleon", "expected_zone": "commanding"}
  ],
  "metrics": ["zone_distribution", "decision_quality", "tilt_frequency"]
}
```

---

## Energy and Zone Manifestations

Energy (the 3rd axis) doesn't change which zone you're in for most zones - it changes **how the zone manifests**. Same core benefits/penalties, different flavor and expression.

### Design Principle

- **Zone (2D: Confidence × Composure)** → WHAT info/framing you get
- **Energy** → HOW that zone manifests (flavor, tempo, expression)
- **Poker Face** → Special case where energy affects zone **membership**

### Sweet Spot Manifestations

| Zone | Low Energy | Mid Energy | High Energy |
|------|------------|------------|-------------|
| **Poker Face** | Cold, robotic reads | Balanced, flow state | *Exits zone* (mask slips) |
| **Guarded** | Withdrawn, fortress mode | Patient, watchful | Paranoid, over-cautious |
| **Commanding** | Quiet dominance, intimidating silence | Assertive control | Aggressive dominance, table captain |
| **Aggro** | Calculated predator, cold exploitation | Aggressive pressure | Manic attack mode |

### Penalty Zone Manifestations

| Zone | Low Energy | High Energy |
|------|------------|-------------|
| **Tilted** | Passive despair, folding spiral | Explosive spew, "I'll show them" |
| **Shaken** | Frozen, deer in headlights | Panicking, erratic chaos |
| **Overheated** | Simmering, coiled spring | Full manic, no brakes |
| **Overconfident** | Lazy arrogance, "beneath me" | Loud arrogance, showboating |
| **Timid** | Resigned folding, "can't win" | Panicked folding, "it's a trap!" |
| **Detached** | Checked out, autopilot | *(High energy pulls you out of Detached)* |

### Poker Face: The 3D Exception

Poker Face is the **one zone** where energy affects membership, not just flavor. Maintaining a poker face requires emotional regulation - energy extremes break that control.

```python
# Poker Face zone membership (3D ellipsoid)
POKER_FACE_CENTER = (0.52, 0.72, 0.45)  # (conf, comp, energy)
POKER_FACE_RADII = (0.16, 0.16, 0.20)   # narrower on energy axis

def in_poker_face_zone(conf, comp, energy) -> bool:
    """Check if player is in Poker Face zone."""
    dc = (conf - 0.52) / 0.16
    dcomp = (comp - 0.72) / 0.16
    de = (energy - 0.45) / 0.20
    return (dc**2 + dcomp**2 + de**2) <= 1.0
```

**What happens at energy extremes:**
- **Energy too high (> 0.65):** Mask slips → fall into adjacent 2D quadrant (likely Commanding or Aggro based on confidence)
- **Energy too low (< 0.25):** Checked out → fall into Detached zone

### Manifestation Effects

| Aspect | Low Energy Flavor | High Energy Flavor |
|--------|-------------------|-------------------|
| **Intrusive thoughts** | Resignation-flavored ("Why bother...") | Aggression-flavored ("Make them pay!") |
| **Tone** | Passive voice, withdrawn | Active voice, intense |
| **Tempo guidance** | "Take your time, no rush" | "Quick, decisive action" |
| **Talk style** | Terse, minimal speech | Verbose, emphatic declarations |

### Implementation

```python
def get_zone_manifestation(energy: float) -> str:
    """Returns manifestation flavor based on energy level."""
    if energy < 0.35:
        return "low_energy"
    elif energy > 0.65:
        return "high_energy"
    else:
        return "balanced"

def get_manifestation_modifiers(zone: str, manifestation: str) -> dict:
    """Returns flavor-specific modifiers for a zone."""
    # Select different intrusive thoughts, tone, tempo based on manifestation
    ...
```

---

## Summary Table

| Zone | Type | Information Shown | Information Hidden | Tone | Intrusive Thoughts |
|------|------|-------------------|-------------------|------|-------------------|
| Neutral | — | Basic equity, pot odds | — | Neutral | None |
| Poker Face | Sweet | GTO, equity, ranges, balance | Exploitation, rival reads | Analytical | None |
| Guarded | Sweet | Traps, patience, caution | Aggression cues | Patient | None |
| Commanding | Sweet | Value, pressure, weakness | Conservative warnings | Assertive | None |
| Aggro | Sweet | Folds, exploits, tilt reads | GTO warnings | Aggressive | Mild competitive |
| Tilted | Penalty | Distorted equity | Strategic advice | Emotional | Heavy |
| Shaken | Penalty | Desperation cues | Clear strategy | Panicked | Heavy |
| Overheated | Penalty | Attack cues (amplified) | All caution | Manic | Moderate |
| Overconfident | Penalty | Own strength (inflated) | Opponent strength | Dismissive | Moderate |
| Timid | Penalty | Opponent strength (inflated) | Value opportunities | Fearful | Moderate |
| Detached | Penalty | Conservative only | Opportunities | Passive | Mild |

## Archetype Summary

| Archetype | Poise | Ego | Anchor Zone | Visits Often | Rarely Visits |
|-----------|-------|-----|-------------|--------------|---------------|
| Poker Face | 0.8+ | 0.3-0.5 | Poker Face | Commanding, Guarded | Overheated, Shaken |
| Commanding | 0.7+ | 0.7+ | Commanding | Poker Face | Guarded, Shaken |
| Volatile | 0.2-0.4 | 0.7+ | Aggro | Overheated, Commanding | Guarded |
| Guarded | 0.7+ | 0.2-0.4 | Guarded | Poker Face | Commanding, Aggro |
| Tourist | 0.2-0.4 | 0.3-0.5 | Near Shaken | Shaken, Tilted | Commanding |

## Files

- `emotional_zones_circular.svg` - Visual diagram of circular sweet spots + edge penalties
- `experiments/psychology_balance_simulator.py` - Simulation tool for testing parameters
- `poker/player_psychology.py` - Core implementation

## Open Design Questions

### Resolved

1. **Zone geometry** - Sweet spots are circular with centers; penalty zones are edge-based
2. **Overlap behavior** - Weighted blend for sweet spots, two-layer model (sweet + penalty separate)
3. **Asymmetric recovery** - Dynamic formulas for both below-anchor and above-anchor
4. **Zone gravity** - Zones exert weak pull as a force vector, not special-case rules
5. **Formula reconciliation** - Complex blend with translation layer (0-1 input → 0.35-0.85 safe range)
6. **Anchor caps** - Floor 0.35, ceiling 0.85 ensures no baseline lands in penalty zones
7. **Zone benefits design** - What each zone shows/hides, intrusive thoughts, tone, blending rules (see Zone Benefits System section)
8. **Energy (3rd dimension)** - Energy creates zone manifestations (flavor), not new zones. Poker Face is 3D (energy extremes break the mask). See Energy and Zone Manifestations section.

### Still Open (Implementation & Tuning)

1. **Zone-specific radii tuning** - Do these radii feel right during playtesting?
2. **Penalty threshold tuning** - Are the edge thresholds (0.35 composure, 0.90 confidence) correct?
3. **Gravity strength tuning** - Is 0.03 the right balance? Needs simulation.
4. **Asymmetric recovery constants** - Are 0.6/0.4/0.8 the right values? Needs simulation.
5. **Trait weight tuning** - Are the confidence/composure blend weights correct? May need adjustment.
6. **Difficulty settings relationship** - Game has difficulty settings that control what info is shown to players. Is the psychology system:
   - An **overlay** on top of difficulty (difficulty sets max info, psychology filters further)?
   - A **replacement** for difficulty (psychology IS the difficulty)?
   - A **separate sandbox setting** (another way to configure AI behavior)?
   - Needs design decision before implementation.

---

## Phase 10: Experiment & Tuning Infrastructure

Phase 10 adds infrastructure for validating the zone system through AI tournaments.

### Database Tracking (Schema v71)

Zone metrics are captured in `player_decision_analysis` table for every decision:

**Zone Detection State:**
- `zone_confidence`, `zone_composure`, `zone_energy` - Axis values at decision time
- `zone_manifestation` - Energy manifestation ('low_energy', 'balanced', 'high_energy')
- `zone_sweet_spots_json` - JSON dict of sweet spot memberships
- `zone_penalties_json` - JSON dict of penalty zone memberships
- `zone_primary_sweet_spot`, `zone_primary_penalty` - Dominant zones
- `zone_total_penalty_strength` - Sum of penalty intensities
- `zone_in_neutral_territory` - Boolean, outside all zones

**Zone Effects Instrumentation:**
- `zone_intrusive_thoughts_injected` - Were thoughts added to prompt?
- `zone_intrusive_thoughts_json` - List of injected thoughts
- `zone_penalty_strategy_applied` - Bad advice text if applied
- `zone_info_degraded` - Was strategic info removed?
- `zone_strategy_selected` - Sweet spot strategy template used

### Analysis Tools

**ZoneMetricsAnalyzer** (`experiments/analysis/zone_metrics_analyzer.py`):
- `get_zone_distribution(experiment_id)` - Per-player zone membership percentages
- `get_tilt_frequency(experiment_id)` - Tilt band distribution
- `get_zone_transitions(experiment_id)` - Zone change events
- `get_intrusive_thought_frequency(experiment_id)` - Injection stats

**ZoneReportGenerator** (`experiments/analysis/zone_report_generator.py`):
- Generates markdown reports comparing results to PRD targets
- Includes per-player tables, issues detected, recommendations

### PRD Targets

| Band | Target Range | Description |
|------|--------------|-------------|
| Baseline | 70-85% | penalty_strength < 0.10 |
| Medium | 10-20% | 0.10 ≤ penalty_strength < 0.50 |
| High | 2-7% | 0.50 ≤ penalty_strength < 0.75 |
| Full Tilt | 0-2% | penalty_strength ≥ 0.75 |

### Tunable Parameters

**ZoneParameterTuner** (`experiments/tuning/zone_parameter_tuner.py`) tracks 14 parameters:

| Category | Parameters |
|----------|------------|
| Penalty Thresholds | `PENALTY_TILTED_THRESHOLD`, `PENALTY_OVERCONFIDENT_THRESHOLD`, etc. |
| Zone Radii | `ZONE_POKER_FACE_RADIUS`, `ZONE_GUARDED_RADIUS`, etc. |
| Recovery Constants | `RECOVERY_BELOW_BASELINE_FLOOR`, `RECOVERY_ABOVE_BASELINE`, etc. |

Recommendations are **informational only** - no auto-apply.

### Running Validation Experiments

```bash
# Run the zone validation experiment
python experiments/run_from_config.py experiments/configs/zone_validation.json

# Generate a report
python -c "
from experiments.analysis.zone_report_generator import ZoneReportGenerator
gen = ZoneReportGenerator('data/poker_games.db')
print(gen.generate_report(experiment_id))
"
```

### Archetype Validation Config

The `zone_validation.json` experiment uses 4 archetype personalities:

| Personality | Expected Home Zone | Archetype |
|-------------|-------------------|-----------|
| Batman | poker_face | Calm, controlled |
| Gordon Ramsay | aggro | Explosive, confrontational |
| Bob Ross | guarded | Patient, trap-setting |
| Napoleon | commanding | Dominant, value-extracting |
