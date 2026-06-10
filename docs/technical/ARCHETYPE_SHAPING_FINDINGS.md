---
purpose: Data-grounded diagnosis of AI over-aggression, the committed-fold exploit, and the aggression-nudge calibration, plus the design for a per-archetype target-range review tool
type: design
created: 2026-06-08
last_updated: 2026-06-10
---

# Archetype Shaping — Findings & Plan

Goal: give each archetype a **target range** for behavioral stats (VPIP, PFR, 3-bet, c-bet,
AF, all-in%) and a **review tool** to measure actual-vs-target, so AI opponents become
*reliable, readable archetypes* the player can get a read on and be challenged by.

Anchor: **cash games first** (tournament play has ICM / short-stack distortions).

## Dataset note (important)

Initial analysis used the local `my-poker-face` DB, which is **96% tournament** sim/test
hands (`Tester`, `P02..P04`). That data *understated* the problem and suggested 3-bet wars
were rare. **Prod cash data (`root@178.156.202.136`, 485 cash hands Jun 5–8) tells the
opposite story and validates the anecdote.** Always anchor cash-shaping work to prod cash.

## Finding 1 — Over-aggression in cash is REAL and severe (validated)

Prod cash, 485 hands:

| metric | prod cash | healthy cash ballpark |
|---|---|---|
| hands with a 3-bet | **24.9%** | ~6–9% |
| hands with a 4-bet | **10.1%** | ~1–2% |
| hands reaching all-in | **16.9%** | ~2–5% |
| raise : call | **1.83** | ~0.5–0.9 |
| PFR / VPIP (from `opponent_observation_lifetime`) | **0.76** | ~0.45–0.65 |

Two structural drivers:
1. **Polarized preflop play** — across *every* archetype, flat-calling is rare; bots
   raise-or-fold. PFR/VPIP of 0.76 means ~3 of every 4 voluntary hands come in as a raise.
   This is why it feels like "3x raised nearly every hand."
2. **Escalating re-raise wars that end in trash shoves.** Concrete prod hands (hole cards):
   - `cash-wZ6…` h30: **Q2o 4-bet-shoves 1179**, folds out AK.
   - `cash-L7v…` h60: **K6o 3-bets then shoves 120**, folds out A5s.
   - `cash-9yX…` h15: **84s 3-bets** to 75 into 99.

Caveat: the human (Jeff Harris) is the #1 3-bettor (28 3-bets, 19 all-ins), so part of the
*felt* frequency is the human's own aggression drawing re-raises — but the AI trash-shoves
above are independent and real.

### Finding 1a — the trash-shove root cause is the `vs_4bet` stub chart (2026-06-10)

