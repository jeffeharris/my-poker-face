---
purpose: Plan to make earned Infamous Villains emerge in Renown-v2 by fixing the regard denominator (sandbox-scope + recency-weight), rather than relabelling a warm-skewed field with a relative threshold
type: design
created: 2026-06-02
last_updated: 2026-06-02
---

# Renown-v2 — Regard balance & the earned-Villain problem

Follow-on to the figure-cut fix (`fix(renown): top-decile percentile figure cut`,
commit `c266104a`). That change made the **renown** axis produce a small figure
set (top decile). This doc addresses the orthogonal **regard** axis: why the
figure set is ~all Beloved Legends and almost never Infamous Villains, and how to
fix it at the root instead of with a relative-threshold band-aid.

## Background: the two axes

A figure's quadrant is (renown ≥ high_cut) × (regard vs warm threshold):

| | warm | hostile |
|---|---|---|
| **high renown** | Beloved Legend | Infamous Villain |
| low renown | Up-and-comer | Disliked Nobody |

- **Renown** (the "how prominent" axis) — fixed: top-decile percentile.
- **Regard** = `regard_likability + 0.5·regard_respect − regard_heat`, computed in
  `renown_field_repository.build_inputs` as the **mean over an entity's inbound
  relationship edges** (how *others* feel about it). Warm threshold is a fixed
  `REGARD_WARM_THRESHOLD = 0.05`.

## The problem (measured, not assumed)

On the live field, regard is warm-skewed: **86% warm**, field-median regard
**+0.18**, vs the fixed +0.05 cut. So the figure set is ~7 Legends : 1 Villain.

### Why a relative threshold is the wrong fix
Making the warm/hostile split field-relative (e.g. hostile = bottom-X% regard)
*guarantees* a Villain class, but it's a measurement band-aid: it crowns villains
even when the world has no real rivalries. Rejected in favour of generating real
negative regard.

### Why "just generate more negativity" is necessary but not sufficient
The negative-interaction machinery already exists (`relationship_events.py`:
BAD_BEAT +0.30 heat, BLUFFED_OFF +0.20, COOLER/TRASH_TALK +0.10; `chat_intent.
map_tone` bridges table-talk tone → these events). Two experiments
(`scripts/exp_renown_differentiation.py`, run 2026-06-02) established:

1. **The relationship-evolution wiring works** (it writes heat: +19 total heat per
   30 sim ticks).
2. **Over 1000 ticks it does produce earned Villains** — `warm%` 86→73, hostile
   count 11→32, and two renowned figures (`marie_antoinette`, `the_rock`) crossed
   into Infamous Villain through accumulated off-screen heat.
3. **But weakly**: those villains land at regard +0.01 / +0.03 — right on the line,
   not deeply reviled, after 1000 *dense* ticks. `rg_min` stayed frozen at −0.38
   and `rg_max` barely moved: the shift is **diffuse, not concentrated**.

The diagnosis: **regard is a mean over ~4,500 global, historical, non-sandbox-
scoped inbound edges**, so every fresh heat event is diluted ~1/4500. The
bottleneck is the denominator, not the threshold or the event magnitudes.

## Prerequisite (gating, not optional)

**The AI↔AI relationship-evolution wiring is not on `development`.** It is
uncommitted on `release-candidate` (6 files: `cash_mode/full_sim.py`,
`cash_mode/lobby.py`, `poker/memory/{hand_history,hand_outcome_detector,
memory_manager,relationship_events}.py`). Without it there is **zero** off-screen
heat and none of the work below matters. The wiring includes a real fix worth
keeping: `db_path_for_memory` was reading a non-existent `_db_path` attr (always
None), which silently disabled both relationship-simming and pre-existing
opponent-model persistence.

**P0. Land the relationship-evolution wiring on `development`** (merge/cherry-pick
from `release-candidate`; it 3-way-applies cleanly onto current `development`).
Acceptance: a lobby sim moves `relationship_states` heat (verified) without LLM
calls in the detection path.

## The fix: repair the regard denominator

Both changes live in `renown_field_repository.build_inputs` (the inbound-regard
read) and are independent of the wiring once P0 lands.

### P1. Sandbox-scope the inbound regard read
Today: `SELECT opponent_id, likability, respect, heat FROM relationship_states`
(no sandbox filter — every sandbox's edges average together).

Change: scope to the sandbox under evaluation so a sandbox's own dynamics aren't
drowned by the global historical set. Mirror the keying already used for
`cash_scalps`/`cash_pair_stats`. Caveat to resolve: `relationship_states` may not
carry a `sandbox_id` column — if not, scope via the observer/opponent membership
in the sandbox's entity set (already computed in `build_inputs`), or add the
column (schema bump). Decide during P1.

Acceptance: regard for an entity reflects only in-sandbox relationships; the
global mean no longer damps a single sandbox's rivalries.

### P2. Recency-weight the regard mean
Today: flat mean over all inbound edges, so a years-old neutral edge counts the
same as a fresh BAD_BEAT.

Change: weight each edge by recency (exponential decay on `last_seen`/`updated_at`,
or a rolling window). Fresh rivalries then dominate the mean, so a sustained
off-screen feud can drive a figure decisively hostile within a season rather than
nudging it 0.04 over 1000 ticks. Reuse the existing relationship-decay projection
if one is already applied at read time (`load_relationship_state` may already
decay — check and avoid double-decay).

Acceptance (re-run `exp_renown_differentiation.py` with P0–P2): at 1000 ticks,
emergent Villains sit at clearly-negative regard (target ≤ −0.10, not +0.02), and
`rg_min`/`rg_max` move (the shift is concentrated, not diffuse).

### P3 (optional, content lever, not a balance fix)
Let hostile-disposition AIs choose sharper table-talk (ties into the
`temperament` work: `_classify_social_disposition`, `mirror_shift_override`), so
`map_tone` produces more heat from genuinely needling personas. This makes the
villainy *narratively earned* and *concentrated* on specific rivals. Sequence
after P1/P2 so the denominator can actually register it.

## Explicitly out of scope
- **Field-relative warm/hostile threshold** — rejected (band-aid). The fixed
  `REGARD_WARM_THRESHOLD = 0.05` is correct *once the denominator is fixed*; revisit
  only if P1/P2 prove insufficient.
- Changing event magnitudes in `relationship_events.py` — the experiment shows the
  deltas are fine; dilution is the problem.

## Validation harness

`scripts/exp_renown_differentiation.py` (gitignored; force-add if kept) already
measures the right things: `warm%`, hostile count, `rg_min/med/max`, legends,
villains over sim ticks, on an isolated DB copy with LLM narration stubbed. Re-run
it after each phase. Note: each scratch copy is ~5 GB — delete after capturing
results.

## Open questions
- Does `relationship_states` have a usable sandbox key, or does P1 need a schema
  bump? (Resolve first — it gates P1's approach.)
- Is read-time decay already applied (`load_relationship_state`)? If so, P2 is a
  tuning change, not a new mechanism.
- Should the human be eligible for Villain regard symmetrically, or is that a
  separate UX decision?
