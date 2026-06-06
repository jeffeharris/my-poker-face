---
purpose: Build plan for Career M2 — the real, emergent vouch_ready (respect-gated, likability-driven) over the relationship graph, evaluated on the world ticker
type: spec
created: 2026-06-04
last_updated: 2026-06-06
---

# Career M2 — emergent vouches (`vouch_ready`)

Design canon: `CASH_MODE_CAREER_PROGRESSION.md` § "The vouch model" + § "M2".
M1 (handoff: `CASH_MODE_CAREER_M1_HANDOFF.md`) shipped the **scripted** first
vouch (Sal reveals a random room on graduation). M2 makes vouches **emerge from
relationships**: anyone you've actually played with, who respects you enough and
likes you enough, opens the door to *their* room. Without it the world dead-ends
after the first vouch.

> **Shipped 2026-06-06 (flag-gated `CAREER_VOUCH_ENABLED`, default OFF).** The
> reveal target changed from this plan's original "the voucher's **current
> room**" to the voucher's **home table** — the lobby room where that AI has
> played the **most hands** (≥ 50), resolved at fire time. This needed a new
> per-`(sandbox, ai, table)` counter (`ai_table_hand_counts`, schema **v153**,
> on `RelationshipRepository`: `increment_ai_table_hands` / `resolve_home_table`),
> incremented once per AI per hand in **both** the lobby sim (`full_sim`) and
> live games (`game_handler`). The ticker resolver is `_resolve_ai_home_table`
> (replacing `_resolve_ai_room`). A vouch fires only once an AI has an
> **established home** (no current-seat fallback). Sections below that say
> "current room / current table" are superseded by this. Tests:
> `tests/test_cash_mode/test_ai_home_table.py` + `tests/test_ticker_service.py`.

## Step 0 — verification (handoff's required first step): DONE

The handoff said *verify the regard-edge instrumentation exists before tuning.*
Findings (live dev DB, `guest_jeff`):

- **The relationship graph exists and is populated for the human.** Edges live in
  `relationship_states` (per `observer_id → opponent_id`: `heat, respect,
  likability`, heat projected on read). Repo:
  `poker/repositories/relationship_repository.py`.
- **The human is keyed by `owner_id`** (`guest_jeff`) — AI→human edges use it as
  `opponent_id`.
- **AI→human edges ARE written during play.** Observed:
  `sal_moretti → guest_jeff` = respect **0.9** / likability **0.88** (the warm
  mentor seed + play), `loose_larry → guest_jeff` = heat 0.98 but respect/like
  **0.0 / 0.0**.
- **The inbound-regard read M2 needs already exists:**
  `RelationshipRepository.load_inbound_relationships(opponent_id)` → every
  `(observer → opponent)` edge with heat projected. Call it with `owner_id`.

**Tuning caveat (the real open risk, per the design):** Larry (a fish, lots of
play) sits at 0.0/0.0 respect+likability — only **heat** moved. So *random* play
does **not** obviously push respect/likability to ~0.70; the design already
accounts for this — **vouchers start warm** (mentors + home-court regulars seed a
high baseline), and play closes the *last gap*. So M2's vouch candidates are the
**warm-seeded regulars you click with**, not cold fish. Build the mechanism with
the spec'd thresholds; treat threshold tuning + "does play move respect/like
enough" as a follow-on measured from the ticker (see Instrumentation below).

## The model (from canon)

```
vouch_ready(ai → human):
    has_played_with(ai, human)              # PREREQ: an edge exists between them
    not already_vouched(ai)                 # PREREQ: one vouch per AI (v1)
    respect(ai → human)    ≥ RESPECT_FLOOR  # GATE
    likability(ai → human) ≥ LIKE_THRESHOLD # DRIVER (≈ 0.70)
    # eagerness scales with likability above the threshold
```

Two clean failure modes (opposite the prestige axis): **respected but cold** =
feared not invited; **liked but disrespected** = won't sponsor a donkey upward.

## Build

### 1. `vouch_ready` predicate (pure) — `cash_mode/career_progression.py`
- Constants: `RESPECT_FLOOR` (gate) and `LIKE_THRESHOLD ≈ 0.70` (driver).
- `vouch_ready(*, respect, likability, played_with, already_vouched) -> bool`
  and an `vouch_eagerness(likability) -> float` (scales above threshold) so the
  ticker can pick the *most* eager when several qualify.
- `has_played_with` = an edge exists in the inbound set (interaction created it).
  (Optionally require heat/any-axis > 0 to exclude pure-seed-no-play edges; v1:
  edge-exists is enough since seeded regulars are intended candidates.)
