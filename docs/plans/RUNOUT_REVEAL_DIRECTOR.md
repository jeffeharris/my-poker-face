---
purpose: Design for a client-owned run-out reveal director, modeled on useInterhandDirector
type: design
created: 2026-05-29
last_updated: 2026-05-29
---

# Run-Out Reveal Director (mobile)

> **Status (2026-05-29) — Phase 1 shipped; Phase 2 per-card director shipped (option B); §E hero-commit shipped (minimal); §E equity-picked variants deferred.**
> Work lives on branch `career-mode-v0_1`, **local and unpushed**, with `development` merged in.
> Relevant commits (newest first):
> - `abb53197` — **Phase 2: `useRunoutDirector`** — per-card avatar reactions on a client-owned
>   beat + `runout_schedule` emit + socket-layer suppression of backend street reactions while
>   directing. 8 hook tests.
> - `83351e12` — per-card reaction **schedule** (`runout_reactions.py` `steps` + `runout_schedule_payload`)
>   + 5 unit tests. The foundational backend input.
> - `39a1f060` — sequential reveal cascade (per-card + per-opponent) + pre-flop reactions as
>   their own beat + the dead `animation_sleep` removed on the reveal step + this doc's §D/§E.
> - `b06371d4` — Phase 1 staggered reveal + this design note.
> - `f910026b` — merge of `development` (brings `useInterhandDirector` + `interhandTiming.ts`).
> - `11f1e414` — run-out reaction-delivery fix (`is_reaction`) + reveal-hold tune.
>
> **What shipped in Phase 2 — and why it diverged from §A/§C below.** The build picked
> **option B** (see the new "Backend can't branch by client" note under §C). The crux: mobile vs
> desktop is a **client-side** viewport choice (`ResponsiveGameLayout`), the `progress_game`
> run-out loop is **client-agnostic**, and desktop's `PokerTable` has **no** director — so
> "retire backend pacing for mobile, keep it for desktop" (the original §A/§C) is not
> expressible from one loop. Option B keeps **one** backend path: the per-street states + sleeps
> + reactions stay (desktop unchanged), and the backend *additionally* emits one
> `runout_schedule` (reactions + per-card timing, no cards). Mobile's `useRunoutDirector` plays
> the **per-card reactions (§D)** on a client beat aligned to the card cascade and suppresses the
> backend's street-level reactions while it runs. **Because the board stays backend-paced, the
> heavy §C machinery — board freeze, GATED-buffer bypass, `beginShuffle` gating, reconnect
> persistence — was NOT needed** (those existed to support retiring pacing). The reconnect and
> info-leak open questions are thereby **moot** (cards still arrive per-street from the backend;
> nothing future-street ever ships early).
>
> **Honest limitation:** the director is bounded-below by backend pace *between* streets; the
> real win is *within* the street (the per-card flop cascade + reactions on their own beats).
> Full clock independence would need a client-type flag (rejected — duplicate paths) or retiring
> pacing for all (breaks desktop). **§E shipped (minimal):** the human hole-card "commit" — at
> the matchup reveal the hero's cards throw up to *present* over the board and hold (opponents
> read the matchup on the same beat), then pull back to their original dealt placement the
> instant the run-out's first board card deals (so the board is clear as it runs). Client-only,
> rides the existing schedule; `useRunoutDirector` exposes `heroCommitted`/`heroRetreating`,
> `MobilePokerTable` drives per-card `heroPresentUp*`/`heroPullDown*` keyframes. **Still deferred:**
> §E's *equity-picked gesture variants* (confident push vs. loose toss, keyed on the human's
> equity — the schedule already computes it; surfacing it is a one-field add) and whether the
> commit nudges the board/pot. Companion doc: `docs/technical/EMOTION_AND_PRESSURE_ARCHITECTURE.md`.

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

- New socket event `runout_schedule` carrying, **per card-reveal step** (see §D), the board
  after that step, the `PlayerReaction`s (emotion + equities), and the winner set. This is a
  card-level extension of `compute_runout_reactions` output plus the board snapshots.
