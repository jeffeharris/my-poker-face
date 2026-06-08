---
purpose: Data-grounded diagnosis of AI over-aggression, the committed-fold exploit, and the aggression-nudge calibration, plus the design for a per-archetype target-range review tool
type: design
created: 2026-06-08
last_updated: 2026-06-08
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
- **NEW open finding — every archetype over-folds to 3-bets** (mixed-field 6k:
  nit 86, rock 83, tag 78, lag 60, maniac 67, station 83, weak_fish 70; Baseline
  control 82). The vs_3bet defense is too fold-heavy AND its spread across
  archetypes is compressed (a maniac should defend, a nit should fold). Root: base
  chart vs_3bet folds ~73% combo-weighted + the loosen `keep_fold` at vs_3bet +
  the per-action cap. Now the top backlog item — see the handoff. Settle whether
  the *targets* are slightly low (real nits do fold ~75–85%) before chasing.

## Prioritized plan

1. **Build the review tool (measurement first)** — can't tune what we can't see; also gives a
   before/after harness for every fix below. (Architect blueprint ready.)
2. **De-escalate preflop aggression (biggest lever)** — root-cause the 3-bet/4-bet/jam-with-
   trash in the preflop nodes + personality layer; add a flat-call band so entering ≠ raising.
3. **Committed-fold fix** — `math_floor.py` call-off-all-in case (staged).
4. **Nudge UX** — show post-softmax delta + display threshold.
