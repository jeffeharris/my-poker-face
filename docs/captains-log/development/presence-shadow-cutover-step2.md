---
purpose: Grounded narrative log of validating the Presence dual-write shadow and finding/fixing the §C seat→IDLE dedup gap (cutover Step 1+2)
type: reference
created: 2026-05-31
last_updated: 2026-05-31
---

# Captain's log — Presence shadow validation + §C dedup fix (development)

Honest record of picking up the cash-mode Presence cutover from the
`CASH_PRESENCE_CUTOVER_HANDOFF.md` and doing the next two steps: validate the
dormant dual-write shadow against reality (the gate for the irreversible flip),
then close the gap that validation surfaced. Newest at the bottom.

---

## 2026-05-31 — building the divergence audit

**Where we picked up.** Phase 1 (dual-write shadow) was merged and dormant
(flag `PRESENCE_SHADOW_WRITE_ENABLED` default OFF). The handoff's Step 1 was:
flip the flag *in a sim only*, run a divergence audit comparing `entity_presence`
against the authoritative stores, and prove the machine tracks reality before
flipping authority.

**First real obstacle (a quiet one).** The shadow writers resolve the
`entity_presence` repo via `flask_app.extensions.entity_presence_repo` — which
is `None` outside the Flask app. A naive `--db-path` sim with the flag flipped
would have run *completely green and completely meaningless*: every shadow write
a silent no-op, the audit comparing an empty table against itself. So the
harness has to (a) flip `economy_flags.PRESENCE_SHADOW_WRITE_ENABLED` at
runtime and (b) inject `extensions.entity_presence_repo = repos[...]` so the
sim writers can actually reach the table. Caught this by reading the resolver,
not by trusting a first green.

