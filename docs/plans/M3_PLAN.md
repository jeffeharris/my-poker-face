# Milestone 3: Deeper Skills, Multi-Street Tracking, and Review Enhancement

## Background

This is the **Coach Progression System** for a poker game (My Poker Face). It teaches players poker incrementally through play by tracking skill development across a gated skill tree.

**Milestones 1 and 2 are complete** (on branch `poker-coach-progression`). They implemented:
- 7 backend modules: `coach_models.py`, `skill_definitions.py`, `situation_classifier.py`, `skill_evaluator.py`, `coach_progression.py`, `coach_engine.py`, `coach_assistant.py` (all in `flask_app/services/`)
- Gate 1: 3 preflop skills (Fold Trash Hands, Position Matters, Raise or Fold)
- Gate 2: 3 post-flop skills (Flop Connection, Bet When Strong, Checking Is Allowed)
- Skill state machine (Introduced → Practicing → Reliable → Automatic)
- Gate unlock system (Gate N unlocks when Gate N-1 meets required_reliable)
- Player model persistence (3 tables via migration v63)
- Post-action evaluation hook in `game_routes.py`
- Mode-specific LLM prompts with skill context in `coach_assistant.py`
- Session memory with per-skill coaching cadence
- Multi-level onboarding (beginner/intermediate/experienced)
- Silent downgrade when observed play contradicts self-reported level

**Key reference documents:**
- Requirements: `docs/technical/COACH_PROGRESSION_REQUIREMENTS.md`
- Architecture: `docs/technical/COACH_PROGRESSION_ARCHITECTURE.md`

**Files to read before implementing (the M1+M2 implementation):**
- `flask_app/services/coach_models.py` — shared enums/dataclasses (SkillState, CoachingMode, EvidenceRules, PlayerSkillState, GateProgress, CoachingDecision)
- `flask_app/services/skill_definitions.py` — skill/gate definitions, `build_poker_context()`, ALL_SKILLS, ALL_GATES registries
- `flask_app/services/situation_classifier.py` — rule-based trigger checkers per skill
- `flask_app/services/skill_evaluator.py` — per-skill action evaluation; each evaluator constructs `SkillEvaluation(skill_id=..., action_taken=..., evaluation=..., confidence=..., reasoning=...)` directly
- `flask_app/services/coach_progression.py` — state machine, gate unlock, SessionMemory, coaching cadence, silent downgrade
- `flask_app/services/coach_engine.py` — `compute_coaching_data()` and progression wrapper; `_compute_hand_strength()` returns `hand_rank` (1-10) and `hand_name`
- `flask_app/services/coach_assistant.py` — LLM prompts, `_format_stats_for_prompt()` includes SKILL FOCUS section, mode-aware factory
- `flask_app/routes/coach_routes.py` — coach API endpoints (stats, ask, progression, onboarding, hand-review)
- `flask_app/routes/game_routes.py` — `_evaluate_coach_progression()` hook (line ~197)
- `poker/repositories/coach_repository.py` — skill/gate CRUD methods (`save_skill_state`, `load_all_skill_states`, `save_gate_progress`, `load_gate_progress`, `save_coach_profile`, `load_coach_profile`)
- `poker/repositories/schema_manager.py` — migration v63 (lines 2912-2948) creates coach progression tables
- `flask_app/extensions.py` — `coach_repo` initialization via `create_repos()` factory (lines 141-163)
- `poker/memory/hand_history.py` — `RecordedAction` (player_name, action, amount, phase, pot_after) and `RecordedHand` (actions list, community_cards, players)
- `poker/hand_evaluator.py` — `HandEvaluator.evaluate_hand()` returns `hand_rank` (1-10, where 1=Royal Flush, 10=High Card)
- `poker/poker_game.py` — `current_ante` is the big blind (line 700)

**Hand rank scale (1-10):** 1=Royal Flush, 2=Straight Flush, 3=Four of a Kind, 4=Full House, 5=Flush, 6=Straight, 7=Three of a Kind, 8=Two Pair, 9=One Pair, 10=High Card

**Current `build_poker_context()` fields:** phase, canonical, position, is_early, is_late, is_blind, is_trash, is_premium, is_top10, is_top20, is_playable, cost_to_call, pot_total, big_blind, hand_rank, is_strong_hand (rank≤8), has_pair (rank≤9), has_draw (outs≥4), is_air (rank≥10 + no draw), can_check, tags

**Current `compute_coaching_data()` fields:** phase, position, pot_total, cost_to_call, big_blind, stack, equity, pot_odds, required_equity, is_positive_ev, ev_call, hand_strength, hand_rank, outs, outs_cards, recommendation, opponent_stats, hand_actions, hand_community_cards

**Important codebase patterns to follow:**
- Evaluators construct `SkillEvaluation` directly (no helper functions). Each returns `SkillEvaluation(skill_id=..., action_taken=..., evaluation=..., confidence=..., reasoning=...)`.
- Classifier and evaluator dispatch use dicts mapping `skill_id -> method`. New skills must be registered in both dicts.
- `_evaluate_coach_progression()` receives `player_name` and `action` as parameters. The current action is NOT yet in `hand_actions` (action is recorded AFTER evaluation runs). Cross-street flags are safe; same-street data must come from the `amount` parameter.

---

## M2 Review Summary

M2 works well. From real play-testing:
- All 3 Gate 1 skills advanced to Practicing after ~25 hands
- Trigger rates are reasonable (9 trash hand opps, 13 position opps, 6 raise-or-fold opps per ~25 hands)
- Coaching tips now reference specific skills via the SKILL FOCUS section in `_format_stats_for_prompt()`
- Gate 2 has not been unlocked yet (needs 2 of 3 Gate 1 skills at Reliable)

No M2 bugs to fix. The existing marginal evaluation logic correctly skips marginal actions for progression (tested with `TestMarginalNeutrality`).

---

## Pre-Implementation Exploration Findings

Codebase exploration completed before implementation. Key findings that modify the plan:

1. **Step 14 (`check_hand_end()` REST path fix) is UNNECESSARY — skip it.** `check_hand_end()` IS already called from the REST path via: `progress_game()` (game_routes.py:1025) → `handle_evaluating_hand_phase()` (game_handler.py:1282) → `check_hand_end()` (game_handler.py:1028). Both REST and WebSocket action handlers call `progress_game()` after processing actions, and `progress_game()`'s while loop handles `EVALUATING_HAND` phase by calling `handle_evaluating_hand_phase()`.

