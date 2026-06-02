---
purpose: Characterize the current closed-economy bank-reserve trajectory, then tune the constants of the demand-driven "thermostat" levers (reserve-scaled cash rake + tournament bank-overlay) so reserves self-regulate to a band — grounding P2 economy constants by drain rate before any feature code ships.
type: experiment
status: in-progress
hypothesis_summary: A reserve-driven thermostat (scale cash rake up when the bank is low; distribute a tournament overlay when high) bounds the closed economy to a stable reserve band that the un-thermostatted economy does not hold. **Verdict (single-seed): CONFIRMED — un-thermostatted economy balloons (slope 130 chips/tick → reserves 399k); proportional overlay flattens it (slope 3.78, parks at the 0.08 setpoint, ±2.5% band), conservation-clean. Constants need multi-seed + hands-on validation.**
created: 2026-05-30
last_updated: 2026-06-02
---

# Experiment 006 — Bank-Reserve Thermostat Tuning

> **Why this exists:** P2 (the tournament economy, `docs/plans/MULTI_TABLE_TOURNAMENT_P2_ECONOMY.md`)
> introduces a *demand-driven thermostat*: the prize-pool funding source and the
> cash-table rake schedule react to the bank's reserves vs. player holdings — a
> flush bank distributes (tournament overlay drains coffers), an empty bank
> refills (rake scales up across stake tiers). The whole design rests on one
> claim: **graduated, reserve-driven levers make the closed economy
> self-regulate.** The project rule ([[reference_cash_sim_ab_paired]],
> [[project_casino_economy_cycling]]) is that economy constants are tuned by
> **drain rate**, never guessed. This experiment grounds the thermostat constants
> in the existing economy sim (`cash_mode/sim_runner.py`) BEFORE any feature code
> is written — so P2 ships with constants we measured, not invented.
>
> This is staged: **Phase 0** characterizes the *current* economy (does the bank
> drain, balloon, or hold today?); **Phase 1** calibrates each lever; **Phase 2**
> runs both levers together and tests convergence to a band.

## What we're testing

The closed chip economy is a virtual bank pool (`compute_bank_pool_reserves`,
a derived sum over ledger reasons) fed by **faucets** and emptied by **drains**:

- Faucets (deposits): `table_rake` ($1000 tier only today, 2%), fake-vice
  (rich AIs), `casino_seat_return`.
- Drains (draws): `side_hustle_earning` (broke AIs), `casino_seat_seed`.
- Passive regen is OFF (`REGEN_ENABLED=False`); the side hustle is the faucet.

The **thermostat** adds two reserve-driven levers on top:

1. **Cash-rake thermostat** — when `signal` (≈ `bank_reserves / player_holdings`)
   is LOW, expand `RAKE_STAKE_BIG_BLINDS` (turn on $200, then $50 if dire) and/or
   raise `RAKE_RATE` at the top tier; when HIGH, contract back to $1000-only / 0.
2. **Tournament-overlay thermostat** — when `signal` is HIGH, a tournament draws a
   `tournament_overlay` from the pool into a prize pool that pays out to the field
   (distributes reserves into circulation); when LOW, overlay→0 and tournament
   rake refills.

Both consume **one shared economy-state signal**. Phase 1 calibrates each lever's
response curve; Phase 2 tests whether the pair holds reserves in a band.

> The levers are wired into the **sim only** for this experiment (injectable rake
> fn + a per-tick overlay-draw hook in the harness) — NOT the production feature.
> The point is to find the constants; P2 then implements them for real.

## Hypotheses

**H0 (Phase 0 — characterization, no pass/fail).** Run the current economy
(production flags, no thermostat) for a long horizon and classify the
`bank_pool_chips` trajectory. The observed shape selects the next move:
- *Draining* (net negative slope, reserves trend toward 0): refill levers (rake
  scaling) are the priority; an unbounded drain bottoms out at a side-hustle
  starvation floor.
- *Ballooning* (net positive slope, reserves grow unbounded): distribution levers
  (tournament overlay) are the priority.
- *Already stable* (slope within the noise band): the thermostat's job is to keep
  it stable under the *added* tournament load, not to fix a current imbalance.

**H1 (the un-thermostatted economy does not self-regulate under tournament load).**
With the tournament drain/refill *absent* but a synthetic load applied (or simply
over a long horizon), reserves do NOT hold a band: `|net slope|` over the back
half of the run exceeds the **drift band** = 1× the tick-to-tick reserve stdev of
the back half. (Establishes that a passive economy needs the lever.)

