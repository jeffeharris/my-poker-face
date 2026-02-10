# Triage: Deferred Issues

Issues identified during code review but deferred for future work.

---

# Tier 1: High Priority

## Deck Seed Persistence for Hand Replay

Enable exact replay of any historical hand by saving the deck seed.

- **Files:**
  - `poker/repositories/schema_manager.py` - Add `deck_seed` column to `hand_history`
  - `poker/poker_state_machine.py` - Generate and pass seed when dealing
  - `poker/memory/hand_recorder.py` - Save seed to hand record
- **Scope:** ~4 files, schema migration
- **Benefit:** Debug AI decisions by replaying exact scenarios with different code
- **Use case:** When Batman lost JJ vs CaseBot's flush, we wanted to replay with the fixed equity calc but couldn't reconstruct the exact deck
- **Added:** 2026-02-10

**Implementation notes:**
- Seed mechanism already exists: `create_deck(random_seed=...)` and `current_hand_seed` in state machine
- Just need to persist and expose for replay
- Could add `/api/replay-hand/<game_id>/<hand_number>` endpoint

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
