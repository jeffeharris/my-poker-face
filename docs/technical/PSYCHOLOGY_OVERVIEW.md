# Poker AI Psychology System Overview

This document is the single reference for the psychology system's design and mechanics. For detailed zone geometry and pseudocode, see [`PSYCHOLOGY_ZONES_MODEL.md`](PSYCHOLOGY_ZONES_MODEL.md).

---

## 1. Purpose & Principles

The system produces AI poker players that feel **human, readable, and psychologically consistent**.

**Core principles:**
- Emotion is **visible**; strategic intent must be **inferred**
- Personality is **stable within a session** (anchors never change)
- Behavior is **non-deterministic but constrained**
- Opponents are **readable and exploitable over time**

---

## 2. Three-Layer Model

| Layer | What | Mutability |
|-------|------|------------|
| **Identity** (Anchors) | Who the player fundamentally is | Static per session |
| **State** (Axes) | How they currently feel | Changes every hand |
| **Expression** (Output) | What the opponent sees | Filtered from state |

This separation is a hard design constraint: anchors define the gravity well, axes move freely within it, expression filters what leaks out.

---

## 3. Personality Anchors (Identity Layer)

Nine anchors define a player's identity. They never change during a session.

| Anchor | Range | Purpose |
|--------|-------|---------|
| `baseline_aggression` | 0-1 | Default bet/raise frequency |
| `baseline_looseness` | 0-1 | Default hand range width |
| `ego` | 0-1 | Confidence sensitivity to being outplayed (high = fragile) |
| `poise` | 0-1 | Composure resistance to bad outcomes (high = resilient) |
| `expressiveness` | 0-1 | Emotional transparency; narrows poker face zone on energy axis |
| `risk_identity` | 0-1 | Variance tolerance; affects shaken-state direction and zone shape |
| `adaptation_bias` | 0-1 | Opponent adjustment rate |
| `baseline_energy` | 0-1 | Resting energy level |
| `recovery_rate` | 0-1 | Speed of axis decay toward baselines |

---

## 4. Dynamic Axes (State Layer)

Three continuous axes change during play and decay back toward anchor-defined baselines.

### Confidence (0-1)
Belief in one's reads and decisions. Moved by "being wrong" events, filtered through **ego**.

**Impacts:** bluff commitment, bluff-catching, thin value betting, pressure application, effective looseness.

### Composure (0-1)
Ability to regulate emotion under stress. Moved by "bad outcome" events, filtered through **poise**.

**Impacts:** tilt resistance, decision consistency, decision noise, effective aggression.

### Energy (0-1)
Engagement and intensity level. A volume knob for expression, not strategy.

**Controls:** table talk frequency, decision tempo, theatrics. **Does NOT control:** hand evaluation, bluff frequency, bet sizing intent.

### Baseline Derivation

```python
baseline_composure = 0.25 + poise * 0.50 + (1 - expressiveness) * 0.15 + (risk_identity - 0.5) * 0.3
baseline_confidence = 0.3 + baseline_aggression * 0.25 + risk_identity * 0.20 + ego * 0.25
```

Both are clamped to stay outside penalty zone thresholds.

---

## 5. Emotional Space

### 2D Quadrant Model (Confidence x Composure)

```
                    CONFIDENCE -->
        0.0         0.35        0.65        0.90    1.0
       +--------+----------+-----------+--------+-----+
  1.0  |        |          |           |        |     |
       |DETACHED| GUARDED  |POKER FACE |COMMAND-|OVER-|
  0.65 |        |   (o)    |   (o)     |ING (o) |CONF |
       +--------+----------+-----------+--------+-----+
  0.35 | TIMID  |          |  AGGRO(o) |OVER-   |     |
       |        |  TILTED  |           |HEATED  |     |
  0.0  | SHAKEN |          |           |        |     |
       +--------+----------+-----------+--------+-----+

  (o) = sweet spot center
  Edge/corner regions = penalty zones
```

Energy is the 3rd axis. It affects **how** a zone manifests (flavor), not **which** zone. Exception: Poker Face is a 3D ellipsoid where energy extremes break the mask.

---

## 6. Zones

The space contains two types of zones with fundamentally different geometries:
- **Sweet spots** (circular, center-based): Places you *aim for* - they grant bonuses
- **Penalty zones** (edge/boundary-based): Places you *fall into* - they degrade decisions

### Sweet Spots

| Zone | Center (conf, comp) | Radius | Playstyle |
|------|---------------------|--------|-----------|
| Poker Face | (0.52, 0.72) | 0.16 | GTO, balanced, unreadable |
| Guarded | (0.28, 0.72) | 0.15 | Patient, trap-setting |
| Commanding | (0.78, 0.78) | 0.14 | Pressure, value extraction |
| Aggro | (0.68, 0.48) | 0.12 | Exploitative, aggressive |

