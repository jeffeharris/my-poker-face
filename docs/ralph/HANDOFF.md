# Plan: Monitor Ralph Wiggum & Finalize PR

## Context

Ralph Wiggum is an autonomous triage agent running Claude Code in a headless bash loop inside Docker. It works through pre-approved bug fixes and code quality tasks from `docs/TRIAGE.md`. Everything is on branch `triage/ralph-wiggum`.

**Key docs:**
- `docs/ralph/README.md` — overview, launch instructions, monitoring commands
- `docs/ralph/implementation_plan.md` — checkboxed task list Ralph updates as it works
- `docs/ralph/spec.md` — detailed spec for every task
- `docs/ralph/CLAUDE.md` — agent instructions
- `docs/TRIAGE.md` — full triage list (88 items total)

## Current State

### Completed
- **Tier 1 (20/20)**: All done. 13 fixes, 7 dismissed as false positives.
- **Tier 2 (10/10)**: All done. 9 fixes, 1 dismissed.
- **Tier 3 Phase 7**: In progress.

### Phase 7 Status (Tier 3 Mechanical Fixes)
- [x] T3-20: Remove GET from `/api/end_game/<game_id>` — POST only
- [x] T3-24: Add `.editorconfig`
- [x] T3-25: Add `.github/dependabot.yml`
- [ ] T3-27: Update Makefile to `docker compose` v2
- [ ] T3-04: Fix skipped test in `test_prompt_management.py:192`
- [ ] T3-13: Make HTTP timeout configurable via env var
- [ ] T3-16: Consolidate duplicated `_get_db_path()` functions
- [ ] T3-28: Set `--max-warnings=0` in CI workflow

### PR Status
- PR #83 is open: https://github.com/jeffeharris/my-poker-face/pull/83
- Branch is ahead of remote — push when Ralph finishes to update PR

### Known Issues
- **T1-16 test weakness**: `tests/test_generate_game_id.py` tests a local copy of `generate_game_id()` instead of importing the real one. Consider fixing.
- **Modified files in working tree**: Some linter/formatting changes showing in `git status` — review before final commit.

## Monitoring Ralph

### Check progress
```bash
echo "Completed:" && grep -c '\[x\]' docs/ralph/implementation_plan.md
echo "Remaining:" && grep -c '\[ \]' docs/ralph/implementation_plan.md
```

### See what's done vs pending
```bash
cat docs/ralph/implementation_plan.md
```

### Check if Ralph is running
```bash
# Find the container
docker ps | grep ralph

# Check process inside container
docker exec <container_name> ps aux | grep claude

# Tail the log
docker exec <container_name> tail -30 /tmp/ralph-restart.log
```

### View Ralph's latest work
```bash
git log --oneline -15
ls -lt docs/ralph/specs/ | head -5
ls -lt docs/ralph/logs/ | head -5
```

### If Ralph crashed
```bash
# Restart with the resilient script (has retry logic)
docker exec -d <container_name> bash /app/scripts/ralph-wiggum.sh

# Or start a new container
docker run -d --name ralph-new \
  -v "$(pwd):/app" \
  -v "$HOME/.claude:/home/node/.claude" \
  ralph-wiggum bash /app/scripts/ralph-wiggum.sh
```

## When Ralph Finishes Phase 7

### 1. Verify all tasks complete
```bash
grep '\[ \]' docs/ralph/implementation_plan.md
# Should return nothing
```

### 2. Run full test suite
```bash
python3 scripts/test.py          # Python tests
python3 scripts/test.py --ts     # TypeScript type check
```

### 3. Check for failures or dismissals
```bash
grep -E "Failed|Dismissed" docs/ralph/implementation_plan.md
```

### 4. Review changes
```bash
git diff main...triage/ralph-wiggum --stat
git log main..triage/ralph-wiggum --oneline
```

### 5. Handle uncommitted changes
```bash
git status
# If there are formatting/linter changes, review and commit or discard
```

### 6. Push and update PR
```bash
git push
# PR #83 will auto-update
```

### 7. Final PR summary
Update PR #83 body with final counts:
- Tier 1: 20 tasks (13 fixes, 7 dismissed)
- Tier 2: 10 tasks (9 fixes, 1 dismissed)
- Tier 3: 8 tasks (all fixes)
- Total: 38 tasks

## What's Left in TRIAGE.md

### Remaining Tier 2 (24 items) — Need design decisions
- Architecture: T2-01, T2-02, T2-03, T2-04, T2-06, T2-07
- Code quality: T2-10, T2-11, T2-13, T2-16
- Reliability: T2-21
- Frontend: T2-23, T2-24, T2-25, T2-26, T2-27, T2-28
- DevOps: T2-29, T2-30, T2-31, T2-32, T2-33, T2-34

### Remaining Tier 3 (26 items after Phase 7)
Most need human judgment or are architectural. Not suitable for Ralph.

**False positives identified:**
- T3-03: Tests have real assertions (not placeholders)
- T3-07: DB uses context managers, no connection leak
- T3-26: `__pycache__` not committed, gitignore works

## Quick Commands Reference

```bash
# Monitor
cat docs/ralph/implementation_plan.md
git log --oneline -10
docker exec <container> tail -f /tmp/ralph-restart.log

# Test
python3 scripts/test.py --all

# Push
git push

# PR
gh pr view 83 --repo jeffeharris/my-poker-face
```
