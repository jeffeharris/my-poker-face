---
purpose: Design for a client-owned run-out reveal director, modeled on useInterhandDirector
type: design
created: 2026-05-29
last_updated: 2026-05-29
---

# Run-Out Reveal Director (mobile)

> **Reviewed 2026-05-29** (feature-dev:code-reviewer, against the live code). The
> review surfaced two **Critical** issues now folded in below: (a) the existing
> `processStateUpdate` card-animation GATED buffer will fight the director unless the
> backend stops emitting per-street state during the run-out, and (b) gating
> `useInterhandDirector`'s `hasWinner` on the run-out `done` signal breaks its
> `endedHandRef` rising-edge capture — gate the `beginShuffle` *call* instead. §C, Rollout,
> Risks, and Open Questions have been updated accordingly.

## Problem

The all-in run-out is the only major moment whose pacing is still **owned by the
backend**. The `progress_game` loop (`flask_app/handlers/game_handler.py`) emits a
socket event, then `_ff_aware_sleep`s a *guessed* duration, emits the next, and
drives the poker FSM one street at a time (`with_phase(DEALING_CARDS)` + `continue`).
The mobile client is passive — it renders whatever arrives and plays independent CSS
keyframes. There is **no shared clock** between the server's sleeps and the client's
animation durations (the code literally comments *"Flop (3 cards): ~2.825s animation"*).

Consequences we've already hit:
- Reactions arrived **a street late** (fixed in `11f1e414` via the `is_reaction`
  channel, but the underlying split-clock design remains).
- A **dead reveal beat** — the 4s "see the cards" hold (now `RUNOUT_REVEAL_HOLD = 1.5s`)
  fires *after* the flop is already on the board, then a redundant flop `animation_sleep`
  re-waits for an animation that already played.
- Tuning is magic numbers scattered across the loop, not a single source of truth.

Meanwhile the **inter-hand** moment was already converted to a **client-owned** model
in `1185768b` (`useInterhandDirector` + `constants/interhandTiming.ts`): the backend
emits state transitions, the client owns the beat (min-hold so it can't flash,
exit-on-next-hand, safety cap), all durations live in one tuning file. The run-out
should follow the same philosophy.

## Goal

Make the run-out a **client-owned timeline** consistent with `useInterhandDirector`:
the client plays `reveal → flop → turn → river → showdown` with beats it controls,
off a schedule the backend computes **once**, instead of the backend sleeping between
streets. **Mobile only; desktop `PokerTable` untouched** (matches the interhand work).

Non-goals: changing equity math, the psychology pipeline, or the post-hand/interhand
flow itself (we hand off to it).

## What already exists (reuse, don't reinvent)

- `compute_runout_reactions(game_state, ai_controllers)` (`poker/runout_reactions.py`)
  already simulates **all** remaining streets up front (the deck is deterministic) and
  returns a `ReactionSchedule.reactions_by_phase` of `{INITIAL, FLOP, TURN, RIVER,
  SHOWDOWN: [PlayerReaction(name, emotion, equity_before, equity_after, delta)]}`.
  **The full schedule is known at reveal time** — we just don't ship it as one payload.
- `emit_hole_cards_reveal` already sends `players_cards` **and** `community_cards`.
- `useInterhandDirector` / `interhandTiming.ts` — the pattern and tuning-file convention.
- `_ff_aware_sleep` compresses to ~10% under fast-forward; the client director must
  mirror this (FF must still skim the run-out).

## Proposed design

### A. Backend — emit the schedule once, stop pacing the visuals

Replace the per-street sleep choreography in the `run_it_out` block with a single
emission, computed at the hole-card reveal:

- New socket event `runout_schedule` carrying, per street: the board after that street,
  the `PlayerReaction`s (emotion + equities), and the winner set. This is
  `compute_runout_reactions` output plus the per-street board snapshots.
- After emitting the schedule, the backend **settles the hand without visual sleeps**:
  deal the remaining streets, advance to `EVALUATING_HAND`, run the psychology pipeline,
  emit `winner_announcement` — all immediately. The backend no longer owns run-out timing.

### B. Frontend — `useRunoutDirector` + `constants/runoutTiming.ts`

- `runoutTiming.ts` — single tuning point (mirrors `interhandTiming.ts`, "Snappy"):
  `revealStaggerMs`, `revealCardMs`, `perStreetHoldMs`, `showdownHoldMs`, plus a
  `safetyCapMs`. All ms; FF multiplier applied centrally.
