---
purpose: Narrative log of the run-out/showdown presentation-sequencer rework
type: guide
created: 2026-06-06
last_updated: 2026-06-06
---

# Presentation sequencer (run-out / showdown / inter-hand)

## Why

The hand playback "directors" had grown organically and fought each other. The
sharpest symptom: when the backend resolved a hand fast (AI-vs-AI, or after the
human folded), `winner_announcement` bypassed the client's card-animation buffer
queue and set `winnerInfo` immediately — so the result overlay appeared while the
queued AI actions were still replaying. "We saw all the cards but the AI was still
going back and forth on check/raise/call — they weren't connected." Plus a split
clock: the backend slept to pace the all-in board while the client *also* gated
the same deals, so it felt off and you couldn't tell a deliberate beat from a stall.

## What we did (Phases 1–3)

Decided with Jeff to unify, not patch — pay down the "fast not right" debt. One
**pure sequencer engine** (`handSequencer.ts`, no React, unit-tested) plans each
backend signal into `{ timeline, durationMs, next }`; a **thin driver hook**
(`useHandSequencer.ts`) runs one timer at a time, draining beats on one clock. The
winner is just the last beat in the queue, so it structurally can't outrun the
actions. One 3-tier clock (`presentationTiming.ts`): watchable 1.0× / fast 0.4× /
fastest (flush + recap), derived from the existing FF flags.

- **Phase 1**: engine + tiers + spec + 11 unit tests.
- **Phase 2**: wired into `usePokerGame`; deleted the `BufferState` machine,
  `useRunoutDirector`, and `useWinnerRevealGate`; kept `useInterhandDirector`
  (it was already correct). Both tables now read `heroCommitted`/`heroRetreating`
  from the shared hook, so desktop comes along for free. Retired the store's
  `runoutSchedule` (the sequencer holds it in a ref). 262 FE tests green, tsc +
  eslint clean.
- **Phase 3**: cut the backend run-out pacing sleeps (`RUNOUT_REVEAL_HOLD`, the
  per-street animation/reaction holds, the showdown hold, the hand-over visual
  delays) down to `socketio.sleep(0)` yields. The backend now streams the run-out
  as fast as it computes it; the client owns all pacing. Removed the now-redundant
  per-street `_emit_avatar_reaction` path (the sequencer walks `runout_schedule`
  instead). Backend ruff + fast-forward/run-out tests green.

## Honest notes / wrong turns

- Editor TS diagnostics screamed "Cannot find module 'react'" the whole time —
  that's the local server with no node_modules in view, not real. The canonical
  check is `tsc --noEmit` in the frontend container; trust that, not the inline
  squiggles.
- Kept `_ff_aware_sleep` even though it's now callerless: it has dedicated tests
  and is a reasonable backend utility to retain. `runout_emotion_overrides` is now
  always empty during a run-out (client guard preserves the sequencer face), so
  the field reads are vestigial-but-harmless; `reactions_by_phase` on the schedule
  is likewise no longer consumed backend-side. Flagged, not yet pruned.

## Behavior changes to watch in playtest (the Phase-3 check-in)

- **Every AI action now gets a ~1s beat** in watchable mode (previously NORMAL
  applied instantly, only post-deal actions paced). This is the requested "beats,"
  but it makes hands deliberately slower — heads-up cadence especially. The knob
  is `BEAT.action` in `presentationTiming.ts`.
- **Fastest tier (`always`/instant) is a silent flush** right now — the brief
  narrator recap is stubbed for Phase 4.
- Desktop got the sequencer's ordering + reactions for free, but desktop overlay
  polish (and reconnect resilience) is deferred to Phase 5.
