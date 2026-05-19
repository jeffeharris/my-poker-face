---
purpose: Design for hand-driven AI movement at cash tables — leave-pressure score, rebuy-vs-leave split, sim-hand-based fill cadence, chat surfacing
type: design
created: 2026-05-19
last_updated: 2026-05-19

---

# Cash Mode — Movement Pressure Design

## Goal

Make the cash lobby feel alive. AIs should leave for legible reasons
(short stack, won big, detached, tired), seats should fill in
naturally between hands, and the player should see the table cycle
without it feeling like a slot machine.

Right now movement is a memoryless coin flip every hand: same state,
same probability, no accumulation. This doc replaces those flat
rolls with a per-AI **leave pressure score** that ticks up across
hands, plus a per-hand (not per-poll) fill cadence so empty seats
don't refill in seconds.

## What exists today

| Mechanism | Where | Behavior |
|---|---|---|
| `evaluate_ai_movement` | `cash_mode/movement.py:67` | Flat per-hand rolls: `forced_leave` at ≤0.3× buy-in, `stake_up` 30% / `take_break` 10% at ≥2× buy-in, `bored_move` 1.5% base. No memory across hands. |
| `refresh_table_roster` | `cash_mode/movement.py:206` | Applies movement to each seated AI, then live-fills opens at 15% per open seat per call. |
| Unseated tables refresh | `cash_mode/lobby.py:273 refresh_unseated_tables` | Runs on every `GET /api/cash/lobby` — simulates 0+ burst hands, then ONE `refresh_table_roster` call regardless of burst size. |
| Seated table refresh | `flask_app/handlers/game_handler.py:823 _refresh_lobby_table_for_session` | Runs after each real hand. Also one `refresh_table_roster` call per invocation. |
| Mid-game refill | `flask_app/handlers/game_handler.py:646 _refill_cash_seats` | Replaces *busted* AIs only (stack=0). Doesn't touch open seats. |
| Frontend polling | `react/react/src/components/cash/Lobby.tsx:43` | `LOBBY_REFRESH_INTERVAL_MS = 8000` — 8s polling on the lobby page. |
| Psych persistence | `cash_mode/full_sim.py:282 _apply_persisted_emotional_state` | Schema v97 — `ai_bankroll_state.emotional_state_json` follows an AI across tables. **Tenure-via-energy works for free.** |
| Detached zone | `poker/zone_detection.py:292-317` | Already classifies low-confidence + high-composure as detached. **No new counter needed.** |

## The fix in one paragraph

Replace the three static probability branches in `evaluate_ai_movement`
with a **leave pressure score** computed from stack position, detached
zone tenure, and energy. Move the live-fill roll **inside the per-hand
loop** so fills happen at the pace of hands, not at the pace of page
polls. When pressure triggers a short-stack departure event, **split
leave-vs-rebuy** weighted by bankroll and energy. Emit a chat
message on every join/leave so the player sees the table cycle.

## Pressure formula

Each hand, for each seated AI, compute:

```
pressure  = 0
pressure += w_stake_up   * max(0, stack / max_buy_in - 1.0)        # over max → ready to move up
pressure += w_short      * max(0, 1.0 - stack / min_buy_in)        # under min → leave or rebuy
pressure += w_detached   * (hands_in_detached_zone / 8)            # detached too long
pressure += w_tenure     * (1 - energy)                            # tired
roll = rng.random() < pressure / (pressure + LEAVE_K)
```

Notes:
- **Stake-up pressure scales against MAX buy-in** (user spec): an AI
  ≥1.5× max is more eager to book the win and move up.
- **Short-stack pressure scales against MIN buy-in** (user spec):
  pressure rises smoothly as stack drops below the table's min, not a
  binary cliff. `forced_leave` at ≤0.3× min remains as a hard floor.
- **Detached pressure** reads `controller.psychology.zone == 'detached'`
  and accumulates a per-AI `hands_in_detached_zone` counter. Resets on
  zone change. This replaces the old `bored_move` flat rate.
- **Tenure** uses the existing energy axis. No new state — psychology
  already depletes energy with extended play, and v97 persists it
  across table moves.
- `LEAVE_K` shapes the curve. With `LEAVE_K=2.0`, pressure of 1.0
  yields ~33% per-hand leave chance; pressure of 0.3 yields ~13%.
  Tunable.

