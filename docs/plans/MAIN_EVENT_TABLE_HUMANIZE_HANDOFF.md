---
purpose: Single-entry handoff to finish "humanizing" the human Main Event table — wire tournament hands into the opponent-dossier grind, finish the avatar re-key, then (larger) give the live table real persona play. Start here.
type: guide
created: 2026-06-02
last_updated: 2026-06-03
status: Identity unification (names + avatars) DONE + pushed on `tournaments`. **P3.9a (dossier wiring) `790a69e3` + P3.9c (real persona play) `9b13b0d9` pushed. P3.9b avatar dual-key kill DONE as schema v138 (pid-only storage; personality_name column + OR reads gone) — uncommitted.** Full pid-native (name as lookup input) deferred to TRIAGE.md. Prod is months behind on a legacy migration system (`PROD_MERGE_PLAN.md`). Remaining follow-ons: relationship-aware field selection + P4 carry-out. Circuit is ACTIVATED on dev.
---

# Humanize the Main Event Table — Remaining Work (START HERE)

The tournament **economy + lifecycle** is built, validated, and live on dev
(`TOURNAMENT_CIRCUIT_ENABLED=1`). The human-playable Main Event table was promoted
from a stripped "headless-field bridge" toward a first-class persona table; this
doc finishes that arc. It's the single entry point: what's done, what's left, and
the exact seams.

## 0. What's already DONE (pushed to `origin/tournaments`)

