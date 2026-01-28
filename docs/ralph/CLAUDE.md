# CLAUDE.md — Ralph Wiggum Agent Context

This directory contains the autonomous triage agent infrastructure. You are likely reading this because you are Ralph — a Claude Code instance running in headless mode inside a Docker container.

## Your Job

You execute pre-approved tasks from `implementation_plan.md`. You do NOT design or redesign.

## Critical Files (read these first)

1. **`spec.md`** — Detailed specification for every task. This is your source of truth.
2. **`implementation_plan.md`** — Ordered checkboxed task list. Pick the first `- [ ]` item.
3. **`prompt.md`** — Your full instructions. Read this if you're unsure what to do.

## Workflow Per Task

1. Find first unchecked task in `implementation_plan.md`
2. Read corresponding section in `spec.md` (match by task ID like T1-07)
3. If RE-VERIFY: investigate, fix if real, dismiss if false positive
4. If fix: implement, write unit test, run test, commit
5. Update `implementation_plan.md` checkbox
6. Write spec file to `specs/{TASK_ID}.md`
7. Exit (one task per invocation)

## Commit Format

```
fix(T1-XX): short description
verify(T1-XX): dismiss finding as false positive
```

## Test Commands

```bash
# Run specific test
python3 -m pytest tests/ -k "test_name" -v

# Run all tests
python3 -m pytest tests/ -v
```

## Repository Conventions

- Immutable state: never mutate, always `game_state.update()`
- Relative imports within `poker/` package
- Properties must not have side effects
- Use pytest style, not unittest
- Frontend is at `react/react/src/`

## Do NOT

- Redesign the approach — it's been pre-approved
- Modify code outside current task scope
- Skip writing the unit test
- Forget to commit before exiting
- Work on more than one task per invocation
