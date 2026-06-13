---
purpose: Devlog draft on integrating Apple's on-device Foundation Models to generate quick-chat suggestions on iPhone, and what the model is actually good for
type: vision
created: 2026-06-12
last_updated: 2026-06-13
---

> **Draft status:** first draft. Grounded against
> `docs/captains-log/development/on-device-llm-foundation-models.md` and
> `docs/technical/ON_DEVICE_LLM_FEASIBILITY.md`. Numbers (3 to 5s, ~1.5s) are from a
> single device session on an iPhone 15 Pro. Inline `[VERIFY: …]` flags claims to
> confirm before publishing. Track: **Devlog** (career + creator-community).

# I put Apple's on-device AI in my poker game. Here is what it could actually do.

Apple now lets you call the language model behind Apple Intelligence directly, on the
phone, for free. It is a roughly 3-billion-parameter model that runs entirely on
device: no network, no API bill, nothing leaves the handset. When I saw the framework,
the obvious question for My Poker Face was whether I could move some of the game's
smaller AI work onto it.

I spent a session finding out. The short version: yes, for exactly one feature, and the
interesting part was not the wiring. It was figuring out which work is even eligible,
and then chasing down why the first version felt slow.

## Why bother, honestly

I will be upfront about the money, because it is the weakest reason. The calls I moved
on device are cheap ones. The per-call savings are tiny. [VERIFY: pull a real
per-1k-call cost from the usage table for the chat-suggestion call type.] If this were
only a cost play, it would not be worth a line of Swift.

The reasons that hold up are different. It is a real, shipping integration with Apple
Intelligence, which matters for how the app is positioned and, plausibly, for how Apple
features apps that adopt new frameworks. It is private by construction, since the text
never leaves the phone. And it is a genuinely good engineering problem, the kind that
teaches you where a new tool fits and where it does not.

## Most of the work could not move, and the reason is the useful part

My instinct was to push a pile of small LLM calls onto the device. The game makes a lot
of them: chat suggestions, the flavor text when an AI character leaves the table to go
spend money, the one-line read on an opponent's mood, cleanup passes on the dramatic
beats a character acts out.

Almost none of them are eligible, and the reason is a clean rule rather than a quirk:
**a task can only run on the phone if both its input and its output are that one
player's local business.** The moment a task touches shared, server-owned state, it has
to stay on the server.

Walk through them and it sorts itself out. The mood read does not just print a
sentence, it changes the opponent's emotional state, which steers how that AI plays
later. That has to happen where the opponents are computed, on the server. The
"character left to go shopping" line lands in a world ticker that runs on a background
loop on the server, independent of whether your phone is even awake. The beat cleanup
runs on text the server generated mid-hand. All server work.

The one feature that passes the test is quick-chat suggestions. When you are about to
needle Blackbeard after he folds, the app offers you a couple of lines to send. Those
suggestions are ephemeral, shown to exactly one person, on exactly one device, and you
pick one or ignore them. No shared state, no persistence, no fairness question. That is
the whole eligible surface, and it is worth being honest that it is small.

The durable lesson, which I have written on the wall now: **the server owns the
context, the device runs the inference.** Anything that breaks that rule does not belong
on the phone.

## The trick that kept quality intact

My first version built the prompt on the phone from what the client happened to know.
It worked, but the suggestions were a little generic, because the client does not have
the full picture the server prompt uses: the opponent's personality, the hand history,
the recent table talk.

I did not want to reimplement all of that context-building in Swift. So I inverted it.
The server still composes the exact prompt it would have sent to its own model, but
instead of calling the model, it hands the finished prompt back to the phone. The phone
runs it on the Apple model. Same prompt, same quality, and the expensive part (the
inference) is the only thing that moved.

There is a tidy side effect. Because the server never runs the model on this path, it
never writes a usage record for it. So "no usage row appeared" is the proof that the
phone, not the server, generated the lines. The honest cost: the phone still makes one
small network call to fetch the prompt, so this is not an offline feature. That is fine
for a poker game where the table itself needs the network anyway.

## Then it was slow, and the reason was not what I assumed

The first on-device suggestions took 3 to 5 seconds. That is too slow for a little
"here are some things to say" panel.

