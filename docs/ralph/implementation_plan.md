# Implementation Plan — Persistence Refactor (T3-35)

> Ordered by dependency. Ralph picks the first unchecked task each iteration.
> Check boxes when complete. Add notes in brackets if issues arise.
> Each batch = one Ralph invocation = one commit.

## Phase 1: Small Extractions (no dependencies)

- [x] T3-35-B1: Extract `SettingsRepository` + `GuestTrackingRepository` (~135 lines, 6 methods)
- [x] T3-35-B2: Extract `PersonalityRepository` — personality + avatar CRUD (~500 lines, 18 methods)

## Phase 2: User Domain

- [x] T3-35-B3: Extract `UserRepository` — user management + RBAC (~580 lines, 19 methods)

## Phase 3: Experiment Domain (split into two batches)

- [x] T3-35-B4a: Extract `ExperimentRepository` Part 1 — prompt captures, decision analysis, presets, labels (~900 lines, 30 methods)
- [x] T3-35-B4b: Extend `ExperimentRepository` Part 2 — experiment lifecycle, chat sessions, analytics, replay (~900 lines, 28 methods)

## Phase 4: Core Game State

- [x] T3-35-B5: Extract `GameRepository` — game CRUD, messages, AI state, emotional/controller state, opponent models (~800 lines, 25+ methods). Uses `serialization.py` functions.

## Phase 5: Remaining Domains

- [ ] T3-35-B6: Extract `HandHistoryRepository` + `TournamentRepository` + `LLMRepository` (~1,000 lines combined, 19 methods)
