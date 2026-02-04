# Triage: Deferred Issues

Issues identified during code review but deferred for future work.

---

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
