---
purpose: Prioritized roadmap for the next phase — competitive bot quality, cash mode with relationships, and a conditional decision on solver build
type: vision
created: 2026-05-17
last_updated: 2026-05-18
revision: 3 — folds in completed personality_id foundation, incorporates consultancy review (Gate 1 framing, equity-bias correction, cash mode estimate range, merge-point hardening, backout plan exception, LAG split possibility, cloud cost line, play-test rigor, "done" definition, calendar column relabel)
---

# Next Phase Vision

## Vision Statement

The next phase of My Poker Face turns a technically solid poker engine into a game worth a second session: bot opponents that feel genuinely competitive and unpredictable, a persistent character layer where AIs remember that you bluffed them two weeks ago and hold the grudge across sessions, and a cash mode that gives the player a real stake in the world over time. Two tracks launch immediately in parallel — bot decision quality and cash mode foundations — driven by different bottlenecks but converging on the same goal: every session should feel different, the bots should be able to surprise you, and the game should have something to lose. A data-driven gate then determines whether a solver build is needed to close any remaining competitive gap.

---

## Current State

This section establishes where things stand so the roadmap reads as a "from here" plan.

### What is solid

The pre-main triage batch shipped 50+ correctness and UX fixes. The TieredBot decision pipeline has board-aware hand classification, price-sensitive defense floors, unified opponent archetype detection, bluff reduction vs stations, bet-bucket awareness, and a diagnostics harness (`experiments/casebot_breakdown.py`). Full specification in `docs/plans/TIEREDBOT_DECISION_QUALITY.md`.

Three exploitation patches shipped in the pre-roadmap session:

1. Stake-weighted aggregation in `poker/strategy/exploitation.py` — `aggregate_from_spots` now uses `committed_this_hand` as weight rather than equal-weight average (commit `11dd7d7a`).
2. All-in station gate — `non_all_in_station_continuing` kwarg on `compute_exploitation_offsets` / `compute_exploitation_offsets_with_traces`. When the station's aggression is entirely all-in, hyper_passive fold-reduction no-ops with reason_code `station_all_in_only` (commit `2c09c686`).
3. Regression tests in `tests/test_strategy/test_opponent_spots.py` (single-station-dominates) and `tests/test_strategy/test_exploitation.py` (suppression + default-behavior). 1007 strategy tests and 110 memory tests passing.

`docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` is a complete, implementation-ready design for the relationship layer and cash mode v1. No design work remains for those features.

### What has shipped on `phase-1` since this doc was written (revision 3)

**Track B step 1 (Personality ID migration) is fully complete.** 10 commits land the end-to-end surface:

| Commit | Layer |
|---|---|
| `92293f5b` | JSON seed source: stable `id` per personality (slug rule, collision suffix) |
| `fcbfa6f5` | Force-add `scripts/backfill_personality_ids.py` past gitignore |
| `d738ddb2` | Schema v85 — `personality_id TEXT UNIQUE` on `personalities` table + backfill |
| `607da181` | `PersonalityRepository` carries `personality_id` (save/load/by-id/resolve, JSON-seed alignment) |
| `50734a45` | `PersonalityGenerator` factory + `POST /api/personality` route surface the id |
| `2ebb7a19` | Schema v86 — `observer_id` + `opponent_id` columns on `opponent_models` |
| `5e74854b` | `OpponentModel` + `OpponentModelManager` carry `personality_id` surface |
| `ddbc90c6` | `save_opponent_models` / `load_opponent_models` round-trip personality_ids |
| `e17b3b21` | Game startup resolves and registers personality_ids |
| `140c49ba` | Bugfix: don't create v86 indexes in `_init_db` (runs before migrations) |

**82 tests green across the migration surface.** Backend boots cleanly with the new schema. Personality identity is now stable end-to-end: every personality has a slug-based id at creation (factory + create route + JSON seed paths all populate it); every opponent model save/load round-trips both display name and stable id; game startup resolves name→id and registers both surfaces on the manager. The remaining theoretical item — one-time backfill pass for existing saved games — is a no-op given the zero-production-users posture; the v86 migration's name-lookup backfill correctly populates ids for any rows that do exist.

Track B step 1 is **unblocked for step 2 (Relationship Phases 1–3)**.

### Measured leaks (pre-patch HU baselines vs CaseBot, 2000 hands per matchup)

| Archetype | bb/100 | 95% CI |
|---|---|---|
| Rock | −58.8 | [−98.7, −19.0] |
| TAG | −84.3 | [−130.1, −38.5] |
| LAG | −129.0 | [−186.1, −72.0] |

These are pre-patch numbers. The all-in station gate targeted the hyper_passive fold-reduction half that was the primary TAG leak. Disabling that half entirely (the nuclear version of what the gate does surgically) moved TAG from −84.3 to −19.0 bb/100 (CI includes zero). Post-patch bb/100 has not been measured yet — that is the first task of the bot quality track.

### Diagnosed leak structure

The primary leak is not paying off bluffs at showdown. TAG has a 73%+ showdown win rate when it reaches showdown — net positive. The problem is folding strong made hands to value-polarized aggression. Game `h66vGnzs4ccmLq0UcDaSMA` showed:

- CaseBot (Rock) VPIP_per_voluntary_opportunity = 0.98 preflop, AF = 0.53 — classic station profile
- CaseBot median equity when **raising** postflop: 0.82 river, 0.66 flop — polarized to value
- CaseBot median equity when **calling** postflop: 0.30–0.44 — marginal
- Big EV losses came from calling raises with equity in the 0.10–0.20 range, and folding strong hands to jams when the jam was value-weighted

TieredBot treats all "hyper_passive" stations identically regardless of whether their aggression is noisy-caller style or value-polarized. This is the core gap polarization detection addresses.

### Bot exploitability surface

Independent of which archetype runs, the bot has multiple exploitable patterns:

1. Binary 100%/0% action encoding in `preflop_100bb_hu.json` makes hand ranges deducible at showdown
2. Hard 15-hand cold-start gate — predictable "no adaptation" window for first 15 hands
3. Solver-table-driven sizings cluster at the same values — sizing tells
4. Opponent model is position-blind
5. Per-session archetype is static — consistent play exposes patterns

None are blocking issues individually, but they compound. Competitive feel improvements (Bucket 2) address most without requiring solver-quality strategy.

### Strategy table provenance

All three tables are expert-heuristic quality, not solver quality:

