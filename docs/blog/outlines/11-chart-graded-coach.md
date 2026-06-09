---
purpose: Ready-to-write outline for the "chart-graded coach" blog post — how the coach went from a per-hand oracle that was "way off" to grading your real preflop play against the bot's own solver charts
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Outline — A coach that grades you against the charts

- **Working title:** A coach that grades you against the charts
- **Track:** Inside the Table
- **Target reader:** A poker-curious player or build-in-public follower who has seen
  AI "coach" features that confidently say wrong things, and wants to know what it
  takes to make in-game advice that's actually grounded.

## One-line hook

The first version of the coach would tell you to "check the river with the nuts so
you don't risk too much" — so we stopped asking an LLM to be smart, and started
grading your real hands against the same solver charts our bots play from.

## Why this hook (grounded)

Both halves are real and verbatim-sourced:
- The bad-advice line is a near-verbatim founder complaint from the build session
  (see pull-quote 1) — the post should frame it as a real symptom, not a strawman.
- "Grade against the bot's own charts" is literally what shipped: the chart grader
  points at the TieredBot's `vs_open`/`vs_3bet`/`vs_4bet` solver charts (2,535 hands
  each), per `chart-graded-coach.md`.

## Section beats (the narrative spine, in order)

1. **The coach that was confidently wrong.** Open on the real failure: with the
   conversational coach running on the cheap in-game model tier, it hallucinated hand
   facts — narrating a "set of fives" on a hand that ended preflop, or telling you to
   "check the river with the nuts." The founder's read in-session: "the coach right
   now is often way off on things, so we need to be careful with it." This is the
   honest starting point — a feature that was more noise than signal.

2. **Two cheap fixes that mattered before any redesign.** Move the coach off the
   nano/8B tier onto the Assistant tier (the model that can actually reason about a
   hand), and hard-forbid the prompt from inventing board-made hands the cards don't
   support (`COACH_SYSTEM.md` §5). Plus prefetch: because the bots are deterministic,
   the system can compute all the AIs' actions in a quick batch the moment you act,
   then fire the coach LLM call *before* it's your turn again — hiding the latency.
   These didn't make the coach smart; they stopped it from being obviously wrong and
   slow.

3. **The drill that wasn't a drill (a wrong turn, kept honest).** The first
   "training" build let you replay a single hand — but it just dropped you back into
   that hand and let you keep playing it, with the coach highlighting the
   recommended action. The founder's own verdict: there was no thought to it, "i had
   no thougth into it," and hand-curated drills "cant keep it interesting" — not
   scalable. Decision: cut drills as built. This is the section that earns trust:
   we shipped something, looked at it, and called it weak.

4. **The question that flipped the design.** The reframe came from a sideways
   question — "what about grading the *other* preflop spots (facing a raise, 3-bets),
   and would building those charts help the TieredBot too?" The answer inverted it:
   the bot *already has* full solver charts. It's the source of good charts, not a
   beneficiary. The real gap was that the coach was grading you against a crude
   opening-only chart instead of the bot's real ones. The build became wiring, not
   authoring: point the grader at the bot's charts.

5. **The spike that caught two lies.** Before committing, a reconstruction spike ran
   on 85 real seeded hands and caught two bugs that would have shipped a broken
   feature: (a) the proposed 100bb reference was wrong — the real hands are short
   (58% ≤15bb, *zero* at 100bb), so grading short stacks against a deep chart would
   invent false leaks on nearly every hand; fix was depth-aware chart selection
   mirroring the bot's own logic. (b) Grading exact hands at this volume is a
   "sample-fragmentation lie" — 36 decisions split into 36 groups of n=1, none past
   the sample gate, so "0 leaks" read as discipline when it was really *no data*.
   Pivoting to the `(scenario, position)` aggregate surfaced a real leak the old
   framing couldn't see: open-limping from the SB 22% of the time. Lesson:
   a sensitive grader against the wrong reference still lies.

6. **What the player actually sees now.** Two surfaces. In-game, a confirmed-only,
   once-per-spot Socratic nudge that fires when you *enter* a recurring leak spot —
   it changed from "you overplay K2o" to "you're UTG and A6s is outside a standard
   UTG opening range — consider whether this hand plays well from early position."
   Out of game, a "Your Preflop Game" review: VPIP-by-position bars graded against a
   standard opening frequency, the specific spots where your play diverges from the
   solver, and a one-tap Drill on that exact spot. Note the honest UX touch: the bars
   say "context, not a grade" and the number includes calls and blind defense.

7. **Measuring whether any of it helped (and admitting the limits).** Every served
   tip and nudge is logged; `get_tip_effectiveness` joins the nudge to the decision
   that followed, so we can ask "did you follow the solver line after the leak nudge,
   vs baseline." The honest caveat the post must keep: this is *compliance*
   measurement, not a clean nudged-vs-not causal cut — and at the time of the log,
   the effectiveness numbers were empty because no coached game had been played all
   the way through the stack yet. The driving standard, in the founder's words: move
   it forward "a way that is not a gimmick and makes the coach useful vs noise."

## Evidence & assets

**Hard numbers to cite (all from `chart-graded-coach.md` / `COACH_SYSTEM.md`):**
- Bot solver charts: **2,535 hands each, 15 matchups**, mixed frequencies, multiple
  depths (`vs_open`/`vs_3bet`/`vs_4bet`). This is the reference the coach now grades
  against.
- Spike sample: **85 real seeded hands**; **58% ≤15bb, zero at 100bb** (why
  depth-aware selection was required).