**H2 (rake thermostat bounds a drain).** With the reserve-scaled cash-rake lever
ON, a draining economy stops falling below a floor: `min(bank_pool_chips)` over
the back half stays ≥ `FLOOR_TARGET`, and the back-half slope flattens to within
the drift band. Calibrated: find the `(signal threshold, tier-expansion, rate)`
that achieves this with the *least* total rake taken (minimize player-value bite).

**H3 (overlay thermostat bounds a balloon).** With the tournament-overlay lever
ON, a ballooning economy stops rising above a ceiling: `max(bank_pool_chips)` over
the back half stays ≤ `CEILING_TARGET`, and the slope flattens to within the band.

**H4 (both levers → convergence to a band).** With both levers active and a
tournament cadence applied, `bank_pool_chips` enters and stays within
`[FLOOR_TARGET, CEILING_TARGET]` for the entire back half (oscillates within, no
escape), AND `audit_drift == 0` at every audit checkpoint (conservation holds
through every overlay/rake/payout flow).

`FLOOR_TARGET` / `CEILING_TARGET` are set from the Phase 0 baseline (e.g. floor =
the level at which `casino_seat_seed` can no longer fund a $50 table; ceiling =
2× the baseline steady-state) — pinned in **Results** before Phase 2 runs.

**Falsifier:** if H4 fails — reserves escape the band under both levers — the
graduated-response thermostat is the wrong control model (consider hysteresis /
PID / event-gated instead). If H2/H3 require so aggressive a setting that rake
eats >X% of player winnings or the overlay empties the bank in one event, the
lever is too blunt and needs a softer curve. If `audit_drift != 0` ever, a flow
is leaking chips — STOP and fix conservation before tuning anything.

## Setup

- **Branch:** `tournaments`. **DB:** an isolated WAL-safe copy of prod
  (`/app/data/sim/econ_base.db`, `src.backup(dst)` — integrity-checked), NOT the
  live economy. 70 personalities, sandbox-scoped runs.
- **Harness:** `cash_mode/sim_runner.run_sim` via `scripts/run_economy_sim.py`;
  fresh sandbox per config via `scripts/seed_sim_sandbox.py`.
- **Per-run knobs:** `hand_sim_prob=1.0` (drive real rake), `tick_seconds=8`,
  `metrics_every=10`, `audit_every=500`, `rng_seed ∈ {42,142,242}` (3 seeds).
- **Horizon:** Phase 0 baseline = 5,000 ticks (scale to 10k if the trend is slow
  to resolve). Phase 1/2 = 5,000 ticks × 3 seeds per config.
- **Lever wiring (sim-only):** inject `rake_fn(pot, big_blind, reserves)` into
  `play_one_hand` (default = `economy_flags.compute_rake`); add a per-tick
  overlay-draw hook in `run_sim` gated on the signal. Both behind the experiment
  harness, not the app.
- **Output:** `/app/data/sim/<config>.{csv,pids.jsonl,summary.json}`. Drain rate =
  OLS slope of `bank_pool_chips` vs `tick`, converted to chips/sim-day
  (`× 86400/tick_seconds`); cross-checked against net `ledger_delta` per tick.

## Measurements

- **Primary (gate):** `bank_pool_chips` back-half OLS slope (chips/tick) + min/max
  over the back half; `audit_drift` at every checkpoint (must be 0).
- **Secondary:** per-reason `ledger_delta` totals (which faucet/drain dominates);
  total rake taken (player-value bite); overlay distributed; `fish_net_to_players`.
- **Diagnostic:** `gini`, per-pid bankroll trajectory, side-hustle / casino-seed
  firing counts, the signal `bank_reserves/player_holdings` over time.

## Comparison data

| Run | Config | Back-half slope (chips/tick) | min / max reserves | signal final | drift |
|---|---|---|---|---|---|
| Baseline | no lever, modeled rake 130/tick | **130.0** (balloons) | 139k / 399k | 0.234 ↑ | −200 (const) |
| Thermostat | overlay on, flush=0.08, overlay_pct=0.02 | **3.78** (flat) | 164k / 172k | 0.087 (parked) | −200 (const) |

(4000 ticks, seed 42, isolated `econ_base.db`, hands-off + the flow-level lever
harness `scripts/sim_experiments/thermostat_sweep.py`.)

Prior economy-class experiments for method reference: EXP_001 (memory-wired
re-validation), EXP_002 (aspiration under memory) — both drive
`cash_mode/sim_runner` and compare bank_pool / gini / total_chips across configs.

