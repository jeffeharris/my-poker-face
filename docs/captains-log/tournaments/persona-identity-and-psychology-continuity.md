---
purpose: Grounded narrative log of the persona-identity divergence fixes and cash-world psychology continuity (branch tournaments)
type: reference
created: 2026-06-03
last_updated: 2026-06-03
---

<!-- newest entries at the bottom -->

# Captain's log — persona identity & cash-world psychology continuity (tournaments worktree)

Honest record of a session that started as a one-line bug report ("my name shows
as `human:guest_jeff` on the Main Event") and unrolled into two scoped tech-debt
items (T3-76 identity, T3-77 psychology) plus a full implementation of the
second. Wrong turns and corrections kept in. Newest entries at the bottom.

---

## 2026-06-03 — a name bug that exposed a whole identity model

**The report was small; the cause was structural.** The user saw their seat
render as the raw field id `human:guest_jeff` on the Main Event felt, and the
player dossier flash the slug `james_bond` before resolving to "James Bond" —
neither happens at cash tables. I (with an Explore agent) traced it and found the
real divergence: **tournaments overload `Player.name` to carry the raw
`personality_id`** (the "MTT bridge" — the field/eliminations/payouts all key on
the id) and bolt the friendly label onto `Player.nickname`, whereas **cash names
the seat by the display name** and carries `personality_id` out-of-band. So cash
"just works" and tournaments depend on `nickname` being present everywhere.

Two concrete leaks:
- `restore_state_from_dict` serialized `nickname` but never restored it → every
  DB cold-load reverted the tournament seat to the raw id. (Cash immune: its
  `name` is already friendly.)
- The dossier title rendered `name` (the slug) until an async persona fetch
  resolved it → the flash. Cash immune for the same reason.

**Interim fixes, then scope the real one.** I shipped the two small fixes
(restore `nickname` on cold-load; seed the dossier title from `nickname`), then —
at the user's direction — scoped the proper unification (explicit
`Player.personality_id`, `name` = display in both modes) as **T3-76** with a
full blast-radius inventory rather than attempting the big refactor inline. The
scoping turned up that it's actually a *three-way* inconsistency (multi-table
keys on id, single-table on display name, cash on name-with-id-out-of-band) and
that the load-bearing risk is display-name collisions — which is exactly why the
fix must add an explicit id, not key on `name`.

**The bot-type-on-dashboard item.** The user then pointed at "bot types listed"
on the tournament dashboard. It was `seat.archetype` — the **headless-engine sim
strategy label** (`ARCHETYPES[entries[pid]]`), an internal AI-only-sim artifact,
not the persona's real playstyle or live bot type. Removed it (+ dead CSS).
Confirmed via `tournament_ticker.py`'s own comment that archetype ≠ display name.

**Polish-branch check came back empty.** The user asked whether `polish` already
fixed any of this. It didn't: the tournament files don't exist on `polish` (it's
the emotion-families lineage), and the two shared files there hold the *old*
unfixed versions. Net: nothing to pull, but a future merge to be aware of.

**Then the substantive question: does the tournament actually carry cash info?**
The user wanted to be sure career stats / emotions / personality carry over.
Verifying this is where the session got interesting. I traced the real-persona
seat builder and confirmed personality + psychology + dossier all wire up like
cash. But chasing "emotions carried over" surfaced the real gap, and I had to
**correct something I'd said earlier**: I'd implied live emotional state carries.
It doesn't. There are *two* psychology stores:
- `ai_bankroll_state.emotional_state_json` (per persona) — evolves off-screen,
  feeds the lobby card + the off-screen sim.
- per-game `psychology_json` — the live game's own state.

And they were **disconnected both ways on the felt**: live games built every
opponent at baseline (never read the persona blob), and never wrote their evolved
mood back (`save_emotional_state_json` existed only in `cash_mode/`, never in
`flask_app/`). So a lobby-tilted persona sat down calm, and a session that tilted
a persona was lost to the world. I scoped that as **T3-77**.

