---
purpose: Narrative log of driving nit/rock into their target bands on the sim — the deferral I had to be called out of, the shared-keep trap on fold-to-3bet, and the postflop nuance between the two
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

# Driving nit/rock into band

## The deferral I kept reaching for

The nit/rock looseness had been sitting as "a play-test, not a blocker." I kept
saying that — "the sim got them close, the rest is feel," "play-test before pushing
further." The user cut through it: *"i want the sim to get them close, my feel isn't
going to help test at scale. you keep deferring the work and i'm confused."*

They were right, and it stung a little because it was true. I'd been treating
play-test as the instrument when the sim *is* the instrument — at scale, a human's
feel for whether a nit is "a bit loose" is worthless next to 9000 deterministic
hands scored against a band. The unlock for the whole day was just: stop hedging,
drive the number on the gate.

## Build the gate first (it already existed)

"Gate first, then tune" was the plan, and the pleasant surprise was that the gate
was 80% built — `archetype_mixedfield_probe.py`, already tracked, deterministic,
seating the 7-archetype field the bands are calibrated for. It just needed to
*gate*: exit non-zero on a hard fail. The judgment was what counts as a hard fail.
First pass flagged `tag W$SD 49.8` against a 52-56 band — but at n=434 that's inside
the sampling error, and W$SD is a high-variance showdown stat. So the gate
hard-fails only the high-n frequency stats (vpip/pfr/3bet/all-in) and warns on the
variance-heavy tail. A deterministic gate that reddens on noise is worse than no
gate; people learn to ignore it.

## The shared-keep trap

VPIP came down easily — fold more marginal flats facing an open, nit 20→15. But
fold-to-3bet wouldn't move. I'd added a `vs_3bet` tighten and it did nothing.

The trap: nit and rock share one chart, and my "keep pool" of premiums was the same
for facing-an-open and facing-a-3-bet. But a nit's *opening* range is already mostly
premiums — so facing a 3-bet, the keep pool protected almost the whole range, and
there was nothing left to fold. The fix was a *separate, tighter* keep for the
3-bet node: continue only QQ+/AK, fold the medium opens (JJ-99/AJ/KQ). That's the
believable "scared-tight" leak — and it's what the band actually wanted.

Which was the other realization: I'd guessed the fold-to-3bet *band* was probably
too high. It wasn't. It deliberately encodes over-folding (fold above MDF) — the
tight player who over-respects aggression. The chart had been defending too
correctly (~MDF). A "band-not-bug," but inverted: the bug was the *behavior* being
too GTO, and the band was right all along. Same lesson as the rock-band-inversion
and the maniac WARNs: check the band's intent before assuming either side is wrong.

## A stale checkout nearly sent me chasing a ghost

After shipping the in-band charts I went to verify the actual behavior and found
TT/AJs *not* being folded — the tighten apparently inert. I started writing it up as
a bug. It wasn't: my local `main` was a stale checkout sitting a merge behind, so I
was reading the previous PR's chart. One `git fetch` and the real chart showed
TT/99/AJ folding 80% exactly as designed. The honest note here is that I almost
filed a false alarm because I trusted my working tree over the remote; the fix was
to check `origin/main`, not my local ref.

## The nuance I'd flattened

The sharpest moment was the user's: *"are we treating them the same? there is a
nuance between the two in postflop especially."* And the data agreed with them, not
me. nit and rock were genuinely different on *betting* (nit c-bets 53%, rock 34% —
tight-aggressive vs tight-passive, working). But on *calling down* they were
identical, both too sticky. A nit is supposed to be bet-or-**fold**; its profile had
an `auto_cbet` lever for the betting half and nothing for the folding half. So after
the preflop tighten gave it a stronger range, it just called down like a rock.

Adding `fit_or_fold` + `give_up_turn` to nit (strong) and a mild `fit_or_fold` to
rock got their fold-to-c-bet into band and separated them on the fold side too. The
honest leftover is WTSD: it stayed a hair over band and *resisted* the fold levers,
because it's structural — a VPIP-15 nit folds its weak hands preflop, so the hands
that see a flop are strong and legitimately go to showdown (and win). You can't
satisfy a 22-28 WTSD band and a 10-16 VPIP band at once without folding strong hands
to river bets, which is absurd. So I left it warn-only and said so, rather than
torturing the number.

## The throughline

The day's lesson was the user's, not mine: the sim is the test, so use it — don't
defer to a feel that doesn't scale. And the recurring one held again — measure
before believing the framing. The fold-to-3bet "band looks wrong" was wrong; the
"#306 tighten is inert" was a stale checkout; the postflop "we're good" missed a
real nuance. Each time the correction came from looking at the actual numbers (or
the actual remote), not the story I'd attached to them.
