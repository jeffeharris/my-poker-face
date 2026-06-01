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

## 5b. T1 RESULT (2026-06-01) — mechanism works, supply too thin

Built the bluff side in `apply_overbet_context` (`overbet_bluff_fraction`/
`overbet_bluff_classes`, default OFF = byte-identical) + controller fields +
`ab_node_attribution --overbet-bluff-a/-b`. Measured `+bluff vs value-only`
(`overbet_bluff_fraction=1.0`, the ceiling), HU vs jeff, 30000 hands:

| | bb/100 |
|---|---|
| vs **oracle** (sizing-reader) | **+3.37** [+0.98, +5.77] |
| vs **normal jeff** (caller) | **−5.66** [−8.65, −2.67] |

- **Direction confirmed** (CI-clear +3.37 vs the reader) but recovers only ~15% of
  the −22 leak. The turn/river air supply at the overbet nodes is **thin** — most
  air has checked/folded by then — so even routing 100% of it adds little bluff
  mass; the overbet stays mostly value and the oracle keeps folding correctly.
- **Cost vs callers (−5.66) > gain vs readers (+3.37)** → the gate is mandatory,
  and with a weak reader-detector, T1-alone is net-negative-risk.
- `fraction=1.0` is the MAX injection, so calibration only slides the −5.66↔+3.37
  tradeoff — it can't raise the +3.37 **ceiling**, which is set by air supply.

**Verdict: T1 (reroute existing air) is necessary but INSUFFICIENT.** Closing −22
requires *creating* bluff supply — T2 (river bluffs from give-up air) + barreling
more air to turn/river (the bigger change). The T1 mechanism is the infrastructure
that bigger build will drive; kept dormant (OFF) in production.

## 5c. READABILITY AUDIT + T2 + the regime gate (2026-06-01, fresh-context session)

**New instrument — the size→strength "tell map"** (`measure_passivity --tell-map`):
for each (street, bet-size bucket) it reports the hand-class composition of the
hero's own betting range + the bluff share vs the GTO target `s/(1+2s)`. It's a
*readability* audit (no human/oracle needed) and reusable on a human's hand history
to find *their* tells (training branch). Additive; default-off byte-identical.

**Finding — the leak is the RIVER, not the turn** (TAG, HU + 6-max, station + reg,
all reproduce):

| street | big-bet composition | verdict |
|---|---|---|
| TURN | ~target bluff ratios at every size | **balanced — NOT the leak** |
| RIVER | pot+ bets **90–100% value, ~0% bluff** (gap −25 to −44) | **FACE-UP** |
| FLOP | over-bluffed (+33/+42) | low-confidence (merge-confounded) |

