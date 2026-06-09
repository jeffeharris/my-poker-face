---
purpose: Handover for the archetype-shaping workstream — what's shipped, how to measure/tune, and the prioritized backlog with file refs so a fresh context can execute
type: guide
created: 2026-06-08
last_updated: 2026-06-09
---

# Archetype Shaping — Handover

## Session update (2026-06-09b) — research-doc validation; believability is the next frontier

Validated two external briefs ([[../vision/texas_hold_em_research_text_markdown]],
[[../vision/poker_aggression_benchmarks_text_markdown]]) against the shipped system.
Full writeup: `ARCHETYPE_SHAPING_FINDINGS.md` → "Research-doc validation & the
believability thesis." Headline:

- **Methodology is sound** — our formulas match PT4/HM3 opportunity denominators
  (incl. the opener-conditioned fold-to-3bet/4-bet). Gaps: add **AFq** (AF can't
  separate a fit-or-fold nit from a maniac), **WTSD/W$SD** (the station signature),
  per-street AF → backlog **#11**.
- **Band means mostly live-faithful** — online benchmarks are the *wrong*
  population (live regs 3-bet more). **Keep tag/lag — do NOT lower them.** Two
  corrections: **maniac 3-bet (36–52) is too high *sustained*** → lower baseline
  ~20–25, make 30+ conditional (**#9**); **rock band is inverted** (looser than
  nit, but the strategy makes it tighter) → rebuild ≤ nit (**#10**).
- **The real gap is perceptibility, not numbers.** Stacked had great adaptation and
  was *still* "readable in 40 hands" because it was monotonic + invisible — our
  Finding 3 exactly. Highest-leverage work = surface the read + condition the
  aggression (**#12**).

## Session update (2026-06-08c) — fold-to-3bet was a metric bug; depth-commit ruled out

- **fold-to-3bet "systemic over-fold" was ~90% MEASUREMENT contamination** (squeeze
  defence counted as fold-to-3bet). FIXED by opener-conditioning fourbet/fold_to_3bet
  across the recorder, full_sim, live route, and both probes. 6 of 7 archetypes now
  in band; details in backlog #2 below. New `scripts/foldto3bet_attribution.py`
  splits the bucket RFI-vs-3bet vs squeeze. Tests: `test_archetype_review_route.py`
  + a squeeze case in `test_archetype_stats.py`.
- **Exposed residual** (backlog #3, next): tag over-folds + tag/lag/maniac 4-bet a
  touch high as openers — masked until the metric was cleaned.
- **Depth-commit at the 40bb casino buy-in — INVESTIGATED, not a leak.** All-in
  rate ~2× at 40bb vs 100bb, but `scripts/commit_quality_40bb.py` (eval7
  equity-vs-random of every committed hand) shows the 4-bet ranges are
  value-weighted (mean eq 0.61–0.71, **0% trash**) — the prod "Q2o 4-bet-shove"
  symptom does NOT reproduce post-#240. Matches prior art (`SOLVER_CHART_SCOPE`
  parked the solver; Sweep A/D found shallow stacks fine). Elevated all-in is
  correct low-SPR poker + the fish's designed postflop stickiness. A depth-aware
  *strength* fix is NOT warranted; depth-aware 3-bet/4-bet *sizing* remains a
  feel/tell item (→ backlog #7 sizing variety), not a leak.

## Session update (2026-06-08b) — all 7 validated, passive 3-bet fixed

Ran backlog #1 (validate all 7 archetypes). Built two measurement upgrades and
landed one tuning fix:

- **Probe upgraded to full-stat** (`scripts/archetype_3bet_probe.py`): now tallies
  every banded stat (vpip/pfr/threebet/fourbet/fold_to_3bet/af/all_in) for all 7
  archetypes and scores vs `ARCHETYPE_TARGETS` (pass/warn/fail), not just 3bet/4bet.
- **New mixed-field probe** (`scripts/archetype_mixedfield_probe.py`): seats 6 of
  the 7 archetypes at one table (rotating the sit-out) so each is measured vs a
  realistic field — the proper *absolute* instrument (the all-Baseline probe's
  tight field inflates fold-to-3bet). Local stand-in for the review tool's
  `source=sim` until a stack accrues `archetype_stat_counts`.
- **FIXED — passive archetypes over-3-bet** (all now PASS, mixed-field 6k hands):
  nit 9.2→4.7 (2–7), rock 12.1→5.2 (4–9), calling_station 13.3→3.2 (1–5),
  weak_fish 8.6→3.0 (2–7). Root: the loosen/station chart transforms only
  redistributed *fold* mass; they never damped the base chart's existing *raise*
  mass, so every derived chart inherited the base's premium 3-betting (AA raises
  ~85% facing an open). The per-action distortion cap (≤0.30) can't pull an 0.85
  raise to a nit's ~5% — the **chart** is the lever. Fix is in the generator
  (`experiments/build_archetype_charts.py`): `_station_facing` gained a
  `damp_raise` param (routes existing re-raise mass → call: a station/fish *traps*
  premiums) and a new `build_tight()` applies the same damp to the `tight_rfi`
  chart (nit/rock) with `keep_fold=1.0` so the tight range is preserved. Re-ran
  the generator → regenerated `station`, `weak_station`, `tight_rfi`. 1498 strategy
  tests green. **VPIP/PFR/AF/4bet stayed in band; PR #240's looseness→raise decouple
  confirmed** (nit VPIP 15.0, station PFR 9.6 — earlier nit-too-loose /
  station-over-PFR are resolved). Minor: rock PFR slipped 12.6→10.5 (WARN, 0.5
  below floor) since removing ~7pts of 3-bets removes preflop raises; nudge rock's
  RFI up a hair to recover if desired.
- **NEW systemic finding — everyone over-folds to 3-bets** → backlog #2 below.

NOT committed — staged on `archetype-shaping-tuning` (generator + 3 regenerated
charts + 2 probe scripts).

Goal of the workstream: make AI cash opponents **reliable, readable archetypes** —
a player should get a real read on a `nit` vs a `maniac` and feel challenged. We
do this by giving each archetype a **target range** for behavioral stats and
tuning until measured behavior lands in band.

## Shipped (merged to `main`)

- **PR #237** — Archetype Review admin tool + background-sim stat capture.
- **PR #240** — 3-bet frequency fix (pre/postflop split, looseness decouple,
  target rescale, LAG chart trim), `unknown`-labeling fix, live raise-size
  jitter, sim-toggle UX, `chart_label` in the decision snapshot.

Full diagnosis + rationale: `docs/technical/ARCHETYPE_SHAPING_FINDINGS.md`.
Sizing plan: `docs/plans/PREFLOP_SIZING_VARIETY.md`.

## How to work this workstream (read first)

**The review tool** (the measurement surface): Admin → **Archetype Review**.
- Route: `GET /api/admin/archetype-review/summary?source=live|sim&mode=cash|tournament|all`
  (`flask_app/routes/archetype_review_routes.py`).
- `source=live` = games a human played (reads `player_decision_analysis`);
  `source=sim` = AI-vs-AI background games (reads `archetype_stat_counts`).
- Targets + scoring: `poker/archetype_targets.py` (`ARCHETYPE_TARGETS`,
  `score_stat`). DB-overridable live via the `ARCHETYPE_TARGET_OVERRIDES`
  app setting (JSON `{archetype:{stat:[lo,hi]}}`) — no deploy needed to retune a band.

**The metric denominators (critical — don't confuse them):**
- `threebet` = raise/all_in at a `vs_open` node ÷ decisions facing an open
  ("3-bet when facing an open" — a reg is ~15% here, NOT ~7% of all hands).
- `fourbet` = same at `vs_3bet`. `fold_to_3bet` = fold at `vs_3bet`.
- `vpip`/`pfr` = per preflop hand-instance. `af` = postflop (bet+raise)/call. `all_in` = hands with an all-in.

**The probes** (`scripts/` is gitignored — keep copies). Both score every banded
stat (vpip/pfr/threebet/fourbet/fold_to_3bet/af/all_in) vs `ARCHETYPE_TARGETS`
(pass/warn/fail) for all 7 archetypes:
- `scripts/archetype_3bet_probe.py` — **controlled A/B**: 1 hero + 5
  `BaselineSolverBot`s. Deterministic, same field both arms — use it to confirm a
  tuning change moved the right direction. Caveat: the all-`Baseline` (tight) field
  compresses AF and **inflates fold-to-3bet** (tight openers 3-bet a strong range,
  so folding is correct) — don't read its fold_to_3bet as absolute.
- `scripts/archetype_mixedfield_probe.py` — **absolute**: seats 6 of 7 archetypes
  at one table (rotating the sit-out), so each is measured vs a realistic field.
  Use this for absolute band comparison. ~6k hands gives n≈500–1800 per node.
Run (suppress the `[EMOTIONAL]` log noise):
```
docker compose exec -T backend python < scripts/archetype_mixedfield_probe.py 2>&1 | grep -vE "EMOTIONAL|zone_effects"
```
(The 3bet probe reads N_HANDS from the file; `sed 's/^N_HANDS = 6000/N_HANDS = 2500/' …`
to shorten.) Tip: pytest summary lines get eaten by a sentry exit-hang in this
container — use `--junit-xml=/tmp/j.xml` and parse it for pass/fail counts.

**Where the knobs live:**
- Per-archetype distortion: `poker/strategy/deviation_profiles.py` (`DEVIATION_PROFILES`).
  Global `aggression_scale`/`max_per_action_shift` drive ALL streets;
  `reraise_aggression_scale`/`reraise_max_per_action_shift` apply ONLY at preflop
  facing-raise nodes (the pre/postflop split — controller swaps via
  `dataclasses.replace` at `tiered_bot_controller.py` ~833, anchored on `# Layer 2:`).
- Distortion math: `poker/strategy/personality_modifier.py` (`compute_trait_offsets`).
- Base preflop charts: `poker/strategy/data/preflop_100bb_6max*.json`, GENERATED by
  `experiments/build_archetype_charts.py` (do NOT hand-edit JSON; change the
  generator + re-run `python -m experiments.build_archetype_charts`).
  `_loosen_facing(actions, keep_fold, raise_share)` — `keep_fold` sets continue
  rate (VPIP), `raise_share` sets how much freed mass becomes re-raise vs flat-call.
- Live raise-size jitter: `flask_app/handlers/tiered_factory.py` (`LIVE_SIZING_JITTER`).

## Key lessons banked (don't re-derive)

1. **The per-action CAP (`max_per_action_shift`) is the binding distortion
   constraint, not `aggression_scale`** — it saturates, so lowering scale beneath
   it barely moves realized behavior. This is WHY global knobs couldn't separate
   preflop 3-bet from postflop AF, and why the **split** was necessary.
2. Over-3betting was **distortion-driven for lag/maniac, chart-driven for tag.**
   Base chart 3-bets (combo-weighted): standard ~16%, loose_mid (lag) ~23%,
   loose (maniac) ~30%. Distortion then ~doubled lag/maniac.
3. The **LLM/hybrid path is calm** (3-bet ~13%); the leak is the tiered/solver path.
4. Anchor-derived `archetype_name` can NOT see `weak_fish` (loose-passive →
   `calling_station`); always use `_table_archetype_key()` for identity. The
   `deviation_profile` *property* returns a `spot_tendencies` `replace()` copy, so
   an `is` check against it silently fails (the `unknown` bug — both fixed).

## Backlog (prioritized)

### 1. ~~Validate ALL 7 archetypes~~ ✅ DONE (2026-06-08b)
All 7 measured vs `ARCHETYPE_TARGETS` on both probes. VPIP/PFR/AF/4bet/all_in in
band across the board (PR #240 decouple confirmed). Passive over-3-bet FIXED (see
session update above). Residual: tag 3bet 16.1 (WARN, band top — fine, a TAG
3-bets a polarized range; see #4) and the fold-to-3bet systemic below. The
`source=sim` review-tool read is still worth doing once a stack accrues
`archetype_stat_counts` (cross-check the probe's mixed-field numbers).

### 2. ~~"Everyone over-folds to 3-bets"~~ ✅ WAS A METRIC BUG (2026-06-08c)
The apparent systemic over-fold was ~90% **measurement contamination**, not
behavior. `classify_preflop_scenario` buckets `vs_3bet` by raise-count==2 only —
with no check that the actor was the RFI raiser — so it swept in SQUEEZE defence
(cold-call an open, then face a 3-bet), which folds ~100% and dominates the
bucket for the wide-flatting archetypes (squeeze share 33–85%!). **Fix: condition
fourbet / fold_to_3bet on the actor being the RFI opener** — in the recorder
(`cash_mode/archetype_stats.py` `record_decision(is_opener=…)` + `full_sim.py`
tracks `rfi_opener_name`), the live route (`archetype_review_routes._aggregate`
reconstructs opener-ness from the rows — `preflop_node_key` is the strategy node,
can't be repurposed), and both probes. fold_to_3bet after the fix (opener-only,
6k mixed): station 82.9→**22.0**, maniac 67→**20.3**, lag 60→**44**, weak_fish
70→**21**, rock 83→**65**, nit 86→**59**, tag 77→**68**. 6 of 7 now in band /
WARN-by-a-hair. (The bands were always the opener-conditioned definition — this
just makes the measurement match.) **The contaminated 3-bet probe / review-tool
numbers in older notes are wrong for fold_to_3bet & 4-bet; re-measure.** sim
counters (`archetype_stat_counts`) are forward-only — old rows mixed squeeze in;
reset a sandbox's counters for a clean source=sim read.

### 3. 3-bet DEFENCE: ✅ tag + lag/maniac 4-bet DONE (2026-06-09)
Exposed once #2 cleaned the metric: tag/lag/maniac were over-*polarized* facing
3-bets (4-bet-or-fold, flats too little). All addressed.
- **tag — FIXED.** fold_to_3bet 68.3→**49.8** (band 40–58), 4-bet 16.3→**13.3**
  via a new **`defend_3bet` spot tendency** (`poker/strategy/spot_tendencies.py`):
  gates on `scenario=='vs_3bet'`, routes fold→call + a slice of 4-bet→call
  (de-polarize toward flatting). Added `('defend_3bet', 0.24)` to
  `DEVIATION_PROFILES['tag']`, plus a new PREFLOP call to the spot-tendency layer
  in `tiered_bot_controller._layer_preflop_spot_tendencies` (the postflop helper
  reads PostflopNode-only fields, so preflop is a separate call with `street=None`
  + the scenario). Chosen over a chart override after 2 architect designs + 2
  adversarial reviews + codex (B = lower blast radius; A's transform overshot and
  missed 2 of 4 controller-construction paths). Validation: 6k mixed-field (tag in
  band, **every other archetype byte-identical** — the no-op-preflop invariant);
  range-quality A/B (`scripts/tag_vs3bet_range.py`) shows the newly-defended hands
  are textbook (AJo/AQo/KJo/QJo/77/TT…, meanEq 0.59, 5% weak) — NOT trash, the
  4-bet range stays value-weighted; EV gate `champion_challenger.py --change
  tag_defend --archetype TAG`. Tests: `test_spot_tendencies.py` (handler +
  **the no-op-preflop invariant lock** across all postflop tendencies).
- **lag/maniac 4-bet — FIXED via the reraise split.** The old splits were tuned
  against the *contaminated* metric so they were too loose. The per-action CAP is
  the binding lever (not the scale): lag `reraise_max_per_action_shift` 0.20→0.10
  (+scale 0.6→0.45), maniac 0.18→0.08. 6k mixed: **maniac 4-bet 48.5→39.0 (now in
  band)**, lag 4-bet 24.6→21.2 (minor WARN), and a bonus — lag 3-bet 26.7→25.3 and
  maniac 3-bet 47.2→37.3 both pulled into band. Isolated to the two profiles (the
  loose chart is SHARED with spewy_fish/maniac_overbluff — the cap is the
  archetype-only lever; don't trim the chart). Tuned via `scripts/reraise_tune.py`
  (multi-arm sweep). Residual: lag 4-bet floors at ~21 on the loose_mid chart
  (~15% vs_3bet mass) — a loose_mid-only trim (backlog #5) would close the last
  ~1pt; not worth a chart change for a minor WARN.

### 4. Knob 3 — `_apply_hyper_passive` fires in `vs_open` defend spots
`poker/strategy/exploitation.py:1077` adds `+0.3×scale` to raise unconditionally
when a station opponent is detected, with no guard that the station is the
*opener* — so a bot 3-bets MORE vs a passive opener (gate `:1424`,
`is_preflop_defend_spot`). Add a guard so the value-extraction rule doesn't push
3-bets in a 3-bet-defend spot. Validate with the probe + the exploitation tests.

### 5. tag's mild over-3bet (mixed-field 16.1, band 10–16, WARN)
Comes from the **standard chart** (~14.5% combo-weighted), shared as the base for
the tight tier too. Now only a boundary WARN (was a FAIL earlier). Either trim it
(lower `raise_share` on the standard chart's vs_open / base authoring — moves
everyone, careful) or accept the WARN and widen tag's band to ~11–18 (a TAG
legitimately 3-bets a polarized range facing opens). Recommend the latter.
**Research backs the latter** ([[../vision/poker_aggression_benchmarks_text_markdown]]):
a live TAG 3-betting ~16% facing an open is faithful (live reg ~13%, recreational
higher) — don't trim the chart toward online numbers; widen the band.

### 6. Review-tool Phase 3 — c-bet / fold-to-cbet columns
Currently empty. Architect's plan: source them from `opponent_observation_lifetime`
(it has `cbet_attempt_count`, `fold_to_cbet_count`, etc.) via
`reconstruct_tendencies_from_lifetime`. Add to the route + grid.

### 7. Preflop sizing VARIETY (the proper fix; jitter is the band-aid)
Execute `docs/plans/PREFLOP_SIZING_VARIETY.md`: P1 emit multiple raise-size tokens
in the preflop charts, P2 engage the `SIZING_PERSONALITY` size gradient on them
(maniac overbets, nit min-3bets), P3 add a "3-bet size" read to the review tool.
**Per-archetype sizing character — P1/P2 are the substrate work now that raise
amounts round cleanly (#246).** Research reinforces this as a *believability/tell*
lever, not just variety: a maniac who overbets and a nit who min-3-bets *telegraph*
their archetype through size — exactly the legible-style signal the aggression brief
wants (see #12).

### 8. Config VERSIONING for the review tool (compare chart/knob versions)
**Why:** the whole workstream is iterative tuning, but the review tool currently
aggregates ALL decisions for an archetype regardless of which chart/knob version
produced them. After a deploy, new + old decisions mix → before/after comparison
is muddied. Worse, the **sim counters (`archetype_stat_counts`) aggregate in
place**, so they can't be compared across versions at all without a version axis.
**Design (minimum viable):**
- Compute a `strategy_version` at startup = short hash of the active strategy
  config: the chart file contents (`poker/strategy/data/preflop_*.json`),
  `DEVIATION_PROFILES`, `ARCHETYPE_TARGETS`, `LIVE_SIZING_JITTER`, and any DB
  knob overrides — OR simplest: the deployed **git SHA** (captures charts +
  profiles + algorithm, since all are in code) combined with a hash of the
  DB-overridable bits.
- Stamp it on each decision: add `strategy_version` to `player_decision_analysis`
  (column or in the snapshot JSON — `chart_label` already records *which* chart
  but not its *content version*), and add `strategy_version` to the
  `archetype_stat_counts` PK so sim counters bucket per version.
- Small registry `strategy_versions(version, label, config_json, first_seen)` so
  versions are nameable ("v3: split + lag chart trim") and inspectable.
- Review tool: a version selector + an A/B diff view (vN vs vN-1 per archetype/stat).
**Near-term stopgap (no schema):** `chart_label` + decision timestamps already
let you eyeball before/after a known deploy date — coarse but zero-build.

### 9. Maniac 3-bet: cap the baseline, make extremity conditional
Source: [[../vision/poker_aggression_benchmarks_text_markdown]]. Realized maniac
3-bet (facing-open ~37) is above the realistic *sustained* ceiling (live maniac
~15–25% even by expert estimate). **Don't just lower the mean** — lower the
*baseline* to ~20–25 and let conditioning/tilt push it transiently into the 30s
(#12), so it reads as a *state* not a flat constant. Touches
`ARCHETYPE_TARGETS['maniac']['threebet']` + the maniac reraise split in
`deviation_profiles.py`. Re-validate with `scripts/archetype_mixedfield_probe.py`.
Note: tag/lag are live-faithful — this correction is **maniac-specific**.

### 10. Rock band inversion
`ARCHETYPE_TARGETS['rock']` (VPIP 15–22 / PFR 11–17) is looser + more aggressive
than `nit` (10–16 / 8–13), but `deviation_profiles` makes rock *tighter* (looseness
0.7 < nit 1.2, both on `tight_rfi`). The targets predict the opposite VPIP ordering
from what the strategy produces → mis-flag. Rebuild rock band ≤ nit (VPIP ~10–14,
lower PFR, bigger VPIP−PFR gap = the classic tight-passive rock), OR reconcile the
strategy if "rock = tighter-but-harder-than-nit" is the intended definition. Confirm
the produced ordering with the mixed-field probe before re-banding.

### 11. Methodology: AFq + WTSD/W$SD + per-street AF
Add to `archetype_review_routes._aggregate` + `cash_mode/archetype_stats` +
`ARCHETYPE_TARGETS`:
- **AFq** = (bet+raise)/(bet+raise+call+fold) — fixes the AF discriminator (AF
  can't separate a fit-or-fold nit from a maniac; our nit AF band may be unhittable).
- **WTSD** = showdowns / saw-flop, **W$SD** = won-at-showdown / showdowns — the
  calling-station's most legible signature (high WTSD + low W$SD).
- **per-street AF** — aggregate postflop AF hides flop-maniac/turn-passive texture.
This also extends review-tool **#6** (the showdown family pairs naturally with the
c-bet/fold-to-cbet columns sourced from `opponent_observation_lifetime`).

### 12. Perceptibility & conditioning (the believability frontier — highest leverage)
The unifier for Findings 1–3 + the Stacked lesson: adaptation that isn't *felt* is
worthless (Stacked was "readable in 40 hands" despite world-class AI). Two halves:
- **Surface the read** (cheap, do first): voice the exploitation-layer /
  opponent-model read as `dramatic_sequence` callbacks (Nemesis/F.E.A.R.), and turn
  the Finding-3 sample-gate ramp into an audible "figuring you out" arc (Alien).
  Subsumes the parked nudge-display fix. See `docs/plans/TELLS_SYSTEM.md`,
  `docs/plans/PREDATOR_LOADOUTS.md`.
- **Condition the aggression**: modulate 3-bet/aggression by *opponent memory >
  position > tilt-type > image > stack* (priority per the aggression brief) instead
  of a flat per-archetype constant. Backlog **#4** (`_apply_hyper_passive` opener
  guard) is a small correctness instance of opponent-conditioning. Tilt uses
  Tendler's 7 types as triggers into the existing emotion + relationship layers; the
  avatar must telegraph the spike so it's earned and readable. Playtest gates: ID'd
  in <40 hands AND never surprises = too monotonic; no articulable exploit after
  ~200 hands = too random.

## Independent / parked (diagnosed, not in scope of the above)

- **Committed-fold exploit** — `poker/strategy/math_floor.py:106` no-ops when
  `call` is illegal but `all_in` is legal (facing a shove), so a pot-committed
  bot folds. Staged fix: fire toward `jam` when the call-off price is good.
  Engine condition: `poker_game.py:267-291`. Low-frequency; real.
- **Aggression-nudge display** — the exploitation "sensing aggression" nudge
  fires at ~0.0002 (it's a pre-softmax LOGIT offset, and sample-gated by design),
  so it reads as ~0% in-game. Fix = surface the post-softmax probability delta +
  a display threshold; NOT a math change. (`exploitation.py` hyper_aggressive,
  effect_size at ~:1720.)
- **Taxonomy** — `spewy_fish` / `balanced_defender` exist but are measurement-only
  (`deviation_profiles.py`); decide whether to deploy them or add a mid-`fish`
  tier between calling_station and weak_fish.

## Caveats

- All fixes are **forward-only** — the `unknown`-labeling, sizing jitter, and
  3-bet tuning apply to NEW decisions; historical `player_decision_analysis` rows
  and `archetype_stat_counts` keep their old values. Re-measure on fresh play.
- `source=sim` only has data once a stack runs the background sim long enough to
  flush (every 100 hands).
