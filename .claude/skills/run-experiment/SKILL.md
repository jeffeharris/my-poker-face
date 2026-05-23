---
name: run-experiment
description: Execute a planned experiment doc (docs/experiments/EXP_NNN_*.md), run the sim per its Setup section, and fill in the Results / Conclusion sections against the pre-committed validation criteria. Use when the user says "run experiment NNN", "let's run EXP_NNN", or "execute the experiment plan".
argument-hint: <exp-number-or-doc-path>
allowed-tools: Read, Edit, Bash, Glob
---

# Run a planned experiment

Execute an experiment document end-to-end. The doc must already
have its Hypothesis / Falsifier / Setup / Validation criteria
sections filled in (use `/new-experiment` to scaffold, then have
the user fill those in BEFORE invoking this skill).

## Step 1 — resolve the doc

If the user passed a number (e.g. `002`), find the matching doc:

```bash
ls docs/experiments/EXP_<NUM>_*.md
```

If a full path, use it directly.

Read the doc with `Read` to confirm:
- `status: planned` (not `complete`)
- Hypothesis, Setup, Validation criteria sections have content
  (not `<placeholder>` text)

If any of those are missing, refuse to run and ask the user to
fill them in first. Running an unplanned experiment defeats the
methodology.

## Step 2 — parse the Setup section

Pull out:
- The sandbox-seeding command (often `python -m scripts.seed_sim_sandbox`)
- The sim-run command (usually `python -m scripts.run_economy_sim` or a custom wrapper)
- Output destination path
- The rng_seed and other sim_config fields
- Any pre-conditions (config mutations, feature flags)

The setup format isn't strictly templated — read it pragmatically.

## Step 3 — execute

Run the commands inside the docker container:

```bash
docker compose exec -T backend bash -c '<command>'
```

For sims expected to take more than ~2 minutes, use `run_in_background: true` on the Bash call so you get notified when it completes. Don't sleep-poll.

If the setup mutates production state (e.g. personality configs),
make sure the runner has its own try/finally — see
`scripts/sim_experiments/` for the pattern.

## Step 4 — collect data

After the sim completes, gather the metrics specified in the doc's
`Measurements` section. Usually:
- Read the summary.json: `Read /app/data/sim_*/run1.summary.json`
- Compute archetype concentration: see `/tmp/exp001_analyze.py`
  pattern for a worked example
- If H2 / H3 require intervention traces, run a short follow-up
  trace via `scripts/sim_experiments/trace_sim_v2.py` and
  `analyze_interventions.py`

## Step 5 — evaluate against validation criteria

For each sub-hypothesis (H1a, H1b, H1c, H2, H3, etc.):
- Compute actual value
- Compare against threshold
- Mark ✓ MET or ✗ NOT MET

Then look up the matching row in the Validation criteria table.
**That's the conclusion.** Don't generate a fresh interpretation —
use the row's pre-committed decision text verbatim or close to it.

## Step 6 — fill in the doc

Use `Edit` to update three sections in the experiment doc:

**Results** — replace the placeholder with:
- Comparison table (actual numbers vs baseline)
- H1/H2/H3 evaluation table (threshold | actual | ✓/✗)
- Sample outputs (top winners, fire rates, etc.)

**Conclusion** — replace placeholder with:
- One-line verdict ("H2 met, H1 partial" or similar)
- The pre-committed decision text from the validation-criteria row that fired
- 2-3 paragraphs explaining what changed vs baseline

**Decisions made / next steps** — numbered list of follow-ups

Also update the frontmatter:
- `status: planned` → `status: complete`
- `last_updated:` → today
- `hypothesis_summary:` → append " — Verdict: <one-line summary>"

## Step 7 — commit

```bash
git add docs/experiments/EXP_<NUM>_*.md
git commit -m "docs(exp<NUM>): fill in results, conclusion, next steps"
```

(Use the project's commit message convention — check `git log` for
recent EXP commits.)

## Anti-patterns to avoid

- **Don't rewrite the hypothesis after seeing the data.** The
  hypothesis was committed when the doc was created. Use it as-is.
- **Don't talk yourself into a "win" verdict.** If H1a missed by
  0.006, the threshold was missed. Period. The validation-criteria
  row decides the conclusion, not your read on whether "this looks
  good enough."
- **Don't skip the falsifier.** If the doc has a Falsifier section
  with content that matches the actual outcome, the experiment
  was falsified. Say so explicitly.
- **Don't bundle results from multiple runs.** Each EXP gets one
  run. If you want comparison across configurations, scaffold a
  fresh EXP doc.

See `docs/experiments/EXP_001_MEMORY_WIRED_REVALIDATION.md` for a
worked example end-to-end — what it looked like at "planned" and
what it looks like at "complete."
