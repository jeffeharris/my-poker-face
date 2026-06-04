---
purpose: Handoff for the production starting-conditions / Director economy thermostat work — status, flags, how to run, and what's next.
type: guide
created: 2026-06-04
last_updated: 2026-06-04
---

# Starting Conditions / Director Thermostat — Handoff

Pick up the prod "starting conditions" work from a fresh context with this. Full
design + per-section detail + sim findings live in
**`docs/plans/PROD_STARTING_CONDITIONS.md`** — this is the orientation layer.

## What this is

A fresh production sandbox used to boot **economically inert** — AI bankrolls
seeded but an empty bank pool, so no casinos (need pool ≥ 5k/50k/100k), no
tournaments, and rake only trickled at $1000. We built **the "Director"**: a
reserve-band economy thermostat that seeds the bank, taxes the field to build a
prize pool, fires Main Events, and redistributes — plus a 76-persona IP-vetted
launch cast. The chairman/Director is a pure read-model over the chip ledger
(`core/economy/economy_signal.py`): it computes `r = reserves / holdings` and
drives every lever off that one number.

## Status (2026-06-04)

**Built, unit-tested, sim-validated, on `origin/release-candidate`.** EVERYTHING
is behind feature flags **defaulting OFF** — production behaviour is unchanged
until the flags are flipped after a live tuning pass. NOT run against prod (prod
is on legacy schema v70 — a separate migration; see the `project_prod_schema_drift`
memory).

## The flags (all default OFF; in `cash_mode/economy_flags.py` unless noted)

