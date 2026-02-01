# Mobile Test Implementation Plan

> Ordered task list for Ralph to work through. Each task = one Claude invocation.

## Phase 0: Setup

- [x] PW-00: Install Playwright and create mobile test config

## Phase 1: Playwright E2E — Navigation & Auth (mobile viewport)

- [x] PW-01: Landing page renders correctly on mobile
- [x] PW-02: Guest login flow on mobile
- [x] PW-03: Game menu renders on mobile with quick play options and guest locks

## Phase 2: Playwright E2E — Game Creation & Table Load

- [ ] PW-04: Quick Play Lightning creates game and mobile table loads
- [ ] PW-05: Quick Play 1v1 creates heads-up game with opponent panel

## Phase 3: Playwright E2E — Gameplay Actions

- [ ] PW-06: Mobile action buttons display correct options per game state
- [ ] PW-07: Mobile raise sheet — open, slider, quick bets, confirm
- [ ] PW-08: Preemptive fold while waiting for opponent

## Phase 4: Playwright E2E — Chat & Communication

- [ ] PW-09: Mobile chat sheet — open, tab switch, send message, dismiss
- [ ] PW-10: Floating chat bubbles appear and auto-dismiss

## Phase 5: Playwright E2E — Hand Results

- [ ] PW-11: Winner announcement shows after hand and auto-dismisses
- [ ] PW-12: Post-round chat — tone selection and suggestion sending
- [ ] PW-13: Tournament complete screen displays final standings

## Phase 6: Playwright E2E — Edge Cases & Modals

- [ ] PW-14: Guest limit modal appears and offers upgrade
- [ ] PW-15: Offline detection shows banner on mobile
- [ ] PW-16: Reconnecting overlay appears when socket drops
- [ ] PW-17: Mobile navigation — back button returns to menu

## Phase 7: Playwright E2E — Custom Game Wizard (mobile)

- [ ] PW-18: Custom game wizard step 0 — choose opponents on mobile
- [ ] PW-19: Custom game wizard step 1 — game settings on mobile
- [ ] PW-20: Custom game wizard step 2 — review and create on mobile

## Phase 8: Vitest Component Tests — Mobile Components

- [ ] VT-01: MobileActionButtons renders correct buttons for each option set
- [ ] VT-02: MobileActionButtons raise sheet — calculations and interactions
- [ ] VT-03: MobileChatSheet — tabs, messages, guest restrictions
- [ ] VT-04: MobileWinnerAnnouncement — showdown vs fold display
- [ ] VT-05: FloatingChat — message stacking, timing, dismiss
- [ ] VT-06: HeadsUpOpponentPanel — play style, tilt, record display
- [ ] VT-07: LLMDebugModal — stats rendering and CRT aesthetic
- [ ] VT-08: GuestLimitModal — content, CTA, benefits grid
- [ ] VT-09: useViewport hook — returns correct breakpoints
- [ ] VT-10: ResponsiveGameLayout — routes to MobilePokerTable on mobile
