---
purpose: Scope for v134 — fold the Phase-7.5 postflop opportunity-axis counters into the lifetime store so the response/open-aggression tells accumulate cross-game and surface in the dossier + coach
type: guide
created: 2026-06-01
last_updated: 2026-06-01
---

# Lifetime fold v134 — postflop aggression axes

> **Status: SHIPPED (v134 + v135).** The two core axes (`all_in_per_facing_bet`,
> `postflop_jam_open_rate`) shipped as **v134**. `flop_check_then_barrel_rate`
> (the trap read) then shipped as **v135** — it first required adding its two
> counters **and** the rate to `OpponentTendencies._SERIAL_FIELDS` (so they
> serialize to `tendencies_json` for the fold to read), which also updated the
> `test_exploitation_characterization` golden dicts. Same recipe otherwise.
> All three postflop reads now accumulate cross-game.

Same proven counts→fold→derive recipe as v132 (`limp_rate`), the showdown read,
and v133 (sizing). This adds the **postflop opportunity-normalized aggression
axes** to the durable per-sandbox store.

## The gap

`OpponentTendencies` tracks four postflop opportunity counters that derive two
reads in `_recalculate_postflop_stats` (`poker/memory/opponent_model.py`):

| Read | Numerator | Denominator | What it tells you |
|---|---|---|---|
| `all_in_per_facing_bet` | `_all_ins_facing_bet` | `_facing_bet_opportunities` | response aggression — how often they jam *into* a bet (shove-happy / check-raise jammer) |
| `postflop_jam_open_rate` | `_postflop_jam_opens` | `_postflop_open_opportunities` | open aggression — how often they donk-jam into a no-bet pot |

None of these four counters are in `_LIFETIME_COUNT_FIELDS`, so a
lifetime-reconstructed tendency always has them at 0 and both reads sit at their
`0.0` default. They never accumulate cross-game.

Optionally include the third live-only postflop read (separate decision below):

| Read | Numerator | Denominator | Tell |
|---|---|---|---|
| `flop_check_then_barrel_rate` | `_flop_check_barrel_count` | `_flop_check_barrel_opportunity_count` | checks flop OOP then bets turn after a check-through (delayed-cbet / trap) |

## Crucial framing — this is a player/coach read, NOT an AI-behavior change

The live tiered-bot exploitation clamp reads these axes from the **per-game**
`OpponentModelManager` (`aggregate_active_opponents`), never from the lifetime
store — that separation is the v124 design and stays. So folding these into
lifetime does **not** touch AI decisions. The value is purely: the **dossier**
and **coach** can show these tells **cross-game** (like limp/showdown/sizing),
instead of resetting each session. Scope and validate it as a read addition,
not a strategy change.

## Field set

Four new `INTEGER NOT NULL DEFAULT 0` columns (no sums — unlike v133, these are
pure ratios of two counts):

```
facing_bet_opportunities
all_ins_facing_bet
postflop_open_opportunities
postflop_jam_opens
```

If including the trap read, +2: `flop_check_barrel_count`,
`flop_check_barrel_opportunity_count`.

## Build steps (the recipe, proven 3×)

1. **schema_manager.py** — `_migrate_v134_add_postflop_axis_lifetime_counts`,
   guarded ALTER (the v127/v132 pure-INTEGER shape), migrations-dict entry,
   `SCHEMA_VERSION = 134`, comment block.
2. **game_repository.py** — add the 4 (or 6) `_field -> column` entries to
   `_LIFETIME_COUNT_FIELDS` (no `_LIFETIME_SUM_FIELDS` change). The fold UPSERT
   and read SELECT pick them up off the map automatically.
3. **opponent_reads.reconstruct_tendencies_from_lifetime** — set the counters
   from `counts` in the main block. **No ordering subtlety** (unlike v133's
   means): `_recalculate_postflop_stats` derives both rates straight from the
   counts, and it runs inside `_recalculate_stats()`, which is already called.
4. **opponent_reads.deep_reads_from_tendencies** — surface the reads, gated on
   their own denominator counter:
   - `all_in_per_facing_bet`: gate `t._facing_bet_opportunities >= N`
   - `postflop_jam_open_rate`: gate `t._postflop_open_opportunities >= N`
   - (optional) `flop_check_then_barrel_rate`: gate
     `t._flop_check_barrel_opportunity_count >= N`
5. **dossier_scouting.py** — new `ScoutingTier`(s) + `_DEEPER_FIELDS` entries +
   add to `INFORMANT_SECTIONS['deep_reads']['items']`.
6. **game_repository `_ROSTER_SAMPLE_COLUMNS`** — add the denominator columns
   (`facing_bet_opportunities`, `postflop_open_opportunities`[, the trap
   opportunity count]) so the file-cabinet unlock % matches the dossier. **This
   is the seam that's bitten twice — don't skip it.**
7. **coach_assistant.py** — render the new tells in the per-opponent `tells:`
   line (e.g. "jams into bets X%", "donk-jams X%").

## Gating choices (sane defaults — tune freely)

- `all_in_per_facing_bet` — facing a postflop bet is common, so samples come
  fast: floor ~180 hands + ~15 `facing_bet_opportunities`, noun "bets faced".
- `postflop_jam_open_rate` — open spots are common but jams rare; the *rate* is
  meaningful with enough opens: floor ~200 hands + ~20 `postflop_open_opportunities`,
  noun "postflop open spots".
- `flop_check_then_barrel_rate` (if included) — floor ~220 + ~12
  `flop_check_barrel_opportunity_count`, noun "check-flop spots".

No new threshold constants needed; the gate lives in the scouting tier +
`deep_reads_from_tendencies`, mirroring v132/v133.

## Tests (mirror the v133 set)

- `test_observation_lifetime.py`: cross-game fold + derive of each read;
  None-gating when no samples.
- `test_dossier_scouting.py`: add the fields to `_full_response` `deeper_reads`
  + `_maxed`; assert in `test_deep_reads_full_unlock_keeps_everything`.
- `test_dossier_scouting_route.py`: add counters to `_seed_opponent_model`
  + the route test's `sample_cols`.
- `test_coach_assistant.py`: the new tells render; None reads omitted.

## Risks / non-goals

- **Not an AI change** — confirm no code path reads these off the lifetime
  store into a decision (it shouldn't; the clamp uses per-game models).
- Decide up front whether to include `flop_check_then_barrel_rate` (its
  live-only status is currently documented as intentional in
  `opponent_reads.py`; including it is fine — same recipe — but it's a
  deliberate choice, not a silent add).
- Same shape/effort as v133: ~8–9 files, additive/idempotent, no behavior risk
  to existing reads.