| Flag | What it does |
|---|---|
| `GENESIS_RESERVE_ENABLED` | Seed the bank pool to 5% of holdings once at fresh-sandbox birth (`ensure_genesis_reserve_seeded`, wired in `cash_routes`). |
| `VICE_RESERVE_GATED` | Vice (drains the rich → pool) refill scales with the reserve deficit; full at the 0.06 floor, **tapers to off at the 0.18 ceiling ABOVE the 0.12 trigger** (so it crosses the trigger + brakes when hot). |
| `RAKE_RESERVE_GATED` | Rake graduates BOTH tiers and rate by reserve band: `{1000}@2%` / `{1000,200}@3%` / `{1000,200,50}@4%`. $1000 always on (structural). |
| `DIRECTOR_INEQUALITY_RAKE` | On a FLAT field (low `p90/median`, throttled signal in `cash_mode/field_inequality`), the rake widens to lead the refill that vice (no rich target) can't. |
| `DIRECTOR_POLICY_HOLD` | Hold the rake schedule for a `POLICY_WINDOW_SECONDS` (300s) window (`cash_mode/director_policy`, recomputed in the lobby refresh; per-hand rake reads the cached value via `resolve_rake_params`'s `_fresh=` bypass) so the hot path skips the per-hand ledger signal scan. Implies `RAKE_RESERVE_GATED`. |
| `CASINO_RELATIVE_THRESHOLDS` | Casino spawn/close gates scale as fractions of holdings instead of absolute chip counts. |
| `CASINO_RESEED_ON_SPENT` | Lean casino fish: 1 fish/casino (2 at $2), leaner prefund (1.5–2.0×), whale 3–5× (was 10–18×). Turns the lumpy casino drain into a steady trickle. |
| `TOURNAMENT_CIRCUIT_ENABLED` (pre-existing) | The autonomous tournament ticker. The overlay/funding policy lives in `economy_signal`. |

**The canonical reserve ladder** (one source of truth, `economy_signal.py`):
`RESERVE_CRITICAL 0.03` / `RESERVE_HEALTHY 0.06` (= tournament drain floor) /
`RESERVE_TRIGGER 0.12` (offer a Main Event) / `RESERVE_VICE_CEILING 0.18` (vice
off). Vice, rake, and the tournament trigger/floor all reference these.

## How the loop works (the sawtooth)

1. Genesis seeds reserves to ~5% (low band). Casinos open, fish bleed pool→field.
2. Vice (taxes the rich) + rake refill reserves; they **climb floor→trigger**.
3. At `RESERVE_TRIGGER`, `should_offer_event` fires a Main Event;
   `tournament_funding` sizes the overlay to drain reserves back to the
   `RESERVE_HEALTHY` floor; the prize redistributes to the field (holdings up).
4. Reserves climb again. Repeat.

Vice = the de-concentration faucet (targets runaways). Rake = the even-skim
faucet (for a flat field). Casino = the steady pool→field drain. Tournament =
the big redistribution drain.

## How to run the validation sim

`scripts/sim_experiments/thermostat_validation.py` seeds a fresh tempdb (the
76-cast), flips the Director flags on, and traces `reserves/holdings` per chunk,
firing real Main Events at the trigger (drains the overlay) and printing a
per-reason bank-pool flow breakdown.

```bash
# full run (the real 0.12 trigger takes ~1000 ticks to reach):
docker compose exec -T backend python -m scripts.sim_experiments.thermostat_validation \
    --ticks 1000 --chunk 40 --seed 0
# demo the sawtooth fast (lower trigger; must stay > the 0.06 floor):
... --ticks 500 --chunk 25 --trigger 0.08
# isolate the tournament from the casino drain:
... --no-casino --genesis-ratio 0.14 --trigger 0.10
```
Runs take minutes (real solver hands) — run in the background. Seeding itself is
~1.3s; the cost is the hand play.

## Validated findings

- Genesis seeds exactly 5%. The casino seed is the ONLY steady drain; vice is the
  dominant refill.
- **Vice-quits-halfway bug (caught by the user): FIXED** — vice now refills to /
  past the trigger; reserves climb 0.05→0.09 smoothly (pre-fix they stalled ~0.06).
- **Casino drain was lumpy (crashed reserves ~74–90k): FIXED** by the lean fish —
  drain halved (66k vs 128k) and went steady, no crashes.
- **Tournament overlay drains the pool in-loop** (demoed: 0.14 → fire drains 124k
  → 0.057 floor → re-climb). The sawtooth drop is real.

## What's next (open, in rough priority)

1. **Sawtooth CONFIRMED in sim 2026-06-04** (`--ticks 1000 --chunk 40 --seed 0`,
   default 0.12 trigger). Reserves climbed smoothly past the old 0.06 stall —
   0.054 (t40) → 0.080 (t240) → 0.103 (t480) → **0.1197 (t760)** → CROSSED the
   trigger → the Main Event **fired at ~t800: −163,607 overlay drained to the
   field** (holdings 2.47M → 2.62M as prizes landed) → reserves dropped to the
   **0.06 floor (0.0563)** → and the climb restarted (t840: 0.0565, rising). The
   full floor→trigger→fire→floor→re-climb loop, end to end — the vice-taper fix
   (`b7206b8b`) works. **Tuning note for #3:** the vice brake tapers hard near the
   trigger, so the final 0.105→0.12 push is slow (rake-carried) — one
   floor→trigger climb is ~700–900 ticks at this faucet rate (≈one fire per
   1000-tick run). That's the cadence knob: this faucet is on the SLOW side of
   "1–2/day" unless a day is many hundreds of hands.
2. **Policy hold — BUILT 2026-06-04 (`DIRECTOR_POLICY_HOLD`, default OFF).** The
   rake schedule (stakes + rate) is now held for a `POLICY_WINDOW_SECONDS` (300s)
   window — a `cash_mode/director_policy` cache recomputed in the lobby refresh
   (`refresh_director_policy`, same throttle model as `field_inequality`), read by
   `resolve_rake_params` via a `_fresh=` bypass (refresh recomputes `_fresh=True`;
   per-hand reads the held value; cold cache falls through to a live compute).
   **Vice + side-hustle stay per-tick** (the always-on bounds); casino is already
   window-stable. Unit-tested (`tests/test_cash_mode/test_director_policy.py`),
   flag-off byte-identical. Open follow-up: sim-verify the held cadence under load
   (the hold's whole value is the skipped per-hand scan — measure it).
3. **Tune the faucet rate** for ~1–2 Main Events/day against real hand volume.
4. **Seed-time circulating** is applied in `personalities.json`; verify
   Σ(bankrolls) + the 5% seed against the live ledger once it runs in a real sandbox.
5. **OVERLAY_CAP (250k) binds** at $2.64M holdings (0.12 overlay = ~$317k) — raise
   it (~6% of holdings) or accept a two-event drain.

## Key files

- `core/economy/economy_signal.py` — the chairman read-model, reserve ladder,
  `should_offer_event`, `tournament_funding`, `cash_rake_schedule`.
- `cash_mode/economy_flags.py` — all the flags + `compute_rake` / `resolve_rake_params`.
- `cash_mode/ai_vice_spending.py` — `reserve_vice_multiplier` (the vice gate).
- `cash_mode/field_inequality.py` — the throttled `p90/median` signal.
- `cash_mode/casino_provisioning.py` — `resolve_pool_threshold`, `_fish_cap`,
  `_prefund_mults` (lean fish).
- `cash_mode/closed_economy.py` — `ensure_genesis_reserve_seeded`, `seed_bank_pool`.
- `cash_mode/ai_side_hustle.py` — pay-up-front (no escrow; the empty-bank fix).
- `poker/personalities.json` + `scripts/seed_prod_roster.py` — the 76 cast + circulating flags.
- `poker/personality_generator.py` — `generate_from_spec` (spec-pinned generation).
- `scripts/sim_experiments/thermostat_validation.py` — the validation harness.

## Memory

`project_prod_starting_conditions` (in the auto-memory) has the blow-by-blow
commit log and the latest state.
