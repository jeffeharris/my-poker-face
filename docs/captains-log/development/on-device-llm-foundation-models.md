---
purpose: Narrative log of integrating Apple's on-device Foundation Models to generate quick-chat suggestions on iPhone — server-composed prompt parity, prewarm, prompt prefetch, and streaming
type: guide
created: 2026-06-12
last_updated: 2026-06-13
---

# On-device LLM (Apple Foundation Models) — captain's log

## The goal
Started as a research question: what did Apple ship at WWDC for on-device LLMs, and
could we offload any of the "small" in-game LLM tasks (chat suggestions, flavor
narration, categorization, beat cleanup) to it? It ended as a shipped feature:
quick-chat suggestions generated on the phone via Apple's Foundation Models, with the
prompt still composed server-side so quality doesn't drift.

## What Apple actually has
- **WWDC25 — Foundation Models framework.** Direct Swift access to the ~3B on-device
  model behind Apple Intelligence. Free, private, offline. Headline feature is *guided
  generation*: tag a Swift struct with `@Generable`/`@Guide` and the model emits that
  exact shape. Tuned for summarization/extraction/classification/short dialogue, not
  world knowledge or hard reasoning. Apple Silicon (A17 Pro / M-series), iOS 26+.
- **WWDC26 — AFM 3 + Python SDK + Linux.** Rebuilt model, multimodal 20B "Core
  Advanced" variant, third-party routing (Claude/Gemini), free Private Cloud Compute
  for small devs, and a `fm` CLI + Python SDK. The catch that killed the server path
  for us: the Python SDK needs an Apple Silicon Mac + Xcode; "Linux support" is the
  Swift open-source runtime for researchers, not a production server runtime.

Full research write-up: `docs/technical/ON_DEVICE_LLM_FEASIBILITY.md`.

## Why the answer is narrow
The deciding factor isn't platform, it's *where the task's state lives*:
- **Server path is dead.** Our small LLM calls run in the Flask backend, which ships
  as Linux x86 containers on Hetzner. Apple's model can't run there.
- **Only the iOS Capacitor app can reach the model.**
- **Most small tasks are server-authoritative.** Vice/side-hustle ticker narration
  feeds a shared world ticker; emotional categorization mutates AI opponent state;
  beat cleanup runs on server-generated dramatic sequences. None can move to one
  player's device. The *one* client-local fit is **chat suggestions** — shown only to
  the requesting player, who picks one to send.

## The build
- `react/react/ios/App/App/FoundationModelsBridgePlugin.swift` — Capacitor plugin:
  `availability()`, `suggestChat()` (with `@Generable` guided generation), later
  `prewarm()`. Registered explicitly in `MainViewController.capacitorDidLoad()` next
  to the existing WidgetBridge (Capacitor 6 only auto-registers npm-packaged plugins).
- `src/utils/onDeviceLLM.ts` — native-only JS bridge mirroring `widgetData.ts`.
- `src/utils/api.ts` — the two suggestion calls try on-device first, fall back to the
  server LLM on any error.

## The wrong turns (each a lesson)
1. **The Swift file wasn't in the Xcode project.** I wrote the `.swift` on disk but
   never added it to the App target, so Xcode silently skipped compiling it
   (`grep -c FoundationModelsBridgePlugin project.pbxproj` = 0 vs 4 for the existing
   plugin). Fixed by adding it via the `xcodeproj` Ruby gem. Lesson: a new source file
   on disk is invisible to Xcode until it's in the target.
2. **Intel Mac can't simulate Apple Intelligence.** This Mac is an Intel i9, and the
   iOS Simulator borrows the host's silicon, so Foundation Models is unavailable in
   the sim. The simulator was still useful to *compile-check* the Swift against the
   real iOS 26 SDK (no signing needed). Real generation had to run on a physical
   **iPhone 15 Pro / iOS 26.5**, where the model runs on the phone, not the Mac.
3. **`cap run ios` deploy failed to launch** (`ERR_UNKNOWN`). Build succeeded though;
   installed + launched directly with `xcrun devicectl` instead.
4. **No way into a JS console on the device.** Safari's Develop menu wasn't enabled
   and the on-device Web Inspector toggle was greyed, so the usual remote-debug path
   was blocked. Rather than fight it, I built a throwaway in-app `/dev/fmtest` page
   that showed `availability()` and ran a sample generation, plus a native-only home
   menu button to reach it. (Both stripped before the final PR.)
5. **Over-engineered the opt-in.** I gated on-device behind a `localStorage` flag that
   could only be set from the console I didn't have. Pointless friction; made on-device
   the default when the model is available, with `localStorage.onDeviceLLM=0` as a kill
   switch.

## Server-composes parity
First cut built a thin prompt on the client. It worked but felt generic — the client
doesn't have the full context the server prompt uses (personality, hand history,
message log). Rather than duplicate that logic, the server now composes the *exact*
prompt it would send to the LLM and returns it without calling the LLM: the suggestion
routes accept `render_only: true` and return `{ messages, count }`. The phone runs that
on-device. Identical prompt content, the paid inference just moves to the phone, and
because no `client.complete()` runs there's no `api_usage` row — which doubles as the
proof it's on-device. The honest tradeoff: it now needs a (cheap, non-LLM) round-trip
to fetch the prompt, so it isn't offline. Fine here — the rest of the game needs the
network anyway.

