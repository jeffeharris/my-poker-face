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