I assumed the long server prompt was the problem, and a friend's instinct (and mine)
was that prompt length should not matter much, since the model only has to write two
short lines. That turns out to be half right, and the half that is wrong is worth
knowing.

A model answers in two phases. First it reads the entire prompt (this is called
prefill, and it is where "time to first token" comes from). Then it writes the output
one token at a time (decode). Decode scales with how much you write, which here is tiny.
Prefill scales with how much it reads. A long prompt does not slow down the writing, but
it does slow down the reading, and a 3-billion-parameter model on a phone is much slower
at that reading step than a data-center model is. So prompt length does cost time, just
in a phase people forget about.

But prefill was not even the biggest cost. I added a quick timer and split the latency
in two: the network fetch was usually fine, and the on-device generation was the slow
part. A short test prompt still took about 1.5 to 2 seconds on its own, which told me a
large chunk was fixed overhead, not the long prompt. The culprit: I was creating a fresh
model session on every call, and the model was loading cold each time.

The fix is the kind that feels obvious in hindsight. Apple's framework lets you prewarm
the model, loading it into memory ahead of a request and keeping it resident. So I
prewarm it the moment the chat options appear on screen, which is the earliest reliable
signal that you are about to chat. By the time you have read the options and picked a
tone, the model is already loaded. Generation dropped to about 1.5 seconds, and every
suggestion after the first stays warm.

That fixed the on-device half. The other half was the network call. Remember the design:
the phone asks the server for the composed prompt, then runs it. That fetch was usually
quick, but every so often it spiked to about 4 seconds, a cold server moment or a slow
query, and the whole suggestion stalled behind it.

Here is the useful observation: the expensive part of the prompt, the hand context and
the read on the opponent, is identical no matter which tone or length the player picks.
The only thing that changes per tap is small. So instead of fetching one prompt at click
time, I fetch all of them once, the moment the chat options appear, the same trigger as
the prewarm. The server builds the shared context a single time and renders every
variant, a few dozen short prompts, a few kilobytes of text, and the phone caches them.
When the player taps a tone there is no network at all: it reads the matching prompt from
the cache and generates on device. From the moment the options are on screen, the feature
works with no connection, and the occasional 4 second stall is gone. The cache is keyed
to the last action, so when the hand moves on it quietly refetches.

This is the same "compose on the server, run on the phone" idea from before, just moved
earlier in time. The server still owns every prompt. The device picks the right one and
runs it.

There is a further lever I have not pulled: trimming the prompt for the on-device path
to cut the prefill cost, at the price of a little of that hard-won context parity. I did
not need it. 1.5 seconds for a free, private, on-device suggestion is a fine trade.

## How it looks in code

These examples use a generic "welcome the user back" greeting instead of the poker
specifics, so they drop into any app. The shapes are the same ones I shipped.
`[VERIFY: API surface is iOS 26 Foundation Models; re-check names against the current
SDK before publishing.]`

**Guided generation.** You describe the output as a Swift type and the model fills it
in. No parsing, no "please return JSON" and hoping.

```swift
import FoundationModels

@Generable
struct Greeting {
    @Guide(description: "A short, warm welcome-back line, under 12 words")
    var text: String
    @Guide(description: "One emoji that fits the tone")
    var emoji: String
}

func makeGreeting(for name: String, lastSeen: String) async throws -> Greeting {
    let session = LanguageModelSession(
        instructions: "You write short, friendly welcome-back lines for an app."
    )
    let prompt = "Greet \(name), who was last seen \(lastSeen). One warm line."
    let reply = try await session.respond(to: prompt, generating: Greeting.self)
    return reply.content
}
```

**Check availability, and keep a fallback.** The model is only present on recent
hardware with Apple Intelligence turned on. Treat it as a fast path that always needs a
plain default behind it.

```swift
switch SystemLanguageModel.default.availability {
case .available:
    return try await makeGreeting(for: name, lastSeen: lastSeen)
case .unavailable:
    return Greeting(text: "Welcome back, \(name).", emoji: "")
}
```

**Prewarm before you need it.** This was my biggest latency win. Call prewarm when you
know a generation is coming soon (for a greeting, when the screen that will show it
starts loading), so the model is resident by the time you actually ask.

