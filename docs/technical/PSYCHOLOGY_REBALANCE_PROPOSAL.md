# Psychology System Rebalancing Proposal

Based on simulation analysis, the current system is too stable. This document proposes specific changes to achieve the target emotional distribution.

## Current State

**Problem**: 100% of decisions in historical data show "alert" composure (0.6-0.8). No rattled or tilted states occur.

**Root Cause**:
1. Every hand fires a "win" event (+0.05 composure after sensitivity)
2. Recovery pulls toward 0.7 every hand (recovery_rate × distance_from_0.7)
3. Result: System oscillates tightly around 0.7

## Target Distribution

| State | Composure Range | Target % |
|-------|-----------------|----------|
| Focused | ≥0.8 | 0-5% |
| Alert | 0.6-0.8 | 65-75% |
| Rattled | 0.4-0.6 | 15-25% |
| Tilted | <0.4 | 3-7% |

## Proposed Changes

### Option A: Filter Routine Events (Recommended)

**Change**: Only fire "win" events for significant wins (pot > 5 BB).

**Rationale**: Most poker hands are routine. Winning a 2 BB pot shouldn't boost confidence. Only notable victories should matter.

```python
# In pressure_detector.py, detect_showdown_events()
# Add minimum pot threshold for win event
MIN_WIN_POT_BB = 5  # Only track wins > 5 BB

pot_in_bb = pot_total / big_blind
if pot_in_bb >= MIN_WIN_POT_BB:
    events.append(("win", winner_names))
```

**Expected Result**: ~25% of hands have psychological events → 5% tilted

### Option B: Reduce Recovery Frequency

**Change**: Only apply recovery every N hands, not every hand.

```python
# In PlayerPsychology, track hands since recovery
if self.hand_count % 3 == 0:  # Recover every 3 hands
    self.recover()
```

**Expected Result**: Emotional states persist longer, more time in rattled/tilted

### Option C: Amplify Impact Magnitudes

**Change**: Increase negative event impacts by 1.5x.

```python
# In _get_pressure_impacts()
'bad_beat': {'confidence': -0.08, 'composure': -0.38},      # Was -0.25
'got_sucked_out': {'confidence': -0.08, 'composure': -0.45}, # Was -0.30
'bluff_called': {'confidence': -0.30, 'composure': -0.15},   # Was -0.20
```

**Expected Result**: Big events have bigger effect, faster path to tilt

## Recommendation

**Implement Option A first** (filter routine events).

This is the most realistic change - it aligns with actual poker psychology where small pots don't matter emotionally. It's also the safest change since it doesn't modify the core mechanics.

### Implementation Steps

1. Add `MIN_NOTABLE_WIN_BB = 5` constant to `pressure_detector.py`
2. Modify `detect_showdown_events()` to filter small wins
3. Add `MIN_NOTABLE_LOSS_BB = 5` for consistency
4. Run experiment to validate distribution

### Validation Experiment

Run a 10-tournament experiment with:
- 6 players, 50 hands each (3000 decisions)
- Measure composure distribution across all decisions
- Target: 3-7% tilted, 15-25% rattled

```bash
docker compose exec backend python -m experiments.run_ai_tournament \
    --experiment psychology_balance_test \
    --tournaments 10 --hands 50 --players 6
```

Query results:
```sql
SELECT
  CASE
    WHEN elastic_composure >= 0.8 THEN 'focused'
    WHEN elastic_composure >= 0.6 THEN 'alert'
    WHEN elastic_composure >= 0.4 THEN 'rattled'
    ELSE 'tilted'
  END as composure_band,
  COUNT(*) as cnt,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
FROM player_decision_analysis
WHERE experiment_id = (SELECT id FROM experiments WHERE name = 'psychology_balance_test')
GROUP BY composure_band;
```

## Poker Face Zone Integration

The poker face zone (Phase 3) should work with these changes:

- Zone center at (conf=0.65, comp=0.75, energy=0.4)
- Most players hover around (0.5, 0.7, 0.5) - just outside the zone
- After wins, players may briefly enter the zone
- After losses, players move further from the zone (more readable)

This creates a natural dynamic where:
- Winning players are harder to read (they enter the poker face zone)
- Losing players are easier to read (they're clearly rattled)

## Testing the Balance

Use the simulator to test configurations:

```bash
python3 experiments/psychology_balance_simulator.py --sweep
```

Look for configurations where:
- Tilted = 3-7%
- Rattled = 15-25%
- Tilt duration = 3-8 hands

Good configurations from simulation:
- `event_rate=0.25, recovery=0.15, impact_mult=1.5` → 5.2% tilted
- `event_rate=0.35, recovery=0.20, impact_mult=1.5` → 4.9% tilted
