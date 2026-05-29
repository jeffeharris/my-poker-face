---
purpose: Grounded narrative log of shaping the dossier scouting plan and shipping the Phase 1 persistence foundation on the dossiers branch
type: reference
created: 2026-05-29
last_updated: 2026-05-29
---

# Captain's log — opponent dossier & scouting progression (dossiers worktree)

Honest record of reviewing `docs/plans/OPPONENT_DOSSIER_PROGRESSION.md` via the
feature-dev workflow and building Phase 1 (the persistent, per-sandbox
behavioral observation store). Newest at the bottom. Wrong turns kept in.

---

## 2026-05-29 — plan review, then Phase 1 foundation built end-to-end

**Started as a doc review, became a build.** Jeff asked to "shape up" the
existing vision doc. Three code-explorer agents grounded its claims against the
live code, and the doc had drifted in ways worth catching before building:

- It cited `character_routes.py:463-470` for the live dossier sections — those
  lines are now `stake_summary`. The real assembly is `:476-488`.
- It called the behavioral reads "live-only, lost on restart." More precise:
  the per-game `opponent_models`/`pressure_events` rows *do* persist and
  cold-load back; the real gap is they **never accumulate across games**. That
  reframing matters — Phase 1 is *cross-game aggregation*, not crash-safety.
- The gating units "Basic read / Pattern / Pressure / Exploit" never existed in
  the UI. Real sections are PROFILE / BEHAVIORAL INDEX / STANDING / TRACK
  RECORD / etc. The gate has to map onto those.
- Two durable sections the doc omitted (`ai_bankroll`, `stake_summary`) had
  been added since.

