---
purpose: Surface which named table/room the player is seated at, in-game and in the lobby, with a light "arrival" moment on sit-down
type: design
created: 2026-05-26
last_updated: 2026-05-26
---

# Cash Mode — Table Identity ("Where am I?")

## Status (2026-05-26)

**Built** on `career-mode-v0_1` (uncommitted). #1 + #3 are live; #2's pieces
are in place but gated by a reachability caveat (below).

- **Keystone (§1)** — `cash_table_name` stamped at both seat-in paths +
  cold-load; `build_cash_mode_payload` returns `table_id`/`table_name`. ✅
- **#1 header chip (§2)** — desktop `GameHeader` (`room · stake · hand · phase`)
  **and** mobile `GameInfoDisplay` (room name, leading). ✅
- **#2 lobby pin (§3)** — `seated_table_id` on `/api/cash/lobby`; `TableCard`
  `is-seated` variant ("You're here" + inline Resume; open seats on your own
  table resume instead of double-sitting). ✅ **now reachable — see §6.**
- **#3 arrival card (§4)** — `ArrivalWelcome` (centered auto-fading welcome
  card, portaled to body, non-blocking) + `arrivalSubtitle` (real-clock band),
  mounted in `ResponsiveGameLayout`. ✅ *(was a corner toast; upgraded to a
  centered card after seeing it live — see §5.)*
- **Nav / reachability (§6)** — auto-redirect dropped; lobby is an
  always-browsable hub with a persistent Resume bar; back routes to origin
  menu. ✅
- Tests: backend `test_cash_mode_payload.py` (3), frontend `arrival.test.ts`
  (11 band/weekday cases). TS clean, lint clean (2 pre-existing warnings).

## Problem

The lobby names every table — *The Back Room*, *Murphy's Bar*, *The Lodge*,
*High Roller Pit* (see `cash_mode/lobby_config.py`). But the moment the player
sits down, that identity vanishes. In-hand they see only the **stake tier**
(`$50`) and the blinds; the specific room they're in is gone.

This matters more in career mode than in plain cash, because a room is not a
label — per `CASH_MODE_CAREER_PROGRESSION.md` a room is **a place you were
vouched into**. "Where am I" and "what did I earn access to" are the same
question. Surfacing the table reinforces belonging, not just orientation.

The backend already knows the answer (`cash_table_id` is stashed in `game_data`
at seat-in); it simply isn't sent to the game view or echoed back in the lobby.

## Scope

Three features, smallest to richest. All three hang off **one** piece of data
plumbing (§1).

1. **Location chip** in the in-game header — the room becomes your primary
   identity line. *(MVP, fixes a real gap today.)*
2. **Lobby "you are here" pin** — the lobby card for your current table reads as
   a marker on a map; clicking it resumes the game. *(MVP complement.)*
3. **Arrival moment** — a light beat on sit-down: *"Murphy's Bar — Tuesday
   night."* The day/time is **real local time at sit-down**, so the world feels
   live. *(Characterful; see open decision §5.)*

### Explicitly out of scope (discarded)

- **Breadcrumb / back-nav** (`Lobby › $50 › The Lodge`) — defer until there are
  many revealed-by-vouch tables per tier and navigation actually strains.
- **Earned-status marker on the name** (prestige pip / "vouched by Napoleon") —
  belongs to the vouch/prestige layer; revisit when that ships
  (`CASH_MODE_TABLE_ATTRACTIVENESS.md`, occupant prestige).

---

## 1. The keystone: table identity in the game-state payload

Everything below depends on `table_id` + `table_name` reaching the frontend.

**Backend**

- At seat-in (`flask_app/routes/cash_routes.py:1323`, alongside
  `game_data["cash_table_id"] = table_id`) and on the cold-load restore path
  (`:4000`, where the table is already loaded at `:4007`), also stash the
  resolved name: `game_data["cash_table_name"] = table.name`. Stashing at
  seat-time avoids a per-frame DB lookup in the hot path.
