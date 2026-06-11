---
purpose: Self-contained, shareable review packet of the poker strategy charts and decision branching for external feedback
type: reference
created: 2026-06-11
last_updated: 2026-06-11
---

# Poker Strategy Charts — Review Packet

> **For external review.** This is a standalone snapshot of the preflop strategy
> charts and the decision branching that selects them, assembled so a poker player
> can review it without the codebase. It is generated from the live chart data; if
> the numbers here and the JSON ever disagree, the JSON
> (`poker/strategy/data/*.json`) is authoritative.

## TL;DR for the reviewer

We run a rule/heuristic-authored lookup-chart bot (not a solver bot). It plays
6-max cash (100bb), with derived shallower tables (50bb / 25bb), a separate
heads-up preflop chart, and a heads-up short-stack push/fold table. On top of the
base charts we apply **archetype "width-tier" variants** (nit, TAG, LAG, maniac,
calling-station, weak-fish) so different AI characters open and defend with
different ranges.

**What we'd most like feedback on:**
1. Are the **base RFI ranges** sane by position? We recently widened CO/BTN/SB
   toward GTO-shaped pure opens; UTG/HJ are deliberately tight because the bot is
   weaker postflop.
2. Are the **defense branches** (call / 3-bet / 4-bet / fold) reasonable? The full
   `vs_3bet` and `vs_4bet` grids are below — those are our least-confident charts.
3. Where are the biggest leaks a competent reg would punish?

---

## ⚠️ Provenance — please don't read these as GTO

This matters for how you review. **Only one table is solver/equilibrium-derived:**

| Table | How it was made |
|---|---|
| `push_fold_hu.json` (HU ≤15bb push/fold) | **Computed.** Exact chip-EV heads-up push/fold Nash (no ante), solved by fictitious play with eval7 all-in equities, validated against HoldemResources HUNE anchors. This one *is* GTO-grade. |
| **Everything else** (all 6-max preflop charts, HU preflop, postflop, depth charts) | **Hand-authored with AI assistance, then simulation-validated.** These are *calibrated heuristics*, not solver output. Aggregate VPIP/PFR bands were tuned to match targets in 10k-hand sims; individual cells were not solved. |

So when you see a frequency like "3-bet 15%," treat it as "a human's guess that
sim-tested okay," not "the GTO frequency." Telling us where the heuristic diverges
from theory in a way that costs EV is exactly the feedback we want.

---

## The branching — which chart serves which spot

At decision time the bot picks exactly one chart for the spot:

| Situation | Chart used |
|---|---|
| Heads-up (2 players), >15bb | `preflop_100bb_hu.json` (676 entries) |
| Heads-up, ≤15bb effective | `push_fold_hu.json` (Nash push/fold) — overrides the HU chart |
| 6-max / multiway, character **has** a width-tier (nit/LAG/maniac/station/weak-fish) | That archetype's width chart, **at every depth** (identity beats depth — a maniac stays loose even at a 40bb buy-in) |
| 6-max / multiway, character **has no** width-tier (TAG / baseline) | The depth chart nearest the effective stack: 100bb / 50bb / 25bb |
| Postflop (all depths, 6-max and HU) | Single `postflop_strategies.json`; only the `(single-raised pot, high-SPR)` node is authored — everything else rides a degrade ladder (SPR low→high, 3-bet-pot→single-raised-pot) |

Notes worth flagging to a reviewer:
- There is **no 6-max push/fold table** yet — short-stack 6-max spots fall back to
  the nearest depth chart, which is a known gap.
- The **50bb / 25bb depth charts intentionally keep the *old, tight* RFI** (the
  CO/BTN/SB widening was measured at 100bb only). So shallow opens are tighter than
  100bb opens by design, not by accident.
- Postflop is thin: one authored node + fallbacks. Preflop is where the real
  content is, which is why this packet focuses there.

---

## Sizing & action conventions (6-max base chart)

| Parameter | Value |
|---|---|
| Effective stack | 100 BB, cash (no ICM) |
| Open (RFI) | **2.5 BB** |
| 3-bet | 3× the open |
| 4-bet | ~2.2× the 3-bet |
| All-in | `jam` |