**Decisions Jeff locked (not my defaults):** hybrid granularity (grind drips
items, informant buys a section); hands-observed as the grind metric (his
reasoning: a nit shouldn't take forever to scout — observed counts folds too);
materialized lifetime table; Circuit-only scope; achievements tracked globally.

**The wrong turn I avoided (the useful one).** The doc said "migrate
`relationship_states` to per-sandbox (v123) + backfill" as if it were a
one-liner, and I'd written it up that way. Digging into the repo before
touching it: `relationship_states` is **bidirectional**, and its inbound
edges (AI→human / AI→AI) feed the **prestige "regard"** computation that
*just shipped* (commits `105017b5` / `eb9bc354`, two days prior). The code is
explicit that those edges are global *by design*. Adding `sandbox_id` to the
PK would have forced a scoping decision on them and risked breaking prestige.
So I **deferred** that migration (it only blocks Phase 4 anyway) instead of
barrelling into a destructive schema change on a premise I hadn't verified.
Lesson reinforced: verify the premise in the code, not the plan's prose.

**The cash wrinkle that reshaped the fold.** First instinct (and the doc's
words) was "roll up at game-end." But cash sessions don't end — they're
long-lived and *resumable* (cold-load reuses `game_id`). A fold-once-per-game
would lose post-resume hands. Landed on a **continuous delta-fold** with a
per-game high-water mark (`opponent_models.lifetime_applied_json`):
`delta = current − applied; lifetime += delta; applied = current`. Idempotent,
resume-safe, and re-folding an unchanged game writes nothing.

**Then a second pivot, off the minefield.** I'd planned to hook the fold into
the cash leave/settle path. That path has a long bug history (double-settle,
orphan seats — it's all over the memory notes). Because the fold is idempotent,
I moved it to the existing **hand-boundary save points** (`game_routes.py`,
right beside `save_opponent_models`) instead — normal action-processing hooks,
not the settlement code. Bonus: crash-safe (folded every hand) and the lifetime
row is always current, so the dossier reads it directly with no live-overlay
machinery. Cost: dropped the "this session vs. lifetime" split-display for v1
(eye-candy, deferred).

**Reused the canonical rate formula instead of duplicating it.** The lifetime
store holds *counts*; rates (VPIP/PFR/AF/play-style) derive on read. Rather than
re-implement the thresholds (and the AF cap that imports strategy config), the
dossier reconstructs an `OpponentTendencies` from the counts and calls its own
`_recalculate_stats()`. Caught a real bug doing this: my first read divided VPIP
by `hands_observed`, but the app defines it over `hands_dealt` — reusing the
canonical method made the discrepancy impossible.

**Shipped (branch `dossiers`, uncommitted at time of writing):**
- v123 migration: `opponent_observation_lifetime` + `opponent_models.
  lifetime_applied_json`. Verified applied to the live DB.
- `GameRepository.fold_observations_into_lifetime` + `load_observation_lifetime`.
- `MemoryManager.sandbox_id` accessor; fold wired + guarded at both
  hand-boundary saves.
- Dossier prefers the lifetime observation (`character_routes.py`).
- Cheap `cash_pair_stats` dossier read now sandbox-scoped.
- 12 new tests; 54 game-repo/dispatch regression tests green.

**Process scar (minor):** mid-edit, the Flask reloader caught a transient state
where the migrations dict referenced `_migrate_v123` before the method body had
landed — backend crash-looped until both edits were in. Harmless (restart fixed
it) but a reminder that two-part edits to an auto-reloaded module want the
referenced symbol defined first.

**Not done:** backfill of existing `opponent_models` history into lifetime
(needs a `game_id → sandbox_id` map; the fold method itself doubles as the
backfill since it's idempotent); live end-to-end verification in a real cash
session; the deferred `relationship_states` migration; Phases 2–4.

---

## 2026-05-29 (later) — Phase 2: the grind gate

**Built the server-side gate, not a client one.** The dossier's earnable
reads (behavioral tendencies, track record, table posture) now strip out of
the payload until earned — locked intel never reaches the client, so there's
nothing to peek at in devtools. The server returns a `scouting` descriptor
(hands observed, floor, unlocked ids, locked ids + thresholds) and the client
renders the case-file from it. Chose this over client-side hiding because the
whole pitch is "earn the read."

**Confined to the Circuit.** The gate only fires when there's a sandbox +
observer + lifetime row. The dossier endpoint is used in lots of contexts
(lobby, mid-game, anonymous); outside the Circuit it stays ungated exactly as
before. So the meta-game doesn't leak into places it doesn't belong, and I
didn't risk regressing the existing dossier everywhere.

**Kill switch, because this hides previously-shown data.** Unlike a purely
additive feature, Phase 2 *removes* reads players used to see for free.
`DOSSIER_SCOUTING_GATE_ENABLED` flips it all off with zero residual effect
(the gate is a pure read-time transform) — same discipline as the prestige
demeanor switch.

**A leak I checked and cleared.** The portrait shows `merged.playStyle`, which
falls back to `personality.play_style`. For a second I worried that leaked the
gated read — but that's the *declared* style (identity, free, like attitude),
while the gated field is the *scouted* play_style derived from observed
VPIP/AF. Different fields; no leak. Left declared style free (it's identity,
not something you scout).

**Schedule is one tunable constant.** Floor 25, then a drip 25→180 hands.
"Tuning, not design" per the plan, so it's a flat list — reorder/retime
freely without touching the gate logic.

**Frontend leaned on the existing case-file aesthetic.** The dossier already
had wax-seal/gold-rule/aged-paper styling, so the gate reads as a CLASSIFIED →
CLEARANCE clearance strip with a "still to scout" list. Reused the theme
tokens; no new visual language invented.

**Shipped:** `flask_app/services/dossier_scouting.py` (pure), wired into
`get_dossier`; `economy_flags.DOSSIER_SCOUTING_GATE_ENABLED`; `ScoutingStrip`
+ CSS in `CharacterDetailCard`; `DossierScouting` types. 7 gate tests; TS
clean.

**Not done:** live declassification check in a real 25+-hand session (gate is
unit-tracked but not human-played); archetype badge (no detection source);
Phases 3–4.

---

## 2026-05-29 (later still) — live verification + Phase 3 (the informant)

**Verify-live, pragmatically.** A true 25-hand human playthrough wasn't worth
it in this loop (LLM latency/cost), so I wrote an HTTP-level integration test
that drives the real `/api/character/<id>/dossier` route — real request
context, extensions, kill switch, the actual fold+load+gate — with a seeded
observed-hand count. It immediately earned its keep: it caught that below the
floor the observation block came back as a dict of nulls instead of absent,
so the client could render an empty stat panel. Fixed by collapsing a
fully-redacted observation to None. That's the value of testing the wire, not
just the function. Still not done: watching the fold fire during *actual*
gameplay (the hand-boundary hook) — confident by inspection, unproven live.

**Phase 3: the informant.** The pay-to-unlock chip sink. Sections (not items)
are the purchase unit — matches the hybrid decision (grind drips items, the
informant buys a section). Buying unions into the unlock set and **bypasses
the grind floor**, so you can buy intel on someone you've barely played — the
intended "I don't know this guy, so I pay to find out" fantasy.

**Chip path, handled with care (it's a minefield per the memory notes).**
Mirrored the vice-spending sink exactly: player bankroll → recyclable bank
pool via a new `informant_unlock` ledger reason (added to both
`LEDGER_REASONS` and `BANK_POOL_DEPOSIT_REASONS` so scouting fees recycle into
the AI-funding pool — the doc's "feeds the bank pool"). The ordering decision
that mattered: **store the unlock first (idempotent), then debit.** A retry
after a mid-flight failure then hits the already-owned 409 and never
double-charges; the worst case is a free unlock if the debit fails, which
favors the player over charging twice. Reverse order would risk the
double-charge that the cash-mode bug history is littered with.

**Frontend stayed minimal.** Buy buttons live inside the existing
`ScoutingStrip` ("Track record · 1,000 chips"); on success it refetches the
whole dossier so every newly-declassified read populates (the gate reveals
data, the refetch pulls it). Inline error for insufficient bankroll. Reused
the gold-leaf button styling.

**A test that lied, briefly.** My first route test asserted `cash_pair_stats`
was populated after buying `track_record` — but I'd never seeded a cash-PnL
row, and the gate *reveals* data, it doesn't fabricate it. Rewrote the
assertion to check the unlock persists across requests (the section drops out
of `informant_offers`, the item joins `unlocked`). Good reminder to assert on
what the code actually does, not what I hoped.

**Shipped:** v124 `dossier_informant_unlocks`; `INFORMANT_SECTIONS` + purchase
threading in `dossier_scouting.py`; `game_repo.load/record_informant_unlocks`;
`informant_unlock` ledger reason + `record_informant_unlock`;
`POST /api/character/<id>/informant`; `ScoutingStrip` buy buttons +
`buyInformantUnlock`. 12 new tests; 85 green across dossier/ledger/flags; TS
clean.

**Not done:** pricing is a flat first-pass (tuning); a partially grind-unlocked
section still costs full price (noted); the random-vs-chosen lever resolved to
player-chosen; archetype badge; Phase 4 (file cabinet); live UI eyeball of the
buy flow + the deferred `relationship_states` migration.

---

## 2026-05-29 (Tier 1 follow-up) — durable pressure + memorable hands

**The gap the user found.** A "fully unlocked" dossier between games was
missing pressure (signature move, biggest pots, HU record, bluffs) and
memorable hands — Phase 1 only made *observation* durable; these stayed
live-only and vanished when the game left memory.

**Chose aggregate-on-read over a fold.** For observation I built a
materialized lifetime table + delta-fold. For pressure/memorable I went the
other way: re-aggregate on read. Why — the source tables (`pressure_events`,
`memorable_hands`) already hold every event, the live aggregator
(`PlayerPressureStats.get_summary`) already derives the exact summary, and a
*fresh* re-aggregation each read structurally can't double-count (the bug
that bit the observation fold). One repo query + replay vs. a new
table+migration+fold+high-water-mark. Much less surface, and it reuses the
canonical derivation so lifetime and live stats can't drift.

**Scope compromise, surfaced.** Scoped by `owner_id` (the game owner) rather
than strictly per-sandbox, because `pressure_events`/`memorable_hands` carry
no `sandbox_id` and no game→sandbox map exists without the backfill work.
Under v1's 1:1 ownership owner == sandbox, so it's identical today; noted as
a divergence from observation's true per-sandbox keying for the multi-sandbox
future. A neat trick fell out: filtering memorable to `observer_name =
games.owner_name` pins it to the human-as-observer across their games without
needing to know their display name up front.

**Scope call on the file cabinet.** Folded it into the Tier-2 handoff instead
of building it here. It's a self-contained new surface (endpoint + React
list) ideal for a fresh context, and three Phase-sized builds in one session
would have meant rushing the sensitive lifetime-adjacent code. Told the user.

**Shipped:** `get_player_events_for_owner` (PressureEventRepository),
`load_lifetime_memorable_hands` (GameRepository), `_build_lifetime_pressure_summary`
+ dossier preference wiring (character_routes). 5 new route/integration tests
(durable survival between games + still-gated-below-threshold); 34 dossier +
36 repo/pressure regression green.

**Not done:** true per-sandbox scoping for pressure/memorable (owner-scoped
for now); the file cabinet + all of Tier 2 (handed off).
