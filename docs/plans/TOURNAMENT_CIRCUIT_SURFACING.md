---
purpose: Design for how multi-table tournaments surface inside circuit (cash/career) mode — discovery, cadence, the career-bankroll buy-in bridge, and the world-ticker integration — without breaking the player-gated, single-player, isolated-chip model
type: design
created: 2026-05-30
last_updated: 2026-05-30
status: DRAFT — open questions flagged for sign-off; no code written. Depends on P2 economy + the cash state model.
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

So "daily circuit tournament" **cannot mean a synchronized real-world clock.** It
must mean a *player-relative availability rhythm*. The unlock that makes this work
is already in the P2 economy design: **a tournament is autonomous — the AI field
runs with or without the human.** That reframes the whole surface:

> **The circuit periodically spawns an autonomous Main Event (the economy's
> redistribution heartbeat). Its surfacing is a registration *window*: the player
> can buy in from their career bankroll, or ignore it and it runs AI-only and
> redistributes chips anyway. The "event you plan around" is the registration
> window, not a wall clock.**

This single move unifies surfacing + economy + autonomy + the single-player model.
Everything below follows from it.

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

### Q2 — Cadence / who spawns it
**Recommendation: an availability *rhythm*, not a clock — a cooldown-gated
autonomous spawn, framed as "Today's Main Event."**

Because there's no shared clock, "daily" is a cooldown: after a Main Event
resolves, the next becomes available some interval later (a real-time cooldown,
e.g. ~hours, and/or after the player has grinded N cash hands — TBD, sim-tunable).
A scheduler (the P3 wrapper over `create_tournament`) spawns it; a **registration
window** (a countdown in hands or minutes-while-present) precedes the AI field
firing. The player buys in during the window or it runs without them.

- **Open (sign-off):** is the registration window measured in *minutes-of-presence*
  (uses the presence beacon) or in *cash hands played* (more player-gated-consistent)?
  Recommend **hands-or-timeout** (whichever first) so an idle lobby still resolves.
- **Open:** one event-tier at a time, or a small slate (a $2-equivalent "daily" + an
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

### Q5 — Presence / time when you enter (the freeze)
**Recommendation: entering the Main Event freezes your cash table; you return to it
on exit. Reuses the state model's freeze-forever (D4) exactly.**

Entering a tournament = leaving the cash felt. Your cash table **freezes** mid-state
(seat held, chips `AT_TABLE` in the ledger, durable), the ambient cash world pauses
(you're not present), and tournament time becomes player-gated. On bust/finish you
return to the circuit, payout applied, and resume the frozen cash table where you
left it. No new mechanism — it's the freeze model the cash state work is already
building.

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
| Freeze on entry | cash state model freeze (D4) | route "enter tournament" through the same leave/freeze path |
| Live event UI | `/tournament` lobby/standings + shipped toasts | reached from the lobby card instead of the standalone menu |
| Identity/social carry | career bankroll (global) + relationship context | read-only carry-in for v1 |

## 6. Open questions / sign-offs

- **Q2 cadence units** — registration window in minutes-of-presence vs cash-hands
  (recommend hands-or-timeout). And one event vs a tiered slate (recommend one first).
- **Autonomous-event visibility cost** — narrating a background tournament means the
  AI-only field must *advance* while the player grinds cash. That is real compute on
  the (already busy) ticker thread; needs a pacing/batch budget (cf. the hands-off
  sim finding in EXP_006 — AI hands are ~100% of tick cost). **May need the
  autonomous field to advance in coarse batches, not hand-by-hand**, off the hot path.
- **Social carry-out (prestige/relationship deltas from results)** — P4; out of scope
  here, but the winner-beat is the natural hook.
- **Does the Main Event consume the player's cash sandbox or spin its own?** Recommend
  its **own ephemeral tournament sandbox** (isolated chips), bridged to the global
  career bankroll only at buy-in/payout — keeps the cash sandbox's conservation clean.

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