Frequencies are **mixed**: e.g. UTG `K9o` = open 10% / fold 90%. Each row's action
probabilities sum to ~1.0.

### Scenarios in scope (per chart)

| Branch | Hero positions | Hero's options |
|---|---|---|
| `rfi` (open or fold) | UTG / HJ / CO / BTN / SB | open 2.5bb, fold |
| `vs_open` (facing a raise) | HJ / CO / BTN / SB / BB | call, 3-bet, fold |
| `vs_3bet` (open got 3-bet) | UTG / HJ / CO / BTN / SB | call, 4-bet, fold |
| `vs_4bet` (3-bet got 4-bet) | HJ / CO / BTN / SB / BB | call, jam, fold |

Each facing-branch enumerates all 15 (defender × original-raiser) pairings
(UTG_vs_HJ, …, SB_vs_BB), so the bot's response depends on *who* attacked.

### Observed aggregate ranges (after personality distortion, 10k-hand sim)

| Archetype | VPIP | PFR |
|---|---|---|
| Rock | ~23% | ~13% |
| TAG | ~28% | ~22% |
| LAG | ~50% | ~40% |
| Maniac | ~54% | ~47% |

### Base-chart RFI rate by position (combo-weighted)

| Position | Open % | Note |
|---|---|---|
| UTG | 11.5% | Tightest (left tight on purpose) |
| HJ | 14.0% | (unchanged) |
| CO | 27.3% | Widened 2026-05-27 toward GTO (was 17.4%) |
| BTN | 47.5% | Widened 2026-05-27 toward GTO (was 25.1%) |
| SB | 40.3% | Widened 2026-05-27 toward GTO (was 20.2%) |

---

## How to read the grids

Standard 13×13 hand matrix: **pairs on the diagonal, suited hands above it,
offsuit below.** Columns and rows run A→2. Each cell shows the chart's primary
action for that hand and the % weight on it; `·` means pure (or near-pure) fold.
The action letter changes per section — the legend is stated above each block.

---

## The actual ranges (rendered from the live JSON)

### 1. RFI (open-raise) frequencies — all positions

Cell = % of the time the hand opens (raise to 2.5bb); `·` = pure fold. Suited above the diagonal, offsuit below, pairs on it.


**UTG RFI — 11.5% of hands open**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A    95   95   90   90   90   10   10   10   10   10   10   10   10
  K    90   95   90   80   80   10   10   10   10   10   10   10   10
  Q    80   10   95   80   70   10   10   10   10    2    2    2    2
  J    10   10   10   95   80   10   10   10    2    2    2    2    2
  T    10   10   10   10   90   10   10   10    2    2    2    2    2
  9    10   10    2    2   10   80   10   10   10    2    2    2    2
  8    10    2    2    2    2    2   80   10   10   10    2    2    2
  7    10    2    2    2    2    2    2   90   10   10    2    2    2
  6    10    2    2    2    2    2    2    2   10   10   10    2    2
  5    10    2    2    2    2    2    2    2    2   10   10   10    2
  4    10    2    2    2    2    2    2    2    2    2   10   10    2
  3     2    2    2    2    2    2    2    2    2    2    2   10    2
  2     2    2    2    2    2    2    2    2    2    2    2    2   10
```

**HJ RFI — 14.0% of hands open**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A    95   95   90   90   80   70   70   70   10   10   10   10   10
  K    90   95   90   80   80   70   60   10   10   10   10   10   10
  Q    80   70   95   80   80   10   10   10   10    2    2    2    2
  J    70   10   10   95   80   10   10   10    2    2    2    2    2
  T    10   10   10   10   90   80   10   10    2    2    2    2    2
  9    10   10    2    2   10   80   70   10   10    2    2    2    2
  8    10    2    2    2    2    2   80   10   10   10    2    2    2
  7    10    2    2    2    2    2    2   70   10   10    2    2    2
  6    10    2    2    2    2    2    2    2   80   10   10    2    2
  5    10    2    2    2    2    2    2    2    2   10   10   10    2
  4    10    2    2    2    2    2    2    2    2    2   10   10    2
  3     2    2    2    2    2    2    2    2    2    2    2   10    2
  2     2    2    2    2    2    2    2    2    2    2    2    2   10
```

