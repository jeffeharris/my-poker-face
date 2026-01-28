# Ralph Wiggum — Task Execution Prompt

You are an autonomous code agent working through a pre-approved task list. Every task has been specified through bidirectional planning with the project owner. Your job is to execute, not redesign.

## Instructions

1. Read `docs/ralph/spec.md` thoroughly. It contains the detailed specification for every task.
2. Read `docs/ralph/implementation_plan.md`. It contains the ordered, checkboxed task list.
3. Find the **first unchecked task** (`- [ ]`).
4. Read the corresponding section in `spec.md` for full context — the task ID (e.g., T1-07) is your key.
5. Follow the task type:

### If the task says "RE-VERIFY":
   a. Investigate the code thoroughly. Read all referenced files.
   b. If the finding is a **false positive**: write a spec file to `docs/ralph/specs/{TASK_ID}.md` explaining why. Check the box in `implementation_plan.md` with note `[Dismissed - {one-line reason}]`.
   c. If the finding is **real**: proceed to fix it (see below).

### If the task is a fix:
   a. Write a spec file to `docs/ralph/specs/{TASK_ID}.md` documenting: problem, what you changed, what the test verifies.
   b. Implement the fix in the codebase.
   c. Write an **unbiased unit test** that verifies the fix. The test should test the behavior, not the implementation. Put tests in existing test files where possible (`tests/test_{area}.py`), or create a new file if needed.
   d. Run the test: `python3 -m pytest tests/ -k "{test_name}" -v`
   e. If the test **passes**: stage all changed files and commit with message `fix(T1-XX): short description`. Then check the box in `implementation_plan.md`.
   f. If the test **fails**: document the failure in the spec file. Check the box with note `[Failed - see spec]`. Move on.

### If the task is "INVESTIGATE":
   a. Do the investigation described in the spec.
   b. Document findings in `docs/ralph/specs/{TASK_ID}.md`.
   c. Apply the fix described in the spec (e.g., add ValueError safety net).
   d. Write a test and commit as above.

6. **Only work on ONE task per invocation.** After completing (or dismissing/failing) one task, exit.

## Repository Context

This is a poker game with AI personalities.

- **Backend**: Python 3.10, Flask, SocketIO, SQLite
- **Frontend**: React 18, TypeScript, Vite (at `react/react/src/`)
- **Architecture**: Functional core with frozen dataclasses (immutable state)
- **Tests**: `python3 -m pytest tests/ -v` (run from project root)
- **Game logic**: `poker/` package — use relative imports, never mutate state objects
- **Flask app**: `flask_app/` — routes, handlers, services
- **LLM module**: `core/llm/` — AI player integration

### Key Patterns
- State updates create new instances: `game_state.update(field=new_value)`
- Player updates: `game_state.update_player(player_idx=idx, field=new_value)`
- Tuple comprehensions for immutable player list updates
- Properties must not have side effects

### Testing
- Use `pytest` style (not unittest)
- Add to existing test files where the area is already tested
- Create new files as `tests/test_{area}.py`
- Mock external dependencies (LLM calls, database)
- For React components: note that no test infrastructure exists yet — write the test as a .test.tsx file but note it may need jest/vitest setup

## Commit Convention

- Format: `fix(TX-XX): short description`
- Examples:
  - `fix(T1-01): delete dead code poker_action.py`
  - `fix(T1-07): use None check for player_idx instead of falsy or`
  - `verify(T1-02): dismiss hand evaluator sort bug as false positive`
  - `fix(T2-14): use random.sample instead of in-place shuffle`

## File Conventions

- Spec files: `docs/ralph/specs/TX-XX.md` (e.g., `T1-07.md` or `T2-14.md`)
- Test files: `tests/test_{area}.py` (existing) or `tests/test_triage_{id}.py` (new)
- Check boxes: change `- [ ]` to `- [x]` in `docs/ralph/implementation_plan.md`
  - For dismissals: `- [x] TX-XX: ... [Dismissed - reason]`
  - For failures: `- [x] TX-XX: ... [Failed - see spec]`

## Important Reminders

- Read the spec CAREFULLY before starting. The approach has been pre-approved.
- Do NOT redesign or over-engineer. Follow the spec's action items exactly.
- Do NOT modify code outside the scope of the current task.
- Do NOT skip writing the unit test. Every fix needs a test.
- Always commit before exiting. One task = one commit.
- If you get stuck, document what went wrong in the spec file and move on.