## Caveats / known confounders

- **RNG desync** ([[reference_cash_sim_ab_paired]]): the rake-tier toggle changes
  a decision branch, so same-seed A/B desyncs. Mitigation: fresh sandbox per
  config + **3 seeds**, compare distributions not single traces; where a constant
  is purely arithmetic (rate scaling, no branch), same-seed is valid.
- **Overlay lever isn't in the codebase** — modeled as a sim-harness per-tick draw
  for tuning. The production attach point (P2 `tournament_game_builder`) may pace
  overlays differently (per tournament, not per tick); the *constant* (overlay as
  a fraction of reserves) should transfer, the cadence must be re-validated.
- **`tourist_injection` appears retired** (closed_economy docstring) — the current
  drain is side-hustle + casino-seed. Don't tune against a dead faucet.
- **Fake-vice stands in for real LLM vice** in the sim — magnitude parity assumed,
  not LLM-narrated. Same chip drain, so fine for reserve accounting.
- **First-tick controller warmup** (~3s for 40 builds) is wall-time only, not a
  metric confounder.
- **Sandbox seat RNG is unseeded** in `seed_sim_sandbox` → exact rosters differ
  run-to-run; aggregate reserve metrics are robust, per-pid traces are not
  cross-comparable.

## Validation criteria

| Outcome | Decision |
|---|---|
| H0 draining | Prioritize the rake thermostat; set FLOOR from the side-hustle starvation point; proceed to H2. |
| H0 ballooning | Prioritize the overlay thermostat; set CEILING from 2× steady-state; proceed to H3. |
| H0 stable | Thermostat is a *stability-under-load* guarantee; inject a tournament cadence and go straight to H4. |
| H2 met at low rake | Adopt that rake schedule as the P2 default; record the (threshold, tiers, rate). |
| H2 needs heavy rake (>X% bite) | Lever too blunt — soften the curve or add hysteresis; re-run. |
| H3 met | Adopt the overlay fraction as the P2 default. |
| H4 met (band held, drift 0) | **Thermostat model validated** — lock constants into P2 `tournament/funding.py` + the cash-rake schedule; this experiment closes the "constants must be sim-modeled" gate. |
| H4 fails (escapes band) | Graduated response insufficient — escalate to hysteresis/PID/event-gated control; do NOT ship the simple linear thermostat. |
| audit_drift ≠ 0 anytime | STOP — a flow leaks chips; fix conservation before any tuning conclusion. |

## Results

### Smoke + Phase 0 preliminary (2026-05-30)

Isolated DB `econ_base.db` (WAL-safe copy of prod, 70 personalities), fresh
sandbox `8c6b96df…`, seed 42, `hand_sim_prob=1.0`, 200 ticks (≈26 sim-min).

**Trajectory — bank pool BALLOONS from a cold start:** `bank_pool_chips`
330 → 73,191 over 200 ticks, monotonic, not yet at equilibrium. Cumulative
flow composition (sandbox audit `by_reason`):

| Flow | Role | Total | Note |
|---|---|---|---|
| `bank_pool_deposit` (fake-vice) | faucet | 114,133 | **dominant faucet (~4.5× rake)** |
| `table_rake` ($1000 tier only) | faucet | 25,409 | secondary |
| `casino_seat_seed` | drain | 36,451 | main drain; grows as pool crosses spawn thresholds |
| **net bank pool** | | **+103,091** | filling; casino-seed is the only negative feedback |

**Interpretation:** the current economy's natural tendency (fresh sandbox) is to
**fill the bank**, dominated by rich-AI vice, with `casino_seat_seed` as a growing
(threshold-gated) drain. This points at the **overlay/distribution lever as the
primary thermostat need** — i.e. the "bank flush → tournaments distribute"
regime — with the rake-refill lever secondary. (Caveat: fresh sandbox starts near
0 pool; fake-vice magnitude stands in for real LLM vice. Re-confirm against an aged
sandbox before locking constants.)

**Drift — benign, confirmed:** constant `audit_drift = -200` (`drift_reliable=True`)
= the human's 200-chip starting bankroll with no `player_seed` ledger row in this
scoped sandbox. One-time fixed offset, does NOT compound. Phase 2's "drift==0"
criterion will be measured as **drift delta from this -200 baseline** (or fixed by
seeding the player through the ledger).

