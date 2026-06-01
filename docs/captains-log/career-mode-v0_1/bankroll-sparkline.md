---
purpose: Grounded narrative log of the career-lobby bankroll sparkline fix (data source + "stretched" look)
type: reference
created: 2026-05-27
last_updated: 2026-05-28
---

# Captain's log — the career bankroll sparkline (career-mode-v0_1 worktree)

Honest record of a small but instructive fix. Newest entries at the bottom.

---

## 2026-05-27 — "the sparkline looks stretched and isn't tracking actual bankroll"

**The report.** On the main career page the bankroll sparkline (CareerHero →
Sparkline) "looks stretched and looks like it's not tracking actual bankroll,
just +/-." Concrete tell: paid off a debt, the line didn't step down.

**The wrong turn (someone else's, but I almost shipped it).** The first
exploration pass came back with a confident root cause: an "inversion bug" in
the lobby's `bankroll_history` back-walk (`cash_routes.py` ~5040), claiming the
reverse-then-subtract loop produced scrambled, inverted values. It even had a
worked example "proving" the output was wrong. I traced the same example by hand
before touching anything — current 1000, sessions +100 then −50 → the code
yields `[950, 1050, 1000]`, which is exactly correct (before-oldest, after-
oldest, current). The agent had made an arithmetic slip (wrote 900 where 950 was
right) and then called the *correct* output "inverted." Lesson reinforced: a
crisp, worked-example diagnosis is still a guess until you run the arithmetic
yourself. If I'd trusted it I'd have "fixed" working code and left the real bug.

**The real bug is what the code's own comment admits.** `bankroll_history` was
*reconstructed* purely from finalised cash-session nets (`player_take_home −
total_buy_in`), anchored to the current balance and walked backwards. It
explicitly ignores every non-session move — seeds, vice, side-hustle, staking,
debt repayment. Worse: because the Sparkline normalizes to the series min/max, a
debt payoff (which lowers the *anchor*) shifts every point down by the same
amount → identical normalized shape → invisible. So the user was exactly right
on both counts: it tracks session +/-, not actual bankroll, and a payoff can't
show.

**The "stretched" half was the same root.** A player has a handful of finished
sessions → a handful of points → 1–2 long segments smeared across a
`width:100%` box (the SVG is `preserveAspectRatio="none"`). Sparse data, not a
component bug.

**The fix: use the real recorded series we already have.** `holdings_snapshots`
(schema v116) already records per-entity `chips` over time (~10 min/active
sandbox, incl. the human as `player:<id>`). That captures *every* chip move. So
the lobby now reads the player's `chips` series instead of reconstructing from
sessions:
- added `HoldingsSnapshotsRepository.chips_series_for_entity()` — a focused,
  index-hitting read (`idx_holdings_snap_entity`), because the lobby is polled
  hot and `series_since` scans every entity in the sandbox.
- lobby builds `bankroll_history` from that series (oldest→newest, 30-day
  window, downsampled to 40 pts), appends the *live* bankroll as a fresh tip so
  a just-happened move shows before the next tick — **skipped while seated**,
  since recorded `chips` fold in the in-play seat stack and the live off-table
  figure would dip the tip artificially.
- kept `last_session_delta` as a session figure (it labels the "last session"
  chip + tones the curve) — but simplified to "first finalised session,
  newest-first" instead of the whole back-walk.

Dense real data also fixes "stretched": 40 points read as a trend line, not a
smeared segment.

**Verified end-to-end against the live DB.** `player:guest_jeff` had 204
snapshots, 890→1773. The `/api/cash/lobby` endpoint now returns `bankroll: 1773`,
`last_session_delta: 190`, `bankroll_history` = 40 pts climbing 890→1773
(distinct [890, 1183, 1773]). Repo unit tests: 8 passed (5 existing + 3 new).
Frontend untouched — shapes unchanged (`number[]` / `number|null`).

**Left alone on purpose.** Didn't change the Sparkline's `preserveAspectRatio`
or min/max auto-scaling — both are intended design and read fine with dense
data on the mobile target. If it still looks stretched after a look, that's the
next lever.

---

## 2026-05-28 — "it looks the same" (the part I got wrong, twice)

**It looked the same. Because it kind of was.** User reloaded and reported no
change — first "I don't see it," then "the sparkline looks the exact same." My
first instinct was a deployment mismatch, and there *was* a real trap worth
recording: **three poker stacks running at once** — this worktree (`:5176` →
backend `:5002`, has the fix), the main checkout (`:5174` → `:5000`, doesn't),
and lookup-tables (`:5003`). Each has its own code *and its own DB*. The `.env`
even carried a stale `# Dev: http://localhost:5174` comment pointing at the
main checkout. I was sure that was it. It wasn't — user confirmed `:5176`.

**The actual reason it looked the same was humbling.** I had the user paste the
rendered `<svg>`. Decoding the y-coords: a long flat band then a cliff to the
endpoint — visually identical to the old broken one. But for a totally
different reason. The OLD line was flat-low (negative reconstruction junk) then
a jump. The NEW line was *real*: the bankroll genuinely sat at ~$900–$1.8k for
two days, then a recent session rocketed it to ~$6.2k. A 7× range under linear
min/max scaling squashes all the early history into the bottom ~15%. So the fix
worked — the data was now real — but the *visual* was dominated by one extreme
value, and I'd declared victory in the previous entry without ever looking at
how the real series actually *scaled*. Lesson: "the data is correct" is not
"the chart is readable." I verified the payload and skipped the picture.

**Second wrong turn, smaller: I reached for `chips`.** Liquid chips (what I
used) folds in the in-play seat stack, so a leveraged buy-in or an in-session
peak shows as a spike that isn't really "your bankroll." I'd picked the metric
that matched the hero number without thinking about its volatility.

**So I stopped guessing and showed the user real previews.** Generated faithful
block-sparklines (▁▂▃…█) from their actual numbers for four options — linear
liquid, log liquid, net worth, dedup — and asked. They picked **net worth**
(chips + receivable − outstanding): debt events read as dips, and it's the
most balanced-looking line. Accepted tradeoff: it no longer equals the big "$"
hero number (hero = liquid chips). That's a deliberate, user-made call.

**Then they asked for hover.** "Can you hover and see the amount at the time?"
That forced timestamps into the payload, which dovetailed nicely with a second
readability fix: return net-worth **change-points** `{t, value}` (collapse the
hundreds of identical ~10-min idle samples into the moments the value actually
moved), each carrying the time it was reached. Final series for this user:
`$890 → 1,183 → 1,773 → 1,803 → −803 (a loan!) → 1,751 → 3,613 → 6,219` — eight
points, the debt dip finally visible. Reworked `Sparkline.tsx` to scrub on
mouse/touch and float a value+time tooltip; new `Sparkline.css`.

**What I'd do differently:** look at the rendered shape, not just the JSON,
before saying "fixed" — especially for anything with auto-scaling. And don't
anchor on the first plausible explanation (wrong port) when the user has
already given you the evidence to rule it out.
