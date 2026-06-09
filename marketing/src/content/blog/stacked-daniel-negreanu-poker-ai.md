---
title: "Stacked With Daniel Negreanu, and the Poker AI We've Been Chasing for 20 Years"
description: "In 2006, Stacked with Daniel Negreanu put world-class academic poker AI in a $30 console game. It got the big idea right and the execution famously wrong. Here's what it nailed, where it broke, and what My Poker Face took from both."
track: Devlog
date: 2026-06-09
order: 11
hero: /blog/characters-trio.png
heroAlt: Three of My Poker Face's AI opponents. The spiritual descendants of a roster of eight that shipped in 2006.
excerpt: A 2006 game licensed the best poker AI in the world and still got read in 40 hands. Its one fatal flaw shaped everything about how we built our opponents.
draft: true
---

Most people building an AI poker game quietly assume they are first. They are not. In 2006, a game called *Stacked with Daniel Negreanu* shipped with poker AI more sophisticated than most poker apps carry today, and twenty years later it is still the benchmark everyone in this niche gets measured against. It also flopped commercially and got taken apart by serious players for one very specific reason. That reason turned out to be the most important design lesson in our entire game, so this one is an homage and a confession at the same time.

## What Stacked was

*Stacked* came out in 2006 from 5000ft Inc. and Myelin Media, on PC, PS2, Xbox, and PSP, Texas Hold'em only. Its headline feature, and the thing that separated it from the shovelware poker games of the era, was the **Poki engine**, licensed from the University of Alberta's Computer Poker Research Group, which at the time was the world's leading academic poker-AI lab.

Poki was not a set of scripts. It used per-opponent weight tables and Monte-Carlo simulation to model the people at the table and adapt to them in real time. The game shipped it as **eight distinct personalities**, ranging from very conservative to wildly aggressive, and wrapped them in a genuine teaching layer: an in-hand **"Ask Daniel"** advice feature and a school of full-motion-video tutorials, fronted by digital likenesses of real pros including Negreanu, Evelyn Ng, David Williams, Jennifer Harman, and Josh Arieh. Reviewers called its AI the best in any poker video game. A lot of them still would.

## What it got right

Strip away the year and the hardware, and *Stacked* made exactly the bet we are making.

- **Adaptive archetypes plus integrated coaching, in one product.** Opponents with distinct styles that you learn to read, sitting next to a coach who teaches you to play better. That combination is the whole thesis of My Poker Face, and *Stacked* is the historical proof that it resonates with players. It has been technically possible for two decades. Almost nobody has done it well since.
- **Real opponent modeling, not a difficulty slider.** The AI was actually trying to learn you, which in 2006 was remarkable and in 2026 is still rare in a consumer poker game.
- **It proved the appetite.** "Best AI in a poker game" was the headline, the review hook, and the thing players remembered. People want opponents worth reading.

## What it got wrong: the 40-hand problem

Here is the verdict that should be tattooed on the wall of anyone building this kind of game. A serious player reviewing *Stacked* for Gaming Nexus wrote that it took him about **40 hands** to figure out exactly which personality each opponent was. The conservative players played ultra-conservative. The aggressive players played ultra-aggressive. Whatever the Poki engine was doing under the hood to adapt, it did not visibly change anything, so the archetypes read as pre-tuned caricatures within an orbit.

That is two separate failures, and we took both of them personally.

The first is **caricature**. An opponent whose aggression is a flat constant carries no information, so a human brain pattern-matches it to "robot" almost immediately. Real aggressive players are not aggressive at a fixed rate. They vary by position, by who they are up against, by stack depth, by what just happened to them. A spike in aggression is believable when it is the visible output of a state. As a flat setting, it is a tell you read once and never have to read again.

The second is **imperceptible adaptation**. The cruel irony of *Stacked* is that the AI may genuinely have been adapting, and it did not matter, because the player could not feel it. The lab's own papers even hedged that the strong opponent-modeling results came largely from self-play simulation rather than proven results against humans. Under-the-hood intelligence the player cannot perceive is, for game-design purposes, intelligence that does not exist. The coaching had a version of the same disease: "Ask Daniel" was often out of sync with the actual hand, and would sometimes cheerfully tell you to shove rags.

## What My Poker Face took from it

Almost every core decision in our game is a direct response to one of those failures.

- **Archetypes that move with context, not flat dials.** Our opponents change how they play based on position, history, and emotional state, so when one suddenly gets aggressive, it means something happened. We even built a research harness to check that our archetypes hit real-world statistical benchmarks instead of collapsing into the *Stacked* caricature.
- **Adaptation you can actually see.** This is the big one. The recurring lesson across *Stacked*, the Nemesis System in Shadow of Mordor, Alien: Isolation, and Forza's Drivatars is that intelligence has to be surfaced to be felt. So we made the mood visible on the character's face, made [tilt sticky and persistent](/blog/poker-where-the-opponents-are-alive/), and built a dossier so you literally [watch an opponent's read of you accumulate](/blog/your-opponents-remember-you/) across sessions.
- **A coach grounded in the hand you actually played.** Instead of generic, out-of-sync advice, the coach studies your real decisions and names your real leaks. The contrast with "Ask Daniel" telling you to shove rags was very much on our minds.
- **Scale.** *Stacked* had eight personalities. We run around eighty persistent [characters](/opponents/), each with its own style, tells, and memory. As far as we can tell, that is the largest such roster in any poker game.

And there is one place we went the opposite direction on purpose. We were tempted, like everyone in this space, to chase a perfect, unreadable, theoretically optimal bot. We [talked ourselves out of it](/blog/llms-cant-play-poker/), because *Stacked* and our own experiments pointed at the same truth: the fun is in finding the leak, not in facing an opponent who has none. An unbeatable bot is a math problem. A readable one is a story.

## The homage

It is easy to be glib about a twenty-year-old game that did not sell. The honest read is the opposite. *Stacked* put world-leading academic AI into a thirty-dollar console game, paired it with real coaching, and built a roster of opponents people still talk about, and it did all of that before most of the tools we take for granted existed. It got the idea right. It just could not make the intelligence felt.

That is the gap we have spent years trying to close. *Stacked* walked so the rest of us could try to run. If you want to see where that road leads twenty years later, [pull up a chair](/login), or [meet the opponents](/opponents/) who owe more than they know to a roster of eight that shipped in 2006.