| Table | Source | Known gaps |
|---|---|---|
| `preflop_100bb_hu.json` | Acevedo, Chen/Ankenman, WizardOfOdds — documented in `hu_preflop_chart_README.md` | Binary frequencies; defers mixed-strategy hands |
| `preflop_100bb_6max.json` | Hand-authored, AI-assisted, validation-tuned (2026-02-16) | No provenance README |
| `postflop_strategies.json` | "Hand-crafted strategies / heuristic tables" (2026-02-17), 2160 entries | No provenance README |

This is the honest baseline for evaluating whether a solver changes outcomes meaningfully.

### Open from TRIAGE

T3-70..T3-72 and T3-74 are explicitly deferred post-release. T1-26 (guest identity forgeable) and T1-27 (anonymous chat-session leak) remain open from the pre-main batch — **scoped into Phase 1 Track A as small fixes** (verified ~half-day each). The project has zero production users today, so the security items are not urgent against existing exposure, but they are cheap to address now and remove future blockers.

### Production reality

The game has **zero production users today**. Cash mode v1 will sit alongside the SNG mode in production from day one, with no feature-flag plumbing, no gradual rollout, no legacy migration concerns. This collapses several categories of Phase 1 work the original framing implied: feature-flag infrastructure, A/B harness, deployment-staging discipline. Each ships when ready.

### Conflicts found between existing docs

Two inconsistencies worth naming rather than silently picking one:

**`GAME_VISION.md` phase numbering is superseded** by this document for roadmap purposes. That document defines a Phase 1–5 structure from an earlier planning era, predating both the TieredBot pipeline work and the cash mode design. Its three guiding principles — **drama over mathematics**, **emergent personalities**, **living world** — are load-bearing in this roadmap's specific buckets:

- *Drama over mathematics* shapes Bucket 2 (Competitive Feel): creative play injection and visible adaptation are deliberately not pure-EV optimizations.
- *Emergent personalities* shapes Bucket 4 (Relationship Layer): per-pair affinity that mutates from real play is what makes characters persistent rather than scripted.
- *Living world* shapes Bucket 5 (Cash Mode): persistent AI bankrolls with regen, sit/leave/bust dynamics, and (v2+) AI-vs-AI background simulation make the table feel like one corner of a continuous ecosystem.

**Quick win estimates in `QUICK_WINS.md` are superseded for the relationship/rivalry system.** That document estimated the rivalry tracker at 4–5 hours as a "simple counter system." The full design is now in `CASH_MODE_AND_RELATIONSHIPS.md` — a proper persistence layer, cross-session affinity axes, and TieredBot modifier seam. The counter-system estimate is no longer the right reference.

---

## The Seven Buckets

These are the natural workstream groupings for next phase, ordered roughly by execution priority. "Small" = roughly a day or two of Claude coding. "Medium" = roughly a week. "Large" = two or more weeks of coding, or weeks of wall-clock compute time.

---

### Bucket 1: Bot Decision Quality — Polarization Detection

**What it is**

Equity tracking on the opponent model to distinguish value-polarized opponents (CaseBot raises are value; calls are marginal) from noisy-caller stations (frequent caller whose aggression is random). The hyper_passive fold-reduction half is correct against noisy callers and disastrous against polarized value-callers. The all-in station gate handles the extreme case. Polarization detection generalizes this across the full opponent model.

**The spec** (first capture in any doc — not yet in `TIEREDBOT_DECISION_QUALITY.md` or a standalone doc):

Six new fields on `OpponentTendencies`:
- `equity_when_betting_postflop`, `equity_when_raising_postflop`, `equity_when_calling_postflop` — mean observed equity per action type at showdown
- `equity_betting_sum`, `equity_raising_sum`, `equity_calling_sum` — running sums for stake-weighted averaging

Updated at showdown when hole cards are revealed. Aggregated stake-weighted on `AggregatedOpponentStats`.

Derived signal:

```
aggression_polarization = equity_when_raising_postflop - equity_when_calling_postflop
```

A pure-value station has high `aggression_polarization` (raises 0.82 equity, calls 0.30). A noisy caller has low polarization — raises and calls are similarly marginal.

**Sample bias to correct in Phase A** (consultancy critique):
`equity_when_calling_postflop` only updates at showdown — but most calls don't reach showdown (people fold marginal hands by river). The showdown sample skews toward strong calls and would *under-estimate* polarization (calling appears stronger than it is). Phase A must split this signal into two:

- `equity_when_calling_postflop` (showdown sample) — current spec
- `equity_when_calling_postflop_at_decision` (decision-time equity, available from `player_decision_analysis.equity` for every call regardless of whether the hand reaches showdown)

Calibrate the polarization threshold against both samples and pick the one that holds up under the bias.

**Phased rollout**

Phase A (instrument + calibrate, no rule changes):
- Add the six showdown-equity fields + decision-time call equity field, populate at showdown / at-decision respectively
- Emit to diagnostics
- Run 2000-hand sims per archetype (Rock, TAG, LAG, plus CaseBot as opponent)
- **Per consultancy critique #7**: also categorize the leak structure per archetype. If LAG's −129 bb/100 leak does NOT have the same structure as TAG's (e.g., LAG is over-defending too wide rather than calling polarized bets), Phase B may need to split into two rule changes rather than one. The Phase A report should explicitly answer "does one rule change help all archetypes?" before Phase B coding starts.
- Write a standalone polarization spec doc, including the calibrated threshold and any per-archetype split. User reviews and approves before Phase B coding.

Phase B (gate hyper_passive): Condition hyper_passive fold-reduction on `aggression_polarization < threshold` (threshold calibrated in Phase A). High-polarization opponents no longer get fold-reduction applied — only the raise-push half fires. Re-measure bb/100. This measurement feeds Gate 1.

Phase C (new rule — `polarized_value_caller`): When polarization exceeds a high threshold, add an affirmative rule that increases fold probability against the opponent's aggression rather than just suppressing fold-reduction. The flip from "don't discourage folds" to "actively encourage folds vs this player's bets." New rule in `_EXPLOITATION_RULE_ORDER`, gated on archetype == `pure_station` AND `aggression_polarization > high_threshold`.

Phase D (bluffer detection): Symmetric to Phase C. Low `equity_when_raising_postflop` on a frequent raiser → their aggression deserves reduced respect. Enables bluff-catch expansion against confirmed bluffers.

**Remaining from `TIEREDBOT_DECISION_QUALITY.md`**

~~§5.5 per-rule offset budgets~~ — already shipped. `MAX_L1_SHIFT_BY_RULE` constants live at module scope in `poker/strategy/exploitation.py:164`, with post-rule scaling in `compute_exploitation_offsets_with_traces` and dedicated test coverage in `tests/test_strategy/test_section_5_5_offset_budgets.py` (8 passing). The safety net is in place ahead of Phase B.