Strength uses **cosine falloff**: 100% at center, smooth decay to 0% at edge. When in multiple sweet spots, strengths are normalized to sum to 1.0 for blending.

### Penalty Zones

| Zone | Boundary | Effect |
|------|----------|--------|
| Tilted | Composure < 0.35 | Emotional disaster, strategic collapse |
| Overconfident | Confidence > 0.90 | Ignores warnings, hero calls |
| Timid | Confidence < 0.10 | Scared money, over-folds |
| Shaken | Conf < 0.35 AND Comp < 0.35 | Desperate, erratic (risk-identity split) |
| Overheated | Conf > 0.65 AND Comp < 0.35 | Reckless aggression |
| Detached | Conf < 0.35 AND Comp > 0.65 | Too passive, misses opportunities |

Penalty strength scales linearly with distance past the threshold. Multiple penalties stack additively.

For detailed geometry (formulas, pseudocode, worked examples), see [`PSYCHOLOGY_ZONES_MODEL.md`](PSYCHOLOGY_ZONES_MODEL.md).

---

## 7. Zone Effects

Zones modify **information access**, not force actions. The AI still makes its own decisions.

### Sweet Spot Benefits

| Zone | Information Shown | Information Hidden | Tone |
|------|-------------------|--------------------|------|
| Poker Face | GTO equity, ranges, balance reminders | Exploitation cues, rival reads | Analytical |
| Guarded | Trap opportunities, patience cues, pot control | Aggression encouragement | Patient |
| Commanding | Value extraction, pressure points, initiative | Conservative warnings | Assertive |
| Aggro | Opponent folds, tilt reads, exploitation cues | GTO warnings | Aggressive |

**Gradual activation:** 0-10% strength = none, 10-25% = minimal, 25-50% = light, 50%+ = full.

**Blending:** When in multiple sweet spots (e.g. 60% Poker Face + 40% Commanding), guidance from both zones is included, weighted by strength.

### Penalty Zone Effects

Three degradation mechanisms, all scaling with penalty intensity:

1. **Intrusive thoughts** - Probabilistic injection: <25% intensity = 10% chance, 25-50% = 50%, 50-75% = 75%, 75%+ = 100% (cliff). Each penalty zone has distinct thought pools (e.g. Shaken splits by risk_identity into "spew" vs "collapse").

2. **Bad advice** - Strategy guidance replaced with zone-appropriate bad advice at moderate+ intensity. Tilted: "Forget the textbook, trust your gut." Overconfident: "They're probably bluffing." Timid: "That bet looks strong, better fold."

3. **Information removal** - Strategic phrases stripped from the prompt. Tilted loses fold recommendations and balance reminders. Overconfident loses opponent strength indicators. Each zone has a specific phrase removal list.

### Energy Manifestations

Energy changes the *flavor* of zone effects, not which zone applies:

| Zone | Low Energy | High Energy |
|------|------------|-------------|
| Poker Face | Cold, robotic reads | *Exits zone* (mask slips) |
| Guarded | Withdrawn, fortress mode | Paranoid, over-cautious |
| Commanding | Quiet dominance | Aggressive table captain |
| Aggro | Calculated predator | Manic attack mode |
| Tilted | Passive despair | Explosive spew |
| Overconfident | Lazy arrogance | Loud showboating |

---

## 8. Derived Values (Play Style)

Two derived values control strategic tendencies:

### Effective Aggression
```python
aggression_mod = (confidence - 0.5) * 0.3 + (0.5 - composure) * 0.2  # clamped +/-0.20
effective_aggression = baseline_aggression + aggression_mod
```

### Effective Looseness
```python
looseness_mod = (confidence - 0.5) * 0.2 + (0.5 - composure) * 0.15  # clamped +/-0.20
effective_looseness = baseline_looseness + looseness_mod
```

**Shaken gate** (conf < 0.35 AND comp < 0.35): Modifiers widen to +/-0.30, split by risk_identity (risk-seeking = spew, risk-averse = collapse).

Effective looseness maps to position-adjusted hand ranges with hard clamps (8%-65% depending on position). Emotional modifiers shift the cutoff but cannot bypass position clamps.

---

## 9. Expression System

Expression filters what the human player observes.

### Visibility Formula
```python
visibility = 0.7 * expressiveness + 0.3 * energy
```

Applied to avatar emotion display and table talk content. Low visibility = poker face display, high visibility = true emotion shown.

### Output Channels

| Channel | What it shows |
|---------|---------------|
| Avatar emotion | Filtered emotional state or poker face |
| Table talk | Emotional content or neutral |
| Tempo | Decision speed (energy-driven) |

**What expression does NOT hide:** Betting patterns are behavioral, not presentational. A player who always bets big when confident has an exploitable pattern regardless of expressiveness.

