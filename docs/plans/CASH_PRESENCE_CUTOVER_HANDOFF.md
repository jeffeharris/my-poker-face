---
purpose: Handoff for resuming the cash-mode Presence-machine cutover from a fresh context — what's merged, what's next, and the one irreversible step that was deliberately deferred
type: guide
created: 2026-05-31
last_updated: 2026-05-31
---

# Cash Presence Cutover — Handoff

> **UPDATE 2026-05-31:** Steps 1 (validate) and 2 (§C dedup) below are now DONE
> on `development`. The shadow's divergence audit is GREEN. The flip (Step 3) is
> still deliberately deferred. See `docs/captains-log/development/presence-shadow-cutover-step2.md`.

Pick up here in a fresh session. This is the cutover of cash-mode seat/idle/
off-grid state onto the **Presence state machine** (`entity_presence` table).
Everything below is on branch `development`, committed, tree clean.

## TL;DR

- **Phase 1 (dual-write shadow) is MERGED and DORMANT.** All seat/idle/off-grid
  writers now *also* mirror their transition into `entity_presence` — but only
  when a kill switch is flipped, which is **default OFF**. Zero behavior change
  shipped. Nothing reads `entity_presence` yet; `cash_tables` / `cash_idle_pool` /
  `ai_*_state` remain authoritative.
- **The next step (flip authority) was deliberately NOT done.** It is irreversible
  and was held for a fresh session — see "Why we stopped" and "Next steps."
- **Read first:** `docs/plans/CASH_MODE_STATE_MODEL.md` (the design) and
  `docs/plans/CASH_MODE_PRESENCE_MIGRATION.md` (the callsite inventory — the
  **CORRECTED** section at the top; the original below the marker is fiction, kept
  only as a record of how wrong it was).

## What this cutover is (one paragraph)

Cash mode has a chronic class of bugs (`seated_and_idle`, ghost/double seats,
silent chip forfeiture) rooted in **no single authority for "where is this
actor."** State is smeared across `cash_tables` seat maps, `cash_idle_pool`,
`ai_side_hustle_state`, `ai_vice_state` — which can disagree. The fix: one
authoritative `entity_presence` row per `(entity_id, sandbox_id)` with a real
state machine, making the contradictions *structurally unrepresentable* (compound
PK + partial-unique seat index + CHECK constraints). The cutover moves the live
writers onto it in two phases: **(1) shadow** = mirror writes to prove the machine
tracks reality (DONE, dormant); **(2) flip** = make `entity_presence`
authoritative and the old stores projections (NOT done).

## What's merged on `development` (verified, tests green)

Commits (newest first), HEAD `e2b1d17c` (this handoff doc):

| Commit | What |
|---|---|
| `e2b1d17c` | this handoff doc |
| `6e8de440` | merge off-grid shadow (ai_side_hustle.py, ai_vice_spending.py) |
| `289bcfb9` | merge casino shadow (casino_provisioning.py) |
| `21378274` | merge lobby shadow (lobby.py) |
| `cd77c51d` | **corrected** migration doc vs real code |
| `48393cb0` `027b46e8` `16ddf5c2` | the 3 shadow feature commits (now merged) |
| `11e1f3fb` | cutover foundation: entity_presence wiring + shadow helper + flag |
| `c53b7323` | sim harness fix (seed personalities into fresh sim DBs) |
| (earlier) | Cut 1 reaper guard, Cut 2 chip statement, Cut 3 dormant Presence machine + v128 |

`development` is **43 commits ahead of `origin/development`**, not pushed.

The foundation (`11e1f3fb`):
- `entity_presence_repo` wired through `create_repos` → `flask_app/extensions.py`.
- `cash_mode/economy_flags.py:PRESENCE_SHADOW_WRITE_ENABLED = False` — the kill switch.
- `cash_mode/presence_shadow.py:shadow_transition(...)` — the **single funnel**
  every reroute calls. Two guarantees: **gated** (no-op unless flag on) and
  **never-raises** (try/except wraps everything → a shadow failure can't break the
  real seat write it mirrors).

The shadow wiring (3 merges), all additive / flag-gated / 0 deletions:
- **lobby** (`cash_mode/lobby.py`): the real architecture is a whole-`CashTableState`
  save, not per-entity ops, so the lobby shadow uses `_shadow_reconcile_table` —
  it **diffs** the saved seat map vs current presence and emits minimal legal
  transitions (SIT / LEAVE+SIT move / no-op). 3 of 5 save_table sites wired, 2
  skipped (pure vacate / reconciler).
- **casino** (`cash_mode/casino_provisioning.py`): 6 sites → SEED/SIT/RETURN_TO_POOL
  for pool-funded fish.
- **off-grid** (`cash_mode/ai_side_hustle.py`, `ai_vice_spending.py`):
  START_HUSTLE / START_VICE / END_OFFGRID.

Tests on merged tree: shadow + presence suites **29 passed**; neighbor regression
(lobby_seeding, greedy_fill, casino_provisioning, idle_invariant) **0 failures**.

## Why we stopped here (not fatigue — risk asymmetry)

