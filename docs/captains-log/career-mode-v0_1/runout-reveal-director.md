---
purpose: Grounded narrative log of the Phase 2 run-out reveal director build (client-owned all-in run-out)
type: reference
created: 2026-05-29
last_updated: 2026-05-29
---

# Captain's log — the run-out reveal director, Phase 2 (career-mode-v0_1 worktree)

Honest record of picking up `docs/plans/RUNOUT_REVEAL_DIRECTOR.md` at Phase 2.
Newest entries at the bottom. Design doc + companion
`docs/technical/EMOTION_AND_PRESSURE_ARCHITECTURE.md` are the references.

---

## 2026-05-29 — picking up Phase 2, and the fork the doc didn't close

**Where Phase 1 left it.** Phase 1 (shipped, committed `b06371d4` / `39a1f060`
on this branch) is *street-granular*: a staggered CSS reveal cascade, and avatar
reactions at FLOP/TURN/RIVER boundaries, still paced by the backend
`progress_game` run-out loop. Phase 2's promise is the *client-owned* timeline:
per-**card** reactions and a director that owns the beat.

**The user's four calls (asked up front).** Core director first (defer §E human
hole-card "commit"); silent-skip + suppress-all-cards on reconnect (don't persist
the schedule); per-step current-street cards only (no future-card leak); and —
the one the doc under-specified — a single backend path, no mobile/desktop
branching.

**The fork I hit before writing any code.** The doc says, in two places, both
"retire backend pacing" *and* "leave desktop on the old backend pacing." I went
looking for how the backend tells mobile from desktop so it could branch — and
it can't. `ResponsiveGameLayout` picks `MobilePokerTable` vs `PokerTable` purely
from `useViewport().isMobile` (client-side width). The backend `progress_game`
loop is entirely client-agnostic, and both clients share `usePokerGame`'s GATED
card-animation buffer. Desktop's `PokerTable` has **no** run-out director — it
leans entirely on backend sleeps + the shared buffer. So "retire pacing for
mobile, keep it for desktop" is not expressible from one loop. That's a real
design contradiction in the doc, not a detail.

**Why not the obvious "just do per-card reactions on the backend loop" (option
C).** Tempting — smallest change, both clients for free. But per-card flop
reactions would need the backend to sleep *between individual flop cards* to line
its reaction emits up with the client's CSS flop stagger. That's the exact
split-clock anti-pattern the whole doc exists to kill, just finer-grained. The
doc says so outright. So C looks cheap but bakes in jank.

**The call: option B.** One backend path that emits a superset — keep the
per-street states + sleeps + per-street avatar reactions (desktop renders these
unchanged), and *additionally* emit one `runout_schedule` (reactions + per-card
timing, no future-street cards). Mobile runs `useRunoutDirector` off the
schedule, owns the flop's per-card cascade, and reads each street's card faces
from the per-street state it already receives (current street only → no
future-street leak). Desktop ignores the schedule and is genuinely untouched —
and can adopt the *same* director later with zero backend change. That's the
"single backend path, extensible to desktop" property the user wanted, and it's
the only single-path option that gets clean per-card reactions.

**Honest limitation of B I'm not hiding from.** Because the backend still paces
street-to-street for desktop, the mobile director is bounded-below by backend
pace *between* streets — it can't outrun card arrival. The real win is *within*
the street (the per-card flop cascade + reactions on their own beats) and owning
the holds. Full clock independence would need a client-type flag (rejected) or
retiring pacing for all (breaks desktop). Worth revisiting only if the
backend-paced street cadence feels wrong once the per-card cascade is in.

**What I built (option B, two commits).** `83351e12` — restructured
`compute_runout_reactions` to walk the flop card-by-card and emit an ordered
`steps` list, keeping `reactions_by_phase` byte-identical for the unchanged
backend/desktop path; added `runout_schedule_payload` (reactions + timing, never
a card). Tested with a mocked equity calculator so the per-card branching is
deterministic (no eval7 MC variance): the headline assertion is the one in
`test_legacy_street_view_collapses_the_flop` — a mid-flop swing the per-card
steps capture is *correctly absent* from the street-level view, which is the
whole point of going per-card. `abb53197` — `useRunoutDirector`, the
`runout_schedule` emit, and the socket-layer suppression.

**The nicest discovery.** I worried the emotion-only schedule wasn't enough to
render a reaction face — faces are per-emotion backend images, and the client
can't synthesize them. Then I found the existing code already rewrites the
avatar URL's emotion segment client-side for the "thinking" highlight
(`/api/avatar/{name}/{emotion}`, 404→fallback). So the director renders any
reaction by the same trick — no avatar URLs in the payload, no backend change.
That collapsed a whole sub-problem.

**Why most of the doc's §C machinery evaporated.** I'd braced for the scary part
— freezing `communityCards`, bypassing the GATED card buffer, gating
`beginShuffle`, persisting the schedule for reconnect. Under option B none of it
is needed: the board stays backend-paced, so the buffer/board/result-beat are
untouched and the reconnect + info-leak open questions are simply moot (cards
still arrive per-street; nothing future ships early). The one real coordination
seam is the `runoutDirectorActive` flag so mobile's finer faces aren't clobbered
by the backend's street-level reaction emits (which still fire for desktop).

**State at end of session.** Green: 5 backend reaction tests, 8 director hook
tests, all 160 mobile vitest, tsc clean, eslint clean on touched files. Backend
imports verified. Not yet manually run in the app (next: an all-in to eyeball
the per-card cascade timing — `runoutTiming.ts` offsets are educated guesses
aligned to the 1.0s/card community cascade and will likely want a tuning pass).
§E (human hole-card commit) untouched, as scoped.
