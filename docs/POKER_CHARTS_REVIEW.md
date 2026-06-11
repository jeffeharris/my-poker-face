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
| `vs_3bet` and `vs_4bet` rows of the 6-max preflop charts | **Equity-derived gradient** (June 2026, `vs_4bet` PR #272 / `vs_3bet` PR #273). Regenerated from an eval7 all-in-equity matrix vs an assumed villain re-raise range — *not* a full solver/equilibrium, but a principled equity gradient rather than a hand-authored guess. This is what killed the old "jam 72o into a 4-bet" stub. See note below. |
| **Everything else** (RFI + `vs_open`, HU preflop, postflop, depth charts) | **Hand-authored with AI assistance, then simulation-validated.** These are *calibrated heuristics*, not solver output. Aggregate VPIP/PFR bands were tuned to match targets in 10k-hand sims; individual cells were not solved. |

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
- Postflop the *frequency table* is thin (one authored node + fallbacks), **but
  the bot is not blind to what it flopped** — it classifies its real hand into a
  made-hand/draw bucket on every street and routes through equity-aware overrides.
  See "Postflop & adaptation" below. Preflop is where the bulk of the authored
  content is, which is why the grids in this packet focus there.

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

Dominant action + its %: `4`=4-bet (to ~2.2×), `C`=call, `·`=fold. (No jam in 3-bet pots at 100bb.)

> **This chart is a polarized equity gradient (June 2026), and the grids hide one
> structural fact you can't see from a dominant-action cell:** the 4-bet is
> **suited-only**. Value hands and *suited* blocker bluffs (e.g. `A5s`, which 4-bets
> ~30% here) carry a 4-bet; **every offsuit non-value hand has only call/fold — no
> raise key at all**, so neither the archetype transforms nor the personality
> distortion can ever 4-bet offsuit trash (a maniac 4-bets a wide *suited* range,
> never `72o`). Junk is *not* pure-folded — it keeps a thin `call` so the
> station/fish transforms can widen it. Only `AKo` among offsuit hands can 4-bet.


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

> **Also an equity gradient (June 2026), with one difference from `vs_3bet`:** facing
> a 4-bet, **junk is pure-folded** (`72o` = fold 100%) — there's no thin call to widen,
> because defending a 4-bet that wide is indefensible even for a fish. Value stacks
> off (jam), and a few *suited* blockers (`A5s`) jam as bluffs; everything marginal
> calls or folds by price.
>
> **Backstop:** independent of this chart, when the bot faces a cold **all-in**
> preflop it bypasses the chart/distortion entirely and decides **call-or-fold on
> raw eval7 pot odds** (PR #271), and it will never *voluntarily* re-jam over an
> existing all-in (a jam is only returned when calling already commits the whole
> stack). So even if a chart cell or a personality nudge put mass on "jam," a
> trash hand can't shove into an all-in — the equity veto folds it.


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

---

## Short-stack depth charts (50bb / 25bb)

The 100bb grids above are the deep baseline. The 50bb and 25bb charts are **not independent ranges** — they're the same 15-node skeleton, re-derived cell-by-cell from the 100bb chart by a coarse hand-authored polarization rule (*less flatting, more jamming* as stacks shorten). Two structural notes for the reviewer:

- **RFI is passed through unchanged** — opens are byte-identical to the 100bb chart at every depth (the depth logic only rewrites the facing nodes). Because the depth charts were generated *before* the CO/BTN/SB RFI widening, they carry the **old, tight** opens. That's a known footgun, not short-stack theory.
- **The action vocabulary collapses** toward the short stack: at 25bb a `vs_open` 3-bet becomes a **jam**, and `vs_3bet` is **pure jam-or-fold** (no flat, no small 4-bet). The grid topology is identical; only the labels inside change.

These are deliberately coarse (the cheap "100bb → fix" pass), so this is exactly the kind of short-stack range a reviewer can pick apart productively.
## Depth charts — 50bb facing grids

Same 15-node skeleton as the 100bb chart, re-derived cell-by-cell by the depth rules (tighten flats, start committing). RFI rows are **identical to the 100bb chart** (the depth logic only touches facing nodes), so they're not re-rendered here — see §1 above. Note the action vocabulary starts collapsing toward jams.


### 50bb — facing an open (`vs_open`)  ·  `R`=3-bet `J`=jam `C`=call `·`=fold


**HJ vs UTG**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  R 86 R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  R 72 R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  R 86 R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  R 72 R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  R 86 R 86 R 72    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  R 72 R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  R 87 R 87 C 49 C 49    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  R 74 R 87 C 49    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 87    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 49    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 49    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  R 87 R 87 R 74 C 49    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  R 74 R 87 C 49    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 87    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · R 87    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 49    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  R 87 R 87 R 74 R 68    ·    ·    ·    ·    · R 76    ·    ·    ·
  K  R 74 R 87 R 74    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 87    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · R 87    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · R 60    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  R 86 R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  R 72 R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  R 86 R 86 R 72    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  R 72 R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  R 86 R 86 R 72 R 63    ·    ·    ·    ·    · R 72    ·    ·    ·
  K  R 72 R 86 R 72    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · R 54    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  R 86 R 86 R 72 R 72 R 54    ·    ·    ·    · R 72 R 72 R 63    ·
  K  R 72 R 86 R 72 R 72    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 86 R 72    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · R 86    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · R 72    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    · R 63    ·    ·    ·    ·    ·    ·    ·
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
  A  R 87 R 87 C 49 C 49    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  R 74 R 87 C 49    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 87    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · C 49    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 49    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  R 87 R 87 R 74 C 49    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  R 74 R 87 C 49    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 87    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · R 87    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · C 49    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  R 87 R 87 R 74 R 68    ·    ·    ·    ·    · R 76    ·    ·    ·
  K  R 74 R 87 R 74    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 87    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · R 87    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · R 60    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  R 87 R 87 R 74 R 74 R 60    ·    ·    ·    · R 74 R 76 R 68    ·
  K  R 74 R 87 R 74 R 74    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 87 R 74    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · R 87    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · R 74    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    · R 68    ·    ·    ·    ·    ·    ·    ·
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
  A  R 87 R 87 R 74 R 74 R 74    ·    ·    ·    · R 74 R 74 R 74 R 68
  K  R 74 R 87 R 74 R 74 R 60    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · R 87 R 74 R 74    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · R 87 R 74    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · R 74    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    · R 74    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    · R 60    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

### 50bb — our open got 3-bet (`vs_3bet`)  ·  `4`=4-bet `J`=jam `C`=call `·`=fold


**UTG vs HJ**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**UTG vs CO**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**UTG vs BTN**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**UTG vs SB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**UTG vs BB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**HJ vs CO**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**HJ vs BTN**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**HJ vs SB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**HJ vs BB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**CO vs BTN**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**CO vs SB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**CO vs BB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**BTN vs SB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**BTN vs BB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

**SB vs BB**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  4 55 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K     · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · 4 55    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

### 50bb — our 3-bet got 4-bet (`vs_4bet`)  ·  `J`=jam `C`=call `·`=fold


**HJ vs UTG**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 88 J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 52 J 88    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 52    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

## Depth charts — 25bb facing grids

The commit-or-fold regime. The action vocabulary has fully collapsed: `vs_open` 3-bets become **jams**, and `vs_3bet` is **pure jam-or-fold** (the flat-call and 4-bet branches are gone). RFI rows are still identical to the 100bb chart (the short-stack footgun — opens were never re-tuned for depth).


### 25bb — facing an open (`vs_open`)  ·  `R`=3-bet `J`=jam `C`=call `·`=fold


**HJ vs UTG**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 89 J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 78 J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 89 J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 78 J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 89 J 89 J 78    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 78 J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 90 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 95 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 90 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 95 J 90 J100    ·    ·    ·    ·    · J100    ·    ·    ·
  K  J 90 J 95 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J100    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 89 J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 78 J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 89 J 89 J 78    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 78 J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 89 J 89 J 78 J 76    ·    ·    ·    ·    · J 82    ·    ·    ·
  K  J 78 J 89 J 78    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 70    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 89 J 89 J 78 J 78 J 70    ·    ·    ·    · J 78 J 82 J 76    ·
  K  J 78 J 89 J 78 J 78    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 89 J 78    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 89    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 78    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    · J 76    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 90 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 95 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 90 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 95 J 90 J100    ·    ·    ·    ·    · J100    ·    ·    ·
  K  J 90 J 95 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J100    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 95 J 90 J 90 J100    ·    ·    ·    · J 90 J100 J100    ·
  K  J 90 J 95 J 90 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 95 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    · J100    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 95 J 90 J 90 J 90    ·    ·    ·    · J 90 J 90 J 90 J100
  K  J 90 J 95 J 90 J 90 J100    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 95 J 90 J 90    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 95 J 90    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    · J 90    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    · J100    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

### 25bb — our open got 3-bet (`vs_3bet`)  ·  `4`=4-bet `J`=jam `C`=call `·`=fold


**UTG vs HJ**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 90 J 90 J 62 J 62 J 62 J 62    ·    ·    · J 60 J 52    ·    ·
  K  J 77 J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 90    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  J     ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·    ·
  T     ·    ·    ·    · J 77    ·    ·    ·    ·    ·    ·    ·    ·
  9     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  8     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  7     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  6     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  5     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  4     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  3     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  2     ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
```

### 25bb — our 3-bet got 4-bet (`vs_4bet`)  ·  `J`=jam `C`=call `·`=fold


**HJ vs UTG**

```
        A    K    Q    J    T    9    8    7    6    5    4    3    2
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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
  A  J 95 J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  K  J 75 J 95    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
  Q     ·    · J 75    ·    ·    ·    ·    ·    ·    ·    ·    ·    ·
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

Two things to know about how these transforms work, since they shape what you'll
see in the grids:
- **The transforms hit the defense charts too, not just opens.** `vs_open`,
  `vs_3bet`, and `vs_4bet` are all reshaped per archetype (a station flats wider and
  *traps* premiums instead of 3-betting them; a maniac continues wider and 4-bets a
  polarized, suited-only bluff range). Only the *opening* ranges are bespoke
  hand-picked sets; the facing ranges are a proportional fold→call/raise
  redistribution **masked by the base chart** — a hand the base pure-folds stays
  folded, so an archetype never invents continues the base never had.
- **Two layers, not one.** The width-tier *table* carries the range envelope; a
  separate runtime **personality distortion** (a capped logit nudge, ~±0.30 per
  action) adds aggression/passivity *flavor* on top. The nudge can't widen a range
  the table folds ~100% — that ceiling is the whole reason the tables exist.

There's also an orthogonal **skill axis** (`shark` / `reg` / `weak_reg` / `rec`)
that's independent of looseness: it scales how sharply a character bluffs rivers,
defends vs stabs, overbets, and adapts. So a "weak reg maniac" is wide *and*
face-up; a "shark TAG" is tight *and* tricky. Archetype = how loose; skill = how
good. (There is no global difficulty dial — difficulty is just which character +
skill tier you're seated against.)

---

## Postflop & adaptation (what the packet's grids don't show)

The grids above are all preflop. Three systems run beyond them that are worth a
reviewer's eye, because they change how the preflop ranges actually play out:

### The ranges aren't static within a session — a psychology layer moves them

The "±0.30 logit nudge" mentioned under the archetype variants isn't a fixed
personality lean. It's the output of a **live emotional state** (composure /
confidence / energy) that shifts with what happens at the table: a bot that just
took a bad beat tilts (opens wider, over-aggresses); one that's been card-dead or
out-played for a while tightens and second-guesses. So **read every grid in this
packet as the character's *composed baseline*** — the range it plays at emotional
equilibrium. In a live session the effective range *breathes* around that baseline,
by up to ~±0.30 per action (capped — it still can't continue a hand the underlying
table pure-folds, so the table remains the hard envelope).

Why we flag it for review: if you spot a play that looks like a leak — a loose
4-bet, a spew-y call — it may be a **mood distortion (tilt), not a chart value**. The
distinction matters because the fix is different (tune the emotional response vs.
fix the cell), and we don't want a tilt-spew misread as the bot's standard range.
It's also a *deliberate, readable* swing (an exploitable tell), not random noise —
feedback on whether the emotional widen/tighten lands at believable magnitudes is
welcome, but the cell-level EV questions above are best answered against the
composed baseline.

### Postflop is hand-strength aware (just thinly *charted*)

The bot does **not** play postflop purely off SPR/board frequencies. On every
street it evaluates its real hand vs the board into a made-hand + draw bucket
(`nuts / strong_made / medium_made / weak_made / air` × `strong_draw / weak_draw /
backdoor / no_draw`) using a rank-based evaluator (not a Monte-Carlo equity sim),
and that bucket is an input to the lookup. On top of the frequency table sit
equity-aware override layers: a low-SPR commit rule (jam nuts/strong), a pot-odds
floor priced on nut-status, a pure pot-odds/pot-committed math floor, and
value/bluff-catch overrides vs classified aggressors.

So the honest framing: the postflop **lookup table's coverage** is thin (one fully
authored node — single-raised, high-SPR — everything else degrades via fallback),
and that thinness is probably our biggest EV leak vs a competent reg. But it's a
*coverage* gap, not hand-strength blindness. We'd value feedback on where the
fallback ladder gives up too much.

### Opponent modeling / exploitation (no GTO claim here either)

There's a live opponent-read layer, but set expectations correctly:
- **It tracks a lot** (~25 stats: VPIP, PFR, aggression factor, fold-to-cbet,
  barrel rates, stab frequency, equity-at-action, sizing polarization, …) **but
  the archetype label keys off only three** — aggression factor, all-in frequency,
  and a player-count-normalized VPIP — into `hyper_aggressive` / `hyper_passive` /
  `tight_nit`. The richer stats drive a handful of separate exploit *rules*.
- **Convergence is "binary label, gradual strength":** no archetype label before
  **15 hands**; the exploitation *magnitude* then ramps linearly to full over
  ~**100 hands**.
- **Before it converges it does nothing special** — it just plays its own
  archetype table + distortion. There's no separate "GTO baseline mode" underneath.
- **Adaptation is a logit-nudge layer, not a counter-chart swap.** We do *not*
  switch to a villain-specific exploit chart once we read someone. (We tested
  adaptive preflop-table selection — switch tighter vs a station — and it came back
  EV-neutral because the wide range already beat every fixed villain, so it was
  never built.)

### The 7 exploit rules — what each one actually does

The adjustment isn't one blob — it's 7 named rules, each firing only in a specific
spot. Mechanically each pushes the **log-odds** of certain actions up or down, then
re-normalizes (softmax), so a `+0.5` nudge on call ≈ multiply call's probability by
~1.65 before re-norm. It's applied *on top of* the personality-distorted base chart,
never replacing it.

| Rule | Fires when villain… | In this spot | Does (poker terms) |
|---|---|---|---|
| **hyper_aggressive** | AF > 3.5 *or* jams > 30% | facing their bet/jam; also our open & BB defense | Stop folding — call wider, bluff-catch their junk-jams; tighten our *opens* (they 3-bet too much) |
| **hyper_passive** | normalized VPIP > 0.70 **and** AF < 0.80 | as the aggressor (not when defending) | Value-bet bigger/more (stations pay off); fold less (they don't bluff) — *unless* they only raise the nuts |
| **tight_nit** | normalized VPIP < 0.30 | our open only | Widen steals — a nit folds preflop too often. (Never light-3bet them — they only continue with premiums) |
| **high_fold_to_cbet** | folds to flop c-bet > 60% (≥5 seen) | heads-up flop, we're the PF raiser | Fire more c-bets, check less — their air folds |
| **multiway_cbet** | *every* live opponent folds to c-bets >60% | multiway flop, we're the PF raiser | Same c-bet push, but only when the whole field folds (one sticky player blocks it) |
| **value_vs_station** | a confirmed station is in the pot | we hold strong/nuts, unopened | Bet for value more, check less — extract from the station |
| **bluff_reduction** | a confirmed station is in the pot | we hold air | Bet/raise *less*, check/give-up more — bluffs don't work on callers |

**How hard they push (bounded several ways):**
- Each rule's strength ramps with how extreme the read is (e.g. AF 3.5→15 ramps 0→full) **and** with the hero's own `adaptation_bias × skill (exploitation_strength) × confidence (hands seen / 100)`. A low-skill character barely adapts even with a clear read.
- A **three-tier total clamp** caps the *combined* shift across all rules at **0.4 / 0.6 / 0.8** (L1), escalating only as postflop aggression evidence mounts (and decaying if the villain mellows). Plus a per-rule budget so no single rule eats the whole envelope.

**What we measured (bb/100).** We built all 7 rules and then measured them — with
a paired exploit-ON-vs-OFF twin sharing the same deck (`exploit_bb100.py`, a
common-random-numbers gate). The result is that **two of the seven carry essentially
all of the value, against exactly the opponents they were designed for:**

- **Headline:** the layer is **+22.5 bb/100, CI [+16.1, +29.0]** (TAG hero, 24k
  paired hands) against a **CallStation-class caricature** — a pure-station backdrop
  (VPIP ≈ 1.0). CI-clear positive, every seed agreeing in sign.
- **Decomposition** — the +22.5 is carried by two rules; the other five are inert
  (their detectors don't trip, or they trip and flip no action):

  | Rule | measured bb/100 (vs caricature) |
  |---|---|
  | `value_vs_station` | **+13.3** |
  | `hyper_passive` | **+9.1** |
  | `hyper_aggressive`, `tight_nit`, `high_fold_to_cbet`, `multiway_cbet`, `bluff_reduction` | **~0.0** |

- **Against a realistic opponent the layer measured ~0.0** — vs human-style clones
  (Jeff_clone VPIP 0.35, Punisher_clone) the whole layer was **+0.0 bb/100, with
  essentially no offsets firing**: those players sit in the *dead zone* between the
  detectors (nit < 0.30, station > 0.70). Important caveat so this reads correctly:
  those clones are still *exploitable* (the bot beats them handily), just not
  *caricature*-exploitable. So this is **not** "the layer is worthless in production"
  — it's "**the layer's value against balanced/competent opponents is unmeasured**,"
  because the eval suite has no balanced opponent to test against.
- **`hyper_aggressive` is the most interesting null, and we know why.** Measured at
  64k paired hands vs a maniac field it came back **−9.3 bb/100, CI [−22.3, +3.7]
  (inconclusive)** — and the mechanism *was* firing (7–11% of hands changed action).
  The cause is known: the rule defends the wrong street. A maniac's edge comes from
  min-raising your blinds and stealing; this rule only widens calls vs *all-ins/big
  bets* and tightens our *opens* — it has **no blind/steal-defense component** (the
  code flags the missing `fold_to_open` proxy at `exploitation.py:121`). So it's not
  "this rule doesn't work" — it's "**this rule is incomplete, and we know exactly the
  missing piece.**"

**One-line verdict:** the exploitation layer has *established, CI-clear value against
a specific opponent class* (caricature stations: +22.5 bb/100), is *unmeasured against
balanced opponents* (none exist in our eval suite), and **`hyper_aggressive` is the
highest-priority incomplete rule** — it fires but defends the wrong street. (All
numbers are point-in-time eval results against the noted synthetic backdrops, not
production guarantees.)

---

## Known gaps we already suspect (so you can confirm/prioritize)

- **No 6-max push/fold table** — short-stack 6-max rides the 25bb depth chart.
- **Postflop *table coverage* is one authored node + fallbacks** — likely the
  single biggest source of EV leak vs a competent reg. (The bot still reads its own
  hand strength on every street — see "Postflop & adaptation" — so this is a
  coverage gap, not hand-strength blindness.)
- **UTG/HJ may be too tight** — defensible for a weak-postflop bot, but a reg would
  exploit the cap.
- **`vs_3bet` / `vs_4bet` were recently *rebuilt*, not left as stubs** — both were a
  coarse blob (one `{fold,call,4bet}` distribution shared across ~159 hands, which is
  what caused the old "jam offsuit trash into a 4-bet" behavior). In June 2026 they
  were regenerated from an eval7 all-in-equity matrix into polarized gradients
  (suited-only 4-bet bluffs; see the notes on §3/§4 above). Worth a careful look:
  the villain re-raise range they were solved *against* is an assumption, and the
  `vs_3bet` rebuild was shipped as a deliberate **believability-over-EV** trade
  (−3.8 bb/100 vs the old spewy stub in a head-to-head, accepted because the stub
  only "won" by trash-bluffing an over-folding clone). So the open question isn't
  "are these stubs" anymore — it's "is the assumed villain range right, and is the
  EV we gave up worth the more readable archetypes."

## If you want the raw data

Every chart is JSON at `poker/strategy/data/*.json` (preflop charts are 8,450
entries each: rfi + 15 `vs_open` + 15 `vs_3bet` + 15 `vs_4bet` nodes). Happy to
export any other node (or the archetype variants) as grids like these.