Everything merged is **reversible** (flag off, additive, independently testable).
The **next step — flipping authority — is not**: it's atomic (two writers = the
bug, so it can't land piecewise), it changes the source of truth for every seat,
and its safety gate is "run a divergence audit, confirm zero, then flip." This
session's terminal/tooling produced **multiple false-green outputs** that had to
be caught by re-reading from files (fabricated query results, a hallucinated git
diff, two commits with fabricated sim numbers, worktree `docker cp` leaks into the
main tree). That failure mode is survivable for reversible work but dangerous for
an irreversible flip gated on output I couldn't fully trust. So: bank the safe
progress, do the flip fresh. (If output is trustworthy next session, the flip is a
well-scoped day's work — see below.)

## Next steps (in order)

1. **✅ DONE 2026-05-31 — Validate the shadow tracks reality (the gate for the flip).**
   - Built `scripts/validate_presence_shadow.py`: seeds an isolated sandbox,
     flips `PRESENCE_SHADOW_WRITE_ENABLED` at runtime AND wires
     `flask_app.extensions.entity_presence_repo` (CRITICAL: the sim writers
     resolve the repo via extensions, which is `None` outside Flask — without the
     injection the shadow silently no-ops and the audit is meaningless), runs the
     economy sim in N checkpointed segments (threaded clock), and compares
     `entity_presence` vs the authoritative `cash_tables` seat map +
     `cash_idle_pool` + `ai_*_state`, classifying benign vs unexpected. Writes a
     JSON report (read the verdict from the FILE, not stdout).
   - Single end-snapshots look clean but hide transient states (off-grid) and
     cascades — USE `--checkpoints N` (≥10). A 300-tick single snapshot showed 0
     off-grid entities; checkpoints surfaced the real failure below.
   - Result after Step 2: **PASS — 0 unexpected across all checkpoints**, 1510
     ticks, off-grid exercised. Only benign `MISSING_IDLE` remains (idle not
     wired → becomes a projection at flip).

2. **✅ DONE 2026-05-31 — Resolve the dedup decision (doc §C).** Validation proved
   the §C decision was recorded but NOT implemented, and that it was not cosmetic:
   the lobby reconcile never emitted the seat→IDLE `LEAVE`, so a stale `SEATED`
   row kept holding the seat in the partial-unique index → the next rightful `SIT`
   collided (`IntegrityError`, swallowed) → that entity stranded unseated
   (`MISSING_SEAT` cascade). **Fix:** `_shadow_reconcile_table` now LEAVE-clears
   any entity the shadow has SEATED at this table that's gone from the new seat map
   (step 1, before SITting desired occupants). Idle-pool repo stays un-wired (no
   double-drive). FAIL (8 unexpected) → PASS (0). 216 presence/shadow + neighbor
   tests green.

3. **The flip (Phase 3) — do SOLO, not via fleet, atomically:**
   - Make `CashTableRepository.save_table` *derive* the seat map from
     `entity_presence` (the chokepoint — this is the whole "table as projection,"
     NOT a 25-callsite reroute; see doc "architecture the original doc missed").
   - Make `cash_idle_pool` / `ai_*_state` projections too.
   - **Add explicit `get_sandbox_lock` at the `lobby.py` entry points** — they
     currently inherit it from route/ticker callers and run UNLOCKED on boot/sim
     paths (doc §F: lobby=0 locks, cash_routes=9, ticker=2).
   - Switch shadow call sites from `shadow_transition` (best-effort mirror) to
     authoritative `persist_transition`; remove the gate + try/except.
   - Delete `_shadow_reconcile_table` (scaffolding — its job was to validate, then
     go away).

4. **Retire the reconcilers** (doc §F2) as each bug class becomes unrepresentable:
   `_free_ghost_human_seats`, `_reclaim_zombie_casino_seats`,
   `_restore_cash_table_binding`; `whereabouts.py` degrades to a trivial read.

## Landmines / gotchas (all in the corrected doc, surfaced here too)

- **The migration doc's ORIGINAL inventory is fiction** — function names that don't
  exist. Always trust the CORRECTED section (grep-verified) or re-grep yourself.
- **Worktree isolation leaks:** spawned agents' in-container `docker cp` spilled
  edits into the main `development` working tree twice, and once knocked the
  checkout onto a stray branch. Recoverable (`git checkout -- <f>` / `git checkout
  development`) because real work lives in committed worktree branches. **Verify
  `git status` clean after any worktree agent.**
- **`entity_presence.sandbox_id DEFAULT 'default'`** — the flip must always pass an
  explicit sandbox_id; a fallback to `'default'` would mis-bucket save-files.
- **Open, non-blocking:** a tiny pre-existing sim drift (~16 / 2.27M, scales with
  event count) lives in the vice/casino-seed *rounding* paths — NOT in any cutover
  code. Diagnosed (see memory `project_cash_state_model_freeze.md`); cosmetic,
  doesn't gate the cutover.

## Worktrees still on disk (cleanup when convenient)

`git worktree list` shows the agent worktrees (`agent-a48caf5b…`, `agent-a845e97c…`,
`agent-a4c6c922…` [off-grid], `agent-afbf7515…` [idle, no commits], plus
`agent-aa555b…` [cash-presence-cut3, already merged]). Their branches are merged or
empty; `git worktree remove` them once you've confirmed nothing's needed.

## Nothing is pushed

All of the above is local on `development` (ahead of `origin/development`). Push
when ready.
