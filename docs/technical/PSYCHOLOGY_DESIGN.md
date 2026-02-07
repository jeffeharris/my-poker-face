---
purpose: Design philosophy, goals, and success metrics for the AI psychology system
type: design
created: 2025-06-15
last_updated: 2026-02-07
---

# Psychology System Design

## Purpose

The psychology system exists to create **novelty and variety** in AI poker play, not to simulate human psychology accurately. Every hand should feel a bit different — the AI isn't a solved GTO bot playing optimal strategy.

### Core Principles

1. **Novelty over Realism** — We don't expect AI to feel "truly human" (that would be boring). We want texture and variety in decisions.

2. **Competitive Foundation** — Psychology adds spice, not chaos. Skilled play should still win consistently.

3. **Tight Coupling** — What you see (avatar emotion, chat tone) matches what the AI "feels" internally. No performative emotions that don't affect behavior.

4. **Readable Opponents** — Players should be able to read and exploit AI emotional states over time. Expressiveness controls how much leaks through.

---

## The Three-Layer Model

```
PERSONALITY ANCHORS (personalities.json)
    │
    │  9 static traits define identity
    │  (ego, poise, baseline_aggression, etc.)
    │
    ▼
EMOTIONAL AXES (runtime state)
    │
    │  3 dynamic axes pushed by pressure events,
    │  filtered through sensitivity anchors:
    │  • confidence (0-1): belief in reads/decisions
    │  • composure (0-1): emotional regulation
    │  • energy (0-1): engagement/intensity
    │
    ▼
ZONE DETECTION (computed, stateless)
    │
    │  2D space (confidence × composure) defines:
    │  • Sweet spots: Poker Face, Guarded, Commanding, Aggro
    │  • Penalty zones: Tilted, Shaken, Overheated, etc.
    │
    ▼
PROMPT MODIFICATION (information access)
    │
    │  Zones control what info the AI sees:
    │  • Sweet spots grant strategic bonuses
    │  • Penalty zones degrade decisions
    │  • Energy flavors the manifestation
    │
    ▼
EXPRESSION FILTER → AVATAR / TABLE TALK
```

**Key insight:** Anchors define gravity wells, axes move freely, zones modify information access. The AI still makes its own decisions — zones shape what it knows, not what it must do.

**Energy is special:** It affects *how* a zone manifests (flavor/tempo), not *which* zone applies. Exception: Poker Face is a 3D ellipsoid where energy extremes break the mask.

For full architecture details, see [PSYCHOLOGY_OVERVIEW.md](PSYCHOLOGY_OVERVIEW.md). For zone geometry and effects, see [PSYCHOLOGY_ZONES_MODEL.md](PSYCHOLOGY_ZONES_MODEL.md).

---

## Design Decisions

### Why Anchors + Axes (Not Elastic Traits)

The original system used elastic traits (aggression, bluff_tendency, chattiness, emoji_usage) that were pushed within bounds by events. This was replaced because:

- **Trait-based modifiers were noisy** — Four traits changing independently made behavior unpredictable in uninteresting ways
- **No clear "emotional space"** — Couldn't map trait combinations to recognizable emotional states
- **Tilt was bolted on** — Separate tilt system with its own float didn't integrate with personality

The current anchor + axis model gives:
- **Legible emotional states** — 2D space maps to recognizable quadrants (commanding, shaken, etc.)
- **Personality-filtered responses** — Same event affects Batman and Gordon Ramsay differently through ego/poise sensitivity
- **Natural recovery** — Axes drift toward personality-defined baselines, not arbitrary centers

### Why Zones (Not Continuous Modifiers)

Zones create **discrete, recognizable states** rather than smooth gradients:

- Players can learn "Batman is tilted" or "Napoleon is commanding" — clear states to exploit
- Zone effects (intrusive thoughts, info removal) are more impactful than small continuous modifiers
- Sweet spots reward stability; penalty zones punish extremes — creates meaningful dynamics

### Why Resolution Rules (Not Additive Stacking)

Without resolution rules, a single dramatic hand could produce 5+ events that stack into extreme axis swings. The resolution model:

- **ONE outcome** prevents `big_win + win + headsup_win` from triple-counting
- **Ego events at 50%** prevents `successful_bluff + nemesis_win` from over-rewarding
- **Equity shocks skip confidence** because luck events shouldn't make you doubt your reads
- **Pressure events stack** because fatigue genuinely accumulates

### Why a Unified Pipeline

Before T3-55, the psychology cycle (detect → resolve → persist → recover → save) was implemented separately in `game_handler.py` and `run_ai_tournament.py`. This caused recurring divergences:
- Events detected in one but not the other
- Different recovery rates
- Missing state persistence

The unified `PsychologyPipeline` class ensures both codepaths use identical logic, with configuration flags for UI-specific concerns.

