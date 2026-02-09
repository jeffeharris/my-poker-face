# Repository Guidelines

## Project Structure & Module Organization
- `poker/`: core game engine, state machine, AI controllers, prompts, and repositories.
- `flask_app/`: backend API/routes, Socket.IO handlers, services, and app wiring.
- `core/llm/`: provider-agnostic LLM client layer, tracking, and provider adapters.
- `react/react/`: frontend React + TypeScript app.
- `experiments/`: tournament and replay experiment runners.
- `tests/`: Python test suite (`test_*.py`); `tests/personality_tester/` contains helper tooling.
- `docs/`: technical docs, plans, triage, and vision notes.

## Build, Test, and Development Commands
- `make up`: start backend + frontend via Docker Compose.
- `make down`: stop services.
- `make logs`: tail all container logs.
- `make test`: run backend pytest in container.
- `python3 scripts/test.py`: recommended Python test runner.
- `python3 scripts/test.py --quick`: skip slower test files.
- `python3 scripts/test.py --ts`: run TypeScript type checks.
- `python3 scripts/test.py --all`: Python + TypeScript checks.

## Coding Style & Naming Conventions
- Python: 4-space indentation, `snake_case` for functions/variables, `PascalCase` for classes.
- TypeScript/React: `camelCase` for vars/functions, `PascalCase` for components.
- Keep core game logic functional and immutable (avoid in-place mutation of state objects).
- Prefer small, focused functions and explicit error handling; use `logger` instead of `print` in backend code.
- Follow existing module patterns before introducing new abstractions.

## Testing Guidelines
- Framework: `pytest` (configured in `pytest.ini`), tests are run inside Docker.
- Naming: files `test_*.py`, classes `Test*`, functions `test_*`.
- Useful markers: `slow`, `integration`, `llm`, `flask`.
- Full-suite runs in `scripts/test.py` enforce coverage (`--cov-fail-under=40`).
- Add/adjust tests with each behavior change, especially around game-state transitions and route auth.

## Commit & Pull Request Guidelines
- Follow observed Conventional Commit style: `feat: ...`, `fix: ...`, `docs: ...`, `chore: ...`.
- Keep commits scoped and atomic; include rationale for non-obvious behavior changes.
- PRs should include:
  - concise summary of what changed and why,
  - test evidence (commands run + result),
  - linked issue/ticket (if applicable),
  - screenshots/video for frontend UI changes.

## Security & Configuration Tips
- Copy `.env.example` to `.env`; never commit secrets.
- Be explicit about DB path differences (Docker uses `/app/data/poker_games.db`).
- For new endpoints/events, enforce authentication/ownership checks by default.

## Quick PR Checklist
- [ ] Summary explains what changed and why.
- [ ] Tests added/updated for behavior changes (or reason documented if not).
- [ ] Commands run are listed (for example: `python3 scripts/test.py --quick`, `python3 scripts/test.py --ts`).
- [ ] Auth/ownership and input validation reviewed for new routes/socket events.
- [ ] Migrations/config/env changes are documented (including DB path assumptions).
- [ ] Docs updated when behavior, APIs, or workflows changed.
- [ ] Frontend changes include screenshots/video for key states.
