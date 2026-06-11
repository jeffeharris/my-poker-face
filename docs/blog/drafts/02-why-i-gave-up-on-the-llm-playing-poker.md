---
purpose: Full draft of the flagship Devlog post on the ChaosBot to TieredBot bot-journey / change-of-mind story
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

> **Draft status:** revised draft (em-dashes removed; antithesis trimmed). Grounded
> against `FOUNDER_INTERVIEW.md`, `docs/analysis/*`, and
> `docs/technical/TIERED_BOT_ARCHITECTURE.md`. Inline `[VERIFY: …]` markers flag the
> few claims to confirm before publishing. Track: **Devlog** (career +
> creator-community).

# Why I gave up on the LLM playing poker

The first version of my poker AI was a single prompt. One big block of text: the
player's cards, the board, the pot, who'd done what, a description of the character it
was supposed to be, and instructions to respond as JSON with its action, a line of
table talk, and an updated read on its own mood. I called it ChaosBot, though not at
first. At first I called it the whole product.

And as a demo, it was genuinely great. You'd sit down against Sherlock Holmes, or
Blackbeard, or Cleopatra, and they'd banter, fold, shove, and needle you, and it felt
alive. That feeling is the entire reason this project exists. I built the poker engine
myself, in plain Python, years ago, and the part that hooked me was watching a language
model put a personality on top of it.

The problem is that poker is measurable, and I couldn't stop myself from measuring.

## The demo that didn't survive contact with the data

Poker has a property most "does the AI seem smart?" questions don't: you can put a
number on it. Win rate in big blinds per 100 hands. Preflop ranges. How often a player
voluntarily puts money in the pot, what poker people call VPIP. So I built a whole
experiments platform around the game, something that could run hundreds of thousands of
LLM queries with small variations and tell me whether a tweak made the bot play better
or worse. I even built an LLM assistant to design the experiments, run them, and crunch
the results. It was, like the game itself, a cool system.

It was also telling me the bots were terrible.

Two things broke at once. The first was that my measurement was suspect: the same
equity calculation I used to *make* the bot's decisions was the one I used to *grade*
them. That's a closed loop, and you can't trust a test that grades the work with the
same ruler that did the work. The second problem was worse, and it showed up the moment
I stopped looking at win rate and started looking at *ranges*, meaning which hands the
bots actually chose to play.

They played everything. Not loosely, but with no discrimination at all. Every player I
tested sat at **85 to 100% VPIP**, so pocket aces and seven-deuce got played at roughly
the same rate. (I still remember pulling the ranges and finding the worst starting hands
showing up about as often as the best ones, which is backwards from anything resembling
poker.) With no range guidance at all, the gap between my tightest and loosest
characters was **8.4 percentage points** of VPIP, which is to say no real gap. A "tight"
character and a "maniac" character were, statistically, the same player wearing
different hats.

> The LLM read "play aggressively" and played every hand.
> (from my own experiment notes, trying to figure out why a tight-aggressive character
> had a 100% VPIP)

And it didn't transfer. A prompt I'd tuned until it behaved on one model fell apart on
the next. Swap GPT for Gemini and the same instructions produced wildly different play;
one of my range-guidance runs went from an 8-point VPIP spread to a 48-point one, in the
wrong shape, just by changing the model underneath. That killed a dream I'd been quietly
holding: pit the models against each other and find out which one is the better poker
strategist. There was no stable strategist to find. The models were emotional and
dramatic and great at *being a character*, but as strategists they were, no offense,
awful.

In hindsight the ask was unfair. I was handing one model a full personality to perform,
a character voice to stay in, multi-street poker strategy to plan, and the job of
noticing how everyone else at the table was playing, all at once, every decision. It did
the first two beautifully and the last two not at all.

## HybridBot: I'll just narrow its choices

So I tried to make the job smaller. If the LLM is bad at open-ended strategy, don't give
it an open-ended decision. Use real poker logic to narrow every spot down to a short
menu (bet small, bet big, slow-play, bluff, fold), with the options tuned to the
character and the situation, and let the model just *pick*. A choose-your-own-adventure,
where I wrote the pages and it turned to the choices.

This was better. It was also still wrong. It would fold the nuts, the best possible hand,
for no reason a human could reconstruct. And the harder I worked to stop it from doing
that, the more I noticed what I was actually doing: pre-selecting the options, weighting
them, narrowing the menu, until the "choice" I'd left it was the one I wanted it to make.
I wasn't getting the model to play poker. I was playing poker and using the model as an
elaborate dice cup.

Which defeats the purpose. If I'm making the decision, the LLM in the decision loop is
just latency and cost.

That's where I gave up. The game was fun, the core worked, and the one thing it was
supposed to be about, an LLM playing as a character, was the part that didn't work. I
didn't know what to do with it, and I put it down. Not for the first time.

## TieredBot: take the model out of the decision

When I came back, it was almost out of stubbornness. I wanted to salvage the project. And
I let go of the thing I'd been gripping for years: the LLM doesn't have to make the
decision.

Two ideas unlocked it.

