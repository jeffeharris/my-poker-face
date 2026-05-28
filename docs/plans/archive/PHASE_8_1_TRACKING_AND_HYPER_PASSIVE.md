---
purpose: Follow-up to Phase 8 — extend opponent tracking and resolve the hyper_passive vs station interaction
type: design
created: 2026-05-14
last_updated: 2026-05-14
---

# Phase 8.1: Tracking gaps + hyper_passive co-fire

## Context

[Phase 8](PHASE_8_PLAYSTYLE_RULE_FAMILIES.md) shipped two playstyle-
gated rule families:

- `value_vs_station` — fully enabled for `nit` / `rock` / `tag`.
- `steal_pressure` — shipped as **piping + diagnostics only** because
  the proper signal (`fold_to_open`) isn't tracked yet and the VPIP+PFR
  proxy is too weak to ship behaviorally.

Phase 8's Risk #1 also flagged that **most of the TAG-vs-CaseBot drain
(~−2400 BB across 6-max sims)** is `hyper_passive`'s fold-mass
reduction firing on marginal hands against a station that occasionally
jams — `value_vs_station` adds extraction on strong hands but doesn't
plug that leak. Phase 8 v1 ships option 3 of Risk #1 (defer,
instrument, decide from data); this plan captures the follow-up work.

The two themes are linked because the right fix for `hyper_passive`
spew likely uses signals we don't yet capture per-opponent (jam-rate
in the spot context where it matters, fold rates by line).

## Scope (gated on Phase 8 v1 diagnostics)

This doc is a **design sketch**, not a queued implementation. Most
items here should only ship after Phase 8 v1 runs across a 6-max sim
and the diagnostic counters tell us where to invest.

Concretely, before starting any work here, look at:

```
manager._exploitation_counters:
  steal_pressure_eligible_<archetype>
  steal_pressure_enabled_eligible_<archetype>
  steal_pressure_diagnostic_only_<archetype>

  value_vs_station_fired_<archetype>
  value_vs_station_superseded_by_override_<archetype>
```

If `steal_pressure_diagnostic_only_*` is sparse across the sim, the
rule isn't worth enabling — don't bother adding `fold_to_open`. If
`value_vs_station_superseded_by_override_*` is high, the rule isn't
contributing on its current trigger surface and we should look at
why before adding more rules around it.

## Items

### 1. `cbet_attempt_rate` (low-hanging fruit)

`OpponentTendencies` tracks `fold_to_cbet` but not the dual signal:
"when this opponent was the PFR and saw the flop, how often did they
actually c-bet?" The `CbetDetector` already knows the preflop
aggressor and whether they saw the flop — adding the counter is the
same pattern as `_cbet_faced_count` / `_fold_to_cbet_count`.

A LAG with `cbet_attempt_rate=0.85` vs a passive PFR with
`cbet_attempt_rate=0.20` are very different opponents at the same
VPIP. CaseBot would score near 0 here (its `aggression_factor=0.4`
on raises is mostly preflop opens it never follows up), which is a
much cleaner signal than the indirect `vpip × AF` combo we use today.

**Cost**: ~30 lines plus tests. Pattern is `_cbet_faced_count` →
`_postflop_seen_as_pfr_count` and `_fold_to_cbet_count` →
`_cbet_attempt_count`. Wire from `CbetDetector` when phase
transitions to FLOP and the preflop aggressor is still active.

**Payoff**: cleaner station detection (current `hyper_passive` gate
trips on CaseBot at every flop; `hyper_passive AND cbet_attempt_rate
< 0.30` is a *real* station-like-pfr signal we can act on
differently from a station who never opens).

### 2. `fold_to_open` / `fold_to_3bet`

These are the proper signals for `steal_pressure`. The VPIP+PFR proxy
in Phase 8 v1 is documented as weak in the rule's docstring and the
PHASE_8 plan body — `fold_to_open` would let us drop the PFR upper
bound entirely and just gate on observed fold behavior.

**Implementation pattern** (extends the same `update_from_action`
pipeline `_facing_bet_opportunities` already uses):

```python
# New counters on OpponentTendencies
_preflop_open_faced_count: int = 0    # facing a preflop open
_fold_to_open_count: int = 0          # subset: opponent folded
_preflop_3bet_faced_count: int = 0
_fold_to_3bet_count: int = 0

# Derived rates
fold_to_open: float = 0.5
fold_to_3bet: float = 0.5
```

Caller (probably a new `OpenDetector` mirroring `CbetDetector`)
needs to track "the opponent was facing an open from another
seat" before the opponent's action. Today
`MemoryManager.observe_action` doesn't get that context — needs a
`was_facing_open: Optional[bool]` flag threaded through (mirrors
the Phase 7.5 Step 0 `was_facing_bet` pattern for postflop
counters).

**Cost**: ~80-120 lines plus a new detector + tests. Larger than
item 1 because of the detector. Hand-level state machine — track
who opened, queue defenders, drain as they respond.

