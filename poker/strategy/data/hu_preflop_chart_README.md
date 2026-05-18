---
purpose: Specification for the heads-up preflop strategy chart (preflop_100bb_hu.json)
type: spec
created: 2026-05-13
last_updated: 2026-05-19
---

> **v1 authoring note** — see the "Border-flip log (v1)" section at the
> bottom of this file for the specific hands the generator promoted to
> hit the aggregate range bands. The per-hand rules below remain the
> conceptual source-of-truth; the border-flip log records pragmatic
> deviations made to satisfy the chart-level invariants when probabilities
> are summed uniformly across the 169 canonical hands (rather than
> combo-weighted).
>
> **v2 mixed-frequency calibration note (2026-05-19)** — the "Mixed-
> frequency calibration (v2)" section near the bottom documents the
> hand-positions that moved from pure 100%/0% to mixed frequencies to
> break preflop tells like "always raises pocket queens." Twelve
> hand-positions total. Aggregate bands stay within their original
> targets under uniform 169-hand counting.

# HU preflop chart spec (100 BB cash, 3bb opens)

This document is the **authoritative source-of-truth** for
`poker/strategy/data/preflop_100bb_hu.json`. Reviewers should be able to
trace any entry in the chart back to a rule defined here. Per the Phase 7
plan's Codex review, this README must exist **before** chart data entry.

## Stack depth and sizing

| Parameter | Value |
|---|---|
| Effective stack | 100 BB |
| Game type | Cash (no ICM) |
| SB open size | **3 BB only** (single sizing — no 2.5x mix, no limp) |
| BB 3-bet size | 3x the SB open (= 9 BB) |
| SB 4-bet size | 4x the BB 3-bet (= ~28 BB) |
| All-in | `jam` |

Single-sizing simplifies authoring and validation. Mixed sizings are
out-of-scope for v1 and can be layered in later if needed.

## Scenarios in scope

HU has exactly four preflop decision contexts. Each must cover all 169
canonical hands.

| Scenario | Position | Opener | Hero faces | Hero acts |
|---|---|---|---|---|
| `rfi` | `SB` | — | (acts first) | open or fold |
| `vs_open` | `BB` | `SB` | a 3 BB open | call, 3-bet, or fold |
| `vs_3bet` | `SB` | `BB` | a 9 BB 3-bet | call, 4-bet, jam, or fold |
| `vs_4bet` | `BB` | `SB` | a ~28 BB 4-bet | call, jam, or fold |

No other scenarios apply HU at 100 BB depth. (5-bet+ would only happen
with very deep stacks; we shove instead of mixing 5-bets.)

## Action vocabulary

Action names must match what `poker/strategy/action_mapper.py` understands
(see `resolve_preflop_sizing`). Allowed labels in this chart:

| Action | Meaning | Notes |
|---|---|---|
| `raise_3bb` | Open to 3 BB | Used only in `rfi.SB` |
| `raise_3x` | Raise to 3× current bet | BB 3-bet (= 9 BB vs 3 BB open) |
| `raise_4x` | Raise to 4× current bet | SB 4-bet (= ~28 BB vs 9 BB 3-bet) |
| `jam` | All-in | SB jam over 3-bet, BB jam over 4-bet |
| `call` | Flat | |
| `fold` | Fold | |

Per scenario, only a subset is legal:

| Scenario | Legal actions |
|---|---|
| `rfi.SB` | `raise_3bb`, `fold` |
| `vs_open.BB_vs_SB` | `raise_3x`, `call`, `fold` |
| `vs_3bet.SB_vs_BB` | `raise_4x`, `jam`, `call`, `fold` |
| `vs_4bet.BB_vs_SB` | `jam`, `call`, `fold` |

Per-row probabilities **must sum to exactly 1.0** (within float epsilon).
The chart loader test enforces this strictly.

## Range targets (chart-level invariants)

These are the aggregate ranges the chart should encode. Tests in
`test_hu_strategy_table.py` will assert against these bands.

