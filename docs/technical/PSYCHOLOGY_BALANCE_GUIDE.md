# Psychology System Balance Guide

This document provides guidance on tuning the psychology system parameters based on simulation analysis.

## Target Emotional Distribution

| State | Composure Range | Target % | Description |
|-------|-----------------|----------|-------------|
| Focused | ≥0.8 | 0-5% | Peak performance (rare) |
| Alert | 0.6-0.8 | 65-80% | Normal play state |
| Rattled | 0.4-0.6 | 15-25% | Feeling pressure |
| Tilted | <0.4 | 2-7% | Emotional play (rare but impactful) |

**Note**: The current system naturally gravitates toward "alert" (recovery target is 0.7). "Focused" is a peak state achieved through winning streaks, not the default.

## Key Parameters

### 1. Event Frequency

The `event_probability` determines how often notable psychological events occur.

| Setting | Value | Use Case |
|---------|-------|----------|
| Conservative | 15% | Stable, methodical games |
| Moderate | 25% | **Recommended default** |
| Aggressive | 35% | High-drama, volatile games |

**Reality check**: In real poker, ~70% of hands are folded pre-flop with no emotional event. Only showdowns, big pots, and bluff confrontations should trigger events.

### 2. Recovery Rate

The `recovery_rate` controls how fast composure/confidence return to baseline.

| Setting | Value | Effect |
|---------|-------|--------|
| Very slow | 0.05 | Tilt persists 15-20 hands |
| Slow | 0.08 | Tilt persists 8-12 hands |
| **Default** | **0.12-0.15** | Tilt persists 5-8 hands |
| Fast | 0.20 | Tilt persists 3-5 hands |
| Very fast | 0.30 | Near-instant recovery |

**Guideline**: `recovery_rate ≈ 0.5 × event_probability` creates balanced dynamics.

### 3. Impact Multiplier (Global)

Scale all event impacts uniformly.

| Multiplier | Effect |
|------------|--------|
| 0.5x | Very stable - hard to tilt |
| 1.0x | **Current baseline** |
| 1.5x | More volatile - easier to tilt |
| 2.0x | High drama - requires fast recovery |

### 4. Poise (Per-Personality)

The `poise` anchor controls individual sensitivity to bad outcomes.

| Poise | Sensitivity | Personality Type |
|-------|-------------|------------------|
| 0.3 | 0.79x | Volatile (Phil Hellmuth) |
| 0.5 | 0.65x | Average |
| 0.7 | 0.51x | **Default** - stable |
| 0.9 | 0.37x | Very stable (Daniel Negreanu) |

**Formula**: `sensitivity = 0.3 + 0.7 × (1 - poise)`

## Recommended Configurations

### For Casual/Fun Play
```python
event_probability = 0.25  # Events in 1/4 of hands
recovery_rate = 0.15      # Moderate recovery
impact_multiplier = 1.0   # Standard impacts

# Result: ~70% alert, ~22% rattled, ~5% tilted
```

### For Competitive/Serious Play
```python
event_probability = 0.15  # Fewer drama events
recovery_rate = 0.20      # Faster recovery
impact_multiplier = 1.0   # Standard impacts

# Result: ~85% alert, ~12% rattled, ~2% tilted
```

### For High-Drama Entertainment
```python
event_probability = 0.35  # Frequent events
recovery_rate = 0.12      # Slow recovery
impact_multiplier = 1.5   # Amplified impacts

# Result: ~55% alert, ~35% rattled, ~8% tilted
```

## Event Impact Reference

Current base impacts (before sensitivity scaling):

| Event | Confidence | Composure | Category |
|-------|------------|-----------|----------|
| **Positive** |
| win | +0.08 | +0.05 | Standard win |
| big_win | +0.15 | +0.10 | Large pot win |
| successful_bluff | +0.20 | +0.05 | Bluff worked |
| **Negative** |
| small_loss | -0.03 | -0.02 | Minor loss |
| big_loss | -0.10 | -0.15 | Large pot loss |
| bluff_called | -0.20 | -0.10 | Ego blow |
| bad_beat | -0.05 | -0.25 | Variance pain |
| got_sucked_out | -0.05 | -0.30 | Highest composure hit |
| losing_streak | -0.15 | -0.20 | Cumulative pressure |

## Poker Face Zone (Phase 3)

The poker face zone is a 3D ellipsoid where emotions are masked:

**Zone Center** (universal):
- Confidence: 0.65
- Composure: 0.75
- Energy: 0.4

**Coverage**: ~7% of the total state space

**Implication**: Since most play happens at composure ~0.7 (alert), players will frequently be just outside the poker face zone, showing their emotions. The zone is designed to reward composure - only the most focused players maintain a true poker face.

## Tuning for Specific Goals

### "I want more tilt drama"
- Increase `event_probability` to 0.30-0.35
- Decrease `recovery_rate` to 0.08-0.10
- Consider increasing `impact_multiplier` to 1.5x

### "Tilt feels too sticky"
- Increase `recovery_rate` to 0.20-0.25
- Or decrease `event_probability` to 0.15

### "Players never seem rattled"
- Check if `poise` values are too high (>0.8)
- Decrease `recovery_rate`
- Consider increasing negative event impacts

### "Everyone's always tilted"
- Increase `recovery_rate`
- Decrease `event_probability`
- Check if pressure events are firing too frequently

## Simulation Tool

Use the balance simulator to test configurations:

```bash
python3 experiments/psychology_balance_simulator.py --sweep
```

This runs a parameter sweep and shows which configurations hit the 2-7% tilted target.

## Mathematical Model

The system is a mean-reverting stochastic process:

```
composure(t+1) = composure(t) + event_impact × sensitivity + (baseline - composure(t)) × recovery_rate
```

**Steady-state**: When event_impact averages to zero, composure converges to baseline (0.7).

**Variance**: Higher event frequency or slower recovery increases variance around the steady state.

**Tilt probability**: Proportional to `(negative_impact × sensitivity)² / recovery_rate`
