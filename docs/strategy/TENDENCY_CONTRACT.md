---
purpose: First-class "tendency" contract — the shared object that defines a poker leak once and uses it to construct archetypes, detect opponents, and exploit them (the spine of the strategy-layer architecture)
type: spec
created: 2026-06-12
last_updated: 2026-06-12
---

# Tendency contract (first pass)

A **tendency** is a poker leak defined ONCE and reused in all directions (see
`STRATEGY_LAYERS.md`): *construct* an archetype that has it, *detect* it in an
opponent, *counter* it (exploit). Construct and counter are inverses. This doc is
the data contract; it is intentionally implementation-light.

## The object

```
Tendency:
  id:            str            # e.g. "calling_station", "over_folds_to_cbet"
  description:   str

  # --- DETECT (opponent model -> "does villain have this leak?") ---
  detect:
    stat:        str            # field on AggregatedOpponentStats
    op:          ">" | "<"
    threshold:   float
    min_sample:  int            # opportunity count before the read is trusted
    extra:       [predicate]    # optional AND-conditions (e.g. AF_postflop<0.80)

  # --- CONSTRUCT (trunk -> archetype that EXHIBITS the leak) ---
  construct:                    # a transform applied to the TRUNK chart
    branch:      [str]          # rfi | vs_open | vs_3bet | postflop:cbet | ...
    shift:       str            # the deviation, in tendency terms
    # e.g. "route X% of fold mass -> call" / "damp own bluff/raise mass"

  # --- COUNTER (detected in opponent -> hero's exploit) ---
  counter:
    mechanism:   "gear" | "override" | "shift"   # L1 chart-switch | L3 hard | small
    action:      str            # what hero does (catalog counter-tendency)
    magnitude:   "hard" | "large" | "flavor"     # must change behavior unless flavor

  # --- SCOPE (when does construct/counter apply?) ---
  scope:
    who:    "while_active" | "single_villain" | "primary_aggressor"
    where:  {ip: bool, oop: bool, seat: "any"|"late"|"early"}
    when:   [street]            # preflop|flop|turn|river
    depth:  "any" | "deep" | "short"

  # --- GATES ---
  detectability: "have" | "proxy" | "need_stat"   # vs current AggregatedOpponentStats
  confidence:    "sample-ramp"                     # ramp by min_sample (a wrong read is -EV)
  psychology:    per mechanism:                    # gear -> composed_only (HARD: no chart-switch unless fully composed)
                                                    # override/shift -> scaled by _zone_to_tilt_factor (1.0/0.5/0.0)
  tier_scale:    "adaptation_bias"                 # weak heroes adapt less
```

## Worked examples

### calling_station (loose-passive)
- **detect:** `vpip_per_voluntary_opportunity > 0.70` AND `aggression_factor_postflop < 0.80`, min_sample ~15. (have — but re-key the existing `hyper_passive` off *postflop* AF.)
- **construct:** vs_open/vs_3bet → route fold mass → call; postflop → damp own bet/raise (under-bluff), keep calls (over-call).
- **counter:** gear → value-wide base; override A1 stop-bluffing (air→check, **hard**), A2 thin/big value.
- **scope:** who=while_active; when=all streets; depth=any.

### over_folds_to_cbet (weak-tight / fit-or-fold)
- **detect:** `fold_to_cbet > 0.60`, min_sample ~5 cbet-faced. (have)
- **construct:** postflop facing-cbet → raise fold mass (folds when misses).
- **counter:** override D1 c-bet + double-barrel (**large**), **ungate for multiway**; D2 give up when they continue.
- **scope:** who=primary_aggressor/initiative; where=any; when=flop,turn; depth=deep.

### nit_overfold_preflop (tight-passive)
- **detect:** `vpip_per_voluntary_opportunity < 0.30`, min_sample ~15. (have)
- **construct:** rfi → tighten opens; vs_open/vs_3bet → raise fold mass.
- **counter:** gear → steal-wide base from LP (**large**); B4 fold-to-their-aggression (**hard**); B3 3-bet bluff (needs `fold_to_3bet` — need_stat).
- **scope:** who=single_villain (steal) / facing-them (B4); where=seat:late for steal; when=preflop; depth=deep.

### hyper_aggressive (maniac / over-bluffer)
- **detect:** `aggression_factor >= 3.5` OR `all_in_frequency >= 0.30` OR high `barrel_frequency`. (have)
- **construct:** postflop → inflate own bet/raise + bluff mass.
- **counter:** C1 bluff-catch wider (**large**), C2 stop bluffing, C5 don't fold made to barrels.
- **scope:** who=facing aggression; HU strongest; when=all streets.

## How it plugs into the layers (STRATEGY_LAYERS.md)
- **detect** → opponent model / `classify_detected_patterns` (one predicate per tendency).
- **construct** → trunk transform that generates the archetype chart (replaces the ad-hoc `build_archetype_charts.py` transforms with an enumerable tendency set).
- **counter.gear** → opponent-read keyed `_select_preflop_table` (layer 1).
- **counter.override/shift** → layer-3 rule (real shift / hard override, not a dead nudge).
- **scope** → gating around both construct and counter.
- **psychology** → `_zone_to_tilt_factor` gate extended to the gear-switch (composed only).

## Implementation sketch (next pass, do NOT build yet)
1. A `Tendency` dataclass + a registry (like `RULE_ORDER`), each row one tendency.
2. `detect` reuses/extends the `_is_*` predicates → emit detected-tendency set.
3. `counter` dispatch: `gear` → choose trunk+tendencies chart; `override`/`shift` → existing layer-3 apply path (hardened to change behavior).
4. `construct` → a build-time (or runtime) trunk transform per tendency → archetype.
5. Validate EVERY tendency: `exploit_behavior_probe.py` (the play must move vs the matching opponent; must NOT move when tilted) → then bb/100 vs that opponent type (and a non-matching control to catch misfire).

## Prereqs (unblock measurement)
- **Probe detection reachability FIRST**, then re-key only what's proven to fire
  live. The obvious re-keys (`hyper_passive`→postflop AF; ungate multiway
  `high_fold_to_cbet`) are premised on detections that don't currently reach real
  hands (matrix doc §follow-up: vpip-0.35 reg < 0.70 cutoff; observed fold-to-cbet
  ~0.06). Re-keying blind would change nothing.
- For `need_stat` rows: `threebet_rate`, `fold_to_3bet`, `wtsd` are already computed
  in `flask_app/routes/archetype_review_routes.py:391` — **promote them onto
  `AggregatedOpponentStats` / the runtime aggregate**, don't reinvent. Genuinely new:
  `fold_to_turn_cbet`, `fold_to_river_bet`, limp/donk/bet-size.

Note: the schema's `construct` field = the `leak_tendency` (build the archetype) and
`counter` = the `counter_tendency` (exploit it) — related inverses, distinct transforms.
