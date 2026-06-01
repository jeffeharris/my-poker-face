---
purpose: Finalized minimal design for de-face-up'ing the tiered bot's value-overbet by adding balanced overbet-bluffs — the one proven human-exploit fix
type: design
created: 2026-06-01
last_updated: 2026-06-01
---

# Overbet balancing — finalized minimal design

> **One-line goal.** The tiered bot's value-overbet is *face-up* (pure value), so a
> sizing-reader folds to it for a measured **−22 bb/100** (oracle, CI [−28,−16]).
> Add *balanced overbet-bluffs* so folding to the overbet stops being profitable —
> the single human-exploit that survived this session's skeptical isolation. Scope
> is deliberately minimal: change one layer (`overbet_context`), reuse the existing
> oracle as the instrument, gate hard so it never spews into the fish.

## 1. The measured leak (why this and nothing else)

- `ab_node_attribution jeff … --overbet-b [--adaptive-opp]`: value-overbet =
  **+60.9** vs a non-reader, **−22.2 [−28,−16]** vs the oracle (perfect sizing
  reader = a competent human). ~83 bb/100 swing. **Real** (tight CI; clean
  bet-size-relabel mechanism; oracle is perfect-read, not a weak clone).
- Three other candidate fixes (§3 adaptive, Phase B defense, steal-defense) were
  **killed by clean isolation** as artifacts/low-leverage. This is the only one left.

## 2. Supply finding — the range isn't face-up, the OVERBET is

Tiered bot, free-to-act bet rates by hand class (HU vs jeff, 6000h):
- Of everything it *bets*, ~22% is pure air + ~18% weak_made → a ~40%-non-value
  betting range (≈ balanced; GTO wants ~33% bluffs at pot). **Not face-up.**
- But `overbet_context` relabels **only `nuts`/`strong_made`** to 1.5× → the
  *overbet* is **0% air**. The **sizing** is the tell: overbet ⇒ always value
  (fold), normal bet ⇒ mixed (defend).

**Street split (the overbet fires TURN+RIVER):**
- **TURN** has bluff supply already: `air_no_draw` bets 16%, `air_strong_draw`
  (semi-bluff) bets 71%. → reroute existing air/draw bet-mass into the overbet.
- **RIVER** barely bluffs air (gives it up) — yet the river overbet is the *most*
  face-up (no draws, fully polar) and where the leak concentrates. → must
  **create** river bluffs from give-up air (blocker-selected).

⇒ **Two tiers: T1 (turn, reroute existing air — easy) and T2 (river, create
blocker-selected bluffs — the real work).**

## 3. Design

### 3.1 Bluff selection (which hands become overbet-bluffs)
- **Turn:** the hands the bot *already bets* as air/semi-bluff (`air_no_draw`,
  `air_strong_draw`, low `weak_made`). Reroute a fraction of that bet-mass to the
  overbet size instead of the normal size.
- **River:** *give-up air* (hands the bot currently checks), filtered to the best
  **bluff candidates**: top blockers to villain's continue/value range, no/low
  showdown value. (Blocker scoring is the one genuinely new primitive — start with
  a coarse "blocks the nut/flush/straight" heuristic; refine later.)

### 3.2 Frequency (how much)
- Target the **balanced bluff fraction** of the overbet range: for a bet of size
  `s` (pot-fractions), unexploitable bluff share `= s/(1+2s)` → **1.5× overbet ⇒
  ~37.5% bluffs**. Compute the value-overbet mass already relabeled by the layer,
  then add overbet-bluff mass = `(0.375/0.625) × value_overbet_mass`. Cap by the
  available air/give-up supply (don't invent mass that isn't there).
- This is a *frequency*, sampled like the rest of the strategy profile — not "always
  overbet this air."

### 3.3 The gate — never spew (this is the safety-critical part)
Fire the overbet-bluff ONLY when ALL hold:
1. **Dry board** — reuse `overbet_context`'s existing dry-turn gate (raises true fold
   equity + kills the wet-board over-bluff error from board correlation).