**Weights (initial, tune via playtest):**

| Weight | Initial | Rationale |
|---|---|---|
| `w_stake_up` | 0.5 | Strong but capped — winning big should ramp leave/stake-up over a few hands. |
| `w_short` | 0.6 | Slightly stronger than stake_up — frustration leaves faster than satisfaction. |
| `w_detached` | 0.3 | Subtle; only meaningful after several detached hands. |
| `w_tenure` | 0.2 | Background drift. Should never dominate. |
| `LEAVE_K` | 2.0 | Shapes the asymptote. |

## Leave vs rebuy (short-stack only)

When the pressure roll fires AND the dominant factor is short-stack,
split between leave and rebuy:

```
leave_weight  = 1.0
              + w_energy_low    * (1 - energy)
              + w_bankroll_low  * (1 - min(1, bankroll / min_buy_in))

rebuy_weight  = 1.0
              + w_bankroll_high * min(1, bankroll / (min_buy_in * 3))
              + w_attachment    * recent_winning_signal
```

Then `weighted_random(leave_weight, rebuy_weight)`.

**Effect:** flush/engaged AIs top up and stay; tired/broke AIs leave.
Rebuy amount = min buy-in (simplest), debited from AI bankroll, applied
to the seat. Emits a chat event ("Napoleon rebought for $1,000").

This is a **new mechanic** — today AIs sit on a short stack until they
bust, then `forced_leave`. With rebuy in the loop, the chip economy
sees more circulation between AI bankrolls and table stacks.

## Direction split on non-short-stack leaves

When pressure fires and the dominant factor is **stake-up**:
- If a higher stake exists and bankroll affords its min: idle pool
  entry with `target_stake = next_tier` (directed move up).
- Otherwise: degrade to `take_break`.

When the dominant factor is **detached** or **tenure**:
- Idle pool entry with `target_stake = None` (undirected break).
- Lower probability of returning to the same table on the next
  live-fill roll (a small "cooldown" tag on the idle entry, e.g.
  `min_break_hands = 5`).

## Fill cadence — per hand, not per poll

**Today:** `refresh_unseated_tables` runs once per lobby poll (~8s).
Inside, an unseated table can play several burst hands (0-5),
then runs `refresh_table_roster` **once** at the end. Fill prob is
15% per open seat per refresh — so fills are tied to poll cadence,
not hands.

**Change:** move the live-fill roll into the per-hand loop in
`cash_mode/lobby.py:366`. Each simulated hand = one fill roll per open
seat. For seated tables, the hand-boundary hook already runs once
per real hand, so it falls out for free.

**Per-hand fill prob:** lower than today's 15% per-poll, since rolls
now compound across hands instead of polls. Initial: **0.05 per open
seat per hand**, with `defer_freshly_vacated_live_fill=True` so opens
sit empty for at least one hand. At a typical 4-AI table:

- 6-seat table, 4 AIs, 2 opens → 0.10 expected fills per hand.
- After ~10 hands without a leave → ~63% chance table is full.
- After a leave (deferred 1 hand) → ~5% chance next hand fills it
  back.

Feels like the rhythm of a live cash room. Cadence is naturally
rate-limited by hands played, not by how often the user refreshes.

## Chat surfacing

Joins and leaves should appear:

1. **Lobby activity ticker** — already exists via `cash_mode/activity.py`
   ring buffer. Existing events already include join/leave; no change
   needed.
2. **Seated player's in-game chat** — new. When the hand-boundary hook
   applies movement at the seated table, push a system chat message:
   - `"Napoleon left with $1,847"` (leave with current stack)
   - `"Napoleon rebought for $1,000"` (rebuy)
   - `"Elon sat down with $1,000"` (join, after one-hand deferral)
   - `"Taylor moved up to $1k/$2k"` (stake_up — phrased as departure
     plus destination)

Wire via the same socketio pipe used for AI commentary. Format aligns
with existing in-game system messages.

## Forced leave stays

`forced_leave` at ≤0.3× min buy-in remains as a hard floor — busted
AIs gone immediately, no rebuy roll. This is the existing chip-leak
guard; pressure-based rebuy only fires above the floor.

## Code touch points