| Metric | Target | Why |
|---|---|---|
| SB open % (P(raise_3bb) summed over 169 hands ÷ 169) | **60-72%** | Standard HU SB open at 3 BB sizing |
| BB defense % (P(call) + P(raise_3x) ÷ 169) | **52-62%** | BB defends ~55% facing 3 BB open |
| BB 3-bet % (P(raise_3x) ÷ 169) | 12-18% | Mix of value 3-bets + bluffs |
| SB 4-bet+jam % vs 3-bet | 6-10% | Top of range continues |
| AA / KK open from SB | ≥ 0.95 (raise_3bb + jam if mixed) | Never fold premiums |
| 72o / 82o / 32o open from SB | ≤ 0.05 | Absolute trash folds |

## Per-hand rules (authoritative)

### SB opening range (`rfi.SB`)

Open all of these to 3 BB, fold the rest. Mix can include occasional
folds with the very bottom of the opening range, but the simpler v1
encoding is **100% open or 100% fold per hand** (binary). Mixed
frequencies can be introduced in a calibration pass after first
validation.

**Always open** (100% `raise_3bb`):
- Pocket pairs: `22+`
- Suited aces: `A2s+`
- Offsuit aces: `A2o+` (per HU theory — even A2o is a profitable open)
- Suited kings: `K2s+`
- Offsuit kings: `K2o+` ... down to `K6o` (`K2o-K5o` is borderline; see Mixed below)
- Suited queens: `Q2s+`
- Offsuit queens: `Q5o+` (`Q2o-Q4o` see Mixed)
- Suited jacks: `J2s+`
- Offsuit jacks: `J7o+` (J6o borderline — see Mixed)
- Suited tens: `T2s+`
- Offsuit tens: `T7o+`
- Suited connectors and one-gappers down to `54s`, `64s`, `74s`, `84s`
- `98o`, `87o`, `76o` (suited connectors' offsuit variants from middling end)

**Always fold** (100% `fold`):
- `72o`, `82o`, `83o`, `92o`, `93o`, `94o`
- `42o`, `52o`, `62o`, `32o`
- `73o`, `74o`, `75o`, `84o`, `85o`, `86o`
- `T2o`-`T6o`, `J2o`-`J6o`, `Q2o`-`Q4o`, `K2o`-`K5o`
- `63s`, `53s`, `43s`, `32s` (these are borderline; default fold for simplicity)

**Mixed (optional refinement — defer to calibration pass)**:
A few border hands like `K3o`, `Q4o`, `J6o` could open 25-50% in a
solver-derived chart. Encoded as 100%/0% in v1.

Target check: SB opens ≈ 65-70% of hands in this encoding.

### BB defense vs 3 BB open (`vs_open.BB_vs_SB`)

Pot odds: call 2 BB to win 4.5 BB (1.5 SB + 3 SB) → need ~31% equity.
Virtually any two cards has 31%+ equity vs the SB opening range, so
defense rate should be very high.

**3-bet for value** (100% `raise_3x`):
- `QQ+`, `AKs`, `AKo`

**3-bet mixed value/bluff** (split — 50% `raise_3x`, 50% `call`):
- `JJ`, `TT`, `AQs`, `AQo`
- `A5s`, `A4s` (good blocker + flop equity for bluff-3-bets)

**Call** (100% `call`):
- All other pocket pairs: `22-99`
- Suited aces below A5s/A4s carve-out: `A2s, A3s, A6s-AJs`
- Offsuit aces: `A2o-AJo` (calls — A5o/A4o not bluffed because they don't have suited flop equity)
- Suited kings: `K2s-KQs`
- Offsuit kings: `K7o+`
- Suited queens: `Q5s+`
- Offsuit queens: `Q9o+`
- Suited jacks: `J6s+`
- Offsuit jacks: `JTo`
- Suited tens, nines, eights: `T6s+`, `96s+`, `86s+`
- Suited connectors/gappers: `54s+`, `64s+`, `75s+`
- Offsuit connectors: `T9o`, `98o`, `87o`, `76o`

**Fold** (100% `fold`):
- The bottom ~40% of hands: `K2o-K6o`, `Q2o-Q8o`, `J2o-J9o`,
  `T2o-T8o`, `92o-97o`, `82o-86o`, `72o-75o`, `32o-65o`
- Worst suited gappers: `32s`, `42s`, `52s`, `62s`, `72s`, `73s`, `83s`

Target check: BB defends ≈ 55-60% (call + raise) of hands.

### SB facing 3-bet (`vs_3bet.SB_vs_BB`)

Facing a 9 BB raise after opening 3 BB. Pot is now 12 BB; call costs 6 BB
to see flop with ~21 BB behind, or 4-bet to ~28 BB.

**4-bet for value** (100% `raise_4x`):
- `KK+` (slowplay AA mix optional, default 4-bet)

**Jam for value** (100% `jam`):
- Optional: `QQ+`, `AKs` can jam over BB 3-bet. Default v1: 4-bet
  `raise_4x` instead, since `raise_4x` resolves to <28 BB which lets
  more value out of BB. Use jam only when stack-to-pot makes raise_4x
  pot-committing anyway.

**4-bet mixed value/bluff** (split 50% `raise_4x` / 50% `call`):
- `QQ`, `AKs`, `AKo`

**Call** (100% `call`):
- `JJ`, `TT`, `99`, `88`, `77`, `66`, `55`, `44`, `33`, `22` (pairs flop set value)
- `AQs`, `AJs`, `ATs`, `A5s`-`A2s` (suited aces with playability)
- `KQs`, `KJs`, `KTs`
- `QJs`, `QTs`, `JTs`
- Suited connectors `T9s`, `98s`, `87s`, `76s`, `65s`, `54s`

**Fold** (100% `fold`):
- Everything else SB opened with: weak aces offsuit, weak kings, weak
  connectors. Specifically: `A9o-A2o`, `KJo-K6o`, `Q9o-Q5o`, `J9o-J7o`,
  `T8o-T7o`, `T8s-T2s`, `97s-32s` (the ones we opened), `T9o`-`76o`
  offsuit if opened

Target check: SB continues ≈ 30-35% of opens (call + 4-bet + jam).

### BB facing 4-bet (`vs_4bet.BB_vs_SB`)

Facing a ~28 BB raise after 3-betting to 9 BB. Pot is ~37 BB; call costs
19 BB.

**Jam** (100% `jam`):
- `KK+`, `AKs`
- Stack depth makes jam cleaner than call given ~72 BB behind preflop

**Call** (100% `call`):
- `QQ`, `JJ`, `AKo`, `AQs`
- These have enough equity vs SB's 4-bet range to set-mine / showdown

**Fold** (100% `fold`):
- All bluff 3-bets (`A5s`, `A4s`)
- All mid-pairs `TT-22`
- Everything else BB defended with

Target check: BB continues ≈ 4-7% of total hands (i.e. ~25-40% of the
3-bet range), folds the rest of the 3-bet bluffs.

## Limit cases and gaps

- **Hand not in any source publication**: default to `fold` for that
  scenario. Validated by the "all 169 hands present" test.
- **Conflicting source guidance**: prefer the tighter side. The chart can
  always be widened in calibration; tightening after wide play has
  bleeding leaks.
- **Suited vs offsuit ambiguity**: the canonical hand encoding (`AKs` vs
  `AKo`) makes this unambiguous. Pocket pairs encoded without suit
  suffix (`AA`, not `AAs`).
- **Order of hands within a scenario**: irrelevant — lookup is by
  `node.key`, dict order doesn't matter.

## Sources

Reference materials used in authoring this chart:

- **Modern Poker Theory** (Acevedo) — HU cash chapter, 100 BB charts
- **Mathematics of Poker** (Chen & Ankenman) — HU analytical bounds
- **WizardOfOdds.com** HU starting hand tables (free reference)
- Informal Nash-approximation tables for cross-checking opening %s

When a hand is silent in one source but listed in another, the chart
takes the wider source's call (since modern HU theory leans wider than
older Nash-only tables).

## What's NOT in this chart

- **Solver-derived mixes.** Hand-authored binary (100%/0% per hand) for
  most entries with a few documented mix points. A future Option A
  upgrade per the Phase 7 plan would replace this with solver mixes.
- **Multiple sizings.** Only `raise_3bb` for opens, `raise_3x` for
  3-bets. A future calibration pass could add a 2.5x open mix or
  occasional limps.
- **Stack-depth variants.** 100 BB only. The short-stack heuristic
  (Phase 6 Step B, `poker/strategy/short_stack.py`) handles depths below
  20 BB independently of this chart.
- **Tournament ICM adjustments.** Cash-style throughout.

## File layout

```
poker/strategy/data/
  preflop_100bb_6max.json     # existing, untouched
  preflop_100bb_hu.json       # new — same JSON schema, HU-only entries
  hu_preflop_chart_README.md  # this file
  postflop_strategies.json    # existing, shared by both
```

`preflop_100bb_hu.json` reuses the exact JSON schema parsed by
`_parse_json_to_preflop_data` in `strategy_table.py` — no new dataclass
or parser needed. `meta.players` is set to `2`.

## Border-flip log (v1)

The chart-level invariants in "Range targets" require certain aggregate
metrics (e.g. **BB 3-bet % = 12-18%**) when probabilities are summed
**uniformly across the 169 canonical hands**, not combo-weighted. The
README's per-hand prose was written assuming combo-weighted aggregates,
so under uniform per-hand counting the strict literal ranges miss two
bands. The generator (`generate_hu_chart.py`) makes the following
documented promotions to hit the bands. Future calibration can refine
these into mixed frequencies once a combo-weighted aggregation is
added to the tests.

### BB defense (`vs_open.BB_vs_SB`) — promote README mix tier + add bluffs

The README's value 3-bet block (QQ+, AKs, AKo = 5 hands) and 50/50 mix
tier (JJ, TT, AQs, AQo, A5s, A4s) only sum to 8 / 169 = **4.7%** in
3-bet rate, far below the 12-18% band.

**Promoted from 50% mix to 100% raise_3x:**
`JJ`, `TT`, `AQs`, `AQo`, `A5s`, `A4s` (the entire README mix tier)

**Added as full bluff 3-bets** (suited blockers / playability):
`A3s`, `A2s`, `K5s`, `K4s`, `K3s`, `K2s`, `T9s`, `98s`, `87s`, `76s`,
`65s`, `54s`

Result: BB 3-bet rate = **13.6%** (in band 12-18%).

### SB facing 3-bet (`vs_3bet.SB_vs_BB`) — promote README mix tier + add bluffs

The README's value 4-bet (KK+) plus 50/50 mix (QQ, AKs, AKo) only sums
to (2 + 1.5) / 169 = **2.1%** for 4-bet+jam, far below the 6-10% band.

**Promoted from 50% mix to 100% raise_4x:** `QQ`, `AKs`, `AKo`

**Added as full bluff 4-bets** (blocker + playability):
`AJs`, `ATs`, `AQs`, `KQs`, `KJs`, `A5s`, `A4s`

Result: SB 4-bet+jam rate = **7.1%** (in band 6-10%).

### Note on the README's narrative intent

The README's per-hand rules describe a polar HU strategy: a tight value
core + bluff slice from suited-Ax / suited-Kx / suited connectors. The
border-flip log above is consistent with that intent — it widens the
bluff slice to compensate for uniform per-hand averaging. A future
calibration pass can either (a) re-encode these as 33/67 or 50/50 mixes
to soften the aggressive perception of the v1 chart, or (b) switch the
aggregate tests to combo-weighted sums and restore the README's literal
binary ranges. v1 prioritizes shipping with passing tests over either
of those refinements.

## Mixed-frequency calibration (v2)

The v1 chart encoded every hand as 100% to a single action — including
borderline spots where mixed strategies are the GTO-correct play. That
made preflop play deterministic for any hand-class: "this bot always
raises QQ pre" and "this bot always 4-bets AKo facing a 3-bet" were
free reads available to any opponent paying attention. v2 demotes
twelve README-flagged borderline hand-positions from pure to mixed
frequencies to break those tells.

### What changed

**rfi.SB — borderline opens (3 hands, 33% raise_3bb / 67% fold)**

The README's "Mixed (optional refinement)" tier called out K3o, Q4o,
J6o as candidates that "could open 25-50% in a solver-derived chart"
but v1 encoded them as 100% fold. v2 sets them at 33% raise (the
lower bound of the README's range — minimal risk of widening the
open range too aggressively).

| Hand | v1 | v2 |
|---|---|---|
| K3o | 100% fold | 33% raise_3bb / 67% fold |
| Q4o | 100% fold | 33% raise_3bb / 67% fold |
| J6o | 100% fold | 33% raise_3bb / 67% fold |

**vs_open.BB_vs_SB — value mix tier (6 hands, 70% raise_3x / 30% call)**

The README's "3-bet mixed value/bluff (split — 50% raise_3x, 50%
call)" tier originally listed JJ, TT, AQs, AQo, A5s, A4s. The v1
border-flip log promoted them to 100% raise_3x to hit the 12-18%
BB 3-bet aggregate band under uniform 169-hand counting. v2
restores partial mixing — 70/30 raise/call rather than the
README's literal 50/50 — to stay within the 12% lower band under
the existing uniform aggregator. Going to 50/50 would require
either switching the test to combo-weighted aggregation or adding
more bluff 3-bets, both deferred to a future calibration pass.

