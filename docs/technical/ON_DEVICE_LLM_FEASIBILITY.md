---
purpose: Feasibility analysis of using Apple's on-device Foundation Models (WWDC 2025/2026) for the game's smaller LLM tasks
type: design
created: 2026-06-12
last_updated: 2026-06-12
---

# On-Device LLM Feasibility — Apple Foundation Models

Can My Poker Face offload some of its **smaller** LLM tasks (chat suggestions, flavor
narration, categorization, beat cleanup) to Apple's on-device model? Short answer: **yes,
but narrowly** — only the **chat-suggestion** family, only on the **iOS app**, with the
server path as fallback for everyone else. This doc records the research and the reasoning.

## What Apple shipped

### WWDC 2025 — Foundation Models framework
- Direct Swift access to the **~3B on-device model** behind Apple Intelligence.
- **Free, private, offline, no API cost.** Runs on-device only (no server model exposed).
- Headline feature: **guided generation** — annotate a Swift `struct`/`enum` with
  `@Generable` / `@Guide` and the model emits exactly that structure (constrained decoding).
  Also: tool calling and streaming.
- Tuned for **summarization, extraction, classification, short dialogue, short creative
  text** — explicitly **not** world knowledge or hard reasoning (that's what server-scale
  LLMs are for).
- Requires Apple Intelligence enabled + supported silicon (A17 Pro / M-series), iOS 26+.

### WWDC 2026 — AFM 3 and friends
- Rebuilt **AFM 3** on-device model: more intelligent, better logic + tool calling.
- **AFM 3 Core Advanced**: 20B sparse model on high-end devices only (iPhone 15 Pro+/latest
  iPad Pro/Mac), adds **image input** (multimodal) + Vision tool calling (OCR/barcode).
- **Model-abstraction layer**: route the *same* Swift API to Apple's model, Claude, Gemini,
  or other providers conforming to the Language Model protocol — without changing downstream code.
- **Free Private Cloud Compute (PCC)** access for devs in the App Store Small Business
  Program with **< 2M first-time downloads**.
- Framework went **open source**; gained a **Python SDK + `fm` CLI**.
- **Caveat that matters to us:** the Python SDK requires an **Apple Silicon Mac + Xcode +
  Python 3.10+**. "Linux support" is the open-source Swift runtime/utilities aimed at
  researchers/scripting — *"not a full production server runtime."*

Sources: [WWDC25 Foundation Models](https://developer.apple.com/videos/play/wwdc2025/286/),
[Apple ML Research 2025 updates](https://machinelearning.apple.com/research/apple-foundation-models-2025-updates),
[WWDC26 Apple Intelligence guide](https://developer.apple.com/wwdc26/guides/apple-intelligence/),
[WWDC26 fm CLI & Python SDK](https://developer.apple.com/videos/play/wwdc2026/334/),
[apple/python-apple-fm-sdk](https://github.com/apple/python-apple-fm-sdk),
[MacRumors WWDC26 dev updates](https://www.macrumors.com/2026/06/09/apple-outlines-major-ai-and-developer-tool-updates/).

## Why it lands narrow for us

Three integration paths, two are blocked:

1. **Server-side (Python backend)** — where our NANO/FAST calls run today — is a **dead end**.
   The prod backend ships as **Linux x86 containers on Hetzner**
   (`docker-compose.prod.yml` → `ghcr.io/jeffeharris/my-poker-face-backend`). Apple's Python
   SDK needs an Apple Silicon Mac + Xcode, so AFM cannot run there. (A Mac-mini inference node
   is theoretically possible but is real infra for a near-free task — see Non-goals.)
2. **iOS client** — **viable.** We have a Capacitor iOS shell (`react/react/ios/App`,
   `appId: com.mypokerface.app`, deployment target **26.1** — already iOS 26+) wrapping the
   React WebView. A Swift Capacitor plugin can reach the on-device model.
3. **Web** — no on-device model in the browser. Always uses the server path.

And most "small" tasks are **server-authoritative**, so even on iOS they can't move to the device.

### Per-CallType offload table

| CallType | Tier | Output schema | Player-read | On-device? | Why |
|---|---|---|---|---|---|
| `CHAT_SUGGESTION` | Fast | `{suggestions:[{text,tone}]}` | suggestions only | **candidate** | Shown only to the requesting player, who picks one to send. Client-local UX. |
| `TARGETED_CHAT` | Fast | `{suggestions:[{text,tone}]}` | suggestions only | **candidate** | Same — client-local. |
| `POST_ROUND_CHAT` | Fast | `{suggestions:[{text,tone}]}` | suggestions only | **candidate** | Same — client-local. |
| `VICE_NARRATION` | Fast | `{narration}` | yes | no | Posts to a **shared world ticker** all players see → must be server-generated. |
| `SIDE_HUSTLE_NARRATION` | Fast | `{narration}` | yes | no | Same shared-ticker constraint. |
| `NARRATION_CLEANUP` | Nano | `{beats:[...]}` | no | no | Operates on **server game state** (dramatic-sequence beats) mid-hand. |
| `CATEGORIZATION` (emotion) | Nano | `{narrative,inner_voice}` | yes (UI) | no | Feeds **server-side emotional game state**; written/tracked server-side. |

The one genuinely client-local family is **chat suggestions**. Their fixed
`{ suggestions: [{ text, tone }] }` shape is a textbook `@Generable` fit.

## Constraints & unknowns

- **Device gating**: needs iOS 26+, Apple Intelligence **enabled**, and A17 Pro / M-series
  silicon. Use `SystemLanguageModel.default.availability` and fall back when unavailable.
- **Quality is unproven for our use**: AFM3 (~3B class) vs our current FAST tier
  (`xai/grok-4-fast` in prod, `groq/llama-3.1-8b-instant` default). Chat suggestions must be
  *witty and specific*; the small model may be blander. This is the real go/no-go signal and
  must be eval'd by eyeballing real output, not assumed.
- **No usage tracking on-device**: server calls log to `api_usage`; on-device calls won't.
  Acceptable for a spike; note as a follow-up if productionized.

## Recommendation

- Ship on-device chat suggestions on iOS **behind capability detection + a feature flag**,
  with the existing server route as transparent fallback. Net effect when it works: zero API
  cost, no network round-trip, offline-capable, private — for iOS users on recent hardware.
- Treat it as opt-in until an A/B read shows AFM3 suggestion quality is at least on par with
  the server path.
- Re-evaluate the server-side angle only if Apple ships a real Linux runtime, or if a
  Mac-mini inference node / free PCC eligibility ever makes economic sense.

## Non-goals (for now)

- Moving server-authoritative tasks (ticker narration, categorization, beat cleanup) on-device.
- Multimodal / AFM3 Core Advanced (image input) — no current need.
- Private Cloud Compute integration and a Mac-mini inference node — disproportionate infra
  for tasks already served near-free by Groq/xAI.
- Android (no equivalent first-party on-device framework here).

## Proof of concept

A thin, flag-gated spike validates path (2): a Swift Capacitor plugin
(`FoundationModelsBridgePlugin.swift`) exposes `availability()` and `suggestChat()`; a JS
bridge (`src/utils/onDeviceLLM.ts`) mirrors the existing `widgetData.ts` pattern; and the two
suggestion fetches in `src/utils/api.ts` try on-device first, falling back to the server on
any unavailability or error. See the plugin's header comment for the Xcode build steps (the
Swift target can't be built from the Linux CI container).