- `already_vouched` = `ai_id in career_progress.vouched_by` (field already exists;
  Sal is appended there by the scripted first vouch).

### 2. General `fire_vouch` — `cash_mode/career_progression.py`
Generalize the M1 `fire_first_vouch` (which reveals a *random* room) into an
**emergent** vouch that reveals the **voucher's own current room**:
- Resolve the AI's current table: scan `cash_tables` for this sandbox for a seat
  whose `personality_id == ai_id` (`seats_json`), take that `table_id`. (No seat →
  skip; they're between rooms.)
- If the room is already revealed → skip (no-op, don't spend the vouch).
- Add `table_id` to `revealed_table_ids`, append `ai_id` to `vouched_by`, persist.
- Build + `record_event` a `EVENT_VOUCH` LobbyEvent (reuse `format_vouch_message`),
  return it so the ticker emits `world_event` to the lobby room.
- Keep `fire_first_vouch` intact (Sal's scripted, random-room first door); factor
  the shared reveal/emit into a helper both call.

### 3. Evaluate on the world ticker — `flask_app/services/ticker_service.py`
Per active sandbox tick (the ticker already loops active sandboxes):
- Gate: career player only — `career_active and tutorial_complete` (no vouches
  mid-tutorial). Flag-gated by `CAREER_VOUCH_ENABLED` (default OFF) so it ships
  dark.
- Read `load_inbound_relationships(owner_id)`; filter to AIs that are
  `vouch_ready` and not already vouched.
- **Slow growth:** fire **at most one** vouch per sandbox per tick (the most eager
  by `vouch_eagerness`); the world blooms a room at a time, not in a burst.
- Emit the returned `world_event` like the existing ticker events.

### 4. Instrumentation (feeds tuning)
Log each AI→human edge crossing (or near-missing) the thresholds when the ticker
evaluates — `respect`, `likability`, `played`, `vouched?` — so we can answer "does
normal play reach ~0.70 like / clear the respect floor in reasonable time" from
real sessions instead of guesses. Cheap `logger.info` per evaluated candidate,
gated behind the same flag.

## Tests (`tests/test_cash_career_*` / `test_career_progression.py`)
- **Gating:** disrespected (respect < floor) → no vouch even at high likability;
  liked + respected → vouch; likability < threshold → no vouch; not-played-with →
  no vouch.
- **One-per-AI:** an AI in `vouched_by` never vouches again.
- **Reveal target:** the revealed room is the **voucher's current table**, added to
  `revealed_table_ids`; an already-revealed room is a no-op (vouch not spent).
- **Eagerness ordering:** with several ready, the most-liked fires first.
- **Ticker slice:** one vouch per tick; gated off when `tutorial_complete` is False
  or the flag is off.

## Risks / watch-items
- **Tuning (primary):** thresholds may be unreachable by play for non-seeded AIs.
  Mitigated by warm-seeded regulars being the intended candidates + the
  instrumentation. Don't lower the respect gate to force blooms — that breaks the
  "feared ≠ invited" thematic.
- **Room churn:** an AI's room can change between ticks; resolve the table at fire
  time, skip if seatless. Revealing a room that then empties is fine (it's a real
  lobby table; the world refills it).
- **One-per-AI durability:** `vouched_by` is persisted on `career_progress`; the
  ticker must reload-modify-save atomically per sandbox (mirror `fire_first_vouch`).
- **Flag discipline:** `CAREER_VOUCH_ENABLED` default OFF; a custom (non-career)
  sandbox never evaluates (gated on `career_active`).

## Why M2 before the Dream Table
The dream table (`CASH_MODE_DREAM_TABLE.md`) rides the vouch/graduation seam and
the broader promise that *relationships open doors*. Shipping emergent vouches
first means the dream table lands in a world where vouching actually works, not a
one-off scripted reveal.

## File pointers
- Predicate + `fire_vouch` + shared reveal helper: `cash_mode/career_progression.py`
  (alongside `fire_first_vouch`, `pick_home_court`, `SAL_ID`, `vouched_by`).
- Inbound regard read: `poker/repositories/relationship_repository.py`
  (`load_inbound_relationships`).
- Ticker hook: `flask_app/services/ticker_service.py` (per-sandbox loop).
- AI current room: `cash_mode/tables.py` (`seats_from_json`) over `cash_tables`.
- Career state: `poker/repositories/career_progress_repository.py`
  (`revealed_table_ids`, `vouched_by`).
