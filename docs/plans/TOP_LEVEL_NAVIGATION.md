---
purpose: Restructure the app from a /menu hub-and-spoke into a persistent four-tab shell (Circuit · Tournaments · Training · Career)
type: design
created: 2026-06-13
last_updated: 2026-06-13
---

# Top-Level Navigation Restructure

## Motivation

Today the app is **hub-and-spoke**: you land on `/menu` and branch out to `/cash`
(the cash lobby, themed "The Circuit"), `/tournament` (ad-hoc multi-table events),
`/menu/training` (practice), and `/story` (career). Switching modes means bouncing
back through the menu, and the cash lobby has accreted unrelated surfaces (career
highlights, the Champions Roll, reputation) into one long scroll.

Goal: a **persistent tabbed shell** so the major modes are always one tap away,
and each surface holds only what belongs to it.

## Proposed information architecture

Four persistent tabs, in this left-to-right order (per request):

```
[ Career ]   [ Circuit ]   [ Training ]   [ Tournaments ]
```

**Landing tab is an open question** — leftmost (Career) as a home/profile screen,
or Circuit as a play-first default. See open question 0 below.

| Tab | What it is | Holds |
|-----|-----------|-------|
| **Career** | Your record across the circuit — retrospective, not a place to act. | Career Highlights · Champions Roll · Reputation · circuit story (`/story`) |
| **Circuit** | The cash lobby — where you sit down and grind (already themed "The Circuit"). | Resume bar(s) · Main Event (invite + resume) · Tables (Cardroom/Casino) · stakable AIs · live activity feed |
| **Training** | Practice, non-counting (the current `/menu/training`). | Drills, sparring, difficulty presets |
| **Tournaments** | Ad-hoc multi-table events (the current `/tournament` hub). | Register (field size) · standings · resume an active MTT |

**Naming note (confirm):** "Circuit" = the cash lobby (the rooms you grind);
"Career" = your story/record. These read as distinct surfaces. If "Circuit"
should instead mean the tournament/Main-Event ladder, the Circuit↔Career split
flips — flag before Phase 1.

### What moves where (from today's cash lobby)

The cash lobby currently renders, top to bottom: CareerHero, resume bar, Main
Event resume, Main Event invite card, ActivityTicker, tables (Cardroom/Casino),
IdleStakablePanel, CareerHighlightsCard, CircuitChampionsCard, ReputationPanel.

- **Stay in Circuit:** resume bar(s), Main Event (invite + resume), tables,
  IdleStakablePanel, ActivityTicker.
- **Move to Career:** CareerHighlightsCard, CircuitChampionsCard, ReputationPanel,
  and the `/story` content.
- **CareerHero / bankroll:** open question — see below. Leaning toward a compact
  **bankroll chip in the persistent header** (always visible across tabs) plus
  the full CareerHero living in Career.

This makes the in-lobby Play/Circuit sub-swipe we previously scoped **unnecessary**:
Career becomes a top-level tab instead of a nested view, so there's no tabs-inside-tabs.

## Open questions (resolve before/within Phase 1)

0. **Which tab is the landing?** Tab order is Career · Circuit · Training ·
   Tournaments. Does the app land on **Career** (leftmost — a home/profile screen
   you arrive at, then step into play) or **Circuit** (play-first, fewest taps to
   sit down)? These can differ from left-to-right order. (Earlier, for the
   in-lobby split, "play-first" won — but at the top level a Career/home landing
   is a legitimate, different choice.)
1. **Bankroll placement** — persistent header chip (always visible) vs. only inside
   a tab. Recommendation: persistent compact chip + full CareerHero in Career.
2. **Desktop vs mobile nav** — mobile: bottom tab bar; desktop: top nav (reuse/extend
   `MenuBar`) or a left rail. Confirm desktop treatment.
3. **State preservation** — switching tabs must NOT remount/refetch the cash lobby
   (lose scroll, re-hit the rate-limited endpoints). Options: (a) keep all tab
   panels mounted and toggle visibility; (b) lift lobby state into the Zustand
   store / a cache so a remount rehydrates instantly. The cash lobby already has an
   SWR-style snapshot cache (`lobbyCache`), so (b) may be cheap. Decide in Phase 1.
4. **`/menu` fate** — redirect to `/circuit` (the new default), or repurpose as a
   "More / Settings" overflow. Existing deep links (`/cash`, `/tournament`,
   `/menu/training`) should keep working (alias/redirect).
5. **Onboarding / guests** — the Lucky Stack intake and guest flows currently gate
   the cash lobby. Confirm the new-player landing still routes through intake before
   the tab shell.

## Phasing

Each phase is independently shippable and reviewable.

- **Phase 1 — Shell, no content moves.** Add a persistent nav (mobile bottom tab
  bar + desktop nav) as a layout route wrapping the existing pages. Tabs route to
  the *current* `/cash`, `/tournament`, `/menu/training`, and a new `/career` page
  that (for now) embeds the existing `/story`. `/menu` redirects to the default.
  Nothing inside the pages changes yet. Resolve the state-preservation approach
  here.
- **Phase 2 — Content remap.** Move CareerHighlights + Champions + Reputation out
  of the cash lobby into the Career tab; slim Circuit to tables + resume + Main
  Event. Add the persistent bankroll chip.
- **Phase 3 — Polish.** Tab-switch transitions; per-tab scroll/state preservation
  verified; optional mobile swipe between adjacent tabs (done to the no-jank bar —
  real gesture, not content-swap).
- **Phase 4 — Retire the hub.** Remove the old `/menu` spoke UI, clean up dead
  entry points, finalize redirects.

## Risks

- **State/refetch on tab switch** — the highest-risk item; the cash lobby's polls
  are rate-limited, so a naive remount-per-switch would hammer them. Phase 1 must
  settle this.
- **Deep links & back behavior** — existing URLs and the browser back button must
  stay coherent across the shell.
- **Mobile vs desktop divergence** — two nav treatments to keep in parity (this
  repo has an active desktop↔mobile parity effort).
- **Guest/onboarding regressions** — the intake gate must survive the restructure.

## Out of scope

- Visual redesign of the individual pages (this is structure, not skin).
- Changing game/economy behavior.
