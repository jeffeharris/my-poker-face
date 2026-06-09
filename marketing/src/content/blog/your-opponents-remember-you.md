---
title: Your opponents remember you
description: For three years the AI opponents forgot you the moment a game ended. Fixing that had almost nothing to do with a smarter model.
track: Inside the Table
date: 2026-06-09
order: 3
hero: /blog/dossier-edgar-allan-poe.png
heroAlt: An opponent dossier, a CLASSIFIED case file with behavioral reads that unlock as you log hands.
excerpt: A lifetime table, a tally that's safe to run twice, and a dossier you earn. How opponents grew a memory that survives the game ending.
draft: true
---

For almost three years, the AI characters in this game had no memory of you.

They had moods and attitudes. That part has been there since 2023. But the moment a game ended, everything they'd learned about how *you* played evaporated. Sit down at a new table against the same [Blackbeard](/opponents/blackbeard/) and his read of you reset to zero, every time. You could three-bet him off three pots in a row on Monday and he'd have no idea who you were on Tuesday.

The strange part, when I finally dug into it, was that the data wasn't being thrown away. The per-game stats (how often you played a hand, how often you raised, how aggressive you were) persisted fine and reloaded fine. They just never added up across games. Each table was its own little island. Naming the problem that precisely mattered: the characters weren't forgetting you, the counts simply never carried from one game to the next.

And fixing it had almost nothing to do with a smarter model. It came down to a lifetime table, a tally that's safe to run twice, and being careful about the order you take someone's chips.

## Making a number that lasts

There are two honest ways to give a stat a memory, and the game uses both.

For the core reads (how loose you are, how aggressive) there's a **lifetime table**: a running total that each finished hand adds to. For the rarer stuff, like the big dramatic pots and pressure moments, the game does the opposite. It **re-counts from scratch** every time you open a dossier, because the underlying events are all still sitting in the database anyway.

That second choice sounds lazy and is actually the careful one. If you recount from the raw events every time, you structurally cannot double-count, because there's no running total to corrupt. So wherever the source data already existed, that's the path I took.

## The bug I designed out before it happened

Here's the subtle thing that makes the memory trustworthy. Cash sessions don't really "end." They're long-lived, and you can leave and come back to the same table. So a "tally everything once when the game's over" approach would quietly drop every hand you played *after* you resumed. The memory would have holes exactly where you played the most.

So the tally doesn't run once. It runs continuously, and it keeps a high-water mark: add only what's new since last time, then remember where you got to. Run it again on a table where nothing changed and it writes nothing. That property, that re-counting is always safe, is what let the whole thing sit on the normal, boring save points instead of the fragile end-of-session path where this class of bug usually lives.

None of that is glamorous. It's the unglamorous part that makes "they remember you" true instead of mostly true.

## From a stat to intel you earn

A lifetime stat is just plumbing. The actual feature is the **dossier**, and the dossier is something you earn.

You don't get a full read on a stranger. Sit down against someone new and their file is mostly locked: *"Insufficient observation. Play 24 more hands to open this file."* The deeper reads unlock as you log hands against them, starting at a 25-hand floor and filling in from there, with the deepest reads opening up around 180 hands.

The metric is *hands observed*, and that deliberately counts the hands where they fold. The reasoning was simple. A nit who folds everything shouldn't take forever to scout. You learn something real every time they muck, so it should count.

And the lock is real, not cosmetic. The intel you haven't earned never leaves the server, so there's nothing to dig out of your browser. You have to actually play the hands.

## Don't want to grind? Pay an informant

If you don't feel like putting in the 25 hands, you can buy the read instead. Paying for a dossier section skips the grind, the "I don't know this guy, so I'll pay to find out who I'm sitting down against" move. The fee feeds back into the same economy that bankrolls the AI players, so the chips stay in the world.

## It turned into a meta-game

Once you're collecting reads on everyone you've played, those files need a home, so they grew one. What started as a plain "file cabinet" got redesigned into **The Archive**: a noir case-file aesthetic, manila folders and wax seals, the whole private-eye fantasy. Then it folded together with the activity feed and the "who's around right now" view into a single intel hub: **The Wire, The Floor, The Files.**

That progression is the answer to a question I'd been circling for a long time. Why would someone come back and play a second time? A collection you build by playing is a pretty good reason. The dossiers stopped being a stats screen and became something you accumulate.

I'll be honest about the path, too. It didn't arrive fully formed. My own note on the first version was blunt:

> it's fine. i don't like that the who's-around and file cabinet are right next to each other... the file cabinet itself is serviceable. not really exciting.

"Serviceable, not really exciting" is what kicked off the redesign into The Archive. And the unlock boundary bit back at least once: opening a dossier at exactly 25 hands crashed on a missing value (*Cannot read properties of null*), the new feature breaking precisely at the moment it was supposed to light up. Both of those are in here because that's how it actually went.

## Why a memory, instead of a smarter bot

The characters in this game have had a mood since the very beginning, a confidence and an attitude that shift while you play them. Cross-session memory is that same instinct, stretched across time. An opponent isn't only a strategy you're up against. It's a record of the history the two of you have. The Blackbeard who's seen you bluff him a dozen times is, in a small but real way, a different opponent than the one you just met.

And the work that made it real was ordinary. A lifetime table, a tally that's safe to run twice, and charging chips in the right order. Memory, it turns out, is mostly bookkeeping you can trust.

Sit down, log some hands, and earn a read of your own. [Take a seat](/login), or scout the [opponents you'll be playing](/opponents/) first.
