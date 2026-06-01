---
purpose: Grounded narrative log of bringing the desktop poker UI to feature parity with mobile
type: reference
created: 2026-06-01
last_updated: 2026-06-01
---

# Captain's log — desktop ↔ mobile parity (desktop worktree)

Honest record of closing the desktop/mobile feature gap. Newest entries at the bottom.

## 2026-06-01 — the gap, the fork, and the build

Started from a `/goal`: "get desktop functionality to parity with mobile; unify as much
as possible." First instinct was right: don't guess the gap, map it. A code-explorer
inventoried both tables — mobile's `MobilePokerTable` is 1246 lines with a whole ecosystem
(coach, floating chat, directors, richer winner, animations); desktop's `PokerTable` was
512 lines and missing ~13 of those. The split is by viewport in `ResponsiveGameLayout`
(<768px → mobile, else desktop stadium layout).

**Wrong turn, corrected:** I jumped to an AskUserQuestion offering three unification
strategies before the user had context. They rejected it twice — they wanted *guidance*,
not a menu ("I don't know what's realistic"). The right move was to explain the feasibility
tiers and make a recommendation. Their own follow-up ("benefits to having things on desktop
that don't show up on mobile") nailed the key insight: desktop has the room to show coach +
chat + stats + felt *all at once*, so parity should mean "same features, each in its best
container" — a bottom sheet on mobile becomes a docked sidebar on desktop. Not a stretched
phone. They chose: shared feature core, keep the stadium layout, skip merging the two table
files (Tier 3 — pure refactor, zero user value).

**Execution.** Tier 1 quick wins by hand (nickname overrides, reconnect overlay, preemptive
check/fold, guest-limit modal, LLM-debug modal). Then parallelized the big isolated systems
across feature-dev `claude` sub-agents on disjoint files — coach (docked CoachDock + a shared
`CoachPanelBody` extracted from mobile's sheet), richer winner, quick-chat, hero animation,
and finally the run-out director — while I stayed sole editor of `PokerTable.tsx` to keep it
coherent. That division (agents build new files + hand back wiring snippets; I splice) avoided
merge conflicts on the one hot file.

**The coach default-off trap.** After wiring the coach behind `coachEnabled = !isGuest &&
coach.mode !== 'off'`, I checked the default mode: it's `'off'`. So the entire coach I'd built
was unreachable on desktop for *everyone* — mobile re-enables it via a menu toggle desktop
didn't have. Added a coach on/off toggle to GameHeader. Lesson: when you gate a feature behind
a persisted mode, verify the *default* actually surfaces it.

**The pre-existing build break.** `tsc --noEmit` (the project's canonical check) was green the
whole way, but `npm run build` (`tsc -b`, stricter build mode) failed in `gameStore.ts:258` —
a file nobody touched. Stashed all my work and rebuilt clean to prove it: the error reproduced
at HEAD. `applyOptimisticAction` assigned `last_action: action` (a `string`) into a literal
union. One-line cast fixed it; flagged as incidental, not mine.

**Keyframe collision.** The runout agent reused mobile's `heroCardAnimation()` util, which
emits fixed global keyframe names, but defined desktop keyframes with the *same names* at px
(vs mobile's dvh) scale. CSS is global in the SPA, so a mobile→desktop nav in one session
would let the wrong sheet win. Gave the util an optional name-prefix (default `''` keeps mobile
identical; desktop passes `'cmd'`).

A code-reviewer pass caught two real things: `handleCoachToggle` depended on the whole `coach`
object (a fresh literal each render) — would defeat GameHeader's memo on every socket tick;
narrowed to `[coach.mode, coach.setMode]`. And two overlays (LLMDebugModal, GuestLimitModal)
rendered inline instead of portaling to body — against the repo's documented overlay
convention; wrapped both in `createPortal`.

**Where it landed.** tsc, eslint `--max-warnings=0`, and `npm run build` all green. What's NOT
done: live in-browser verification — `docker compose up` is wedged on "all predefined address
pools fully subnetted" (≈19 stale worktree poker-networks pinning Docker's pool; the host-wide
`network prune` is auto-denied). So this is statically verified and faithfully ported from the
proven mobile components, but no human has watched a desktop hand play out yet. Uncommitted.
