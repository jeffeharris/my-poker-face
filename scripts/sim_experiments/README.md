---
purpose: One-off sim experiment drivers — mutate the production DB (or strategy files) temporarily, run a 10k sim, restore via try/finally.
type: guide
created: 2026-05-22
last_updated: 2026-05-22
---

# Sim Experiment Drivers

Standalone scripts that probe cash-mode dynamics by temporarily
mutating personality configs (or strategy files), running a sim, and
restoring originals via `try/finally`.

## Pattern

Each script follows the same shape:

```python
1. Snapshot the production state (config or file contents)
2. Apply experiment-specific mutations
3. Seed a fresh sandbox + run a 10k sim
4. Restore in finally — guaranteed even on crash
```

If you kill the script with SIGTERM, the restore still fires. SIGKILL
will leave the DB / file in mutated state — manually inspect and
restore if that happens.

## Scripts

| Script | What it tests |
|---|---|
| `qoh_at_50.py` | Relocate queen_of_hearts (the dominant maniac) to $50 with low bankroll. Tests if her domination was personal or structural. **Conclusion: structural — blackbeard (another maniac) took her place.** |
| `wealthy_at_50.py` | Relocate the entire "wealthy class" (7 AIs) to $50 with 10k bankrolls each. Tests if removing the wealthy presence from $1000 breaks concentration. **Conclusion: yes — Gini dropped 0.75 → 0.61, but the winner was still a maniac (don_quixote) who climbed up.** |
| `wealth_tuning_experiments.py` | Three back-to-back experiments (equalize 100k / demote to 60k / gradient compression). **Conclusion: only the strongest demotion meaningfully broke concentration; gentle tuning preserved the dominator pattern.** |
| `scrooge_at_1000.py` | Keep scrooge (the rock) at $1000, demote QoH + blackbeard. Tests if a non-maniac at the top tier breaks the loop. **Conclusion: no — blackbeard climbed back from $50 with 8k bankroll → ended with $1.25M at $1000.** |
| `nerf_maniac.py` | Clamp the MANIAC deviation profile to LAG-like values (`max_kl 1.2 → 0.8`, `aggression_scale 2.0 → 1.5`). Tests if softening the bot strategy itself breaks the pattern. |
| `trace_sim.py` | Hook into `TieredBotController.decide_action`, capture every decision's pipeline snapshot to JSONL. Lightweight — only the snapshot, no intervention traces. |
| `trace_sim_v2.py` | Same as above PLUS capture per-layer intervention traces (which rules fired, what action they changed). The diagnostic version. |
| `analyze_trace.py` / `analyze_trace2.py` | Per-archetype VPIP/PFR/fold-rate stats from the v1 snapshot trace. v2 fixes phase string match. |
| `analyze_interventions.py` | Reads the v2 trace and reports per-archetype intervention firing rates. **Key finding from this tool: 0% fire rate on `exploitation::hyper_aggressive` in sim — opponent-modeling is unfed.** |

## Findings from the diagnostic series

**Trace investigation (2026-05-22):** none of the exploitation rules fire in the sim path because `AIMemoryManager` / `CbetDetector` are wired into the experiments tournament runner (`experiments/run_ai_tournament.py`) but NOT into `cash_mode/full_sim.py`. The maniac-defense and barrel-induce rules see cold-start opponent stats and never trip their gates. This means our earlier "maniacs always win" finding was running on bots that **never adjusted to opponents**. Production (where memory IS wired via routes) likely shows different dynamics. Wiring memory into the sim is the natural next step.

## Headline finding from the series

The aspiration-ask mechanic is working as designed. The wealth
concentration we observe in 10k-tick sims is **not** an economy bug
— it's a **bot strategy issue**: maniac archetypes exploit
non-maniacs in the TieredBot solver+deviation framework, regardless
of starting position. Whichever maniac reaches $1000 first wins
decisively.

The right fix is upstream in `poker/strategy/deviation_profiles.py`
(narrowing the maniac's deviation envelope) and/or adding
opponent-aggression modeling so non-maniacs adjust their call
frequencies against repeat aggressors.

## How to add a new experiment

1. Copy the `scrooge_at_1000.py` template (it's the cleanest)
2. Update the `MUTATIONS` list with your target overrides
3. Update the output dir + sandbox name
4. Run via `docker compose exec backend python /tmp/your_script.py`
5. Pair with a `_analysis.py` script to read the CSV + JSONL output

Sim outputs land under `/app/data/sim_*/` — these are gitignored
artifacts, recreate from scratch when needed.