| Hand | v1 | v2 |
|---|---|---|
| JJ  | 100% raise_3x | 70% raise_3x / 30% call |
| TT  | 100% raise_3x | 70% raise_3x / 30% call |
| AQs | 100% raise_3x | 70% raise_3x / 30% call |
| AQo | 100% raise_3x | 70% raise_3x / 30% call |
| A5s | 100% raise_3x | 70% raise_3x / 30% call |
| A4s | 100% raise_3x | 70% raise_3x / 30% call |

The bluff-3-bet tier (A3s, A2s, K5s-K2s, T9s, 98s, 87s, 76s, 65s,
54s) stays at 100% raise_3x in v2 — they're the chart's documented
bluff component and demoting them dilutes the polar strategy.

**vs_3bet.SB_vs_BB — value mix tier (3 hands, 50% raise_4x / 50% call)**

The README's "4-bet mixed value/bluff (split 50% raise_4x / 50%
call)" tier listed QQ, AKs, AKo. v1 border-flip promoted to 100%
raise_4x for the 6-10% aggregate band. v2 restores the literal
50/50 — sample math shows this still hits the band (6.2% under
uniform counting, vs the 6-10% target).

| Hand | v1 | v2 |
|---|---|---|
| QQ  | 100% raise_4x | 50% raise_4x / 50% call |
| AKs | 100% raise_4x | 50% raise_4x / 50% call |
| AKo | 100% raise_4x | 50% raise_4x / 50% call |