| File | Change |
|---|---|
| `cash_mode/movement.py` | Rewrite `evaluate_ai_movement` to return pressure-driven decisions. Add `MovementContext` dataclass for the per-AI inputs (stack, min/max buy-in, bankroll, energy, detached_hands). Add `decide_leave_or_rebuy` helper. |
| `cash_mode/movement.py` | Move live-fill roll into per-hand caller — `refresh_table_roster` becomes thinner (just movement decisions); fill becomes its own helper called per-hand. |
| `cash_mode/lobby.py` | In `refresh_unseated_tables` burst loop, call the per-hand fill helper inside the `for _ in range(burst_n)` loop instead of one `refresh_table_roster` at the end. |
| `flask_app/handlers/game_handler.py` | In `_refresh_lobby_table_for_session`, call the per-hand fill helper (same one used by unseated path) — runs once per real hand. Plumb `controller.psychology.zone` and `energy` into the movement context. Emit chat events on movement decisions. |
| `flask_app/handlers/game_handler.py` | Add rebuy path: when `decide_leave_or_rebuy` returns "rebuy", debit AI bankroll, update seat chips, emit chat event. |
| `cash_mode/tables.py` | Optional: track `hands_in_detached_zone` somewhere. Cheapest: derive each hand from controller state at the hook, no persisted counter needed. Alternative: persist on the slot dict so unseated-table sim can use it too. |
| `flask_app/handlers/chat_handler.py` (or similar) | New: `emit_table_movement_event(game_id, kind, name, amount)` helper. |

## Resolved decisions

1. **Detached works on unseated tables too — no extra effort.**
   `cash_mode/full_sim.py:258-385` already builds full
   `TieredBotController` instances per sim hand with hydrated
   psychology. `controller.psychology.zone` is available on both
   paths. Per-hand: after `play_one_hand`, read each AI's zone and
   tick `hands_in_detached_zone` accordingly. State persists across
   sim hands via the existing `emotional_state_json` flush every 10
   hands.

2. **Rebuy amount is probabilistic, bucketed.** Three buckets, each
   AI rolls per rebuy event:

   | Bucket | Amount | Bias toward |
   |---|---|---|
   | `min` | `min_buy_in` | low bankroll, low energy, recent loss streak |
   | `mid` | `(min + max) / 2` | neutral state |
   | `max` | `max_buy_in` | high bankroll, high energy, on tilt-up |

   Weights computed from `bankroll`, `energy`, and `emotional_state`
   intensity. Crisp formula deferred to implementation — start with
   uniform-ish (40/40/20 default) and bias from there.

3. **Cooldown is hybrid: floor + variable.** `min_break_hands = 2` as
   the hard floor (no immediate re-seat at the same table). On top of
   that, an additional variable cooldown driven by:
   - **Money**: high bankroll → shorter cooldown (eager to play
     again); low bankroll → longer (needs regen time)
   - **Emotion/psych**: high tilt intensity → longer cooldown ("walked
     off frustrated"); calm/positive → shorter
   - **Tenure at last table**: long session → longer cooldown
     ("burned out")

   Computed as `cooldown_hands = 2 + round(0..8 * pressure_factor)`.

4. **5% per-open-seat per-hand fill rate confirmed.** Each hand at a
   table (sim or real) = one independent 5% roll per open seat. At a
   6-seat table with 2 opens, expected fills per hand = 0.10. Average
   ~10 hands between fills. With one-hand deferral on freshly-vacated
   seats, the natural rhythm becomes "AI leaves, seat empty for a
   beat, then probability slowly recruits a replacement."

5. **Individual messages, arrival order.** Locked.

## Acceptance criteria

- At a 4-AI table, with the player just sitting down, the first AI
  movement happens between hand 5 and hand 30 (typical).
- Big winners (≥1.5× max buy-in) leave within ~5-15 hands of hitting
  that threshold; rebuy never fires for them.
- Short-stack AIs (≤0.7× min) leave or rebuy within ~3-10 hands,
  weighted by bankroll/energy.
- Detached AIs (zone = detached) start showing leave pressure after
  ~6 hands in the zone.
- A seat that empties stays empty for at least 1 hand before live-fill
  considers it.
- Player sees join/leave chat messages in the in-game chat, not just
  the lobby ticker.
- No mass-fill events (table going from 2 AIs to 6 AIs in under 30s)
  unless ~30 hands have elapsed.