- `useRunoutDirector({ schedule, fastForward })` — owns the timeline: walks
  `INITIAL → FLOP → TURN → RIVER → SHOWDOWN`, exposing the current board to show and the
  current per-player emotion, holding each beat for its tuned duration (FF-compressed),
  with a safety cap so a malformed schedule can't hang. Emits a `done` signal when the
  showdown beat elapses.
- Reveal animation: staggered slide-in on `.opponent-revealed-cards` (per-card and
  per-opponent `animation-delay`, slightly slower slide). **No desktop flip** (`cardReveal`
  is desktop-only and broken). The reveal *is* the beat — no separate frozen hold.

### C. The coordination seam (the crux)

Two tensions, **resolved per review**:

1. **Board-card source + the GATED buffer (Critical).** `MobilePokerTable` reads the board
   from the Zustand store's `communityCards`, not from `revealedCards`. And
   `processStateUpdate` (`usePokerGame.ts` ~336-413) already has a **card-animation gate**:
   when an `update_game_state` carries `newly_dealt_count > 0` it opens a ~2825ms gate and
   queues later updates, draining them on its own `REPLAY_DELAY_MS` clock. If the backend
   settles immediately and emits per-street states, that buffer's replay clock and the
   director's beat clock advance the board **independently** → the board flickers or shows
   future cards.
   **Decision:** during the run-out the backend emits the **schedule only** and **suppresses
   per-street `update_game_state`**; it pushes **one** authoritative final state after
   settling. The client **freezes `communityCards`** while `isRunningOut` (board sourced from
   the director/schedule) and **reconciles to authoritative state on `done`**. The GATED
   buffer must be bypassed while `isRunningOut`. (Withholding cards from authoritative state
   on the backend is the wrong fix — it re-couples the backend to client timing.)
2. **Don't let the result beat start early — but don't gate `hasWinner` (Critical).**
   `useInterhandDirector` captures `endedHandRef` on the **rising edge of `hasWinner`**
   (`useInterhandDirector.ts:52-57`). Under Phase 2 the backend deals the next hand right
   after settling, so `handNumber` may already be N+1. If `hasWinner` is gated on the run-out
   `done`, its rising edge fires *after* `handNumber` advanced → `endedHandRef` stamps the
   new hand → the shuffle-exit condition is instantly true → the shuffle **flashes** (the
   exact bug the director was built to prevent).
   **Decision:** let `hasWinner` rise at `winner_announcement` as today (so `endedHandRef`
   captures the just-ended hand), and instead gate the **`beginShuffle` call** (the
   auto-dismiss / Continue path) until the run-out director reports `done`. Thin coordination
   seam; keep the two directors as **siblings**, not unified (unifying forces one hook to
   juggle two safety caps and two exit observables — harder to test).

## Alternative considered — hybrid (client holds, backend still deals per street)

Keep the backend dealing street-by-street but move only the *holds* to the client. Rejected
as the primary design: the backend would either race ahead (deal all streets before the
client plays them — same board-source problem) or require per-street client→server
"ready" signaling (chatty, and re-introduces a shared clock). The interhand model avoids
this by having the client wait on an observable (next-hand `handNumber`); the run-out analog
is the pre-computed schedule. **However**, a *reduced* hybrid is the natural Phase 1 (below).

## Rollout (incremental, each shippable)

Phase 1 was **rescoped after review** — the original "client-held reveal beat" needed new
client auto-dismiss logic that Phase 2 would throw away, and left the per-street progression
still backend-paced (so the "visible win" was smaller than advertised).

- **Phase 1 — staggered reveal animation, CSS-only (low risk, fully reusable).** Keep the
  backend loop and the 1.5s `RUNOUT_REVEAL_HOLD` for now. Add per-card/per-opponent
  `animation-delay` + a slightly slower slide on `.opponent-revealed-cards` so the reveal
  reads as deliberate motion. **Pure CSS** — no new state machine, no socket protocol, and
  the animation is exactly what `useRunoutDirector` drives in Phase 2, so nothing is thrown
  away. (Does **not** zero the backend hold or touch per-street sleeps — that's Phase 2's
  job, to avoid a half-migrated split clock.)
- **Phase 2 — full schedule + retire backend pacing.** Add `runout_schedule`, build
  `useRunoutDirector`, **suppress per-street `update_game_state` and freeze `communityCards`
  during `isRunningOut`** (§C.1), gate `beginShuffle` on the director's `done` (§C.2), and
  delete the per-street `_ff_aware_sleep`s. This is the architecturally consistent end state.