§1.5b extended archetype taxonomy (`maniac`, `lag`, `tag`, `nit`, `rock`, `balanced`) remains explicitly deferred. Do not ship these labels speculatively — they accumulate calibration debt as dead code until a consuming rule needs them.

**Dependencies**: Showdown hole-card reveal path must expose all-player cards (verify in the hand engine before Phase A coding).

**Effort**: Phase A small; Phase B small-medium; Phase C medium; Phase D medium.

---

### Bucket 2: Competitive Feel

**What it is**

The bot's play is predictable enough that a player who has learned its patterns can consistently beat it — not through poker reads but through pattern recognition. These are presentation and randomization gaps, not fundamental strategy bugs. Most fixes are zero EV cost.

**The improvements**

1. **Sub-action sizing randomization** — sample bet size uniformly from a configurable band (e.g., [55%, 78%] pot) instead of a fixed table-derived value. Zero EV cost. Eliminates sizing tells. Small.

2. **Action ties** — when two actions are within a configurable EV threshold (starting value: 3%), pick uniformly rather than deterministically selecting the highest-ranked. Prevents tells like "always raises pocket aces preflop." Uses local `random.Random()`. Small.

3. **Creative play injection** — low-frequency lines not captured in strategy tables, modulated by personality:
   - Slow-play monsters: check back on flop with a set or better, 10–15% of eligible spots
   - Triple-barrel pure bluff: 5–10% of continuation opportunities where hand and board support it
   - 3-bet junk from the blinds: 3–5% when facing a late-position open

   Personality-weighted (aggressive archetypes execute them more). Medium per line type.

4. **Cold-start replacement** — replace the hard 15-hand MIN_HANDS_DEFAULT gate with a Bayesian prior that starts at population-average behavior and gains confidence smoothly as hands accumulate. Eliminates the predictable 15-hand no-adaptation window. Interacts with Polarization Phase A — the equity-tracking fields benefit from the same smooth ramp. Small-medium.

5. **Surface `narrative_observations`** — the exploitation layer produces observations about perceived opponent tendencies but they are not shown in-game. Surface them in chat (throttled, significant observations only). Makes the bot feel like it is actively adapting because it is. Small.

6. **Per-session archetype drift** — perturb personality anchors at session start so the bot does not play identically across sessions with the same archetype. Configurable magnitude. Small.

7. **Stakes-aware play** — adjust tightness as a function of stack position relative to starting stack (tighten when ahead, loosen when behind). Addresses the "bot won't go broke defending" dynamic. Requires stack-ratio input to the bounded options generator. Medium.

**Dependencies**: None on other buckets. All items are independent presentation-layer changes.

**Effort**: Items 1, 2, 5, 6: small (can ship any time). Items 3, 4, 7: medium each.

---

### Bucket 3: Stack-Depth Coverage

**What it is**

Existing strategy tables cover 100bb. WTA SNG tournament play runs to 15–30bb late in games — the user noted the bot is too weak at these depths. Cash mode v1 uses fixed 100bb, so this is primarily a SNG-mode problem.

**Two components**

Push/fold tables for <15bb: Published Nash push/fold solutions (WizardOfOdds, HRC output, Sklansky-Chubukov) are directly applicable to WTA SNG because chip EV = $ EV throughout (no ICM adjustments needed). Engineering: add a stack-depth gate — when effective stack < ~15bb, bypass the postflop strategy tables and route to push/fold lookup keyed on (hero stack in bb, position, hole cards, active opponent count). Small engineering effort once a source is chosen.

**Vet license terms before embedding.** Sklansky-Chubukov originates from a 2008 book; raw computational outputs typically aren't copyrightable but presentation may be. WizardOfOdds publishes free reference tables but redistribution terms should be confirmed. HoldemResources sells access and may have known terms. The "source selection and vetting" task is a real upstream gate, not a side note — write it down as a deliverable before any code references the chosen table.

15–30bb interpolation heuristic: Between 15bb and 100bb, the tables are calibrated for 100bb. At 20bb, correct strategy tightens significantly (SPR is low, implied odds evaporate). A heuristic approach scales raise sizes and opening ranges as a function of effective stack depth. Medium. A full multi-depth table set (25bb/40bb/100bb solved separately) requires either the solver path (Bucket 6) or significant manual authoring — defer unless the heuristic proves insufficient.

**Dependencies**: None upstream. Independent items.

**Effort**: Push/fold ingest: small. Interpolation heuristic: medium.

---

### Bucket 4: Relationship Layer

**What it is**