## The latency fix (the satisfying part)
First on-device numbers were rough: `gen` 3 to 5s. Instrumented the split with a temp
toast (`net` = render_only round-trip, `gen` = on-device). `net` was usually fine (one
2s spike, a cold server/SQLite blip); `gen` was the problem. The `/dev/fmtest` page,
running a *short* prompt, took ~1.5 to 2s — so part of `gen` was fixed overhead, part
was the long prompt's prefill.

Two real costs:
- **Prefill.** Prompt length hits time-to-first-token (prefill), not decode. The 3B
  model on the A17 Pro is much slower at prefill than a datacenter LLM, so the long
  server prompt costs real time. (Counter to the common assumption that prompt length
  doesn't affect "generation speed" — it does, via prefill.)
- **Cold model load.** We created a fresh `LanguageModelSession` per call and the model
  was loading cold each time.

Fix for the bigger one: a `prewarm()` plugin method that loads the model and holds a
session so the model stays resident, called when the quick-chat options (and the
post-round winner screen) mount — i.e. the "user is about to chat" signal, with the
second or two of read-time as lead. `gen` dropped to ~1.5s. Prompt trimming is the
remaining lever if we ever want sub-1s, at a small parity cost; not needed yet.

## Removing the network round-trip (prefetch)
Prewarm fixed `gen`. The other half was `net` — the `render_only` fetch that gets the
composed prompt. Usually quick, but it spiked to ~4s sometimes (cold server / slow
query), stalling the whole suggestion. Key observation: the expensive part of the prompt
(hand context + opponent social read) is identical across every tone/length/intensity;
only the knobs change. So the server gained a `render_only: "batch"` mode that builds the
common context once and renders every variant in one response (`{ system, variants }`, a
few dozen short prompts, a few KB). The client prefetches that when the chat options mount
(same trigger as prewarm) and caches it keyed by game+target+last-action. A tone tap is
then a pure cache lookup → on-device generate, with zero network. Misses (the hand moved
on) fall back to a live `render_only`, then the server LLM. (PR #326.)

## Streaming (the felt-latency win)
~1.5s is fine but still a blank-then-pop. The tempting "it's free, pre-generate all 36
variations" idea is a trap: the on-device model is a *serialized* resource, so queuing
dozens of generations cooks the battery/thermal budget and can make the user's actual
pick *slower* (it waits behind the others). The right tool is streaming the one
generation they asked for: `session.streamResponse(...)` emits cumulative snapshots, so
the first line appears before the second finishes. Same total time, feels near-instant.

Plumbing notes:
- Capacitor streaming = a callback method (`CAPPluginReturnCallback`): `call.resolve()`
  fires once per snapshot until `{ done: true }`; the JS `registerPlugin` proxy invokes
  the passed callback each time.
- The streamed element is a `Snapshot`, not the partial directly — the compiler taught
  us it's `partial.content.suggestions` (each field optional as it fills in).
- The JS wrapper self-heals: if the stream errors it falls back to one non-streaming
  generation, so a caller always gets a result.
- Latent bug caught in review: `call.keepAlive = true` was set *before* the
  `#available(iOS 26)` gate, and the unsupported path rejects without `releaseCall` → a
  leak if reached. Unreachable today (deployment target is 26.1, so the gate is always
  true), but moved keepAlive inside the available branch anyway. (PR #341.)

## Is the prompt too big? (measured, then left alone)
Sized the targeted prompt: ~205 tokens quiet, ~366 tokens busy (10 table-talk lines is
the biggest variable; the `Return JSON` tail is ~31 redundant tokens under guided
generation). After prewarm + streaming, prefill is not the bottleneck — a warm long
prompt runs about the same as a short one — so trimming buys ~100-200ms, not worth the
parity divergence now that streaming carries the felt latency. Left as-is.

## Shipping mishaps
- `render_only` (the server half) went in as PR #310. Then "merged it" turned out to
  have merged the *wrong* PR (#311, an unrelated bot change); #310 was still open.
- The red X on main looked like broken deploys but was only the flaky E2E Playwright
  job; the "Deploy to Production" step succeeds independently.
- #310 itself was blocked by a trivial Prettier format check on two new files. One
  `prettier --write` and it was green. Merged, CI deployed render_only to prod.
- We deploy via CI on merge to main, not `./deploy.sh` (noted to memory).
- Prewarm + the debug cleanup go in a follow-up PR.

## Takeaways
- On-device is only architecturally eligible for **client-local UX** (output the
  requesting player consumes, no shared/persisted state). The durable rule:
  *server owns the context, device runs the inference.*
- **Cold model load** is the hidden latency cost; `prewarm()` on the "about to use it"
  signal is the single biggest win.
- For the rest of the latency: **prefetch** the prompt off the critical path, and
  **stream** the generation so it *feels* instant. Do NOT pre-generate every variation —
  the on-device model is serialized, and "free" ignores battery and thermals.
- The cost savings are modest (fast-tier calls). The real value is a genuine,
  shipping Apple Intelligence integration.
