---
purpose: Plan to persist and consume per-AI Renown-v2 (the deferred "wire up the AI" stage), using Option 1 (extend prestige_snapshots) with the keying decision and gated build sequence.
type: guide
created: 2026-06-02
last_updated: 2026-06-02
---

# Renown-v2 — AI Wiring Plan

Follow-on to `RENOWN_V2_HANDOFF.md`. The human-only v2 slice is built and merged
into `development` (schema **v138**, flag `RENOWN_V2_ENABLED` default-OFF). This
doc plans the deferred stage: give **AI entities** a persisted, field-relative
renown, and decide what consumes it. Spec: `CASH_MODE_PLAYER_PRESTIGE.md`
(Renown v2). Honest narrative: `docs/captains-log/renown/`.

> **Baseline / branch note.** The numbers below assume the post-merge
> `development` lineage (`SCHEMA_VERSION = 138`). The new migration is therefore
> **v139**. Do this work on a branch off the *merged* `development`, not the
> pre-merge `renown` worktree (which is still at v133). If you continue on
> `renown`, rebase/merge `development` first so the migration lands as v139.

---

## Status (2026-06-02)

- **Stage A is BUILT + tested + committed** (`cab24b0d`, branch `renown` at the
  merged v138 baseline; schema bumped to **v139**). 45 green; flag
  `RENOWN_V2_PERSIST_AI` default **OFF**, zero live behavior change.
- **Stress gate RUN** (live 81-entity field, `guest_jeff` sandbox
  `4db9b9f2-…`). Verdict and the bottleneck it exposed are below under A4.
- **`build_inputs` OPTIMIZED** (`4ff4b087`): the ~523ms field read the gate
  exposed is now ~185ms (SQL-aggregate the holdings read + v140 covering index).
  Parity preserved. Details under A4.

## TL;DR

- **The hard part is already done.** The ticker's `_maybe_v2_overlay`
  (`flask_app/services/ticker_service.py:386`) already calls `score_renown_field`
  over the **whole field** every recompute (~480ms, throttled to 300s per
  sandbox) — then keeps `scored.get(owner_id)` (the human) and **throws every AI
  row away** (`ticker_service.py:422`). The AI math runs every cycle and is
  discarded. Persisting it is a *write fan-out*, not new computation.
- **Stage A (infrastructure):** give `prestige_snapshots` an entity identity and
  persist the AI rows the field scorer already produces. Zero behavior change —
  it only makes data exist.
- **Stage B (consumption):** is a set of **new surfaces**, each its own product
  decision and flag. See the premise correction below — the existing 4 hooks do
  *not* light up for free.

## Premise correction (read this before scoping Stage C)

The handoff framed the last stage as *"the 4 reputation hooks read the persisted
per-entity quadrant."* That is **not how the hooks work.** All four consume the
**human's** quadrant/regard to modulate AI behavior — the AIs are *recipients* of
the human's fame, not sources of their own:

| # | Hook | File | Reads |
|---|------|------|-------|
| 1 | Table pull / rival-draw | `game_handler.py:854` `_reputation_order_refill_pool` | human `quadrant` + AIs' relationship edge **toward the human** |
| 2 | Backing economy gate | `cash_routes.py:5236` `_resolve_human_regard` → `sponsor_offers.py:309` | human `regard` float (`VILLAIN_REGARD_FLOOR`) |
| 3 | Chat tone | `game_handler.py:4185` `_resolve_human_reputation_tone` | human `quadrant` → prompt suffix |
| 4 | AI demeanor (psych) | `game_handler.py:903` `_apply_reputation_demeanor` (flag `REPUTATION_DEMEANOR_ENABLED`) | human `quadrant` → seated-AI psychology stimulus |

So persisting AI renown changes **none** of these by default. The flag flip (C)
for the human is already shipped. "Wiring up the AI" therefore = **A (persist)**
+ **B (new consumers we choose to build)**. Don't expect the existing hooks to
start using AI renown unless we *add* that (Hook 1 prestige-seeking is the one
natural extension — see Stage B4).