2. **Hand number access**: Available via `game_data['memory_manager'].hand_count` inside `_evaluate_coach_progression()`. No need to add `hand_number` as a new function parameter — extract directly from `game_data` (already passed to the function).

3. **Skill versioning (Step 12) — tiered initialization**: Use `Introduced` for skills in the player's current unlocked gate, `Practicing` for skills in gates the player has already passed (per requirements §11.6). A gate is "passed" if the next gate is also unlocked.

4. **Existing dispatch patterns** confirmed: Both `SituationClassifier._check_skill_trigger()` (situation_classifier.py:85) and `SkillEvaluator.evaluate()` (skill_evaluator.py:63) use dict-based dispatch. New skills must be registered in both dicts.

5. **`build_poker_context()`** (skill_definitions.py:193) is the single source of truth for evaluation context. All new multi-street and equity fields go here.

---

## M3 Scope (from Requirements §4, §8.3, §8.4, §10.5-10.6, §5.2, §9.5, §11.6)

**What ships:**
- Gate 3 skills: Draws Need Price, Respect Big Bets, Have a Plan for the Hand
- Gate 4 skills: Don't Pay Off Double Barrels, Size Your Bets With Purpose
- Multi-street action tracking (needed for skills 9, 10)
- Bet sizing evaluation (needed for skill 11)
- Enhanced hand review with multi-skill coverage
- Player explanation support in hand review
- Practicing mode split (LEARN vs COMPETE at 60% accuracy threshold)
- Skill definition versioning for existing players
- `check_hand_end()` REST path fix
- Marginal band tuning based on M1-M2 playtesting data

