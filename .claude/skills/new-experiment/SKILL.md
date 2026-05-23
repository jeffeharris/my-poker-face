---
name: new-experiment
description: Scaffold a new experiment document at docs/experiments/EXP_NNN_<name>.md from the project template. Auto-increments the EXP number. Use this when the user wants to formalize an experiment plan — keywords like "design an experiment", "plan a sim run with hypothesis", "test whether X is true" should trigger it.
argument-hint: <short-kebab-name>
allowed-tools: Read, Write, Bash, Glob
---

# Scaffold a new experiment

Create a new experiment design doc following the project's
established format. See `docs/experiments/EXP_001_MEMORY_WIRED_REVALIDATION.md`
for a worked example.

## Step 1 — derive the next experiment number

Run from the project root:

```bash
ls docs/experiments/ 2>/dev/null | grep -oE '^EXP_[0-9]+' | sort -V | tail -1
```

Take the integer that follows `EXP_`, add 1, zero-pad to 3 digits.
If `docs/experiments/` doesn't exist yet, start at `001`.

## Step 2 — read the template + substitute

Template lives at `.claude/skills/new-experiment/templates/EXP_TEMPLATE.md`.

Read it with the `Read` tool. Substitute:

| Placeholder | Replacement |
|---|---|
| `{{NUM}}` | The zero-padded EXP number from Step 1 (e.g. `002`) |
| `{{NAME}}` | The user's argument, uppercased + underscored (`SOME_NAME`) |
| `{{TITLE}}` | The user's argument, title-cased + spaced (`Some Name`) |
| `{{DATE}}` | Today's date in `YYYY-MM-DD` |

## Step 3 — write the doc

Path: `docs/experiments/EXP_<NUM>_<NAME>.md`

Use `Write`. After writing, output the path so the user knows
where it is.

## Step 4 — guide the user through filling it in

After scaffolding, the doc has many `<placeholder>` sections. **Don't fill these in yourself** — the user needs to commit to the hypothesis and falsifier *before* running anything, or the methodology defeats itself.

Surface the sections that need user input, in priority order:

1. **Hypothesis** (H1/H2/H3 with quantitative thresholds)
2. **Falsifier** (what outcome would tell us we're wrong)
3. **What we're testing** (the single variable change)
4. **Setup** (sandbox state, sim config, output destination)
5. **Validation criteria table** (outcome → decision mapping)
6. **Caveats / Known Confounders** (list potential sources of confounding)

Ask the user about #1 first. Don't ask all six at once — that's overwhelming. Iterate.

## Why the format matters

Pre-committing the hypothesis and the outcome→decision mapping is what keeps experiments honest. Without it, every run can be talked into being either "a clear win" or "no result" depending on what you want to see. See the methodology note at the bottom of `EXP_001` for the worked example.

Do not skip steps. Do not pre-fill the hypothesis.