**Tractability finding (decisive for method):** the harness works, but
`hand_sim_prob=1.0` costs **~5.5 s/tick** (200 ticks = 1101 s). A `hand_sim_prob=0.0`
control = **0.057 s/tick** (30 ticks = 1.7 s) → **hand simulation is ~100% of the
cost**; the per-tick economy machinery (vice, side-hustle, casino spawn/draw) is
~100× faster than real hands. Critically, every faucet/drain EXCEPT `table_rake` is
hand-independent and fires per-tick. **So the long thermostat sweeps will run
hands-OFF with a `table_rake` faucet MODELED at a rate calibrated from a short
real-hands run** (≈ the measured rake/table/sim-hour). This turns a 5k-tick run
from ~7.5 h into ~5 min, making the Phase 1/2 sweep tractable. One config will be
re-validated with real hands as a fidelity check.

### Phase 1/2 first result (2026-05-30): the overlay thermostat self-regulates

Two 4000-tick runs, fresh sandboxes, seed 42, on the flow-level harness
(`thermostat_sweep.py`: real cash world hands-off + injected levers as
conservation-clean chip moves):

- **Baseline (no lever)** — reserves 0 → 91k → 139k → 269k → **399k**, monotonic +
  accelerating; back-half slope **130 chips/tick** (= the modeled rake faucet);
  signal 0.045 → **0.234** and climbing; holdings drain 2.02M → 1.71M. The
  un-thermostatted economy **balloons unbounded** → **H1 confirmed.**
- **Thermostat (overlay on, `flush=0.08`, `overlay_pct=0.02`)** — reserves climb
  to ~164k by tick 2000 then **flatten** at 164k–172k; back-half slope **3.78
  chips/tick** (**34× flatter**); signal parks at **0.087**, just above the 0.08
  setpoint — textbook proportional-control behaviour (the lever drains at exactly
  the faucet rate at a small steady-state offset above setpoint). Band width ±2.5%
  → **H3/H4 supported (single seed).**
- **Conservation:** `audit_drift` stays at the benign constant **−200** on both
  runs (the unledgered human seed). The lever moves (modeled-rake debits + overlay
  credits, each a real ai_bankroll_state move + matching ledger row) introduced
  **zero** new drift → conservation invariant holds through the thermostat.

### §6 re-validation (2026-06-02): the per-tournament cadence — constants did NOT transfer; fix found

The P2 §6 gate before flipping the thermostat on: EXP_006 above tuned a *per-tick*
overlay, but production fires the overlay **per tournament** (chairman
`should_offer_event`: FLUSH + a 30-min cooldown, sized by `tournament_funding`).
Re-ran the harness with a new `--mode tournament_cadence` that calls the REAL
production functions (`economy_signal.should_offer_event` for the gate +
`tournament_funding` for the size) so this measures the actual production cadence,
not a re-model. All runs 3000 ticks, fresh sandboxes, `--preload-reserves 220000`
(pre-charge to FLUSH so the regulated regime is exercised without the slow ~1600-
tick natural fill), `base_rake=130`, on `econ_base.db`.

| Run | Sizing | back-half slope (chips/tick) | reserves band | signal_final | events |
|---|---|---|---|---|---|
| baseline (no lever) | — | **130.0** (balloons) | →495k | 0.331 | 0 |
| per-tick overlay (control) | `pct×reserves` | **5.10** (flat) | 197k–208k | 0.110 | — |
| cadence, **production sizing** | `min(reserves×0.02, cap)` | **99 / 100 / 98.5** (3 seeds) | →427–440k | 0.25–0.30 | 11 |
| cadence, **drain-to-setpoint** | `reserves − 0.08×holdings`, cap | **6.9 / 12.0 / 9.4** (3 seeds) | 178k–245k | 0.12–0.13 | 11–12 |

**Finding (decisive):** the per-tick-tuned `0.02 × reserves` overlay **does NOT
transfer** to the per-tournament cadence. Across the 30-min cooldown a fixed-
percent draw is ~225× too weak — each event drains ~2% of reserves while the
faucet accrues ~225 ticks of inflow between events — so the bank balloons at
~99 chips/tick, barely better than the un-thermostatted 130. **As-is, the
thermostat must NOT be flipped on.** (Conservation held throughout:
`holdings + reserves` was identical across baseline and cadence sandboxes —
2,470,000 — and the overlay shifted exactly its recorded amount from reserves to
holdings, so the lever leaks nothing; it was simply too small.)

