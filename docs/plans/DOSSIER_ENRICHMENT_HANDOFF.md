---
purpose: Handoff for the next batch of opponent-dossier work — the file cabinet (Phase 4) plus the Tier-2 intel enrichments — with grounding, decided design, and concrete build steps
type: guide
created: 2026-05-29
last_updated: 2026-05-29
---

# Dossier Enrichment Handoff (file cabinet + Tier 2)

Picks up where the scouting meta-game left off. **Phases 1–4 + durable
pressure/memorable + the Intel hub + B1 (deep postflop reads) are shipped**
(as of 2026-05-29); the next phase is **Part B2 — exploit hints**, below.
Parent vision + decisions:
`docs/plans/OPPONENT_DOSSIER_PROGRESSION.md`. Narrative log (incl. the
wrong-turns): `docs/captains-log/dossiers/opponent-dossier-progression.md`.

> **NOTE for the next context:** Part A (the file cabinet) is **DONE** — it
> shipped as the **Intel hub** ("The Field Office": `IntelHub.tsx` with three
> tabs — **The Wire** / **The Floor** / **The Files**). Skip Part A; start at
> **Part B / B1**. The file-cabinet roster you'll touch for B1 is
> `flask_app/services/file_cabinet.py` + `FileCabinetPanel.tsx`.

## What already exists (read this first)

The dossier is a **persistent, per-sandbox scouting meta-game**:

- **Lifetime observation store** — `opponent_observation_lifetime` (schema
  v123), cumulative behavioral COUNTS per `(sandbox_id, observer_id,
  opponent_id)`, folded each hand-boundary via
  `GameRepository.fold_observations_into_lifetime` (delta-fold, resume-safe,
  high-water mark on `opponent_models.lifetime_applied_json`). Read via
  `load_observation_lifetime`. Rates derived through the canonical
  `OpponentTendencies._recalculate_stats` (no duplication).
- **Pressure + memorable** are durable too, but via *aggregate-on-read scoped
  by owner* (not the fold): `PressureEventRepository.get_player_events_for_owner`
  + `GameRepository.load_lifetime_memorable_hands`.
- **The grind gate** — `flask_app/services/dossier_scouting.py`
  (`SCOUTING_SCHEDULE`, `compute_scouting`, `apply_scouting_gate`). Server
  strips locked reads; floor 25; item drip 25→180 hands. Behind kill switch
  `economy_flags.DOSSIER_SCOUTING_GATE_ENABLED`. Circuit-only (gates only
  when a sandbox+observer exist).
- **The informant** — `dossier_informant_unlocks` (schema v124),
  `INFORMANT_SECTIONS`, `POST /api/character/<id>/informant` (debits bankroll
  → bank pool via `informant_unlock` ledger reason). Purchasing is gated to a
  Circuit context via the `circuitContext` prop on `CharacterDetailCard`.
- **Dossier endpoint** — `GET /api/character/<id>/dossier`
  (`flask_app/routes/character_routes.py:get_dossier`). Returns `scouting`
  (gate state + `informant_offers`) and `player_bankroll` (so the informant
  UI disables unaffordable unlocks). **UI** —
  `react/react/src/components/character/CharacterDetailCard.tsx` (+ `api.ts`,
  `CharacterDetailCard.css`); the scouting/informant UI is `ScoutingStrip`.
- **Intel hub (Part A, DONE)** — `IntelHub.tsx` ("The Field Office", archive
  aesthetic): **The Wire** (activity feed), **The Floor** (whereabouts),
  **The Files** (the dossier roster). Roster aggregator
  `flask_app/services/file_cabinet.py` (`build_file_cabinet`) →
  `GET /api/cash/file-cabinet`; rendered by `FileCabinetPanel.tsx` (search +
  reversible sorts incl. Name; excludes the viewer's own account). Opened
  from the lobby's "Intel" trigger folded into the wire's title bar; a dossier
  opens on top (z-index 1600) and the cabinet refreshes on purchase via
  `onIntelChanged`.

### Conventions to follow (learned the hard way)
- **Migrations**: bump `SCHEMA_VERSION` in `schema_manager.py`, add a
  `_migrate_vN_*` method + dict entry + changelog comment. New tables go in
  the migration only (fresh DBs run the chain). Add the method BEFORE the
  dict entry so the auto-reloader never sees a dangling reference.
- **Tests run in Docker** on non-default ports here (backend 5001, frontend
  5175): `docker compose exec -T backend python -m pytest <path> -p no:warnings`.
  `docker compose exec -T frontend npx tsc --noEmit` for types.
- **Don't fold-then-overlay carelessly** — the observation fold double-counted
  because `save_opponent_models` delete+reinserts and wiped the high-water
  mark; the unit test missed it because it never simulated a save between
  folds. Prefer aggregate-on-read when the source events already exist.
- **Gate leaks via static props** — `CharacterDetailCard.merged` falls back to
  the static `character.*` prop when a fetched field is null; in a gated
  context a null means "classified", so don't fall back. (Fixed for
  observation; watch for it on any new gated field.)
