# E2E CI Troubleshooting Plan

## Current State (2026-02-02)

### What works in CI
- **Backend tests**: 950 tests, all passing (~1m20s)
- **Frontend tests**: Vitest + lint + build, all passing (~40s)

### What's broken: E2E (Playwright) in GitHub Actions

The E2E job builds Docker containers on a GitHub-hosted runner and runs
258 Playwright tests. Currently **every test fails** with what appears to
be the frontend not rendering properly.

### Recent fixes applied
1. Created `.env` file in CI (docker-compose.yml requires `env_file: .env`)
2. Fixed `.env` format (heredoc had leading whitespace, switched to printf)
3. docker-compose.test.yml correctly sets `AI_DECISION_MODE=fallback_random`
   so no real API calls are made

### Root cause hypotheses (investigate in order)

**1. Frontend not connecting to backend**
The test compose file may not be wiring the frontend to the backend
correctly. Check:
- Does the frontend container's env have the right backend URL?
- Is the backend healthy before Playwright starts?
- Check `docker-compose.test.yml` for missing depends_on or healthcheck

**2. Frontend build issues in CI**
The frontend is built inside Docker. Check:
- Is the Vite build completing successfully?
- Are environment variables (VITE_*) being passed to the frontend build?
- Is the frontend serving on the expected port?

**3. WebKit/Safari-specific issue**
All failing tests show `[Mobile Safari]` browser. Check:
- Does the Playwright config only test Mobile Safari?
- Would Chromium tests pass? Try adding a Chromium project.
- WebKit in Docker on GitHub runners has known issues with missing deps

**4. Timing / race conditions**
- Backend may not be ready when Playwright starts
- Frontend dev server may not be ready
- Add explicit wait/healthcheck before running tests

### Key files to examine
- `docker-compose.test.yml` - test overrides, service wiring
- `react/react/playwright.config.ts` - Playwright config (browsers, baseURL)
- `react/react/e2e/` - test helpers, fixtures
- `.github/workflows/deploy.yml` - CI job definition (lines 71-89)

### Option: Disable E2E in CI

If investigation doesn't yield a quick fix, the pragmatic option is to
make E2E optional (not block deploy) or skip it in CI entirely. The E2E
tests still run locally in Docker. Change the deploy job to not require
E2E:

```yaml
# In deploy.yml, change:
needs: [test-backend, test-frontend, test-e2e]
# To:
needs: [test-backend, test-frontend]
```

Or add `continue-on-error: true` to the E2E job.

### Commands to debug locally
```bash
# Run E2E tests locally (same as CI does)
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  up --build --exit-code-from playwright playwright

# Check backend logs during test run
docker compose -f docker-compose.yml -f docker-compose.test.yml logs backend

# Check frontend logs
docker compose -f docker-compose.yml -f docker-compose.test.yml logs frontend

# Run a single test to debug
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  run playwright npx playwright test e2e/mobile/landing.spec.ts --debug
```
