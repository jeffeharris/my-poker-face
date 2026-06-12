---
purpose: Narrative log of the mobile avatar-row sizing rework (unified weight model)
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

# The director was already running

## Why

Jeff brought this in framed as a director problem: "Following the director
framework we've used for runouts and showdowns, can you help me evaluate a
similar solution for the avatar row?" The row of opponent avatars does a lot of
expand-to-fill / expand-for-your-turn / shrink-on-fold activity and it could be
janky — sometimes smooth, sometimes a sharp cut, and when 3–4 opponents all
folded to you they'd "all shrink to the fold width and slide to the left so it
looks weird."

## The first wrong turn (mine)

The instinct to reach for the director (`handSequencer`) was right at the
*philosophy* level, but I had to dig in to find the actual axis. The discovery
that reframed everything: **the director already governs this row.** Fold/turn
state drives avatar sizing via `store.players`, and the sequencer writes that
store one beat at a time via `applyState`. So four folds aren't applied on the
same React tick — they're already serialized. Adding a *second* director would
have been redundant with what's already there. The jank lived on a different
axis.

Then I aimed my first clarifying question at the wrong axis anyway. I asked Jeff
how the *collapse should feel* — smooth-simultaneous vs staggered cascade vs
batch — i.e. a question about timing. He corrected me: the problem isn't the
timing of the collapse, it's the **final distribution**. "It works fine for the
first 3 but the 4th folds and now they are all shrunk and they don't fill the
space on the bar." The fold width was a fixed `14vw` with `flex-shrink: 0`, so
once everyone folded you got four `14vw` chips marooned at the left with dead
space to the right. He wanted them redistributed evenly across the bar, still
in fold state, a little wider based on how many there are.

## What was actually wrong

Two layout engines bolted together:

- a **default fixed-width scrolling row** (30vw / 50vw thinking / 14vw folded),
  and
- **hand-coded per-count special cases** — `two-opponents-mode`,
  `three-opponents-mode`, `three-opponents-showdown-mode` — each with its own
  magic flex numbers (`flex: 1.5 1 60%`, etc).

Those special cases were essentially **mini-directors, one per opponent count**.
There was no `four-opponents-mode`, so at 4 the default scroll layout leaked
through and the fixed `14vw` chips couldn't grow to fill. The punchline that
tied it back to Jeff's instinct: the fix was the director *lesson* (replace N
special cases with one model), applied to layout instead of timing.

## What we did

Replaced the per-count handlers with a single weight model. Each avatar's width
is its `flex-grow` share of the bar, set per state class:

```
folded 0.4   ·   normal 1.0   ·   thinking 1.7
```

with `flex-basis: 0` and min/max clamps. Equal states share equally and fill
the row; a mix gives live players more. When everyone folds, equal weights
spread them evenly across the full bar — wider than the old `14vw`, scaled by
how many remain. That's exactly the behavior the per-count cases hand-coded for
2 and 3, generalized to 4–5 for free. Heads-up keeps its own mode (it has the
psychology panel); 6+ falls back to the scrolling row.

## The second wrong turn (also mine)

In the evaluation I pitched framer-motion `layout` (already a dependency) as the
motion substrate. Building it, I walked that back to **pure CSS**. Two reasons:
the codebase's own proven prior art for this exact row is the CSS `flex`
transition the 2/3 modes already used, and framer's `layout` animates size via a
scale transform that would distort the avatar faces/text mid-tween. CSS
`flex-grow` transitions give smooth resize *and* smooth sibling reflow with zero
distortion. Worth remembering: I'd also told Jeff early on that siblings
"teleport because flex reflow isn't animatable" — that was wrong. CSS width /
flex-grow transitions *do* reflow siblings continuously each frame. The real
culprits were the fixed-width-no-fill default mode and a stray scroll nudge.

## The hiccup Jeff caught

After it shipped to his dev environment: "with 4 it looked like there was a
small hiccup right at first, the leftmost avatar got pushed off the screen just
slightly. but with 5 and 3 it looked great." This was the one racing clock I'd
flagged in the evaluation but not yet pulled in: the turn-change auto-scroll
effect (`scrollTo` on a 320ms timeout). In fill mode the row stretches to fit
and never needs to scroll, but the active player's glow ring (`::after`, `inset:
-8px`) briefly overflows the container's left edge — enough for `scrollTo` to
nudge the row and clip the leftmost avatar. Gated the auto-scroll to scroll mode
only (the case it was actually written for), and bumped fill-mode edge padding
`8px → 12px` so the glow ring isn't clipped by `overflow-x: hidden`.

## Verifying

Couldn't easily drive a live 4-folded table, so I built a phone-width static
harness that linked the *real* stylesheets (so every design token resolved) and
rendered all four fill states — all-active, one-to-act, mixed, all-folded.
The all-folded row spreading evenly across the full bar was the money shot: the
fix Jeff asked for falls straight out of equal weights, no per-count code.

## Incidental yak-shave

The stack wouldn't come up to test: the worktree's gitignored
`docker-compose.override.yml` was pinned to subnet `.50`, colliding with the
ios-conversion worktree (moved it to a free `.51`), and `./data` had been
auto-created `root:root` while the backend runs as uid 1000, so SQLite hit
`unable to open database file` (recreated the dir as the host user — no sudo,
since the parent is mine). Both are the documented worktree gotchas, just
biting at once.

## Result

PR #307. Net −58 lines — the per-count special-case CSS is gone, replaced by one
weight model. tsc + eslint clean.