- After emitting the schedule, the backend **settles the hand without visual sleeps**:
  deal the remaining streets, advance to `EVALUATING_HAND`, run the psychology pipeline,
  emit `winner_announcement` — all immediately. The backend no longer owns run-out timing.

### B. Frontend — `useRunoutDirector` + `constants/runoutTiming.ts`

- `runoutTiming.ts` — single tuning point (mirrors `interhandTiming.ts`, "Snappy"):
  `revealStaggerMs`, `revealCardMs`, `perCardHoldMs`, `showdownHoldMs`, plus a `safetyCapMs`.
  All ms; FF multiplier applied centrally.
- `useRunoutDirector({ schedule, fastForward })` — owns the timeline: walks the schedule
  **step by step** (hole-card reveal → each board card → showdown), exposing the current
  board to show and the current per-player emotion, holding each beat for its tuned duration
  (FF-compressed), with a safety cap so a malformed schedule can't hang. Emits `done` when
  the showdown beat elapses.
- Reveal animation: staggered slide-in on `.opponent-revealed-cards` (per-card and
  per-opponent `animation-delay`, slightly slower slide) — **shipped in Phase 1**. **No
  desktop flip** (`cardReveal` is desktop-only and broken). The reveal *is* the beat — no
  separate frozen hold.

### C. The coordination seam (the crux)

> **⚠️ Build-time finding (2026-05-29): the backend can't branch by client.**
> The two tensions below were framed assuming the backend could "retire pacing for mobile,
> keep it for desktop." It can't: mobile vs desktop is a **client-side** viewport decision
> (`ResponsiveGameLayout` → `useViewport().isMobile`), the `progress_game` run-out loop is
> **client-agnostic**, and both clients share `usePokerGame`'s GATED buffer. Desktop's
> `PokerTable` has **no** director. So the shipped build chose **option B**: keep one backend
> path (states + sleeps + reactions, desktop unchanged) and *add* a `runout_schedule`; mobile
> overlays a **reaction-timing-only** director. Under option B the board stays backend-paced, so
> **neither tension below actually arises** — they only mattered if the backend settled
> immediately. Kept verbatim for the record / a future "full client-owned timeline" phase.

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

### D. Per-card reactions (the Phase 2 payoff over Phase 1's street granularity)