This **reframes the prior session's turn-overbet focus** — the turn was already
balanced (which is *why* T1's reroute-turn-air recovered only ~15%). The face-up
river lives in the **base strategy** (overbet_context is dormant in this harness),
and the bot gives up its air into `check` by the river → big river bets are pure
value → a reader folds to them free and stabs the capped river checks.

**T2 mechanism built** (`_promote_check_to_bet` in `overbet_context.py`):
promotes a fraction of give-up-air river CHECK mass to a bet at the overbet size —
*creates* supply T1 can't (no river air bet-mass to relabel). Params
`river_bluff_fraction` / `river_bluff_classes` / `river_bluff_size`; default OFF
byte-identical. Wired into the controller + both eval harnesses
(`measure_passivity` env-knob, `ab_node_attribution --river-bluff-a/-b`).

**T2 measured (fraction 0.5, paired CRN, 24000h HU):**

| | bb/100 | 95% CI |
|---|---|---|
| vs **oracle** (sizing-reader) | **+1.90** | [+0.57, +3.23] |
| vs **normal jeff** (caller) | **−7.18** | [−8.83, −5.54] |

Same shape as T1: helps vs a reader, hurts vs a caller; supply thin (1.4% of
hands diverge — all river). The oracle **understates** the gain (it only *folds*
to overbets, never "pays off value because forced to defend") → +1.90 is a lower
bound. **Ungated, net-negative in a caller-heavy pool → the gate is mandatory.**

**The regime gate (the completing build).** River bluff fires ONLY vs a detected
over-folder/reader (`fold_to_big_bet >= river_bluff_min_ftbb`, default 0.6 — the
existing over-folder threshold). Cold-start / no read / a caller → value-only.
First consumer of the Phase A `fold_to_big_bet` read. Resolver
`_resolve_river_bluff_ftbb` (HU only for MVP; requires a matured read
`_big_bet_faced_count >= 8`). Eval override `river_bluff_ftbb_override` /
`--river-bluff-ftbb` simulates the read (no model manager in sim).

**Gate VALIDATED (paired CRN, 24000h HU):**

| gated arm | bb/100 | CI |
|---|---|---|
| vs **reader** (ftbb 0.8) | **+1.90** | [+0.57, +3.23] — gain kept (= ungated) |
| vs **caller** (ftbb 0.2) | **+0.00** | [0, 0], 100% no-divergence — cost killed |

So the fix is a vs-human win at **zero fish cost**: it fires *specifically* against
the opponents who punish the face-up river, and stays value-only vs everyone else.
**Honest caveats:** (1) the gated benefit can't be fully measured in sim (no reader
population + the oracle only folds, never pays off → +1.90 is a lower bound); ships
on theory + the tell-map confirmation that the range balances vs a reader. (2) the
read's real-world maturity/accuracy (does `fold_to_big_bet` converge fast enough,
correctly classify a human) is itself unmeasured here — the gate is only as good as
the read feeding it.

**Raise audit DONE (2026-06-01).** Split the tell map by bet-vs-raise context
(`ctx` column). Finding — **raising readability MIRRORS betting; no separate
gaping hole:**

| raise context | composition | verdict |
|---|---|---|
| TURN raise (jam ~3.8x) | 40% val / 47% blf, blf/pol 54% vs target 44% | **balanced — has bluffs** |
| RIVER raise (~pot) | 82–100% val, ~10% blf (gap −23) | under-bluffed/thin, **low freq** (n≈22/10kh) |
| FLOP raise (check-raise) | over-bluffed | low-confidence (merge artifact) |

The turn/flop raise ranges already carry bluffs; the only face-up raise spot is the
RIVER (same direction + street as the bet leak, much lower frequency). So **the
river BET fix (T2) is the dominant lever**; a river check-raise-bluff is an
analogous minor secondary lever, not a priority. Raise samples are thin (the bot
seldom raises, esp. vs a station) → moderate confidence, but consistent across
station + reg.

**Preflop 3-bet audit DONE (2026-06-01).** Added a preflop-3-bet-readability block
(`pf_tier_action`, hand tier via `hand_ranges._classify_hand_tier`): per scenario,
the raise range's tier composition. 6-max vs jeff (real sample):

| raise | n | value% (prem+strong) | non-prem% |
|---|---|---|---|
| open (RFI) | 1430 | 19% | 81% |
| 3-bet (vs_open) | 232 | 18% | 82% (57% trash-tier) |
| 4-bet (vs_3bet) | 49 | 43% | 57% |
| fold-to-3bet | 419 | — | 80% fold / 9% call / 12% 4bet |

**Preflop raising is NOT face-up — the OPPOSITE of the river.** The 3-bet range is
wide and heavily non-premium (lots of light 3-bets) → no "reader folds to every
3-bet" leak; for the vs-human goal a bluff-mixed 3-bet range is good. Caveats:
(1) `_classify_hand_tier` is absolute full-ring → A5s/suited-gappers (the standard
3-bet bluff candidates) score as "trash", so "82% non-premium" overstates bluffiness
— a position-relative tiering is needed to tell "well-polarized" from "over-bluffing"
(if anything the bot may OVER-3-bet-bluff, an EV-vs-fish question, not a readability
one). (2) fold-to-3bet 80% looks high but is a wide open shedding its bottom; the
prior steal-defense isolation already found vs_3bet defense ~neutral (not a leak).

