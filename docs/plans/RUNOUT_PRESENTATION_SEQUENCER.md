---
purpose: Design + plan for the unified client-side hand-presentation sequencer that owns run-out / showdown / inter-hand timing and ordering
type: design
created: 2026-06-06
last_updated: 2026-06-06
---

# Run-out Presentation Sequencer

## Problem

Backend→screen timing was split across four independent "directors" plus a
backend pacer, coordinated only by a shared boolean and coincidentally-equal
constants:

- **State buffer** (`usePokerGame.ts`, `BufferState` NORMAL→GATED→REPLAYING) paced
  `update_game_state` events and gated card-deal animations.
- **`winner_announcement`** bypassed that queue entirely (`setWinnerInfo` fired the
  instant the socket event arrived) → the winner overlay appeared *before* the
  queued AI actions finished replaying. **This is the #1 bug**: "we see the result
  before the AI actions have been shown — they aren't connected."
- **`useRunoutDirector`** paced per-card avatar reactions on its own client clock,
  while the run-out *board* was paced by **backend sleeps** (`_ff_aware_sleep`) — a
  split clock that could drift.
- **`useWinnerRevealGate`** only held the verdict for all-in + folded-spectator,
  not the normal "watching a hand" case.
- **`useInterhandDirector`** owned the result→shuffle→next-hand beat (this one was
  already correct).

Three separate timing-constant files; the deal-gate durations were also duplicated
inline. Fast-forward was a single binary `rushing → 0.1×`. Net effect: timings felt
off, the result outran the action, and a deliberate pause was indistinguishable
from a stall.

## Decision

One **pure presentation sequencer** owns the whole in-hand playback timeline. Every
backend signal becomes an ordered *beat* on one queue drained by one clock. The
winner is the last beat — it can never outrun the actions, *by construction*.

- **Pure engine** (`handSequencer.ts`, no React): `planEvent(state, event, tier)`
  turns each source event into a `{ timeline, durationMs, next }` plan. Deterministic,
  fully unit-testable without jsdom.
- **Thin hook** (`useHandSequencer.ts`): feeds socket events in, runs the single
  driving timer, applies effects to the Zustand store / overlays, exposes a
  `isPlaying` progress signal. (Phase 2.)
- **One clock, three tiers** (`presentationTiming.ts`): `watchable (1.0×)` /
  `fast (0.4×)` / `fastest (flush + recap)`, derived from the existing FF flags.

### What is absorbed vs. kept

| Piece | Fate |
|---|---|
| `BufferState` machine in `usePokerGame` | **Absorbed** into the engine |
| `useRunoutDirector` (reactions, hero gesture) | **Absorbed** |
| `useWinnerRevealGate` (verdict hold) | **Absorbed** — ordering makes most of it unnecessary; the fold-watch breather moves into the winner beat |
| `useInterhandDirector` (shuffle beat) | **Kept** — already correct; now fed by an engine-ordered `winnerInfo` |
| `useCommunityCardAnimation` | **Kept but rewired** — now animates off the engine's authoritative `dealCards` token (`store.cardDeal`) instead of inferring a deal from card-count deltas, so the board can't double-deal (a duplicate push / re-render / cold-load re-assert never re-fires it). The engine's `communityCount` baseline drops duplicate deals upstream; the token is the render-side half. |
| `heroCardAnimation` | **Kept** — pure CSS presentation driven by store state the engine sets |
| Backend run-out `_ff_aware_sleep` pacing | **Removed** (Phase 3) → `socketio.sleep(0)` yields; the client owns all pacing |

### Board pacing moves fully client-side

Previously the backend slept between streets (Option B). The client buffer *also*
gated the same deals — double pacing, and the source of the drift. Phase 3 removes
the backend pacing sleeps (keeping a zero-delay cooperative yield so emits still
flush). The backend becomes a pure event stream; the engine gates every deal.

## The engine model

Source events fed to the engine (from sockets):

- `state` — an `update_game_state` (AI action, blinds, hole-card deal, or a
  community-card deal, detected via `newly_dealt_count`).
- `reveal` — `reveal_hole_cards` (all-in matchup).
- `winner` — `winner_announcement`.
- `runout_schedule` — handled as **data**, not a queued beat: it sets the engine's
  schedule ref immediately so reaction *content* is resolved at fire time (robust to
  the reveal-then-schedule wire order).

`planEvent` returns:

- `timeline: { at, effect }[]` — effects at offsets (ms) from this event's start.
- `durationMs` — when the next event may begin (the beat's hold).
- `next` — the new engine state.

Effects (executed by the hook against the store / overlays): `applyState`,
`setReveal`, `hero(commit|retreat|idle)`, `reactions(phase, cardIndex)` (resolved
against the live schedule), `setActive`, `setWinner`, `recap` (fastest tier).

### Per-event plans (watchable durations; scaled by tier)

- **action** (`state`, no new community card): `applyState@0`; hold `action` (1000ms).
- **deal** (`state`, new community card): `applyState@0`; if in run-out `hero(retreat)@0`
  and per-card `reactions@ i*perCardStagger + reactionAfterCard`; hold = `flopGate`
  (3 cards, 2825ms) or `cardGate` (1 card, 825ms). On the **river** also schedule the
  SHOWDOWN reactions and `setActive(false)` after `showdownHold`; hold extends to cover it.
- **reveal**: `setReveal@0`, `setActive(true)@0`, `hero(commit)@0` (unless hero folded),
  `reactions(INITIAL)@ initialReactionDelay`; hold = `revealHold` (1500ms, the matchup beat).
- **winner**: `setWinner@lead` where `lead` = `foldWatch` only for a hero-folded
  showdown that had **no** run-out (board already landed); else 0. Fastest tier adds a
  `recap` beat. After this, the (kept) inter-hand director runs the shuffle.

### Ordering guarantee

The winner event is enqueued like any other and only processed once every prior
event's `durationMs` has elapsed. The result is structurally the last thing shown.

## Three-tier clock

`deriveTier(fastForward, alwaysFastForward, aiInstant)`:

- `always`/`aiInstant` → **fastest** (multiplier 0: flush; show a brief recap then the result)
- manual `fastForward` (incl. after-fold) → **fast** (0.4×: snappy but every beat still visible + ordered)
- otherwise → **watchable** (1.0×)

The fastest-tier recap reuses existing hand narrators/formatters — it is intentionally
minimal, not a new formatting system.

## Resilience (nice-to-have)

On reconnect/refresh the hook resets the engine and applies the REST-fetched state
directly (no stale replay). Best-effort; not a hard requirement.

## Phases

1. **Pure engine + 3-tier clock + spec** (this doc, `presentationTiming.ts`,
   `handSequencer.ts`, unit tests). No wiring.
2. **Wire into mobile** via `usePokerGame`; delete the buffer; absorb the run-out
   director + winner gate; winner becomes the terminal beat (fixes the #1 bug).
3. **Cut backend run-out sleeps** → cooperative yields; the client owns all pacing.
4. **3-tier polish + progress signal** — tier behaviors, the "still going" indicator,
   the fastest-tier recap via existing narrators.
5. *(deferred)* Desktop friendliness + reconnect resilience.

## Known follow-ups (captured during playtest)

- **Cadence done (uncommitted on `run-out-ux`):** watchable-tier salience pacing
  (`ACTION_BEAT_MS`: fold/check ~450, call/bet/raise ~1000, all-in ~1400) + a
  commentary floor (`COMMENTARY_BEAT_MS` ~3200) so the comment lingers on the actor;
  FF/fastest stay flat. Plus **comment↔action coupling**: `send_message(immediate=False)`
  in `handle_ai_action` defers the AI comment + Table action-text onto the action's
  state push (backend `sleep=1` dropped), so they surface together on the paced beat
  for *all* bot types (esp. fixes fast tiered/"sharp" bots).
- **Tournament bust skips the run-out (BUG, to fix).** On a single-table tournament
  final hand where the human goes all-in and *loses*, it jumps straight to the
  TournamentComplete screen — no run-out, no revealed cards, no winner/showdown. The
  player never sees their hand or why they lost. Cause: `single_table_hand_boundary`
  (flask_app/handlers/single_table_tournament.py) sets `GAME_OVER` + emits
  `tournament_complete` immediately, bypassing the sequencer's reveal→board→winner
  beats. Fix direction: the tournament-end transition must be the *last* beat —
  play the run-out + show the result (and a beat to absorb it) before the end screen.
- **Action badge missing on an all-in (BUG, cause TBD — needs repro).** The
  *action chip* on top of the avatar — `ActionBadge` (`components/shared/ActionBadge.tsx`):
  FOLD / ALL-IN / last_action(CALL/RAISE/CHECK, with fade) — did not show for the
  *caller* of an all-in (observed: Fred Durst calling Hulk Hogan all-in → no chip for
  Fred). NOT occlusion (badge is `top:0`, z-index 15, above the revealed cards). NOT
  the message-coupling change (badge keys on state fields `is_all_in`/`last_action`,
  which we never touched). Candidates to verify by reproducing an AI-vs-AI all-in:
  (a) caller *covers* (not all-in) → shows a transient CALL badge that clears/fades the
  instant the run-out advances the phase; (b) `is_all_in` not set on the caller in the
  displayed state; (c) a state/beat gap. Don't guess-fix — repro first.
