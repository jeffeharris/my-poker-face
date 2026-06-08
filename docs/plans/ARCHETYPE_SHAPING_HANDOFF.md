---
purpose: Handover for the archetype-shaping workstream — what's shipped, how to measure/tune, and the prioritized backlog with file refs so a fresh context can execute
type: guide
created: 2026-06-08
last_updated: 2026-06-08
---

# Archetype Shaping — Handover

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

### 3. tag / lag / maniac over-aggressive 3-bet DEFENCE  ← do first (PART 2)
Exposed once #2 cleaned the metric. Opener-conditioned (6k mixed): **tag** fold
68.3 (band 40–58 FAIL) + 4-bet 16.3 (5–13 WARN) — over-*polarized* (4-bet-or-fold,
flats too little); **maniac** 4-bet 48.5 (24–40 FAIL); **lag** 4-bet 24.6 (10–20
WARN). lag/maniac reraise splits (`reraise_aggression_scale`) were tuned against
the *contaminated* metric so the true 4-bet is higher than thought; tag has no
split at all. Lever: the reraise split in `deviation_profiles.py` — add one to
tag (+ nudge it toward flatting more vs 3-bets), tighten lag/maniac's a touch.
Re-tune against the now-correct probe; strength-check via `sng_runner.py` since
widening flat-defence changes EV. Open question: are tag's bands (fold 40–58)
modeling a flat-heavy TAG our polarized one legitimately isn't? Sanity-check first.

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

### 6. Review-tool Phase 3 — c-bet / fold-to-cbet columns
Currently empty. Architect's plan: source them from `opponent_observation_lifetime`
(it has `cbet_attempt_count`, `fold_to_cbet_count`, etc.) via
`reconstruct_tendencies_from_lifetime`. Add to the route + grid.

### 7. Preflop sizing VARIETY (the proper fix; jitter is the band-aid)
Execute `docs/plans/PREFLOP_SIZING_VARIETY.md`: P1 emit multiple raise-size tokens
in the preflop charts, P2 engage the `SIZING_PERSONALITY` size gradient on them
(maniac overbets, nit min-3bets), P3 add a "3-bet size" read to the review tool.

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
