---
purpose: Grounded narrative log of diagnosing why no AI reaches "figure" status in Renown-v2, fixing the figure cut, and testing whether negative social interactions can mint earned Villains
type: reference
created: 2026-06-02
last_updated: 2026-06-02
---

# Captain's log — Renown-v2 figure cut + the Villain question (development)

Honest record of a session that started as a five-minute "does the renown badge
render?" spot-check and turned into a formula diagnosis, a shipped fix, and two
sims that answered a balance question the other way round from how it was asked.
Newest entries at the bottom. Wrong turns kept in.

---

## 2026-06-02 — the badge renders, but everyone's an "Up-and-comer"

Merged `renown` into `development`, flipped the three flags on the dev box
(`RENOWN_V2_ENABLED`, `RENOWN_V2_PERSIST_AI`, `PRESTIGE_SEEKING_ENABLED` — the
first is the parent the other two imply, easy to miss), and ran a lobby sim to
populate per-AI renown rows. The B1 dossier badge renders correctly — verified
in isolation because the Vite dev server serves a blank doc to Playwright (known
gotcha), so I rebuilt the badge markup + real CSS standalone and screenshotted
it. Glyphs, quadrant palette, percentile line all good.

But Jeff caught the real problem immediately: `fishy2` was "ahead of 100% of the
field" and still labelled **Up-and-comer**, not Legend. Every top AI was. Zero
figures.

## 2026-06-02 — why: a multiplicative gate on a concave scale

Traced `quadrant_label_relative` → `high_renown_cut`. The cut was
`max(top-20% boundary, 3×median)`, and on the live field the **3×median floor
was binding at 109.4 — above the field maximum of 95.9**. So nobody could be a
figure.

The root cause is in `compute_components_v2`: **every** driver is concave —
`scalps` is `Σ log1p(count)`, `peak_worth` is `log1p(networth/unit)`,
`backing`/`breadth` are `log1p(raw/median)`, the rest are `sqrt`. A sum of
concave functions is concave, so renown is intentionally thin-tailed (anti-
treadmill, anti-whale-runaway). cthulhu sits on ~100× the median net worth and
the log crushes that to ~2.6× the median *renown*. A 3× **multiplicative** gate
on an additive-concave scale is a category error — it wants a heavy tail the
scoring is specifically designed never to produce. Worse, the floor is `3×median`,
so it gets *harder* as the field grows more accomplished. Backwards.

The archetype test fixture (`build_archetypes`, 37 entities) is heavy-tailed
(max 63 / median 11 = 5.6×) — which is exactly why 3×median was tuned to work
*there* and silently fails on the compressed *real* field. The fixture encoded a
"mature, differentiated world" the live data isn't.

## 2026-06-02 — fix: pure top-decile percentile