**Payoff**: `steal_pressure` becomes shippable for LAG/Maniac. Drop
PFR upper-bound clause. Magnitude can rise from `0.15` to match
`value_vs_station`'s `0.30` once the proxy isn't the limiting factor.

### 3. Barrel response rates (`fold_to_turn_cbet`)

Stations frequently call the flop and fold the turn — a critical
signal for multi-street value extraction that `fold_to_cbet`
(flop-only) misses entirely. `CbetDetector` today stops tracking
after the first c-bet response; extending to turn would let us:

- Strengthen `value_vs_station` extraction (bet harder turn against
  opponents we expect to fold the turn even after calling the flop).
- Detect "loose-passive flop, tight turn" hybrid stations who don't
  fit either current bucket cleanly.

**Cost**: ~50-80 lines. Larger detector state — need to track the
second-barrel attempt + response.

**Payoff**: opens turn-bet sizing decisions to opponent profile. Out
of scope until v1 diagnostics confirm value_vs_station fires often
enough to make the turn distinction matter.

### 4. Position / texture splits for existing stats

`fold_to_cbet` aggregates across positions and board textures.
Splitting buys precision but explodes the state surface (and
multiplies cold-start time per opponent). Defer until a rule
actually consumes the split signal.

## Risk #1 follow-up — `hyper_passive` co-fire decision

Phase 8's Risk #1 documents three options for the `hyper_passive`
vs `value_vs_station` interaction. v1 shipped option 3 (defer).
After v1 diagnostics, pick between:

### Option A — Suppress hyper_passive's fold-mass reduction when value_vs_station is enabled and active

Inside `compute_exploitation_offsets`, when both rules would fire on
the same opponent AND the archetype enables value_vs_station,
**blend the fold offset** — keep hyper_passive's raise-frequency
push (which is complementary), but drop the `-0.2 × scale` fold
reduction (which is what punishes hero on marginal hands vs an
occasional-jammer station).

```python
# Inside the hyper_passive branch (sketch)
if hyper_passive_intensity > 0.0:
    scale = multiplier * hyper_passive_intensity
    for action in raise_like:
        offsets[action] = offsets.get(action, 0.0) + 0.3 * scale
    # Conditional: skip fold reduction when value_vs_station is the
    # gate driving exploitation on the same opponent.
    if 'fold' in available_actions and not vvs_co_active:
        offsets['fold'] = offsets.get('fold', 0.0) - 0.2 * scale
```

`vvs_co_active` is a new flag the controller passes through —
true when value_vs_station_intensity > 0 AND the underlying
station spot is the same opponent driving hyper_passive's
aggregate intensity.

**Cost**: small (one branch). **Risk**: changes hyper_passive
behavior for everyone — needs a regression sim across archetypes
that don't enable value_vs_station to confirm no leak.

### Option B — Add a "passive-with-jams" pattern detector

The signals already exist:

- `vpip > 0.60` (passive trait)
- `aggression_factor < 0.80` (passive AF)
- **NEW**: `all_in_frequency > 0.05` OR `postflop_jam_open_rate >
  threshold` (the "but jams" qualifier)

A new pattern detector `_is_passive_with_jams(stats)` would gate the
fold-mass reduction differently than the bare hyper_passive pattern.
Cleaner than option A because it doesn't couple hyper_passive's
behavior to whether value_vs_station is enabled — it makes the
hyper_passive pattern itself smarter.

**Cost**: medium (new pattern + tests + sim validation).
**Risk**: lower — change is opt-in for opponents who match the new
gate, doesn't touch behavior for pure stations with
`all_in_frequency=0`.

**Recommended path**: Option B is the cleaner architectural fix.
Option A is the smaller diff if Option B's classifier doesn't pan
out in sims. Decide post-diagnostics.

## Targets

Stretch targets (from Phase 8's "Phase 8.1 stretch" section, lines
229-234 of that doc, repeated here for proximity):

- TAG vs CaseBot HU mean moves toward `−35.6` bb/100 (FoldyBot floor).
- 6-max 3-seed mean moves toward 0 (net flat or positive).
- Per-opponent CaseBot drain shrinks from `~−2400 BB` toward
  `~−1000 BB` while ABCBot gain stays at or above `~+2800 BB`.

These targets are gated on either Option A or Option B from the
co-fire decision landing — `cbet_attempt_rate` and barrel stats
alone don't move bb/100 against CaseBot.

## Sequencing within Phase 8.1

If we end up shipping the full doc:

1. **Phase 8.1a** — `cbet_attempt_rate` (item 1). Cheapest, useful
   on its own, doesn't depend on other items.
2. **Phase 8.1b** — Risk #1 fix (Option A or B). Most bb/100 impact.
3. **Phase 8.1c** — `fold_to_open` (item 2) + enable `steal_pressure`
   for LAG/Maniac. Only if v1 diagnostics show meaningful eligibility
   volume.
4. **Phase 8.2** — `fold_to_turn_cbet` (item 3). Enables sizing
   distinctions that haven't been designed yet.

Items 1-3 can ship in any order independently. Item 4 is a separate
phase because it's larger and depends on actually using barrel
signals (not just having them).
