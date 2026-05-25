# Triage: Deferred Issues

Issues identified during code review but deferred for future work.

---

# Tier 1: High Priority

## Deck Seed Persistence for Hand Replay — **FIXED**

Enable exact replay of any historical hand by saving the deck seed.

- **Status:** Implemented. Every hand now records its deck seed.
- **Remaining:** Add `/api/replay-hand/<game_id>/<hand_number>` endpoint to reconstruct and replay a hand from its saved seed.

---

# Tier 2: Medium Priority

## Type Safety: SkillEvaluation enum

Convert `SkillEvaluation.evaluation` from `str` to `EvaluationResult` enum.

- **File:** `flask_app/services/skill_evaluator.py`
- **Scope:** ~50 usages of `'correct'`/`'incorrect'`/`'marginal'`/`'not_applicable'` strings
- **Benefit:** Type safety, prevent typos
- **Added:** PR #139 review

```python
# Proposed change
class EvaluationResult(str, Enum):
    CORRECT = 'correct'
    INCORRECT = 'incorrect'
    MARGINAL = 'marginal'
    NOT_APPLICABLE = 'not_applicable'

@dataclass(frozen=True)
class SkillEvaluation:
    skill_id: str
    action_taken: str
    evaluation: EvaluationResult  # Changed from str
    confidence: float
    reasoning: str
```

## Static type checking (mypy), incremental rollout

Introduce `mypy` one package at a time, starting with `core/` (smallest, most
isolated, ~6K LOC). Make each module strict, then freeze it and expand outward.

- **Why deferred:** Unlike ruff/prettier, mypy can't be bulk-autofixed — every
  error is a real annotation decision. It also adds a hand-satisfied gate for
  contributors, so it's a deliberate project, not a baseline-setup task.
- **Measured scope (`core/` only, 2026-05-25):** `mypy core/ --ignore-missing-imports`
  → **80 errors in 13 of 27 files**. Breakdown: 34 `arg-type`, 22 `assignment`,
  13 `union-attr` (these are real None-safety bugs — same class as the Phase 4
  F821 finds), 7 `call-overload`, 4 misc.
- **Plan:** Add `[tool.mypy]` to `pyproject.toml` scoped to `core/` only → fix
  the 80 errors (prioritize `union-attr`) → add a `typecheck-backend` CI job →
  expand to the next package once green. Full codebase is many multiples of
  this (`poker/` alone is 69K LOC), hence the per-module rollout.
- **Added:** CI/maintainability pass, 2026-05-25.

---

# Tier 3: Low Priority

## CI: security scan (bandit or CodeQL)

Add a static security-analysis job to CI. Catches hardcoded credentials, weak
crypto, SQL-injection patterns, unsafe deserialization, etc.

- **Options:** `bandit` (Python-specific, fast, one CI job) or GitHub CodeQL
  (multi-language, deeper, GitHub-native). Bandit is the faster win.
- **Scope:** New CI job in `.github/workflows/deploy.yml`, parallel to
  `lint-backend`. Start non-blocking, triage findings, then flip to required.
- **Added:** CI/maintainability pass, 2026-05-25.

## CI: per-package coverage thresholds

The current `--cov-fail-under=40` is a single global floor. Split into per-package
floors so mature, well-tested modules can't regress.

- **Rationale:** `core/` and `poker/hand_evaluator` are well-covered and should be
  held to a high bar (e.g. 80%); newer modules like `cash_mode/` can stay at 40%
  while they stabilize. A global floor lets a well-tested module silently rot.
- **Approach:** Either separate `pytest --cov` invocations per package with
  distinct `--cov-fail-under` values, or a coverage tool that supports per-path
  thresholds (e.g. `diff-cover` for changed-lines-only, or a `coverage.py`
  config with per-module targets).
- **Added:** CI/maintainability pass, 2026-05-25.

## GitHub contributor labels + issue migration *(low priority)*

Create `good first issue` / `help wanted` labels, then sift `docs/TODO.md` and
`docs/triage/` for well-scoped, newcomer-friendly items and migrate them into
GitHub Issues with the right label.

- **Why it matters:** The CI/format baseline makes the repo *pleasant* to
  contribute to, but contributors still need *discoverable, scoped work* to pick
  up. This is the actual contributor-attraction step.
- **Why low priority:** Pure curation/labeling — no code, no blocker. Do it once
  there's a reason to invite outside contributors.
- **Added:** CI/maintainability pass, 2026-05-25.
