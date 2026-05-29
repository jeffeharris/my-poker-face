---
purpose: Handoff for the next batch of opponent-dossier work — the file cabinet (Phase 4) plus the Tier-2 intel enrichments — with grounding, decided design, and concrete build steps
type: guide
created: 2026-05-29
last_updated: 2026-05-29
---

# Dossier Enrichment Handoff (file cabinet + Tier 2)

Picks up where the scouting meta-game left off. **Phases 1–3 are shipped and
committed on branch `dossiers`** (pushed to `origin/dossiers`); this doc is
the next context's worklist. Parent vision + decisions:
`docs/plans/OPPONENT_DOSSIER_PROGRESSION.md`. Narrative log (incl. the
wrong-turns):`docs/captains-log/dossiers/opponent-dossier-progression.md`.

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
  (`flask_app/routes/character_routes.py:get_dossier`). **UI** —
  `react/react/src/components/character/CharacterDetailCard.tsx` (+ `api.ts`,
  `CharacterDetailCard.css`); the scouting/informant UI is `ScoutingStrip`.

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

## Part A — The File Cabinet (Phase 4)

A browsable index of **everyone you've met in the sandbox**, each row opening
their dossier. The retention/collection surface. Cash-mode (Circuit) scope.

**Backend**
- New endpoint, e.g. `GET /api/cash/met-opponents` (cash_routes), returns the
  observer's roster: for each opponent met, their personality basics +
  current emotion + a few headline stats for sorting (hands observed, lifetime
  PnL, heat, dossier-unlocked %).
- Data source: `relationship_repo.load_all_relationships(observer_id)` is the
  "everyone I've met" spine (global today; becomes per-sandbox if/when the
  deferred `relationship_states` migration lands). Cross-reference
  `cash_pair_stats` (`list_cash_pair_stats_for_observer`) and the lifetime
  observation store for sort keys. The whereabouts feature
  (`cash_mode/whereabouts.py`, `WhereaboutsDrawer.tsx`) is the closest
  existing met-filtered aggregator — mirror its scoping.
- "Dossiers unlocked: M" = count opponents whose `compute_scouting(...)` has
  empty `locked` (full) — reuse `dossier_scouting`.

**Frontend**
- New React view (a drawer or page) listing tappable cards, each opening
  `CharacterDetailCard` (pass `circuitContext` true — the cabinet is a Circuit
  surface). `WhereaboutsDrawer.tsx` is the scaffold to copy.
- Sort/filter: most-played · rivals (heat) · biggest winners/losers vs you ·
  recently seen · locked vs unlocked. Header: "People met: N · Dossiers
  unlocked: M."
- Remember the portal-to-body convention for overlays (PageLayout traps
  z-index).

**Achievement tie-in** (when the achievements system ships): the "dossiers
fully unlocked" count is the natural badge counter — see the parent doc.

---

## Part B — Tier 2 intel enrichments

The dossier already has the data for most of these — it's mostly surfacing
work. Each becomes new grind tiers / reads.

### B1. Deep postflop reads as new grind tiers (highest leverage)
`OpponentTendencies` already computes and serializes far more than the
lifetime store keeps — see `to_dict` in `poker/memory/opponent_model.py`:
`_fold_to_cbet_count`/`_cbet_faced_count`, `_cbet_attempt_count`,
`_barrel_count`/`_barrel_opportunity_count`, `_third_barrel_*`,
`_all_in_count`, postflop aggression counters, equity-at-action
(polarization) sums/counts, opportunity-normalized preflop counters.

- **Extend the lifetime store**: add these counts to
  `opponent_observation_lifetime` (migration) + the fold's
  `_LIFETIME_COUNT_FIELDS` map (`game_repository.py`) + the read
  (`load_observation_lifetime`) + the dossier derivation
  (`_observation_from_lifetime` / a new deeper-reads builder, reusing
  `_recalculate_stats` so fold-to-cbet/cbet/barrel rates match live).
- **Add grind tiers** past 180 in `SCOUTING_SCHEDULE` (e.g. fold-to-cbet @
  120, cbet @ 150, barrel @ 220, polarization @ 300) and informant sections
  for them. This gives the 100–500-hand range something to unlock and makes a
  maxed dossier genuinely deep.
- The gate's `_redact_item` needs cases for the new item ids.

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
1. **File cabinet** (Part A) — the retention payoff; self-contained.
2. **B1 deep reads** — extends the grind + enriches the max dossier from data
   we already collect (do the lifetime-store extension first, then tiers).
3. **B2 exploit hints** — high flavor, small.
4. **B3 / B4** — polish.

## Open tuning knobs (not blocking)
- Per-item grind thresholds + informant prices (flat first-pass today).
- Partial-section informant pricing (full price for partial progress today).
- Pressure/memorable true per-sandbox scoping (owner-scoped today).