**What already exists from M1+M2:**
- State machine (advancement + regression logic) ✓
- Gate unlock logic (checks previous gate's skills, reloads after mutations) ✓
- Persistence (tables, CRUD) ✓
- Coaching decision with session memory cadence ✓
- Post-action hook in game_routes ✓
- `build_poker_context()` with post-flop fields ✓
- Skill context in LLM prompts ✓
- Onboarding with multi-level initialization ✓

**What M3 actually needs to add:**
1. Multi-street action tracking + equity/pot-odds fields in `build_poker_context()`
2. `player_name` and `bet_to_pot_ratio` threading through coaching data
3. Gate 3 skill definitions + evaluators + classifiers
4. Gate 4 skill definitions + evaluators + classifiers
5. Enhanced hand review with multi-skill focus (SessionMemory.hand_evaluations)
6. Player explanation support in hand review endpoint
7. Practicing mode split (LEARN/COMPETE at 60% threshold)
8. Skill definition versioning for existing players with unlocked gates
9. `check_hand_end()` call from REST action path
10. Updated onboarding for experienced level (Gate 3)
11. Marginal band tuning (review thresholds)

---

## Implementation Plan

> **Implementation order note**: Steps 1-2 (context fields) must be implemented before Steps 5-6 (evaluators), because the evaluators reference fields like `equity`, `required_equity`, `player_bet_flop`, `opponent_double_barrel`, and `bet_to_pot_ratio` that are added in Steps 1-2.

### Step 1: Multi-street action tracking + equity/pot-odds fields in `build_poker_context()`

**Why**: Skills 9 ("Have a Plan") and 10 ("Don't Pay Off Double Barrels") need to know what the player did on prior streets this hand. The "Draws Need Price" evaluator needs `equity` and `required_equity`. The current `build_poker_context()` only sees the current street's state.

**Modify**: `flask_app/services/skill_definitions.py` — `build_poker_context()`

Add fields derived from `coaching_data['hand_actions']` and equity data:

```python
# --- Multi-street context ---

# Extract player's own prior actions this hand
hand_actions = coaching_data.get('hand_actions', [])
player_name = coaching_data.get('player_name', '')

# Player's actions by phase (list per phase — a player may act multiple times per street)
from collections import defaultdict
player_actions_by_phase = defaultdict(list)
for a in hand_actions:
    if a.get('player_name') == player_name:
        player_actions_by_phase[a['phase']].append(a['action'])

# Opponent actions by phase (list per phase — multiple opponents or re-raises possible)
opponent_bets_by_phase = defaultdict(list)
for a in hand_actions:
    if a.get('player_name') != player_name and a['action'] in ('raise', 'bet', 'all_in'):
        opponent_bets_by_phase[a['phase']].append(a)

# Derived multi-street booleans
# "player bet flop" = any aggressive action (bet/raise/all_in) on the flop
_aggressive = {'raise', 'bet', 'all_in'}
player_bet_flop = bool(_aggressive & set(player_actions_by_phase.get('FLOP', [])))
player_bet_turn = bool(_aggressive & set(player_actions_by_phase.get('TURN', [])))
opponent_bet_flop = len(opponent_bets_by_phase.get('FLOP', [])) > 0
opponent_bet_turn = len(opponent_bets_by_phase.get('TURN', [])) > 0
opponent_double_barrel = opponent_bet_flop and opponent_bet_turn

# --- Equity fields (for Draws Need Price evaluator) ---
equity = coaching_data.get('equity')
required_equity = coaching_data.get('required_equity')

# --- Bet sizing context ---
# NOTE: bet_to_pot_ratio for the CURRENT action is injected by
# _evaluate_coach_progression() in game_routes.py, not derived from
# hand_actions (because the current action hasn't been recorded yet).
# This field may also be set from hand_actions for prior-action analysis.
bet_to_pot_ratio = coaching_data.get('bet_to_pot_ratio', 0)
```

Context dict adds: `player_actions_by_phase`, `player_bet_flop`, `player_bet_turn`, `opponent_bet_flop`, `opponent_bet_turn`, `opponent_double_barrel`, `equity`, `required_equity`, `bet_to_pot_ratio`

> **Design note**: `player_actions_by_phase` and `opponent_bets_by_phase` use lists because a player can act multiple times per street (e.g., check then call, or bet then get raised and call). The boolean flags (`player_bet_flop`, etc.) check if *any* action in the list was aggressive, which is the correct semantic for "did the player bet the flop?".

> **Timing note**: At evaluation time, the current action has NOT been recorded into `hand_actions` (action recording happens after `_evaluate_coach_progression()` runs). This means cross-street flags are safe (`player_bet_flop` evaluated on TURN is correct), but same-street data like `bet_to_pot_ratio` must be injected separately from the `amount` parameter. See Step 2.

### Step 2: Thread `player_name` and `bet_to_pot_ratio` through coaching data

**Why**: `build_poker_context()` needs `player_name` to filter `hand_actions` by player. `bet_to_pot_ratio` for the current action must come from the `amount` parameter (not `hand_actions`, where the current action hasn't been recorded yet).

**Modify**: `flask_app/services/coach_engine.py` — `compute_coaching_data()`

Add `player_name` to the returned dict (line ~365, before `return result`):
```python
result['player_name'] = player_name
```

**Modify**: `flask_app/routes/game_routes.py` — `_evaluate_coach_progression()`

The function signature already has `player_name` and `action` as parameters. Add `amount` and `hand_number`:

```python
def _evaluate_coach_progression(game_id: str, player_name: str, action: str,
                                 amount: int, game_data: dict,
                                 pre_action_state) -> None:
```

After `compute_coaching_data` returns and before classification, inject the current action's bet sizing:

```python
coaching_data = compute_coaching_data(
    game_id, player_name, game_data=game_data,
    game_state_override=pre_action_state,
)
if not coaching_data:
    return

# Inject current action's bet sizing (not available from hand_actions
# because the current action hasn't been recorded yet)
if action in ('raise', 'bet', 'all_in') and amount > 0:
    pot_total = coaching_data.get('pot_total', 0)
    coaching_data['bet_to_pot_ratio'] = amount / pot_total if pot_total > 0 else 0
```

**Also update** both call sites of `_evaluate_coach_progression()` in `game_routes.py` (lines ~1003 and ~1293) to pass `amount`:
```python
_evaluate_coach_progression(game_id, current_player.name, action, amount, current_game_data, pre_action_state)
```

**Hand number**: Extract from `game_data` inside the function rather than adding as a parameter:
```python
memory_manager = game_data.get('memory_manager')
hand_number = memory_manager.hand_count if memory_manager else 0
```

### Step 3: Gate 3 skill definitions

**Modify**: `flask_app/services/skill_definitions.py`

Add 3 new skills:

```python
SKILL_DRAWS_NEED_PRICE = SkillDefinition(
    skill_id='draws_need_price',
    name='Draws Need Price',
    description='Only call with a draw when pot odds justify it.',
    gate=3,
    evidence_rules=EvidenceRules(
        min_opportunities=6,
        window_size=30,
        advancement_threshold=0.70,
        regression_threshold=0.55,
    ),
    phases=frozenset({'FLOP', 'TURN'}),
    tags=frozenset({'pot_odds', 'draws', 'postflop'}),
)

SKILL_RESPECT_BIG_BETS = SkillDefinition(
    skill_id='respect_big_bets',
    name='Respect Big Bets',
    description='Fold medium hands facing large bets (>=50% pot) on turn or river.',
    gate=3,
    evidence_rules=EvidenceRules(
        min_opportunities=6,
        window_size=30,
        advancement_threshold=0.65,
        regression_threshold=0.50,
    ),
    phases=frozenset({'TURN', 'RIVER'}),
    tags=frozenset({'bet_reading', 'postflop'}),
)

SKILL_HAVE_A_PLAN = SkillDefinition(
    skill_id='have_a_plan',
    name='Have a Plan for the Hand',
    description="Don't bet the flop then check-fold the turn without reason.",
    gate=3,
    evidence_rules=EvidenceRules(
        min_opportunities=6,
        window_size=30,
        advancement_threshold=0.75,  # Higher — this is about consistency
        regression_threshold=0.60,
    ),
    phases=frozenset({'TURN'}),  # Evaluated on turn after flop action
    tags=frozenset({'multi_street', 'planning', 'postflop'}),
)
```

Add Gate 3 definition:
```python
GATE_3 = GateDefinition(
    gate_number=3,
    name='Pressure Recognition',
    description='Understand pot odds on draws, respect aggression, follow through on plans.',
    skill_ids=('draws_need_price', 'respect_big_bets', 'have_a_plan'),
    required_reliable=2,
)
```

### Step 4: Gate 4 skill definitions

**Modify**: `flask_app/services/skill_definitions.py`

Add 2 new skills:

```python
SKILL_DONT_PAY_DOUBLE_BARRELS = SkillDefinition(
    skill_id='dont_pay_double_barrels',
    name="Don't Pay Off Double Barrels",
    description='Fold marginal hands when opponents bet multiple streets.',
    gate=4,
    evidence_rules=EvidenceRules(
        min_opportunities=5,
        window_size=30,
        advancement_threshold=0.60,
        regression_threshold=0.45,
    ),
    phases=frozenset({'TURN', 'RIVER'}),  # Evaluated when facing 2nd+ barrel
    tags=frozenset({'multi_street', 'bet_reading', 'postflop'}),
)

SKILL_SIZE_BETS_WITH_PURPOSE = SkillDefinition(
    skill_id='size_bets_with_purpose',
    name='Size Your Bets With Purpose',
    description='Size bets proportional to the pot — not too small, not too big.',
    gate=4,
    evidence_rules=EvidenceRules(
        min_opportunities=12,
        window_size=30,
        advancement_threshold=0.65,
        regression_threshold=0.50,
    ),
    phases=frozenset({'FLOP', 'TURN', 'RIVER'}),
    tags=frozenset({'bet_sizing', 'postflop'}),
)
```

Add Gate 4 definition:
```python
GATE_4 = GateDefinition(
    gate_number=4,
    name='Multi-Street Thinking',
    description='Recognize multi-street aggression and size your bets with purpose.',
    skill_ids=('dont_pay_double_barrels', 'size_bets_with_purpose'),
    required_reliable=2,  # Both skills must reach Reliable (stricter than Gates 1-3's 2-of-3)
)
```

> **Design note**: Gate 4 has only 2 skills with `required_reliable=2`, meaning BOTH must reach Reliable. This is intentionally stricter than Gates 1-3 (2-of-3). The requirements note "[Additional skills TBD]" for Gate 4. If a third skill is added later, consider keeping `required_reliable=2` for the 2-of-3 pattern.

Update registries: `ALL_SKILLS`, `ALL_GATES`.

### Step 5: Gate 3 situation classifiers

**Modify**: `flask_app/services/situation_classifier.py`

Add trigger checkers:

```python
def _check_draws_need_price_trigger(self, ctx):
    """Trigger when facing a bet with a draw."""
    return (ctx['phase'] in ('FLOP', 'TURN')
            and ctx.get('has_draw', False)
            and ctx['cost_to_call'] > 0)

def _check_respect_big_bets_trigger(self, ctx):
    """Trigger when facing a big bet (>=50% pot) on turn/river with medium hand."""
    if ctx['phase'] not in ('TURN', 'RIVER'):
        return False
    # Big bet = cost_to_call >= 50% of pot before the bet
    pot_before_bet = ctx['pot_total'] - ctx['cost_to_call']
    is_big_bet = pot_before_bet > 0 and ctx['cost_to_call'] >= pot_before_bet * 0.5
    # Medium hand = has pair but not strong (rank 9 = one pair)
    is_medium = ctx.get('has_pair', False) and not ctx.get('is_strong_hand', False)
    return is_big_bet and is_medium

def _check_have_a_plan_trigger(self, ctx):
    """Trigger on turn when player bet the flop."""
    return (ctx['phase'] == 'TURN'
            and ctx.get('player_bet_flop', False))
```

> **Known limitation**: `pot_before_bet = pot_total - cost_to_call` is an approximation. In multi-raise pots, `cost_to_call` may not equal the opponent's original bet size. For the target audience (beginner-intermediate in 6-max), multi-raise pots are rare enough that this is acceptable.

Register all three in the `_check_skill_trigger` dispatch dict:
```python
checkers = {
    # ... existing Gate 1-2 entries ...
    'draws_need_price': self._check_draws_need_price_trigger,
    'respect_big_bets': self._check_respect_big_bets_trigger,
    'have_a_plan': self._check_have_a_plan_trigger,
}
```

### Step 6: Gate 4 situation classifiers

**Modify**: `flask_app/services/situation_classifier.py`

```python
def _check_dont_pay_double_barrels_trigger(self, ctx):
    """Trigger when opponent has bet both flop and turn, player has marginal hand."""
    if ctx['phase'] not in ('TURN', 'RIVER'):
        return False
    is_double_barrel = ctx.get('opponent_double_barrel', False)
    # Marginal = has pair but not strong
    is_marginal = ctx.get('has_pair', False) and not ctx.get('is_strong_hand', False)
    return is_double_barrel and is_marginal and ctx['cost_to_call'] > 0

def _check_size_bets_with_purpose_trigger(self, ctx):
    """Trigger on any post-flop street. The evaluator filters to actual bets/raises."""
    # This trigger fires broadly — the evaluator returns not_applicable when the
    # player didn't bet or raise. This is intentional: bet sizing can only be
    # evaluated AFTER the action, so we trigger on all post-flop situations and
    # let the evaluator decide. The not_applicable rate will be high but harmless.
    return ctx['phase'] in ('FLOP', 'TURN', 'RIVER')
```

Register in the `_check_skill_trigger` dispatch dict:
```python
checkers = {
    # ... existing entries ...
    'dont_pay_double_barrels': self._check_dont_pay_double_barrels_trigger,
    'size_bets_with_purpose': self._check_size_bets_with_purpose_trigger,
}
```

### Step 7: Gate 3 skill evaluators

**Modify**: `flask_app/services/skill_evaluator.py`

All evaluators follow the existing pattern: construct `SkillEvaluation` directly.

```python
def _eval_draws_need_price(self, action: str, ctx: dict) -> SkillEvaluation:
    """Draw + facing bet: call when pot odds are good, fold when bad."""
    if not ctx.get('has_draw') or ctx['cost_to_call'] <= 0:
        return SkillEvaluation(
            skill_id='draws_need_price', action_taken=action,
            evaluation='not_applicable', confidence=1.0,
            reasoning='Not facing bet with draw',
        )

    required_equity = ctx.get('required_equity') or 0
    equity = ctx.get('equity') or 0

    # Need both equity values for a meaningful evaluation
    if required_equity > 0 and equity > 0:
        if equity >= required_equity:
            # Good pot odds: calling is correct
            if action == 'call' or action.startswith('raise'):
                return SkillEvaluation(
                    skill_id='draws_need_price', action_taken=action,
                    evaluation='correct', confidence=0.9,
                    reasoning='Called/raised with good pot odds on draw',
                )
            if action == 'fold':
                return SkillEvaluation(
                    skill_id='draws_need_price', action_taken=action,
                    evaluation='incorrect', confidence=0.8,
                    reasoning='Folded a profitable draw',
                )
        else:
            # Bad pot odds: folding is correct
            if action == 'fold':
                return SkillEvaluation(
                    skill_id='draws_need_price', action_taken=action,
                    evaluation='correct', confidence=0.9,
                    reasoning='Folded draw without proper pot odds',
                )
            if action == 'call':
                return SkillEvaluation(
                    skill_id='draws_need_price', action_taken=action,
                    evaluation='incorrect', confidence=0.8,
                    reasoning='Called draw without pot odds to justify it',
                )

    # Insufficient equity data — neutral
    return SkillEvaluation(
        skill_id='draws_need_price', action_taken=action,
        evaluation='marginal', confidence=0.3,
        reasoning='Insufficient equity data to evaluate pot odds',
    )

def _eval_respect_big_bets(self, action: str, ctx: dict) -> SkillEvaluation:
    """Medium hand + big bet on turn/river: fold is correct."""
    pot_before_bet = ctx['pot_total'] - ctx['cost_to_call']
    is_big_bet = pot_before_bet > 0 and ctx['cost_to_call'] >= pot_before_bet * 0.5
    is_medium = ctx.get('has_pair') and not ctx.get('is_strong_hand')
    if not (is_big_bet and is_medium):
        return SkillEvaluation(
            skill_id='respect_big_bets', action_taken=action,
            evaluation='not_applicable', confidence=1.0,
            reasoning='Not a big bet with medium hand',
        )

    if action == 'fold':
        return SkillEvaluation(
            skill_id='respect_big_bets', action_taken=action,
            evaluation='correct', confidence=0.9,
            reasoning='Folded medium hand facing big bet — good discipline',
        )
    if action == 'call':
        return SkillEvaluation(
            skill_id='respect_big_bets', action_taken=action,
            evaluation='incorrect', confidence=0.8,
            reasoning='Called big bet with medium hand — likely dominated',
        )
    if action.startswith('raise'):
        return SkillEvaluation(
            skill_id='respect_big_bets', action_taken=action,
            evaluation='incorrect', confidence=0.8,
            reasoning='Raised into big bet with medium hand',
        )
    return SkillEvaluation(
        skill_id='respect_big_bets', action_taken=action,
        evaluation='marginal', confidence=0.5,
        reasoning='Ambiguous action facing big bet',
    )

def _eval_have_a_plan(self, action: str, ctx: dict) -> SkillEvaluation:
    """Turn after betting flop: check-fold = incorrect, follow through = correct."""
    if not ctx.get('player_bet_flop'):
        return SkillEvaluation(
            skill_id='have_a_plan', action_taken=action,
            evaluation='not_applicable', confidence=1.0,
            reasoning='Player did not bet the flop',
        )

    if action in ('raise', 'bet') or action.startswith('raise'):
        return SkillEvaluation(
            skill_id='have_a_plan', action_taken=action,
            evaluation='correct', confidence=0.9,
            reasoning='Followed through on flop aggression',
        )
    if action == 'call':
        return SkillEvaluation(
            skill_id='have_a_plan', action_taken=action,
            evaluation='marginal', confidence=0.6,
            reasoning='Called on turn after flop bet — passive but not a collapse',
        )
    if action == 'check':
        return SkillEvaluation(
            skill_id='have_a_plan', action_taken=action,
            evaluation='marginal', confidence=0.5,
            reasoning='Checked turn after flop bet — lost initiative',
        )
    if action == 'fold':
        return SkillEvaluation(
            skill_id='have_a_plan', action_taken=action,
            evaluation='incorrect', confidence=0.8,
            reasoning='Bet flop then folded turn — no plan',
        )
    return SkillEvaluation(
        skill_id='have_a_plan', action_taken=action,
        evaluation='marginal', confidence=0.4,
        reasoning='Ambiguous action on turn after flop bet',
    )
```

> **Known limitation**: `_eval_have_a_plan` marks all turn folds after a flop bet as `incorrect`, even when board texture changes make folding correct (e.g., scare card appears). The evaluator has no concept of board runout changes between streets. This is a simplification — the heuristic "follow through on your plan" is correct most of the time for the target audience. A more robust approach would compare flop vs turn `hand_rank` or `has_draw` status, but that requires storing per-street context. Acceptable for M3; defer improvement if playtesting reveals issues.

Register in the `evaluators` dispatch dict in `evaluate()`:
```python
evaluators = {
    # ... existing Gate 1-2 entries ...
    'draws_need_price': self._eval_draws_need_price,
    'respect_big_bets': self._eval_respect_big_bets,
    'have_a_plan': self._eval_have_a_plan,
}
```

### Step 8: Gate 4 skill evaluators

**Modify**: `flask_app/services/skill_evaluator.py`

```python
def _eval_dont_pay_double_barrels(self, action: str, ctx: dict) -> SkillEvaluation:
    """Facing double barrel with marginal hand: fold is correct."""
    if not ctx.get('opponent_double_barrel') or ctx['cost_to_call'] <= 0:
        return SkillEvaluation(
            skill_id='dont_pay_double_barrels', action_taken=action,
            evaluation='not_applicable', confidence=1.0,
            reasoning='Not facing double barrel',
        )

    is_marginal = ctx.get('has_pair') and not ctx.get('is_strong_hand')
    if not is_marginal:
        return SkillEvaluation(
            skill_id='dont_pay_double_barrels', action_taken=action,
            evaluation='not_applicable', confidence=1.0,
            reasoning='Hand is not marginal',
        )

    if action == 'fold':
        return SkillEvaluation(
            skill_id='dont_pay_double_barrels', action_taken=action,
            evaluation='correct', confidence=0.9,
            reasoning='Folded marginal hand vs double barrel',
        )
    if action == 'call':
        return SkillEvaluation(
            skill_id='dont_pay_double_barrels', action_taken=action,
            evaluation='incorrect', confidence=0.8,
            reasoning='Called double barrel with marginal hand',
        )
    if action.startswith('raise'):
        return SkillEvaluation(
            skill_id='dont_pay_double_barrels', action_taken=action,
            evaluation='marginal', confidence=0.5,
            reasoning='Raised vs double barrel — could be a bluff raise',
        )
    return SkillEvaluation(
        skill_id='dont_pay_double_barrels', action_taken=action,
        evaluation='marginal', confidence=0.4,
        reasoning='Ambiguous action vs double barrel',
    )

def _eval_size_bets_with_purpose(self, action: str, ctx: dict) -> SkillEvaluation:
    """When player bets/raises: check bet-to-pot ratio is in 33%-100% range."""
    if action not in ('raise', 'bet', 'all_in') and not action.startswith('raise'):
        return SkillEvaluation(
            skill_id='size_bets_with_purpose', action_taken=action,
            evaluation='not_applicable', confidence=1.0,
            reasoning='Player did not bet or raise',
        )

    ratio = ctx.get('bet_to_pot_ratio', 0)
    if ratio <= 0:
        return SkillEvaluation(
            skill_id='size_bets_with_purpose', action_taken=action,
            evaluation='not_applicable', confidence=1.0,
            reasoning='No bet sizing data',
        )

    # Good sizing: 33% to 100% of pot
    if 0.33 <= ratio <= 1.0:
        return SkillEvaluation(
            skill_id='size_bets_with_purpose', action_taken=action,
            evaluation='correct', confidence=0.9,
            reasoning=f'Good bet sizing ({ratio:.0%} of pot)',
        )
    if 0.25 <= ratio < 0.33 or 1.0 < ratio <= 1.5:
        return SkillEvaluation(
            skill_id='size_bets_with_purpose', action_taken=action,
            evaluation='marginal', confidence=0.6,
            reasoning=f'Borderline bet sizing ({ratio:.0%} of pot)',
        )
    if ratio < 0.25:
        return SkillEvaluation(
            skill_id='size_bets_with_purpose', action_taken=action,
            evaluation='incorrect', confidence=0.8,
            reasoning=f'Bet too small ({ratio:.0%} of pot) — gives cheap draws',
        )
    # ratio > 1.5
    return SkillEvaluation(
        skill_id='size_bets_with_purpose', action_taken=action,
        evaluation='incorrect', confidence=0.7,
        reasoning=f'Bet too large ({ratio:.0%} of pot) — overcommitting',
    )
```

Register in the `evaluators` dispatch dict:
```python
evaluators = {
    # ... existing entries ...
    'dont_pay_double_barrels': self._eval_dont_pay_double_barrels,
    'size_bets_with_purpose': self._eval_size_bets_with_purpose,
}
```

### Step 9: Enhanced hand review with multi-skill coverage

**Change**: Store each `SkillEvaluation` in `SessionMemory` keyed by hand number so hand review can reference them.

**Modify**: `flask_app/services/coach_progression.py` — `SessionMemory`

Add `hand_evaluations` dict:
```python
# In SessionMemory.__init__:
self.hand_evaluations: dict[int, list] = defaultdict(list)
# (list of SkillEvaluation — import at top of file)

# New methods:
def record_hand_evaluation(self, hand_number: int, evaluation) -> None:
    """Store a skill evaluation for later hand review retrieval."""
    if evaluation.evaluation != 'not_applicable':
        self.hand_evaluations[hand_number].append(evaluation)

def get_hand_evaluations(self, hand_number: int) -> list:
    """Retrieve all skill evaluations for a given hand, sorted for review."""
    evals = self.hand_evaluations.get(hand_number, [])
    # Sort: incorrect first, then marginal, then correct
    priority = {'incorrect': 0, 'marginal': 1, 'correct': 2}
    return sorted(evals, key=lambda e: priority.get(e.evaluation, 3))
```

**Modify**: `flask_app/routes/game_routes.py` — `_evaluate_coach_progression()`

Add `hand_number` parameter and SessionMemory recording:

```python
def _evaluate_coach_progression(game_id: str, player_name: str, action: str,
                                 amount: int, game_data: dict,
                                 pre_action_state) -> None:
    # ... existing code to compute coaching_data, classify, evaluate_and_update ...

    # Get or create session memory
    from flask_app.services.coach_progression import SessionMemory
    session_memory = game_data.get('coach_session_memory')
    if session_memory is None:
        session_memory = SessionMemory()
        game_data['coach_session_memory'] = session_memory

    # Get hand number from memory manager
    memory_manager = game_data.get('memory_manager')
    hand_number = 0
    if memory_manager and hasattr(memory_manager, 'hand_recorder'):
        hand_number = getattr(memory_manager.hand_recorder, 'hand_count', 0)

    # Record evaluations for hand review
    if evaluations:
        for ev in evaluations:
            session_memory.record_hand_evaluation(hand_number, ev)
```

**Modify**: `flask_app/routes/coach_routes.py` — `coach_hand_review()`

Retrieve evaluations and format them for the LLM prompt. Also switch to mode-aware coach factory with REVIEW mode:

```python
@coach_bp.route('/api/coach/<game_id>/hand-review', methods=['POST'])
def coach_hand_review(game_id: str):
    # ... existing hand loading code ...

    body = request.get_json(silent=True) or {}
    request_player_name = body.get('playerName', '')
    explanation = body.get('explanation', '').strip()  # Step 10: player explanation

    hand = completed_hands[-1]
    context = build_hand_context_from_recorded_hand(hand, player_name)
    hand_text = format_hand_context_for_prompt(context, player_name)

    # Append skill evaluations from SessionMemory (if available)
    session_memory = game_data.get('coach_session_memory')
    hand_number = getattr(hand, 'hand_number', None)
    if session_memory and hand_number is not None:
        evaluations = session_memory.get_hand_evaluations(hand_number)
        if evaluations:
            skill_eval_text = "\n\nSKILL EVALUATIONS FOR THIS HAND:\n"
            for ev in evaluations:
                skill_eval_text += f"- {ev.skill_id}: {ev.evaluation} — {ev.reasoning}\n"
            hand_text += skill_eval_text

    # Append player explanation (Step 10)
    if explanation:
        hand_text += f"\n\nPlayer's explanation: {explanation}"

    # Use mode-aware coach with REVIEW mode
    coach = get_or_create_coach_with_mode(
        game_data, game_id,
        player_name=request_player_name or player_name,
        mode='review',
        skill_context='',
    )

    review = coach.review_hand(hand_text)
    return jsonify({'review': review, 'hand_number': hand_number})
```

> **Design note**: Evaluations are stored in `SessionMemory` (in-memory, per game session) rather than persisted to the database. This is consistent with the existing session memory design (§13.3 of requirements) — if the server restarts, hand review evaluations are lost, but persisted skill stats are unaffected. The worst case is a hand review without skill-specific context, which falls back to the existing generic review behavior.

> **Fallback**: When `session_memory` is None (server restart mid-game, or first hand before coaching triggers), the hand review proceeds without skill evaluation context — identical to current M2 behavior.

**Modify**: `flask_app/services/coach_assistant.py` — `HAND_REVIEW_PROMPT`

Update to conditionally handle skill evaluations:
```python
HAND_REVIEW_PROMPT = """\
Review this completed hand from the player's perspective. Be concise (3-5 sentences).

Structure your review as:
1. One sentence summarizing what happened
2. One sentence on what the player did well OR the key mistake
3. One sentence of specific advice for similar situations

If SKILL EVALUATIONS FOR THIS HAND are provided above, reference them:
- Cover incorrect evaluations first (1-2 sentences each)
- Then mention correct applications briefly
- Keep each skill's review to 1-2 sentences

If the player provided an explanation, acknowledge their reasoning and compare it with the stats.

Be honest — if they played well, say so briefly. If they made an error, explain what the better play was and why (use pot odds/equity math if relevant). Don't sugarcoat, but don't be harsh either.\
"""
```

### Step 10: Player explanation support in hand review

**Already integrated into Step 9 above.** The `explanation` field is read from the POST body and appended to `hand_text` before calling `review_hand()`.

> **Note**: This is backend only for M3. Frontend integration (UI to enter explanation) is deferred to M4.

### Step 11: Practicing mode split (LEARN vs COMPETE)

**Why**: Requirements §5.1-5.2 define that Practicing players with accuracy >= 60% should receive Compete mode coaching (descriptive, stat-surfacing) rather than Learn mode (prescriptive). The M2 code has an explicit TODO for this at `coach_progression.py:478-479`.

**Modify**: `flask_app/services/coach_progression.py` — `_determine_mode()`

```python
def _determine_mode(self, skill_state: Optional[PlayerSkillState]) -> CoachingMode:
    """Determine coaching mode from skill state."""
    if not skill_state:
        return CoachingMode.LEARN

    if skill_state.state == SkillState.INTRODUCED:
        return CoachingMode.LEARN
    if skill_state.state == SkillState.PRACTICING:
        # Split: accuracy >= 60% gets Compete mode (per §5.2 of requirements)
        if skill_state.window_accuracy >= 0.60:
            return CoachingMode.COMPETE
        return CoachingMode.LEARN
    if skill_state.state == SkillState.RELIABLE:
        return CoachingMode.COMPETE
    if skill_state.state == SkillState.AUTOMATIC:
        return CoachingMode.SILENT
    return CoachingMode.LEARN
```

### Step 12: Skill definition versioning for existing players

**Why**: Requirements §11.6 specifies that when new skills are added to an existing gate, they should appear at `Introduced` state for players who already have that gate unlocked. Without this, existing "experienced" players (who have Gate 3 unlocked from onboarding) and players who naturally unlocked Gate 3 during M2 will have no Gate 3 skill rows.

**Modify**: `flask_app/services/coach_progression.py` — `get_player_state()` or `get_or_initialize_player()`

Add a check that initializes missing skills for already-unlocked gates, with tiered state:

```python
def _ensure_skills_for_unlocked_gates(self, user_id: str,
                                        skill_states: dict,
                                        gate_progress: dict) -> dict:
    """Create missing skill rows for already-unlocked gates.

    Handles the case where new skills are added to an existing gate
    in a deployment (per requirements §11.6).

    Uses tiered initialization:
    - Skills in "passed" gates (next gate is also unlocked) start at Practicing
    - Skills in the current gate (unlocked but next gate is NOT unlocked) start at Introduced
    """
    now = datetime.now().isoformat()

    for gate_num, gp in gate_progress.items():
        if not gp.unlocked:
            continue

        # Determine if this gate is "passed" (next gate is also unlocked)
        next_gate = gate_progress.get(gate_num + 1)
        is_passed = next_gate is not None and next_gate.unlocked
        initial_state = SkillState.PRACTICING if is_passed else SkillState.INTRODUCED

        for skill_def in get_skills_for_gate(gate_num):
            if skill_def.skill_id not in skill_states:
                ss = PlayerSkillState(
                    skill_id=skill_def.skill_id,
                    state=initial_state,
                    first_seen_at=now,
                )
                self._coach_repo.save_skill_state(user_id, ss)
                skill_states[skill_def.skill_id] = ss
                logger.info(f"Initialized missing skill {skill_def.skill_id} "
                            f"for user {user_id} (gate {gate_num}, state={initial_state.value})")

    return skill_states
```

Call this from `get_player_state()` after loading skill_states and gate_progress, before returning.

### Step 13: Update onboarding for experienced level

**Modify**: `flask_app/services/coach_progression.py` — `initialize_player()`

Currently experienced level sets Gate 1 Reliable + Gate 2 Practicing. Per requirements §11.4, the experienced level should also initialize Gate 3:

| Level | Gate 1 | Gate 2 | Gate 3 |
|-------|--------|--------|--------|
| Beginner | Introduced | — | — |
| Intermediate | Practicing | Introduced | — |
| Experienced | Reliable | Practicing | Introduced |

Add to the `experienced` branch of `initialize_player()`:

```python
# Experienced: also unlock Gate 3
if level == 'experienced':
    self._coach_repo.save_gate_progress(
        user_id, GateProgress(gate_number=3, unlocked=True, unlocked_at=now)
    )
    for skill_def in get_skills_for_gate(3):
        ss = PlayerSkillState(
            skill_id=skill_def.skill_id,
            state=SkillState.INTRODUCED,
            first_seen_at=now,
        )
        self._coach_repo.save_skill_state(user_id, ss)
```

Gate 4 remains locked for all starting levels — it unlocks through normal progression.

### Step 14: ~~Fix `check_hand_end()` on REST action path~~ — SKIPPED

**SKIPPED**: Pre-implementation exploration confirmed this is unnecessary. `check_hand_end()` IS already called from the REST path via: `progress_game()` (game_routes.py:1025) → `handle_evaluating_hand_phase()` (game_handler.py:1282) → `check_hand_end()` (game_handler.py:1028). Both REST and WebSocket paths call `progress_game()` after processing actions.

### Step 15: Marginal band tuning

Review the M1-M2 evaluation logic and adjust marginal bands where needed:

- Gate 1 skills: Currently working well, no changes needed
- Gate 2 `flop_connection`: Air detection uses `hand_rank >= 10` — verify this matches actual play data
- Gate 2 `bet_when_strong`: Strong = rank ≤ 8 (two pair+). Consider whether this is too conservative (should one pair top-kicker count?)
- Gate 3 `draws_need_price`: The equity comparison handles the marginal band naturally (equity ~= required_equity is a grey zone)
- Gate 3 `respect_big_bets`: The 50% pot threshold may need tuning

No code changes unless play data reveals issues. Document thresholds for future reference.

### Step 16: Tests

**Create/modify** tests:

- `tests/test_skill_definitions.py`:
  - Gate 3+4 skill definitions exist in ALL_SKILLS
  - Gate 3+4 gate definitions exist in ALL_GATES
  - `build_poker_context()` multi-street fields: `player_bet_flop`, `player_bet_turn`, `opponent_double_barrel`
  - `build_poker_context()` equity/required_equity fields
  - `build_poker_context()` `bet_to_pot_ratio` passthrough
  - `player_name` filtering of hand_actions
  - Edge case: empty hand_actions, missing player_name

- `tests/test_situation_classifier.py`:
  - Gate 3 triggers: draws with bets, big bets on turn/river, flop-then-turn aggression
  - Gate 4 triggers: double barrels, bet sizing (all post-flop)
  - Trigger does NOT fire in wrong phase
  - `size_bets_with_purpose` fires on all post-flop (evaluator filters)

- `tests/test_skill_evaluator.py`:
  - Gate 3: draws_need_price (good odds call=correct, bad odds fold=correct, etc.)
  - Gate 3: respect_big_bets (fold medium=correct, call=incorrect)
  - Gate 3: have_a_plan (follow through=correct, check-fold=incorrect)
  - Gate 4: dont_pay_double_barrels (fold marginal=correct, call=incorrect)
  - Gate 4: size_bets_with_purpose (33-100% correct, <25% incorrect, >150% incorrect)
  - Edge cases: missing equity data, zero pot, forced all-in
  - bet_to_pot_ratio from ctx (not from hand_actions)

- `tests/test_coach_progression.py`:
  - Gate unlock chain: 1→2→3→4 end-to-end
  - Experienced onboarding: Gate 3 skills at Introduced, Gate 3 unlocked
  - Skill definition versioning: unlocked gate + missing skill rows → auto-initialized
  - Practicing mode split: accuracy < 60% → LEARN, accuracy >= 60% → COMPETE
  - SessionMemory.hand_evaluations: record, retrieve, sort order
  - SessionMemory.hand_evaluations: not_applicable filtered out
  - Regression tests: existing Gate 1-2 behavior unchanged

---

## Files Modified (Summary)

| File | Change |
|------|--------|
| `flask_app/services/skill_definitions.py` | Gate 3+4 skills/gates, extended `build_poker_context()` with multi-street + equity + bet sizing |
| `flask_app/services/situation_classifier.py` | Gate 3+4 trigger checkers, registered in dispatch dict |
| `flask_app/services/skill_evaluator.py` | Gate 3+4 evaluation methods (using existing SkillEvaluation pattern), registered in dispatch dict |
| `flask_app/services/coach_progression.py` | SessionMemory.hand_evaluations, `_determine_mode()` practicing split, `initialize_player()` Gate 3 for experienced, `_ensure_skills_for_unlocked_gates()` versioning |
| `flask_app/services/coach_engine.py` | Add `player_name` to `compute_coaching_data()` result dict |
| `flask_app/services/coach_assistant.py` | Enhanced `HAND_REVIEW_PROMPT` with conditional skill evaluation + explanation instructions |
| `flask_app/routes/coach_routes.py` | Hand review: skill eval context from SessionMemory, mode-aware coach factory (REVIEW mode), player explanation support |
| `flask_app/routes/game_routes.py` | `_evaluate_coach_progression()`: add `amount` param, inject `bet_to_pot_ratio`, extract `hand_number` from `game_data`, SessionMemory recording |
| `tests/test_skill_definitions.py` | Gate 3+4 tests, multi-street context tests, equity fields, bet_to_pot_ratio |
| `tests/test_situation_classifier.py` | Gate 3+4 trigger tests |
| `tests/test_skill_evaluator.py` | Gate 3+4 evaluator tests with edge cases |
| `tests/test_coach_progression.py` | Gate unlock chain 1→4, experienced onboarding, skill versioning, practicing mode split, SessionMemory.hand_evaluations |

---

## Key Design Decisions

1. **Multi-street tracking via hand_actions**: Rather than adding new persistence, we derive multi-street context from the existing `hand_actions` list in `coaching_data`. This is already populated by the hand recorder and available at evaluation time. Cross-street flags are safe; same-street data (bet_to_pot_ratio) must be injected separately.

2. **Bet sizing uses `amount` parameter, not `hand_actions`**: The current action hasn't been recorded into `hand_actions` when `_evaluate_coach_progression()` runs (recording happens after evaluation). So `bet_to_pot_ratio` is computed in `_evaluate_coach_progression()` from the `amount` parameter and injected into `coaching_data` directly.

3. **Bet sizing evaluation**: Uses bet-to-pot ratio. Good range is 33%-100% of pot. Below 25% or above 150% is incorrect. The 25-33% and 100-150% ranges are marginal. This is simple and teachable. Hand-strength correlation (per requirements) is not included in M3 — the simplified ratio-only approach is sufficient for the target audience.

4. **"Have a Plan" evaluation**: Only triggers on TURN when player bet the FLOP. Check-fold on turn = incorrect. This teaches follow-through without being too prescriptive about strategy. Known limitation: doesn't account for board texture changes (scare cards).

5. **"Don't Pay Off Double Barrels"**: Requires both `opponent_bet_flop` and `opponent_bet_turn` to be True in the context. Only applies to marginal hands (has pair but not strong). Strong hands should obviously continue.

6. **"Draws Need Price" uses existing equity/required_equity**: The `compute_coaching_data()` already calculates these. The evaluator uses explicit `or 0` checks instead of truthiness to handle `0.0` equity values correctly.

7. **"Respect Big Bets" threshold**: 50% pot is the threshold (matching requirements). `pot_before_bet = pot_total - cost_to_call` estimates the pot size before the opponent's bet. Known limitation: approximation degrades in multi-raise pots.

8. **Hand review enhancement**: Uses the existing `/api/coach/<game_id>/hand-review` endpoint. Adds skill evaluations as additional context to the LLM prompt rather than building a separate evaluation system. Falls back to generic review when SessionMemory is unavailable.

9. **Player explanation**: Simple — just appends to the hand review text. The LLM naturally considers it alongside the stats. Backend only for M3; frontend integration deferred to M4.

10. **Gate 3-4 window size**: 30 opportunities (same as Gate 2). These skills trigger less frequently (~3-6 per 50 hands), so a 30-opportunity window may span 300-750 hands — larger than the requirements' hand-based recommendation of 150-200 hands, which is appropriate.

11. **Gate 4 `required_reliable=2` of 2**: Both skills must reach Reliable. This is stricter than Gates 1-3 (2 of 3). Intentional — a third skill may be added later.

12. **Skill definition versioning**: Runtime check in `get_player_state()` auto-initializes missing skill rows for already-unlocked gates. This handles both the deployment case (new skills added) and the existing "experienced" onboarding case.

13. **Practicing mode split**: Players at Practicing with >= 60% accuracy get COMPETE mode (stat-surfacing, brief reminders). Below 60% stays at LEARN mode (prescriptive teaching). This resolves the explicit M3 TODO in the codebase.

14. **`size_bets_with_purpose` trigger**: Fires on ALL post-flop situations. The evaluator returns `not_applicable` for non-bet actions. This is intentional — bet sizing can only be evaluated after the action, and the broad trigger ensures no betting action is missed.

---

## Implementation Batches

Implement in this order, verifying tests pass after each batch:

| Batch | Steps | What | Key Files |
|-------|-------|------|-----------|
| 1 | 1-2 | Multi-street context fields, `player_name` threading, `bet_to_pot_ratio` injection | `skill_definitions.py`, `coach_engine.py`, `game_routes.py` |
| 2 | 3-4 | Gate 3+4 skill/gate definitions + registry updates | `skill_definitions.py` |
| 3 | 5-8 | Gate 3+4 situation classifiers + skill evaluators | `situation_classifier.py`, `skill_evaluator.py` |
| 4 | 9-13 | Hand review enhancement, practicing mode split, skill versioning, experienced onboarding | `coach_progression.py`, `coach_routes.py`, `coach_assistant.py` |
| 5 | 16 | Tests for all new functionality | `tests/test_*.py` |

**Skipped**: Step 14 (check_hand_end REST path — already works), Step 15 (marginal band tuning — no changes needed per plan).

---

## Verification

1. `python3 scripts/test.py` — all tests pass (existing + new)
2. `python3 scripts/test.py test_skill` — Gate 1-4 skill tests pass
3. `python3 scripts/test.py test_situation` — all trigger tests pass
4. `python3 scripts/test.py test_skill_eval` — all evaluator tests pass
5. `python3 scripts/test.py test_coach_progression` — gate unlock chain (1→2→3→4), session memory, practicing mode split, skill versioning, silent downgrade pass
6. Regression: existing Gate 1-2 tests pass unchanged
7. Manual: play hands with draws facing bets, verify "Draws Need Price" triggers
8. Manual: face large bets with medium hands, verify "Respect Big Bets" triggers
9. Manual: bet flop then fold turn, verify "Have a Plan" evaluates as incorrect
10. Manual: advance Gate 2 skills to Reliable, verify Gate 3 unlocks
11. Manual: verify practicing player at >60% accuracy gets Compete mode coaching