- **Scope**: new per-pair intel keys on `(sandbox_id, observer_id,
  opponent_id)`. Pressure/memorable are currently owner-scoped (≈ sandbox
  under 1:1) — fine for v1, flagged for multi-sandbox.

---

## Part A — The File Cabinet ✅ DONE

Shipped as the **Intel hub** (see "What already exists" above). Don't rebuild;
the next context starts at Part B.

---

## Part B — Tier 2 intel enrichments  ← **START HERE**

The dossier already has the data for most of these — it's mostly surfacing
work. Each becomes new grind tiers / reads.

### B1. Deep postflop reads as new grind tiers ✅ DONE (2026-05-29)

Shipped: migration **v125** added the deep count/sum columns to
`opponent_observation_lifetime`; the fold (`_LIFETIME_COUNT_FIELDS` +
new `_LIFETIME_SUM_FIELDS`) and `load_observation_lifetime` now build their
SQL from those maps so future fields are picked up automatically;
`_deeper_reads_from_lifetime` in `character_routes.py` derives the reads and
the route attaches a `deeper_reads` block; 6 new `SCOUTING_SCHEDULE` tiers
(fold-to-cbet @220 → polarization @480) with `_DEEPER_FIELDS` redaction + a
`deep_reads` informant section ($1500); a gated **DEEP READ** section renders
in `CharacterDetailCard.tsx` (type `DossierDeeperReads` in `api.ts`). Tests in
`test_observation_lifetime.py` + `test_dossier_scouting.py`.

> **Gotcha the original plan missed:** the equity polarization MEANS
> (`equity_when_betting/raising/calling`) are NOT recomputed by
> `_recalculate_stats()` — the live path updates them incrementally in
> `record_equity_at_action`. So `_deeper_reads_from_lifetime` derives them
> manually as `sum / count` from the stored `_equity_*_sum`/`_count` pairs.
> Everything else (fold-to-cbet, c-bet, barrel, all-in, postflop AF) DOES come
> out of `_recalculate_stats()` as the plan described.

> **Follow-up under investigation:** gating the deep tiers on *opportunity
> counts* (e.g. fold-to-cbet unlocks at `cbet_faced_count >= K`) instead of
> raw `hands_observed`, so the unlock is an honest 1:1 with what was actually
> witnessed (a 300-hand nit may give only 3 c-bet samples). The opportunity
> denominators are already stored as of v125. Hand-count gating shipped first.

**ORIGINAL PLAN (kept for reference):**

The idea: deep postflop reads as new grind tiers.

**The idea:** the lifetime store currently keeps only the headline counts
(VPIP/PFR/AF/showdown). `OpponentTendencies` computes far more and already
serializes it to `opponent_models.tendencies_json` (see `to_dict` in
`poker/memory/opponent_model.py`). Promote those into the lifetime store so
they accumulate cross-game, then expose them as new grind tiers — fold-to-cbet,
c-bet %, barrel/double-barrel, all-in frequency, postflop aggression, and the
polarization (equity-at-action) reads. This is what gives the 100–500-hand
range something to unlock and makes a maxed dossier genuinely deep.

**The build (in order):**