Pinned the exact mechanism on a fresh prod report (`cash-SDNe_35V--6Ndgr8WnUS7A` h38:
Alexander jams **47o**, King Midas jams **89o**, both into Hemingway's AA 4-bet shove).
The decision snapshots gave **identical** `base_strategy_probs {fold:0.645, call:0.223,
jam:0.132}` for both hands — a tell that the chart row is hand-independent. Confirmed in
`poker/strategy/data/preflop_*6max*.json`: every `vs_4bet` matchup has 169 hands but only
**3 distinct distributions** — KK/AA jam ~0.75, two hands (AKs/QQ) call ~0.67, and **all
other 165 hands share one blob**. So 72o/47o/89o get the same line as AKo/JJ facing a
4-bet: continue 35%, **jam 13%**. The lag personality distortion then bumps jam → ~0.22,
and it's sampled. `vs_3bet` is also coarse (5 distinct distributions / 169). The charts
were hand-authored with no per-hand equity source (base-chart README, Feb-2026). Nothing
downstream vetoes it: `math_floor` only *forces* a committed call, never *blocks* a −EV
call/jam (the analyzer's post-hoc `optimal=fold, ev_call=−25,640` is not in the decision path).

**Shipped (guardrail, this branch — PR):** a **facing-an-all-in equity veto** in
`tiered_bot_controller._get_preflop_decision`. Facing a cold all-in preflop the controller
decides **call/fold on pot odds** (eval7 equity vs `required_equity`) and returns
immediately, bypassing the distortion/exploitation layers — never a *voluntary* re-jam
(jam only when calling already commits the whole stack, where jam == call). Equity is a
fixed-seed local Monte-Carlo (`_preflop_allin_equity`, 600 iters) so it's deterministic and
never perturbs `self.rng`. Tests: `tests/test_strategy/test_facing_all_in_veto.py` (reproduces
h38 — 47o/89o/72o fold, AA calls). This stops the trust-killing shoves at runtime.

**Option A — `vs_4bet` regenerated as an equity gradient (SHIPPED, separate PR).**
`poker/strategy/data/build_vs4bet_defense.py` rebuilds the base chart's `vs_4bet` section
from the precomputed all-in equity matrix (`push_fold_equity_matrix.json`): each of the 169
hands gets equity vs an assumed opener 4-bet range (`VILLAIN_4BET` = QQ+/AK + A5s/A4s
bluffs), mapped to a `{fold,call,jam}` distribution — AA/KK value-jam, AKs/QQ/AKo call+jam,
JJ–44 light continue, A5s/A4s/A3s blocker bluff-jams, **everything else exactly
`{fold:1.0}`**. The pure-fold floor is load-bearing: `_loosen_facing`/`_station_facing`
(build_archetype_charts) and `t_vs_4bet` (generate_depth_charts) all skip rows with
`fold >= 0.999`, so trash stays folded across all 9 derived charts while the continue range
widens per archetype (maniac jams JJ ~12%, station flats, nit damps). Result: base `vs_4bet`
went from a **3-bucket stub (165/169 hands jam 13%)** to a **real gradient (16 continue
hands, 1.5% base jam)**; every trash hand is `{fold:1.0}` in all 9 charts. Regression test
`tests/test_strategy/test_vs4bet_gradient.py`. **EV gate** (champion_challenger TAG, gradient
vs stub, 96k hands / 6 seeds): challenger **−0.8 bb/100, CI [−4.4, +2.8] — inconclusive, no
detectable regression** (the gradient costs a competent archetype nothing head-to-head; TAG
rarely reaches a 4-bet pot with trash so the fix mostly bites looser/multiway prod spots).
**Option A2 — `vs_3bet` regenerated as a POLARIZED gradient (SHIPPED, separate PR).**
`poker/strategy/data/build_vs3bet_defense.py` rebuilds base `vs_3bet` (hero opened, faces a
3-bet, decides fold/call/4-bet) from the equity matrix vs a wider villain 3-bet range. Two
rules differ from vs_4bet: (1) **the 4-bet is polarized** — value hands + *suited* blocker
bluffs carry `raise_2.2x`; **offsuit non-value hands get call/fold only (no raise key)**, so
no archetype or distortion can 4-bet offsuit trash (kills the stub's universal 10%
trash-4-bet). (2) **junk is NOT pure-folded** (unlike vs_4bet) — it keeps a small `call` so
the station/fish `_station_facing` transform widens it (a station defends 3-bets wide);
pure-folding would collapse that. The stub (5 distinct dists / 169 hands) → real gradient
(10 dists). Test `tests/test_strategy/test_vs3bet_gradient.py` locks "offsuit junk never
4-bets" across all 9 charts.

Validated on `scripts/archetype_mixedfield_probe.py` (9000-hand mixed field, the harness the
bands were written for): **0 hard fails**; 6/7 archetypes hit `fourbet` + `fold_to_3bet`;
3 minor residual WARNs (rock 60.4 vs 65 floor, station 18.5 vs 20, lag 48.1 vs 48 — all the
"passive types defend a touch wider than the stub" signal, ≤5 pts / noise). **Maniac band
lowered per § B** (`fourbet` 26-38 → **10-24**, `fold_to_3bet` 15-40 → **15-48** in
`archetype_targets.py`): the old band was only reachable by 4-betting offsuit trash; a
polarized (suited-only) 4-bet caps at ~15% — the believable maniac the research § B calls
for. `build_loose` vs_3bet `raise_share` bumped to 0.70 (maniac amplifies the suited bluff
pool); `build_station`/`build_weak_station` vs_3bet `keep_fold` raised (0.55→0.63 / 0.35→0.45)
to hold their fold-to-3bet floor against the wider-calling gradient.

**EV tradeoff (accepted, documented).** The champion_challenger gate (TAG, gradient vs stub,
96k hands / 6 seeds) is **CI-clear NEGATIVE: −3.8 bb/100, CI [−7.3, −0.4]** — the gradient
*loses* head-to-head to the stub. Cause: the stub 4-bet-bluffs offsuit trash ~10%, and vs a
clone that over-folds to 4-bets, *more* bluffing extracts more — so removing the trash 4-bets
sacrifices fold-equity EV. This is the exact confound `champion_challenger.py`'s docstring
flags ("a bb/100 gain can mean the change is correct OR that it extracts more from a passive
opponent"); the champion here **is** the known-bad spewy stub. We ship the gradient anyway:
the believability/bands gate passes cleanly and the readable-archetype thesis (§ C) values a
non-spewy, polarized 3-bet defense over max-EV extraction from an over-folding bot. Unlike
vs_4bet (EV-neutral), this is a deliberate believability-over-EV trade, not a free win.

**Still TODO:** the `stack_utils` committed-`bet` fix.

**Adjacent bug found, NOT fixed here:** `stack_utils.effective_stack_chips` omits committed
`bet`, so when all live opponents are all-in (stack 0) it returns **0.0** effective stack
(snapshot showed `effective_stack_bb: 0.0` for Alexander at ~78bb). Fix = use `stack + bet`
(mirrors `_try_push_fold_lookup`). Deferred: it has broad blast radius (SPR/depth for *every*
decision) so it wants its own paired-EV sim, and the veto already supersedes the only spot
where it bit a live decision.

### AI-only control (the human is NOT the cause)

The human reported 3-betting/shoving *defensively* in response to the AI aggression, so the
clean signal is AI-only games. Prod has **zero** AI-only cash hands, but local eval-harness
6-max cash runs (no human, production tiered bots) settle it:

| dataset (AI-only) | raise:call | all-in% | 3-bet% | 4-bet% |
|---|---|---|---|---|
| `exp_archetyp` (mix) | 0.92 | 16.2 | 31.8 | 6.5 |
| `exp_profile_` | 2.17 | 1.6 | 44.7 | 10.1 |
| `exp_style_pr` | 1.19 | 18.1 | 54.1 | 10.8 |
| `exp_personal` | 4.66 | 8.9 | 38.2 | 23.6 |
| `exp_hybrid_v` (LLM, not tiered) | 0.94 | 11.7 | 12.8 | 1.2 |
| prod cash (human present) | 1.83 | 16.9 | 35.1 | 10.1 |

AI-only matches/exceeds the human-present table → **the over-aggression is intrinsic to the
AI**, not caused by the human. Crucially, the only *calm* row is the **LLM (hybrid) path**;
every **tiered/solver+personality** config runs hot. So the leak lives in the **preflop
solver charts + personality distortion**, not the LLM path. (Eval configs vary, so this is
strong directional evidence; a clean production-field AI-only sim will pin the exact number.)

Likely root cause (to confirm before tuning): the preflop **vs-open / 3-bet / 4-bet nodes**
and/or the **personality deviation layer** (maniac/lag flip call→raise; seen live e.g. Jim
Cramer maniac flipping `call`→`raise_3x` at effect 0.70) over-weight re-raise/jam with weak
holdings. Depth selection reads *remaining* stack, so after investing, a bot re-reads a
"fresh shallow" chart that is push/fold-tilted (`tiered_bot_controller.py:781`,
`stack_utils.py:39-48`).

## Finding 2 — Committed-fold exploit is REAL but low-frequency

A pot-committed bot can be handed `fold ≈ 1.0` and fold off a big investment. Prod cash:
~7 of 485 hands (1.4%) show a ≥60%-committed fold (e.g. A Baby folds at 87% on the flop;
Captain Ahab folds at 76% preflop).

Mechanism (confirmed via code + Codex):
- Engine makes `call` **illegal** but `all_in` **legal** when `player.stack <= cost_to_call`
  (`poker/poker_game.py:267-291`).
- The math floor that would force a call/jam **bails on exactly that case**:
  `poker/strategy/math_floor.py:106` → `if 'call' not in legal_actions: no-op`.
- Its pot-commit signal (`player_bet > player_stack`, `math_floor.py:124`) keys on the
  *current street's* bet vs remaining stack — it misses cross-street commitment.

Net: facing a shove that puts it all-in, a committed bot can fold a fold-heavy table row.
Exploit = re-raise/jam a committed bot to fold it off its equity.

**Fix (staged, not yet applied):** in `math_floor.py`, when `call` is illegal but `all_in`
is legal and the *call-off price* is good (remaining stack small vs pot → genuinely
committed), fire toward `jam` instead of no-op. Conservative threshold so trash at a bad
price still folds correctly. Needs unit tests + a before/after sim (touches all sharp bots).

## Finding 3 — Aggression-sensing nudge: not a math bug, a display + calibration issue

The "sensing aggression" you see fire in-game is the **exploitation layer** (`hyper_aggressive`
rule, `poker/strategy/exploitation.py`). Measured over 20.5k decisions it fires **2%** of the
time at **median effect 0.0012** (samples 0.0002–0.0006) — your "0.000002%."

Two reasons, both *not* a naive arithmetic error (Codex confirmed no normalization bug):
1. **Sample-confidence gating by design** — the three-tier clamp + intensity ramps require
   ~50–120 observed hands to ramp up. Against a human in a short session the sample is tiny →
   DEFAULT tier → near-zero magnitude. This protects against misfiring on noise.
2. **The displayed number is in logit space.** `effect_size` is the pre-softmax logit-offset
   L1 (`exploitation.py:1720,1742`), not the post-softmax probability change — so the figure
   shown *under-represents* the real impact.

**Recommended (low-risk):** (a) surface the **post-softmax probability delta**, and (b) only
show the nudge in-game once it crosses a meaningful threshold (e.g. ≥1–2% prob shift), so it
stops looking broken. Optionally warm the read faster vs humans via a light personality prior,
but that trades away noise-protection — measure first.

## Proposed default target ranges (cash, starting points to tune)

Per-decision/derived %s. VPIP/PFR/3bet are per-hand; AF = (bet+raise)/call postflop.

| archetype | VPIP | PFR | 3-bet | c-bet | fold-to-cbet | AF | all-in% |
|---|---|---|---|---|---|---|---|
| nit | 10–14 | 8–12 | 1–3 | 50–65 | 55–70 | 1.5–2.5 | <2 |
| rock | 14–20 | 10–16 | 2–4 | 55–70 | 45–60 | 1.5–2.5 | <3 |
| tag | 20–26 | 16–22 | 6–9 | 60–75 | 40–55 | 2.5–3.5 | <4 |
| lag | 28–38 | 22–30 | 9–13 | 65–80 | 30–45 | 3.5–5 | <6 |
| maniac | 45–65 | 35–55 | 14–22 | 75–90 | 15–30 | 5–9 | <12 |
| calling_station | 40–55 | 5–12 | 1–3 | 30–45 | 20–35 | 0.5–1.2 | <4 |
| weak_fish | 35–50 | 6–14 | 1–4 | 35–50 | 25–40 | 0.8–1.5 | <4 |

Current measured (prod cash, where samples exist) vs these targets shows the gap to close:
`calling_station` raises ~19% preflop (target PFR 5–12); `tag` flats ~2% (fine on VPIP but
zero flatting = too polarized); aggregate 3-bet/all-in rates run hot field-wide.

## Review tool design (architect blueprint — to build)

Store targets in `poker/archetype_targets.py` (`ARCHETYPE_TARGETS` dict of `{stat:(lo,hi)}`
+ `PRODUCTION_ARCHETYPES` + `score_stat()`), DB-overridable later via `app_settings`
(mirrors the model-tier override pattern). Backend route
`GET /api/admin/archetype-review/summary` aggregates **cash-only** stats from
`player_decision_analysis` (dedup on `(game_id, player_name, hand_number, phase)`, archetype
via `json_extract(strategy_pipeline_snapshot_json,'$.deviation_profile_name')`), scores
vs target → pass/warn/fail. Phase 3 pulls c-bet / fold-to-cbet from
`opponent_observation_lifetime` (proper per-hand rates). Frontend: a new admin tab
`ArchetypeReviewPanel` (archetype × stat grid, status coloring) next to Range Explorer.
Full file-by-file build sequence in the architect output (Phases 1–4).

Note: most cash snapshot rows currently carry `deviation_profile_name='unknown'` — the review
tool must surface coverage (how many decisions are actually archetype-labeled) or the grid
will be dominated by `unknown`.

## Background-sim stat capture (BUILT)

The background AI-vs-AI cash sim (`cash_mode/full_sim.py`) plays full hands with
TieredBotControllers but was **LEAN by construction** — it never wired the
hand-history or decision-analysis repos, so its entire (perpetual) decision
stream was discarded. That's why prod had **0 AI-only cash hands** and the live
grid is `unknown`-heavy: the cleanest archetype signal was being thrown away.

Note on throughput: the "~227 hands/sec" in `full_sim.py:23` is an **isolated
Phase-0 benchmark**, not the live rate. Production runs `hand_burst_count`
(`full_sim.py:194`): steady-state **0–1 hand per table per lobby refresh** while
the player is active, bursting only to catch up after an absence (cap 30/table).
Real sustained rate is single-digit hands/sec. So throughput was never the
blocker — but **counters are still the right design** because the sim runs
forever and full per-decision logging would grow `player_decision_analysis`
unbounded. Counters stay O(sandboxes × archetypes).

Implemented (lightweight counters):
- Table `archetype_stat_counts` (per-file migration `20260608_1600_archetype_stat_counts`) — per (sandbox, archetype)
  tallies: hands, pf_decisions, vpip, pfr, vs_open(+agg), vs_3bet(+agg/+fold),
  postflop_agg/call, allin_hands.
- `poker/repositories/archetype_stat_repository.py` — delta-upsert + summed read.
- `cash_mode/archetype_stats.py` — `ArchetypeStatRecorder` (in-memory tally,
  per-hand boolean roll-up, flush every 100 hands) + per-sandbox cache.
- `cash_mode/full_sim.py` — records each decision in `_run_hand` (node
  classified from preflop raise depth, controller-independent), flushes at
  hand end. Best-effort; never breaks the world tick.
- Review route gains `source=live|sim`; the panel gets a **Live (you in) / Sim
  (AI-only)** toggle — the human-in-vs-not-in comparison.

Forward-only: counters fill as the sim runs (no backfill of the discarded past).

## Implemented (this branch)

Validated with a controlled 6-max sim (hero vs 5 BaselineSolverBots), measuring
realized facing-open 3-bet, facing-3bet 4-bet, and postflop AF:

| hero | 3-bet | 4-bet | AF | before |
|---|---|---|---|---|
| baseline (no distortion) | 14.9% | 6.8% | 2.19 | — |
| tag | 17.8% | 7.5% | 3.28 | unchanged |
| lag | **32.0%** | 15.2% | 3.61 | 3-bet 44 |
| maniac | **47.1%** | 27.0% | **4.59** | 3-bet 64, AF restored |

- **Knob 0 — target denominators.** `threebet`/`fourbet` bands rescaled to the
  facing-open / facing-3bet denominator the review tool actually computes (a
  solid reg is ~15% facing an open, not ~7% of all hands). Without this the tool
  flagged correct frequencies as fails.
- **Knob 1b — looseness↔raise decouple** (`personality_modifier.py`). Looseness
  now widens *entry* (less fold → more call), not 3-bet frequency. Stops loose
  archetypes double-counting looseness as aggression. Global, all streets.
- **Pre/postflop aggression SPLIT** (the keystone). Global knobs couple the
  streets — cutting maniac's preflop 3-bet via the cap dropped its postflop AF
  *below tag's*, breaking the archetype. Added optional `reraise_aggression_scale`
  / `reraise_max_per_action_shift` to `DeviationProfile`, applied **only at
  preflop vs_open/vs_3bet/vs_4bet nodes** (controller swaps via
  `dataclasses.replace`). lag/maniac keep full global aggression (postflop AF
  preserved — maniac AF back to 4.59, field's wildest) while re-raise frequency
  drops. Maniac now fully in band; lag at its chart floor (~32%, needs Knob 2).
- **Labeling fix** — the snapshot `deviation_profile_name` used an `is` check
  against the `deviation_profile` *property*, which returns a `replace()` copy
  when a persona carries `spot_tendencies` → mislabeled as `unknown` (the prod
  "weak_fish/rock/maniac missing, ~1050 unknowns" symptom). Now uses the robust
  `_table_archetype_key()` (raw `_deviation_profile` reverse-lookup, lazy-resolved).
- **chart_label in the snapshot** — `player_decision_analysis` now records which
  base chart fed each decision (`6max:loose_mid`, `50bb`, `HU`, …).
- **Knob 2 — chart raise-damp for the passive tiers** (2026-06-08b). The loosen/
  station transforms in `build_archetype_charts.py` only redistributed *fold*
  mass; they never touched the base chart's existing *raise* mass, so every
  derived chart inherited the base's premium 3-betting (AA raises ~85% facing an
  open) and the per-action distortion cap (≤0.30) couldn't pull it down to a
  passive archetype's band. Fix: `_station_facing` gained `damp_raise` (routes
  existing re-raise mass → call — a station/fish *traps* premiums) and a new
  `build_tight()` applies the same damp to the `tight_rfi` chart (nit/rock) with
  `keep_fold=1.0` (tight range preserved). Regenerated `station`/`weak_station`/
  `tight_rfi`. Mixed-field 6k result — all passive archetypes now PASS 3-bet:
  nit 9.2→4.7, rock 12.1→5.2, calling_station 13.3→3.2, weak_fish 8.6→3.0. (Minor:
  rock PFR slipped 12.6→10.5 WARN — removing 3-bets removes preflop raises.)
- **fold-to-3bet "systemic over-fold" was a METRIC BUG** (2026-06-08c). The
  apparent "every archetype over-folds (60–86%), even the distortion-OFF Baseline
  at 82%" was ~90% measurement contamination: `classify_preflop_scenario` buckets
  `vs_3bet` by raise-count==2 with no check that the actor was the RFI raiser, so
  it swept in SQUEEZE defence (cold-call an open → face a 3-bet), which folds
  ~100% and made up 33–85% of the bucket for the wide-flatting archetypes. Fixed
  by conditioning fourbet/fold_to_3bet on the RFI opener (recorder + full_sim +
  live route reconstruction + both probes). After the fix (opener-only, 6k mixed):
  station 22, maniac 20, lag 44, weak_fish 21, rock 65, nit 59, tag 68 — 6/7 in
  band. The clean metric then exposed the real residuals: tag over-folds (68 vs
  40–58) and tag/lag/maniac 4-bet a touch high as openers (their reraise splits
  were tuned against the contaminated number). See the handoff backlog #2/#3.
- **Depth-commit at 40bb — investigated, not a leak** (2026-06-08c). All-in ~2× at
  the 40bb casino buy-in, but `scripts/commit_quality_40bb.py` (eval7
  equity-vs-random per committed hand) shows 4-bet ranges are value-weighted (mean
  0.61–0.71, 0% trash) — the "Q2o 4-bet-shove" symptom doesn't reproduce post-#240.
  Consistent with `SOLVER_CHART_SCOPE` (solver parked; shallow "collapse" was a
  Jeff_station artifact). Depth-aware *sizing* stays a feel/tell item, not a fix.
- **tag over-fold to 3-bets — FIXED via `defend_3bet` spot tendency** (2026-06-09).
  Once #2's metric fix cleaned the measurement, tag was a real residual: fold_to_3bet
  68.3 (band 40–58) — over-*polarized* (4-bet-or-fold, flats too little). It's
  CHART-driven (distortion-OFF Baseline folds 61% on the shared base chart), so the
  fix can't be the shared chart. New `defend_3bet` tendency (`spot_tendencies.py`)
  gates on `scenario=='vs_3bet'` and routes fold→call + a slice of 4-bet→call
  (de-polarize). It's the first PREFLOP-scoped spot tendency — added a separate
  preflop call to the layer (`tiered_bot_controller._layer_preflop_spot_tendencies`,
  `street=None`; the postflop helper reads PostflopNode-only fields). Picked over a
  scenario-scoped chart override after 2 architect blueprints + 2 adversarial reviews
  + codex (chart override overshot its transform + missed 2 of 4 controller
  construction paths; the tendency is lower blast radius). `('defend_3bet', 0.24)`
  on tag → fold 68→**49.8**, 4-bet 16→**13.3** (6k mixed); every other archetype
  byte-identical (the no-op-preflop invariant, test-locked). Range-quality A/B
  (`scripts/tag_vs3bet_range.py`): newly-defended hands are textbook (AJo/AQo/KJo/
  77/TT…, meanEq 0.59) not trash; 4-bet stays value-weighted. EV gate
  (`champion_challenger --change tag_defend --archetype TAG`, 30k antithetic):
  +1.1 bb/100, CI [−5,+7] — no harm.

## Prioritized plan

1. **Build the review tool (measurement first)** — can't tune what we can't see; also gives a
   before/after harness for every fix below. (Architect blueprint ready.)
2. **De-escalate preflop aggression (biggest lever)** — root-cause the 3-bet/4-bet/jam-with-
   trash in the preflop nodes + personality layer; add a flat-call band so entering ≠ raising.
3. **Committed-fold fix** — `math_floor.py` call-off-all-in case (staged).
4. **Nudge UX** — show post-softmax delta + display threshold.

---

## Research-doc validation & the believability thesis (2026-06-09)

Two external research briefs were validated against the shipped system:
[[../vision/texas_hold_em_research_text_markdown]] (archetype benchmarks, study
methodology, the Poki/"Stacked" deep dive) and
[[../vision/poker_aggression_benchmarks_text_markdown]] (live-vs-online aggression
data + designing a believable aggressive archetype). **Headline: our measurement
methodology is sound and our band *means* are mostly live-faithful — the real gap
is that aggression is context-free and invisible, which is the exact failure that
sank "Stacked."**

### A. Methodology validation (vs PokerTracker 4 / Hold'em Manager 3)

Our `archetype_review_routes._aggregate` formulas match the canonical
*opportunity-based* definitions:
- VPIP/PFR per preflop hand-instance ✓ (minor: we don't exclude *walks* from the
  denominator the way PT4 does — negligible at 6-max).
- 3-bet = raise at `vs_open` / decisions facing an open ✓ — the canonical
  opportunity denominator (`cnt_p_3bet / cnt_p_3bet_opp`).
- fold-to-3bet & 4-bet **opener-conditioned** ✓ — *exactly* PT4's rule that only
  the initial raiser has a fold-to-3bet opportunity (this is the Finding-2 metric
  fix; we got it right).
- AF = postflop (bet+raise)/call ✓.

Gaps to close (all cheap; `archetype_review_routes` + `cash_mode/archetype_stats`):
1. **AFq** = (bet+raise)/(bet+raise+call+fold). AF alone can't separate a
   fit-or-fold nit from a maniac (both rarely call → both trend high-AF). Our
   **nit AF band (1.5–2.8) silently assumes the nit calls postflop** — a real nit
   folds its non-value and plays value hard → *high* AF, so that band may be
   structurally unhittable. AFq fixes the discriminator.
2. **WTSD + W$SD** — untracked anywhere. `high WTSD + low W$SD` is *the*
   calling-station signature and the most player-legible "calls too much" read.
3. **Per-street AF** — aggregate postflop AF hides flop-maniac/turn-passive texture.

### B. Target-band corrections

Key reframe from the aggression brief: **online tracker benchmarks are the WRONG
reference population.** Live players 3-bet *more* (winning live reg ~13% vs ~8–10%
online — Hand2Note 972k-hand live DB), fields are looser/deeper/multiway, and
folds-to-3bet are rare. So our elevated bands are *directionally correct* — do
**not** lower tag/lag toward online numbers.

| archetype | our `threebet` band (facing-open) | research verdict | action |
|---|---|---|---|
| tag | 10–16 | live-faithful (live reg ~13%) | **keep** |
| lag | 16–26 | "high edge but defensible" for live recreational | **keep** (watch ceiling) |
| **maniac** | **36–52** | **above realistic *sustained* ceiling** (live maniac ~15–25% even by expert estimate; 15% is already "spewy" online) | **lower baseline to ~20–25; make 30+ a *conditioned/tilt* state, not a flat constant** |

Unambiguous (VPIP is total-hands, no denominator subtlety):
- **`rock` band is inverted.** Our rock target (VPIP 15–22 / PFR 11–17) is *looser
  & more aggressive than our own nit* (10–16 / 8–13), but `deviation_profiles`
  makes rock *tighter* (looseness 0.7 < nit 1.2, both on `tight_rfi`). The targets
  predict the opposite VPIP ordering from what the strategy produces → a sim will
  mis-flag. Research def: rock = tightest + most passive (VPIP ~10–14, big
  VPIP−PFR gap). **Rebuild rock band ≤ nit, lower PFR.**
- `lag` VPIP ceiling (40) blurs into station/maniac; loose live fields justify it,
  but note it.

> Earlier-turn hedge resolved: I initially worried the maniac/lag ceilings might
> just be a denominator artifact (the texas-holdem brief's §1B 3-bet column appears
> to mix total-hands and opportunity numbers — the trap it warns about). The
> aggression brief anchors cleanly to PT4 HUD (opportunity) numbers, so the
> maniac-too-high verdict is apples-to-apples and real; tag/lag are genuinely fine.

### C. The believability thesis (all three sources converge)

> **A high frequency is realistic; a *constant* high frequency is a caricature.**
> "Stacked" (2006) carried genuine world-class adaptation (Poki's per-opponent
> weight tables) and was *still* "readable in ~40 hands" — because aggression was
> monotonic and **the adaptation was imperceptible.** This is flanderization, and
> it is **our Finding 3 verbatim**: the exploitation layer fires ~2% of the time at
> ~0.0012 logit effect, sample-gated, never surfaced.

The fix is not band numbers — it's **conditioning + variance + perceptibility.**

**Poki — what players loved → our equivalent:**

| Loved | Mechanism | Ours |
|---|---|---|
| "it learns and reacts" | per-opponent weight table over 1,081 hands, re-weighted each action | `exploitation.py` + `opponent_observation_lifetime` |
| emergent tactics (un-scripted check-raise) | simulation / selective sampling | solver tables + EV options |
| "Ask Daniel" coaching | in-hand advice | our coach (Assistant tier) |

**Poki — what players hated = our open bug list:**

| Hated in Stacked | Our finding |
|---|---|
| "pre-tuned, readable in 40 hands; adaptation made no difference" | **Finding 3** (exploitation invisible) |
| "calls all-ins with junk; folds to small reraises" | **Finding 1** (trash 4-bet shoves) + **Finding 2** (committed-fold) |
| "coaching out of sync with the action" | coach now off the hallucinating 8B tier — keep grounded |

**Cross-game lessons → the work:**
- **Nemesis** (memory → explicit callbacks): *speak* the opponent-model read
  ("you've folded every 3-bet tonight"). Perceived memory *is* the relationship.
  We have heat/respect + `dramatic_sequence` but never voice the read.
- **F.E.A.R.** (intelligence via dialogue): voicing the read makes the exploitation
  layer *legible* even at small magnitude.
- **Alien: Isolation** (progressive unlock = illusion of learning): reframes
  Finding 3's sample-gate from a hidden weakness into an audible "figuring you out"
  arc. Ties to `docs/plans/PREDATOR_LOADOUTS.md`.
- **Drivatars** (fidelity without bad habits; visible rubber-banding breaks trust):
  keep adaptation framed as "reading *you*," never house-rigging.

**The target player experience (the readability/depth arc):**
- `<40 hands`: "that's the aggressive guy" (readable archetype — good).
- `~200 hands`: "he 3-bets my steals but folds to my 4-bets when calm" (a
  learnable *conditional* exploit).
- under tilt: the read *inverts* (he won't fold) → re-read.

**Conditioning levers** (priority order, per the aggression brief): opponent-specific
memory > position > tilt/emotional state > table image/recent history > stack
depth/straddle. **Tilt is the best variance tool** — Tendler's 7 tilt types
(running-bad, injustice, hate-losing, mistake, entitlement, revenge, desperation)
each have a distinct *trigger* = direct fuel for the emotion + relationship layers;
the avatar must *telegraph* the spike so it's earned and readable.

**Playtest decision thresholds:**
- archetype ID'd in `<40` hands AND never surprises → aggression too monotonic (add conditioning).
- no articulable exploit after ~200 hands → variance too random (tighten the rules).
- maniac *session-average* 3-bet `> ~25%` sustained → drifting to caricature (cap baseline, let only transient states exceed).
- tilt spikes not attributable to a visible cause → strengthen avatar/table-talk telegraphing.

Actionable items land in the handoff backlog (`docs/plans/ARCHETYPE_SHAPING_HANDOFF.md`
#9–#12).
