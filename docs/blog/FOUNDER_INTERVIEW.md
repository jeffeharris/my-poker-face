---
purpose: Founder interview capturing the motivation, decisions, and voice the artifacts (git + transcripts) can't show — the human layer for the blog
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Founder interview — 2026-06-09

Grounded Q&A to get the things only the founder knows out of his head, after the
git + transcript research. Lightly edited for readability; substance and tone
preserved. Not dramatized — that's the point.

## Why he kept coming back (and kept stopping)

> It's constantly digging at me. It's a fun project — building a poker sim, building
> the analytics tools around it to improve the system, and just building a web app
> in general is interesting. I like poker and I like solving puzzles.

The gaps were life and frustration, not loss of interest: two kids, a lot of
changeover in his job situation, and the project being obsessive enough that he
sometimes needed to step away. Other times he stepped away because he couldn't
find a good *use* for it, or because he was so frustrated with **how bad the LLMs
were at playing poker.** He spent a few years trying to get a pure LLM agent to
play poker *and* represent personality.

> When I first started the project there was no Claude Code, so I actually wrote the
> functional-programming core poker engine myself, and then was able to use ChatGPT
> and other tools to write the frontend. I used OpenAI as the LLM engine to begin,
> but we use a mix of LLMs in different areas now.

## What made December 2025 the restart that stuck

He had time. Following the **acquisition of Yotascale in August 2025**, he moved
into **freelance consulting with early-stage founders around LLM and B2B
products**, and was also stay-at-home dadding — so his days were mostly free to
spend on this.

> I decided I needed shapeable poker bots instead of leaving it up to the LLM. The
> game was fun and the core worked, but the LLM was INSANE and terrible at poker
> even if I spoon-fed the options to it. At that point I gave up pretty much and
> didn't know what to do with it. But now it feels like there's an idea and a hook
> around the poker playing that feels fun. So that's what's keeping me here.

## What "completion" meant at first

> I wanted to get to completion, and at the time completion felt like getting it
> hosted on the internet and being able to share a link to it with someone. I was
> going to use it as a bit of a portfolio project to highlight what I'd been able to
> do on my own.

## The bot journey — the heart of the Devlog

This is the throughline. Three architectures, each abandoned for the next:

**ChaosBot** — the original single-prompt LLM player.

> It had all the hand info, opponent info, instructions on how to respond, and it
> would form a JSON and play a character. On the surface it looked great — a great
> demo. But then I got curious: poker is statistically measurable. And I had a TON
> of data — I'd built a whole experiments platform that let me run hundreds of
> thousands of LLM queries with minor tweaks to figure out how to shape the
> responses. I built an LLM assistant to design the experiments, run them, and
> analyze the massive amounts of data. It was cool. But we were getting nowhere.

Two problems killed it. The measurement was suspect — **the same equity calc made
the decisions and graded them.** And when he finally looked at ranges, the bots
were just random:

> They played EVERYTHING. AA was played 40% of the time and 22 was played 57% of
> the time. It was insane. And the prompt fell apart if you tried a different LLM,
> so my hope of playing LLMs against each other to see which was the better
> strategist fell apart too. The LLMs were awful strategists (no offense). It was a
> lot to ask — juggle a full personality, play a character (which it did pretty
> well), plan poker strategy, and notice how others were playing. Even with the full
> history of every decision at the table, it wasn't pulling out solid strategy or
> exploitation. It was very emotional and dramatic.

**HybridBot** — poker logic narrows the LLM to a bounded set of choices.

> More like a choose-your-own-adventure story — it was given multiple choices with
> bet sizing, or slow-playing, or bluffing, depending on the archetype and the
> emotions. But again, the LLM was awful. Slightly better than pure ChaosBot, but it
> would fold the nuts on occasion and it made no sense. I got close at one point, but
> it felt like I was really just making the decision for the LLM and trying to get it
> to pick what I wanted it to. Which defeated the purpose.

He gave up. Came back later.

**TieredBot** — the salvage attempt that worked.

> A desperation attempt because I wanted to salvage the project. I had an idea that
> if users could *read* the opponent, they'd be able to play against AIs and it
> would be fun and challenging. And the LLM layer was really useful for running the
> narration. So the decisions and weights all become deterministic — and all of a
> sudden the game is affordable to play! Now I can build sim harnesses, run
> multi-table tournaments, and shape the AIs to play a certain way.

The GTO road not taken:

> I almost built a GTO solver — there's a plan doc still there, just in case. But it
> was looking at ~$50k of compute at least, which was a stretch. Then it clicked
> that even if I had a GTO solver bot, that wouldn't be fun, because it's inherently
> not beatable. People want to find leaks!

## The hook, in his words

> Being able to play against anyone you want, and they have a memory and learn how
> you play over time. It's the interaction — but being able to put the table on hold
> and come back and everyone's still there, ready to keep the game going. I think
> the hook is progression and feeling like the world is alive. The cash-game idea is
> great too, because it makes the stakes feel more real.

On the coach feature:

> I'd been looking for a reason someone would play it more than once for a long
> time. The coach thing came from that. But it was too weak, and we didn't have any
> real data — but we could build the feature and it was neat as an idea.

## Where it stands publicly

It's live and shareable. So far only his **brother and dad** have looked at it. The
blog is the first deliberate step outward.

## What the blog is for

- **Devlog track → career + creator-community focused.** Establishing credibility.
- **Inside the Table track → player focused.** Anything about gameplay or features.

## Candor / framing notes

- Frame the employment context as **freelance consulting with early-stage founders
  around LLM and B2B products after the acquisition of Yotascale (August 2025)** —
  not "unemployed / no job."
- The honest, grounded candor (years of the LLM being bad, "I pretty much gave up,"
  stay-at-home dad, world built by one person) stays in — it's what makes it real.
- Private color, keep out of public posts unless he opts in: the specific ~3-month
  detour exploring a B2B SaaS idea / producing movies.