**COMPLETE readability map (all dimensions audited):** preflop wide/mixed (not
face-up) · flop over-bluffed (merge artifact) · turn balanced (bets+raises) · RIVER
face-up (bets; raises thinly). **The whole aggressive game has exactly ONE
readability leak: the river bet — which T2+gate addresses.** No new fix indicated.

## 5d. CALIBRATED + TURNED ON (2026-06-01)

Calibration sweep (tell map under production config: `overbet_fraction=1.0` value
relabel ON + gate firing as a reader), river overbet (1.5x, GTO target ~37%):

| river_bluff_fraction | overbet bluff share | gap |
|---|---|---|
| 0.0 (baseline) | ~5% | −32 (FACE-UP) |
| 0.25 | 9% | −28 |
| 0.5 | 19% | −19 |
| 0.75 | 25% | −13 |
| **1.0** | **31%** | **−7** |

**Supply caps the bluff share at ~31% even at full injection** (the thin-supply wall,
quantified) → no over-bluff risk, so the calibrated value is **`river_bluff_fraction
= 1.0`** (max), taking the river overbet from face-up (−28) to near-balanced (−7).
Set as the production `__init__` default (was 0.0). FIRES only behind the regime
gate (mature `fold_to_big_bet >= 0.6`), so value-only vs fish/cold-start. Eval
harnesses bypass `__init__` → unaffected; this turns it on in REAL games only.
Resolver `_resolve_river_bluff_ftbb` unit-verified end-to-end (reader fires, caller/
immature/multiway/cold-start → value-only, override wins). 148 overbet/tiered +
full strategy suites green.

**Residual −7:** see §5e — the obvious "barrel more air to the river" supply build
was built + measured and is a NO-OP (the residual is structural). Other caveats
unchanged: the live read's real-world accuracy is untested (false-positive cost vs a
misclassified caller grows with fraction — ~−7 bb/100 at 0.5 in the sweep); dial
`river_bluff_fraction` down if the read proves noisy in production.

## 5e. River-air SUPPLY build — TRIED + REJECTED (no-op, 2026-06-01)

Hypothesis: T2's ~31% bluff-share cap is because the bot gives up air on the turn,
so little reaches the river. Fix: barrel turn air (gated turn `air_no_draw` barrel
in `multistreet_context`, same reader gate + HU + turn-only) so more air survives to
the checked-to river for T2. Built OFF-by-default (`air_barrel_target`), measured.

**Result (HU vs jeff, mode on, river_bluff=1.0, reader):**

| air-barrel | river overbet bluff% | promote count | bb/100 |
|---|---|---|---|
| OFF | 25% | 205 | +94.0 |
| ON (0.5) | 25% | 199 | +94.9 |

**NO-OP** (barrel confirmed firing — turn air-bet share shifted, e.g. xs 50%→73%).
**Why the premise was wrong:** give-up air ALREADY reaches a checked-to river — air
dies from *folding to a bet*, not from *checking* the turn. So barreling turn air a
street earlier adds zero river bluff candidates (and air raised off the turn slightly
*reduces* supply: 205→199). The ~31% cap is **structural** — set by the natural
air:value ratio in the river range — not a turn-give-up problem. The only levers that
move it are unattractive (float air to the river → faces a bet → not an unopened node
→ T2 can't fire; or relabel less value to the overbet → dilutes the +EV value bet,
moves the tell to smaller sizes). **Verdict: accept ~31% (river_bluff_fraction=1.0)
as the achievable balance; the residual −7 is structural.** Mechanism kept dormant
(`air_barrel_target=0.0`) as a documented measured-negative.

## 6. Build sequence
1. **T1 turn overbet-bluffs** (reroute existing air/draw mass) + the gate (§3.3) +
   config flag. Measure vs oracle (−22 → ?) and fish cost. *This is the MVP.*
2. **T2 river overbet-bluffs** (blocker-selected give-up air) — only if T1's oracle
   gain is real and river supply exists.
3. Tune the cushion/threshold against `hand_history` board-texture data.
4. (Deferred) generalize beyond the overbet to other face-up sizes.