1. **Schema migration (v125):** add the new count columns to
   `opponent_observation_lifetime`. ⚠ For each derived *rate* you must store
   **both numerator AND denominator** counts, because the read reconstructs an
   `OpponentTendencies` and calls `_recalculate_stats()` to derive the rate
   (this is how we avoid duplicating formulas). The pairs:
   - fold-to-cbet → `_fold_to_cbet_count` / `_cbet_faced_count`
   - c-bet attempt → `_cbet_attempt_count` / `_postflop_seen_as_pfr_count`
   - barrel → `_barrel_count` / `_barrel_opportunity_count`
   - 3rd barrel → `_third_barrel_count` / `_third_barrel_opportunity_count`
   - all-in freq → `_all_in_count` (denominator already there: hands_dealt)
   - postflop AF → `_postflop_bet_raise_count` / `_postflop_call_count`
   - polarization → `_equity_betting_sum`/`_count`,
     `_equity_raising_sum`/`_count`, `_equity_calling_sum`/`_count`
   (Confirm the exact key names against the current `to_dict` before writing
   the migration — they're the source of truth.)
2. **Fold:** add every new key to `_LIFETIME_COUNT_FIELDS`
   (`game_repository.py`) — it's a flat `{tendencies_key: column}` map, so the
   delta-fold picks them up automatically. (Sums work for all of these,
   including the equity `_sum` accumulators.)
3. **Read:** add the columns to `load_observation_lifetime`'s SELECT + return.
4. **Derive:** in `character_routes._observation_from_lifetime`, set the new
   `_*` fields on the reconstructed `OpponentTendencies` before
   `_recalculate_stats()` — then `t.fold_to_cbet`, `t.cbet_attempt_rate`,
   `t.barrel_frequency`, etc. come out correct, matching the live path.
   Surface them in the response (extend the observation block or add a
   `deeper_reads` block).
5. **Gate + grind tiers:** add new item ids to `SCOUTING_SCHEDULE`
   (`dossier_scouting.py`) past 180 (e.g. fold-to-cbet @120, c-bet @150,
   barrel @220, polarization @300) and a `_redact_item` case for each. Note:
   `reads_total` is `len(SCOUTING_SCHEDULE)`, so the gate progress, the
   dossier "X/N pages", and the cabinet unlock % **all auto-scale** when you
   add tiers — no other change needed there.
6. **Informant:** optionally add an `INFORMANT_SECTIONS` entry bundling the
   new deep reads (so they're buyable).
7. **Frontend:** render the new reads in `ScoutingStrip` / the dossier's
   observation/TABLE-POSTURE section (gated rows; null when locked).
8. **Tests:** mirror `tests/test_repositories/test_observation_lifetime.py`
   (fold lossless-merge for the new counts) + `tests/test_dossier_scouting*`
   (gate redaction for the new item ids).

### B2. Exploit hints ("the read")
Turn stats into one-line advice: *"folds to c-bets 62% — barrel relentlessly"*,
*"stations the river — value-bet thin, don't bluff."* A pure function over the
(unlocked) tendencies → a short string, surfaced as a gated bit. The doc lists
"a specific exploit line" as an intended unlock. There's an exploitation/
deviation layer (`poker/strategy/`, deviation profiles) whose thresholds can
inform the phrasing, but a standalone rules-on-stats function is enough for v1.

### B3. Trend / tilt indicator
`OpponentTendencies.recent_trend` and the pressure `tilt_score` already exist.
Surface *"VPIP climbing — they're steaming"* / a tilt gauge. Mostly display.

### B4. Field-relative percentiles
*"Looser than 80% of the field."* Anchor a stat against the population:
`experiments/profile_population.py` + `llm_field.csv` (see
`project_archetype_exploitation_goNoGo` in memory) characterize the real LLM
field's VPIP/AF/FTC distribution. Precompute percentile buckets; show the
opponent's standing. Contextualizes the raw numbers.

### Also parked (bigger, separate efforts)
- **Archetype badge** (fish/whale/regular) as a gated bit — needs a
  classification source wired from the whale/fish work
  (`CASH_MODE_WHALE_AT_CARDROOM.md`, `CASH_MODE_FISH_AS_PERSONAS.md`).
- **Tells system** (`TELLS_SYSTEM.md`).
- **Relationship-history hands** — surface the rivalry-defining coolers
  (`relationship_events`).
- **Perks / "Data Collector"** passive scouting (parent doc "Future").

---

## Suggested order
1. ~~File cabinet (Part A)~~ — ✅ done (the Intel hub).
2. ~~**B1 deep reads**~~ — ✅ done (2026-05-29, schema v125).
3. **B2 exploit hints** ← **next** — high flavor, small.
4. **B3 / B4** — polish.

## Open tuning knobs (not blocking)
- Per-item grind thresholds + informant prices (flat first-pass today).
- Partial-section informant pricing (full price for partial progress today).
- Pressure/memorable true per-sandbox scoping (owner-scoped today).
