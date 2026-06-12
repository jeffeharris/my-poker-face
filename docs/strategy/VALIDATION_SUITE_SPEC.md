---
purpose: Implementation spec for chart validation - static lints, probe bots, low-variance head-to-head harness
type: design
created: 2026-06-10
last_updated: 2026-06-12
status: proposed
depends_on: scripts/test.py (Docker test runner), experiments/run_ai_tournament.py, chart JSON schema
---

# Validation Suite Spec

Current validation is aggregate VPIP/PFR band-matching over 10k-hand self-play
sims. That regime cannot catch the bugs that matter:

- The `vs_3bet` copied-range bug (all 15 nodes byte-identical; ditto `vs_4bet`)
  is **invisible to aggregate stats** — VPIP/PFR look fine while BTN's
  open-weighted fold-to-3bet sits at 73.9% against a 65.2% auto-profit line.
- Raw NLHE variance is σ ≈ 90 bb per 100 hands, so a 10k-hand sim has a 95%
  CI of roughly **±18 bb/100**; resolving ±2 bb/100 takes ~1M raw hands. A
  10k-hand A/B cannot detect even the −3.8 bb/100 `vs_3bet` trade that was
  shipped (that figure itself deserves re-measurement under tier 3), let
  alone subtler leaks. Variance reduction isn't an optimization — without it,
  head-to-head numbers at feasible volume are noise.

Three tiers, increasing cost. Tiers 1–2 run in CI on every chart regen; tier 3
gates releases.

## 1. Tier 1 — static chart lints (milliseconds, every regen + CI)

Pure JSON checks, no simulation. Each is a named assertion with a node-level
failure message.

**Structural**
- Action weights sum to 1.0 ± 0.01 per cell; no negative weights.
- Action vocabulary legal for the node type (e.g. no `jam` in 100bb `vs_3bet`).
- All 15 nodes present per facing branch; 169 hands per node.

**Strategic invariants** (the ones that would have caught known bugs)
- `vs_3bet`: per-node fold-to-3bet ≤ 0.65 (OOP) / 0.58 (IP), computed as
  1 − Σ(open_weight × continue_weight)/Σ(open_weight). *Catches the copied-range bug.*
- **Anti-clone**: no two facing-branch nodes byte-identical. *Catches it
  trivially (confirmed live: all 15 `vs_3bet` and all 15 `vs_4bet` nodes are
  byte-identical today).*
- Monotonicity: continue mass increases with the node's own open% (same
  villain seat); jam ranges widen as stacks shrink (depth + push/fold charts);
  BB defend% increases from vs-UTG to vs-SB.
- BB `vs_open` defend floors: ≥ {UTG .30, HJ .36, CO .43, BTN .52, SB .58}.
  (Live charts: UTG/HJ pass; CO/BTN/SB fail — BTN 44.6%, SB 49.4%.)