The first was a reframe about what's actually *fun*. I'd been chasing a bot that plays
correctly. But a human doesn't sit down wanting to lose to a flawless machine. They want
an opponent they can *read*, someone with tendencies and tells and a style they can
figure out and exploit. "Hard to beat" and "fun to play" turned out to be different
targets, and I'd been aiming at the wrong one.

The second idea followed from the first. If the decision should be readable and
shapeable, it should be deterministic. So I split the bot into layers:

| Layer | Its job | How |
|---|---|---|
| **Strategic Core** | *What to do* | Solver-derived baselines plus poker heuristics |
| **Personality Modifier** | *How to deviate* | Bounded distortion of those base frequencies |
| **Expression Layer** | *What to say* | The LLM (chat, reactions, table talk) |

The decision is math now. The personality is a deliberate, measurable distortion of
correct play, so a maniac over-bets because I turned a dial, not because a model felt
like it. And the LLM finally has a job it's genuinely excellent at: being the voice. It
narrates, it needles, it stays in character. It just doesn't get a vote on the chips.

The moment I made that split, three things fell out almost for free:

- **The game became affordable to actually run.** A deterministic decision costs nothing.
  There's no per-hand model call to decide a fold. The LLM only fires for flavor. That's
  the difference between a tech demo and something I can leave online for people to play.
- **I could finally simulate at scale.** Deterministic bots can play millions of hands
  against each other in a sim harness. Multi-table tournaments, A/B tests on a strategy
  tweak, whole-economy runs, all of it became possible the instant the decisions stopped
  going through a model.
- **I could shape them.** Because the play is dials and distributions, I can make a
  character tight, loose, sticky, or aggressive on purpose, and verify it in the data.

And here's the part that still feels like vindication: the deterministic bot is *better
at poker*. A disciplined tiered bot does to a calling station exactly what a good player
should. It wins, and it wins big: a later version of it posts +102 big blinds per 100
hands head to head against a bot that calls everything, the kind of clean, repeatable
result the LLM versions never produced. (The full numbers, and the one fix that got it
there, are the next post.) The model I'd spent years trying to make play well played
better the moment I stopped asking it to play.

## The $50k bot I'm glad I didn't build

There was one more temptation. If I wanted truly strong poker, the textbook answer is a
GTO solver: game-theory-optimal play, computed. I got close enough to scope it that
there's still a plan doc in the repo, just in case. Then I looked at the compute bill: on
the order of **$50,000** by my rough estimate at the time, to solve the spots I'd need.

I'm glad the number was absurd, because it stopped me long enough to see that the solver
was the same mistake in a more expensive hat. A GTO bot is, by construction, unbeatable,
and an unbeatable opponent is not fun. People don't come to a poker table to admire
perfect play. They come to find the leak, make the read, and take the pot they weren't
supposed to get. The whole appeal is exploitability. I'd been trying to build the one
thing my players would least enjoy losing to.

## What I'd tell anyone building with an LLM

The lesson wasn't "LLMs are bad." It's that I'd put the model in the wrong layer. It's
extraordinary at the open-ended, subjective, voice-shaped work: being a character,
reacting, talking. It's unreliable at the part that has a correct answer and needs to be
consistent, cheap, and measurable. For years I kept handing it the second job because it
was so convincing at the first.

Take it out of the decision. Give it the lines instead. The game got cheaper, more
honest, and more *playable*, and, the part I didn't see coming, better at poker, the day
I stopped trusting it to play.

---

*Next in the Devlog: the deterministic bot I claim beats a calling station. Here are the
actual numbers, and the one counterintuitive fix that made a tight bot stop losing to a
player who calls everything. After that: how you make a bot hard for a* human *to read
when you have no humans to test against.*

<!--
CROSS-LINKS:
- to #6 / A4 "The bot that learned to beat the calling station" (the RegPlus numbers this post forward-references)
- to #8 / A6 "Making an AI hard to read with no human to test against"
- to B1 "Poker where the opponents are alive" (the Expression Layer, told from the player's side)

OPEN GAPS / FOUNDER INPUT:
- [RESOLVED] ChaosBot range figures: softened. Body now leads with the documented 85 to 100% VPIP wall and drops the unverifiable AA 40% / 22 57% specifics. (Still your call if you want the exact numbers back in.)
- [VERIFY] $50k GTO compute estimate: now framed as "my rough estimate at the time." Confirm you're comfortable publishing the number at all.
- [RESOLVED] RegPlus forward number: confirmed +102 bb/100 HU from keystone-regplus.md. Body states +102 and attributes the −88 to the next post (it was plain Reg, a different deterministic bot, not the LLM, so kept out of here to avoid conflation).
- [ASSET ~] Expression Layer visual: `/blog/cash-table-the-garage.jpeg` and `cash-table-flop.jpeg` (captured 2026-06-09) show public-domain characters with live emotion-state portraits ("smug," "poker_face") and action badges. A true text-banter speech bubble was NOT captured — chatter is probabilistic and the socket-heavy game page kept crashing the headless browser. Grab a banter bubble manually during real play if wanted.
- Title alternatives: "I took the LLM out of the decision," "The poker bot got better when I stopped trusting the model."
-->