---

## The keying decision — Option 1, variant "subject-id"

**Chosen:** extend `prestige_snapshots` rather than a parallel
`ai_prestige_snapshots` table (one queryable field, one ratchet path, one prune).

Today the table is keyed `(sandbox_id, owner_id)` and `owner_id` is *always* the
human. Every read does `WHERE sandbox_id=? AND owner_id=?`
(`prestige_snapshots_repository.py` lines 121/143/169/193; consumers at
`game_handler.py:885,937,4216`, `cash_routes.py:5250,5913`). Meanwhile the field
scorer, `RenownFieldRepository`, and `build_renown_inputs_from_repos` are already
**AI-symmetric** and identify every entity by a **raw id** (the human's
`owner_id` or an AI's `personality_id`, no `ai:`/`player:` prefix —
`renown_field_repository.py:47`). The human works in `scored.get(owner_id)`
precisely because `owner_id` is itself a raw id in the field dict.

### Decision: treat `owner_id` as the universal subject id + add `entity_kind`

- `owner_id` keeps meaning **"who this row is about"** — unchanged for the human;
  for an AI it holds the **`personality_id`** (a raw id, same scheme the field
  scorer already uses, so `scored.items()` keys drop straight in).
- Add one column: **`entity_kind TEXT NOT NULL DEFAULT 'player'`** — `'player'`
  or `'ai'`. Existing rows default to `'player'`; no backfill needed.

**Why this variant (and not a separate `entity_id` + `owner_id`=sandbox-owner):**