- In `build_cash_mode_payload` (`flask_app/handlers/game_handler.py:707`) add to
  the returned dict:
  ```python
  'table_id': current_game_data.get('cash_table_id'),
  'table_name': current_game_data.get('cash_table_name'),
  ```
  Both nullable — a table seeded before this change (or any edge where the name
  wasn't stashed) degrades gracefully to "no chip / use stake label."

**Frontend types** (`react/react/src/types/game.ts`, `CashModeInfo`)

```ts
table_id?: string;
table_name?: string;
```

The lobby wire type (`react/react/src/components/cash/types.ts`, `LobbyTable`)
already carries `table_id` and `table_name?` — no change needed there.

---

## 2. Feature #1 — Location chip in the header

**Where:** `react/react/src/components/game/GameHeader/GameHeader.tsx`, center
section (lines 44–54).

**What:** Prepend the room as the leftmost info item, making it the anchor;
stake/blinds become secondary.

```
The Lodge · $50 · Hand #12 · Pre-Flop
```

- Add an optional prop, e.g. `location?: { tableName?: string; stakeLabel?: string }`,
  threaded from `GamePage` off `gameState.cash_mode`.
- Render the room name only when present (tournament games and pre-change cash
  sessions render exactly as today — no chip).
- Keep the existing `·` separator style. The stake label (`$50`) is a useful
  addition next to blinds since the header currently shows neither stake nor
  room.

**Effort:** small. One payload field consumed, one component edit + CSS.

---

## 3. Feature #2 — Lobby "you are here" pin

**Where:** `react/react/src/components/cash/Lobby.tsx` +
`react/react/src/components/cash/TableCard.tsx`.

**Backend:** The `/api/cash/lobby` response should expose the player's currently
seated table. The endpoint already reads the active game's `cash_table_id`
(`cash_routes.py:4779`); surface it as a top-level field on the lobby payload:

```python
"seated_table_id": active_game.get("cash_table_id") if active_game else None,
```

(Fall back to the persisted `cash_sessions` row if no live `active_game`, same
source the cold-load path uses at `:4000`.)

**Frontend:** `Lobby` passes `seated_table_id` down; `TableCard` renders a
"You're seated here" state when `table.table_id === seated_table_id`:

- Distinct affordance (e.g. a highlighted border + a small "You're here" badge),
  replacing the usual `Sit`/`Full` CTA with **Resume**.
- Clicking resumes the in-progress game rather than starting a new sit-down.

**Effort:** small–medium. One lobby field + a card variant. The lobby becomes a
map with a pin, closing the loop with the header chip.

---

## 4. Feature #3 — Arrival moment

**Trigger:** fires when the game view first binds to a cash table (or when
`cash_mode.table_id` changes), i.e. a real sit-down.

**Content:** `"{table_name} — {day} {timeband}"`, e.g. *"Murphy's Bar — Tuesday
night."*

**Day/time derivation — "just the actual night":** computed **client-side** from
the player's local clock at sit-down. Cheap (no backend, no new field), and it's
genuinely *their* Tuesday night.

```ts
// react/react/src/utils/arrival.ts (new)
function arrivalSubtitle(now = new Date()): string {
  const day = now.toLocaleDateString(undefined, { weekday: 'long' }); // "Tuesday"
  const h = now.getHours();
  const band =
    h < 5  ? 'late night' :
    h < 12 ? 'morning'    :
    h < 17 ? 'afternoon'  :
    h < 21 ? 'evening'    : 'night';
  return `${day} ${band}`;            // "Tuesday evening"
}
```

**Charming wrinkle (intentional):** a table literally named *"Saturday Home
Game"* showing *"…Tuesday night"* — the *game* is called the Saturday game, but
you wandered in on a Tuesday. Keep it. It's flavor, not a contradiction.

**Presentation (built):** a brief **centered welcome card** — `ArrivalWelcome`,
portaled to `<body>` — not a corner toast. Auto-fades in/out in ~2.5s via CSS
(no button); **non-blocking** (backdrop `pointer-events:none` so the live table
behind stays clickable; tapping the card dismisses early). Shows room name +
real-clock subtitle. Fires on every table_id transition (fresh sit / resume /
cold-load), deduped per table by a ref.

---

## 5. Arrival weight — DECIDED (revised)

| Option | What | Frequency |
|---|---|---|
| ~~A. Light toast~~ | ~~Corner toast via `react-hot-toast`.~~ Tried first; the corner notification didn't read as "arrival." | every sit-down |
| **B. Welcome card** ✅ | **Centered auto-fading card** (name + time). Cinematic "you walked into a place" beat, but *non-blocking + auto-dismiss* so it doesn't grate. | every fresh sit-down |

**DECIDED: B, as an auto-fading centered card** (not the tap-to-enter modal — a
blocking step on every sit-down would grate; auto-fade keeps it a moment). Name
+ time only for now. The richer "who's at the table" variant and *fire-only-on-
fresh-sit* gating are easy follow-ups if replays-on-resume ever annoy.

> Superseded the initial light-toast pick after seeing it live — the top-right
> toast read as a notification, not an arrival. Implemented as `ArrivalWelcome`
> + kept `arrivalSubtitle` (the toast hook `useArrivalToast` was removed).

---

## Build sequence

1. **Plumbing (§1)** — stash `cash_table_name`, extend payload + TS types.
   *Blocks everything.*
2. **#1 header chip (§2)** — smallest visible win; validates the payload end to
   end.
3. **#2 lobby pin (§3)** — `seated_table_id` + `TableCard` Resume variant.
4. **#3 arrival toast (§4, option A)** — only after §5 is confirmed.

#1 + #2 are independently shippable as the orientation MVP; #3 can follow.

---

## 6. Nav / reachability — making the lobby a browsable hub

#2's pin is only useful if the player can actually *see* the lobby while seated.
`Lobby.tsx` historically redirected straight into the active game on mount (via
`/api/cash/state`), so a seated player never saw the lobby.

### Prior art on `development` (`7ce61038`) — and why it's wonky

That commit made the lobby reachable via a **one-shot `skipResume` flag** in
router state (set by the in-game back button) plus a detached top "Resume
session" banner. Three problems:

1. **Transient flag.** `skipResume` lives only in router state — refresh the
   lobby, or reach `/cash` any other way, and the mount effect bounces you
   straight back into the game. You can't reliably *stay*.
2. **Doesn't answer the ticket.** The banner is detached from the grid; the
   seated table renders as an ordinary "Sit" card, so it never shows *which*
   table you're at, and tapping your own open seat tries to start a second sit.
3. **Inconsistent / two sources of truth.** Resume only appears via the back
   button; the banner and the seated-session concept are wired separately.

### Chosen approach — drop the gate, make resume part of the table

- **Remove the forced auto-redirect entirely.** The lobby is always browsable.
  Audit of every `/cash` entry point: the redirect only ever "helped" the
  **Career** menu button (bounced a seated player into their game); everywhere
  else you reach `/cash` only *after* the session ended. So dropping it changes
  exactly one behavior — Career while seated lands on the hub (table pinned,
  resume one tap away) instead of teleporting into the hand. That's the desired
  hub behavior.
- **`seated_table_id` (from the lobby poll) is the single source of truth.** It
  drives both the card pin and a **persistent Resume bar** near the top — shown
  whenever a live session exists (not a one-shot flag), so it survives refresh
  and is identical from every entry point.
- **Resume lives where you'd look:** the pinned table card's Resume button +
  the bar. Open seats on your own table resume (you can't sit twice).
- **Keep `development`'s good half:** back routes to origin menu (career →
  `/cash`, tournament → `/menu/tournament`) — no `skipResume` needed now.
- **Bar/pin/card-Resume are all blue** (vs green "new sit"), so "your live
  session" reads as one concept.

Net vs `7ce61038`: no transient flag, survives refresh, consistent everywhere,
and it shows *what table you're at* instead of a generic resume.

> **Merge note:** `development` will carry `7ce61038` (skipResume + banner).
> When these branches meet, take *this* version: drop `skipResume` and the
> detached banner; keep the always-on bar + seated pin + back-to-origin routing.

## Testing notes

- Backend: extend the cash game-state payload test to assert `table_id` /
  `table_name` appear for a seated cash session and are absent/None for a
  tournament game. Cover the cold-load path (name resolved from the loaded
  table). See `cash_cold_load` tests + the double-seat recurrence guard.
- Frontend: `GameHeader` renders the chip only when `tableName` is present;
  `TableCard` shows the Resume/here state iff `table_id === seated_table_id`.
- `arrivalSubtitle` is a pure function — unit test the band boundaries
  (04:59→late night, 05:00→morning, 21:00→night).
- TS: `python3 scripts/test.py --ts`.

## Open questions

- **#3 weight** (§5) — toast vs card. *Blocking for #3 only.*

## Decided

- **Header chip is not a lobby link.** No tap-to-pop-lobby. The chip's
  interactivity is reserved for a future **hand replay / hand-info** surface
  (the natural "click the room to see what just happened here"), not navigation
  back to the lobby. Build it as plain text for now; wire the click later when
  replay/hand-info exists.
