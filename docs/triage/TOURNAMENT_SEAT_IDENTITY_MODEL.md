---
purpose: Scope the divergence between tournament and cash seat-identity models and the refactor to unify them
type: design
created: 2026-06-03
last_updated: 2026-06-03
---

# Tournament vs Cash Seat Identity — Divergence & Unification Scope

> **TRIAGE ref:** T3-80 (Tier 3, Code Organization; was T3-76 on-branch,
> renumbered on the development merge). Follow-on to T3-79 (tournament
> unification). **Owner-flagged design miss**, not a fresh bug.

## TL;DR

Tournament and cash mode store seat identity two different ways. The tournament
way overloads `Player.name` to carry the *economic* id (`personality_id`) and
bolts the human-readable label onto `Player.nickname`; cash names the seat by the
display name and carries `personality_id` out-of-band. The split leaks raw ids to
the UI (the felt showed `human:guest_jeff`, the dossier flashed `james_bond`) and
forces a `resolve_display_name` shim plus per-surface fallbacks that don't exist
in cash. The two models should be one.

## What the user saw

In the Main Event (tournament) live table, two things that never happen in cash:

1. The human seat rendered as the raw field id `human:guest_jeff` instead of a
   friendly name.
2. Opening a player dossier flashed the raw `personality_id` (`james_bond`)
   before an async fetch resolved it to "James Bond".

## Root cause — two identity conventions

**Cash** (`flask_app/routes/cash_routes.py:893-911`):

```python
pid = personality_repo.resolve_name_to_personality_id(player.name)   # name → id
memory_manager.initialize_for_player(player.name, personality_id=pid)
```
- `Player.name` = the **friendly display name** ("James Bond").
- `personality_id` is carried **out-of-band**, resolved per seat and passed
  explicitly to memory / dossier / relationships.
- The UI "just works": `name` is already human-readable, so
  `useDisplayNickname` (`overrides[name] || nickname || name`) shows a real name
  with no extra plumbing.

**Tournament** (`flask_app/handlers/tournament_game_builder.py:154-159, 231-236`):

```python
Player(name=s.player_id, nickname=resolve_display_name(s.player_id, ...))  # name IS the id
memory_manager.initialize_for_player(s.player_id, personality_id=s.player_id)
```
- `Player.name` = the **raw `personality_id`** ("james_bond") or human field id
  ("human:&lt;owner&gt;") — the "MTT bridge" convention.
- The friendly name is bolted on as `nickname`, resolved by
  `tournament/identity.py::resolve_display_name`.
- Display now depends on `nickname` being present *and* every surface honoring it.

### Why the tournament did it this way

It's a deliberate shortcut, not an accident. The tournament engine tracks the
**entire field** — across tables, eliminations, payouts, ticker, completion — by
`personality_id`. Making the live seat's `name` equal the field id means results
flow **back** to the field by identity, with no reverse lookup
(`tournament_completion.py:35`, `tournament_ticker.py:142`). Cash has no field to
reconcile against, so it was free to name seats by the display name. The
tournament traded clean display for a clean bridge.

### Note: even `nickname` means different things in the two modes

In a regular/cash game, `game_handler.py:593-598` sets the served `nickname` to
the persona's *short alias* from `personality_config`. In tournaments, `nickname`
is the *full display name*. So `useDisplayNickname` doesn't even resolve to the
same thing across modes — another symptom of the split.

## Interim fix already shipped (the cheap half of "Option B")

Two low-risk patches stopped the visible leak without touching the identity model:

- `poker/repositories/serialization.py` — `restore_state_from_dict` now restores
  `nickname`. `to_dict` serialized it but the cold-load path dropped it, so any
  DB reload reverted the tournament seat label to the raw id. (No-op for cash;
  its `name` is already friendly.)
- `react/react/src/components/character/dossierFromPlayer.ts` — the dossier title
  is now seeded from `player.nickname` before falling back to `player.name`, so
  it no longer flashes the slug before the persona fetch resolves.

**Still leaking after the interim fix:** the standings panel
(`TournamentStandings.tsx:150,168,211,278`) prints `player_id` directly because
the backend `standings_view` (`tournament/session.py:166-177,217-233`) doesn't
emit display names at all. That is the remaining Option-B item; it's a backend
view + component change, independent of the identity refactor below.

## Proposed solution — unify on an explicit identity field (Option A)

Stop overloading `Player.name`. Give `Player` an explicit, stable identity field
and make `name` the display name in **both** modes:

- Add `Player.personality_id: Optional[str]` (the stable economic key; `None`
  for the human seat, whose stable key is the `owner_id`).
- `Player.name` = the friendly display name **everywhere** (tournament and cash).
- All keying — field entries, eliminations, payouts, memory/dossier registration,
  live-result write-back, cold-load controller maps — uses `personality_id`
  (or `owner_id` for the human), **never** `name`.
- `resolve_display_name` / `resolve_display_names` collapse to a single
  build-time resolution; the per-surface `nickname` fallbacks and the standings
  shim disappear because `name` is always human-readable.