- **Smallest blast radius.** Every existing `WHERE sandbox_id=? AND owner_id=?`
  read keeps working untouched — the human's `personality`-free id never
  collides with a slug like `napoleon`, and `sandbox_id` already scopes the
  world. We add `entity_kind` for *explicit* filtering ("all AIs in this
  sandbox") and to make intent legible, not because the queries need it for
  correctness.
- **Matches the codebase idiom.** It mirrors how v2 itself shipped: additive,
  PRAGMA-guarded `ADD COLUMN`, optional repo kwargs that default to the old
  behavior (the `cash_scalps` table is likewise raw-id, entity-symmetric).
- **One scoreboard.** Marquee/leaderboard reads ("top renown in this sandbox")
  are a single `ORDER BY renown_v2 DESC` over one table.

**Rejected — separate `entity_id` + repurpose `owner_id` as the sandbox owner:**
semantically purer but forces a signature + `WHERE`-clause change at all 5 read
sites and a data backfill, for no functional gain (sandbox ownership is already
carried by `sandbox_id`). **Rejected — parallel `ai_prestige_snapshots`:**
duplicates the ratchet, prune, and every read; kills the single-leaderboard win.

> **Guardrail (correctness).** If a future change ever sets an AI row's
> `owner_id` to the *sandbox owner* instead of the personality id, the human's
> `load_latest` would start matching AI rows. The invariant is: **`owner_id` =
> the subject, and `entity_kind` disambiguates.** Encode it in the repo (assert
> kind on the AI write paths) and in a test.

---

## Stage A — persist per-AI renown (infrastructure, zero behavior change)

Gated behind a **new sub-flag** so the write fan-out is independently
killable from the human gauge:
`RENOWN_V2_PERSIST_AI = _env_flag("RENOWN_V2_PERSIST_AI", False)` in
`cash_mode/economy_flags.py`. (It also implies `RENOWN_V2_ENABLED`, since the
overlay only runs when that's on.)

### A1 — Schema migration v139 (additive)

`poker/repositories/schema_manager.py`, following the v138 idiom exactly:

1. Bump `SCHEMA_VERSION = 139`.
2. Add `_migrate_v139_add_prestige_entity_kind(self, conn)` using the
   PRAGMA-guarded `ADD COLUMN` loop (see `_migrate_v138_add_prestige_v2_columns`):
   - `("entity_kind", "TEXT", "NOT NULL DEFAULT 'player'")`
3. Register `139: (self._migrate_v139_add_prestige_entity_kind, "…")` in the
   migrations dict.
4. **Index:** add `idx_prestige_snap_kind` on
   `(sandbox_id, entity_kind, renown_v2)` for the leaderboard/marquee read, *or*
   extend the existing `idx_prestige_snap_scope` — decide when B's read shape is
   fixed. The current `(sandbox_id, owner_id, captured_at)` index already serves
   the per-entity `load_latest`.

Non-destructive, idempotent, safe on the already-migrated live DB (the column is
absent there today, so it applies cleanly). **Do not** renumber below 138.

### A2 — Repository changes (`prestige_snapshots_repository.py`)

All back-compat (default `entity_kind='player'` everywhere):

- `record(..., entity_kind: str = 'player')` — write the column.
- `load_latest`, `load_renown_peak`, `load_renown_v2_peak`, `series_since` —
  accept optional `entity_kind='player'`; add it to the `WHERE`. (Human callers
  unchanged.)
- **New batched reads** for the fan-out (avoid N queries/cycle):
  - `load_renown_v2_peaks(sandbox_id) -> {owner_id: peak}` —
    `SELECT owner_id, MAX(renown_v2) … WHERE sandbox_id=? AND entity_kind='ai' GROUP BY owner_id`.
  - `record_many(rows)` — one transaction (mirror
    `CashScalpsRepository.record_many`).
  - Optional `top_by_renown_v2(sandbox_id, limit, entity_kind='ai')` for B's
    leaderboard.
- `prune()` stays global-by-`captured_at` — already entity-agnostic; just note
  the row count grows ~N× (60-day window × field size). Consider a tighter AI
  retention or "latest-only for AIs" if growth bites (measure first).

### A3 — Ticker write fan-out (`ticker_service.py`)

In `_maybe_v2_overlay` / `_maybe_recompute_prestige`, **after** the existing
human persist, when `RENOWN_V2_PERSIST_AI`:

1. We already have `scored = score_renown_field(field, weights)` (whole field) and
   `field` for the relative cut.
2. One batched `peaks = load_renown_v2_peaks(sandbox_id)`.
3. Build one row per **AI** entity (`eid != owner_id`):
   `owner_id=eid`, `entity_kind='ai'`, `formula_version='v2'`,
   `quadrant=quadrant_label_relative(fr, field)`,
   `renown_v2=max(fr.renown_total, peaks.get(eid, 0))` (own-scale ratchet),
   `victim_percentile`, `high_cut`, `renown_v2_components`, `field_size`.
   Leave the v1 `renown`/`regard`/v1-components at 0/NULL — **AI rows are
   v2-native**; consumers read `quadrant` + `renown_v2`.
4. One `record_many(rows)`.

Cost: the field build + score already happen; the *added* cost is one GROUP-BY
read + one batched insert of ≤N rows, every 300s per sandbox. Keep it inside the
existing throttle, **after** the human row, and wrap best-effort (a fan-out
failure must not lose the human row — match the current `try/except → v1
fallback` posture).

### A4 — Validation (the gate)

1. **Parity unchanged.** `scripts/renown_field_parity.py` still PASSes
   (prod loader == oracle) — A doesn't touch the math.
2. **New repo unit tests** (`test_prestige_snapshots_repository.py`):
   `record`/`load_latest` for an AI `entity_kind='ai'` row; **isolation** — a
   human `load_latest(sandbox, owner)` does **not** return AI rows and vice
   versa; `load_renown_v2_peaks` batched ratchet; `record_many`.
3. **New wiring test** (`test_renown_v2_wiring.py`): with `RENOWN_V2_PERSIST_AI`
   on, a seeded field persists one AI row per field AI with the relative
   quadrant; off → only the human row (regression).
4. **Stress gate — RUN 2026-06-02 (live 81-entity field).** Measured against the
   real `guest_jeff` sandbox via a directly-instantiated `RenownFieldRepository`
   (read path) + my v139 repo on a temp DB (write path), 5–8 iterations each:

   | Stage | Median | Max | Notes |
   |---|---:|---:|---|
   | `build_inputs` (read) | **523ms** | 650ms | the field read — **pre-existing** |
   | `score_renown_field` | 2.5ms | 4.3ms | pure |
   | build AI rows (pure) | 0.5ms | 0.6ms | the fan-out construction |
   | `load_renown_v2_peaks` | 0.3ms | 2.5ms | one GROUP-BY, 80 AIs |
   | `record_ai_many` (80 rows) | 1.5ms | 3.8ms | one batched insert; 640-row history |

   **Verdict — fan-out PASSES, but the gate exposed a pre-existing bottleneck:**
   - **Stage A's marginal cost ≈ 2.3ms** (build-rows 0.5 + peaks 0.3 + write
     1.5). Negligible; the per-AI write fan-out is **not** a budget risk. WAL
     one-writer + 5s `busy_timeout` + a single 1.5ms transaction → DB-lock-under-
     burst is implausible (the 300s throttle means ~one write/5min/sandbox).
   - **`build_inputs` ≈ 523ms ALREADY exceeds `CYCLE_BUDGET_MS=250ms`**
     (`ticker_service.py:42`). This is **pre-existing**: the *human-only* overlay
     (`_maybe_v2_overlay`) calls the same `build_inputs`, so the cost is shipped
     today, latent behind the default-OFF `RENOWN_V2_ENABLED`. Stage A does not
     introduce it.
   - **Impact is bounded, not catastrophic.** The 250ms budget is a *soft
     early-break* between sandboxes in the 2s cycle (`_run_cycle`), not a hard
     timeout — a recompute that runs 527ms just defers the cycle's *remaining*
     sandboxes to the next 2s tick. With the 300s recompute throttle that's ~once
     per 5min per sandbox. It backs up only if many active sandboxes recompute in
     the same window.

   **Gate conclusion: the AI fan-out is safe to enable independently. The
   ~523ms `build_inputs` it exposed is now OPTIMIZED to ~185ms** (`4ff4b087`):
   - **SQL-aggregate the holdings read** (the field's largest table). It fetched
     all 87K snapshot rows and derived three aggregates in a Python loop;
     now peak + presence are a GROUP BY and time-at-#1 is a per-tick rank window
     — ~one row per entity + one per tick transferred. **523ms → 368ms**
     (measured live). Byte-parity preserved (`renown_field_parity.py` PASS, 0
     mismatches): peak floored at 0.0 to match the old loop, window tie-break
     `net_worth DESC, entity_id ASC` reproduces the old scan-order first-wins, 0
     per-tick ties on the real field.
   - **v140 covering index** `holdings_snapshots(sandbox_id, entity_id,
     net_worth)` so `MAX(net_worth) GROUP BY entity_id` is index-only. On the
     live DB the non-covering MAX cost ~200ms (a table lookup per row); the
     covering sibling `COUNT(DISTINCT captured_at)` over the same rows is ~18ms,
     so covering drops peak ~200ms→~15ms → build ≈ **~185ms**, under the 250ms
     cycle budget. (The end-to-end live number lands once the migration runs on
     the live DB; a fresh-copy timing isn't faithful — warm sequential pages
     don't reproduce the live DB's cold scattered ones, which is the exact cost
     the index removes. Evidence: the covering plan + the same-DB
     presence-vs-peak control.)

   Remaining holdings cost is the time-at-#1 window (~91ms); a second covering
   index `(sandbox_id, captured_at, net_worth, entity_id)` would make it
   index-only too, but ~185ms already clears the budget so it's not worth a
   second index on a ticker-written table. If the write side ever gets tight:
   cap to top-K AIs by renown, or stagger AI persistence beyond the human's 300s.

---

## Stage B — consumption surfaces (each independent, each its own decision)

None of these are required by A; pick per product value. Each is read-only
display unless noted, and each should ship behind its own flag.

- **B1 — Dossier quadrant badge.** Surface an AI's persisted `quadrant` +
  `renown_v2` on the character dossier (the natural home; today it carries zero
  renown). Read `load_latest(sandbox, personality_id, entity_kind='ai')`.
  *Lowest-risk, highest-legibility first surface.*
- **B2 — Lobby seat / table marquee.** Add `reputation_quadrant` (+ maybe a
  `renown_v2` rank) to the AI seat payload (`cash_routes.py` ~5596–5673) so a
  Beloved Legend / Infamous Villain at a table renders a badge and makes that
  table draw humans (the "marquee table" idea from
  `CASH_MODE_TABLE_ATTRACTIVENESS.md`).
- **B3 — Whereabouts.** Augment the off-table cards
  (`cash_mode/whereabouts.py`) with the AI's quadrant ("Deadpool — Infamous
  Villain, 47 renown").
- **B4 — Prestige-seeking behavior (the one behavioral extension).** Extend
  Hook 1 (`_reputation_order_refill_pool`) and/or movement so high-renown AIs
  gravitate toward marquee tables / rivals. **This is a real chip-flow &
  movement change → sim-validate**, separate flag, its own mini-plan. Don't fold
  it into A.

---

## Risks & gotchas

- **The owner_id-as-subject invariant** (above) — the one correctness trap. Test
  human↔AI isolation explicitly.
- **Row growth.** N× snapshots in the 60-day prune window. Measure; consider AI
  latest-only or shorter retention.
- **Write contention.** A single batched `record_many` per recompute is the only
  new write; WAL + 5s busy_timeout should absorb it, but the stress gate is
  non-negotiable before a real field.
- **Identity scheme drift.** Field ids are *raw* (prefix-stripped from
  `holdings_snapshots`' `ai:`/`player:`). Persist the **raw** id as `owner_id`;
  never the prefixed form (it would silently fail every join). See the handoff's
  prefix gotcha.
- **AI rows are v2-only.** `compute_prestige` (v1) is never run per-AI; AI rows
  have NULL v1 columns. Any consumer must read `quadrant`/`renown_v2`, not the v1
  `renown` fill. The lobby/`ReputationPanel` v1 path must never be handed an AI
  row.
- **Verify before "needs live data."** Per the handoff, the read-side already had
  proxies for everything; the AI math is already computed in-cycle. Confirm
  timing on a captured field before assuming you must run live.

---

## Open decisions (for you)

1. **Sub-flag name / coupling** — `RENOWN_V2_PERSIST_AI` (recommended) vs reuse
   `RENOWN_V2_ENABLED`. Recommend the sub-flag for an independent kill switch.
2. **Persist scope** — all field AIs, or top-K by renown only (cheaper, smaller
   table, but the leaderboard tail is missing). Recommend: all, revisit if growth
   bites.
3. **AI persist cadence** — same 300s as the human, or slower. Recommend: same,
   simplest; slow it only if the stress gate is tight.
4. **First B surface** — recommend **B1 dossier badge** (read-only, validates the
   data end-to-end with no chip-flow risk) before anything behavioral (B4).
5. **Do we want B4 at all this pass**, or is "AI fame exists + is visible" the
   deliverable, with behavior deferred?

## Sequencing

```
A1 schema v139 ─▶ A2 repo ─▶ A3 ticker fan-out ─▶ A4 validate+stress  (GATE)
                                                          │
                                                          ▼  enable RENOWN_V2_PERSIST_AI on dev
                                          B1 dossier badge (read-only, ship first)
                                          B2 marquee / B3 whereabouts (read-only)
                                          B4 prestige-seeking (sim-gated, separate)
```

Keep a dated `docs/captains-log/renown/` entry as you go (wrong turns included).