```swift
final class GreetingService {
    private let session = LanguageModelSession(
        instructions: "You write short, friendly welcome-back lines for an app."
    )

    // Call this on screen-will-appear, not at the moment you render.
    func warmUp() {
        session.prewarm()
    }

    func greeting(for name: String, lastSeen: String) async throws -> Greeting {
        let prompt = "Greet \(name), who was last seen \(lastSeen). One warm line."
        return try await session.respond(to: prompt, generating: Greeting.self).content
    }
}
```

**Compose on the server, run on the phone (optional).** If the good version of your
prompt needs data only your server has, let the server build the prompt and return it
without running a model, then run that prompt on the device. You keep the rich prompt
and skip the cloud inference bill.

```ts
// The server route returns the finished prompt, not a model result:
//   POST /api/greeting/prompt  ->  { system, prompt }
const { system, prompt } = await fetch('/api/greeting/prompt', {
  method: 'POST',
  body: JSON.stringify({ context: 'returning user' }),
}).then((r) => r.json());

// Hand it to your native bridge, which runs it on the on-device model.
const greeting = await OnDeviceModel.generate({ system, prompt });
```

**Stream it so it feels instant.** Even at a second or two, a blank wait followed by a
sudden pop feels slow. Streaming shows the text as the model writes it, so something
appears almost right away. Iterate the response stream and push each cumulative snapshot
to the UI.

```swift
func streamGreeting(
    for name: String,
    lastSeen: String,
    onUpdate: @escaping (Greeting.PartiallyGenerated) -> Void
) async throws {
    let prompt = "Greet \(name), who was last seen \(lastSeen). One warm line."
    let stream = session.streamResponse(to: prompt, generating: Greeting.self)
    for try await snapshot in stream {
        // Each snapshot is the structure filled in so far; its fields are optional
        // until they arrive. Publish it to the UI as it grows.
        onUpdate(snapshot.content)
    }
}
```

One gotcha that will save you a compile error: the stream yields a `Snapshot`, and the
partially built value lives under `snapshot.content`, not on the snapshot itself. And
resist the urge to pre-generate every possible answer "because it is free." The
on-device model runs one request at a time, so a queue of speculative generations heats
the phone and can leave the one the user actually wanted waiting behind the rest. Stream
the one they asked for.

## What I would tell someone eyeing this framework

Three things, in order of how much time they would have saved me.

First, decide eligibility before you write any Swift. The question is not "is this an
LLM task," it is "does this task read or write anything other than this one user's local
state." If it does, it stays on your server, and most things do.

Second, budget for the model-load cost and prewarm. The cold load was a bigger latency
hit than my long prompt, and prewarming on the "about to use it" signal was the single
biggest improvement I made.

Third, keep your good prompt on the server and ship it to the device to run. You do not
have to choose between on-device inference and the rich context your backend already
builds. Compose on the server, execute on the phone.

The cost savings will not move your numbers. But a real on-device AI feature, private
and free to run, generated on the same silicon that runs Apple Intelligence, is a more
interesting thing to have built than the few cents it saves would suggest.

## What is next

Two follow-ups, one practical and one bigger.

The practical one is Android. Google ships an on-device model too, Gemini Nano, reachable
through ML Kit's GenAI APIs, and the shape of this work ports almost directly: check
availability, keep a fallback, compose the prompt on the server, run it on the device,
prewarm before you need it. I am wiring the same poker chat suggestions through Gemini
Nano now, and that is its own post once it is working.
`[VERIFY: confirm the Gemini Nano access path and device coverage before publishing.]`

The bigger one is the part that actually matters for businesses, and it goes well beyond a
chat box. The real driver, for me and for a lot of teams, is cost predictability. When an
LLM is core to your product, the bill is genuinely hard to forecast and harder to cap. Spend scales with your users, and so does abuse: someone will
find the text box and hammer it. You end up building rate limits, quotas, and fraud checks
just to keep one feature from running up a number you cannot predict. For a small developer
with no leverage to self-host a model and flatten that curve, the math often just says no,
and a whole category of features never gets built.

On-device inference changes that calculation. The marginal cost of a generation is zero,
the spend is bounded by definition, and there is no hole to plug because there is no meter
running. It will not fit everything, and the eligibility rule from earlier still holds,
but it quietly opens a long list of use cases that were cost-prohibitive a year ago,
especially for the people who could least afford the unpredictability. That is the post I
actually want to write, and this small poker feature is the first proof of it for me. More
soon.
