---
purpose: Grounded narrative log of adding a seat-reservation hold so the sponsorship-decision window can't be raced by AI live-fill ("cut by the AI at the casino")
type: reference
created: 2026-05-29
last_updated: 2026-05-29
---

# Captain's log — sponsorship seat-hold (development worktree)

Honest record of fixing "I get cut by the AI on spots at the casino" —
when you tap a seat you can only afford via sponsorship, the AI ticker
grabs it while you're picking a lender. Newest at the bottom.

---

## 2026-05-29 — reserving the seat through the SponsorModal window

**Report.** Player taps an open cash seat they can't self-fund. The
backend returns `402 requires_sponsor` and the SponsorModal opens. While
they read the offers, the seat gets filled by an AI; accepting the
sponsor then fails with "Seat is not open" (409). They get cut.

**The race, traced.** `/api/cash/sit` returns the 402 *without claiming
the seat* — it only signals "you're sponsor-eligible." The lobby's world
ticker (`_process_global_greedy_fills`, reached from `GET
/api/cash/lobby`) live-fills any `"open"` seat with an idle/eligible AI.
So between the 402 and the eventual `/api/cash/sponsor-and-sit` claim,
the seat is genuinely open and fair game. The per-sandbox seat lock that
both claim paths hold doesn't help — nothing holds it across the
modal window.

**Design choice: a distinct `"reserved"` kind, not reused `"human"`.**
The seat model only had open/ai/human. Reusing `"human"` for the hold
would have meant the smallest blast radius (the ticker already skips
non-open seats, the frontend already renders human), but it collides
head-on with the ghost-seat bug class in this codebase: a TTL sweep that
reclaims abandoned holds couldn't tell a hold from a genuinely seated
player, and a human seat with no game is exactly the "ghost me" symptom.
A dedicated `"reserved"` kind lets the expiry sweep target holds
*precisely* and never risk evicting a real player. The cost was touching
the seat-kind validator and ~30 `kind`-branching readers — but the fan-
out showed almost all of them correctly treat non-open/non-ai seats as
"skip" (movement, whereabouts, holdings) or "occupied" (dealer button,
fill exclusion), so the real edits were few.

**What shipped.**
- `cash_mode/tables.py`: `reserved_slot(owner_id, now)` (stamps
  `expire_at` = now + `SEAT_RESERVATION_TTL_SECONDS`, 120s),
  `is_reservation_expired`, `reserved_seat_index_for`, validator +
  docstring.
- `/api/cash/sit` 402 path: reserve the seat under the sandbox lock
  before returning; echo `table_id`/`seat_index`. Re-check open under
  the lock → clean 409 if an AI already took it between tap and reserve.
- `/api/cash/sponsor-and-sit`: accept a seat the caller already holds
  (`reserved` + their owner_id) instead of demanding `"open"`; still
  409s on a hold owned by someone else. The in-lock
  `_free_ghost_human_seats` sweep converts their own hold back to open
  right before the human claim.
- `_free_ghost_human_seats`: broadened to also free the owner's stale
  `reserved` holds (so tap-A-then-tap-B can't strand seat A).
- New `POST /api/cash/release-seat`: frees the caller's own hold;
  idempotent no-op otherwise. Frontend calls it on SponsorModal close.
- `refresh_unseated_tables`: TTL sweep at the top of the refresh frees
  abandoned holds (closed tab / dropped network) back to `"open"`.
- Lobby payload renders a `reserved` hold as the player's `"human"`
  seat (cash tables are per-sandbox, so the only viewer is the holder) —
  zero frontend type change, and it reads as "you're holding this seat."

**Why no false Resume bar.** Confirmed `has_active_session` /
`seated_table_id` are game-row driven (`_find_active_cash_game_id` +
`cash_session_repo`), and `human_seat_index()` reads the stored kind
(`"reserved"` ≠ `"human"`), so a hold never fabricates a session. The
reserved→human mapping is render-only.

**Tests.** New: data-model (slot shape, validator, expiry true/false,
malformed-expiry-as-expired, `reserved_seat_index_for`), route (402
reserves; release frees + idempotent; release leaves another player's
hold alone; re-tap frees the prior hold), sponsor (accepts own hold;
rejects another's), refresh (expired hold swept, fresh hold survives).
Full cash suite green (920 passed); tsc + eslint clean.

**Loose ends, honestly.** If `sponsor-and-sit` claims the hold then the
game build fails, the rollback reopens the seat — re-exposing it to the
ticker during the player's retry. That's a pre-existing, now-narrower
window I chose not to over-engineer. The 120s TTL is a guess at "long
enough to read offers, short enough not to strand a seat"; no telemetry
behind it yet.
