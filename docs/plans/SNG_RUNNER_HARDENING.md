---
purpose: Execution-ready plan to harden experiments/sng_runner.py into a trustworthy feature-cutting gate
type: guide
created: 2026-05-26
last_updated: 2026-05-26
---

# SNG runner hardening — make it a cut-grade gate

> **For a fresh context.** This is a self-contained execution plan. Read
> `docs/plans/EVAL_HARNESS_PLAN.md` (the parent — P0/P0.5/P1 eval program) and
> the `tieredbot-bb100-lookup-tables` + `eval-harness-landed` memory notes
> first. The file under work is `experiments/sng_runner.py` (the
> EVAL_HARNESS_PLAN §P1 WTA-SNG runner). All tests run in Docker:
> `docker compose exec backend python -m pytest ...`.

## Why this exists

We need a **trustworthy yardstick** before cutting features (the low-SPR / 3BP
precision chart slices, and re-judging multistreet). Two harnesses exist:

- `experiments/champion_challenger.py` — relative bb/100 A/B. **Audited as
  fatally flawed:** (1) overlapping seeds (`hand_seed = base_seed + hand_num`
  → 97% shared hands across "seeds"), (2) CI computed on pooled per-hand iid
  deltas (wrong unit; huge poker variance → mislabels stable signals
  "inconclusive"), (3) no role/seat-swap (challenger pinned to seats 0/2/4).
  Its verdicts are **not trustworthy**.
- `experiments/sng_runner.py` — absolute WTA win-rate. **Audited as the sounder
  base:** it *structurally fixes* flaws (1) and (2) — each SNG is one
  independent tournament (non-overlapping seeds via `_split`), and the verdict
  is a Wilson CI over independent SNG Bernoulli outcomes vs the right null.
  **`field` mode is positionally clean (rotates the field by `seed`).** But
  **`champion_challenger` mode is not yet a cut-gate** — it inherits the
  role/seat confound and has outcome-accounting holes.

This plan hardens the SNG runner's `champion_challenger` mode into a gate, and
adds the integrity/calibration checks both modes need. (Audit source: two
codex-assist rounds + the in-session analysis; codex was sharper than the
initial take — see P2, A-A alone is **not** sufficient.)

## Definition of done

`sng_runner.py champion_challenger` mode produces a verdict we can act on to cut
features: antithetic role-swap removes seat/role bias, an A-A run confirms ~null
under the final protocol, a known-extreme calibration confirms sign sensitivity,
outcome accounting proves the run was clean, and no-op counters prove the change
fired. After P0–P4 land, it is gate-grade; P5–P8 are integrity/quality.

## Tasks (in execution order)

### P0 — Verify per-hand deck independence *within* a tournament (verify, don't assume)
- **What:** Confirm the engine advances each hand's deck deterministically from
  the tournament seed across a single `play_sng`. The runner only sets the
  initial `make_game_state(seed=sng_seed)` + `sm.current_hand_seed = sng_seed`
  (`sng_runner.py:89-99`); the *later* hands' decks are claimed to be handled by
  the SM's hand-seed progression (comment lines 108-109) but not proven here.
- **Why:** If later-hand decks aren't seeded reproducibly from the tournament
  seed, "independent SNG" is weaker than claimed and re-runs won't reproduce.
- **Where:** trace `hand_over_transition` / deck construction in
  `poker/poker_state_machine.py` + `poker/poker_game.py`
  (`reset_game_state_for_new_hand`).
- **Acceptance:** a test that runs the same `sng_seed` twice and asserts
  identical winner + hand count + the deck sequence is a deterministic function
  of `sng_seed`.

### P1 — Outcome accounting (gate integrity) — REQUIRED
- **What:** Track and report per run: attempted SNGs, completed SNGs,
  `None`-winners (no survivors), max-hands chip-leader fallbacks, and
  multi-survivor-at-cap count. The gate must **fail loudly** on `None > 0` or a
  fallback rate above a tiny threshold (e.g. >0.5%).
- **Why:** Both workers currently **drop `None` winners silently**
  (`_field_worker` ~184-187, `_cc_worker` ~204-209) and `report_field` reports
  `sum(merged.values())` (~332) rather than attempted SNGs — so SNGs can
  vanish from the denominator, and a "led at cap" (`play_sng` max-hands branch,
  line 128 picks chip leader among multiple survivors) is silently counted as a
  win. A gate can't have invisible dropouts.
- **Where:** `play_sng` returns enough to distinguish clean-finish vs cap-hit
  (it returns `hands_played`; add a terminal-reason flag). Thread counts through
  workers → reports.
- **Acceptance:** report prints attempted/completed/None/fallback/multi-survivor;
  non-zero `None` or fallback>threshold raises or prints a loud ⚠ and refuses a
  verdict.

### P2 — Antithetic role-swap for `champion_challenger` mode — REQUIRED (the verdict unit)
- **What:** For each SNG seed, run the tournament **twice**: (A) challenger in
  `_challenger_seat_indices(...)` (e.g. 0/2/4), (B) challenger in the
  **complement** (1/3/5). The verdict unit is the **paired block** (the pair of
  outcomes for that seed). Aggregate challenger-group win-rate over paired
  blocks; Wilson/bootstrap over the independent seed-blocks.
