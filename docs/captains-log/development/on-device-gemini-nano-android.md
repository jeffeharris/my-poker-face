---
purpose: Narrative log of porting on-device chat suggestions to Android — Gemini Nano via ML Kit GenAI, behind the same bridge contract as iOS Foundation Models
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

# On-device chat suggestions on Android (Gemini Nano) — captain's log

## The goal
iOS shipped on-device chat suggestions yesterday via Apple Foundation Models
(`FoundationModelsBridgePlugin.swift` + `onDeviceLLM.ts` + the server-composes-parity
routing in `api.ts`). The ask: do the same on Android with **Gemini Nano**, routing the
*same* JS path — no duplicate frontend. The feasibility doc had listed Android as a
non-goal ("no equivalent first-party on-device framework"); ML Kit's GenAI **Prompt API**
is exactly that equivalent, so the non-goal was simply out of date.

## The shape (why it's small)
The win was the existing abstraction. `onDeviceLLM.ts` does
`registerPlugin('FoundationModels')` and `api.ts` already tries on-device-then-server.
So Android needed **zero JS changes** — just a Kotlin plugin registering under the same
`FoundationModels` jsName (the class is honestly named `OnDeviceLLMPlugin`; only the bridge
id is shared), implementing `availability()` / `suggestChat()` / `prewarm()` against Gemini
Nano. Apple Foundation Models on iOS, Gemini Nano on Android, one contract, and the WebView
can't tell the difference.

## The build chain (each failure taught the next fix)

**1. minSdk 24 → 26.** Research said `genai-prompt` needed API 24; the manifest merger said
26. Bumped the floor (Android 8.0 — low-single-digit-% of devices in 2026).

**2. Kotlin too old.** The artifacts ship **Kotlin 2.2.0** metadata; the Kotlin Gradle
plugin was pinned at 1.9.24, which can't read them ("incompatible version of Kotlin… up to
2.0.0"). Bumped the Kotlin plugin to 2.2.0 (+ coroutines 1.9.0); AGP 8.7.3 / Gradle 8.9
take it fine.

**3. The API wasn't what the docs implied.** `generateContentRequest(TextPart(...))` didn't
resolve, and `response.candidates`/`.text` were unresolved. Rather than guess again, I
**`javap`'d the actual jar** out of the Gradle cache — the source of truth beats any blog:
   - `Generation.getClient()` → `GenerativeModel` ✓
   - `checkStatus()` returns a plain **`Int`** compared to `FeatureStatus` int constants (an
     `@IntDef`, not an enum)
   - there's a **`generateContent(String)`** overload — no `GenerateContentRequest`/`TextPart`
     needed for plain text
   - the response is `GenerateContentResponse.candidates: List<Candidate>`, `Candidate.text`
   - bonus: a **`warmup()`** suspend fn — a cleaner prewarm than download-only
   Rewrote against the verified surface → **BUILD SUCCESSFUL**, 18.2 MB APK.

*Lesson: for an alpha SDK, the compiled artifact + `javap` is faster and more honest than
docs or model guesses. Two failed builds of guessing; one `javap` and it compiled.*

## No `@Generable`, so JSON-and-parse
Apple's guided generation constrains the model to a Swift struct. Nano's Prompt API emits
free text, so the plugin instructs it to return a JSON `[{text,tone}]` array and parses it
(tolerating code fences / stray prose), **rejecting on any miss** so the JS falls back to the
server — identical "throw → server" semantics to the Swift plugin.

## What the emulator could and couldn't prove
The generic `google_apis;android-34` emulator has **no AICore / Gemini Nano**. So this
validates: the Kotlin compiles against the real API, the plugin registers
(`Capacitor: Registering plugin instance: FoundationModels` in logcat), and the app runs
clean — `availability()` (try/caught → false) makes every request fall back to the server
silently. **Actual on-device generation needs a Pixel 9/10** — the exact mirror of iOS
needing a real Apple-Intelligence device. The A/B "is Nano's banter as sharp as the Fast
tier?" read still awaits real hardware.

## A latency thread (being solved elsewhere)
The routing still makes one `render_only` **server hop** to compose the identical prompt
before generating locally. A separate branch is prefetching/caching those prompts so the
on-device call is immediate — and because the plugin generates from whatever `prompt`/`system`
it's handed, that optimization makes Android *and* iOS fully-local with **no plugin change**.

Result: **on-device chat suggestions now run the same JS path on both native platforms** —
Apple Foundation Models on iOS, Gemini Nano on Android — server fallback everywhere else,
compiling and registering green on the Android toolchain.