The "identity" half of humanizing the table is complete:
- **Display names unified** (`47b95ea0`) — `tournament/identity.py::resolve_display_name`
  is the one canonical resolver; the felt, the ticker winner beat, and the
  completion standings all route through it. The MTT bridge keeps `Player.name =
  personality_id` (identity); the persona's real name rides on `Player.nickname`
  (the frontend's existing display hook).
- **Avatars re-keyed on `personality_id`** (`209860f0`, schema **v137**) —
  `avatar_images` now carries `personality_id` (backfilled by the unique
  display-name join). Reads tolerate either key during the transition; writes
  populate both. So a tournament seat (looks up by id) hits the persona's
  **existing** cached avatar instead of regenerating — no more on-demand storm.
- Earlier in the arc: cold-load rehydrate fix (`b8b40510`), the hand-boundary
  anti-freeze (`ec186716`), the "Resume the Main Event" lobby bar (`dd704025`),
  the 429 + cannot-field error fixes (`a88e8348`).

What's left is below, in build order.

---

## P3.9a — Wire tournament hands into the opponent-dossier grind — ✅ DONE (2026-06-02, uncommitted)

**Shipped.** All three seams wired; both Breaks closed on the fresh-build AND the
cold-load ("Resume the Main Event") path. Decision on the open question: wired via
`set_relationship_repo(cash_mode=False, sandbox_id=…)` — the richer v1 path, so
tournament play ALSO fires relationship events (warmth/respect/heat) at the
boundary (`on_hand_complete` already runs for tournaments in `game_handler.py`
before the boundary), while `cash_mode=False` keeps chip-PnL / `cash_pair_stats`
writes off (chips reset in a tournament).

What changed:
- **`tournament_game_builder.py::build_tournament_game`** — resolves the owner's
  default sandbox, calls `set_relationship_repo(relationship_repo,
  cash_mode=False, sandbox_id=…)` (Break A), and registers each AI seat with
  `personality_id=s.player_id` gated on `econ.real_persona_ids_for(...)` (Break B).
- **`tournament_game_builder.py::tournament_hand_boundary`** — new `_fold_observations`
  helper (save_opponent_models + fold_observations_into_lifetime) runs every
  boundary, so the hand that finishes on an AI action / the final hand still folds
  (the per-human-action fold in `game_routes` covers the rest).
- **`tournament_handler.py::reconcile_live_table`** — new `real_persona_ids` param;
  balanced-in seats register their pid too (Break B mirror). Fed by
  `_real_persona_ids_for_session(session)` from `advance_tournament_after_hand`.
- **`game_routes.py` cold-load** — added `is_tournament_game`; `cold_load_sandbox_id`
  now resolves for tournaments (Break A on resume) and tournament seats resolve
  `pid = player.name` (gated on being a real persona) instead of
  `resolve_name_to_personality_id` (which queries by display name → None for a pid
  slug = Break B on resume).
- **Test:** `tests/test_tournament/test_live_play_integration.py::test_tournament_observations_fold_into_dossier`
  — seeds a real persona, plays heads-up via the real `progress_game` loop, asserts
  the durable `opponent_observation_lifetime` row exists keyed by
  `(sandbox, owner_id, persona_id)`. Full `tests/test_tournament/` suite green.

**Outstanding for the next session:** commit this (still uncommitted on
`tournaments`); the schema is unchanged (no new migration). Then P3.9b → P3.9c.

---

## P3.9a — original spec (kept for reference)

**Problem.** In cash/regular games, as the human plays, their observed-hand count
of each opponent accrues and unlocks dossier "reads" (the scouting grind). In a
tournament, **hands observed don't count** — clicking an opponent's dossier shows
nothing earned. This is the last unwired identity-keyed surface.

**Root cause (traced — three stages, two broken).** The grind is NOT a single
boundary call:
1. **Per-action observation** (builds `hands_observed` + tendencies in the
   in-memory `OpponentModelManager`) — **already runs** for tournaments: the
   action paths (`message_handler.py::record_action_in_memory` →
   `memory_manager.on_action` → `opponent_model.observe_action`) are shared and
   ungated, and the builder registers the human observer.
2. **Per-hand persistence + fold into the durable lifetime table**
   (`opponent_observation_lifetime`, which the dossier actually reads) — **NOT
   run** for tournaments. Two independent breaks:
   - **Break A — `memory_manager.sandbox_id is None`.** The builder constructs
     `AIMemoryManager(...)` and never sets a sandbox, so
     `fold_observations_into_lifetime` early-returns (it's gated on a truthy
     sandbox_id at `game_repository.py:911-912`).
   - **Break B — AI `opponent_id` is None.** The builder calls
     `memory_manager.initialize_for_player(s.player_id)` **without
     `personality_id=`**, so `register_player_id(name, None)` leaves the name→id
     map empty for AIs; the fold then skips every AI row
     (`game_repository.py:933`, `if not opponent_id … continue`).
3. **Dossier read + scouting gate** (`dossier_scouting.py::apply_scouting_gate`,
   called from `character_routes.py::get_dossier`) reads **only** the durable
   lifetime table (`load_observation_lifetime(sandbox_id, observer_id,
   personality_id)`) — never the in-memory model. So stage-1's in-memory accrual
   is invisible until stage-2 persists it.

**Keying (verified — unifies, doesn't fork).** The lifetime row + dossier are
keyed `(sandbox_id, observer_id, opponent_id)` where `opponent_id` is the
**personality_id**. A tournament seat's `Player.name` already **IS** the
personality_id slug. So registering the id (Break B fix) writes under the SAME key
the cash dossier reads → tournament + cash observations **unify** into one
observed-hand count per persona. (Observer axis already matches: human registered
with `personality_id=owner_id`, dossier reads `observer_id = owner_id`.)
**Do NOT register the display name** — that would fork (the axis is the id, not
the name).

**The minimal seam (all in `flask_app/handlers/tournament_game_builder.py`,
mirrored in `tournament_handler.py`):**

- **(a) Set the memory_manager's sandbox_id** (fixes Break A). After constructing
  `memory_manager` (builder ~line 120), resolve the owner's sandbox (the same
  `resolve_default_sandbox_for(owner_id, sandbox_repo=sandbox_repo)` already used
  by the payout helper at ~line 251) and call
  `memory_manager.set_relationship_repo(relationship_repo, cash_mode=False,
  sandbox_id=sandbox_id)` (keeps `cash_mode=False` → no cash PnL/pair writes; also
  lights up relationship events — see the open question below) **or** set
  `memory_manager._sandbox_id = sandbox_id` directly if relationship events are
  unwanted for v1.
- **(b) Register each AI seat's personality_id** (fixes Break B). Change the
  builder's `initialize_for_player(s.player_id)` (~line 126) to
  `initialize_for_player(s.player_id, personality_id=s.player_id)` — **gated to
  real-persona fields** (guard with `econ.real_persona_ids_for(session,
  personality_repo)`, already imported at builder ~line 263, so a synthetic `P07`
  field doesn't write junk lifetime rows). Mirror the same change in
  `tournament_handler.py::reconcile_live_table` (~line 166), so players balanced in
  from broken tables also accrue.
- **(c) Run the fold at the tournament boundary** (robustness). Today the fold
  lives only in `game_routes.api_process_action` (`game_routes.py:2077-2092,
  2600-2615`), which fires on the human's action POST — present for tournaments,
  so (a)+(b) alone make it persist per human action. For the final hand / AI-only
  progression, add `save_opponent_models(...)` +
  `fold_observations_into_lifetime(game_id, memory_manager.sandbox_id)` into the
  tournament boundary — natural insertion point is `tournament_game_builder.py`
  `_persist_boundary` (~line 227), which already runs under the progress_game lock
  at every advance and has `game_id` + `game_data` in scope. Mirror the two repo
  calls from `game_routes.py:2077-2092`.

**Open question for the executor:** whether tournament play should also fire
**relationship events** (warmth/respect/heat shifts) the way `set_relationship_repo`
with a repo would. v1 recommendation: set the sandbox via `set_relationship_repo`
with `cash_mode=False` so the dossier grind works AND relationships evolve (your
nemesis remembers you busted them) — but if that's too much for v1, set
`_sandbox_id` directly to get ONLY the dossier grind. Decide and note it.

**Verify:** play a few tournament hands as the human, then click an opponent's
dossier — `hands_observed` should be > 0 and reads should start unlocking; the
same persona's count should be SHARED with cash (play them in both, confirm one
running total). Check `opponent_observation_lifetime` has rows keyed by the AI's
`personality_id` + `owner_id`. Add a test mirroring the cash observation-fold test
but through the tournament boundary.

---

## P3.9b — Finish the avatar re-key caller cleanup (small)

The avatar re-key (v137) shipped the **safe, backward-compatible** cut: reads
tolerate either `personality_id` OR the legacy `personality_name`. To finish it:

- Update the cash/regular callers to pass **`personality_id`** instead of the
  display name (the investigation found they all have the id in scope):
  `cash_routes.py:3408` (`c.personality_id`), `:5581` (`pid`), `:5963`
  (`person["personality_id"]`); `game_handler.py:592, 232` and
  `game_routes.py:1278` (resolve `player.name` → pid via
  `game_data['cash_personality_ids']` or `resolve_name_to_personality_id`, but
  **pass through** when `player.name` is already a pid — tournament seats);
  `personality_routes.py:425` and `admin_dashboard_routes.py:682` (resolve).
- Then drop the `OR personality_name = ?` fallback from the repo reads and let
  `personality_name` become a pure debug column.
- **Re-run the v137 backfill safety check on PROD** before relying on it there:
  `personalities.name` unique, `personality_id` non-null, and the orphan count
  (avatar `personality_name` with no matching persona) — the dev box had a clean
  0, prod may differ (zombie-persona cleanups ran on dev, maybe not prod).

This is optional for correctness (the tolerant reads already work) — it just
completes the migration and removes the dual-key surface.

**UPDATE (2026-06-03 — DONE the dual-key kill; full pid-native deferred).**
Shipped the storage-side cut as **schema v138**: `avatar_images` is now keyed
SOLELY on `personality_id` (NOT NULL, `UNIQUE(personality_id, emotion)`), the
legacy `personality_name` column + every `OR personality_name` dual-key read are
GONE, and the repo resolves any incoming key (name or pid) to a pid once at the
storage boundary (`_resolve_avatar_pid`). Writes for a key matching no persona are
a logged no-op (an avatar can't be keyed without a pid). v138 rebuilds the table
and drops NULL-pid orphan rows; v5/v137 guarded so the fresh-DB migration chain
(which runs over the new `_init_db` shape) doesn't reference the dropped column.
Tests: repo avatar tests reworked to seed personas + a `test_v138_*` migration
test + `test_fresh_db_avatar_images_is_pid_only`. The remaining **full pid-native**
step (banish the name as a lookup INPUT too — URL `/api/avatar/<pid>/…`, payload +
frontend plumbing) is **deferred to `TRIAGE.md`** ("Full pid-native avatars") — a
~15-file cross-stack change for ~zero gain over the UNIQUE-name invariant; no user
needs it yet.

**PROD note (checked 2026-06-03):** the prod backfill safety check below is MOOT
on the current prod — prod is months behind on a different migration system and
has no `personality_id` column at all (see `PROD_MERGE_PLAN.md` /
`project_prod_schema_drift`). The v137+v138 avatar backfill runs FRESH at the
eventual prod upgrade; orphan/dupe checks matter THEN.

**ORIGINAL recommendation (superseded — kept for context):** The
"small caller swap" framing was wrong. Two findings:
- **The write path is already canonical.** `save_avatar_image` / `assign_avatar`
  route through `_resolve_avatar_identity`, which normalizes whatever the caller
  passes (display name OR pid) into BOTH columns. So changing callers to pass
  `personality_id` is cosmetic for writes — the storage is already correct.
- **Dropping the `OR personality_name = ?` fallback is NOT a small change.** The
  whole avatar *generation* service keys on the display name: `get_avatar_url_with_fallback`
  → `get_full_avatar_url(player_name)` and, on a miss,
  `start_single_emotion_generation` → `generate_character_images(personality_name)`
  → `CharacterImageService.generate_images`, which looks up the persona's
  appearance BY NAME to build the image prompt. Passing a pid into those callers
  would break on-demand generation unless pid-resolution is threaded through the
  entire image service. That's a real refactor for ~zero correctness gain.

So the read-tolerance is doing useful work (it's exactly what lets a tournament
seat keyed by pid hit the persona's name-keyed cached avatar). **Recommendation:
keep the dual-key reads; do NOT migrate callers / drop the fallback** unless the
image service is separately refactored to be pid-first. The ONLY load-bearing
item from this section is the prod backfill safety check below.

**Prod backfill safety check (still wanted, needs prod access).** Verify v137's
assumptions hold on prod: `personalities.name` unique, `personality_id` non-null,
avatar rows have `personality_id` populated, and zero orphan avatar rows
(`personality_id IS NULL` AND no persona with that `name`). Dev was clean; prod
may differ (zombie-persona cleanups ran on dev, maybe not prod). Read-only.

---

## P3.9c — Real persona play on the human's table — ✅ DONE (2026-06-02, uncommitted)

**Shipped (Full / mirror-cash).** The human's live tournament table now builds its
AI seats the way a cash table does — per-persona `bot_type` from
`assign_bot(personality_config)` + `expression_enabled=True` (table talk) — gated
to real-persona fields. The relationship axes were already wired in P3.9a, so the
psychology/relationship half came for free; this added the visible half (varied
play + table talk). Cost == one cash table per active human tournament (one live
table ~5 seats; a `CallType.COMMENTARY` narration call per AI decision). The rest
of the field stays headless `FakeHandResolver` — no added cost there.

What changed (all in `tournament_game_builder.py` + one cold-load line):
- **`build_tournament_seat_controller(...)`** — new shared seat factory. Real
  persona → `build_controller(bot_type=assign_bot(...).bot_type,
  llm_config=..., expression_enabled=True, prompt_config=standard)` (fish persona
  → fish controller); synthetic `P##` → the old zero-LLM tiered solver. Records
  the chosen `bot_type` + `llm_config` into dicts for cold-load.
- **`build_tournament_game`** — computes `real_persona_ids` once (reused by the
  P3.9a dossier wiring), builds seats via the factory, stores
  `tournament_is_persona_field` / `tournament_bot_types` /
  `tournament_player_llm_configs` in game_data, and persists the cash-style
  `llm_configs` blob (`ai_chat=True` + per-seat configs) for persona fields vs the
  old `ai_chat=False` + all-`sharp` for synthetic (via `_build_seat_llm_configs`).
- **`tournament_hand_boundary`** — `_make` (the reconcile factory for balanced-in
  seats) now uses the same persona factory, so a persona balanced in from a broken
  table also talks; `_persist_seat_llm_configs` re-saves the blob after a balance
  so cold-load stays correct (the live save path COALESCEs llm_configs).
- **`game_routes.py` cold-load** — the `tourney-` guard now HONORS the persisted
  `ai_chat` (default False for legacy/synthetic rows that saved none) instead of
  hard-forcing expression off, so a persona field rebuilds WITH table talk + valid
  per-seat configs and a synthetic field stays zero-LLM.
- **Tests:** `test_persona_field_builds_talking_controllers` (persona seat has an
  expression generator; `ai_chat=True` + per-seat `player_llm_configs` persisted)
  + the existing `test_build_persists_zero_llm_intent` still guards the synthetic
  side. Full `tests/test_tournament/` = 284 passed.

**Follow-ons (not done):** relationship-aware field selection (draft your nemesis
into the field) + P4 carry-out (prestige / relationship shifts / settlement). Note
the FakeHandResolver field still uses funny-money archetypes that don't reflect the
persona's real play — only the human's live table got the real controllers.

---

## P3.9c — original spec (kept for reference)

Names + avatars + dossiers make the table *look and track* like the circuit. The
remaining gap is that the AIs **play** like a uniform solver, not like personas:

- `make_tournament_ai_controller` (`tournament_game_builder.py`) builds the
  **tiered solver bot** for every seat with `expression_enabled=False` — no
  psychology, no relationship-to-you, no table talk. The `archetype` (LAG/CaseBot)
  in the field is funny-money flavor that doesn't even apply to the live table.
- **The move:** on the **human's table only** (the other field tables stay
  headless `FakeHandResolver` for speed/cost), build the AI seats with the **same
  persona-flavored controller cash mode uses** — psychology axes, relationship
  state, table talk — seeded from each persona's real state. This is an LLM-cost
  decision (one live table, budget it). It's the difference between "a solver sim
  with persona labels" and "the circuit's characters showed up to play *you*."
- Adjacent, smaller: **relationship-aware field selection** — draft the field from
  the player's circuit (rivals, met personas, tier peers) via the relationship
  graph instead of generic `list_eligible_for_cash_mode`, so your nemesis shows up.
- **P4 carry-out:** results feed back — prestige, relationship shifts, real
  bankroll/stake settlement. (Staking into entries is separately blocked on
  buy-in tournaments existing — v1 Main Events are freerolls; see
  `P3_REMAINING_HANDOFF.md` Deferred.)

Sequence: P3.9a (dossier) → P3.9b (avatar cleanup) → P3.9c (persona play), with
field-selection and carry-out as follow-ons.

---

## Context / scar tissue (read before editing)

- **Tests run in Docker:** `docker compose exec -T backend python -m pytest …`
  (or `python3 scripts/test.py`). Never bare pytest on the host.
- **The dev Flask auto-reload kills in-flight `docker compose exec` sims AND
  desyncs a live game if you edit a `.py` mid-hand** — it's what froze the live
  tournament during this work (the live game + MTT session cold-loaded from
  different points → `_apply_result` KeyError). Don't edit backend `.py` files
  while a tournament is being played; the `.get(pid, 0)` anti-freeze degrades a
  desync gracefully but doesn't make it correct. The proper fix (atomic
  game+session persistence + cold-load reconcile) is a latent follow-up.
- **Schema is at v137.** Any new migration → v138, dual-path (`_init_db` +
  `_migrate_vNNN` + `migrations` dict). **v136/v137 collide with the `polish`
  branch** (emotion-families v136) — renumber on merge to `development`. An
  `_init_db` index on a migration-added column MUST be guarded on the column
  existing (init runs before migrations) — see `idx_avatar_pid` for the pattern.
- **Tournament seat identity:** `Player.name = personality_id` (for the MTT
  bridge), `Player.nickname = display name` (for the UI). Everything persona —
  bankroll `ai:<pid>`, ledger, relationships, dossier, avatars (now) — keys on
  `personality_id`. Resolve display via `tournament/identity.py`. Never key a
  durable persona surface on the display name (rename-fragile + forks cash/tourney).
- **Real-persona vs synthetic field:** a `/register` (legacy) field is all
  synthetic `P##` ids the human drives; an autonomous/accept field is real
  personas. `econ.real_persona_ids_for(session, personality_repo)` distinguishes
  them — gate any persona-state wiring (dossier, persona controllers) on it so
  synthetic seats don't write junk.
- **Activation:** `TOURNAMENT_CIRCUIT_ENABLED=1` is set in the dev `.env`
  (prod default stays False). Force a test invite with
  `docker compose exec -T backend python -m scripts.force_main_event guest_jeff`.
  The economy (drain-to-setpoint overlay) is sim-validated (EXP_006 §6); a
  hands-ON aged-sandbox fidelity run is still wanted before the prod flip.

## Pointers
- Identity resolver: `tournament/identity.py`.
- Live table builder + boundary: `flask_app/handlers/tournament_game_builder.py`,
  `flask_app/handlers/tournament_handler.py`.
- Observation/dossier engine: `poker/memory/memory_manager.py`,
  `poker/memory/opponent_model.py`, `poker/repositories/game_repository.py`
  (`save_opponent_models`, `fold_observations_into_lifetime`,
  `load_observation_lifetime`), `flask_app/services/dossier_scouting.py`,
  `flask_app/routes/character_routes.py::get_dossier`.
- Avatar repo: `poker/repositories/personality_repository.py` (avatar methods
  ~843-1140), `poker/character_images.py`, `flask_app/routes/image_routes.py`.
- Prior handoff (economy/lifecycle): `docs/plans/P3_REMAINING_HANDOFF.md`.
