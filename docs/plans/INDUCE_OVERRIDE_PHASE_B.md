---
purpose: Phase B — extend the induce_override rule with proper street-resolved barrel tracking, confidence-scaled mixing, broader hand classes, and OOP support
type: design
created: 2026-05-15
last_updated: 2026-05-15
---

# Induce Override — Phase B

## Context

[Phase A](INDUCE_OVERRIDE_PHASE_A.md) ships a narrow facing-bet IP
induce rule with a fixed 0.85 call / 0.15 raise mix on a binary gate.
The Phase A gate uses today's available proxy
(`aggression_factor_postflop >= 3.0` AND
`cbet_attempt_rate >= 0.70`) which is sufficient to test the premise
but cannot distinguish a multi-street barreler from a player who jams
preflop a lot and never sees a flop. The fixed mix is also insensitive
to signal confidence — a barreler signal from 10 samples gets the same
treatment as one from 100.

Phase B does not start until Phase A clears its testable hypothesis
(see [Phase A — Decision Points](INDUCE_OVERRIDE_PHASE_A.md#decision-points)).
Specifically, Phase B is **only worth doing if `induce_followup_barrel_rate`
in Phase A is high enough to indicate the premise is correct, but the
fixed mix is leaving value on the table or the proxy is firing
inconsistently**. If Phase A misses with low followup-barrel rate, the
right next step is *not* Phase B — the rule needs to be reconceived.

Scope of Phase B, broken into items so each can ship and validate
independently:

1. **`barrel_frequency` stat** — proper street-resolved signal on
   `OpponentTendencies`.
2. **Confidence-scaled mixing** — call probability ramps with sample
   confidence and signal magnitude (pattern matches
   `compute_pattern_intensity` in `exploitation.py:792`).
3. **`strong_made` inclusion** — extend the rule to vulnerable made
   hands on dry boards.
4. **Open-spot IP induce** — separate branch for the OOP-check-then-
   barrel exploit. Requires `TrapBaitBot` opponent.
5. **OOP induce** — check-raise tech (the harder branch).
6. **Personality-aware intensity** — different hero archetypes use
   different trap frequencies.

Each item has its own testable hypothesis. They are sequenced so
earlier items reduce risk for later ones; do not parallel-ship.

## Item 1 — `barrel_frequency` stat

### Problem

Today's `OpponentTendencies` carries `aggression_factor_postflop`,
which aggregates bet+raise+all-in vs call across all postflop streets.
A maniac who jams preflop and folds postflop has high `AF_pf` once
they get to a postflop spot, but they don't actually barrel. The proxy
fires false positives.

### Design

Add to `poker/memory/opponent_model.py`:

```python
# OpponentTendencies new fields
barrel_frequency: float = 0.5         # bet rate on turn after being called on flop
barrel_opportunities: int = 0          # count of "called flop, now choosing turn action as PFR"
third_barrel_frequency: float = 0.5    # bet rate on river after being called on turn
third_barrel_opportunities: int = 0

# Counters (private)
_barrel_count: int = 0
_barrel_opportunity_count: int = 0
_third_barrel_count: int = 0
_third_barrel_opportunity_count: int = 0
```

Wire from `CbetDetector` (or its sibling) when the phase transitions
flop → turn and the preflop aggressor (a) c-bet the flop, (b) was
called, (c) is acting on the turn. Pattern is parallel to the existing
`update_cbet_attempt(...)` / `update_fold_to_cbet(...)` methods. Same
"neutral prior 0.5 until first opportunity" stance.

Aggregation up to `AggregatedOpponentStats`:

```python
barrel_frequency: float = 0.5
barrel_opportunities: int = 0
third_barrel_frequency: float = 0.5
third_barrel_opportunities: int = 0
```

Surface these to the existing playstyle/archetype detectors. No
behavior changes from Item 1 alone — purely additive tracking.

### Testable Hypothesis (Item 1)

> Adding `barrel_frequency` tracking will, over a 500-hand HU run of
> TieredBot vs ManiacBot, produce `barrel_frequency >= 0.80` and
> `barrel_opportunities >= 30` in the opponent model — i.e. the stat
> measurably converges to ManiacBot's actual ~100% barrel behavior
> within sample.

Validation: run any HU experiment with TieredBot vs ManiacBot, dump
the final `OpponentTendencies` for the villain, check the field.

This is a **plumbing-correctness hypothesis**, not a behavioral one —
Item 1 is preparatory work for Item 2.

### Effort

~80 lines plus tests in `poker/memory/opponent_model.py` and a
counter-wiring update in the phase transition detector. Pattern is
fully established by `cbet_attempt_rate` (Phase 8.1a).

## Item 2 — Confidence-scaled mixing

### Problem

Phase A uses a fixed 0.85 call / 0.15 raise mix on a binary gate. Once
Item 1 ships, the proper signal (`barrel_frequency`) supports a
meaningful ramp on two axes:

- **Sample confidence**: 10 opportunities → soft mix; 50+ → full trap.
- **Signal magnitude**: `barrel_frequency = 0.60` → minimal trap;
  `0.85+` → maximal trap.

### Design

Replace the `induce_override.py` redistribution logic with a
confidence-aware ramp. Pattern from `exploitation.py:compute_pattern_intensity`:

```python
def _induce_call_probability(stats) -> float:
    rate_intensity = _ramp(stats.barrel_frequency, 0.60, 0.85)
    sample_confidence = _barrel_sample_confidence(stats.barrel_opportunities)
    intensity = rate_intensity * sample_confidence
    # Map intensity [0, 1] to call probability [0.70, 0.90]
    return 0.70 + intensity * (0.90 - 0.70)

def _barrel_sample_confidence(opportunity_count: int) -> float:
    return _ramp(float(opportunity_count), 10.0, 50.0)
```

The 0.70 lower bound prevents the rule from degrading toward
value_override's 0.50 at low confidence — if the gate fires at all,
we're at least mildly trapping. The 0.90 upper bound preserves the
unexploitability tax against adaptive opponents.

The gate itself moves from
`aggression_factor_postflop AND cbet_attempt_rate` to:

```python
stats.barrel_frequency >= 0.60
AND stats.barrel_opportunities >= 10
AND NOT _is_passive_with_jams(stats)
AND NOT _is_hyper_passive(stats)
# ... other Phase A gates carry over (street, IP, dry board, etc.)
```

`aggression_factor_postflop` falls out as a gate input — it's no
longer needed once we have the direct signal.

### Testable Hypothesis (Item 2)

> Replacing the binary AF_pf×cbet gate + fixed 0.85 mix with the
> barrel_frequency-based gate + confidence-scaled mix will:
>
> - **(B2-H1)** Maintain or improve bb/100 vs ManiacBot relative to
>   Phase A's shipped numbers, with `induce_fire_rate` at least 10%
>   higher (the new gate fires on a slightly wider set of spots
>   because `barrel_frequency=0.85` is a more specific signal than
>   `AF_pf=3.0`).
> - **(B2-H2)** Reduce `induce_fire_rate` against TAG/nit by at
>   least 50% relative to Phase A, because TAG's barrel_frequency
>   (~0.45) is below the 0.60 threshold while their AF_pf can spike
>   above 3.0.
> - **(B2-H3)** Show measurable correlation between
>   `induce_call_probability` (decision-level) and downstream chip
>   delta — higher trap intensity should correlate with higher
>   expected value on hands where the rule fires.

(B2-H3) is the new diagnostic and the reason confidence-scaling is
worth the cost. If trap intensity *doesn't* correlate with chip
delta, the ramp adds complexity without value and we revert to fixed
mix.

### Decision Points

| Outcome | Decision |
|---|---|
| All three (B2-H*) hold | Ship Item 2. Move to Item 3. |
| (B2-H1) holds, (B2-H2) fails (still firing on TAG) | Investigate ramp curve — likely the 0.60 lower threshold is too permissive. Re-run with 0.70 threshold. |
| (B2-H3) fails (no correlation) | Revert to fixed mix. Ramp adds bugs without value. Reconsider whether `barrel_frequency` alone is the right signal. |
| (B2-H1) regresses | Either the new gate misses spots Phase A caught, or the lower-confidence soft-mix is leaking value. Diagnose via per-spot ablation. |

## Item 3 — `strong_made` inclusion

### Problem

Phase A is `nuts`-only. This excludes legitimate trap candidates like
top set on a one-flush board (currently classified `strong_made` with
one danger flag), which on a strict equity basis still benefit from
slow-playing vs a barreler.

### Design

Extend the gate from `hand_class == 'nuts'` to
`hand_class in ('nuts', 'strong_made')` with stricter board-texture
gates for `strong_made`:

- `nuts`: existing gate (`danger_flags <= 1`)
- `strong_made`: `danger_flags == 0` (fully dry) AND
  `node.outs_to_being_beaten <= 3` (or equivalent — depends on
  postflop classifier API)

The narrower texture gate compensates for the increased turn-card
risk on non-nut hands.

### Testable Hypothesis (Item 3)

> Extending induce to `strong_made` hands with stricter texture gates
> will produce ≥ +2 bb/100 incremental lift vs ManiacBot (on top of
> Phase A's shipped baseline) without breaching the (H2) leak floor
> on any non-barreler matchup.

### Decision Points

| Outcome | Decision |
|---|---|
| Incremental lift ≥ +2 bb/100, no (H2) breach | Ship Item 3. |
| Incremental lift < +1 bb/100 | Don't ship — the firing surface is real but the EV per firing is marginal. Existing system handles these spots adequately. |
| (H2) breach on any matchup | Texture gate is too permissive. Tighten to `danger_flags == 0 AND nut_status == 'second_nuts_or_better'` and re-run. |

## Item 4 — Open-spot IP induce

### Problem

Phase A is facing-bet only. The open-spot IP exploit (villain checks
OOP, hero IP free to act) is a real but distinct line: a TrapBaitBot-
class opponent that checks flop ~70% OOP and then barrels turn/river
hard would never trigger Phase A's facing-bet gate.

This is the original scope of the [first Phase A draft](INDUCE_OVERRIDE_PHASE_A.md#context),
re-introduced once the facing-bet version is shipped and validated.

### Design

Add a parallel branch in `induce_override.py`:

```python
def apply_induce_override(strategy, ...):
    if facing_bet:
        return _facing_bet_induce(strategy, ...)
    if open_spot:
        return _open_spot_induce(strategy, ...)
    return strategy, no_op_trace()
```

Open-spot gate is similar to facing-bet but additionally requires:
- A new stat: `flop_check_then_barrel_rate` — rate at which the
  villain checks flop OOP then bets turn after being checked back.
  (Item 1 plumbing extended.)
- Higher confidence threshold (this is a rarer pattern, sample
  builds slowly).

Open-spot redistribution: `check = 0.70`, `raise = 0.30` split across
available raises. (Same numbers as the original Phase A draft.)

### Testable Hypothesis (Item 4)

> Adding open-spot IP induce, tested vs a new `TrapBaitBot` opponent
> (checks flop ~70% OOP, barrels turn ~80%, river ~50%), will produce
> ≥ +5 bb/100 lift vs TrapBaitBot with the rule ON vs ablated, and
> ≤ -2 bb/100 leak against ManiacBot/CaseBot/GTO-Lite/ABCBot. (Same
> shape as Phase A, just with a different villain target.)

### Dependency

Requires `TrapBaitBot` (see [Parallel Work](#parallel-work)) — a new
rule_bot strategy. Pattern is in
`poker/rule_strategies.py:_strategy_maniac`. Sample implementation:

```python
def _strategy_trap_bait(context: Dict) -> Dict:
    """Check flop OOP to set the trap; barrel turn and river hard.
    Tests if AI exploits the OOP check-then-barrel line."""
    phase = context.get('phase', 'PRE_FLOP')
    position = context.get('position', 'IP')
    equity = context.get('equity', 0.5)
    cost = context['cost_to_call']
    rng_value = context.get('rng_value', 0.5)  # deterministic via seed

    # FLOP OOP first-to-act: check 70% of the time regardless of equity
    if phase == 'FLOP' and position == 'OOP' and cost == 0:
        if rng_value < 0.70:
            return {'action': 'check', 'raise_to': 0}
        # 30%: small c-bet, sized to look like a probe
        # ... (delegate to maniac-style sizing)

    # TURN and RIVER: barrel like a maniac unless completely dead
    # ... (delegate to maniac strategy)
```

## Item 5 — OOP induce (check-raise tech)

The hardest branch. Hero OOP with nuts on flop, villain IP, hero
checks intending to check-raise villain's c-bet. Requires:

- Detection of "we're OOP this street and villain has cbet history"
- A two-decision sequence (check → check-raise) where the check is
  recorded so the subsequent raise decision knows it's the trap leg
- Bluff/value protection: pure check-raise from nuts is too
  obvious; need to mix in occasional check-fold (with weak hands) and
  check-call (with marginal hands) to balance the line — but that
  goes beyond the induce rule's scope and into general OOP strategy.

Defer until Items 1–4 are validated. The poker premise is correct
(check-raising nuts vs an OOP c-bettor extracts more value than
leading) but the engineering surface is large enough that it deserves
its own design pass.

## Item 6 — Personality-aware intensity

### Problem

Phase A applies the same redistribution regardless of hero's
personality archetype. A LAG hero who already plays aggressively
benefits less from adding more passive lines (they're already
disguised); a nit hero benefits more from occasional trap mixing
(adds balance to an otherwise predictable strategy).

### Design

Multiply the Item 2 confidence-scaled intensity by an
archetype-dependent factor:

```python
ARCHETYPE_INDUCE_SCALE = {
    'nit': 1.0,     # full trap intensity — nits benefit most from balance
    'tag': 1.0,     # full trap
    'lag': 0.6,     # partial — already disguised
    'maniac': 0.4,  # partial — adds discipline they otherwise lack
    'rock': 1.0,    # full
    'station': 1.0, # full
}
```

These are placeholder values — Item 6's experimental work is tuning
them.

### Testable Hypothesis (Item 6)

> Applying archetype scaling to the trap intensity will produce no
> regression for any archetype (each archetype's bb/100 vs ManiacBot
> stays at or above its post-Item-2 baseline) and will show at least
> one archetype improving by ≥ +3 bb/100 vs its non-scaled baseline.

If no archetype benefits, the scaling adds tuning surface without
value and we revert.

## Counter-adaptation (out of scope for B, noted for completeness)

Once an opponent (LLM-driven, future TieredBot, or another rule_bot
calibrated against TieredBot) observes the trap line consistently,
they should counter-adapt: check the turn after our flop call instead
of barreling. The 0.15 minimum raise frequency in Phase A is the
known floor against this — even a perfect adapter can't pure-fold
turn because we still raise sometimes.

A full counter-adaptation defense requires:

- Detection of "this villain has stopped barreling after our calls"
  (a meta-statistic over the per-opponent model)
- Conditional un-trap: when detected, fall back to value_override's
  50/50

This is Phase C work, after Phase B ships and after we have an
adaptive opponent in the test matrix.

## Parallel Work

Useful to build alongside Phase B but not required for any single
item:

- **`TrapBaitBot`** — required for Item 4. Build during Item 1
  plumbing so it's available when Item 4 starts.
- **`AdaptiveTrapBot`** — a `rule_bot` that *starts* like ManiacBot
  and switches to checking the turn after being called on flop ≥ 3
  times in the same match. Tests Phase C counter-adaptation logic
  and provides a live adversarial signal during Phase B.

## Decision Points

After each item:

- Item 1 (`barrel_frequency` stat): plumbing test. Pass → Item 2.
  Fail (stat doesn't converge) → debug, do not advance.
- Item 2 (confidence-scaled mixing): main behavioral test. Pass →
  ship and proceed to Item 3. Fail → revert ramp, stay on fixed
  mix.
- Item 3 (`strong_made` inclusion): incremental EV test. Pass →
  ship. Fail → don't widen, stay nuts-only.
- Item 4 (open-spot induce): new branch test. Requires TrapBaitBot.
  Pass → ship. Fail → kill open-spot branch entirely.
- Item 5 (OOP induce): design-only for Phase B. Implementation
  pushed to Phase B+ once shape is validated.
- Item 6 (personality-aware intensity): tuning test. Pass → ship.
  Fail → revert; no harm done.

After Phase B (whichever subset ships), Phase C covers
counter-adaptation against learning opponents.

## Open Questions

- Should `barrel_frequency` track only PFR-as-aggressor barrels, or
  any postflop bet-after-call? PFR-only is the conventional poker
  metric, but a non-PFR aggressor who donk-bets and barrels is the
  same exploit opportunity. Probably both, recorded as separate
  counters.

- Does the existing `OpponentSpot` infrastructure support per-spot
  barrel tracking? Item 1 may need to extend it.

- For Item 6, the archetype mapping treats "nit" and "tag" the same
  (full trap). Is that right? Or should nits trap less because they
  rarely *get* to the trap spot (their range is too narrow to land
  on flop nuts often enough for trap mixing to matter)?

- Item 5 (OOP check-raise) intersects with general OOP strategy in
  a way the other items don't. Worth a separate design doc when it
  comes up rather than tucking into induce_override.

## Related Plans

- [Phase A — Induce Override](INDUCE_OVERRIDE_PHASE_A.md) — the
  prerequisite. Phase B does not start until Phase A's testable
  hypothesis clears.
- [Phase 6 — Opponent Exploitation](PHASE_6_OPPONENT_EXPLOITATION.md)
- [Phase 7.5 — Adjustment Layer Widening](PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md)
- [Phase 7.6 — Intervention Trace](PHASE_7_6_INTERVENTION_TRACE.md)
- [Phase 8.1 — Tracking & Hyper-Passive](PHASE_8_1_TRACKING_AND_HYPER_PASSIVE.md)
- [TieredBot Decision Quality](TIEREDBOT_DECISION_QUALITY.md)
