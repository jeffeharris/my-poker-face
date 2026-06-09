---
purpose: Ready-to-write outline for the Devlog post "The bot that learned to beat the calling station"
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Outline — The bot that learned to beat the calling station

- **Working title:** The bot that learned to beat the calling station
- **Track:** Devlog (#4 in the series, per `docs/blog/CONTENT_PLAN.md`)
- **Target reader:** Builders — HN / indie hackers / AI engineers. People who've
  fought their own "my eval says it's great but is it?" problem. Poker is the
  vehicle; the real subject is *how you measure a thing when your test set is too
  weak to tell good from lucky.*

## One-line hook (grounded)

> A tight, disciplined poker bot lost **126 bb/100** to a bot that plays 95% of its
> hands and calls everything. That number is backwards from real poker — and that
> backwardness was the whole diagnosis.

## The narrative spine (section beats, in order)

1. **The blind spot.** The opponent pool was all fish — calling stations, always-call
   rule bots. Against fish, "actually skilled" and "merely fish-hunting" produce the
   *same* winning bb/100, so every prior "better bot" claim was unfalsifiable. You
   cannot prove a bot is robust until you have an opponent that punishes "play 95% and
   call down." (Source: `keystone-regplus.md` "The setup"; the project plan
   `BUILD_A_BETTER_BOT.md` thesis that every "smarter" approach had secretly only
   measured fish-hunting.)

2. **Why this mattered to the founder, not just the eval.** This wasn't an academic
   metric problem. The stated goal was an AI that's *hard for a human* — and the
   tournament data kept showing the wrong bots winning: aggressive, spewy "maniac"
   archetypes farming the passive ones. The honest worry, in the founder's own words,
   was whether the maniacs *should* keep winning. (See pull-quotes.)