**The harness** (`scripts/validate_presence_shadow.py`): seed an isolated
sandbox, turn the shadow on + wire the repo, run the economy sim, then compare
`entity_presence` against `cash_tables` seat map / `cash_idle_pool` /
`ai_side_hustle_state` / `ai_vice_state`, classifying every mismatch into
known-benign (documented gaps) vs unexpected (real wiring bugs). Writes a JSON
report so the verdict is read from a *file*, not from terminal stdout (last
session's false-greens make stdout untrustworthy for an irreversible gate).

**A 300-tick single snapshot looked great** — 49/52 MATCH, 3 STALE_SEAT, 0
unexpected — but it was an end-of-run snapshot and showed **zero** off-grid
entities. The shadow's off-grid wiring was effectively untested, and transient
contradictions would be invisible. So I added checkpointed auditing: run the sim
in N segments with a threaded clock, audit after each.

## 2026-05-31 — what the checkpoints found

1500 ticks / 10 checkpoints / seed 7 turned the verdict to **FAIL — 8
unexpected** with a new class: `MISSING_SEAT` (truth says an entity is SEATED;
presence says it's IDLE/POOL — a SIT that never landed).

**It wasn't random — it was a cascade.** Querying the leftover DB:

- `napoleon`: presence SEATED@(casino-2-001, seat 5), but truth = *idle* (left).
- `lucky_mona`: truth SEATED@(casino-2-001, seat 5) — the rightful new occupant
  — but presence = POOL (stranded).

i.e. napoleon left the seat, but the lobby reconcile never emitted his `LEAVE`,
so a **stale SEATED row kept holding seat 5 in the partial-unique index**. When
lucky_mona legitimately took the seat, her `SIT` hit `sqlite3.IntegrityError`,
which `shadow_transition` swallows by design — leaving her stranded unseated.
Same shape for queen_elizabeth_i blocking another fish. So `MISSING_SEAT` was a
*downstream symptom* of the `STALE_SEAT` gap, not an independent bug.

**Root cause = the unresolved §C dedup decision.** The lobby persists a whole
`CashTableState`; a vacated seat appears only as the entity's *absence* from the
new seat map, never as an event. The migration doc §C had already DECIDED the
seat→IDLE `LEAVE` should be emitted by the lobby reconcile-diff (which sees the
seat go empty) — but the implementation explicitly did NOT do it ("Vacated seats
are NOT turned into LEAVEs here"). The code contradicted the recorded decision.
Validation made the cost concrete: it's not cosmetic divergence, it actively
corrupts the shadow's seat index.

**The fix** (`cash_mode/lobby.py` `_shadow_reconcile_table`): before seating the
desired occupants, LEAVE every entity the shadow currently has SEATED at *this*
table that is no longer in the new seat map at the same seat. Clears stale rows
so rightful SITs don't collide. Cross-table moves stay handled per-entity in
step 2 (LEAVE-then-SIT against the source table). The idle-pool repo stays
deliberately un-wired (wiring it too would double-drive — §C).

**Re-validation** (same 1500/10/seed 7): **PASS — 0 unexpected at every
checkpoint.** `STALE_SEAT` and `MISSING_SEAT` both eliminated; `classes_ever_seen`
collapsed to `[MATCH, MISSING_IDLE]`. The residual `MISSING_IDLE` (an idle entity
with no presence row) is the documented-benign class — the idle pool isn't
independently shadow-wired and becomes a projection at the flip. Off-grid was
exercised and matched cleanly. All 216 presence/shadow + neighbor-regression
tests green.

**Why this matters for the flip.** The gate is now genuinely green for the seat
machine: no double-seat, no ghost-seat, no stale-seat survives a full sim. The
irreversible authority flip (table-as-projection) can proceed on a shadow that
demonstrably tracks reality — which is exactly the precondition the handoff held
the flip for. Step 1 (validate) and Step 2 (§C dedup) are done; the flip itself
remains deliberately deferred.

## 2026-05-31 — hardening for the human path (the AI-only blind spot)

Asked whether we were "ready to cut over." We were not — and the reason turned
out to be sharper than expected. The whole validation above was **AI-only** (the
economy sim has no human), and the user's original loss was a *human* buy-in. So
before building the flip I went to validate the human seat path.

**Two real gaps, found by reading the code instead of trusting "Phase 1 done":**

1. **The human path was never shadowed at all.** Phase 1 wired only AI writers
   (lobby seed/fill/burst, casino, off-grid). The human SIT/LEAVE in
   `cash_routes.py` — the literal seat write for a paying human — had zero shadow
   calls. AI-only sims could never have caught this.
2. **`_shadow_seat_state` didn't recognise a human slot.** `human_slot` stores the
   owner_id in `personality_id` (so the routing layer treats human + AI seats
   uniformly), but the shadow reader only checked `owner_id`/`player_id`/`user_id`
   → a seated human was silently dropped, so even a *wired* reconcile would give
   the human no presence row. Caught this by checking `human_slot`'s actual shape
   before writing the wiring on top of it — and the human-path test, run against
   the un-fixed reader, confirms it (human comes back OFFLINE → red).

**Design call: human cash-out is `GO_OFFLINE`, not `LEAVE`.** The lobby
reconcile-diff models a vacated seat as `LEAVE`→IDLE, which is right for an AI
(it rests in the idle pool). A human who leaves has *cashed out of the sandbox* —
design §5.1 reserves IDLE for the AI idle pool and OFFLINE for "human cashed
out." So the leave handler emits an explicit `GO_OFFLINE` for the human (and lets
the subsequent `refresh_unseated_tables` reconcile the freed AI seats). SIT reuses
the reconcile-diff, which also clears any stale occupant so the human can't be
stranded by the §C collision.

**Wired** (flag-gated, best-effort, zero behaviour change when off): SIT at the
self-funded + sponsored sit sites, `GO_OFFLINE` at leave, plus the
`_shadow_seat_state` human-key fix. **Validated** by `test_shadow_human.py` (7
tests: sit records a row, no double-seat, stale-occupant cleared, leave→OFFLINE,
cold-load-leave is a safe swallowed no-op, full sit→leave→re-sit→leave lifecycle
with no ghost). 167 shadow + cash-route regression tests green; the AI-only
divergence audit is unaffected (no humans in the sim, sim doesn't touch
`cash_routes`).

Net: Phase 1's human blind spot is closed and the human seat lifecycle is now a
durable regression gate for the flip — which stays deferred.

## 2026-05-31 — building the flip behind the flag (Steps 0–4, validated)

"We're good, lets make it happen." Built the Phase-3 flip mechanism — all behind
a new `PRESENCE_AUTHORITY_ENABLED` flag (env-gated, default off), so the running
dev backend is unaffected until the one-line cut. Reversible commits:
- **Step 0** schema v129: `cash_idle_metadata` satellite (idle reason/target_stake
  the pure machine won't carry); `cash_idle_pool` stays a written cache (view
  conversion deferred). (Hot-reload bit me: registered the migration before
  appending its body → backend crash-looped; lesson re-learned, append body
  first.)
- **Step 2** `cash_mode/presence_transitions.py`: the authoritative engine.
  Reconciles `entity_presence` to a saved `CashTableState` ON the caller's sqlite
  connection. Diff-driven origin (player→GO_OFFLINE, fish→RETURN_TO_POOL,
  AI→LEAVE+metadata) and — crucially — SIT-precursor promotion (seated-elsewhere→
  LEAVE, off-grid→END_OFFGRID, fresh fish→SEED) which fixes the exact illegal-and-
  swallowed transitions live shadowing surfaced. 11 unit tests.
- **Step 3** `save_table` calls the engine inside its transaction → presence +
  seats commit atomically. Single chokepoint writer.
- **Step 4** `is_enabled()` = shadow OR authority, so off-grid keeps mirroring
  post-flip and the legacy call-site reconciles become harmless redundant no-ops.

**Big simplification found mid-build:** the Phase-1 call-site shadow reconciles
all gate on the shadow flag, so at the flip (authority on, shadow off) they go
dormant automatically — `save_table`'s engine is the sole writer with no
conflict. So Steps 5/6 (lobby/route surgery) are NOT needed. Less code, less risk.

Also built **Step 1** `scripts/backfill_presence.py` (idempotent seed of
entity_presence from the legacy stores; dry-run on dev = 633 seated + 225 idle
across 12 sandboxes) and a `--authority` mode on the validator.

**Proof:** authority-mode sim (800 ticks, 8 checkpoints) → PASS, 0 unexpected,
classes [MATCH, MISSING_IDLE]. The chokepoint keeps presence consistent with the
legacy stores under real churn. Full cash_mode suite: 1069 passed. All pushed.

**Held:** the atomic cut (`PRESENCE_AUTHORITY_ENABLED=True`) is task #10 — pending
a pre-flip lock audit (live paths already hold get_sandbox_lock; boot/sim are
single-threaded), a real backfill run, and explicit go-ahead. Everything to here
is reversible (flag off ⇒ inert).

## 2026-05-31 — the flip, on dev (write-authority soak)

"Flip it." First, a review pass (feature-dev code-reviewer) found two real issues
I fixed before flipping: (1) `_apply` swallowed `IllegalPresenceTransition` even
in authority mode — now propagates (rolls back the save, fail-loud) under
authority; (2) the `is_enabled()=shadow-OR-authority` change had re-armed the
legacy call-site reconciles under authority with a TOCTOU window — now
`_shadow_reconcile_table` returns early under authority, so the chokepoint is the
unambiguous sole seat writer. Re-validated (authority sim PASS, 1071 tests).

Branch-impact check before flipping: all in-flight branches are flag-dormant (a
merge can't activate the flip); circuit-progression's live career seating uses
`save_table` (drives presence — the migration doc's "scripted seeding writes
cash_tables directly" worry is CLEARED); tournaments/training-room don't touch
cash seats. Only merge work is the usual schema renumber to 130+ and modest
lobby/schema conflicts.

Honest scope note I gave first: flipping the flag now delivers WRITE-authority
(presence writes fail-loud on conflict) but the app still READS `cash_tables` —
the read-side migration (retire reconcilers, read presence) is deferred. So this
is a write-path soak, reversible (cash_tables still written as a cache).

The flip, on dev, via env (reversible): quiesced presence writes → cleared +
re-backfilled `entity_presence` from the authoritative stores for a clean
baseline (638 seated + 221 idle / 12 sandboxes) → `PRESENCE_AUTHORITY_ENABLED=1`.
A pre-flip audit showed a transient MISSING_SEAT/STALE_SEAT — diagnosed as
TICKER-CHURN LAG (the world ticker moves `cash_tables` every 2s independent of
presence, so the backfill snapshot lagged), NOT a real contradiction. (Separately
confirmed the authoritative data is full of real pre-existing `seated_and_idle`
contradictions — blackbeard et al. in both a seat and the idle pool — the exact
class this kills; backfill seated-wins resolves them.) Post-flip + a few churn
cycles: **audit PASS, 0 unexpected (856 MATCH, benign MISSING_IDLE); no fail-loud
exceptions under real churn.** The authority engine reconciles presence in
lockstep with every save_table, so the lag self-healed. Committed default stays
OFF; only the running dev container has it on. NEXT: soak on dev (play + watch),
then the read-side migration (Step 8) for the full bug-class kill, then prod (the
truly irreversible step on real player data).

## 2026-05-31 — design pass for the flip + shadow armed on live dev

Asked to design the flip (leveraging feature-dev agents) and to "push and
shadow." Ran a code-explorer (territory map) + code-architect (blueprint) pass;
wrote `docs/plans/CASH_MODE_PRESENCE_PHASE3_FLIP.md`. The mechanism: promote the
proven shadow reconcile-diff into an authoritative write that runs INSIDE
`save_table`'s sqlite transaction (presence + seats commit together), with origin
derived from the departing slot (human→GO_OFFLINE, fish→RETURN_TO_POOL,
AI→LEAVE). One refinement over the raw architect output: keep `cash_idle_pool` a
written cache and DEFER the SQL-view conversion (their Step-0 view breaks every
`save_idle` writer at once and contradicts their own deferred `cash_tables`
demotion). New flag `PRESENCE_AUTHORITY_ENABLED` keeps everything reversible until
one atomic flip.

Pushed all 47 commits to origin/development (was never pushed). Then armed the
shadow on the dev backend: env-gated the flag (`_env_flag`, default still False so
prod is untouched), added a default-off pass-through to docker-compose.yml,
recreated the backend with `PRESENCE_SHADOW_WRITE_ENABLED=1`, verified
`is_enabled()` live. The world ticker only churns sandboxes with an ACTIVE human
(design D4), so on an idle dev box nothing populated — correct, not a bug. To get
a real-data signal anyway, mirrored the CURRENT seat state of all 12 dev sandboxes
(147 tables, 1 human + 51 fish seats) into `entity_presence` (presence-only
writes, `cash_tables` untouched) via the real reconcile, then ran a new READ-ONLY
auditor (`scripts/audit_presence_divergence.py`): **PASS, 0 unexpected** — 630
MATCH incl. the human + all fish, only benign MISSING_IDLE + one STALE_SEAT_GONE.
That's the first validation against real slot shapes the AI-only sim never made.
Shadow left armed so ongoing real play keeps populating it. The flip stays
unbuilt, pending review of the blueprint.