This matches cash's "name is for humans, identity is a separate key" intent, but
makes the identity key *explicit on the object* instead of re-derived via a
name→id DB lookup (which is itself collision-prone — see below).

## Blast radius (scoping inventory)

~30–35 distinct coupling points. Grouped by load-bearing vs cosmetic.

### Load-bearing — the identity bridges (must change together)

| # | Site | What couples |
|---|------|--------------|
| L1 | `tournament_game_builder.py:154-159` / `tournament_handler.py:141-151` | Seat construction: `Player(name=s.player_id, nickname=resolve_display_name(...))`. |
| L2 | `tournament_game_builder.py:231-236` / `tournament_handler.py:184-186` | `memory_manager.initialize_for_player(s.player_id, personality_id=s.player_id)` — both args are the field id; dossier rows key on it. |
| L3 | `tournament_handler.py:206,225` / `tournament_game_builder.py:270` / `single_table_tournament.py:82` | Live-result dicts `{p.name: p.stack}` flow back to the session; keys must match `field.stacks`. |
| L4 | `tournament/session.py:315-328` (`apply_live_round`, `fold_live_hand`) | Consume `{player_id: stack}`; keys must equal field `player_id`. |
| L5 | `tournament/field.py:75,25-36` + `tournament/session.py:61-76,227` | Field `entries` / `Elimination` keyed by `player_id`; central lookup everything derives from. |
| L6 | `econ.real_persona_ids_for(session, personality_repo)` (`tournament_game_builder.py:191,228`) | Decides who registers with the dossier / gets paid by filtering `entries` keys through `load_personality_by_id`. Must filter on `personality_id` if it's separated. |
| L7 | `single_table_tournament.py:51-53` | Single-table builds `entries[p.name]` — keyed on the **display name**, not personality_id. (Internal inconsistency vs multi-table; see risk R2.) |
| L8 | `game_handler.py:376-407` (`restore_ai_controllers`) | Cold-load keys `bot_types[player.name]` / `player_llm_configs[player.name]`; if `name` becomes display, these persisted maps must be re-keyed/migrated to `personality_id`. |

### Cosmetic — display only (safe once keys are consistent)

| # | Site | Change |
|---|------|--------|
| C1 | `resolve_display_name[s]` callers: `tournament_completion.py:64-99`, `tournament_ticker.py:239-243`, builders | Collapse to one build-time resolution; pass `personality_id` not `name`. |
| C2 | `TournamentStandings.tsx:150,168,211,278` + `tournament/types.ts` | Read a `display_name`/`nickname` field from the payload instead of printing `player_id`. (Also the standalone Option-B fix.) |
| C3 | Ticker / beat messages (`tournament_ticker.py`) | Carry resolved names at construction. |

### Data-agnostic (no change)

`tournament/registry.py:_rehydrate`, `tournament/session.py:from_dict` — rebuild
from JSON; entries stay keyed however they were serialized.

## Risks

- **R1 — Display-name collisions.** Display names are **not** unique within a
  table (two "Fish" seats; duplicate personas). If `name` ever becomes a *key*
  this is silent data loss. The fix is exactly to **not** key on `name` — key on
  the explicit `personality_id`/`owner_id`. Cash gets away with `name` keys today
  only because its table personas happen to be unique.
- **R2 — Single-table already keys on `name`** (`single_table_tournament.py:51`).
  This is the pre-existing inconsistency the refactor resolves; it must be
  migrated to the same identity key as multi-table in the same pass.
- **R3 — Cold-load map re-keying** (L8). Persisted `bot_types` /
  `player_llm_configs` are keyed by `name` today. Either migrate existing saved
  games or key the new maps by `personality_id` and tolerate old blobs.
- **R4 — Immutable `Player` change.** Adding a field touches the frozen
  dataclass, `to_dict`/`restore_state_from_dict`, and every `Player(...)`
  construction site. Mechanical but wide.

## Suggested sequencing

1. **Land the standalone Option-B standings fix first** (C2 + backend
   `standings_view` emits names). Closes the last user-visible leak, no risk.
2. **Add `Player.personality_id`** + serialization round-trip; default `None`;
   no behavior change yet (cash/tournament keep current keys).
3. **Thread `personality_id`** through memory registration (L2), live-result
   write-back (L3/L4), and `real_persona_ids` (L6) so identity no longer rides on
   `name`.
4. **Flip `Player.name` → display name** in the tournament builders (L1) and
   migrate single-table (L7) + cold-load maps (L8) to the explicit key.
5. **Delete** `resolve_display_name` shims (C1) and per-surface `nickname`
   fallbacks once `name` is authoritative for display in both modes.

## Test strategy

- Chip-conservation + field-integrity across a full tournament (build → play →
  eliminations → payouts) keyed by the new identity field — reuse the cash
  seat/conservation harness pattern.
- Cold-load round-trip: save mid-tournament, reload, assert controllers + stacks
  + display names survive.
- Collision regression: a field with two identical display names must keep
  distinct field rows and pay out correctly.
- Frontend: standings + felt + dossier all show friendly names on a fresh load
  *and* after a cold-load, in both single- and multi-table.