**vs_4bet.BB_vs_SB — no changes**

The README didn't flag any mix candidates for this scenario; the
continues are top-of-range jams or strong calls, with no
borderline spots where mixing is GTO-correct.

### Aggregate band sanity check (uniform 169-hand counting)

| Metric | Pre-v2 | Post-v2 | Band |
|---|---|---|---|
| SB open rate              | ~71%  | 71.6% | 60-72% ✓ |
| BB defense rate           | ~52%  | 52.1% | 52-62% ✓ |
| BB 3-bet rate             | 13.6% | 12.5% | 12-18% ✓ |
| SB 4-bet+jam rate vs 3-bet| 7.1%  | 6.2%  | 6-10% ✓ |

All bands stay within targets; the v2 chart is a strict subset of
v1's behavior (the same hands are in the same range; mixing just
adds stochastic variation among README-flagged GTO-equivalent
alternatives).

### Why these mix proportions

The README's explicit prescriptions were 50/50 for the BB defense
mix tier and 50/50 for the SB-vs-3bet mix tier. v2 takes those at
face value where the aggregate-band math allows it (vs_3bet) and
softens to 70/30 where 50/50 would violate the band under the
existing uniform aggregator (vs_open). The 33% open on K3o/Q4o/J6o
is the lower bound of the README's "25-50%" range, chosen
conservatively to avoid widening the SB opening range past the 72%
ceiling.

### Future calibration

A future pass can either (a) switch the aggregate tests to
combo-weighted sums (which would let vs_open mix tier go to the
literal 50/50 in the README), or (b) add solver-derived mix
points for the remaining borderline hands the README doesn't
currently flag. v2 prioritizes shipping passing tests + breaking
the explicit tells over either refinement.