Persistent per-(observer, opponent) affinity state — three axes (heat, respect, likability) — that accumulates cross-session, feeds TieredBot as modifier signals, and provides the persistent-character foundation for cash mode. Fully specified in `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 1.

**Why it ships before cash mode**

The relationship layer is independently useful in existing SNG and tournament modes: AIs with high heat toward a player bluff them more aggressively; AIs with earned respect are harder to bluff off. It is also a prerequisite for cash mode's full character (rivalry-seek seating, soft-play on friends both depend on live relationship state).

**Baseline values for personality-pair relationships**

The relationship layer needs initial heat/respect/likability values for each personality-to-personality pair. Rather than hand-authoring a NxN matrix or computing baselines from a heuristic, **a one-time LLM seed prompt generates the full pairwise matrix and writes it to the DB at first migration**. The prompt takes the personality roster and asks for coherent baseline affinity values per ordered pair, producing a deterministic seed that the relationship layer then mutates over real play. Re-runnable if the personality roster changes (rare). This is a one-time operation, not architecture — implementation is a single Python script in `scripts/` that calls the existing `LLMClient` with `CallType.PERSONALITY_GENERATION` and writes results via the relationship repository.

**Implementation** (from `CASH_MODE_AND_RELATIONSHIPS.md`):

Relationship Phase 1 (7 independent commits, none touching the controller decision path):
- `RelationshipEvent` enum + actor's-POV and mirror dispatch tables (with `UNKNOWN` sentinel for legacy string quarantine)
- `MemorableHand.memory_type` renamed to `event: RelationshipEvent` — DB column stays `memory_type`, holds enum `.value` strings
- Personality ID migration: backfill `personality_id` to `personalities.json`; add column to `opponent_models`; name→id backfill for active games
- `RelationshipState` dataclass + `project_heat` (plateau-then-exponential decay, pure projection on read) — in-memory only at this step
- New `relationship_states` and `cash_pair_stats` tables + repository methods (projection on read by default; admin raw-read variant explicitly named)
- `OpponentModelManager.record_event()` — single entry point for all axis mutations, project-first-then-apply ordering, bilateral updates
- `get_relationship_modifier()` reader — strictly pairwise, pure projection, not yet wired into the controller

Relationship Phase 2 (controller integration):
- Multiway target selection at the `_apply_exploitation` call site, reusing existing `_select_exploitation_stats_from_spots` aggressor logic; heat-max fallback for no-aggressor situations
- `_apply_exploitation` calls `get_relationship_modifier()` once with the selected target; scales existing offsets; composition order: pattern detection → modifier scaling → existing clamp/gating; trace gains `relationship_modifier` field

Relationship Phase 3 (live population from hands):
- `HandOutcomeDetector` maps existing pressure/equity signals to `RelationshipEvent`s via the adapter table in the design doc
- Multiway chip-flow allocation determines (actor, target) pairs for `BIG_WIN` / `BIG_LOSS`; same allocation feeds `cumulative_pnl` in `cash_pair_stats`
- Dedup keyed by `(hand_id, actor_id, target_id, event)`

After Phase 3, the relationship layer is fully live from hand outcomes. Chat inputs (Relationship Phase 5 in the design doc) are additive and Phase 2 work on this roadmap.

**Merge-risk coordination point** (revised per consultancy critique #3): Relationship Phase 2 and Polarization Phase B both modify `_apply_exploitation` in `poker/tiered_bot_controller.py` and the offset logic in `poker/strategy/exploitation.py`. **Track B Relationship Phase 2 hard-blocks Track A Polarization Phase B coding.** Track A items 1–5 and 7–11 proceed in parallel during the block; only step 6 waits. Once Phase 2 lands, Phase B rebases on top and the intended application order is documented in `_apply_exploitation`'s docstring: pattern detection → relationship modifier scaling → polarization gating → existing clamp/gating.

**Backout plan** (revised per consultancy critique #5): the modifier seam is the load-bearing change to `_apply_exploitation` and a regression here is slow to debug under sim runtime pressure. **Exception to the no-feature-flag rule for this one seam**: ship with an `apply_relationship_modifier: bool = True` controller-level flag so the modifier can be toggled off at runtime if a regression surfaces. Sim runs A/B with flag on/off, and any production-feel issue is one boolean away from quarantine. This is the only feature flag justified in Phase 1 given zero-production-users posture.

**Dependencies**: Personality ID migration is a prerequisite for all persistence work. Run it first. Relationship Phases 1→2→3 are sequential.

**Effort**: Medium (~1 week coding with tests). Spec is complete; implementation is mechanical.

---

### Bucket 5: Cash Mode v1 ✅ SHIPPED

**What it is**

A new game mode alongside the existing SNG flow. Single-table cash game with persistent bankrolls, sit/leave/top-up between hands, AI bankrolls that regen over real time (pure projection on read), and bust handling with mid-hand quit and disconnect grace window. Fully specified in `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2.

**Status: shipped on `phase-1` branch (commits `613c0e9b` → `bcfe4a69`).** Eleven commits land schema v88 + cash_mode/ package + Flask routes + React UI + sanity script. Per-personality bankroll knobs tuned for all 53 seeded personalities. 116 tests passing across the cash-mode surface. See `docs/plans/CASH_MODE_V1_HANDOFF.md` (post-mortem updated) and `docs/plans/CASH_MODE_V1_WIRING_PLAN.md` (codex-vetted design).

**v1 scope**

- Single table, no lobby, no concurrent tables
- Persistent player bankroll (fresh-grant on full bust, no cooldown)
- Per-personality AI bankrolls with `project_bankroll` regen — no background timers, pure projection on read
- Stakes ladder: $2/$10/$50/$200/$1000 big blind using "X table" friendly notation
- Sit/leave/top-up between hands only (`CashTable.hand_in_progress` blocks changes during a hand)
- Bust handling: AI hard bust with regen cooldown; player fresh-grant on full bust
- Mid-hand quit: forfeit entire table stack to pot, bankroll untouched
- Disconnect: 60-second reconnect grace window; auto-check/auto-fold during window; expiry = mid-hand quit semantics
- `cumulative_pnl` and `cash_pair_stats` updates at hand settlement, chip-flow allocated per-pot per the design doc rule
- Bankroll knobs (`bankroll_cap`, `bankroll_rate`, `buy_in_multiplier`) loaded from DB-backed `PersonalityRecord` at runtime

**Architectural invariants** (from the design doc — these make v1 a non-dead-end foundation):
- Tables are first-class objects; v1 has one, v2 has many
- Hand orchestration decoupled from player presence; v3 will run hands without humans
- AI bankrolls in their own state object (`AIBankrollState` keyed by `personality_id`), not buried in game state
- All time-based effects are pure projection on read
- Cash-session state separate from relationship state

**v1 explicitly does not ship**: multi-table lobby, AI table selection priorities (rivalry-seek seating, upward drift, comfort zone), stop-loss/stop-win per-personality knobs, chat inputs to the relationship system.

