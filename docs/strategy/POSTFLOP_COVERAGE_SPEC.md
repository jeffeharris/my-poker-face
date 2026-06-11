---
purpose: Implementation spec for generated postflop frequency coverage (replaces one authored node + degrade ladder)
type: design
created: 2026-06-10
status: proposed
depends_on: postflop_strategies.json schema, existing hand-bucket classifier and override layers
---

# Postflop Coverage Spec

The review packet's self-assessment is right: one authored node (SRP, high-SPR)
with a degrade ladder is the biggest EV leak vs a competent opponent. The bot
isn't hand-blind — the bucket classifier and equity overrides do real work —
but every 3-bet pot and every low-SPR spot currently plays a frequency table
authored for a different situation.

**Approach: don't author more nodes. Generate the full grid from a small
parameter set, calibrate the parameters, keep the override layers on top.**
Hand-authoring 100+ nodes is how the `vs_3bet` copied-range bug happened;
a generator makes coverage uniform and regeneration cheap.

## 1. Node key

Generate a frequency table for every combination of:

| Dimension | Values | Count |
|---|---|---|
| Pot type | SRP, 3BP, 4BP+ | 3 |
| Role × position | PFR-IP, PFR-OOP, caller-IP, caller-OOP | 4 |
| SPR band | <1.5, 1.5–4, 4–8, >8 | 4 |
| Street | flop, turn, river | 3 |
| Multiway | HU pot, multiway | 2 |

= 288 nodes, each a distribution over {bet/raise size-family, check/call,
fold} **per hand bucket** (existing 5 made × 4 draw classes = 20 buckets).
~5,800 generated cells — far too many to author, trivial to generate.

The existing bucket classifier is unchanged and remains the lookup input.
The existing override layers (low-SPR commit rule, pot-odds floors, value/
bluff-catch vs classified aggressors) remain on top, unchanged. The generator
replaces only the *frequency table* and its degrade ladder.

## 2. Generator: parametric rules

All 288 nodes derive from ~30 named parameters, e.g.:

```
cbet_base_ip          = 0.62   # SRP flop c-bet, IP, HU
cbet_oop_penalty      = -0.15
cbet_3bp_bonus        = +0.10  # range advantage in 3BP
multiway_penalty      = -0.25  # per extra player, on all bluff weights
value_bet_floor       = strong_made and better   # always bet/raise region
semibluff_weight      = {strong_draw: 0.7, weak_draw: 0.35, backdoor: 0.15(flop only)}
barrel_decay          = 0.75   # turn bluff freq = flop bluff freq × decay
river_bluff_ratio     = f(pot_odds_offered)      # sizing-derived, not free
bluffcatch_mdf_anchor = 1 - bet/(pot+bet) scaled by 0.9 (IP) / 0.8 (OOP)
spr_commit_threshold  = SPR < 1.5 → jam/call-off region from bucket
size_families         = {small: 0.33, mid: 0.66, big: 1.0, overbet: 1.4} × pot
```

Generation rules per node (sketch):

- **Aggressor nodes**: value region bets (floor by bucket), semi-bluffs weighted
  by draw class × barrel decay × multiway penalty; air checks at a frequency
  that keeps total bet% near the c-bet target for that pot type/position;
  river bluffs sized-and-ratioed off `river_bluff_ratio` (bluff count follows
  from sizing — this keeps rivers near-balanced by construction).
- **Defender nodes**: continue region anchored to `bluffcatch_mdf_anchor` of
  the facing size, filled by bucket order (made strength, then draws by equity);
  raises from {nuts, strong_made, strong_draw} only, frequency capped.
- **Low SPR band**: vocabulary collapses toward jam/call-off via
  `spr_commit_threshold` (consistent with the existing commit override, so the
  table and the override agree instead of fighting).

Output: same `postflop_strategies.json` schema, every node explicitly present.
**Delete the degrade ladder** after parity testing — fallback masking bugs is
how thin coverage stayed invisible. Keep one assert-and-log fallback for
genuinely unreachable keys.

## 3. Calibration

Two loops, in order:

1. **Sanity calibration (closed form)**: assert generated aggregates land in
   accepted bands before any sims — SRP IP flop c-bet 55–70%; fold-to-flop-cbet
   40–55%; turn barrel 45–60% of flop bets; river value:bluff consistent with
   sizing; WTSD 24–30% in self-play.
2. **Sim calibration (parameter search)**: coordinate-descent over the ~30
   params, objective = self-play EV vs the probe-bot suite
   (VALIDATION_SUITE_SPEC.md) + head-to-head vs current table with duplicate
   dealing. Small parameter count is what makes this tractable — that's the
   main argument for parametric generation over per-cell authoring.

## 4. Archetype & psychology interaction

- Width-tier transforms and the ±0.30 logit psychology nudge apply unchanged —
  they operate on whatever table is loaded. The mask rule ("never invent a
  continue the base pure-folds") now binds against *generated* cells; since
  defender nodes keep thin calls in marginal buckets (mirroring the `vs_3bet`
  station-mask convention), stations still widen naturally.
- The skill axis (shark/reg/weak_reg/rec) maps onto generator params, not new
  tables: e.g. rec tier gets `barrel_decay × 0.8`, `river_bluff_ratio × 0.6`,
  `bluffcatch_mdf_anchor × 0.85` (under-defends). One parameter vector per
  skill tier, generated the same way. This replaces ad-hoc per-tier behavior
  with something testable.

## 5. Acceptance

- All §3.1 sanity bands pass.
- Probe bots: always-cbet bot, always-raise-flop bot, overfold-exploit bot —
  each ≤ 0 EV per attempt vs the new tables (see VALIDATION_SUITE_SPEC.md).
- Head-to-head ≥ +2 bb/100 vs the current single-node + ladder table
  (duplicate-dealt, 100k+ hands). Given the ladder serves wrong-situation
  frequencies in every 3BP and low-SPR pot, this bar should be comfortably met.
- Degrade-ladder hit telemetry = 0 over a 10k-hand self-play run before the
  ladder is removed.

## 6. Out of scope (v2 candidates)

- Board-texture dimension (dry/wet/paired multiplier on bluff weights) —
  additive later; the bucket classifier already encodes hand-vs-board.
- Node-level solver verification (spot-check generated frequencies vs a real
  solver on a few canonical boards) — valuable, not blocking.
- Bet-size mixing within a node (one size family per node-bucket in v1).