## Risks / edge cases (review-updated)

- **Reconnection mid-run-out (Important).** Under Phase 2, a client that reconnects after the
  backend settled calls `refreshGameState` → `applyGameState` and sees a full board, `phase`
  past the hand, **no `winner_announcement`** (it was emitted during the run-out and missed),
  and `revealedCards: null` — the hand silently ended showing all cards with no beat. The fix
  (re-send schedule on resync) requires **persisting the schedule**: today
  `runout_reaction_schedule` is in-memory in `current_game_data` and dropped at hand end
  (`game_handler.py` `game_data.pop('runout_reaction_schedule', ...)`). Decide: persist the
  schedule for resync, or accept the silent skip (and at least suppress the all-cards reveal).
- **Fast-forward stale closure (Important).** Backend emits no per-street state during Phase 2
  playback, so the store's `fastForward` can be stale if toggled mid-run-out. The director must
  read `fastForward` from a **ref at the moment each `setTimeout` fires**, not a captured
  closure, and compress beats ~10% to match `_ff_aware_sleep`.
- **Game deleted / safety cap (Minor).** `_ff_aware_sleep` bails when `current_game_data` is
  gone; the client director has no such check. The `safetyCapMs` timer must fire `done`
  **unconditionally** (copy `useInterhandDirector.ts:69`'s `maxTimer`, which fires regardless),
  or a deleted game leaves `beginShuffle` gated forever waiting on a `done` that never comes.
- **Lock held during psychology (Important, not new).** Phase 2 removes the sleeps that gave
  "breathing room" before `handle_evaluating_hand_phase`; the **synchronous** psychology
  pipeline now runs immediately after the schedule emit, holding the `progress_game` lock
  (seconds on slow LLM providers). Pre-existing behavior, but confirm it's acceptable with the
  sleeps gone.
- **Information leak (Moderate).** The Phase 2 schedule payload would contain **future-street
  cards** before they animate — readable via devtools/patched socket. All-in cards are
  determined, but "in the payload now" ≠ "shown seconds later." Decision: send **reactions +
  timing only**, and advance the board via a per-step event carrying only the **current**
  street's cards (more work, no leak), or accept the leak as immaterial for all-in (no further
  action possible). Matters more for spectator/replay surfaces.
- **`revealedCards` clearing (Minor).** Today `revealedCards` persists until `clearWinnerInfo`
  in `handleResultComplete`. Phase 2 must define when opponent hole cards transition to
  director-managed display vs. clear, so the winner overlay and the reveal don't both show.
- **Heads-up / multiway splits.** Schedule must carry per-player emotion for all active players;
  split-pot showdown emotion (equity ~0.5) already handled by `_equity_to_showdown_emotion`.
- **Desktop.** Run-out reveal also renders on desktop; scope must confirm desktop is unaffected
  (left on the old backend pacing) — matches the interhand work's "desktop untouched."

## Testing

- Unit (`useRunoutDirector`): timeline progression, FF compression, safety cap, empty/short
  schedules, single-street (all-in on the turn/river).
- Mobile component: staggered reveal renders; board sourced from director during playback;
  result beat gated until `done`.
- Manual: all-in preflop / flop / turn; split pot; reconnect mid-run-out; FF.

## Open questions — resolved by review

1. **Board-source:** Render from the director during playback; **freeze `communityCards`**
   while `isRunningOut` and **suppress per-street `update_game_state`** on the backend
   (one final authoritative push, reconciled on `done`). Do **not** have the backend withhold
   cards from state — that re-couples backend to client timing. (§C.1)
2. **Sibling vs unified:** Keep `useRunoutDirector` and `useInterhandDirector` as **siblings**;
   coordinate via gating the `beginShuffle` call on `done` (not by gating `hasWinner`). (§C.2)
3. **Info leak:** Real, not just philosophical — a future-cards payload is a cheating/spoiler
   surface. Default to **reactions + timing only**, advancing the board via a current-street
   event; revisit if spectator/replay surfaces need the full schedule. (Risks)

## Still open (need a call before Phase 2 build)

- **Reconnection:** persist the schedule for resync, or accept the silent skip? (lean: at
  minimum suppress the all-cards reveal on a missed run-out)
- **Phase 1 scope confirmation:** ship the CSS-only staggered reveal alone, or bundle it with
  the Phase 2 director in one go?
