---
purpose: Narrative log of building the perceptibility/conditioning believability feature (#12) across five delegated phases, plus the two real wrong turns — a stacked PR that "merged" without delivering, and uncommitted correctness fixes nearly lost in a branch switch
type: reference
created: 2026-06-10
last_updated: 2026-06-10
---

# Perceptibility, conditioning, and the stacked-PR trap

This was the believability push — backlog #12, the "make the adaptation *felt*"
frontier — built mostly overnight while Jeff slept ("run with it"). It also
caught two mistakes that are better blog material than the feature itself.

## The feature (what shipped, PR #255)

The thesis, from the research + our own Finding 3: the tiered bot already adapts
(the exploitation layer reads opponents) but it's invisible — fires ~2% of
decisions at a near-zero, never-surfaced magnitude. "Stacked"/Poki had genuine
opponent-modeling and was *still* "readable in 40 hands" because the adaptation
was imperceptible and the aggression was a flat constant. So: surface the read,
condition the aggression.

Built via the feature-dev flow (explore → clarify → architect → build), five
phases, each delegated to a background agent and reviewed/committed in turn:

1. **Surface the read** — the sharp bot now voices an *earned* read ("you've
   folded every 3-bet tonight"), confidence-arc-gated (tentative→confident→sure),
   rate-limited, intuition-framed (a test forbids any number/jargon leaking into
   the phrasing). Frequency-neutral — it's post-decision.
2. **`tilt_conditioning` layer** (Option C, the clean traceable layer) — inert by
   default, flag-gated off, no archetype opted in at its own commit → byte-identical.
3. **Maniac #9** — the one default change: baseline 3-bet 36→30, with tilt
   spiking it to ~41 transiently (flag-on).
4. **Sizing tell** folds into the Phase-1 path.
5. **2AFC harness** — to actually *measure* perceptibility later.

The discipline that made an overnight autonomous run safe: every
frequency-touching piece is flag-off-by-default, the full suite ran each phase
(8284 green at the end), and the one real default change (#9) was probe-validated
in-band with the other six archetypes byte-identical.

## Wrong turn #1 — the stacked PR that delivered nothing

Earlier in the session I shipped the rock/stats batch as **PR #251 stacked on
#249** (`archetype-rock-and-stats` → `sizing-tendencies-design`). #249 merged to
`main` first. GitHub then marked #251 "MERGED" — but its base had already folded
into main, so #251's commits **never reached `main`**. The work was stranded on
the branch while the PR looked done.

Jeff caught it cold: "there is no open PR right now." I'd been blithely PATCHing
#251's title, treating a merged PR as live. I verified (`gh pr list --state all`):
both #249 and #251 were MERGED, and `origin/main..archetype-rock-and-stats` still
listed all five commits — they were not in main. Opened **#252** (branch → main
directly) to actually land them. It merged.

Lesson, now a rule: **don't stack a PR on a base that's about to merge.** Base the
next branch on `main` and merge in dependency order, or keep one branch. Every
branch since (#12) is off `main`.

## Wrong turn #2 (caught, not committed) — fixes nearly lost in a checkout

Creating the #12 branch off `main`, `git checkout -b` carried two **uncommitted**
test-file mods across. My first instinct was "stale leftovers, discard." They were
not: a real, well-tested correctness fix to the just-merged #11/#6 —

- **WTSD over-counted showdowns**: a flop-seer who *folded the turn* was credited
  with "went to showdown" whenever the *hand* showdown'd. WTSD is per-PLAYER.
- **C-bet keyed on the wrong raiser**: the sim flagged c-bet on the *first* raiser,
  not the last preflop raiser (in a 3-bet pot, the 3-bettor).

I surfaced it instead of discarding, Jeff said bundle it, and it became the first
commit on the branch. The near-miss: treating uncommitted working-tree changes as
disposable. They almost never are.

## The honest deferral

#9 wanted the maniac baseline at ~20-25. It floored at ~30 — the shared loose
chart's own re-raise mass is ~30%, and the cap can only pull *toward* the chart,
not below it. Closing the last ~5pt needs a maniac-only chart (the loose chart is
shared with spewy_fish/maniac_overbluff). Landed ~30, re-banded to it, logged the
gap rather than hacking the shared chart.

## The small one

The 2AFC harness tripped `test_flags_are_only_read_through_the_registry` — it
save/restored `TILT_CONDITIONING_ENABLED` via a raw `os.environ.get`, which the
invariant forbids. Fixed with `mock.patch.dict` (sets + auto-restores, no raw
read) — spirit-correct: `resolve()` stays the only flag reader; the harness just
scopes the env the registry reads.