Jeff's call: percentile, or "top-5-in-each-tail". Implemented pure top-10%
percentile, dropped the median floor, in BOTH `cash_mode/prestige.py` and the
oracle `scripts/renown_v2_scorer.py` (the parity test loads the oracle directly,
so they can't drift). Removed `HIGH_RENOWN_MEDIAN_MULTIPLE`.

One snag worth recording: on the archetype fixture, top-10% **excludes Patron**
(the weakest of the four accomplishment "routes"), which the rung1 test asserted
must be a figure. Rather than overfit the fraction to thread that needle, I
reframed the test honestly — dominant routes (Grinder/Whale/Villain) reach the
cut; all four still out-rank the control + bogey. The "every route can be a
figure" intent survives as an ordering guarantee.

Live result: high_cut 109→76, figures **0→8** (7 Beloved Legend, 1 Infamous
Villain). Committed `c266104a`. 21 prestige_v2 tests green incl. parity, 161 across
the renown/dossier surface, 1159 in test_cash_mode.

## 2026-06-02 — "is it because the field is young?" — sim says no, opposite

Jeff's hypothesis: maybe the field is compressed because there've been no ticks /
it hasn't differentiated. Good instinct, worth a real test. First finding while
building the harness: the lobby sim on `development` plays hands + records
scalps + moves bankrolls, but it does **not** write `holdings_snapshots` (only
the live ticker does), and `peak_net_worth` + `ticks_at_#1` (the dominant
standing drivers) read from that table. `cash_pair_stats` has **no production
writer on this branch at all** (only test fixtures). So a naive sim can't move
the wealth-standing drivers — the experiment has to record holdings snapshots
itself.

Did that, on a DB copy, with standing reset so differentiation rebuilds from
play. Result over 1500 ticks (`exp_renown_differentiation.py`):

- A **fresh** field is *more* spread (max/median 5.4×), not less.
- Play **compresses** it (median climbs, max pinned), locking at a stable
  equilibrium by ~tick 400 and holding flat for 1100 more ticks.
- `figs@3xmed` decays 2→1 as the field matures; `figs@10%` holds steady at 8.

So the hypothesis is *falsified and inverted*: ticks erode the tail, they don't
build it. Compression is structural (concave scoring × a self-correcting,
mean-reverting economy — matches the crown-rotation finding from prior sims), not
developmental. This independently vindicates the percentile: the old floor gets
worse over time, the percentile is regime-stable.

## 2026-06-02 — the Villain question: balance by negativity, not a relative threshold?

The fix produces ~7 Legends and ~1 Villain because regard is warm-skewed (86%
warm; fixed +0.05 threshold vs field-median regard +0.18). I floated making the
warm/hostile split field-relative. Jeff pushed back — rather than a measurement
band-aid, fix the *input*: more negative interactions (the chat-sentiment signal,
AI-chosen sarcasm). Right instinct. The machinery already exists:
`relationship_events.py` has BAD_BEAT (+0.30 heat), BLUFFED_OFF (+0.20), etc.,
and `chat_intent.map_tone` is exactly the table-talk → heat bridge.

The blocker: **AI↔AI relationships don't evolve in the lobby sim on
`development`** (the renown sim showed `warm%` frozen at 86 across 1500 ticks).
That wiring is uncommitted on `release-candidate` (6 files: full_sim, lobby,
poker/memory/*). The base files matched, so I 3-way-applied the patch onto a
scratch dev worktree to test it, then reverted.

With the wiring live: confirmed it fires (+19 heat / 30 ticks). Then 1000 ticks,
`hand_sim_prob 0.7`:

| tick | warm% | hostile | rg_med | villains |
|---|---|---|---|---|
| 0 | 86 | 11 | 0.18 | 1 |
| 500 | 85 | 18 | 0.11 | 1 |
| 1000 | **73** | **32** | **0.09** | **2** |

Regard genuinely spreads hostile, and two **earned** Villains emerge
(`marie_antoinette`, `the_rock` — renowned figures turned hostile through
accumulated off-screen heat). So Jeff is right: no field-relative band-aid needed
in principle.

But it's **slow and weak** — the two villains sit at regard +0.01 / +0.03, right
on the line, not deeply reviled, after 1000 dense ticks. The diagnosis is in the
data: `rg_min` frozen at −0.38 and `rg_max` barely moving means the shift is
*diffuse*, not *concentrated*. The cause is the **denominator**: `regard` in
`build_inputs` is the mean over ~4,500 **global, historical, non-sandbox-scoped**
inbound edges, so each fresh heat event is diluted ~1/4500. The real unlock isn't
the threshold and isn't bigger heat deltas — it's **sandbox-scoping and
recency-weighting the regard mean** so current rivalries dominate. Plan written:
`docs/plans/RENOWN_REGARD_VILLAIN_BALANCE.md`.

Reverted the wiring patch; `development` carries only the committed percentile
change. All scratch DBs (5 GB each!) deleted.

### Lessons for future me
- A multiplicative threshold on a deliberately-concave score is always wrong;
  reach for a percentile/rank.
- Test fixtures can encode a world shape the live data doesn't have — when a
  threshold "works in tests, fails live", suspect the fixture's distribution.
- "Run it longer" is a real experiment, not a hedge: here it *inverted* the
  stated hypothesis twice (compression grows with ticks; villains are denominator-
  bound, not interaction-bound).
- The lobby sim doesn't feed every renown driver on `development` (no
  holdings_snapshots, no cash_pair_stats writer) — measure the driver, not just
  the output.