**A correction that improved the design.** My first scope said "cash two-way,
tournament read-only." The user corrected the framing: continuity follows the
**cash world**, not the table type — so a *cash-world (Circuit) tournament* should
be two-way like a cash table (chips still reset; mood is continuous), and only a
*non-cash* tournament starts at baseline. That's cleaner and it's what I built.
The gate fell out naturally: `real_persona_ids` / `tournament_is_persona_field`
(economy binding) rather than the `cash_mode` chip flag.

**Implementation, in stages.** Shared hook (`cash_mode/psychology_persistence.py`,
promoted verbatim from the sim so equivalence held) → live cash hydrate on
build + flush on leave → cash-world tournament hydrate on build + flush at
completion. Then closed the deferred refinements one at a time.

### Wrong turns / gotchas worth remembering
- **A merge landed mid-work.** While I was on the shared-module commit,
  `origin/development` + the polish PR got merged into `tournaments`. My
  uncommitted changes were swept into the merge commit instead of getting their
  own — the code's all there and tested, but Commit 1 has no clean message. I
  verified the earlier UI fixes survived (no conflict markers) and re-ran the
  bucket before continuing. Lesson: check `git status`/log before assuming a
  "nothing to commit" means failure.
- **I deferred things that were actually easy and more correct.** I parked
  "per-vacate flush" citing concurrency caution. On inspection the caution was
  unfounded (a *seated* persona is never simultaneously sim-played — the sim runs
  unseated tables), and the hook already existed (`_remove_departed_ais_from_game`
  with the live controller + name→pid map right there). Per-vacate is strictly
  *more* correct than the human-leave flush, which only catches whoever's still
  seated. Closed it.
- **Test stub bit me twice the same way.** The hydrate hook reads
  `controller.ai_player.personality_config`; my first reconcile-test stub had no
  `ai_player`, so hydration silently no-opped and the test failed. Real
  controllers chain into `AIPokerPlayer`, so the stub had to as well. Worth
  remembering the hook's implicit contract.
- **The user caught a genuine confusion and it was a good one.** They asked: "if
  energy wasn't recovering while idle, what *was* happening during recovery?" The
  honest answer untangled a real subtlety: recovery (`project_idle_energy`) *was*
  being computed — but only as a **re-seat gate** ("is this AI rested enough to
  return?"), the result thrown away, never written to the snapshot. And until
  this session, live games never read the snapshot at all. So "recovery" meant
  "we let rested AIs return," not "the stored mood improved." Connecting the
  snapshot to the felt (T3-77) is what made the frozen-snapshot problem real, and
  decay-on-read is what materializes the recovery the lobby was only estimating.

### Decisions made explicit (so future-me doesn't relitigate)
- **Projection-on-read beats continuous recovery.** Exact (closed-form), free
  while idle, downtime-proof (a continuous ticker would have to catch up = re-do
  projection anyway), and consistent with bankroll regen. Event-sourced at the
  boundaries (flush on leave), projected in between (recover on read).
- **Idle recovery is energy-only** — matches the lobby model. A nap doesn't
  un-tilt a persona or restore confidence; those carry as the last snapshot until
  live hands move them. Defensible, but a *choice*: if we ever want "sleep it off"
  cooldowns for tilt, that's a deliberate extension, not a bug.
- **Cold-load must never hydrate from the persona blob** — it restores the
  per-game `psychology_json`; hydrating there would clobber an in-progress
  session's evolved mood with a staler value. Hydrate only on a *fresh* seat.

**State at end of session.** T3-77 fully implemented + all three refinements
closed (balanced-in re-hydrate, per-vacate flush, decay-on-read); 18 new tests;
cash slice 343 / tournament bucket 287 / sim-psych 53 green; ruff clean. T3-76
(identity unification) remains *scoped only* — the principled fix is a real
refactor and was deliberately left for a dedicated pass; the user-visible leaks
it caused are patched. Scope docs:
`docs/triage/TOURNAMENT_SEAT_IDENTITY_MODEL.md`,
`docs/triage/PERSONA_PSYCHOLOGY_HYDRATION_HOOK.md`.
