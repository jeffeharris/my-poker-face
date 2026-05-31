---
purpose: Design for how multi-table tournaments surface inside circuit (cash/career) mode — discovery, cadence, the career-bankroll buy-in bridge, and the world-ticker integration — without breaking the player-gated, single-player, isolated-chip model
type: design
created: 2026-05-30
last_updated: 2026-05-31
status: DRAFT — time model resolved (2026-05-31); minor open items remain. No code written. Depends on P2 economy + the cash state model.
---

# Tournament Circuit Surfacing

## 1. Why this exists

Tournaments are built but **disconnected**: they live in a standalone Tournaments
menu (`TournamentMenu.tsx` "Main Event" → `/tournament` lobby), on an isolated
chip universe, reached by a different route than circuit/cash mode. The vision
(`MULTI_TABLE_TOURNAMENT_PLAN.md` Phase 5) is that tournaments become a **recurring
event in the circuit a player can plan around** — but none of the surfacing
mechanics are decided. This doc pins them down.

The plan deliberately deferred this ("resist pulling circuit forward") and bet that
it can be a *thin* layer over two existing seams: a `create_tournament(config)`
entry point and the world-ticker / `lobby:{owner_id}` broadcast bus that already
reaches the player in both the lobby and in-game. This doc tests that bet and fills
in the product decisions.

## 2. The reconciliation problem (the crux)

Three already-locked facts collide and have to be reconciled before any UX:

- **Circuit time is presence-gated + continuous.** The cash world ticks while you
  are present (60s presence TTL) and pauses when you leave.
- **Tournament time is player-gated + discrete.** Nothing in a tournament advances
  except when *you* play a hand; backing out to standings freezes the whole field.
- **Cash mode is single-player per sandbox** (state model D6). There is no shared
  wall clock to synchronize a "daily 8pm tournament" against — there's one human.

The resolution (decided 2026-05-31) is that there **is no separate tournament
clock** — the tournament rides the existing **world tick**:

> **When the human is NOT in the tournament, it advances at the same tick pace as
> the rest of the world — it's just another active element in the sandbox. When the
> human IS in it, the sandbox's ticks pause and the tournament becomes player-gated
> by the human's hands.** The AI field runs with or without the human (P2 autonomy);
> the only thing that changes on entry is *what drives the clock* (world tick →
> the human's hands).

That dissolves the "no shared clock" problem (there's nothing to synchronize) and
unifies surfacing + economy + autonomy + the single-player model. The circuit
periodically spawns an autonomous Main Event (the economy's redistribution
heartbeat); its surfacing is a **registration window** measured in world ticks —
buy in from your career bankroll, or ignore it and it runs AI-only and
redistributes chips anyway. Everything below follows from it.

## 3. Decisions (recommendations — flagged for sign-off)

### Q1 — Discovery / entry model
**Recommendation: a Main Event *card* in the cash lobby + a ticker announcement,
additive to (not replacing) the standalone menu.**

- The cash lobby (`/api/cash/lobby` → `Lobby.tsx`) gains a **Main Event card** when
  one is in its registration window or running: prize pool, entrants so far, your
  buy-in cost, time/▒hands left to register, a Register button.
- The world ticker announces the lifecycle ("Main Event open — 12 entered",
  "registration closing", "down to the final table", "X won the Main Event for $Y").
- The standalone `/tournament` lobby + the "Main Event (Beta)" menu entry **stay**
  as a direct/dev entry and for players not in a circuit sandbox.

*Rejected:* a separate top-level "Tournaments" home card (the home menu already
calls cash mode "The Circuit"; a sibling card fragments the surface). The tournament
belongs *inside* the circuit, announced by it.

### Q2 — Cadence / time model
**Decided (2026-05-31): the tournament runs on the world tick.** When the human is
not in it, it advances at the same tick pace as everything else in the sandbox.
When the human enters, the sandbox's ticks pause and it becomes player-gated (§Q5).

There is no separate tournament clock to synchronize. So:

- **Spawn cadence** and the **registration window** are measured in **world ticks**
  — a cooldown of T ticks after one resolves, then a window of W ticks before the AI
  field fires. A scheduler (the P3 wrapper over `create_tournament`) owns the rhythm.
- During the window (and after, while the human stays out), the field advances on
  world ticks; the player buys in during the window or it runs without them.

- **Open (minor):** one event-tier at a time, or a small slate (a cheap "daily" + an
  occasional bigger "Main Event")? Recommend **start with one**, tier later.

### Q3 — The career-bankroll buy-in bridge
**Recommendation: buy-in/​payout cross the real career bankroll via the unified
ledger; the tournament stack stays isolated funny-money. Social context carries in,
chips do not.** (This is the `TOURNAMENT_ECONOMY_ON_STATE_MODEL.md` contract.)

- **Entry:** a confirm modal (pool, your bankroll, buy-in). On confirm:
  affordability gate (402 if short), debit career bankroll → `tournament:<id>`
  escrow (a `record_transfer`, drift-invisible), seat the human in the field.
- **Identity/social carries in:** the human plays under their career identity;
  relationship/prestige context travels (read-only for v1). Chips do **not** — the
  tournament is a `field_size × starting_stack` isolated universe.
- **Exit:** final placement → real chips back to the career bankroll
  (`tournament:<id>` → `player:<id>` transfer), as an **I6 idempotent terminal
  transition**. Busting just means a 0 payout if out of the money.
- **Career bankroll is global** (one row per `player_id`, NOT sandbox-scoped), so the
  bridge is clean across the tournament's ephemeral sandbox and the circuit sandbox.

### Q4 — World-ticker surfacing (the broadcast bus)
**Recommendation: tournament beats ride the existing `lobby:{owner_id}` world
ticker as new event types — no plumbing change.**

We already built the beat vocabulary (`tournament/beats.py`: knockout, table_break,
bubble, milestone, level_up) and the toast surface. For circuit, the same beats —
*plus* lifecycle beats (open / closing / winner) — become `world_event` types the
cash ticker renders. Two contexts:

- **You're grinding cash, a Main Event runs in the background (autonomous):** the
  ticker narrates it like any world drama ("Main Event: final table",
  "Blackbeard wins the Main Event, +50k"). The winner's chips cycling back is
  *visible economy drama* — the redistribution heartbeat you can watch.
- **You entered:** you're at the felt; the toast surface (already shipped) covers
  your live event; the cash ticker is paused (you're not present there).

Discipline (carried from the plan): the ticker shows **only top-drama** tournament
beats (breaks / bubble / final table / winner), never every hand — the same
"structural only" filter the toasts already use.

### Q5 — Time when you enter (pause the sandbox's ticks)
**Decided (2026-05-31): entering the Main Event pauses the sandbox's world ticks;
the tournament is player-gated by the human while they're in it. Exit resumes ticks.**

Entering flips the tournament's clock from world-tick (Q2) to **player-gated** —
nothing in that sandbox ticks except via the human's own hands. Concretely:

- The **cash tables freeze** (state model D4: seat held, chips `AT_TABLE` in the
  ledger, durable) — they don't tick because the sandbox is paused.
- The **other tournament tables** advance only when the human plays a hand at theirs
  (the existing `TournamentSession` player-gated path), so the field stays in step
  with the human instead of sprinting on ticks.
- On bust/finish the human returns to the circuit (payout applied) and the sandbox's
  ticks resume where they left off.

No new mechanism — it's "pause the sandbox tick" + the player-gated session path that
already exist. The only integration point is the switch: *human enters ⇒ stop ticking
this sandbox and drive the tournament off the human's hands; human exits ⇒ resume.*

## 4. Worked player journey

1. Grinding a $2 cash table. Ticker: **"Main Event opens — register (12 in, $480
   pool)."** A Main Event card appears in the lobby strip.
2. Tap Register → confirm modal (buy-in $40, bankroll $1,310, pool/structure). Confirm
   → bankroll debited to escrow, seated in the field. Cash table **freezes**.
3. Drop onto the tournament felt; play your table. Structural toasts narrate the
   field ("Table 4 broke", "blinds up next hand", "bubble burst"). Back out to
   standings → whole field pauses (player-gated).
4. You bust 9th of 18, in the money. Payout → **+$95 to career bankroll**. Routed
   back to the circuit; your frozen $2 cash table resumes.
5. (Or you never registered.) You keep grinding; the ticker narrates the autonomous
   event: **"…Lady Macbeth takes the Main Event, +$210."** Those chips re-enter the
   cash economy as she sits back down — the thermostat at work.

## 5. Integration surface (reuse, mostly built)

| Need | Existing system | Change |
|---|---|---|
| Spawn | `create_tournament(config)` / tournament registry | P3 scheduler wrapper (cooldown-gated) |
| Discovery | cash lobby `/api/cash/lobby` + `Lobby.tsx` | add a Main Event card to the payload + UI |
| Drama | world ticker (`ticker_service.py`, `lobby:{owner_id}`, `cash_mode/activity.py`) | add tournament lifecycle event types |
| Buy-in/payout | unified ledger + `tournament:<id>` escrow (economy note) | the confirm-modal flow + affordability gate |
| Advance the field | world ticker (out) / `TournamentSession` player-gated (in) | switch driver on enter/exit; pause the sandbox's ticks while in |
| Freeze on entry | cash state model freeze (D4) — cash tables freeze when the sandbox pauses | route "enter tournament" through the pause-ticks path |
| Live event UI | `/tournament` lobby/standings + shipped toasts | reached from the lobby card instead of the standalone menu |
| Identity/social carry | career bankroll (global) + relationship context | read-only carry-in for v1 |

## 6. Open questions / sign-offs

- **RESOLVED (2026-05-31) — time model.** The tournament rides the world tick when
  the human is out (Q2); entering pauses the sandbox's ticks and it becomes
  player-gated (Q5). No separate clock; no presence-minutes / cash-hands distinction.
- **RESOLVED (2026-05-31) — compute cost.** Advancing a background tournament is
  negligible next to running the live world with ~14 tables active; it rides the
  existing world-tick budget, no special batching needed.
- **Event slate (minor)** — one event-tier at a time vs a cheap "daily" + an
  occasional bigger "Main Event". Recommend start with one, tier later.
- **Social carry-out (prestige/relationship deltas from results)** — P4; out of scope
  here, but the winner-beat is the natural hook.
- **Sandbox (confirm).** The Main Event spins its **own ephemeral tournament sandbox**
  (isolated chips), bridged to the global career bankroll only at buy-in/payout — so
  "pause the sandbox's ticks" on entry (Q5) cleanly freezes the tournament without
  touching the player's cash sandbox, and conservation stays clean.

## 7. Sequencing / dependencies

This is **P3**, and it is gated:

1. **P2 economy** (buy-in/payout/escrow on the unified ledger + the EconomyChairman)
   — the buy-in bridge (Q3) and the redistribution drama (Q4) are not real without it.
2. **Cash state model freeze (D4)** — Q5 (enter-freezes-your-table) rides it.
3. **Then P3 surfacing:** the scheduler wrapper, the lobby card, the ticker event
   types, the confirm-modal entry. Mostly assembly over the table above.

Do **not** build the surfacing before the economy bridge exists — an entry that
doesn't move real chips is a demo, not the circuit event this describes.

## Related

- `MULTI_TABLE_TOURNAMENT_PLAN.md` — Phase 5 (circuit) + the broadcast-bus / thin-layer bets.
- `MULTI_TABLE_TOURNAMENT_P2_ECONOMY.md` — autonomy, buy-in, redistribution.
- `TOURNAMENT_ECONOMY_ON_STATE_MODEL.md` — the ledger/escrow/chairman substrate this rides.
- `CASH_MODE_STATE_MODEL.md` — freeze model (D4), single-player (D6), unified ledger.
