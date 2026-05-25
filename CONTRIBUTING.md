---
purpose: How to contribute to My Poker Face — setup, workflow, code style, and PR expectations
type: guide
created: 2026-05-25
last_updated: 2026-05-25
---

# Contributing to My Poker Face

Thanks for your interest in contributing! This guide covers everything you need to make your first PR.

For deeper architectural context, see [`AGENTS.md`](AGENTS.md) and [`CLAUDE.md`](CLAUDE.md) — they document
the layout, patterns, and conventions in detail.

## Quick links

- [Quick Start (developers)](docs/QUICK_START.md) — setup options and first game
- [Architecture overview](AGENTS.md#project-structure--module-organization)
- [Code of Conduct](CODE_OF_CONDUCT.md)

## Setup

```bash
git clone https://github.com/jeffeharris/my-poker-face.git
cd my-poker-face
cp .env.example .env       # Then fill in API keys
make up                    # Docker compose: backend + frontend + redis
```

Open <http://localhost:5173>.

### Local dev environment (without Docker)

```bash
python -m venv my_poker_face_venv
source my_poker_face_venv/bin/activate
pip install -r requirements-dev.txt
pre-commit install         # see "Pre-commit hooks" below
cd react/react && npm ci
```

## Workflow

1. Fork the repo (or branch from `main` if you have push access).
2. Create a feature branch: `git checkout -b feat/your-thing`.
3. Make your changes; keep commits small and atomic.
4. Run the checks (see below).
5. Open a PR against `main` with a summary and test plan.

## Code style

We enforce style with automated tools so reviewers can focus on the substance, not the formatting.

### Python — ruff

Config lives in [`pyproject.toml`](pyproject.toml).

```bash
ruff check .                    # Lint
ruff check . --fix              # Auto-fix what's auto-fixable
ruff format .                   # Format
ruff format --check .           # Format check (what CI runs)
```

Conventions (also encoded as ruff rules):

- 4-space indentation, line length 100.
- `snake_case` for functions and variables; `PascalCase` for classes.
- Use `logging` — never `print()` — in `poker/`, `flask_app/`, and `core/`.
- Keep game logic functional and immutable (avoid in-place mutation of state objects).
- See [`AGENTS.md`](AGENTS.md#coding-style--naming-conventions) for the rest.

### TypeScript / React — ESLint + Prettier

```bash
cd react/react
npm run lint                    # ESLint
npm run typecheck               # tsc --noEmit
npm run format                  # Prettier (writes files)
npm run format:check            # Prettier (CI-style check)
```

Conventions:

- 2-space indentation, single quotes, line length 100.
- `camelCase` for vars/functions; `PascalCase` for components.
- See [`react/CLAUDE.md`](react/CLAUDE.md) for component, state-management, and memoization patterns.

## Pre-commit hooks

We use [`pre-commit`](https://pre-commit.com) to run the same checks locally that CI runs. After
cloning, install the hooks once:

```bash
pip install pre-commit
pre-commit install
```

Now every `git commit` will format and lint the files you touched. To run all hooks against the whole
tree (useful before a big PR):

```bash
pre-commit run --all-files
```

## Testing

```bash
python3 scripts/test.py              # Run all Python tests (in Docker)
python3 scripts/test.py --quick      # Skip slow/integration tests
python3 scripts/test.py --all        # Python + TypeScript checks
python3 scripts/test.py test_card    # Run tests matching pattern

cd react/react && npm test           # Vitest unit tests
```

See [`AGENTS.md`](AGENTS.md#testing-guidelines) for naming, markers, and coverage expectations.

## Pull requests

A good PR has:

- a concise summary of **what** changed and **why**,
- the commands you ran and their results,
- linked issue/ticket if applicable,
- screenshots or short video for frontend UI changes.

We follow Conventional Commits: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`.

## Security

- Never commit secrets. `.env` is gitignored — use `.env.example` as the template.
- For new endpoints or socket events, enforce authentication/ownership checks by default.
- Report security issues privately (see [`README.md`](README.md) for contact).

## Questions?

- Open a [GitHub Issue](https://github.com/jeffeharris/my-poker-face/issues) — even a half-formed question is fine.
- Check `docs/` for design docs, vision, and feature plans.
