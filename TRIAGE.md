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

**Current seed behavior:**
```python
# poker/poker_game.py
def create_deck(shuffled=True, random_seed=None):
    rng = random.Random(random_seed)  # None = system entropy (non-reproducible)
    rng.shuffle(shuffled_deck)

# experiments/run_ai_tournament.py (deterministic if config.random_seed set)
next_hand_seed = base_seed + (tournament_number * 1000) + hand_number + 1
```

**What to add:**
1. Generate seed at hand start: `seed = int(time.time() * 1000000)` or `random.getrandbits(32)`
2. Pass to `reset_game_state_for_new_hand(deck_seed=seed)`
3. Save seed in `hand_history` table (add column)
4. Replay: load seed, reconstruct deck, replay actions

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
