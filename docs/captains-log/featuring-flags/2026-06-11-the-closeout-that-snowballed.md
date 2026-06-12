---
purpose: Narrative log of finishing the feature-flag consolidation and the branch-archaeology / feature-revival work it snowballed into
type: guide
created: 2026-06-11
last_updated: 2026-06-11
---

# The flag closeout that snowballed (2026-06-11)

The ask was small: "consolidate and document all current feature flags, decide
promote/hold/kill for each." A finishing pass on the registry built on 06-07.
It did not stay small. Cleaning up flags pulled a thread that ran through branch
archaeology, recovering two pieces of lost work, and reviving a whole feature
that had never merged. Logging it as one entry because that is how it happened:
one tidy job at a time, each uncovering the next.

## Flags: the "looks dead but isn't" pattern, three times

The plan part was easy. The doing part kept tripping on the same shape: a flag
that looks dead but is load-bearing.

- `RAKE_ENABLED` is marked GRADUATED, which `flags.py check` flags as "delete the
  dead branches." But `compute_rake` returns 0 when it is off, and the economy
  conservation tests toggle it as a live faucet-vs-sink knob. Not dead. Left it,
  noted the GRADUATED-vs-still-a-knob contradiction instead.
- `PRESENCE_SHADOW_WRITE_ENABLED` is RETIRED. Looked like a clean delete of a
  whole module. But the `presence_shadow` module is alive, kept on by the
  graduated `PRESENCE_AUTHORITY_ENABLED` (it ORs the two). Only the retired
  operand was dead. The real removal was small; the apparent removal was a trap.
- `REGEN_ENABLED` again: a live, tested mechanism, just off by default. Retiring
  it meant stage flip plus partition bookkeeping, not ripping out the faucet.

Jeff's instinct caught the first one before I did ("rake_enabled looks suspect").
After that I stopped trusting `flags.py check`'s "dead branch" label and read the
call sites every time. That was the right call each time.

The migration found a real gap: a `_bool_env` helper in `guest_limits.py` read
`os.environ.get(name)` with a *variable* name, which slid past the centralization
guard (it only matches literal flag names). That is exactly how
`GUEST_FREE_CHAT_ENABLED`, a security toggle, stayed invisible. Closed it by
banning local bool-env helpers in a new test.

Then the lifecycle calls: closed the Director-thermostat dev/prod drift (there was
no principled reason dev ran the simpler economy, just unarmed dev envs), retired
REGEN, and promoted the three economy levers to dev-only BETA.

## Where I was wrong about tilt

I told Jeff `TILT_CONDITIONING_ENABLED` was blocked: the reachability analysis
said tilt was nearly unreachable, so promoting it would ship an inert feature.
Jeff pushed back: "I think we're actually ready to turn that on." I was citing a
stale version of the doc. Its own measurement update had retracted the
"unreachable" framing weeks earlier (a bad beat tilts 56 of 104 personas). The
feature was reachable, wired to the maniac, and tested. I had asserted a blocker
from memory instead of re-reading. Promoted it. Second time this kind of thing
bit me today, which is the theme below.

## The branch cleanup, and two self-inflicted wounds

92 local branches. The goal was to delete the safely-merged ones without losing
anything. `git branch --merged` only catches merge-commit merges, not squash
merges, so I built up the verification in layers: merged-ancestor, then patch-id
equivalence (`git cherry`) for squash-merges, then file-existence-in-main for the
ones squashed under a different shape. Down to a handful. `renown` looked
unmerged but its fix had landed a *better* way (main dropped the median-floor
mechanism the branch was tuning), so it was obsolete, not pending.

Two mistakes here, both mine, both about side effects I did not think through:

1. Running an economy sim, I forgot `--db-path` on the run step (I put it on the
   seed step). The sim wrote orphan rows into the live dev DB. The harness
   correctly blocked my cleanup DELETE, and I had to ask Jeff to authorize it.
   The rows were drift-neutral, but it was avoidable.
2. To "sanity check" the salvaged marketing `publish-next.mjs` script, I ran it.
   It is not a dry-run tool. It published a draft blog post (`the-circuit`),
   flipping its `draft: true` and stamping the date. Reverted it cleanly, but I
   should have read the script, not executed it. I had literally just read that
   it mutates files.

Lesson I keep relearning: do not run unfamiliar mutating things to inspect them,
and double-check the destructive flags on a command before it touches shared
state.

The cleanup also recovered real lost work. `image-prompt-factory` (4 months
stale) had a fix that never merged, and main still carried the bug it fixed:
the content-policy fallback mutated a *cached* personality dict in place. Salvaged
it by re-applying to current code rather than cherry-picking the stale diff.

## Push/fold 6max: the feature the reviews actually finished

The big one. `push-fold-6max` was a complete, never-merged feature (a multi-way
Nash push/fold chart, the "#1 SNG leak" per its own scope doc). I revived it: the
chart and lookup dropped in clean, but the controller routing was 70 commits
behind, so I re-applied it by hand against current code, keeping main's improved
effective-stack accounting instead of the branch's older inline copy.

Then the reviews came, and this is the honest part: they found five separate
correctness bugs, each valid, and my initial revival had shipped or carried all
five. Every one was the same class: the chart firing in a spot it does not model.

- multiple all-ins, the loop kept the last jammer in seat order
- limped pots, a call does not bump `raises_this_round`, so they hit the first-in chart
- a short all-in under a larger live raise, treated as the faced jam
- a non-BB hero getting the BB-vs-jam tables
- 7-to-9-max tables getting 6-max ranges (9-max even collapses to a UTG fallback)

My unit tests had covered the happy path and nothing else. The feature only
became fail-closed through the review cycle, not through my implementation. One
of the review sweeps I ran myself (checking whether the same aggressor-id bug
existed in the other charts) turned up a backwards docstring that, if someone had
"fixed the code to match the comment," would have *introduced* the bug. So the
reviews were net strongly positive, but the takeaway stands: I should have built
the out-of-scope tests first, not after a reviewer pointed at each gate.

## What I am taking away

The flag work was solid and is mostly merged. The branch archaeology was careful
and recovered two real things. The push/fold feature is genuinely useful and now
honestly scoped, with a handoff note in the scope doc for the open items (the
biggest being that the ranges are still only validated against published Nash, not
a real short-stack sim, because the sim knob to exercise them was never built).

But the pattern across the whole day is one thing: I am fast at the mechanical
work and too quick to trust a label, a memory, or my own first cut. "rake looks
suspect," "I think we're ready on tilt," and five review findings on push/fold
were all cases where reading the actual thing one more time would have saved a
correction. The corrections happened, which is the point of the loop, but the
cheaper path was available each time.
