# Test Infrastructure PRs — Review & Merge Plan

## Merge Order

These PRs must be merged sequentially. Each builds on the previous branch's changes.

| Order | PR | Branch | Depends On |
|---|---|---|---|
| 1 | #111 — Extract auth helper & eliminate global state | `fix/test-infra-pr1-auth-helper-global-state` | `main` |
| 2 | #112 — Extract Socket.IO mock utility | `fix/test-infra-pr2-socket-mock-utility` | #111 |
| 3 | #114 — Add data-testid & migrate selectors | `fix/test-infra-pr3-data-testid` | #112 |
| 4 | #115 — Error scenario tests | `fix/test-infra-pr4-error-scenario-tests` | #114 |

## Pre-Merge: Rebase onto main

There is a known lint fix landing on `main` soon. Once that merges, rebase all 4 branches in order:

```bash
# 1. Update main
git checkout main && git pull

# 2. Rebase PR 1 onto main
git checkout fix/test-infra-pr1-auth-helper-global-state
git rebase main
git push --force-with-lease

# 3. Rebase PR 2 onto PR 1
git checkout fix/test-infra-pr2-socket-mock-utility
git rebase fix/test-infra-pr1-auth-helper-global-state
git push --force-with-lease

# 4. Rebase PR 3 onto PR 2
git checkout fix/test-infra-pr3-data-testid
git rebase fix/test-infra-pr2-socket-mock-utility
git push --force-with-lease

# 5. Rebase PR 4 onto PR 3
git checkout fix/test-infra-pr4-error-scenario-tests
git rebase fix/test-infra-pr3-data-testid
git push --force-with-lease
```

## Review Checklist Per PR

### PR 1 (#111) — Auth helper & global state
- [ ] `setAuthLocalStorage()` correctly replaces all inline localStorage blocks
- [ ] `MockContext` return type works in both mock and real modes
- [ ] No remaining `_pendingSocketEvents` / `_pendingGameId` globals
- [ ] Run: `cd react/react && npx tsc --noEmit`

### PR 2 (#112) — Socket.IO mock
- [ ] `socket-mock.ts` protocol comments are accurate
- [ ] Both `mockGamePageRoutes` and `mockMenuPageRoutes` use `mockSocketIO()`
- [ ] No remaining inline `transport=polling` logic in helpers.ts
- [ ] Run: `cd react/react && npx tsc --noEmit`

### PR 3 (#114) — data-testid migration
- [ ] `data-testid` attributes added alongside (not replacing) CSS classes
- [ ] Spec files use `getByTestId()` instead of `.locator('.class')`
- [ ] `navigateToGamePage` waits on `getByTestId('mobile-poker-table')`
- [ ] Run: `cd react/react && npx tsc --noEmit`
- [ ] Run: `cd react/react && npx vitest run` (unit tests still pass)

### PR 4 (#115) — Error scenario tests
- [ ] Tests verify graceful degradation, not specific error UI
- [ ] `route.abort()` and delayed `route.fulfill()` patterns are correct
- [ ] Run: `cd react/react && BASE_URL=http://localhost:5174 npx playwright test e2e/mobile/error --project="Mobile Chrome"`
- [ ] Some tests may fail if app lacks error handling — file follow-up tickets, don't block merge

## Merge Procedure

After each PR passes review and CI:

```bash
# Merge PR 1
gh pr merge 111 --squash

# Rebase remaining PRs onto updated main, then merge PR 2
git checkout main && git pull
git checkout fix/test-infra-pr2-socket-mock-utility && git rebase main && git push --force-with-lease
gh pr merge 112 --squash

# Repeat for PR 3 and PR 4
```

Alternatively, if all 4 pass review simultaneously, merge them rapidly in sequence (squash each).

## Files Changed Summary

| PR | Files | Key changes |
|---|---|---|
| #111 | 11 | `helpers.ts`, 10 spec files |
| #112 | 2 | `helpers.ts`, new `socket-mock.ts` |
| #114 | 19 | 7 component `.tsx` files, `helpers.ts`, 12 spec files |
| #115 | 2 | New `error-api.spec.ts`, `error-network.spec.ts` |