---

## 10. Recovery & Dynamics

### Event Sensitivity

Events are routed through anchors:

| Event Type | Target Axis | Sensitivity Anchor | Formula |
|------------|-------------|--------------------|---------|
| Being outplayed | Confidence | Ego | `impact * (floor + (1-floor) * ego)` |
| Bad outcomes | Composure | Poise | `impact * (floor + (1-floor) * (1-poise))` |
| Engagement | Energy | Energy Anchor | Direct push |

Severity floors: minor = 0.20, normal = 0.30, major = 0.40. High-ego players lose more confidence when bluffed; high-poise players shrug off bad beats.

### Recovery (Asymmetric)

```python
base_recovery = 0.12 + 0.25 * (1 - poise)

# Below baseline: tilt is sticky
below_modifier = 0.6 + 0.4 * current_value

# Above baseline: hot streaks last
above_modifier = 0.8
```

Recovery pulls toward **personality baselines**, not toward the poker face zone.

### Zone Gravity

Zones exert weak gravitational pull (default strength 0.03, tunable 0.02-0.05):

- **Sweet spot gravity**: Pulls toward zone center (stabilizing)
- **Penalty zone gravity**: Pulls toward zone extreme (trap effect)

| Penalty Zone | Gravity Direction |
|--------------|-------------------|
| Tilted | Down toward composure = 0 |
| Shaken | Toward (0, 0) corner |
| Overheated | Toward (1, 0) corner |
| Overconfident | Right toward confidence = 1 |
| Timid | Left toward confidence = 0 |
| Detached | Toward (0, 1) corner |

Combined movement per tick: `event_force + recovery_force + gravity_force`.

---

## 11. Tuning & Experiments

### Target Distributions (PRD)

| Band | Target | Definition |
|------|--------|------------|
| Baseline | 70-85% | penalty_strength < 0.10 |
| Medium | 10-20% | 0.10 <= penalty_strength < 0.50 |
| High | 2-7% | 0.50 <= penalty_strength < 0.75 |
| Full Tilt | 0-2% | penalty_strength >= 0.75 |

### Tunable Parameters

14 parameters organized into three categories, managed via `experiments/tuning/zone_parameter_tuner.py`:

| Category | Parameters |
|----------|------------|
| Penalty Thresholds | `PENALTY_TILTED_THRESHOLD` (0.35), `PENALTY_OVERCONFIDENT_THRESHOLD` (0.90), `PENALTY_TIMID_THRESHOLD` (0.10), etc. |
| Zone Radii | `ZONE_POKER_FACE_RADIUS` (0.16), `ZONE_GUARDED_RADIUS` (0.15), etc. |
| Recovery Constants | `RECOVERY_BELOW_BASELINE_FLOOR` (0.60), `RECOVERY_ABOVE_BASELINE` (0.80), `GRAVITY_STRENGTH` (0.03) |

Override at runtime:
```python
from poker.player_psychology import set_zone_params, get_zone_param
set_zone_params({'RECOVERY_BELOW_BASELINE_FLOOR': 0.70})
```

Or via experiment config JSON (`zone_params` key).

### Experiment Infrastructure

- **Schema v71**: 15 columns in `player_decision_analysis` track zone state and effects per decision
- **ZoneMetricsAnalyzer** (`experiments/analysis/`): Zone distributions, tilt frequencies, transitions
- **ZoneReportGenerator**: Markdown reports comparing results to PRD targets
- **Experiment configs**: `experiments/configs/zone_validation.json` and tuning variants

```bash
python experiments/run_from_config.py experiments/configs/zone_validation.json
```

---

## 12. Key Files

| File | Purpose |
|------|---------|
| `poker/player_psychology.py` | Core system: anchors, axes, zones, zone effects, recovery, gravity |
| `poker/expression_filter.py` | Visibility calculation, emotion dampening |
| `poker/range_guidance.py` | Looseness-to-range mapping, position clamps |
| `poker/prompt_config.py` | `zone_benefits` and `tilt_effects` toggles |
| `poker/prompts/decision.yaml` | Zone strategy templates (24 energy-variant templates) |
| `poker/controllers.py` | Zone context building, guidance injection into prompts |
| `config/game_modes.yaml` | Per-mode psychology toggles (pro mode disables tilt_effects) |
| `experiments/psychology_balance_simulator.py` | Standalone simulation tool |
| `experiments/analysis/` | ZoneMetricsAnalyzer, ZoneReportGenerator |
| `experiments/tuning/` | ZoneParameterTuner, tunable parameter definitions |
| `tests/test_psychology_v2.py` | Psychology unit tests |
| `tests/test_psychology_zones.py` | Zone detection unit tests |
| `docs/technical/PSYCHOLOGY_ZONES_MODEL.md` | Detailed zone geometry spec |
