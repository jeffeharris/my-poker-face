---
purpose: Design + handoff for unifying the duplicated opponent-stat computation paths (VPIP/PFR/AF/WTSD/fold-to-cbet…) behind a single source of truth for the FORMULAS, and feeding them from one event stream
type: design
created: 2026-06-12
last_updated: 2026-06-12
status: Tier 1 + sim showdown feed IMPLEMENTED (2026-06-12) — see §9
---

# Opponent-stat source of truth

> **Handoff doc.** Written from the strategy re-validation session (2026-06-12)
> after the same root cause blocked detector work **three times in a row**. The
> recommendation is deliberately scoped: **unify the stat *definitions*, and make
> the *event feed* consistent across prod and sim — NOT build a pub/sub stats
> service** (that solves a problem we don't have yet). Read §3 (evidence) before
> deciding scope; it's the justification.

## 1. TL;DR / recommendation

The same family of opponent stats (VPIP, PFR, AF, postflop AF, fold-to-cbet,
call-rate, WTSD, per-street AF, saw-flop) is **computed in ~4 places with ~4
hand-written implementations** of the same formulas, fed by **divergent event
paths** (prod vs sim). They drift. We have repeatedly hit "this stat isn't
trustworthy/consistent across paths" while trying to harden the exploitation
detector.

**Do, in ROI order:**

1. **Single stat-*definition* module** (pure functions) — one canonical formula
   for each stat, imported by all sites. Kills definitional drift. Low risk,
   high value. **This is the core ask.**
2. **One event-reducer, many projections** — live tendencies, archetype stats,
   sim, and backfill all fold the *same* "action observed / showdown reached"
   event stream through the *same* counters. Bigger; do it if drift persists
   after (1). The immediate concrete instance: **make the sim feed showdowns**
   (it currently doesn't — see §3) via the same path prod uses.
3. **Subscribable live-stats service** — **DEFER.** There is essentially one
   real-time consumer today (the exploitation detector, read synchronously).
   Pub/sub is over-engineered until multiple consumers each re-derive live.

**Source of truth = the definition (the formula), not a runtime service.**

## 2. The problem

"Opponent stats" are computed at four sites, at different granularities, time
horizons, and for different consumers. The *storage/lifecycle* differences are
legitimate and should stay. What is duplicated — and drifts — is the **formula
logic**: "what counts as *saw flop*," the AF ratio, "what's a *facing-bet
opportunity*," the WTSD ratio, the per-opportunity VPIP/PFR normalization.

| # | Site | File | Granularity | When | Consumer |
|---|---|---|---|---|---|
| 1 | live `OpponentTendencies` | `poker/memory/opponent_model.py` (`update_from_action`, `_recalculate_stats`, `_recalculate_postflop_stats`) | per observer×opponent | online, event-driven | the exploitation detector (`AggregatedOpponentStats`) |
| 2 | archetype aggregate | `poker/repositories/archetype_stat_repository.py` + migration `…/20260609_1200_archetype_stat_showdown.py` + `flask_app/routes/archetype_review_routes.py` | per archetype | sim-side + retroactive batch | Archetype Review tool |
| 3 | clone derivation | `poker/human_clone.py` (`derive_profile_from_db`, ~L130–189) | per player | offline, one-shot from `hand_history` | sim test beds (`clone_profiles/*.json`) |
| 4 | retroactive backfill | `player_decision_analysis` + `hand_history` (the review tool's "live path") | per archetype | batch | review tool |

The detector reads #1 via `AggregatedOpponentStats` (`poker/strategy/exploitation.py`),
built at copy sites in `poker/tiered_bot_controller.py` (~L3733, ~L3973, ~L4646)
and blended in `aggregate_from_spots` / `_aggregate_stats` (field lists
`_AGG_RATE_FIELDS` / `_AGG_MIN_FIELDS`).

## 3. Evidence — why this is now load-bearing, not cleanup

Three drift instances hit in one session, plus an already-acknowledged duplicate:

1. **Authored clone stats ≠ live-observed.** `punisher.json` authors
   `fold_to_cbet=0.70`, `aggression_factor=3.0`; the live detector observes
   ~`0.00` and ~`0.9` for the same bot in play (`detection_fidelity_probe`,
   `STRATEGY_REVALIDATION_MATRIX.md`). Partly sampling, partly **the clone derives
   stats one way (offline from `hand_history`, site #3) and the live model observes
   them another way (online event stream, site #1)** — different denominators,
   different definitions.

2. **`call_rate_facing_bet` confounded.** Added as a "stickiness" axis; reads
   0.80–0.92 for *every* clone including an authored 90%-folder, because realized
   calling depends on what the hero bet, not just villain stickiness. (Signal-choice
   issue, but it's *why* we reached for WTSD, which exposed #3.)

3. **Showdowns fed in prod but NOT in sim → WTSD dead in sims.** WTSD numerator is
   `_showdowns` (incremented by `OpponentTendencies.update_showdown`, called from
   `MemoryManager.complete_hand` → `observe_showdown`, `opponent_model.py:1379`).
   **`simulate_bb100.run_hand` bypasses `MemoryManager.complete_hand`** and never
   calls `observe_showdown`, so `_showdowns` stays 0 and `wtsd = 0/saw_flop = 0`
   for every opponent in every sim. The denominator (`_saw_flop`, added this
   session) populates fine; the numerator path simply isn't wired in sims.

**The smoking gun:** `experiments/simulate_bb100.py:691` —
`_record_sim_equity_at_actions` is documented as *"Sim-side equivalent of
`MemoryManager._record_showdown_equity_at_actions`"*, a deliberate **sim-side
reimplementation** of a prod recorder, written because "`run_hand` bypasses
`MemoryManager.complete_hand`." So the codebase *already* carries a duplicated
recorder for exactly this reason — and the showdown feed fell through the same
crack. This is the pattern to kill.

## 4. Proposed architecture

### Tier 1 (do this) — shared stat-definition module

Create `poker/memory/stat_definitions.py` (name negotiable): **pure functions, no
state**, one per stat/counter rule. Every site imports these instead of inlining
the formula. Candidates (each currently inlined ≥2×):

- `is_saw_flop(action, phase) -> bool` — "took any postflop action" (the WTSD /
  per-street denominators key off this).
- `wtsd(showdowns, saw_flop) -> float` (clamped [0,1]).
- `aggression_factor(bet_raise, call) -> float` and
  `aggression_factor_postflop(...)` (incl. the zero-call cap, currently in
  `_recalculate_postflop_stats`).
- `call_rate_facing_bet(calls, facing_bet_opps) -> float`.
- `fold_to_cbet(folds, faced) -> float`, `fold_to_big_bet(...)`.
- `vpip_per_voluntary_opportunity(...)`, `pfr_per_open_opportunity(...)` — the
  per-opportunity normalization (the player-count-stable definitions that diverge
  from raw `vpip`/`pfr` AND from `archetypes.py`'s raw-VPIP thresholds — see
  `STRATEGY_REVALIDATION_MATRIX.md` "canonical grid" note).
- Per-street AF (flop/turn/river) — defined in `archetype_stat_repository` and
  re-derived in `human_clone`.

Then refactor the four sites to call them. Behavior-preserving; lock with the
existing characterization tests (`tests/test_strategy/test_exploitation_characterization.py`)
+ a new "all sites agree on the same inputs" test.

**Also unify the threshold/quadrant definitions** with `poker/archetypes.py`
(`play_style_label`, `VPIP_TIGHT`, `AF_PASSIVE`…), which is a *fifth* place the
"what is a station/nit/TAG" boundaries live — see the matrix doc's two-taxonomies
finding. At minimum, cross-reference; ideally the exploitation detector's
`_is_loose_passive_station` / `_is_tight_nit` / `_is_hyper_passive` thresholds and
`archetypes.play_style_label` derive from one constant set.

### Tier 2 (if drift persists) — one event-reducer, many projections

The counters (`_vpip_count`, `_saw_flop`, `_showdowns`, `_facing_bet_opportunities`,
…) are a reducer over an event stream of `(observer, opponent, action, phase,
was_facing_bet, hand_number, showdown_reached, won)`. Today three feeders emit a
*subset* of that stream into a *copy* of the reducer:

- prod: `MemoryManager.complete_hand` / live action hooks → full stream incl.
  showdown.
- sim: `simulate_bb100` `observe_action` + `_record_sim_equity_at_actions` →
  actions + equity, **no showdown** (the bug).
- backfill: `player_decision_analysis` + `hand_history` walk.

**Concrete immediate win (independently useful):** make `simulate_bb100` emit the
showdown event through the *same* `observe_showdown` path prod uses. The showdown
set is already computed at `_record_sim_equity_at_actions`
(`revealed = [p for p in players if not p.is_folded]`, `simulate_bb100.py:723`);
when `len(revealed) >= 2`, call `opponent_manager.get_model(hero, p.name).observe_showdown(won=…)`
for each non-hero `p`. For WTSD only the *count* matters (`won` affects only
`showdown_win_rate`); determine `won` best-effort from final stacks or pass a
documented placeholder. This unblocks WTSD validation in the clone beds.

The fuller Tier-2 move is to delete `_record_sim_equity_at_actions` as a *separate*
implementation and have the sim drive the *same* recorder prod uses (extract
`MemoryManager`'s per-hand stat-feeding into a controller-agnostic function the sim
can call). That removes the acknowledged duplicate.

### Tier 3 (defer) — subscribable live-stats service

Only if multiple real-time consumers (dossier UI, coach, narration, detector) each
start re-deriving live reads. Today they don't. Not justified yet.

## 5. Scope / non-goals

- **Keep the four storage lifecycles.** Online per-pair, offline per-archetype, and
  frozen per-player snapshots are genuinely different and should remain. This is
  about the *formulas* and the *event feed*, not merging the stores.
- **Behavior-preserving.** Tier 1 must not change any live decision; it's a refactor
  locked by characterization tests. Any stat-value change is a separate, measured
  decision.
- **Clone fidelity is related but separate.** Sharing definitions removes the
  *definitional* part of the authored≠observed gap; the online-vs-offline +
  hero-dependent-sampling part remains and is tracked in the matrix doc.

## 6. First concrete steps for the implementer  *(DONE — see §9 for what shipped)*

1. Inventory every inlined formula (grep the four sites for `aggression_factor`,
   `fold_to_cbet`, `wtsd`/`saw_flop`, `_per_voluntary_opportunity`,
   `_per_open_opportunity`, per-street AF). Build the function list.
2. Write `stat_definitions.py` with pure functions + docstrings stating the exact
   denominator for each (the matrix doc already has a glossary;
   `PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md` "Stat-definition glossary" too).
3. Refactor site #1 (`OpponentTendencies`) first; run
   `test_exploitation_characterization.py` (golden to_dict/round-trip) +
   `experiments/detection_fidelity_probe.py` (values must be byte-identical pre/post).
4. Add the sim showdown feed (§4 Tier 2 immediate win); confirm `wtsd` becomes
   non-zero in `detection_fidelity_probe` and **separates** the authored folder
   (`spewy_folder_fish`, authored wtsd 0.30) from the stations (`station_fish` 0.78,
   `jeff` 0.59). That validation was the blocker that motivated this doc.
5. Then refactor sites #2–#4 to import the same definitions.

## 7. Current state this doc hands off (already on branch `strategy-revalidation`)  *(pre-implementation snapshot — superseded by §9)*

- WTSD is **already wired on the live model** (`_saw_flop` counter + `wtsd` field +
  persistence + `AggregatedOpponentStats.wtsd` + build-site copy + `_AGG_RATE_FIELDS`).
  Tests green (`627 passed`). It is **dormant in the detector** — the sticky axis of
  `_is_loose_passive_station` still uses `call_rate_facing_bet`, NOT `wtsd`. Switching
  the axis to WTSD is pending §6 step 4 (validate it discriminates first).
- `call_rate_facing_bet` is also wired (same pattern) but shown to be confounded;
  keep or drop per the implementer's call after WTSD validates.
- Nothing in the live decision path changed from these additions (both new axes are
  surfaced-but-the-detector-still-gates-on-call_rate, which fires the same as before).

## 8. Related docs

- `docs/plans/STRATEGY_REVALIDATION_MATRIX.md` — the session this came from; the
  detection-fidelity findings, the canonical-grid / two-taxonomies note, the
  clone-fidelity gap.
- `poker/archetypes.py` — the canonical 4-quadrant grid (a 5th definition site).
- `PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md` — existing stat-definition glossary.

## 9. What was implemented (2026-06-12)

Tier 1 (the core ask) + the sim showdown feed shipped. Tier 2's fuller move
(deleting `_record_sim_equity_at_actions` as a separate recorder) and Tier 3
(pub/sub service) remain deferred as designed.

**`poker/memory/stat_definitions.py`** — new pure-function module, the home for
every stat formula used anywhere (centralized for discoverability, not just the
ones that drifted). Contents:
- canonical action/phase vocabularies (`VOLUNTARY_PREFLOP_ACTIONS`, `PFR_ACTIONS`,
  `AGGRESSIVE_ACTIONS`, `POSTFLOP_PHASES` + `is_*` predicates);
- core ratios: `safe_ratio`, `aggression_factor` (parameterized `zero_call_cap`
  unifies global / postflop / per-street AF), `wtsd`, `fold_to_cbet`, `vpip`,
  `pfr`, `all_in_frequency`, per-opportunity VPIP/PFR, `call_rate_facing_bet`,
  `all_in_per_facing_bet`, `postflop_jam_open_rate`, `showdown_win_rate`,
  `limp_rate`;
- non-ratio shapes: `mean` (running average behind the equity-at-action means),
  `polarization` (high-bucket − low-bucket, behind `sizing_polarization_score`
  and the aggression-polarization signal);
- postflop tendency rates pulled in from the live model (were single-site):
  `fold_to_big_bet`, `stab_frequency`, `cbet_attempt_rate`, `barrel_frequency`,
  `third_barrel_frequency`, `flop_check_then_barrel_rate`;
- **iso-over-limper scaffolding** (`fold_to_iso`, `limp_call_rate`,
  `limp_reraise_rate`) — pure definitions + the documented counter contract for
  the sibling-branch exploit; no live feeder yet (functions are inert until wired).

Its docstring is the "add a new tendency stat" contract. Unit + cross-site-agreement
tests: `tests/test_memory/test_stat_definitions.py`.

**Sites refactored to import it** (all behavior-preserving; the live-model site
verified **byte-identical** on `detection_fidelity_probe` pre/post, characterization
green):
- `poker/memory/opponent_model.py` (`update_from_action` predicates +
  `_recalculate_stats` / `_recalculate_postflop_stats` formulas) — the canonical
  reducer.
- `cash_mode/archetype_stats.py` and `flask_app/routes/archetype_review_routes.py`
  — their duplicated local action sets (`_VOLUNTARY`/`_AGGRESSIVE`/`_POSTFLOP`)
  were **drifted**: they omitted `'bet'`. Adopting the canonical superset is a
  no-op there because both data sources are tiered-bot-only and tiered bots never
  emit `'bet'` (only `'raise'`); their presentation layer (percent ×100, `99.0`
  all-agg sentinel, None-for-no-data) is left as-is — that's per-consumer, not drift.
- `poker/human_clone.py` (`_mine_hand_history` per-street `_af` + `wtsd`) — uses
  the shared **formula** only.
- `poker/strategy/exploitation.py` ↔ `poker/archetypes.py` — `archetypes.py` now
  documents itself as the registry/index of BOTH stat spaces (raw-stat quadrant
  vs the detector's per-opportunity/postflop space); the one genuinely-shared
  boundary (`VPIP_TIGHT` 0.30) is imported so it lives in exactly one place. AF
  boundaries intentionally differ and stay separate.

**Sim showdown feed** — `experiments/simulate_bb100.run_hand` now feeds
`observe_showdown` on the unconditional hand-end path (gated only on an attached
`opponent_manager` + `hero_name`; bare bb/100 sims are untouched). WTSD is now
non-zero in opponent-model sims and deterministic.

### Known follow-ups (separate, measured decisions — NOT done here)

- **WTSD works and discriminates — the open question (swap the sticky axis to it)
  is moot, for a clone-fidelity reason, not a WTSD reason.** With the feed live,
  `detection_fidelity_probe` (2000 hands, seed 42):

  | clone | WTSD hu / 6max | call_rate hu / 6max | AF_postflop hu / 6max |
  |---|---|---|---|
  | SpewyFolder (authored folder) | 0.71 / 0.81 | 0.86 / 0.68 | 0.23 / 0.53 |
  | Station | 0.80 / 0.86 | 0.91 / 0.74 | 0.15 / 0.25 |
  | Jeff | 0.80 / 0.88 | 0.91 / 0.82 | 0.17 / 0.44 |
  | Punisher (aggressive reg) | **0.46** / 0.79 | 0.79 / 0.69 | 0.85 / 1.30 |

  WTSD cleanly separates the aggressive reg (Punisher 0.46 HU) from the sticky
  fish (0.71–0.80). It does NOT separate the *authored* folder from the stations —
  but that's because `spewy_folder_fish` doesn't fold in play (`call_rate` 0.86):
  its authored `fold_to_cbet=0.70` doesn't manifest (same fidelity gap as
  Punisher's authored fold_to_cbet → ~0.00). WTSD agrees with `call_rate` — both
  correctly read it as sticky. **So switching `_is_loose_passive_station`'s sticky
  axis from `call_rate_facing_bet` to WTSD buys nothing on this clone set (they
  measure nearly the same thing here); the real gap is a folder clone that doesn't
  fold, which is a clone-authoring fix, not a stat fix.** WTSD itself is validated.
- **Clone per-street AF uses a narrower aggression set** (`'raise'` only, not
  `all_in`/`bet`) than canonical `AGGRESSIVE_ACTIONS`. Left as-is (changing it
  would move derived clone profiles); flagged in `human_clone.py`. Reconcile as a
  measured decision if/when clone fidelity work resumes.
- Tier 2 fuller move + Tier 3 service: deferred as designed.
