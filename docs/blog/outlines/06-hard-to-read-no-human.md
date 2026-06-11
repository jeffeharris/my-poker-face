---
purpose: Ready-to-write outline for the Devlog post on making the poker bot hard to read with no human test data
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Outline 06 — Making an AI hard to read, with no human to test against

- **Track:** Devlog (post #6 in the publish order; the self-contained follow-up to #4, "The bot that learned to beat the calling station")
- **Target reader:** builders — HN / indie hackers / AI-eng. People who'll appreciate "I had an unmeasurable objective and found a way to measure it." Light poker knowledge assumed but every poker term gets a one-line gloss.
- **Working title (candidates):**
  - "Making an AI hard to read, with no human to test against" (plan default)
  - "How do you make a bot unexploitable when you have no one to exploit it?"
  - "The river was the only leak: measuring readability without a human"

## One-line hook (grounded)

> "Hard for a human to beat" sounds impossible to measure when you have zero human hands and every opponent in your simulator is a static bot — until you realize poker theory already tells you exactly what a thinking opponent punishes, and all of it is visible in the bot's own bets.

## Section beats (the narrative spine, in order)

1. **The stuck objective.** The goal was set in stone and kept getting lost: build a bot that's *hard for a competent human*, not one that farms our own fish. The honest friction — the AI pair kept drifting toward "make it extract more value," and the founder had to keep dragging it back to "harder to read." (This is the human-steering thread; quote the frustration verbatim, see pull-quotes.) The blocker the prior session hit: hard-for-humans felt unmeasurable — no human data, all opponents static.

2. **The reframe that dissolved it.** You don't need humans. *Theory* tells you what a thinking opponent exploits: a bet size that leaks hand strength, a size you never bluff, a spot where you over-fold. Every one of those is a property of the bot's *own* decisions — measurable with no opponent at all. (Be honest: the post should note this direction had been *named and parked* in a prior session; the contribution was re-deriving why and building the instruments, not inventing the idea.)

3. **The tell-map instrument.** Build a readability audit that needs neither a human nor an oracle: for each (street, bet-size), tabulate the hand-class composition of the bot's own betting range, and compare its bluff share to the GTO target `s/(1+2s)` (the share of bluffs that makes a given bet size unexploitable). A bonus the post can mention: point the same tool at a human's hand history and it finds *their* tells — which is exactly what the coaching feature needs.

4. **The finding: there is exactly one leak, and it's the river.** The audit overturned the prior session's assumption. In aggregate the bot's range is roughly balanced; the turn was *already* balanced — which is why an earlier "fix the turn" attempt recovered only ~15%, it was de-facing-up a street that wasn't leaking. The real leak: river pot-plus bets were 90–100% value, ~0% bluff (gap of −25 to −44 points vs target). Reproduced both heads-up and 6-max, vs a station and vs a reg. The whole aggressive game had **one** readability leak: the river bet.

5. **Fixing it without spewing — why the gate is the whole point.** The naive fix (just bluff more on the river) loses money against the calling stations that make up the real player pool: measured +1.90 bb/100 vs a reader but −7.18 vs a caller. So the fix only fires vs a *detected* over-folder (fold-to-big-bet ≥ 0.6). Gated, it keeps the +1.90 vs a reader and costs +0.00 vs a caller. The 0.6 threshold isn't arbitrary — it's the break-even fold rate for a 1.5×-pot bluff (1.5/2.5), so the bluff only fires when it's already +EV on fold equity alone. Honest caveat: at 0.6 the feature is *dormant against the entire current AI field* — it only wakes up for a genuinely over-folding human, which the data can't supply.

6. **A wrong turn, kept in.** The residual gap looked like a "supply" problem — too few river bluffs available — so the obvious fix was to barrel more air on the turn so more reaches the river. Built it, measured it: **no-op.** River bluff share was 25% with it on and off. The premise was wrong: give-up air *already reaches* a checked-to river; air dies by folding to a bet, not by checking. The ~31% bluff cap is structural, not a turn problem. Measure-first paid for itself — it stopped an EV-risky change that does nothing.

7. **Validating the safety story on 57,347 real hands.** The gate's entire safety claim is "fish score low, gate stays off, no spew." Reconstructed the fold-to-big-bet stat across 57,347 real casino hands: **0 of 58 mature opponents** trip the 0.6 gate; stations sit at 0.00–0.08, the whole population maxes at 0.56. Spew risk vs the real fish: proven zero.

8. **Building the missing instrument (optional deeper section — the strongest result).** A fixed "oracle" opponent can show a bluff getting through, but can never show value finally getting *paid* because the reader is forced to call. So we built an *adaptive* reader: a competent reg that watches the bot's revealed overbet hands, estimates its bluff frequency, and best-responds. It genuinely learned and discriminated — 0.02 observed bluff freq against a face-up bot vs 0.14 against the balanced one — and balancing helped even against this thinking, adapting opponent by **+2.2 bb/100, identical across all three seeds.** Three independent instruments (tell-map, oracle, adaptive reader) reached the same ceiling by different mechanisms.

9. **Where it landed / the one honest gap.** One readability leak, found and fixed to its structural maximum, gated so it costs nothing vs the fish and only activates against opponents who'd punish it — all derived from theory, validated on real data. The single thing still beyond reach without live humans: the exact magnitude of the benefit against a real over-folding human. That's now a precisely isolated open question, not a fog. (Good closing: this is *why* we build in the open — the last number waits on real players.)

## Evidence & assets

**Hard numbers to cite (all from `captains-log/lookup-tables/river-readability-and-the-adaptive-reader.md`):**
- River pot-plus bets were 90–100% value, ~0% bluff; bluff-vs-target gap of **−25 to −44** points.
- The earlier turn-reroute recovered only **~15%** — wrong street.
- Naive river bluff: **+1.90 bb/100 vs a reader, −7.18 bb/100 vs a caller** (the exact fish/human tension).
- Gated: **+1.90 vs reader, +0.00 vs caller.** Gap moved −28 → −7.
- Gate threshold **0.6 = 1.5/2.5**, the break-even fold rate for a 1.5×-pot bluff (self-calibrating, not arbitrary).
- Structural bluff-supply cap **~31%** vs a ~37% target — max injection is correct, no over-bluff risk.
- Safety validation: **0 of 58 mature opponents trip the gate across 57,347 real casino hands**; stations 0.00–0.08, population max 0.56.
- Adaptive reader: learned **0.02 vs 0.14** bluff freq; balancing worth **+2.2 bb/100, identical across 3 seeds** (+2.3 / +2.3 / +2.2).
- GTO bluff target formula: `s / (1 + 2s)`.

**Supporting technical references (for the mechanics / a sidebar):**
- `technical/POSTFLOP_OVERRIDES.md` §4 — `overbet_context` layer: `_promote_check_to_bet` is the river-bluff-supply mechanism; the `river_bluff_min_ftbb` default 0.6 gate is code-verified there. Good for a "where this lives in the pipeline" note.
- `technical/TIEREDBOT_DECISION_QUALITY.md` — the broader postflop quality pipeline (hand classifier, archetype classifier, bet-size buckets). Use sparingly; this post is about *one* leak, not the whole pipeline.

**Screenshots / images available (verify fit before using):**
- `react/react/src/assets/screenshots/preflop-leaks.png` and `.images/preflop-leaks.png` — a leak-finding visual; relevant to the tell-map idea but it's the *coach's* preflop view, not the river tell-map. Caption honestly or rebuild a tell-map chart.
- `react/react/src/assets/screenshots/range-explorer.png` / `.images/poker_range_explorer.png` — range-grid visual; supports "we can read a range's composition." Closest existing asset to the tell-map concept.
- **Best asset would be a purpose-built chart** the post doesn't have yet: the per-(street, bet-size) bluff-vs-value composition bars with the `s/(1+2s)` target line. Flagged as an open gap below.

**Commits to reference:** the captain's log doesn't cite the specific SHAs for this work. The post can reference the dormant-mechanism code (`overbet_context.py::_promote_check_to_bet`) by file rather than SHA. (Open gap — founder or `git log` can supply the exact commits if wanted.)

## Candidate pull-quotes (verbatim)

1. **The founder overriding the AI's drift (real chat, session 39cf1325, 2026-06-01):**
   > "i do not want a value machine - have we been talking at all? are you hearing me? sorry i'm frustrated. i used the '/goal' thing and still you focused on value, you dont seem to be taking me serious i think the best thing to do is to write an opinion agnostic handoff that lets a new context take a fresh look at it without bias from you or i."

   This is the emotional center: a real wrong turn where the human had to stop the AI and reset. Use it to anchor beat 1.

2. **The goal, stated plainly by the founder (real chat, same session):**
   > "the goal was not wxploting the bots, which arent even iur fish!!! the goal was always to build a better bot that is harder for humans."

   (Typos verbatim — build-in-public honesty; the post can quote as-is or `[sic]`-clean, founder's call.)

3. **The reframe, from the log (paraphrasable or quotable):**
   > "we don't need humans; theory tells us what a thinking opponent exploits."

   From the captain's log narration, not a chat line — attribute it to the log/the reframe, not to a person.

## Draft intro paragraph (post voice)

> I wanted a poker bot that was hard for a *human* to beat. Not one that farmed the weak AI players we use as fish — a bot that a thinking opponent couldn't read. The problem: I had no human hands to test against, and every opponent in my simulator was a static script. "Hard for humans" looked like the kind of goal you can argue about forever and never measure. The thing that unstuck it wasn't more data. It was remembering that poker theory already names what a thinking opponent punishes — a bet size that leaks how strong you are, a size you never bluff, a spot where you fold too much — and every one of those is sitting right there in the bot's own decisions. No human required to find them. (The honest part of this story is that it took a frustrated "are you hearing me?" to my AI pair to stop chasing the wrong target first.)

## Open gaps (what's missing / needs the founder)

- **The hero asset doesn't exist yet.** The post wants the per-(street, bet-size) bluff-vs-value composition chart with the `s/(1+2s)` target line. The `measure_passivity --tell-map` tool produces this data; someone needs to render it as a figure. Existing screenshots (preflop-leaks, range-explorer) are *adjacent*, not the thing.
- **Exact commit SHAs** for the river-bluff gate, the tell-map tool, and the adaptive reader aren't in the log. `git log` on the lookup-tables work (late May–June 1) can supply them if the post wants precise links.
- **How much poker theory to assume.** The post leans on bb/100, GTO bluff fractions, MDF, fold-to-big-bet. Founder should decide the gloss level for a builder (not poker) audience — recommend a one-line inline definition per term, no sidebar.
- **The depth fork:** beats 1–7 + 9 make a tight, complete post. Beat 8 (the adaptive reader) is the most impressive result but adds ~30% length and a harder concept. Founder's call whether this is one post or whether the adaptive-reader/aggressor instruments become their own follow-up.
- **The dual instruments** (adaptive bluff-raiser, stab-defense) are in the same log and are genuinely strong ("the bot's sticky calling is what makes it robust vs aggression"). They're out of scope for *this* post's "readability" frame but could be the post-after-this. Confirm with founder before cutting vs splitting.

## Cross-links (within the series)

- **← #4 / A2 "The bot that learned to beat the calling station"** — this post is the explicit, self-contained follow-up. #4 establishes the eval blind spot (fish-hunting and real skill look identical) and the asymmetry fix; #6 picks up "now make it *unreadable*, not just profitable." Open with a one-line callback.
- **→ #3 / B2 "Your opponents remember you"** — the player-facing twin of the *reads*. The tell-map and fold-to-big-bet stats this post computes are the same machinery that lets AIs scout a human across sessions. Cross-link: "the reads are real — here's how they're computed."
- **↔ #11 / B6 "A coach that grades you against the charts"** — the tell-map's bonus ("point it at a human's hand history and it finds *their* tells") is literally the coaching feature. Natural forward-link.
- **Series spine:** part of the reconstructed origin arc — the late-May/June 2026 lookup-tables/bot-eval saga, where the founder is visibly steering and sometimes overriding the AI pair. Fits the blog's "honesty is the brand" / "wrong turns kept in" thesis.