2. **Multiway veto** — `∏ fold_to_big_bet_i ≥ breakeven(size) × cushion` over the
   *continuing* opponents (Phase A `fold_to_big_bet`). Any live **station**
   (won't fold) or **unmatured read** ⇒ veto. Mirrors
   `_continuing_opponents_block_bluff_catch`. The cushion grows with board wetness
   and opponent count (independence is biased toward over-bluffing — err to value).
3. **Regime switch** on the (single, HU) opponent read:
   - **over-folder** (`fold_to_big_bet` high, not a reader) → *exploit*: size the
     bluff for max fold equity (`≤ F/(1−F)`), balance not required.
   - **competent / unknown-but-not-fish** → *balance*: bluff at the value size,
     hit the §3.2 ratio so a reader can't profitably fold.
4. **Cold-start default = value-only** until reads mature → safe vs the unknown
   casino fish (and multiway naturally collapses here).

### 3.4 Integration point
`poker/strategy/overbet_context.py`, where it already relabels value bet-mass to
`bet_150`. Extend `apply_overbet_context` to *also* relabel a gated, calibrated
share of air/weak (T1) / give-up-air (T2) mass to the overbet size. Runs after
`multistreet_context` (frequency) and before `defense_floor`, behind
`enable_overbet_context` + a new `overbet_bluff_*` config so the OFF arm is
byte-identical. Needs the aggressor's per-opponent `fold_to_big_bet` + a station
check at the decision point (the controller already plumbs `spots` + the model
manager to the bluff-catch layer — reuse that path).

## 4. Measurement plan (the gate before merge)
- **Human-robustness (headline):** `ab_node_attribution … --overbet-b --adaptive-opp`
  — the overbet's EV vs the oracle should move **−22 → ~0** (a balanced overbet
  can't be profitably folded to). The primary success metric.
- **Fish-EV cost:** the same overbet ON/OFF vs stations/`WeakFish`/`jeff` (no
  oracle) should stay **≥ ~0** — confirm bluffing didn't start spewing into callers.
  The multiway veto + cold-start should keep this near zero.
- **Spew guard / no-regression:** vs the rule-bot gauntlet, the overbet-bluff arm
  must not go negative anywhere (esp. vs `CallStation`/`Maniac`). Paired CRN.
- **Supply realism:** confirm T2 (river) actually finds enough blocker-air to hit
  the frequency; if not, river stays value-only and we accept partial-fix (turn
  only) rather than inventing mass.

## 5. Risks / honest caveats
- **River supply may be too thin** → T2 underfills the ratio → the river overbet
  stays partly face-up. Acceptable: T1 (turn) alone already moves the number; T2 is
  upside, not a gate.
- **Independence is biased toward over-bluffing** on wet/correlated boards →
  mitigated by the dry-board gate + wetness cushion (§3.3). Verify against
  `hand_history` multiway big-bet spots before trusting the veto threshold.
- **The oracle only tests the OVERBET.** A win here de-face-ups the *overbet* size,
  not the bot's whole game — other sizes still have their own (smaller) tells. This
  is the highest-leverage slice, not a complete "harder for humans" solution.
- **Regime classifier for "competent reader" is weak** (no clean signal) → default
  to *balance* when not-clearly-a-fish; that's the safe-vs-human default and costs
  little vs fish (balanced overbets still get paid by callers, just sized at value).

## 6. Build sequence
1. **T1 turn overbet-bluffs** (reroute existing air/draw mass) + the gate (§3.3) +
   config flag. Measure vs oracle (−22 → ?) and fish cost. *This is the MVP.*
2. **T2 river overbet-bluffs** (blocker-selected give-up air) — only if T1's oracle
   gain is real and river supply exists.
3. Tune the cushion/threshold against `hand_history` board-texture data.
4. (Deferred) generalize beyond the overbet to other face-up sizes.
