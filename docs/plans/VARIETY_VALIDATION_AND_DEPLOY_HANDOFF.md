---
purpose: Executable handoff — validate the shipped variety/fish work at short stacks, price the aggressive end vs a calling field, migrate live fish personas, and prep prod deploy
type: guide
created: 2026-05-29
last_updated: 2026-05-29
status: A PASS (validated), D done, B re-run vs proper calling fields, C script built+validated (apply pending merge), E pending
---

# Variety + fish validation & deploy — NEW-CONTEXT START HERE

> **Read this first.** The casino-fish + per-archetype-variety work is BUILT and pushed
> (`origin/lookup-tables`, head `db6cb05e`). This doc is the validation/pricing/deploy punch
> list to run next — Jeff asked to "get all this data now," is **clear to use Hetzner** for
> longer runs, wants it **documented**, wants a **scheduled recurring eval**, and wants the
> **live-fish migration prepped for the development branch / main worktree** (this lookup-tables
> worktree is a dev env being retired). Do the tasks roughly in the order below; **A is the one
> with real risk** (a behavior change shipped without short-stack validation).

## What shipped (so you have the map)
- **Per-archetype variety:** width-tier preflop tables (`poker/strategy/data/preflop_100bb_6max_{tight_rfi,_,loose_mid,loose,station,weak_station}.json`, gen by `experiments/build_archetype_charts.py`) selected by `ARCHETYPE_WIDTH_TABLE` (`poker/strategy/deviation_profiles.py`) + retuned distortion profiles. Field is now visibly distinct (Nit 15 → Maniac 56 VPIP). Doc: `PERSONALITY_PRICING_AND_VARIETY.md`.
- **Fish → tiered calling_station:** casino fish run through the tiered engine (`flask_app/handlers/tiered_factory.build_fish_controller`), routed at sit/live-fill/restore + `cash_mode/full_sim`. `archetype='fish'` untouched (economy key). Doc: `FISH_AS_CALLING_STATION.md`.
- **$2 weak fish:** `WEAK_FISH_STAKES={'$2'}` (`cash_mode/stakes_ladder.py`) forces the `weak_fish` profile (weak_station table + sticky 0.85 + over_bluff 0.55 + **position_blind 0.8**) at the $2 tier; $10/$50 stay `calling_station`. Drains ~−40 bb/100 @40bb.
- **Key levers (deviation_profiles.py):** `spot_tendencies`, `position_blind` (new), `ARCHETYPE_WIDTH_TABLE`. Controller hooks: `_select_preflop_table` (width table wins at ALL depths — the precedence flip), `_table_archetype_key`, `_apply_position_blindness` (preflop facing-seat shift, RFI exempt), `_apply_postflop_position_blindness` (OOP→IP).
- **Measurement archetypes already in `experiments/simulate_bb100.py::ARCHETYPES`:** `WeakFish`, `StationPBlind` (+ profile `calling_station_pblind`). Tool: `measure_passivity` (`--hero <ARCH> --opponents <roster> --hands N --seeds ... --stack-bb D`); paired-CRN pricing: `ab_node_attribution`.

## Hetzner rules (from memory + docs/EVAL_RUNNER.md)
Burst sims on the Hetzner box use the **`poker-bot-optimization` project ONLY — never prod**; confirm billing; **always tear down** after. Runbook: `docs/EVAL_RUNNER.md` (on this branch). Box results are bit-identical to local, so develop the commands locally first, then scale hands/seeds on the box. Watch the docker-exec orphan-kill recipe in memory ([[reference_hetzner_eval_runner]]).

---

## A. Short-stack validation (HIGHEST PRIORITY — shipped, unvalidated)
**Risk:** the precedence flip in `_select_preflop_table` makes width-tier archetypes
(nit/rock/lag/maniac/calling_station/weak_fish) use their **100bb** width table at **every**
depth — so at 50/25bb they no longer use the depth charts (TAG/Baseline still do). A maniac/LAG
playing 100bb-wide ranges at 25bb could be spewy. **Never measured shallow.**

