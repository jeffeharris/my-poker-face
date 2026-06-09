---
purpose: Ready-to-write outline for the "Your opponents remember you" blog post (Inside the Table track)
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Outline — "Your opponents remember you"

- **Working title:** Your opponents remember you
- **Track:** Inside the Table
- **Target reader:** Players and build-in-public followers who want to see how a
  single-session AI poker game grew a memory — and the small, careful engineering
  decisions that made "memory" real instead of cosmetic.

## One-line hook (grounded)

For three years the AI opponents forgot you the moment a game ended; the fix wasn't
a bigger model, it was a lifetime table and a per-game high-water mark so the same
counts couldn't be tallied twice.

## Narrative spine (section beats, in order)

1. **The premise that was missing.** My Poker Face always tracked how an opponent
   played — VPIP, PFR, aggression factor — but only inside one game. Start a new
   table against the same Blackbeard and every read reset to zero. The honest framing
   (from the build log) is precise: the per-game `opponent_models` rows *did* persist
   and cold-load back; the real gap was that they **never accumulated across games**.
   So Phase 1 was cross-*game* aggregation, not crash-safety — naming the problem
   correctly was the first move.

2. **Two ways to make a number durable.** The post's technical heart: there are two
   honest patterns and the project used both. Observation counts (VPIP/PFR/AF) got a
   **materialized lifetime table** plus a delta-fold. Pressure stats and memorable
   hands went the other way — **re-aggregate on read** from tables that already hold
   every event. Why the split: a fresh re-aggregation each read structurally *can't*
   double-count, so it was the safer choice wherever the source events already existed.

3. **The bug that idempotency designed out.** The fold runs continuously, not once at
   game-end — because cash sessions don't end, they're long-lived and resumable
   (cold-load reuses the same `game_id`). A fold-once-per-game would silently drop every
   post-resume hand. The fix is a per-game high-water mark:
   `delta = current − applied; lifetime += delta; applied = current`. Re-folding an
   unchanged game writes nothing. That one property let the fold move off the
   bug-prone cash settle path onto the normal hand-boundary save points.

4. **From "stat" to "intel you earn."** Persistence alone is plumbing; the feature is
   the **dossier**. Reads are gated behind a grind: you start at a 25-hand floor and
   the deep reads drip in as you observe more hands (the schedule runs out to ~180).
   The metric is *hands observed* (which counts folds too — Jeff's reasoning: "a nit
   shouldn't take forever to scout"). The gate is server-side: locked intel never
   reaches the client, so there's nothing to peek at in devtools.

5. **The chip sink: pay an informant.** If you don't want to grind a read, you buy it.
   Buying a section bypasses the floor — the "I don't know this guy, so I pay to find
   out" fantasy. The careful bit is the money path (cash mode has a long double-charge
   bug history): **store the unlock first, then debit**, so a mid-flight retry hits an
   already-owned 409 and the worst case is a *free* unlock, never a double charge. The
   fee recycles into the AI-funding bank pool rather than vanishing.

6. **It became a meta-game, not a setting.** The dossiers needed a home, so they grew
   one. A "file cabinet" of everyone you've scouted, redesigned into "The Archive" (a
   noir manila-folder case file), then folded with the activity feed and "who's around"
   into a single Intel hub — "The Wire / The Floor / The Files." This is the retention
   loop the founder's "what keeps you coming back?" question was after: a collection you
   build by playing.

7. **Closing: why a memory, not a smarter bot.** Tie back to the project's origin —
   living personalities that have a mood and an attitude predate the AI pair by years.
   Cross-session memory is the same instinct extended in time: the opponent isn't just
   a strategy, it's a record of your shared history. The work that made it real was
   ordinary, careful engineering (idempotent folds, server-side gating, debit ordering),
   not a model upgrade.

## Evidence & assets

**Hard facts / numbers to cite (verify each against code before publishing):**
- Grind gate: **floor 25 hands**, drip schedule **25 → 180 hands** (one tunable
  constant; "tuning, not design"). Source: dossier captain's log, Phase 2 entry.
- Delta-fold formula, verbatim: `delta = current − applied; lifetime += delta;
  applied = current` (per-game high-water mark in `opponent_models.lifetime_applied_json`).
- Two durable mechanisms: materialized lifetime table (observation) vs.
  re-aggregate-on-read (pressure / memorable hands).
- Cross-session data shape (from `CROSS_SESSION_OPPONENTS.md`): historical block carries
  `session_count`, `total_hands`, weighted `vpip`/`pfr`/`aggression`, `style`, and up to
  **5** notes. Example (doc uses a licensed name; recast as Blackbeard for the post):
  3 sessions / 47 hands vs Blackbeard, historical VPIP 58% vs today's 35%.
- Schema versions touched: **v123** (`opponent_observation_lifetime` +
  `lifetime_applied_json`), **v124** (`dossier_informant_unlocks`), **v125** (B1 deep
  reads), **v133–v135** (sizing / postflop-aggression / trap reads persisted to lifetime).
- Kill switch: `DOSSIER_SCOUTING_GATE_ENABLED` (the gate is a pure read-time transform,
  so flipping it off has zero residual effect).