3. **Build the opponent first.** The keystone decision: before building any "smart"
   adaptive bot, build the *competent opponent* that would expose a weak one. Start
   by **measuring** the existing tight "Reg" rather than trusting its label — and it
   didn't just fail to beat the station, it got demolished: **−88 bb/100 heads-up**,
   **−126 bb/100 6-max** vs the calling-station clone. A TAG should destroy a station;
   this was inverted. (Source: `keystone-regplus.md`, "The baseline was worse than
   advertised.")

4. **The three leaks — and the counterintuitive fix.** The tight bot under-extracted
   (value-bet 0.66 pot when a station calls overbets), paid off the station's big bets,
   and nitted out preflop. The tempting fix is to *balance* the bot — tighten it, make
   it accurate. That was proven wrong five times in earlier dead-ends: **balance
   under-exploits a leaky pool.** The actual fix was an asymmetry: keep the maniac-grade
   extraction (overbet when checked to — the station pays anyway) and add *one* thing —
   a fold button (fold everything but the nuts to a polarized big bet; never bluff-barrel
   a caller). (Source: `keystone-regplus.md`, "The fix.")

5. **The asymmetry, in one sentence.** *When the bot overbets, the station pays; when
   the station overbets, the bot folds.* That one-way street is how a competent player
   beats a station — and it's the load-bearing idea of the whole post.

6. **The result.** `RegPlus` flipped every cell positive: **+102 bb/100 HU vs the
   station** (was −88), **+38 6-max** (was −126). Inverse check: the calling-station bot
   at a table of five RegPlus copies goes from +378 to **−199 bb/100**. A one-knob
   hardening sweep (overbet 1.1 → 1.3) was the only free win; every other deviation
   regressed. (Source: `keystone-regplus.md`, "The result" + "Hardening pass.")

7. **The honest boundary (the part most posts would cut).** Then the founder asked the
   real question: not "does it beat bots?" but "is it hard for a *human*?" Four
   purpose-built attacker bots were written to exploit RegPlus's one residual leak
   (it's face-up: bet size is a 1:1 tell, it never bluffs) — and **all four lost.** The
   honest conclusion: a static rule bot fundamentally *cannot measure* human-difficulty,
   because the exploit requires range-reading selectivity a fixed rule can't express —
   it collapses to "bet a lot," which RegPlus's calling range punishes. So RegPlus ships
   as the right bot for the fish-heavy field it actually faces, while "hard for a human"
   stays an open, honestly-flagged problem requiring a human in the loop or a learning
   opponent. (Source: `keystone-regplus.md`, "I tried to break RegPlus," and the
   "Recalibration" close.) This is the bridge to post #6 (A3).

> Optional sidebar / contrast: the *opposite* result from earlier — **CaseBot**, a
> hand-built adaptive rule bot, beats a small-LLM player (~47-60% win rate vs GPT-5-nano
> / Groq-8B where 25% is the baseline) but drops to baseline 25% against full GPT-5. Same
> lesson from the other direction: a deterministic strategy exploits weak opponents and
> the result tells you more about the *opponent's* weakness than the bot's strength.
> (Source: `CASEBOT_EXPERIMENT_REPORT.md`.) Use only if the post needs a second data
> point; the RegPlus arc stands alone.

## Evidence & assets

**Hard numbers to cite (all from the cited docs — verify the exact figure against the
source before publishing):**
- Reg vs CaseBotV2 (calling-station clone): **−88 bb/100 HU**, **−126 bb/100 6-max**;
  CaseBotV2 vs 5×Reg = **+378 bb/100**. (`keystone-regplus.md`)
- RegPlus vs same: **+102 HU**, **+38 6-max**; CaseBotV2 vs 5×RegPlus = **−199 bb/100**.
  (`keystone-regplus.md`)
- Hardening sweep: overbet 1.1 → 1.3 moved worst gauntlet cell **+0.0 → +8.2** (positive
  everywhere); every other knob regressed. (`keystone-regplus.md`)
- Four attacker bots built to exploit RegPlus, **all lost** (Exploiter −149 HU / −420
  6-max vs RegPlus). (`keystone-regplus.md`)
- Supporting tournament data (the maniac-farms-fish backdrop): in a 6-max mix of 5 rule
  bots, **Maniac nets +1235 bb/100** — but the per-opponent decomposition shows it's
  *farming passive bots* (CallStation +3,379 BB) while *getting blown out* by ManiacBot
  (−3,985 BB). The headline only works because the pool is net-passive. This is the eval
  blind spot, quantified. (`TIERED_VS_RULE_BOTS_REPORT.md`)
- Method credibility note: the gate uses **common random numbers (CRN)** — replay each
  dealt hand both ways on the identical deck so card variance cancels and ~97% of hands
  are exact ties. The variance lives only in the few hands where decisions differed.
  (`eval-harness-and-exploitation.md`) Good "we did this rigorously, not vibes" beat.

**Screenshots / files:**
- 3-layer architecture diagram: build from the table in
  `docs/technical/TIERED_BOT_ARCHITECTURE.md` (Strategic Core → Personality Modifier →
  Expression Layer). No existing image; needs to be drawn.
- `react/react/src/assets/screenshots/range-explorer.png` and
  `.../preflop-leaks.png` — the admin Range Explorer / preflop-leak views. Useful to
  *show* "we can see a bot's VPIP / leaks," even if the post is about the eval, not the
  UI. Optional.
- A simple bb/100 bar chart (Reg vs RegPlus across the cells) would carry the post —
  needs to be made from the numbers above.

**Commits / references:**
- Captain's logs are the spine: `docs/captains-log/lookup-tables/keystone-regplus.md`
  and `eval-harness-and-exploitation.md`.
- The overbet-sweep "free win" and the hardening pass are described in keystone-regplus
  but the *commit SHA* for the RegPlus ship isn't in the doc — **founder to supply** if
  a code link is wanted.

## Candidate pull-quotes (verbatim)

Real human prompts from the build transcripts (`.../lookup-tables/*.jsonl`), lightly
trimmed only at the ends — wording is exact:

1. > "so tieredbot adaptations just dont work? i've been spinning on this issue of
   > building a competitive and adaptable bot, i dont know why its not possible. i can't
   > build GTO so i want adaptation and exploitation"

   — The founder naming the actual problem. This is the emotional center of the post.

2. > "i don't thin it makes sense that they keep winning, so thats ultimately what i want
   > to do is to get it more [mitigated]"

   — On the maniacs farming the fish. (Verify exact tail in transcript before quoting;
   the prompt trails off — quote only the clean clause.)

3. > "better is B and C is required for that"

   — The founder's terse correction that robustness (B) requires adaptation (C) — a
   thesis the keystone work then *refuted* (RegPlus is a single static bot that is both
   robust and a fish-extractor). A nice "we were wrong, here's the data" beat.

Real commit-subject-style lines from the doc itself (not git, but verbatim from
`keystone-regplus.md`) usable as section epigraphs:
- > "extract like CaseBotV2, but add a fold button"
- > "when RegPlus overbets, the station pays; when the station overbets, RegPlus folds."

## Draft intro paragraph (in the post's voice)

> A tight, disciplined poker bot — the kind that's supposed to crush loose players — sat
> down against a bot that plays 95% of its hands and calls almost everything. It lost
> 126 big blinds per 100 hands. In real poker that result is impossible: a good tight
> player is *supposed* to feast on a calling station. So either our bot wasn't good, or
> our test couldn't tell. Both were true — and the second one was the actual problem.
> Our entire pool of practice opponents was fish, and against fish, genuine skill and
> dumb luck win the same amount. You can't prove a bot is good against opponents who
> can't punish a bad one. So before we built a "smarter" bot, we had to build a meaner
> one: a competent opponent whose only job was to expose a weak one. This is the story
> of that bot, the one counterintuitive fix that made it work, and the honest line where
> our measurements ran out.

## Open gaps (need the founder or more reporting)

- **Commit SHA / PR** for the RegPlus ship (and whether RegPlus actually got promoted to
  a production bot type, or stayed an eval-only archetype — keystone doc lists this as an
  open "ship it vs build the human-in-a-bot" fork). Founder to confirm current state.
- **Exact tail of pull-quote #2** — the prompt trails off in the transcript; confirm the
  clean clause to quote.
- **Did the "hard for a human" thread ever get a human in the loop?** The keystone close
  says it needs either the founder playing a live session or a learning/CFR opponent.
  Whether that happened is the natural sequel (post #6, A3) — confirm status so the
  cross-link is accurate.
- **CaseBot sidebar** — include or cut? It's a clean second data point but risks
  diluting the single-arc clarity. Founder's call.
- **Numbers are bot-vs-bot sims**, not live human data. The post should say so plainly;
  confirm the founder is comfortable leading with sim numbers (he has been throughout
  the logs, but flag it).

## Cross-links (series continuity)

- **Pairs with B1 — "Poker where the opponents are alive"** (Inside the Table): same
  3-layer system told through the *personality* lens instead of the eval lens.
- **Sets up #6 / A3 — "Making an AI hard to read — with no human to test against":** this
  post ends exactly where A3 begins (the face-up-tell / human-difficulty problem). A3 is
  the direct sequel; end this post pointing at it.
- **Echoes #2 / A1 — "Four wrong turns on launch day":** same recurring method —
  *when you're sure, that's exactly when to go measure.* The eval blind spot here is the
  same shape as the four launch-day misdiagnoses. Worth a one-line nod.
- **Tonal anchor:** the project's `feedback_confidence_calibration` / "verify the
  premise" habit — this post is the cleanest worked example of it.
