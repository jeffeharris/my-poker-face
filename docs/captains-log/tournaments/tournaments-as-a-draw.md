---
purpose: Grounded narrative log of building "tournaments as a draw" — AIs pulled off cash tables into the Main Event (branch tournaments, phases A–D)
type: reference
created: 2026-06-03
last_updated: 2026-06-03
---

<!-- newest entries at the bottom -->

# Captain's log — tournaments as a draw (tournaments worktree)

Honest record of building the cash→tournament migration: AI personas LEAVE cash
tables to enter a Main Event, pulled by an attractiveness/"draw" model, and the
tournament redistributes bank reserves. Phase A (the safe vacate primitive) and
the design doc landed in a prior session; this log covers picking that up cold
and carrying it through B–D. Wrong turns and corrections kept in. Newest at the
bottom.

---

## 2026-06-03 — the "OOM" that wasn't

Picked up a handoff that said the dev backend kept OOM-dying (exit 137) and had
blocked verifying Phase B2. First real lesson of the session: **it wasn't OOM.**
The `tournaments` worktree was simply missing its `docker-compose.override.yml`
(gitignored, per-worktree). Without it, `docker compose up` runs the *stale baked
image* — which (a) omits recently-added `cash_mode/` modules (the container died
with `ModuleNotFoundError: cash_mode.presence`, exit 1) and (b) omits `tests/`
entirely (`.dockerignore` excludes them). I chased a `presence` import error, a
`tests/` "file not found", and the `.dockerignore` before realizing the sibling
worktrees all have an override that bind-mounts `.:/app` live and pins a distinct
/24 subnet. Recreated it (subnet .48); the backend came up healthy with no
rebuild. The prior session's "OOM" was almost certainly the same missing-mount
symptom misread. Saved it as a reusable note — this will bite again.

With a real container, B2 verified clean (schema v148, `reserved_pids` /
`vacated_pids` columns present on a fresh build, 30 tests green) and got pushed.

## 2026-06-03 — B3: the effectful wiring, and a fail-closed scare

Built B3 (the draw scorer wired into the invite lifecycle) through the
`feature-dev` flow: explore the repos, ask the two genuinely-underspecified
questions (how to read the `ego` trait without the `times_used` write side-effect
→ a new side-effect-free batch reader; what proxy for `cash_comfort` with no
net-winnings signal → seat-stack depth), then implement. The shape that fell out
cleanly: a single `DrawContext` bundle threaded as ONE optional param so
`offer()` didn't sprout five repo arguments, and everything gated behind
`TOURNAMENT_DRAW_ENABLED` (default off) so production is byte-for-byte unchanged.

The code review caught a real one: `reserved_pids_for_owner` did a bare
`SELECT reserved_pids …`, and because B3 wires that scan into `draft_exclusions`
on *every* spawn (not just flag-on), a DB lagging the v148 migration would throw
`OperationalError` → wrapped `DraftScanError` → **abort every tournament spawn.**
In this branch B2 ships with B3 so it can't actually happen, but the fix was
cheap and correct (a PRAGMA column guard, matching the existing `_row_to_dict`
pattern), so I took it. Two other review findings I pushed back on and left as
deliberate (the binary `field_top_renown` is field-relative by design; refusing
the draw when bankroll is unwired is safer than a degenerate all-prize ranking).

## 2026-06-03 — C: "all three sites" was two, and vacate ≠ stays-vacated

Phase C is the riskiest seam (it touches the live world tick, the ledger, and the
double-presence/ghost-seat bug class the project keeps re-fighting). Two
corrections to the plan as written:

1. The plan said thread `called_up_pids` into "all three" roster-refresh
   call-sites. There are **two** (`lobby.py` background + the human's hand-boundary
   refresh in `game_handler`); the "rejoin"/`_refill` paths are seat-*fill*, not
   refresh. Worth verifying rather than trusting the count — missing a site is the
   whole risk.
2. The Phase-A `called_up` primitive only forces *seated* personas to LEAVE; it
   does nothing to stop the next fill from *re-seating* them. So a naive "thread
   the vacate in" would have the world vacate a reservation and then immediately
   re-seat it. The real fix was the other half: exclude reserved pids from the
   fill candidate pools too (the lobby's `unavailable` set + all three
   `game_handler` seat-fill paths via a `_tournament_bound_pids` helper).

Two product calls from the user shaped the rest: vacate from **both** sites
(including the human's live table), and **no early AI-only spawn** — the human
keeps their full registration window; their seat is held and AI-filled at expiry.
That second decision quietly collapsed the planned "spawn-when-gathered" work
(the existing `expire_due` path already spawns the gathered field), and it forced
a subtle invariant: only gather a reservation when the invite has an `expires_at`,
or a vacated persona could be stranded with no spawn to absorb it. The review
found no functional bugs — only observability gaps that *follow from* the
no-early-spawn choice (`vacated_pids` is now bookkeeping, not a spawn gate), which
I documented in code so they don't read as latent bugs later.

## 2026-06-03 — D: learning the renown model before touching it

Phase D (grant renown on a tournament win, feeding it back into the draw) was
mostly a matter of *not* fighting the existing prestige model. Renown-v2 is an
append-only snapshot history whose "value" is `MAX(renown_v2)` at read time, with
a periodic 5-minute recompute and **no immediate-grant seam** — so a one-off
grant is just a new row at `peak + bump`, and `load_latest` lets me clone the
finisher's quadrant/regard so the grant doesn't reset the rest of their
scoreboard. I hung it inside `apply_payout_on_complete` (the single idempotent
`claim_payout` once-block all three payout paths funnel through) in its own
try/except, so a renown failure can never strand a fully-paid escrow.

The honest tension the review surfaced: the `reconcile_stuck_payout` watchdog
(crash-recovery) doesn't grant renown. Adding it there would risk a *double*-bump
on the crash-after-grant window — strictly worse than a rare one-off skip for a
cosmetic, ratcheted value. I chose the skip and documented it. The review also
caught that I'd imported `paid_places_for` from `tournament.session` while the
actual payout schedule uses `tournament.economy`'s — identical today, but they'd
silently diverge if the in-the-money fraction is ever tuned, so renown's "paid
places" now reads from the same source as who gets chips.

## 2026-06-03 — where it stands

All of A–D is built, committed, pushed, and code-reviewed phase-by-phase — and
**every line is flag-gated off** (`TOURNAMENT_DRAW_ENABLED`, plus
`RENOWN_V2_PERSIST_AI` for the renown feedback to actually reach the draw). The
full loop exists on paper and in tests: AIs are scored and reserved → trickle off
cash → spawn the field → winning grants renown → that renown re-weights the next
draw. What it has **not** had is a single run with the flag on. The next honest
step isn't more code — it's a sim to tune the four draw weights against a real
persona pool (scaffolded as EXP_007), then a hands-on playtest, *then* the flag.
Resisting the urge to flip it early is the discipline here; a redistribution
mechanism that pulls the wrong personas (or the identical cast every time) would
be worse than no draw at all.
