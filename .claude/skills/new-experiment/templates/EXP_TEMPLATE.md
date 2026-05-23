---
purpose: <one-line description of what this experiment is testing>
type: experiment
status: planned
hypothesis_summary: <one-line summary of the testable claim>
created: {{DATE}}
last_updated: {{DATE}}
---

# Experiment {{NUM}} — {{TITLE}}

> **Why this exists:** <context — what observation, question, or
> prior result motivated this experiment? What's the open question
> the experiment will answer?>

## Hypothesis

**H1 (primary):** <the main testable claim. Be specific. Include
quantitative thresholds.>

- <sub-claim 1a with threshold>
- <sub-claim 1b with threshold>
- <sub-claim 1c with threshold>

**H2 (secondary, optional):** <a secondary claim, often about a
necessary precondition for H1>

**H3 (null-validating, optional):** <a claim that would invalidate
the test if not met — e.g., "the mechanism we're testing actually
fires at all">

**Falsifier:** <explicit description of what outcome would tell us
the hypothesis was wrong on each axis. Keep this honest — the
falsifier is what prevents Goodharting.>

## What we're testing

<the SINGLE variable change being made. Everything else identical
to a reference baseline. Say which baseline.>

## Setup

**Sandbox:** <how it's seeded, what state is reset>

**Sim config / experiment parameters:**

```python
# code or config block describing exact settings
```

**Wiring status / preconditions:** <any feature flags, code paths,
or env requirements>

**Output destination:** <where results land — file paths>

## Measurements

**Primary metrics (used for H1):**

- <metric name and what it tells us>

**Secondary metrics (used for H2):**

- <metric name>

**Diagnostic metrics (used for H3 / context):**

- <metric name>

**Captured via:** <which scripts / commands produce these>

## Comparison data

| Run | Source | <metric1> | <metric2> | ... |
|---|---|---|---|---|
| **<baseline>** | `<path/to/baseline>` | <val> | <val> | <val> |
| **{{NAME}}** | TBD | TBD | TBD | TBD |

## Caveats / Known Confounders

<List things ahead of time that could make the result misleading.
This is the most important section to fill in BEFORE running.
Examples:>

1. <single-seed risk>
2. <cold-start period>
3. <unmodeled component>
4. <comparison-data limitations>

## Validation criteria

**Outcomes we'll act on:**

| Outcome | Decision |
|---|---|
| H1 + H2 + H3 all met | <what we'll do — usually "act on the finding, ship the change, move on"> |
| H2 met, H1 partial | <intermediate response> |
| H2 met, H1 not met | <weaker but still informative response> |
| H2 not met | <"debug the mechanism before drawing any conclusions"> |

## Results

*To be filled after running.*

## Conclusion

*To be filled after analysis.*

## Decisions made / next steps

*To be filled after conclusion.*