**Screenshots / files:**
- PRIMARY HERO: `react/react/src/assets/screenshots/mobile-dossier.png` — the
  CLASSIFIED case file showing the locked state ("Insufficient
  observation. Play 24 more hands to open this file."), the per-section informant prices
  (Behavioral read −630, Track record −880, The read −1,130, etc.), and the wax-seal /
  aged-paper aesthetic. This single image sells beats 4–6.
- Supporting: `react/react/src/assets/screenshots/coach-tip.png` — in-game stats overlay
  (the live read side of the system). Use if illustrating "the live read" vs "the
  durable read."
- Source docs to link/excerpt: `docs/technical/CROSS_SESSION_OPPONENTS.md` (data shape,
  coach example) and `docs/captains-log/dossiers/opponent-dossier-progression.md` (the
  decision narrative — most of the post's spine comes from here).

**Commits to reference (real subjects, dated):**
- `f6f9c5b3 feat(dossier): Phase 4 — the file cabinet` (2026-05-29)
- `fc8f5d9c feat(dossier): make pressure + memorable hands durable cross-game` (2026-05-29)
- `f60bd374 feat(dossier): redesign the file cabinet as "The Archive"` (2026-05-29)
- `dde751ef feat(dossier): fold intel surfaces into one "Field Office" hub` (2026-05-29)
- `7fcce262 feat(dossier): name the intel tabs The Wire / The Floor / The Files` (2026-05-29)
- `cb42c50d feat(stats): persist sizing-aware opponent reads to the lifetime store (v133)` (2026-06-01)
- `72cc60c2 fix(dossier): crash opening a dossier at 25 hands (toFixed on null)` — the
  real wrong-turn coda (a null read crashed the dossier exactly at the unlock boundary).

## Candidate pull-quotes (verbatim)

- Commit subject: **`feat(dossier): name the intel tabs The Wire / The Floor / The Files`**
  — captures the meta-game's noir voice in one line.
- Jeff, steering the scope (chat, 2026-05-29): **"skip backfill, take on the rest of
  tier 1, then package tier 2 for a new context to take on."** — shows the founder
  triaging, not the AI.
- Jeff, on the file cabinet (chat, 2026-05-29): **"its fine. i dont like that the whos
  around and file cabinet are right next to each other... the file cabinet itself is
  serviceable. not really exciting but maybe there is something you could do..."** — the
  honest "serviceable but flat" note that triggered the Archive redesign. (Lightly
  trimmable for length; keep the "serviceable / not really exciting" core verbatim.)
- Jeff, the bug report that closes the loop (chat): **"i open a dossier for someone ive
  played 25 hands with, i get a crash ... Cannot read properties of null (reading
  'toFixed')"** — the unlock boundary biting back. Pairs with commit `72cc60c2`.

## Draft intro paragraph (post voice)

> For almost three years, the AI characters in My Poker Face had no memory of you. They
> had moods and attitudes — that part has been there since 2023 — but the moment a game
> ended, everything they'd learned about how *you* played evaporated. Start a new table
> against the same Blackbeard and his read of you reset to zero. The strange part, when I
> finally dug in, was that the data wasn't actually being thrown away — the per-game
> stats persisted fine. They just never added up across games. Fixing that turned out
> to have almost nothing to do with a smarter model, and almost everything to do with a
> lifetime table, an idempotent fold, and being careful about the order you charge
> someone's chips.

## Open gaps (need the founder or more reporting)

- **Live vs. unit-tested:** the captain's log repeatedly flags that the grind gate and
  the fold were verified by HTTP/integration tests but **not watched firing during a
  real human-played 25+-hand session**. Confirm with Jeff whether a live playthrough
  has happened since, or frame the post as "verified by wire test, confident by
  inspection."
- **Production status / flag state:** is `DOSSIER_SCOUTING_GATE_ENABLED` on in prod, and
  did the backfill ("can you backfill data for dossiers as much as possible?") run? The
  log says backfill was deferred (idempotent fold doubles as backfill). Confirm before
  claiming it's live for real players.
- **Coach integration framing:** `CROSS_SESSION_OPPONENTS.md` describes the coach
  consuming historical data ("You've played 3 sessions against Blackbeard..."). Confirm the
  dossier-grind lifetime store and the coach's `load_cross_session_opponent_models` are
  the *same* pipeline or two related ones, so the post doesn't conflate them. (INFERRED
  related, not verified identical.)
- **Numbers to re-verify at write time:** the 25→180 schedule and the exact informant
  prices on the screenshot are first-pass tuning values and may have changed; re-check
  against current code/UI before printing them.

## Cross-links (within the series)

- **Origin post** (living personalities / "Added some confidence and attitude," 2023):
  the natural setup — memory is that same instinct extended across time. Open the post
  by referencing it.
- **Cash mode / "living economy" post:** the informant chip sink and the bank-pool
  recycling belong to that economy; cross-link beat 5.
- **The coach post** (if one exists in the Inside the Table track): cross-session
  opponent history is what lets the coach say "he's playing tighter than usual today."
- **A "wrong turns / build-in-public honesty" post:** the `toFixed`-at-25-hands crash
  and the deferred `relationship_states` migration are good material to share between
  this post and that one.