**Known v1 UX limitation** (per consultancy critique #8): cash mode v1 ships with relationship state that only moves on **hand outcomes**, not chat. Chat-driven affinity (Relationship Phase 5 in the design doc) is Phase 2 on this roadmap. The player can play their way into rivalries but can't *signal* anything to characters — "you got lucky" / "good fold" type lines don't move the needle. Rivalries will feel one-sided in playtest until chat lands. Plan for this in the playtest framing, not as a discovery.

**Dependencies**: Relationship Phases 1–3 must complete first. Personality ID migration is the shared prerequisite. The `cash_mode/` package is a new directory sharing the hand engine without requiring engine modifications.

**Effort** (revised per consultancy critique #2): Large, **3–4 weeks coding, 5 if migration surprises surface**. The original "2–3 weeks" estimate was optimistic. Honest scope: new package; new persistence tables; new orchestration layer; bankroll accounting (8 documented edge cases in the design doc); sit/leave/top-up with exact accounting order; side-pot accounting tests; mid-hand quit semantics; 60s disconnect grace window; `cumulative_pnl` chip-flow allocation; bankroll-knob loading from `PersonalityRecord`. Plus the personality_id migration's collision blast on every game-load path (Track B step 1 must be fully landed first).

**Actual: shipped in ~11 commits across one session.** Two design pivots during implementation: (1) bankroll knobs moved from columns to a `config_json` sub-dict (matches existing pattern), (2) AI bankroll shim → dedicated `sit_down_ai` sibling function (codex flagged that the synthetic-wrapper approach buried `last_regen_tick` invariants). The codex-vetted wiring plan (`CASH_MODE_V1_WIRING_PLAN.md`) caught 10 concerns before code landed — biggest reversals were settlement-delta-arithmetic (replaced with direct `Player.stack` sync) and double-settlement-guard (state machine must not advance past `EVALUATING_HAND`).

---

### Bucket 6: Solver Build (Conditional)

**What it is**

Building a CFR solver to generate game-theory-optimal strategy tables, replacing the hand-authored heuristics. This is not a committed project. It is a major option hinging on whether Buckets 1–3 close the competitive gap.

**Engineering vs compute breakdown**

| Component | Engineering | Compute |
|---|---|---|
| Validation pipeline (Kuhn → Leduc → HU LHE vs Cepheus) | ~1 week | Hours |
| Full HU No-Limit solve | Included above | ~12 hours |
| 3-way preflop solve | ~2 weeks | 1–2 days |
| 6-max preflop (covers HU/3-way as subtrees) | ~2–3 weeks | 2–5 days |
| Full WTA SNG (16–20 solves, 4 stack depths) | ~2–3 weeks total | 15–30 days serial; parallelizable |
| 6-way postflop | Skippable — cost prohibitive | Weeks per board texture |

WTA SNG format simplifies significantly: chip EV = $ EV throughout, removing 1–2 weeks of ICM-aware CFR engineering. Push/fold tables for <15bb come from published sources (Bucket 3), not solver runs.

**Why the validation pipeline is the correct first step**: Phase 1 of the solver is Kuhn Poker CFR with equilibrium verification → Leduc Poker → HU Limit Hold'em vs published Cepheus strategy. This is ~1 week of coding and zero compute. If Cepheus match succeeds at Gate 3, the solver is viable. If it fails, abort before spending cloud compute. A solver that can't reproduce Cepheus will not produce trustworthy No-Limit solutions.

**The honest question at Gate 2**: Is the remaining competitive gap a strategy-frequency problem (wrong action frequencies at equilibrium — solver directly helps) or a tactical-decision problem (wrong hand-class reads, wrong defense execution — pipeline fixes help)? The post-patch measurement and Gate 1 evaluation are the mechanism for making this concrete.

**Dependencies**: Gate 1 measurement (post-Polarization Phase B) feeds the go/no-go decision. Gate 2 (user decision). Gate 3 (Cepheus validation). No dependency on cash mode or relationship layer work.

**Effort**: Phase 1 validation only: small-medium engineering, zero compute. Full solver: large engineering + weeks of compute. Compute and calendar time are the binding constraints, not coding speed.

**Cloud cost estimate** (added per consultancy critique #13): If the full 6-max WTA SNG solve is greenlit at Gate 2 and parallelized on cloud rather than serial workstation, realistic budget is **~$5K at modest abstraction, ~$50K at fine abstraction**. The wide range reflects how aggressively the abstraction is tuned: more buckets per street → bigger memory footprint → bigger instance type → higher per-hour rate, and more iterations to converge. My confidence on the specific dollar figures is medium-low — neither has been measured against a representative job. The signal is that this is a **decision input at Gate 2, not a footnote** — cloud cost is comparable to a year of MonkerSolver licenses, so the build-vs-buy calculus shifts depending on abstraction target.

---

### Bucket 7: Strategy Provenance and Diagnostics

**What it is**

Two related cleanup items that improve confidence in what is being measured and built on.

**Provenance documentation**

`preflop_100bb_6max.json` and `postflop_strategies.json` have no README documenting sources, validation approach, or known gaps. `hu_preflop_chart_README.md` is the right model: it documents sources, calls out the binary-frequency limitation, and defers mixed-strategy hands explicitly. Write equivalent READMEs for 6max preflop and postflop: what philosophy or sources drive the strategy, what the coverage gaps are, and what would change if replaced by solver output. Sets honest expectations for the solver decision. Small, any time.

**Equity-tracking diagnostics**

Once Polarization Phase A ships, extend `casebot_breakdown.py` with an equity distribution section: for each opponent archetype label, show the distribution of `equity_when_raising`, `equity_when_calling`, and derived `aggression_polarization` across the sim run. This is the data layer that makes future tuning decisions data-driven rather than intuitive. `InterventionTrace` promotion to a per-decision Trace payload is deferred — the existing snapshot mechanism is sufficient until a cross-iteration replay use case emerges. Small, follows naturally from Phase A instrumentation.

**Dependencies**: Provenance READMEs: none. Equity diagnostics: Polarization Phase A must ship first.

**Effort**: Small total.

---

## Prioritized Phases

### Phase 1 (two parallel tracks, both launch immediately)

The tracks run in parallel. They touch mostly different files. The one coordination point (both eventually modify `_apply_exploitation`) is explicitly managed: **Track B Phase 2 hard-blocks Track A Phase B coding**; everything else in Track A proceeds without waiting.

---

**Track A — Bot Quality and Competitive Feel**

Steps 1–5 and 7–11 proceed in parallel (only step 6 is blocked on Track B Phase 2). Within Track A, the natural order is:

1. Post-patch bb/100 measurement (Rock/TAG/LAG vs CaseBot, 2000 hands × 3–5 seeds). Establishes the new baseline after shipped patches. First task; everything else builds on this. Small (sim run time).

2. ~~§5.5 per-rule offset budgets~~ — verified already shipped (commit 8511414a). Safety net in place; no work needed for Phase B prerequisite.

3. Polarization Phase A — instrument equity-tracking fields (both showdown-equity and decision-time-equity per the bias correction above), populate at showdown / at decision, emit to diagnostics, calibrate via sim, **categorize leak structure per archetype to decide whether Phase B is one rule change or split per archetype**, write standalone polarization spec doc, user approves. Small-medium.

4. Competitive feel items 1 and 2 (sizing randomization, action ties). Independent, zero EV cost. Can ship in parallel with any other Track A step. Small.

5. Push/fold tables for <15bb. **License-vet upstream task lands first** (Sklansky-Chubukov / WizardOfOdds / HRC terms). Independent thereafter. Small.

6. **(Blocked on Track B Relationship Phase 2 merging)** Polarization Phase B — gate hyper_passive fold-reduction on calibrated `aggression_polarization` threshold, re-measure bb/100. This measurement feeds Gate 1. Small-medium.

7. Competitive feel items 3, 4, 6 (creative play injection, cold-start replacement, per-session drift). Sequence after Phase B measurement to keep the baseline clean. Medium each.

8. Competitive feel item 5 (surface narrative_observations). Independent. Small.

9. Strategy provenance READMEs. Independent. Small.

10. T1-26: guest identity forgeable. `poker/auth.py:317, 355`. Random UUID for guest IDs + signed cookie. Small (~half day).

11. T1-27: anonymous chat-session leak. `flask_app/routes/experiment_routes.py:1640, 1689` + `poker/repositories/experiment_repository.py:727`. Per-session owner identifier instead of bucketed `'anonymous'`. Small-medium (half to one day).

---

**Track B — Relationship Layer and Cash Mode**

Sequential within the track.

1. **Personality ID migration** — prerequisite for all persistence work. Higher risk than LOC implies (consultancy critique #6): the in-memory keying flip on `OpponentModelManager.models` from display name to `personality_id` touches every game-load path. If the backfill misses a row, opponent observations get unkeyed mid-session. Mitigations: keep `opponent_name` column permanently (never drop); UNIQUE index allows NULLs so partial-backfill states are recoverable; migration is idempotent and tested for partial-state recovery. **Required additional safeguard**: dry-run the migration against a snapshot of the production DB before flipping the in-memory dict-keying. **Status: foundation shipped on phase-1** (commits `92293f5b`, `fcbfa6f5`, `d738ddb2`, `607da181`, `50734a45`, `2ebb7a19`). Remaining: in-memory keying flip, name→id resolution at game startup, one-time backfill for saved games. Medium total.

2. Relationship Phases 1–3 — vocabulary, identity migration, state, persistence, `record_event()`, pairwise reader, wire modifier seam into `_apply_exploitation`, `HandOutcomeDetector`. The modifier seam commit (Phase 2) is the merge-risk coordination point: **hard-blocks Track A Polarization Phase B coding**. Ships with `apply_relationship_modifier: bool = True` runtime flag for backout (see Bucket 4 backout plan note). Medium (~1 week).

3. Cash mode v1 — `cash_mode/` package: tables, bankrolls, regen, orchestrator, sit/leave/top-up accounting, bust handling, disconnect grace window, side-pot tests, `cumulative_pnl` updates. **Estimate revised to 3–4 weeks (5 if migration surprises surface)** per Bucket 5 effort note.

---

**Groundwork in Phase 1 supporting later plans**

- Personality ID migration (Track B step 1): prerequisite for endgame economy persistence, character unlock flags, any future cross-game-mode state.
- Equity tracking fields (Track A step 3): foundation for Polarization Phases C and D; diagnostic layer for data-driven tuning; ground-truth distributions that can validate solver output if the solver path is chosen.
- Cash mode package architecture (Track B step 3): explicitly designed so v2 (multi-table lobby) and v3 (AI-vs-AI background simulation) do not require redesign.
- Per-rule offset budgets (Track A step 2): safety net for all future rule additions; prevents Phase 8.1b-class regressions from recurring.

---

### Phase 2

Begins after both Phase 1 tracks ship (or are near-complete) and after Gate 1 is evaluated.

Bot quality (regardless of solver decision):
- Polarization Phase C: `polarized_value_caller` rule, affirmative fold-probability increase vs high-polarization opponents. Medium.
- Polarization Phase D: bluffer detection, symmetric low-equity raiser rule. Medium.
- §1.5b extended archetype taxonomy labels, each only when a consuming rule needs the label.
- Competitive feel remaining items (stakes-aware play, any not shipped in Phase 1).
- 15–30bb stack-depth interpolation heuristic if push/fold tables alone prove insufficient.

Relationship layer chat inputs (from `CASH_MODE_AND_RELATIONSHIPS.md` Relationship Phase 5):
- Chat categorizer with `prompt_template='relationship_chat_categorization'`, confidence floor defaults to noise at < 0.6
- `SessionChatState` with diminishing-returns and per-session axis caps
- Post-hand commentary context multipliers
- Medium total.

Solver Phase 1 (if Gate 2 triggers):
- Kuhn Poker CFR → Leduc Poker → HU LHE vs Cepheus. ~1 week coding, zero compute. Gate 3 gates further investment.

---

### Phase 3+

Regardless of solver: cash mode v2 (multi-table lobby, AI table selection, rivalry-seek seating, stop-loss/stop-win knobs — large); endgame economy design passes (staking contracts, character unlocks, private games — each its own design sprint); character/social features (XP from drama, progressive unlocks, tell system).

If solver committed at Phase 2: full HU No-Limit solve (~12h compute), 3-way preflop solve (1–2 days), 6-max WTA SNG full solve (15–30 days serial, Phase 4 at earliest).

---

## Dependencies and Sequencing

```
Personality ID migration
  → relationship_states + cash_pair_stats tables
  → OpponentModelManager keyed on personality_id
  → AI bankroll persistence in cash_mode/

Relationship Phase 1 (vocab, state, persistence, record_event, reader)
  → Relationship Phase 2 (wire modifier seam into _apply_exploitation)   ← MERGE POINT
    → Relationship Phase 3 (HandOutcomeDetector, relationships live from hands)
      → Cash Mode v1
        → Cash Mode v2

Post-patch bb/100 measurement
  → Polarization Phase A (calibrate on post-patch baseline)
    → Polarization Phase B (gate hyper_passive)                          ← MERGE POINT
      → Gate 1 evaluation
        → Polarization Phases C + D (regardless of solver decision)
        → Gate 2 (solver go/no-go)
          → Solver Phase 1 (validation pipeline)
            → Gate 3 (Cepheus match)
              → Full solve (HU → 3-way → 6-max WTA SNG)

§5.5 per-rule offset budgets → Polarization Phase B (stacking safety net)

Push/fold tables <15bb      — no upstream dependency
Sub-action randomization    — no upstream dependency
Action ties                 — no upstream dependency
Creative play injection     — no upstream dependency
Strategy provenance READMEs — no upstream dependency
```

Both MERGE POINT commits touch `_apply_exploitation` in `poker/tiered_bot_controller.py` and offset logic in `poker/strategy/exploitation.py`. Sequence them: merge Relationship Phase 2 before Polarization Phase B coding begins. Document intended application order in `_apply_exploitation`'s docstring: existing pattern detection → relationship modifier scaling → polarization gating → existing clamp/gating.

---

## Decision Gates

### Gate 1: Post-Polarization Phase B Evaluation

**When**: After Polarization Phase B ships and a 2000-hand sim runs. Several weeks into Phase 1.

**Measurement**: bb/100 for TAG (and Rock + LAG) vs CaseBot-class opponents, 2000 hands × 3–5 seeds. Paired-delta comparison against the post-patch baseline established at Track A step 1.

**Starting target** (revised per consultancy critique #1): TAG bb/100 improves by **≥ 20 bb/100** vs the post-patch baseline, with a CI on the paired delta that does not cross zero. **This is a starting target, not a hard cutoff.** The actual threshold must be **calibrated from Phase A's distribution data** before Gate 1 fires — if Phase A reveals the polarization signal isn't powerful enough to drive a 20 bb/100 swing, the target moves to a delta that's realistic given the signal's actual strength. Locking the threshold to 20 ahead of measurement risks either (a) declaring success on a barely-better bot or (b) declaring failure on a meaningful improvement that just isn't 20.

**Paths**:
- Gap closes substantially (delta exceeds calibrated target): continue Polarization Phases C and D; defer solver indefinitely.
- Gap does not close meaningfully, or user can still consistently beat the bot: trigger Gate 2. Continue Polarization Phases C and D regardless — they are not dependent on the solver decision.

**Calibration note**: A bot at −20 bb/100 in simulation that genuinely surprises the player may be good enough. A bot at −10 bb/100 that still feels mechanical is not. Both data points feed this gate, but the **sim signal is the rigorous one** — see play-testing source below.

---

### Gate 2: Solver Go/No-Go

**When**: After Gate 1 triggers the consideration. Roughly coincides with Phase 2 start.

| Factor | Go signal | No-go signal |
|---|---|---|
| TAG bb/100 after polarization | Substantially negative (e.g., still > −40) | Near zero |
| User subjective feel | "I can still clean up" | "It genuinely surprises me" |
| Leak character | Strategy-frequency problem | Tactical-decision problem |
| Calendar/compute appetite | Available | Not allocated |

**Paths**:
- Go: Begin solver Phase 1 (validation pipeline). ~1 week coding, zero compute. Proceed to Gate 3.
- No-go: Defer solver indefinitely. Continue Phases C and D. Revisit if future play-testing reopens the question.

---

### Gate 3: Cepheus Validation Checkpoint

**When**: After solver Phase 1 (HU LHE vs Cepheus) completes.

**Measurement**: Does the HU LHE solver converge to the Cepheus equilibrium within acceptable tolerance? Recommended starting bar: ≤ 2% mean strategy error across canonical hand×board situations at 100k CFR iterations.

**Paths**:
- Pass: Proceed to HU No-Limit solve and 3-way preflop. Budget cloud compute.
- Fail: Debug and fix, or abort. A solver that cannot reproduce Cepheus will not produce trustworthy No-Limit solutions. Do not spend cloud compute on an unvalidated engine.

---

## Effort Sizing Summary

Column note (per consultancy critique #10): "External compute" means *rented compute or solver runtime* — not test-suite drag, which is real wall-clock time tracked separately in the "Bottlenecks that are not engineering" section below. Every bucket pays test-suite drag at merge time.

| Bucket | Engineering effort | External compute | Phase |
|---|---|---|---|
| 1: Polarization Phase A + B + §5.5 | Small-medium total | Sim runtime (~hours per 2000-hand sweep) | Phase 1 Track A |
| 1: Polarization Phase C + D | Medium each | Sim runtime | Phase 2 |
| 2: Competitive feel (quick items) | Small | None | Phase 1 Track A (any time) |
| 2: Competitive feel (medium items) | Medium each | None | Phase 1 Track A (after Phase B) |
| 3: Push/fold tables <15bb | Small (after license vet) | None | Phase 1 Track A |
| 3: Stack-depth interpolation heuristic | Medium | None | Phase 2 |
| 4: Relationship layer (Phases 1–3) | Medium (~1 week) | None | Phase 1 Track B |
| 5: Cash mode v1 | Large (3–4 weeks, 5 if migration surprises) | None | Phase 1 Track B |
| 6: Solver Phase 1 (validation only) | Small-medium | Hours of CFR runtime | Phase 2 (gate-dependent) |
| 6: Solver full solve | Large | 15–30 days serial OR ~$5K–$50K cloud | Phase 3+ (gate-dependent) |
| 7: Provenance READMEs + diagnostics | Small | None | Phase 1 Track A (any time) |

**Bottlenecks that are not engineering**

**Regression test turnaround is the largest pace constraint.** The full test suite (3700+ tests in Docker) is the single biggest drag on iteration speed. Coding a change is fast; verifying it didn't break anything elsewhere is slow. Mitigation strategy for this phase:

- **Inner loop**: run only directly-affected test modules during development (`python3 scripts/test.py test_strategy` or specific files). Trust unit-test scope to catch the immediate regressions.
- **Pre-commit**: run `python3 scripts/test.py --quick` for the fast subset. Catches most non-trivial regressions without paying for the slow ones.
- **Pre-merge**: full suite + TypeScript check. The slow path is acceptable at this gate but not for every keystroke.
- **Per-bucket policy**: identify the test module(s) most relevant to each bucket before starting (e.g., Bucket 1 = `test_strategy/`, Bucket 4 = `test_memory/`, Bucket 5 = TBD `test_cash_mode/`). Inner-loop runs scope to those.
- **Failure budget**: if the inner-loop run is clean and pre-commit `--quick` is clean, accept the ~5% risk of a full-suite surprise rather than running full-suite each iteration. Cost of catching the rare regression at PR time is lower than the cost of waiting on full-suite every commit.

Solver compute: 15–30 days serial wall-clock for full WTA SNG solve. Parallelizable on cloud, but requires setup time, cost decision, and cloud access.

Gate review: each of the three gates requires play-testing and a go/no-go decision. These add real calendar time independent of coding speed.

Polarization calibration: Phase A equity tracking must accumulate enough hands (recommend 2000+ per archetype) before Phase B threshold can be set. This is sim run time, not a coding delay.

---

## Not in Scope for Next Phase

These are explicitly deferred, not permanently canceled.

**ICM-aware play**: Not needed for WTA SNG (chip EV = $ EV throughout). Would matter only if the game adds a pay-jump structure. No current plan for that.

**6-way postflop solves**: Compute cost is prohibitive and postflop heuristics are not the identified binding constraint. Use heuristics for 6-max postflop until there is evidence they are the bottleneck.

**Multi-table cash lobby (cash mode v2)**: Fully designed in `CASH_MODE_AND_RELATIONSHIPS.md` Relationship Phase 6. Depends on cash mode v1 being stable. Phase 3+ at earliest.

**Tournament narrative system**: Simulated other tables, news flashes, emergent storylines (`FEATURE_IDEAS.md`). Major content and simulation effort. Phase 3+ at earliest.

**Full XP / progression / unlocks**: Depends on the relationship layer being mature and real usage data generating calibration signals. Phase 3+ at earliest.

**AI-vs-AI background simulation (cash mode v3)**: Designed-for as an architectural constraint on the v1 package structure. Not shipped until v3.

**Endgame economy** (staking contracts, private games, character unlocks): Each requires a separate design sprint. Phase 3+ at earliest.

**Coach mode M3** (`docs/plans/M3_PLAN.md`): Active parallel effort targeting Gate 3 and Gate 4 coaching skills. M1 and M2 already shipped — the foundation exists. M3 runs in a **separate Claude session**, touching `flask_app/services/coach_*.py` only. No intersection with the bot decision pipeline, relationship layer, or cash mode work in this roadmap. Coordination point: database schema migrations — both sessions check git before committing migration files. Not deferred — runs independently of this roadmap.

**Personality mixer, emoji quick chat, daily rotation** (`QUICK_WINS.md`): Fun additions, not competitive-feel or character-depth improvements at the level this phase requires. Defer to between-phase bandwidth.

**~~T1-26 and T1-27~~**: Moved into Phase 1 Track A scope (small fixes, ~1 day total).

---

## Operating Notes

### Cadence

**Trigger-based.** Default state is async — Claude executes, ships in coherent chunks, you review when convenient. Sync check-ins fire at three triggers:

1. **Gate decisions** (Gates 1, 2, 3). User makes the strategic call.
2. **Polarization spec doc complete** (Phase A → Phase B handoff). User reviews the spec and approves before Phase B coding begins.
3. **Unexpected scope discovery.** Anything that materially changes Phase 1 scope (new dependency surfaces, an architectural choice that affects multiple buckets, etc.) — surface immediately rather than ship through.

Otherwise, Claude ships work in batches and surfaces a summary at natural milestone boundaries (post-patch measurement done, migration done, Phase A instrumented, etc.).

### Play-testing source

Revised per consultancy critique #14:

- **Rigorous signal: sim measurements.** bb/100 over 2000+ hands × 3–5 seeds against CaseBot and varied opponents. Claude runs these. This is the gate input.
- **User playtesting**: confirms the bot doesn't feel obviously broken to a fresh perspective. Good for "I notice it doing X" qualitative observations that sim doesn't surface. Not the gate.
- **Friends playtesting**: low-rigor sanity check only. They'll either go easy (confirmation theater) or play casually for 50 hands and get bored before bb/100 stabilizes. Useful for "did anyone find it weird?" but not for go/no-go decisions.
- No open beta in Phase 1. Defer to whenever the project is ready for it.

Order of evidence weight at gate time: **sim >> user playtest >> friend playtest**. If sim says go but the user says "still feels weak," investigate (likely an unmeasured leak the sim doesn't catch). If sim says go and friends say "still feels weak," friends might just be playing differently than the sim opponents.

### Polarization spec doc workflow

Polarization Phase B is gated on an approved spec doc:

1. **Phase A ships** — instrumentation, no rule changes. Data accumulates from sim runs.
2. **Claude writes the spec doc** — using Phase A's calibration data to set thresholds. Doc lands at `docs/plans/POLARIZATION_DETECTION.md` following the YAML header convention.
3. **User reviews and approves** — explicit go-ahead before Phase B coding starts.
4. **Phase B implementation** — code follows the approved spec.

This gate exists because the threshold choices in Phase B (what counts as "polarized enough" to suppress fold-reduction) carry real bb/100 consequences. The spec-then-approve pattern mirrors how the HU preflop chart was authored — design committed in doc form before code.

### Coach M3 parallel-session coordination

M3 work runs in a separate Claude session against `docs/plans/M3_PLAN.md`. The only coordination point is database schema migrations: both sessions check `poker/repositories/schema_manager.py` before committing migration files to avoid collisions. Other than that, the two sessions touch disjoint code (`flask_app/services/coach_*.py` for M3; `poker/strategy/`, `poker/memory/`, and the new `cash_mode/` package for this roadmap).

### What "done" means for this phase

Phase boundaries are advisory in the sense that we don't ship a release on a date — we iterate and reach gates when the data is in. But the gate triggers need a concrete "phase done" definition so we know when Gate 1 fires (revised per consultancy critique #12):

**Phase 1 done = all of:**
- Track A items **1–6** shipped to main (post-patch baseline → §5.5 budgets → Polarization Phase A instrumented + spec approved → competitive feel quick items → push/fold tables → Polarization Phase B gating)
- Track B items **1–2** shipped to main (personality_id migration complete end-to-end → relationship layer Phases 1–3 wired + apply_relationship_modifier flag in place)
- **Gate 1 evaluated**: post-Phase B bb/100 measured against post-patch baseline; user reviews and either triggers Gate 2 or defers solver indefinitely

**Track A items 7–11 (competitive feel medium items + provenance READMEs + security fixes) and Track B item 3 (cash mode v1)** may be partial at the end of Phase 1 — they continue into Phase 2 without blocking Gate 1.

Phase 2 then either kicks off solver Phase 1 validation (if Gate 2 was triggered at the Gate 1 review) or focuses on Polarization Phases C+D + chat-driven affinity + the carryover Phase 1 items.

---

## Source Documents

| Document | Role in this roadmap |
|---|---|
| `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` | Complete spec for Buckets 4 and 5. Relationship Phases 1–8 defined there. Treat as canonical; this doc does not duplicate its content. |
| `docs/plans/TIEREDBOT_DECISION_QUALITY.md` | Bot quality work §1–§7. §5.5 and §1.5b are the remaining open items from that doc. Polarization detection (Bucket 1) is new — a standalone polarization spec doc should be written before Phase B implementation begins. |
| `docs/vision/GAME_VISION.md` | Strategic philosophy ("drama over mathematics," "emergent personalities," "living world"). Phase 1–5 structure in that doc is superseded for roadmap purposes by this document. |
| `docs/vision/FEATURE_IDEAS.md` | Feature brainstorm; most items deferred to Phase 3+. Relationship/rivalry quick-win estimates superseded by the full design. |
| `docs/vision/QUICK_WINS.md` | Quick win estimates; rivalry tracker estimate superseded. Other quick wins remain valid but deferred. |
| `docs/triage/PRE_MAIN_SCOPING.md` | Pre-main batch — all T1/T2 items shipped; T3-70..T3-74 deferred post-release. |
| `docs/TRIAGE.md` | Per-item status; T1-26 and T1-27 remain open. |
| `docs/plans/M3_PLAN.md` | Coach progression M3 — parallel effort, no intersection with this roadmap. |
| `poker/strategy/hu_preflop_chart_README.md` | Model for strategy provenance documentation. Write equivalent READMEs for 6max preflop and postflop tables (Bucket 7). |