- 4-bet mass ∈ [6%, 14%] of open mass per `vs_3bet` node.
- Suited-only 4-bet/jam bluff invariant (AKo exempt). *Regression-guards PR #272/273.*
- Depth charts: every 50bb node retains ≥ 40% of the 100bb node's flat mass
  (regression floor — live charts measure 78%, so this guards future regens,
  it does not flag today's); depth BB defend within 5 points of the 100bb
  floor (live charts fail: 34.9% vs BTN at 50bb); depth RFI == current 100bb
  RFI (catches the stale-RFI footgun — live charts fail).
- Archetype containment: transform continues ⊆ base-chart support (the mask
  promise, verified rather than assumed).

**Diff report** (informational, not blocking): per-node max cell delta vs the
previous chart version, top-20 changed cells printed — makes regens reviewable.

## 2. Tier 2 — probe bots (minutes, CI on chart-affecting PRs)

Scripted single-purpose exploiters seated against the base chart (and
optionally each archetype). Each probe asserts an EV bound **per attempt** in
its target spot, not bb/100 overall — per-spot EV converges orders of
magnitude faster and pinpoints the leak.

| Probe | Behavior | Assertion |
|---|---|---|
| `threebet_any_two` | 3-bets 100% vs every open, folds to 4-bet, plays fit-or-fold after | EV per 3-bet ≤ 0 at every (hero_pos, villain_pos) node |
| `steal_any_two` | Opens 2.5bb any two from CO/BTN/SB | EV per steal ≤ +0.3bb (blinds make steals slightly +EV even at equilibrium; bound the excess) |
| `jam_any_two` | Open-jams every hand at every depth | EV per jam ≤ 0 vs ≤12bb tables; regression-guards the eval7 call backstop deeper |
| `always_cbet` | C-bets 100% flop, gives up turn | EV per c-bet ≤ 0 vs defender nodes |
| `barrel_three` | Bets flop/turn/river 100% as PFR | EV per river barrel ≤ 0 (tests bluff-catch floors) |
| `overfolder` | Folds to any aggression without strong_made+ | Hero bb/100 vs it ≥ +15 (charts must *punish* overfolding — tests we generate enough aggression, not just enough defense) |
| `station_clone` / `nit_clone` | Synthetic extreme profiles | Exploit rules fire (hyper_passive / tight_nit) and adaptation EV ≥ non-adaptive baseline — first real exercise for the dormant exploit rules |

Implementation: controllers implementing the existing player-controller
interface with fixed policies; harness = `experiments/run_ai_tournament.py`
with a `--probe` mode. Per-spot EV logged to the existing experiment tables.

## 3. Tier 3 — head-to-head acceptance (hours, release gate)

**Variance reduction is mandatory** — raw sims at feasible volume cannot
resolve the differences being shipped.

- **Duplicate dealing**: pre-deal N deck states; play each deck once per seat
  rotation (6 rotations) with strategies swapped, so both strategies see
  identical cards in identical seats. Compare paired per-deck outcomes
  (paired t-test). Cuts required hands by ~5–10× for preflop-chart diffs.
- **All-in EV correction** (AIVAT-lite): score all-ins at eval7 equity share
  instead of realized outcome. Cheap, removes the largest variance source.
- Volume math (be honest about it): duplicate dealing + all-in correction
  yield roughly 10–20× variance reduction, i.e. effective σ ≈ 20–28 bb/100.
  At 100k duplicate-dealt hands that's a 95% CI of ~±1.3–1.8 bb/100. Always
  report the CI with the estimate; scale hands until the CI half-width is
  ≤ the margin the decision needs (≈300k hands for ±1 bb/100 decisions).
- **Ship criteria** for a chart change: head-to-head vs previous chart
  ≥ −0.5 bb/100 with CI half-width ≤ 1.5 (or an explicitly accepted
  believability trade, recorded in
  the chart's provenance block like the June `vs_3bet` −3.8 note), all tier-1
  lints green, all tier-2 probes green, archetype VPIP/PFR separation within
  ±3 points of targets.

## 4. Wiring

- `scripts/test.py --charts` → tier 1 (fast, default in `--quick`).
- `scripts/test.py --probes` → tier 2 (Docker, chart-affecting PRs; gate via
  path filter on `poker/strategy/**`).
- `scripts/test.py --acceptance` → tier 3 (manual / release branch).
- `make validate-archetype-bands` → **archetype band gate** (BUILT 2026-06-12,
  PR #303). The deterministic mixed-field probe (`scripts/archetype_mixedfield_probe.py`,
  seed 4242, 9000 hands) scores every archetype's full banded stat set vs
  `ARCHETYPE_TARGETS` and exits non-zero on a hard fail. Gates only the high-n
  frequency stats (`HARD_FAIL_STATS = {vpip, pfr, threebet, all_in}`); low-n /
  variance-heavy stats and the in-calibration `WARN_ONLY_ARCHETYPES = {nit, rock}`
  are reported as WARN. `PROBE_HANDS` overrides N. See
  `docs/technical/ARCHETYPE_SHAPING_FINDINGS.md` (nit/rock band calibration).
- Tier-1 lint set lives next to the generators
  (`poker/strategy/lints.py`) so a regen script can refuse to write a chart
  that fails — bugs caught at generation time, not review time.
- Provenance blocks in every chart JSON gain a `validation` stanza: lint
  version, probe results hash, acceptance run id. The review packet's
  provenance table then becomes generatable.

## 5. Out of scope

- Human-play telemetry monitors (prod fold%-drift dashboards) — valuable,
  separate effort.
- Full AIVAT (requires a value function); the all-in correction captures most
  of the win.
- Exploitability computation (best-response EV vs full strategy) — the probe
  set is a practical lower bound; true best-response search is a research
  project.