Phase 1 (and today's backend) reacts at **street** boundaries — the flop's three cards land
together and get one reaction. The director's whole point is to go **per card**:

- **Each flop card individually.** `compute_runout_reactions` must compute equity after each
  card added to the board (partial-board equity for flop card 1, then 2, then 3 — eval7
  handles 1- and 2-card boards fine, just higher variance), not only after the full flop. The
  schedule becomes a list of steps `[holeReveal, flop₁, flop₂, flop₃, turn, river, showdown]`,
  each with `board_after`, per-player `equity_after`, and the reaction from that card's delta.
  Now a player holding `AK` can light up on the `K` and stay flat on the `7` and `2`.
- **Hole-card reveal.** The matchup read (today's `INITIAL`) is one reaction after both hole
  cards are shown — a per-*card* hole reaction isn't poker-meaningful (you need both cards to
  evaluate), so the hole step stays a single beat, timed to land as the cascade settles
  (the behavior Phase 1's pre-flop change already set up). The human's own hole-card display
  is part of this step — see §E.
- Cost: ~6–7 equity calcs per hand instead of ~4. Pre-computed once on the deterministic
  deck, so negligible.
- The director plays each step: reveal the card (animation) → show per-player reaction →
  `perCardHoldMs` → next card. This is the "react as each card peels off" feel; it's only
  possible once the client owns the timeline (the backend can't sanely sleep between
  individual flop cards without re-introducing the split-clock problem).

### E. Human hole-card "commit" (immersion — idea, to refine in Phase 2)

Today the human's hole cards sit static as large cards at the bottom of the screen
(`.hero-cards` in `MobilePokerTable.tsx`, driven by `useCardAnimation` — which already has
deal/exit animation infra with CSS-var-driven per-card transforms). The idea: during an
all-in run-out, **animate the human's cards into the table** — "tossed" or "pushed in" —
rather than leaving them parked.

- **A few animation variants, chosen by comparative hand strength.** The human's equity is
  already in the schedule (the reveal/`INITIAL` step). Map it to the gesture: a confident
  forward *push* when well ahead, a looser *toss* when behind / on a draw, something neutral
  in between. Small library of motions, equity-selected.
- **The commit IS the matchup reveal — and the AI's cue to react.** Pushing/tossing the cards
  in is the beat that puts the human's hand on the table, which is exactly when the AI
  opponents react to the matchup (the hole-reveal/`INITIAL` reaction in §D). So the human's
  card-commit animation and the opponents' reaction are the *same* timeline step: human
  commits → AIs react. The director sequences them together.
- **Why it needs the director.** The variant depends on schedule equity, and the commit must
  be timed against the opponent reveal + reaction beat — both are timeline concerns the
  director owns. Reuses the existing `useCardAnimation` transform/CSS-var pattern; adds a
  run-out "commit" state with the equity-picked variant.
- **To refine:** the exact set of variants and the equity→variant thresholds; whether the
  commit also nudges the board/pot visually; reduced-motion fallback (static, as today).

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
- **Human hole-card "commit" — now specified in §E**, but two things to pin before build:
  the set of animation variants and the equity→variant thresholds, and whether the commit
  also nudges the board/pot visually.

## Shipped (Phase 1, on `career-mode-v0_1`)

- `b06371d4` — staggered, sequential hole-card reveal (per-card + per-opponent cascade,
  CSS + a `--reveal-index`), and the design note.
- `11f1e414` — reaction-delivery fix (`is_reaction`) + reveal-hold tune.
- `39a1f060` — backend pre-flop pacing: skip the dead `animation_sleep` on the reveal step;
  surface pre-flop (`INITIAL`) reactions as their own beat after the cards settle. Still
  **street-granular** — per-card reactions (§D) are the Phase 2 upgrade.

## Shipped (Phase 2 — per-card reaction director, option B, on `career-mode-v0_1`)

- `83351e12` — **per-card schedule.** `compute_runout_reactions` now emits an ordered per-card
  `steps` list (INITIAL → flop₁ → flop₂ → flop₃ → TURN → RIVER → SHOWDOWN) alongside the
  unchanged street-granular `reactions_by_phase`; `runout_schedule_payload()` serializes
  reactions + timing only (no board cards). `tests/test_runout_reactions.py` (5 tests).
- `abb53197` — **`useRunoutDirector`.** Backend emits `runout_schedule` once at the all-in
  reveal. Mobile walks it, applying each card's reaction on a client beat aligned to the
  community-card cascade (`constants/runoutTiming.ts`); a reaction face is just
  `/api/avatar/{name}/{emotion}`, so the director rewrites the URL's emotion segment. While
  directing, a store flag (`runoutDirectorActive`) makes the socket layer drop the backend's
  street-level `is_reaction` updates so they can't clobber the per-card faces. Desktop never
  sets the flag → unchanged. `__tests__/mobile/useRunoutDirector.test.tsx` (8 tests).

## Shipped (§E hero card-commit, minimal — on `career-mode-v0_1`)

- The human's hole cards now **present** at the all-in matchup: at reveal they throw up over the
  board (left card, then right a beat later) and **hold** while the opponents read the matchup,
  then **pull back to their original dealt placement** the instant the run-out's first board card
  deals — so the board is unobscured as it runs. Client-only, no backend change (rides the
  existing schedule + reveal). `useRunoutDirector` exposes `heroCommitted`/`heroRetreating`;
  `MobilePokerTable` selects per-card animations via a `heroCardAnimation()` helper driving
  `heroPresentUp*`/`heroPullDown*` keyframes. Gesture timing centralized in `RUNOUT_TIMING.hero`;
  keyframe *shape* (reach/spread/tilt) in `MobilePokerTable.css`. Reduced-motion → no slam.
  Hand-tuned with the user over several passes, then a `code-simplifier` cleanup (extracted the
  helper, centralized timing). 9 director tests cover present→hold→retreat→reset.

**Not built (deferred):** §E's *equity-picked gesture variants* + board/pot nudge; the "full
client-owned timeline" (board freeze / `beginShuffle` gating / reconnect persistence) — see the
§C build-time finding for why option B made those unnecessary rather than merely deferred.