- Sample-fragmentation: **36 gradeable decisions → 36 distinct n=1 groups** under
  exact-hand grouping; SB open-limp leak surfaced at **22%** under the
  `(scenario, position)` aggregate.
- Live preflop-leaks screenshot shows **181 preflop decisions analyzed** and VPIP
  bars (Early 15.2% / Middle 25% / Late 37.5% / Blinds 33.9%) — real captured figures
  from the dev DB, usable as a concrete example.
- Coach progression scaffolding (context, optional sidebar): **4 gates, 11 skills**;
  conversational coach runs on the **Assistant tier**, tagged `CallType.COACHING`.

**Screenshots / files to include:**
- `react/react/src/assets/screenshots/coach-tip.png` — the in-game nudge in context
  ("You're UTG and A6s is outside a standard UTG opening range… 23% equity").
  HERO IMAGE — it shows the grounded, Socratic voice the whole post is about.
- `react/react/src/assets/screenshots/preflop-leaks.png` — "Your Preflop Game"
  review: VPIP-by-position bars + "Where your play diverges from the solver" rows
  with Drill buttons (open-limp from SB 33%, facing a raise in HJ).
- `react/react/src/assets/screenshots/range-explorer.png` — admin Range Explorer; the
  founder explicitly pulled this into the coach idea ("we a;sp have the … RangeExplorer
  and that could be really useful to the coach"). Optional, for the "where the charts
  come from" beat.
- `.images/in-game-coach.png` / `.images/in-game-coach-2.png` — desktop coach context
  if a non-mobile shot is wanted.

**Commits / docs to reference:**
- Captain's log: `docs/captains-log/training-room/chart-graded-coach.md` (the primary
  narrative source — find → nudge → drill → measure).
- Spec: `docs/plans/COACH_CHART_LEAKS.md` (referenced by the log; not yet read —
  confirm it exists before linking).
- Architecture: `docs/technical/COACH_SYSTEM.md`, `docs/technical/COACH_PROGRESSION_ARCHITECTURE.md`.
- Migration markers from the log: capture-forward at **schema v123**, measurement
  (`coach_tips` + `get_tip_effectiveness`) at **v124**. Flag: verify these version
  numbers against current code before publishing — schema has since been squashed.

## Candidate pull-quotes (verbatim, from the 2026-06-01 training-room session)

1. > "so i feel like teh coach mostly gets the situation wrong and says things like
   > 'check on the river with the nuts so you dont risk too much' like it makes 0
   > sense." — the failure that motivated the whole rebuild. (typos preserved; the
   > post can quote it raw for authenticity or clean lightly — founder's call.)

2. > "the 'drills' as we've built it should be dropped. dropping people into
   > situations is different than a drill." — the wrong-turn admission.

3. > "what opportunities do we have to move this forward in a meaningful way? a way
   > that is not a gimmick and makes the coach useful vs noise?" — the standard the
   > feature was held to. (lightly de-typo'd from "thiat".)

4. (alt) > "oh, we shouldnt be using nano for this though, we need to use an assistant
   > level endpoint for the coach." — the cheap fix that mattered.

## Draft intro paragraph (post voice)

The first time I let the coach watch me play, it told me to check the river with the
nuts so I wouldn't risk too much. That's not advice — it's noise wearing a graduation
cap. The honest problem was that I'd wired an AI coach the easy way: hand it the board,
ask it to be smart, and hope. It hallucinated made-up hands, it was slow, and the one
"training" mode I'd built just dropped you back into a hand and highlighted the button
to press. So I tore most of it out and asked a narrower question: instead of asking a
model to reason about poker from scratch, what if the coach graded your *real* hands
against the exact solver charts our bots already play from? This is the story of that
rebuild — including the two bugs a spike caught that would have made the coach
confidently lie in a brand-new way.

## Open gaps (need the founder or more reporting)

- **Effectiveness numbers are likely still empty / thin.** The log says no coached
  game had been played through the full stack yet. If the post wants a real
  "did it help" result, the founder needs to play coached sessions and pull
  `/tip-effectiveness`. Otherwise the post must stay honest that this is
  instrumentation-ready, not yet validated.
- **Schema version drift.** Log cites v123 (capture-forward) and v124 (measurement),
  but MEMORY notes the schema chain was later squashed to a generated baseline. Don't
  print version numbers without re-verifying against current `schema_manager.py`.
- **`COACH_CHART_LEAKS.md` not confirmed.** Referenced as the spec but not read for
  this outline — verify it exists / is current before linking.
- **Is the chart-graded coach live in production?** Confirm whether this is shipped on
  mypokerfacegame.com or dev-only at time of writing. The post's tense depends on it.
- **The 181-decisions / VPIP figures in the screenshot** are from a dev DB the founder
  said "won't be treated as real." Fine as an illustrative example; don't present them
  as a production stat.

## Cross-links (other posts in the series)

- **04 — Beat the calling station** (`04-beat-the-calling-station.md`): the same bot
  intelligence that exploits you is the source of the charts that now grade you. Strong
  thematic pair — the bot's solver knowledge points both inward (its own play) and
  outward (your coaching).
- **06 — Hard to read, no human** (`06-hard-to-read-no-human.md`): the coach's
  "sizing tells" surface (how readable your own bet sizing is) is the self-coaching
  twin of opponent readability — natural follow-on.
- **01 — The opponents are alive**: the celebrity personas are the opponents you're
  being coached against; this post is the "and now the table teaches you" turn.
- Forward link: if a future post covers the skill-progression gates (4 gates / 11
  skills), this post is the preflop-grading foundation it builds on.