---

## Goals & Success Metrics

### 1. Novelty (Variety in Play)

**Goal:** Each AI shows meaningful decision variety, not robotic consistency.

**Metric:** Zone distribution per player

```sql
SELECT
    player_name,
    zone_primary_sweet_spot,
    COUNT(*) as decisions,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY player_name), 1) as pct
FROM player_decision_analysis
WHERE experiment_id = ?
GROUP BY player_name, zone_primary_sweet_spot
```

**Target:** Players should spend majority time near their home zone but visit 2-3 other zones. No player should be 100% in one zone.

### 2. Competitive Foundation (Skill Matters)

**Goal:** Good decisions should lead to better outcomes. Psychology adds noise, not chaos.

**Metric:** Penalty zone correlation with decision quality

```sql
SELECT
    CASE
        WHEN zone_total_penalty_strength >= 0.50 THEN 'high_penalty'
        WHEN zone_total_penalty_strength >= 0.10 THEN 'moderate_penalty'
        ELSE 'baseline'
    END as penalty_band,
    AVG(CASE WHEN decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistake_rate,
    COUNT(*) as decisions
FROM player_decision_analysis
WHERE experiment_id = ?
GROUP BY penalty_band
```

**Target:** Clear positive correlation between penalty strength and mistake rate. 2-3x baseline mistake rate at high penalty.

### 3. Tight Coupling (Emotion = Behavior)

**Goal:** Visible emotional state predicts actual behavior changes.

**Metric:** Zone state correlation with aggression and looseness modifiers

```sql
SELECT
    zone_primary_sweet_spot,
    AVG(zone_confidence) as avg_confidence,
    AVG(zone_composure) as avg_composure,
    AVG(CASE WHEN action_taken = 'raise' THEN 1 ELSE 0 END) as raise_rate
FROM player_decision_analysis
WHERE experiment_id = ?
GROUP BY zone_primary_sweet_spot
```

**Target:** Commanding zone shows higher raise rate than Guarded. Tilted shows erratic patterns.

### 4. Balanced Distribution (Not Always Extreme)

**Goal:** Extreme emotional states are rare and dramatic, not constant.

**Metric:** Penalty strength distribution

```sql
SELECT
    CASE
        WHEN zone_total_penalty_strength >= 0.75 THEN 'full_tilt'
        WHEN zone_total_penalty_strength >= 0.50 THEN 'high'
        WHEN zone_total_penalty_strength >= 0.10 THEN 'medium'
        ELSE 'baseline'
    END as band,
    COUNT(*) as cnt,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
FROM player_decision_analysis
WHERE experiment_id = ?
GROUP BY band
```

**Target Distribution (PRD):**

| Band | Target % | Definition |
|------|----------|------------|
| Baseline | 70-85% | penalty_strength < 0.10 |
| Medium | 10-20% | 0.10 <= penalty_strength < 0.50 |
| High | 2-7% | 0.50 <= penalty_strength < 0.75 |
| Full Tilt | 0-2% | penalty_strength >= 0.75 |

### 5. Personality Distinctiveness

**Goal:** Different AI characters behave differently.

**Metric:** Home zone alignment

```sql
SELECT
    player_name,
    zone_primary_sweet_spot,
    COUNT(*) as cnt,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY player_name), 1) as pct
FROM player_decision_analysis
WHERE experiment_id = ?
  AND zone_primary_sweet_spot IS NOT NULL
GROUP BY player_name, zone_primary_sweet_spot
ORDER BY player_name, cnt DESC
```

**Target:** Each personality spends the most time in their expected home zone. Batman → Poker Face, Gordon Ramsay → Aggro, Napoleon → Commanding, Bob Ross → Guarded.

---

## Validation Checklist

Before shipping psychology changes:

1. **Run penalty distribution query** — Verify target distribution (70-85% baseline)
2. **Check penalty → mistake correlation** — Should be 2-3x at high penalty
3. **Verify personality distinctiveness** — Different characters, different home zones
4. **Run experiment** — `python experiments/run_from_config.py experiments/configs/zone_validation.json`
5. **Generate report** — Use `ZoneReportGenerator` to compare against PRD targets
6. **Playtest for "feel"** — Does it add novelty without feeling random?

---

## Related Documentation

- [PRESSURE_EVENTS.md](PRESSURE_EVENTS.md) — Event catalog, impacts, and resolution rules
- [PSYCHOLOGY_OVERVIEW.md](PSYCHOLOGY_OVERVIEW.md) — Full system architecture
- [PSYCHOLOGY_ZONES_MODEL.md](PSYCHOLOGY_ZONES_MODEL.md) — Zone geometry and effects
- [EQUITY_PRESSURE_DETECTION.md](EQUITY_PRESSURE_DETECTION.md) — Equity-based events