**Run:** `measure_passivity` for each of `{Nit, Rock, TAG, LAG, 'Calling Station', Maniac}` at
`--stack-bb 100 / 50 / 25` vs a realistic field (use `--opponents Baseline,Baseline,Baseline,Baseline,Baseline` for speed — no equity MC; ~30× faster than `gto`). Also run the WTA-SNG gate (`experiments/sng_runner.py`, short-stack by construction) on the archetypes if you want a tournament-relevant read. Long Hetzner version: bump hands to 3000 × 8 seeds.
```
docker compose exec -T backend python -m experiments.measure_passivity \
  --hero <ARCH> --opponents Baseline,Baseline,Baseline,Baseline,Baseline --hands 1500 --seeds 42,3042,6042 --stack-bb 25
```
**Document:** a VPIP/PFR/AF/bb-100 table per archetype × {100,50,25}bb. **Red flag:** an archetype
whose bb/100 craters or whose VPIP/jam% goes absurd at 25bb (e.g. maniac jamming 100bb-wide).
**If broken:** options are (a) gate the precedence flip to ≥~50bb (fall back to depth charts
below that), or (b) generate depth-specific width tables. Decide from the data.

## B. Price the aggressive end vs a CALLING field (recurring caveat — resolve it)
**Problem:** all pricing so far was vs foldy fields (Baseline/TAG over-fold), so aggression
reads +EV (Maniac +54 self-play; position_blind +EV @100bb; over_bluff). The TRUE cost vs a
**calling** opponent is unmeasured. **Run** `ab_node_attribution` (paired CRN, 6-max — HU bypasses
the archetype table) and/or `measure_passivity` vs a calling roster (`jeff` clone = vpip 0.39/WtSD
0.59 calls down; or `CallStation,CallStation,...`; or build a "calling grinder"):
```
# combo price of an archetype vs a calling field (6-max, 24k):
docker compose exec -T backend python -m experiments.ab_node_attribution \
  jeff 3000 42,3042,6042,9042,12042,15042,18042,21042 --a base --b base --a-hero Baseline --b-hero Maniac --top 12
```
Price at minimum: **Maniac, LAG** (combo) and the **position_blind** lever (StationPBlind vs
Calling Station) and **over_bluff/sticky**, vs a calling field, at 40bb AND 100bb.
**Document:** for each, the {foldy-field, calling-field} × {40bb,100bb} matrix — the honest cost
that the foldy-field numbers hid. This is the real skill-gradient picture.

## C. Live-fish persona migration + PROD-deploy prep
**Gap:** the 4 LIVE DB fish (Vacation Greg/Bachelorette Brenda/Cruise Carl/Birthday Bobby) are
**bare** in `config_json` (no `spot_tendencies`); only the **fixture** (`poker/personalities.json`,
9 fish) carries them. So at $10/$50 the live fish are identical bland calling-stations. ($2 is fine
— weak_fish is stake-forced in code.)
**Migration (idempotent, conservation-safe, WAL-safe backup first):** for each DB persona whose
`config_json.rule_strategy=='fish'`, copy the matching fixture persona's `spot_tendencies` (by name)
into its `config_json`. The mapping is `fish_loadout.fish_spot_tendencies(fish_leak)` — but the live
rows lack `fish_leak` too, so map by NAME → fixture entry. Write a script under `scripts/` (force-add
past .gitignore per [[feedback_scripts_force_add]]); back up the DB via the sqlite backup API (plain
cp is WAL-malformed — [[reference_sqlite_wal_backup]]).
**Do it on the development branch / main worktree** (Jeff's instruction — NOT this retiring
lookup-tables dev env). So first: **merge the variety+fish work (lookup-tables) into development**,
then run the migration there against that worktree's DB.
**PROD considerations to flag to Jeff (he asked):**
1. The **code** (profiles, tables, build_fish_controller, stakes_ladder) ships with the branch — no
   data migration needed for the $2 weak-fish wiring or the archetype tables (they're committed files
   in `poker/strategy/data/`). Confirm the deploy includes those JSON files.