- **Why:** SNGs are **path-dependent** — eliminations reshape the seating graph,
  short-handed phases aren't balanced over original seats, and first-button
  (`dealer_idx=0` always, line 85) + per-seat RNG (`sng_seed + 1_000_000*i`,
  line 103) bias *who survives*. Within-SNG button rotation does **not** undo
  this. Role-swap cancels fixed seat-index / first-button / per-seat-RNG effects
  by construction. (A-A alone is insufficient — it only detects bias, doesn't
  remove it.)
- **Where:** `_cc_seat_specs` (149-169) + `_cc_worker` (190-209): run both seat
  assignments per seed; report from paired blocks.
- **Acceptance:** `--mode champion_challenger` runs both role assignments per
  seed; verdict computed from paired-block win-rates.

### P3 — A-A calibration under the final (role-swapped) protocol — REQUIRED
- **What:** A `null` change where champion == challenger (identical table +
  flags). Run it through the **full role-swapped cc protocol**; challenger-group
  win-rate must cover the null (`n_challenger/n_seats`) with no persistent seat
  skew.
- **Why:** Proves the harness is unbiased *after* P2. If A-A ≠ null, residual
  bias remains.
- **Where:** add a `null` entry to `champion_challenger.CHANGES`
  (champion_table == challenger_table, empty flags).
- **Acceptance:** `--change null` → win-rate CI covers the null; per-seat skew
  negligible.

### P4 — Known-extreme sign calibration — REQUIRED
- **What:** A/B a deliberately broken strategy vs normal (e.g. challenger table
  forced to fold/limp, or a crippled flag). The broken side must lose **big and
  CI-clear**, symmetrically (broken challenger → below null; broken champion →
  challenger above null).
- **Why:** Confirms the harness has the **sensitivity and sign** right — if it
  can't detect an engineered disaster, its verdicts mean nothing.
- **Acceptance:** broken challenger CI-clear BELOW null; broken champion → CI-clear ABOVE.

### P5 — No-op / change-takes-effect counters
- **What:** Per change under test, count that the mechanism actually **fired**
  during the run (e.g. SPR-fallback hits, `postflop_commit` fires/disabled-trace
  counts, table-lookup divergences between champion/challenger, flag-driven
  branch hits). Report must show the path fired enough to matter.
- **Why:** A silently-identical A/B (table/flag that didn't actually change
  behavior) would read as "no effect" and we'd draw the wrong conclusion.
- **Acceptance:** report shows fire-counts > 0 for the change under test;
  zero-fire → loud warning, no verdict.

### P6 — Blind-progression reporting + tests
- **What:** Report hands-played distribution, final blind level reached, and
  proof SNGs escalated through ~100→50→25→push-fold depths. Add a test asserting
  blinds escalate over a tournament.
- **Why:** P1's entire value is exercising the depth ramp; if SNGs end before
  getting shallow (or blinds don't escalate), the "tests the depth progression"
  claim is false.
- **Acceptance:** report shows an end-depth distribution; test asserts escalation.

### P7 — Field-mode null fix for duplicate archetypes
- **What:** `report_field` uses `1/len(field)` (line ~243) but aggregates wins
  by archetype (`_field_worker` strips `#n`, ~186). With duplicate archetypes in
  the field, the null for an archetype is `count(archetype)/len(field)`.
- **Acceptance:** duplicate-archetype field uses the correct per-archetype null.

### P8 — Power rules
- **What:** Predeclare the minimum actionable win-rate lift (e.g. the
  WTA-equivalent of "+X bb/100 matters"), estimate variance from independent
  paired blocks, and document the required block count. Win-rate is **coarse** —
  small cash edges are tiny win-rate bumps needing many SNGs.
- **Acceptance:** a documented power note + a default `--sngs` that powers the
  declared minimum effect.

## Scope note — what about the bb/100 `champion_challenger.py`?
It has the same three issues (seed overlap, per-hand CI unit, no role-swap). Two
options, pick one and write it down:
- **Demote it** to a fast, *non-binding* screening tool (sensitive per-hand
  signal for iteration), and make the **hardened SNG runner the only binding
  cut-gate**; or
- **Fix it too** (non-overlapping hand-seeds, per-seed/block CI, antithetic
  role-swap) so its relative bb/100 stays usable for sensitive small-effect
  screening (SNG win-rate is low-power for small effects — see P8).
Recommendation: demote bb/100 to screening now; fix it only if SNG power proves
too coarse for the decisions we actually face.

## After hardening — the decisions waiting on this gate
1. **Cut the precision slices?** Re-run `slices` (and `low_spr`/`three_bp`)
   through the hardened SNG cc gate. The non-binding smoke (this session) is the
   prior; the hardened gate is the verdict.
2. **Re-judge multistreet** (was −20 HU on the flawed bb/100 harness) cleanly.
3. **Verify the preflop depth charts** (50/25bb, +13.8/+4.8 vs Jeff, never
   self-play tested) — needs a preflop-table-flavor change in `CHANGES`.
