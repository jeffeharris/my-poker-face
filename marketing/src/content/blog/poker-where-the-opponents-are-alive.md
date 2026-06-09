---
title: Poker where the opponents are alive
description: The AI opponents in My Poker Face carry a real, persistent mood you can read. They tilt, they stay tilted, and they remember how they feel about the table.
track: Inside the Table
date: 2026-06-09
order: 1
hero: /blog/cash-table-the-garage.jpeg
heroAlt: A cash table of public-domain characters (Louis XIV, Alexander the Great, Cleopatra, Santa Claus), each with a live emotional state.
excerpt: Most poker apps give you a difficulty slider with a name attached. This one gives you characters with a mood the game actually keeps track of.
draft: true
---

Most poker apps give you opponents that are really just a difficulty slider with a name bolted on. "Hard mode" doesn't have a bad day. It doesn't get rattled when you bluff it off the best hand, and it definitely isn't still rattled twenty minutes later when you sit back down.

This game started from a different place. The opponents are characters first and a difficulty setting almost never. You play against Sherlock Holmes, or Blackbeard, or Cleopatra, and the thing that makes them worth playing against isn't how good they are at poker. It's that they have a mood, and the mood is real: a number the game keeps track of, that moves when things happen to them, and that you can read on their face if you're paying attention.

That premise is older than almost everything else in the project. Three years ago, one of the very first changes was a tiny commit titled *"Added some confidence and attitude,"* which let each character carry an emotional state instead of a fixed one. Everything since has been about making that real enough to play against.

## "Alive" means the emotion is visible and the strategy is not

There's one design rule the whole game hangs on:

> Emotion is visible; strategic intent must be inferred.

That single line is what separates a chatbot in a poker skin from an opponent you can actually read. You get to see the face: the table talk, the tilt, the swagger after a big pot. You don't get told what they're holding or what they're planning. You have to figure that out yourself, from how they're acting and how they've played. Which is, when you think about it, the entire game of poker.

## Why the character doesn't just dissolve into noise

The obvious worry with an AI character is that it'll be random, chaotic one hand and meek the next with no through-line. The game avoids that by building each opponent in three layers:

- **Who they are.** A fixed identity. Ten traits like ego, poise, and expressiveness that hold steady during a session. This is the character's gravity. A cocky player stays fundamentally cocky.
- **How they feel right now.** Three live dials, confidence, composure, and energy, that move every single hand based on what just happened.
- **What leaks out.** A filter on top that decides how much of the mood actually shows.

So the mood moves, but it moves inside the character. A high-ego player and a calm, grounded one will react to the exact same bad beat in completely different ways, and they'll each react like themselves every time.

## Tilt is a number, and it's sticky

Here's the part that makes it feel alive at the table. The mood is not re-rolled every hand. It's stored, and it carries.

When a player takes a brutal beat, the game doesn't just generate a sad sentence. It routes the event through that character's psychology and actually moves their dials. A player with high ego loses more confidence when you bluff them. A player with high poise shrugs off a cooler that would rattle someone else. And the recovery is lopsided on purpose:

> Below baseline: tilt is sticky.

Hot streaks fade fast; tilt lingers. That's why the opponent you just coolered plays scared three hands later instead of snapping back to normal. The character isn't acting out a one-line sulk. They're genuinely off their game for a while, and they have to climb back out of it.

## And it follows them out of the room

The newest piece surprised even me with how much it changes the feel. The mood persists across sessions. When an AI gets up from your cash table rattled and you come back later, it's still rattled. Their emotional state is saved when they leave and restored when they return, and if they've been idle a while, their energy quietly recovers on its own in the meantime, the way a real person cools off between sessions.

So a rivalry can actually build. The character you've been needling all week doesn't reset to a blank slate every time the page reloads. The table was still there while you were gone.

## You can read it, and the game shows you that you're reading it

All of this would be wasted if you couldn't see it, so the payoff is the opponent dossier: a profile that fills in as you log hands against someone. It'll flag a character as "overconfident," or sum up their style in a line ("wild and high-pressure, shoves on a whim"), with deeper behavioral reads unlocking the more you play them.

![Edgar Allan Poe's dossier: a CLASSIFIED case file with his style line, current mood, and a ladder of behavioral reads that unlock as you log hands.](/blog/dossier-edgar-allan-poe.png)

How much shows depends on the character. Expressiveness is most of what determines whether a player's mood leaks out. A theatrical character broadcasts everything, while a stone-faced one keeps the mask on no matter what they're feeling. Reading the table is the actual skill the game rewards.

## The honest part: the AI isn't cheating

It would be easy to make "personality" a cheap trick, handing the bot a tell when it's convenient and taking it away when it's not. This game goes the other way, and the difference matters if you're going to spend real (pretend) money against these players.

Two honest things to know:

- **The mood changes what you can see, not what the cards are.** A tilted player isn't dealt worse hands. Their emotional state affects how they read a spot and how much they give away. It never touches the deck. No rubber-banding, no hidden hand.
- **A poker face hides the face, but not the pattern.** Expression can mask the mood, but it can't mask the habit. A character who always bets big when they're confident stays exploitable no matter how good their poker face is, because the tell lives in the betting, not the chatter. That's deliberate. It's what makes the read a real read instead of a scripted one. The stoic players are harder to feel out, but nobody is truly unreadable if you watch how they actually play.

## Why any of this matters

The point of the whole stack, the moods and the stickiness and the persistence and the dossiers, is that a hand stops being a math problem and becomes a story. You didn't just win a pot. You found the spot where Blackbeard was still rattled from an hour ago and pushed him off it. That's a thing you'd actually tell someone about.

![A finished hand narrated back to you as a story, from the High Roller Pit session recap.](/blog/tell-your-story.png)

That's the bet the whole game makes: that the best hand doesn't always win, sometimes the best read does, and that an opponent worth reading is one that's actually alive.