2. The **fixture** `personalities.json` now carries fish `spot_tendencies` — if prod **re-seeds**
   personas from the fixture, fresh prod fish get them automatically; if prod has **existing bare
   fish rows**, run the migration script against the prod DB (Hetzner `/opt/poker`, backend-stopped,
   backup first). Determine which by checking how prod seeds fish.
   **RESOLVED (2026-05-29):** `deploy.sh:57` runs `migrate_avatars_to_db.py` **without `--overwrite`**,
   and `seed_personalities_from_json(overwrite=False)` **SKIPS existing rows**. So prod's existing
   fish rows will **NOT** auto-pick-up the new fixture `spot_tendencies` on deploy — only brand-new
   (never-seeded) fish names would. **→ The migration script (`scripts/migrate_fish_spot_tendencies.py`)
   IS required for prod's existing fish.** (An equivalent alternative: a one-off
   `migrate_avatars_to_db.py --overwrite` — but that overwrites ALL persona configs from the fixture,
   blowing away any prod-side hand-edits; the targeted migration script only touches fish_leak +
   spot_tendencies on fish rows, so it's the safer surgical choice.) Script is built + dry-run-validated
   on the lookup-tables DB (correctly maps Greg/Brenda/Bobby→sticky, Cruise Carl→bare); WAL-safe backup,
   idempotent, conservation-safe, DRY-RUN by default.
3. The `weak_fish`/`calling_station_pblind`/`StationPBlind` measurement archetypes are harmless in
   prod (only `weak_fish` is assigned, via the $2 stake gate).
4. **Don't** let `position_blind` reach DEEP fish/whales (it helps them vs foldy fields) — it's
   stake-gated to $2 today; keep it that way.

## D. Buy-in depth experiment (try a few, document the diff)
**Finding to confirm:** drain is depth-capped (station −9.6 @40bb vs −68 @100bb, 7×). So a deeper
bottom buy-in is the biggest cycling lever. **Run** `measure_passivity --hero 'Calling Station'`
and `--hero WeakFish` at `--stack-bb 40 / 60 / 80 / 100` vs a TAG-grinder field; tabulate bb/100 vs
depth. **Document the diff** + the design recommendation: keep $2 shallow + weak fish, or deepen the
bottom buy-in (changes `MIN_BUY_IN_BB`/`MAX_BUY_IN_BB` in `cash_mode/stakes_ladder.py` or a per-tier
override). This is a product/economy call for Jeff — give him the numbers.

## E. Schedule a recurring eval (Jeff: "schedule this as a test")
Set up a scheduled/cron eval that re-runs the core data (the A short-stack table + the B
pricing-vs-calling-field matrix + the D depth diff) so we have ongoing/fresh numbers. Use the
`/schedule` skill (remote routine) or CronCreate; or formalize via `/new-experiment`
(`docs/experiments/EXP_NNN_*`). Point it at the Hetzner runner for the heavy version. Keep it in the
`poker-bot-optimization` project; ensure teardown so it doesn't bill idle.

## F. (LAST — parked) Sizing tells + reviving the sizing-aware exploit
Lowest priority, after A–E. No `spot_tendency` reshapes bet **sizes** (only frequencies), so the old
fish "size=strength" tell is lost and the parked sizing-aware program (D1 oracle / adaptive overbet,
`SIZING_AWARE_OPPONENT_MODELING.md`) still has no face-up-sizing target. Building a **sizing-tendency**
hook would restore the tell AND give the parked exploit work something to hunt. Design only for now.

## Also parked (from prior turns, lower priority)
- **$50 whale not co-spawning with $200** — by design (one-whale-at-a-time, picks highest stake in
  `resolve_whale_provisioning`). Recommend KEEP (protects mid-tier challenge); optionally add
  stake-rotation so the single whale visits different tiers over time.
- **Tournament buy-ins** as an economy-cycling channel (tourists pad low-tier prize pools, bust,
  prize cycles to winner) — on the `tournaments` branch. See [[project_casino_economy_cycling]].
- **Predator loadouts** — `docs/plans/PREDATOR_LOADOUTS.md`, the heterogeneous-exploiter feature.

## Housekeeping
Scratch sim sandboxes were left in this dev DB today (`6e14a278-...`, `860dd100-...`, `8e84beda-...`,
`aefb6656-...`, `fish_ab_*`) — harmless/scoped, clean up if you like. A test-generated zombie
persona ('Slots Linda') was already deleted.
