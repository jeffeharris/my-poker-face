---
purpose: Implementation spec for regenerating vs_3bet (per-node, MDF-anchored) and fixing BB defense in vs_open
type: design
created: 2026-06-10
status: proposed
depends_on: poker/strategy/data/*.json (chart branch), eval7 equity matrix tooling from PR #272/#273
---

# Preflop Defense Regeneration Spec

Fixes the two largest exploitable leaks found in the June 2026 chart review:

1. **`vs_3bet` is position-invariant.** All 15 nodes are byte-identical (so are
   all 15 `vs_4bet` nodes — confirmed against the live JSON). Combo-weighted
   fold-to-3bet relative to each position's own open range: **UTG 53.5%,
   HJ 56.9%, CO 65.0%, BTN 73.9%, SB 71.3%.** The 3-bettor's auto-profit line
   is 65.2% (risking 7.5bb to win 4bb), so 3-betting any two cards prints vs
   BTN/SB opens and is ~breakeven vs CO; UTG/HJ are actually fine. The leak is
   real but **confined to the positions that open wide** — the copied range is
   roughly correct for UTG and degrades with opening width.
   *(Earlier drafts cited "fold-to-3bet ~90%+"; that was an artifact of reading
   the dominant-action grids, which hide sub-50% call weights. Numbers above
   are open-weighted from the live JSON.)*
2. **BB overfolds vs steals.** Core defends carry 45–55% mixed call weights and
   the defend region is too narrow. Measured (live JSON): BB defends **44.6%
   vs BTN** and **49.4% vs SB** — ~7–9 points under the 52%/58% floors — and
   is under its 43% floor vs CO. UTG/HJ defense passes its floors.

Both are generator fixes, not hand-edits: the 15-node machinery already exists —
it currently carries one range copied 15 times.

---

## 1. The math anchors

### 1.1 MDF vs a 3-bet (100bb, open 2.5bb, 3-bet 3× = 7.5bb)

Breakeven fold frequency for the 3-bettor's bluffs (what we must not exceed):

| 3-bettor | Risks | Wins if we fold | BE fold % | MDF continue (of opens) |
|---|---|---|---|---|
| Non-blind (HJ/CO/BTN) | 7.5 | 2.5 + 1.5 = 4.0 | 65.2% | 34.8% |
| SB (0.5 posted) | 7.0 | 2.5 + 1.0 = 3.5 | 66.7% | 33.3% |
| BB (1.0 posted) | 6.5 | 2.5 + 0.5 = 3.0 | 68.4% | 31.6% |

Raw MDF ≈ continue with **~33–35% of the opening range**. We target above MDF
when the opener is in position postflop (better equity realization), slightly
above-or-at MDF when out of position:

- **IP nodes** (3-bettor is a blind; also SB-open is OOP vs BB — see table):
  continue factor **k = 0.45**
- **OOP nodes** (3-bettor has position on opener): **k = 0.38**

### 1.2 Per-node targets

Continue target (of all hands) = open% × k. Calibrate the generator to these
targets (equivalently, the lint thresholds) — not to "improving" any current
measurement.

| Node(s) | Hero postflop | Open % | k | Continue target (of hands) | Fold-to-3bet target | Current F3B (measured) |
|---|---|---|---|---|---|---|
| UTG vs HJ/CO/BTN | OOP | 11.5 | 0.38 | 4.4% | ≤62% | 53.5% ✓ |
| UTG vs SB/BB | IP | 11.5 | 0.45 | 5.2% | ≤55% | 53.5% ✓ |
| HJ vs CO/BTN | OOP | 14.0 | 0.38 | 5.3% | ≤62% | 56.9% ✓ |
| HJ vs SB/BB | IP | 14.0 | 0.45 | 6.3% | ≤55% | 56.9% (marginal) |
| CO vs BTN | OOP | 27.3 | 0.38 | 10.4% | ≤62% | 65.0% ✗ |
| CO vs SB/BB | IP | 27.3 | 0.45 | 12.3% | ≤55% | 65.0% ✗ |
| BTN vs SB/BB | IP | 47.5 | 0.45 | 21.4% | ≤55% | 73.9% ✗ |
| SB vs BB | OOP | 40.3 | 0.38 | 15.3% | ≤62% | 71.3% ✗ |

The copied cells produce a fold-to-3bet *gradient* only because open
compositions differ — UTG's opens concentrate in the premiums that continue,
so UTG lands at target by accident; BTN/SB opens are junk-heavy, so the same
cells overfold badly there. The regen's payload is CO/BTN/SB (and HJ's IP
nodes, marginally); UTG/HJ-OOP should come out nearly unchanged, which doubles
as a sanity check on the generator.

### 1.3 4-bet targets

4-bet = 2.2× of 7.5bb ≈ 16.5bb; risking 14 to win 11.5 → 3-bettor folding >55%
makes bluffs profitable. Targets:

- **4-bet 8–12% of the opening range** per node (value+bluff), value:bluff ≈ 55:45.
- Keep the existing invariants: **suited-only 4-bet bluffs** (A5s–A2s first,
  then KQs/KJs-type blockers), AKo the only offsuit 4-bet, junk keeps a thin
  call for the station mask.

---

## 2. vs_3bet generation algorithm (per node)

Replace the single global generation with a loop over all 15 (hero_pos,
villain_pos) nodes:

```
for node in NODES_15:
    hero_open   = rfi[node.hero_pos]                      # weighted combos
    villain_3b  = vs_open[node.villain_pos][node.hero_pos].raise_range
                                                          # ← self-consistent villain model
    ip          = hero_has_position_postflop(node)
    k           = 0.45 if ip else 0.38
    k          *= clamp(weight(villain_3b) / 0.08, 0.6, 1.0)   # taper vs value-only 3-bettors
    target_cont = k * weight(hero_open)
    target_4bet = 0.10 * weight(hero_open)

    # rank hero's opens
    eq_allin    = eval7_equity_matrix(vs=villain_continue_vs_4bet(villain_3b))
    eq_vs_range = eval7_equity_matrix(vs=villain_3b)
    playability = suitedness + connectedness + high_card bonus    # existing heuristic ok

    fourbet_value = top of hero_open by eq_allin, until 0.55 * target_4bet
    fourbet_bluff = best suited blockers (A5s..A2s, suited broadways),
                    weighted, until 0.45 * target_4bet
    calls         = top remaining by (0.7*eq_vs_range + 0.3*playability),
                    until target_cont - target_4bet
    junk          = thin call floor (unchanged, for station mask)
```

Key changes from the June 2026 generator:

- **Villain 3-bet range comes from our own `vs_open` chart** for that
  (villain, hero) node — not one global assumed range. This makes the ecosystem
  self-consistent: the range we defend against is the range our own bots
  actually 3-bet. It also answers the open question from the review packet
  ("is the assumed villain range right") with: it is, by construction, against
  our own field. The taper term handles nodes where that range is value-only.
- **MDF anchor is a hard constraint**, not an emergent property. The equity
  gradient decides *which* hands continue; the anchor decides *how many*.
- **Call region scores on equity + playability**, not all-in equity alone —
  flatting a 3-bet realizes through playability (suited, connected), which is
  why pure all-in-equity gradients produce call ranges that are too
  offsuit-broadway-heavy.

### 2.1 Weight discipline

- Core of each region (top 60% by score): weights ≥ 80% on the assigned action.
- Margin band: mixed 30–70% as the gradient dictates.
- Never assign ~50% weights across an entire region (this is the BB bug, §3).

### 2.2 IP/OOP node classification

OOP-postflop nodes (7): UTG vs HJ/CO/BTN, HJ vs CO/BTN, CO vs BTN, SB vs BB.
IP-postflop nodes (8): every node where the 3-bettor is SB or BB, except SB vs BB.

---

## 3. BB defense fix (`vs_open`, BB nodes)

### 3.1 The two bugs

1. **Region too narrow** — suited playable hands (Q9s, T8s, low suited
   connectors/gappers, weak suited Kx) fold outright vs late opens.
2. **Systematic half-weight calls** — the core defend region carries 45–55%
   call weights, halving effective defense. Calling 1.5 into 4.0 needs 27% raw
   (~32% adjusted for OOP realization) equity vs the open range — vs a 47.5%
   BTN range, nearly every suited hand and most connected offsuit hands clear
   that bar *at full frequency*.

### 3.2 Targets (vs 2.5bb open)

| Opener | Defend total | 3-bet | Call | Current (measured) |
|---|---|---|---|---|
| UTG | 34% | 5% | 29% | passes 30% floor |
| HJ | 40% | 6% | 34% | passes 36% floor |
| CO | 48% | 8.5% | 39.5% | under 43% floor |
| BTN | 58% | 12% | 46% | 44.6% |
| SB | 65% | 15% | 50% | 49.4% |

The gap is ~7–9 points vs late-position opens — a real, quantified overfold,
not a rebuild-from-scratch situation. UTG/HJ defense is already adequate; the
generator should leave those nodes close to current values.

Shape guidance (generator inputs, not hand-edits):

- **3-bet**: linear value top (JJ+/AQs+/AKo vs late, QQ+/AK vs early) plus the
  existing suited-bluff machinery (A5s–A2s, K9s, suited gappers) scaled to the
  target.
- **Call**: everything clearing the equity bar at **≥85% weight** in the core;
  40–60% mixing reserved for the bottom ~10% of the defend region (weak offsuit
  gappers, worst offsuit Ax vs early opens).

### 3.3 Same fix, smaller dose, for IP `vs_open` nodes

BTN vs UTG currently 3-bets only QQ+/AK and flats AQo/AJo/KQo. Fold the offsuit
broadways vs early opens; move some suited wheel-Ax and suited broadways into
the 3-bet mix. Not MDF-driven (IP cold-defense has no MDF obligation) but it
un-caps the face-up 3-bet range, which matters because §2 reads villain 3-bet
ranges from these nodes.

---

## 4. Ordering & downstream regeneration

Strict order — later stages consume earlier outputs:

1. **`vs_open` fixes** (§3) — BB defense + IP 3-bet ranges.
2. **`vs_3bet` regen** (§2) — villain ranges read from the new `vs_open`.
3. **`vs_4bet` regen** — same per-node pattern; villain 4-bet range read from
   the new `vs_3bet` node (the hands that 4-bet). Position-invariance bug
   exists here too.
4. **Depth charts (50/25bb)** — regenerate from the new 100bb chart. Measured
   against the live JSON, the depth derivation is *not* the flat-deletion
   cliff earlier drafts described (the 50bb chart retains 78% of 100bb flat
   mass — the zero-call grids in the review packet were a dominant-action
   rendering artifact). The real issues:
   (a) The flat-drop is **far too aggressive at 25bb for BB**. Even with the new
   100bb `vs_open` applied in memory, `generate_depth_charts` collapses BB defense
   to ~25% vs BTN / ~29% vs SB at 25bb — ~35pp under the 100bb targets, not the
   ≤5pp an earlier draft assumed. Fixing the 100bb baseline is necessary but **not
   sufficient**: the depth transform needs its OWN per-position BB-defense floors
   (especially at 25bb), not inherit-and-tighten.
   (b) RFI is **not** an issue — `t_rfi` is pass-through, so depth RFI is
   byte-identical to the *widened* 100bb opens (an earlier draft wrongly called it
   stale pre-widening; verified against the live JSON).
   Note also that width-tier archetypes use their identity chart at every depth
   (`tiered_bot_controller`), so depth-chart fixes reach only TAG/baseline players,
   not nit/LAG/maniac/station — see the cross-cutting findings.
5. **Archetype width-tier regeneration** — re-run the transform generator over
   the new base (per existing process; transforms are masked by base, so they
   inherit all fixes).

---

## 5. Validation & CI

### 5.1 Static chart assertions (cheap, run on every chart regen)

For each of the 15 `vs_3bet` nodes:

- `fold_to_3bet ≤ 0.65` (OOP nodes), `≤ 0.58` (IP nodes) — buffer under the
  65.2% auto-profit line.
- `4bet_weight / open_weight ∈ [0.06, 0.14]`.
- Monotonicity: continue%(of hands) strictly increases with open% across nodes
  for the same villain seat. (This single assertion would have caught the
  copied-range bug.)

For BB `vs_open` nodes: `defend ≥ {UTG .30, HJ .36, CO .43, BTN .52, SB .58}`.

### 5.2 Probe bots (sim suite, run before ship)

- **3bet-any-two bot**: 3-bets 100% vs every open. Assert its EV per 3-bet
  attempt vs the base chart is negative at every node. (Today it is positive
  vs BTN/SB opens and ~breakeven vs CO.)
- **min-raise-any-two steal bot** (BTN/SB): assert BB defense makes blind
  steals ≤ marginal.
- **jam-any-two bot**: regression-guard the existing eval7 pot-odds backstop.

### 5.3 Head-to-head acceptance

- New base vs old base, 100k+ hands with **duplicate dealing** (mirrored hole
  cards across seat-rotations) — 10k-hand sims cannot resolve multi-bb/100
  differences.
- Expect to recover most of the −3.8 bb/100 conceded in the June `vs_3bet`
  believability trade: that EV was lost to overfolding, which this spec fixes
  without reintroducing trash 4-bet bluffs (suited-only invariant retained).
- Believability check: archetype VPIP/PFR separation (Rock ~23/13 … Maniac
  ~54/47) must survive regeneration within ±3 points.

---

## 6. Out of scope (tracked separately)

- 6-max push/fold table (extend the HU fictitious-play solver).
- Postflop node coverage (generate from equity buckets × SPR × aggressor).
- UTG/HJ 2–10% any-two "noise" opens — recommend zeroing the 2% floor; random
  trash opens are pure EV burn and muddy sim calibration. Tilt-driven widening
  already lives in the psychology layer where it belongs.