**CO RFI — 27.3% of hands open**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A   100  100  100  100  100  100  100  100  100  100  100  100  100
  K   100  100  100  100  100  100  100  100  100  100  100    ·    ·
  Q   100  100  100  100  100  100  100  100    ·    ·    ·    ·    ·
  J   100  100  100  100  100  100  100    ·    ·    ·    ·    ·    ·
  T   100  100  100  100  100  100  100    ·    ·    ·    ·    ·    ·
  9   100    ·    ·    ·    ·  100  100  100    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·  100  100  100    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·  100  100    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·  100  100    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·  100  100    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·  100    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·  100    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·  100
```

**BTN RFI — 47.5% of hands open**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A   100  100  100  100  100  100  100  100  100  100  100  100  100
  K   100  100  100  100  100  100  100  100  100  100  100  100  100
  Q   100  100  100  100  100  100  100  100  100  100  100  100  100
  J   100  100  100  100  100  100  100  100    ·    ·    ·    ·    ·
  T   100  100  100  100  100  100  100  100    ·    ·    ·    ·    ·
  9   100  100  100  100  100  100  100  100    ·    ·    ·    ·    ·
  8   100  100  100    ·    ·  100  100  100  100    ·    ·    ·    ·
  7   100  100    ·    ·    ·    ·  100  100  100  100    ·    ·    ·
  6   100  100    ·    ·    ·    ·    ·    ·  100  100  100    ·    ·
  5   100  100    ·    ·    ·    ·    ·    ·    ·  100  100  100    ·
  4   100    ·    ·    ·    ·    ·    ·    ·    ·    ·  100  100    ·
  3   100    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·  100    ·
  2   100    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·  100
```

**SB RFI — 40.3% of hands open**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A   100  100  100  100  100  100  100  100  100  100  100  100  100
  K   100  100  100  100  100  100  100  100  100  100  100  100  100
  Q   100  100  100  100  100  100  100  100  100  100    ·    ·    ·
  J   100  100  100  100  100  100  100  100    ·    ·    ·    ·    ·
  T   100  100  100  100  100  100  100  100    ·    ·    ·    ·    ·
  9   100  100  100  100  100  100  100  100    ·    ·    ·    ·    ·
  8   100    ·    ·    ·    ·    ·  100  100  100    ·    ·    ·    ·
  7   100    ·    ·    ·    ·    ·    ·  100  100  100    ·    ·    ·
  6   100    ·    ·    ·    ·    ·    ·    ·  100  100  100    ·    ·
  5   100    ·    ·    ·    ·    ·    ·    ·    ·  100  100  100    ·
  4   100    ·    ·    ·    ·    ·    ·    ·    ·    ·  100  100    ·
  3   100    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·  100    ·
  2   100    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·  100
```

### 2. Facing an open — sample defense nodes (`vs_open`)

Dominant action + its %: `R`=3-bet, `C`=call, `·`=fold. (All 15 defender×opener nodes exist in the JSON; two shown here.)


**BB vs BTN**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  R 85 R 85 R 70 R 70 R 50 C 55 C 55 C 45 C 45 R 70 R 70 R 60 C 45
  K  R 70 R 85 R 70 R 70 C 65 C 55 C 45 C 45 C 45 C 45 C 45 C 45 C 45
  Q  C 65 C 55 R 85 R 70 C 65 C 45 C 45 C 45 C 45 C 45 C 45    ·    ·
  J  C 55 C 45 C 45 R 85 C 65 C 45 C 45 C 45 C 45    ·    ·    ·    ·
  T  C 45 C 45 C 45 C 45 R 70 C 55 C 45 C 45 C 45    ·    ·    ·    ·
  9  C 45 C 45 C 45 C 45 C 45 R 60 C 55 C 45 C 45 C 45    ·    ·    ·
  8  C 45 C 45    ·    · C 45    · C 65 C 45 C 45 C 45 C 45    ·    ·
  7  C 45 C 45    ·    ·    ·    ·    · C 55 C 45 C 45 C 45    ·    ·
  6  C 45 C 45    ·    ·    ·    ·    ·    · C 45 C 45 C 45 C 45    ·
  5  C 45    ·    ·    ·    ·    ·    ·    ·    · C 45 C 45 C 45 C 45
  4  C 45    ·    ·    ·    ·    ·    ·    ·    ·    · C 45 C 45 C 45
  3  C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    · C 45    ·
  2  C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    · C 45
```

