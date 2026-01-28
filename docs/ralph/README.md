# Ralph Wiggum — Autonomous Triage Agent

"Me fail code? That's unpossible!"

## What This Is

Ralph is a bash loop that runs Claude Code in headless mode to work through the Tier 1 triage items from `docs/TRIAGE.md`. Each iteration picks the next unchecked task, implements the fix, writes a unit test, and commits.

## Files

| File | Purpose |
|------|---------|
| `spec.md` | Detailed specification for every task (pre-approved) |
| `implementation_plan.md` | Ordered checkboxed task list (Ralph checks these off) |
| `prompt.md` | Instructions given to Claude each loop iteration |
| `specs/` | Per-task spec files written by Ralph at runtime |
| `logs/` | Per-task execution logs |

## How to Run

```bash
# 1. Be on the triage/ralph-wiggum branch
git checkout triage/ralph-wiggum

# 2. Build the container
docker build -f Dockerfile.ralph -t ralph-wiggum .

# 3. Start interactively
docker run -it --rm \
  -v "$(pwd):/app" \
  -v "$HOME/.claude:/root/.claude" \
  ralph-wiggum bash

# 4. Authenticate Claude (inside container)
claude
# Log in, then exit the interactive session

# 5. Start the loop
./scripts/ralph-wiggum.sh
```

## Monitoring

From the host while Ralph runs:

```bash
# Progress count
grep -c '\[x\]' docs/ralph/implementation_plan.md
grep -c '\[ \]' docs/ralph/implementation_plan.md

# Recent commits
git log --oneline -10

# Latest spec file
ls -lt docs/ralph/specs/ | head -5

# Tail the current log
tail -50 docs/ralph/logs/$(ls -t docs/ralph/logs/ | head -1)

# Watch progress live
watch -n 30 'echo "Done:"; grep -c "\[x\]" docs/ralph/implementation_plan.md; echo "Remaining:"; grep -c "\[ \]" docs/ralph/implementation_plan.md'
```

## After Ralph Finishes

1. Review commits: `git log --oneline`
2. Check for failures: `grep "Failed\|Dismissed" docs/ralph/implementation_plan.md`
3. Read spec files for dismissed/failed items: `cat docs/ralph/specs/T1-*.md`
4. Run full test suite: `python3 -m pytest tests/ -v`
5. If satisfied, PR the branch into main