**The fix (validated):** size each discrete event to **drain reserves back to the
FLUSH setpoint** — `overlay = min(max(0, reserves − FLUSH_SETPOINT × holdings),
OVERLAY_CAP)` — instead of a fixed fraction. This is a sawtooth controller matched
to the discrete cadence: reserves climb on the faucet between events, one event
per FLUSH+cooldown resets them to the setpoint. It held the band across 3 seeds
(slope 6.9–12.0 vs ~99; reserves 178k–245k; signal parked ~0.12), conservation-
clean. `EconomyState` already carries `holdings`, so the production
`tournament_funding` computes it with no new inputs.

**Shipped:** `core/economy/economy_signal.py::tournament_funding` FLUSH branch
changed from `reserves × OVERLAY_DRAIN_PCT` to drain-to-setpoint (the constant is
retained, marked legacy/per-tick). Tests updated. Harness:
`--mode tournament_cadence --cadence-sizing {production,to_setpoint}` in
`scripts/sim_experiments/thermostat_sweep.py`.

### Hands-ON fidelity check (2026-06-02): the cap does NOT bind — PASS

Drain-to-setpoint is faucet-agnostic by construction (it drains reserves back to
the setpoint *each event*), so the ONLY residual failure mode is the
`OVERLAY_CAP` (250k) binding: if the *real* faucet × the 30-min cooldown exceeds
the cap, one event can't fully reset reserves and the band escapes upward. Measured
the real faucet with a hands-ON run (`--hands-on --mode baseline --base-rake 0`, so
NO modeled faucet — pure real rake + fake-vice + casino, the production inflow),
100 ticks, seed 7:

| metric | value |
|---|---|
| back-half faucet slope | **665.6 chips/tick** |
| per 30-min cooldown (×225 ticks) | **~150k** |
| OVERLAY_CAP | 250k |
| headroom | **~1.67×** (cap does not bind) |

So the real faucet (665.6/tick — higher than EXP_006 Phase 0's ~515 fresh-sandbox
estimate, as expected with real hands) drains ~150k into the bank per cooldown,
comfortably under the 250k cap → **drain-to-setpoint fully resets reserves each
event and the band holds.** The §6 gate is satisfied. Levers if an *aged*
production sandbox ever runs hotter than ~1100 chips/tick (the point the cap would
bind): raise `OVERLAY_CAP` or shorten `MAIN_EVENT_COOLDOWN_SECONDS`.

**Activation:** circuit flipped ON in **dev** (`TOURNAMENT_CIRCUIT_ENABLED=1`);
prod flip is now economy-justified, pending a deploy.

## Conclusion

**Validation-criteria rows that apply:** *H0 ballooning* → overlay is the primary
lever (matches the product intent: "bank flush → tournaments distribute"); *H4
met (band held, drift 0-delta)* on a single seed → the **simple proportional
overlay thermostat is a validated control model** for draining a ballooning bank
to a stable reserve band. The graduated-linear response did NOT need escalation to
hysteresis/PID (the falsifier did not trigger). The rake-refill lever was not
exercised because the economy never starved (it balloons) — it remains the
fail-safe direction, untested here.

**Confidence:** medium. The *structure* (balloon → overlay converges → band) is
robust and conservation-clean. The *constants* are first-cut: single seed, and the
hands-off harness understates the real (vice-dominated) faucet, so the true
overlay authority needed is higher than 130/tick — but the lever showed large
headroom (parked just above setpoint with 34× slope reduction), so the model is
under-, not over-stated. P2 should adopt the proportional-overlay *shape* and the
~0.08 signal setpoint as the default, then re-confirm magnitudes hands-on.

## Decisions made / next steps

- **Adopt the proportional overlay thermostat** for P2 `tournament/funding.py`:
  `bank_overlay ∝ (signal − flush)` above a ~0.08 `reserves/holdings` setpoint;
  rake-refill as the fail-safe below an `empty` threshold (untested — economy
  balloons, doesn't starve).
- **Remaining to firm the constants (cheap, ~5 min each):** (1) multi-seed
  (142, 242) to confirm the band holds across RNG; (2) an `overlay_pct` sweep
  (0.005 / 0.01 / 0.02 / 0.04) to map the setpoint-tightness vs distribution-burst
  tradeoff; (3) a discrete-cadence variant (overlay as periodic tournament events,
  not per-tick) to check the setpoint transfers; (4) one hands-ON validation run to
  re-confirm against the real vice faucet. (5) re-run against an aged production-
  like sandbox (these start near-0 pool).
- **Harness lives at** `scripts/sim_experiments/thermostat_sweep.py` (force-add per
  the scripts/ convention if keeping). Isolated DB `/app/data/sim/econ_base.db`.