**BTN vs UTG**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  R 85 R 85 C 75 C 75 C 65 C 55 C 55    ·    ·    ·    ·    ·    ·
  K  R 70 R 85 C 75 C 65 C 65 C 55    ·    ·    ·    ·    ·    ·    ·
  Q  C 65 C 55 R 85 C 65 C 65    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 75 C 65    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 75 C 55    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    · C 65 C 55    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    · C 65    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    · C 55    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

### 3. Our open got 3-bet — all 15 nodes (`vs_3bet`)

Dominant action + its %: `4`=4-bet (to ~2.2×), `C`=call, `·`=fold. (This chart has no jam in 3-bet pots at 100bb.)


**UTG vs HJ**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**UTG vs CO**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**UTG vs BTN**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**UTG vs SB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**UTG vs BB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**HJ vs CO**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**HJ vs BTN**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**HJ vs SB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**HJ vs BB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**CO vs BTN**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**CO vs SB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**CO vs BB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**BTN vs SB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**BTN vs BB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**SB vs BB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55 C 46 C 46 C 46 C 46    ·    ·    ·    ·    ·    ·    ·
  K  C 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 55    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

### 4. Our 3-bet got 4-bet — all 15 nodes (`vs_4bet`)

Dominant action + its %: `J`=jam (all-in), `C`=call, `·`=fold.


**HJ vs UTG**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**CO vs UTG**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**CO vs HJ**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**BTN vs UTG**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**BTN vs HJ**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**BTN vs CO**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**SB vs UTG**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**SB vs HJ**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**SB vs CO**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**SB vs BTN**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**BB vs UTG**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**BB vs HJ**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**BB vs CO**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**BB vs BTN**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

**BB vs SB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 82 C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  C 45 J 82    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · C 45    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

---

## Archetype width-tier variants (how characters differ)

The base chart above is the TAG/baseline. Each "personality" archetype plays a
**generated** transform of it (never hand-edited — the generator is re-run):

| Archetype → chart | Shape |
|---|---|
| nit / rock → `tight_rfi` | The pre-widening tight opens, with premium re-raises damped into flat-calls |
| LAG → `loose_mid` | Between TAG and maniac; flats wide rather than 3-betting wide |
| maniac → `loose` | Widest realistic opening envelope |
| calling-station → `station` | Floods fold→call; damps premium re-raises into calls so it *traps* (3-bets premiums only ~3% realized) |
| weak-fish → `weak_station` | Widest passive-caller; flats almost anything vs a raise |

These are deliberately *exploitable* character ranges, not attempts at balance — a
maniac is *supposed* to be too loose. Feedback on whether they read as believable
versions of those player types (vs. just "random") is welcome.

---

## Known gaps we already suspect (so you can confirm/prioritize)

- **No 6-max push/fold table** — short-stack 6-max rides the 25bb depth chart.
- **Postflop is one authored node + fallbacks** — likely the single biggest source
  of EV leak vs a competent reg.
- **UTG/HJ may be too tight** — defensible for a weak-postflop bot, but a reg would
  exploit the cap.
- **`vs_3bet` / `vs_4bet` are our weakest charts** — note `vs_3bet` has *no 4-bet
  bluffs as jams* (only the linear 4-bet-or-call-or-fold split) and `vs_4bet`
  collapses to jam-or-fold-or-call. We had a historical bug where the bot jammed
  trash into 4-bets (since fixed), but the frequencies are still heuristics.

## If you want the raw data

Every chart is JSON at `poker/strategy/data/*.json` (preflop charts are 8,450
entries each: rfi + 15 `vs_open` + 15 `vs_3bet` + 15 `vs_4bet` nodes). Happy to
export any other node (or the archetype variants) as grids like these.
