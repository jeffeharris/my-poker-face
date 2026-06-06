---
purpose: Inventory of every LLM call the app makes — tier, model, sync/async classification, timeout, and prod latency — to keep the right model on the right job.
type: reference
created: 2026-06-06
last_updated: 2026-06-06
---

# LLM Call Map

A living map of every LLM call site: which **tier/model** it uses, whether it is
**action-synchronous** (a player/user is blocked on it) or **action-incidental**
(background/offline), its **timeout + fallback**, and its observed **prod
latency**. Update this when call sites, tiers, or timeouts change.

## Tiers & resolution

Four tiers. A call's tier is chosen *at its call site* by which getter builds the
`LLMClient`/`Assistant` (there is no central CallType→tier dispatch table).

Resolution order (`core/llm/settings.py`): **DB `app_settings`** row → else the
**`core/llm/config.py` default** (env-overridable). So prod can override any tier
via `app_settings` without a deploy.

| Tier | Code default (`config.py`) | Prod (`app_settings`) | Used for |
|---|---|---|---|
| DEFAULT | `openai/gpt-5-mini` | same | commentary, end-of-hand + sharp-bot narration, theme gen, image description |
| FAST | `groq/llama-3.1-8b-instant` | **`xai/grok-4-fast`** | player-read flavor: chat suggestions, vice/side-hustle narration |
| NANO | `groq/llama-3.1-8b-instant` | same | mechanical, never-read: beat cleanup, categorization |
| ASSISTANT | `deepseek/deepseek-chat` | same | coaching, personality gen, experiment design/analysis, theme |
| IMAGE | `runware/runware:400@1` (FLUX.2 dev) | same | avatar / character images |

NANO exists so the mechanical plumbing stays on the cheapest/fastest model even
when prod bumps FAST to a pricier, more characterful model (grok) for the lines
players actually read. All tiers are editable in the admin Settings UI.

Which models are **enabled** at DB init is `DEFAULT_ENABLED_MODELS` in
`config.py` (seeded into `enabled_models` by `schema_manager`): currently
`gpt-5-mini`/`gpt-5-nano`/`dall-e-2` (openai), `llama-3.1-8b-instant` (groq),
`grok-4-fast` (xai), `runware:400@1`/`runware:100@1` (runware).

### The `reasoning_effort` gotcha (latency)

`LLMClient` defaults `reasoning_effort="low"`. On a **toggleable** model
(`xai grok-4-fast`) that selects the **reasoning** variant; only `"minimal"`
selects the fast non-reasoning variant. On **gpt-5** models, effort is a native
slider (`low` reasons more than `minimal`). Latency-sensitive flavor calls
therefore **must pass `reasoning_effort="minimal"`**. Sites that do: categorizer,
chat suggestions, vice/side-hustle narration, beat cleanup, sharp-bot narration,
end-of-hand commentary.

### Timeouts & retries

| Constant | Value | Applies to |
|---|---|---|
| `INGAME_LLM_TIMEOUT_SECONDS` | 30s | player decision, sharp-bot narration |
| `TICKER_LLM_TIMEOUT_SECONDS` | 10s | vice/side-hustle ticker narration |
| `FAST_LLM_TIMEOUT_SECONDS` | 15s | chat suggestions, beat cleanup |
| `LLM_HTTP_TIMEOUT` (shared httpx) | 600s | batch/experiment fallback when no per-call timeout |

Retries are owned by the **app loop** (`client.py`, `max_retries=2` → 3 attempts,
retryable-error aware). The provider SDK clients are built with **`max_retries=0`**
so SDK retries don't *stack* on the app loop (the stack previously multiplied a
per-attempt timeout into a multi-minute wall-clock stall). The timeout passed to
`.complete()` is **per attempt**.

## Call inventory

Legend — **Sync**: player/user blocked on it in a request. **Async**: background /
fire-and-forget. **Ticker**: runs in the single shared world-tick greenlet.

### Action-synchronous (must stay blocking)
| Call | Site | Tier | Timeout / fallback |
|---|---|---|---|
| Player decision (chaos/standard/lean) | `controllers.py:1314`, `hybrid_ai_controller.py:216`, `lean_bounded_controller.py:183` | per-game | 30s / deterministic fallback action |
| Decision recovery | `controllers.py:1418` | per-game | 30s / fallback; skipped on transport error |
| Chat suggestions / targeted / post-round | `stats_routes.py` (3 routes) | FAST (minimal) | 15s / JSON error |
| Coaching reactive `ask` | `coach_assistant.py:228` | ASSISTANT | route 504/500/503 |

### In-hand flavor — must stay SYNC, but kept cheap/fast
These run synchronously on the AI turn and **cannot be made async**: the narration
has to be delivered *with* the action it describes (async would detach the line
from its hand), and beat cleanup has to finish *before* the comment can post. The
lever is the model, not the dispatch.
| Call | Site | Tier | Why sync |
|---|---|---|---|
| Sharp-bot Layer-3 narration | `tiered_bot_controller.py:4060` → `expression_generator.py` | DEFAULT (minimal) | the comment must match the action it narrates (held until ready) |
| Beat cleanup | `controllers.py` / `expression_generator.py` / `commentary_generator.py` cleanup clients | **NANO** (minimal) | reformats the narration before it can post; mechanical → cheapest model |

### Genuinely reducible (Phase 2)
| Call | Site | Why |
|---|---|---|
| End-of-hand commentary join | `game_handler.py` `commentary_complete.wait(10)` | commentary already streams per-player via callback; the 10s next-hand join is mostly dead wait and can be shortened/removed |

### Already async (the correct template)
`_run_async_narration` (fire-and-forget), proactive-coach prefetch (overlaps
think time), leave-narrative (ThreadPool), experiment analysis (bg thread),
image generation during run-outs.

### Ticker (async to users, but a stall pauses ALL sandboxes)
| Call | Site | Tier | Timeout |
|---|---|---|---|
| vice narration | `cash_mode/vice_narration.py:124` | FAST (minimal) | 10s |
| side-hustle narration | `cash_mode/side_hustle_narration.py:120` | FAST (minimal) | 10s |

Both run synchronously inside `ticker_service._run_cycle` (one greenlet for every
sandbox + the watchdogs), so a stall blocks the whole lobby tick.

### Offline / admin (single-request blast radius)
personality generation/preview, theme generation, image gen/description,
experiment design, debug replay/interrogate — all ASSISTANT/DEFAULT/IMAGE, sync to
that admin request only.

## Prod latency reference (7-day, as of 2026-06-06)

| Model | tier role | avg | min | max | cost |
|---|---|---|---|---|---|
| `llama-3.1-8b-instant` (groq) | FAST code default | 1.4s | 0.2s | 10.2s | ~free-tier |
| `grok-4-fast-non-reasoning` (xai) | FAST prod default | 2.3s | 0.6s | 5.7s | ~$0.20 in / $0.50 out per Mtok |
| `grok-4-fast-reasoning` (xai) | misconfig (now fixed) | 12.1s | 1.7s | **98.8s** | — |
| `gpt-5-mini` (openai) | DEFAULT | ~5s | — | 30s | reasoning-class; pass `minimal` |
| `runware:400@1` | IMAGE | ~23s | — | 196s | $0.0038 / image |

**FAST trade-off:** both `llama` (cheaper, ~1.4s) and `grok-4-fast-non-reasoning`
(~2.3s, more entertaining/coherent) are snappy. The 98s stalls were *only* the
reasoning variant, eliminated by forcing `reasoning_effort="minimal"`. Choice is
now quality-vs-cost, not latency.

## History

- **2026-06-06 (Phase 1):** forced `reasoning_effort="minimal"` on all FAST/DEFAULT
  flavor calls (narration, cleanup, chat, sharp-bot narration); `max_retries=0` on
  SDK clients; added `FAST_LLM_TIMEOUT_SECONDS` (15s) to FAST clients that had
  none. Set tier defaults: DEFAULT=`gpt-5-mini`, FAST=`llama-3.1-8b-instant`
  (prod overrides to grok), IMAGE=`runware:400@1`; enabled them as system models
  at DB init.
- **2026-06-06 (NANO tier):** added a 5th tier for mechanical, never-read work
  (beat cleanup, categorization) defaulting to `groq/llama-3.1-8b-instant`, so it
  stays cheap/fast even when prod points FAST at grok. Exposed in the admin
  Settings UI alongside the other tiers. Corrected the earlier plan: sharp-bot
  narration and beat cleanup must stay SYNC (the narration matches its action;
  cleanup precedes posting) — the lever was the tier, not async.
- **Phase 2 (planned):** shorten/remove the end-of-hand commentary 10s join (the
  only genuinely reducible blocker; commentary already streams per-player).
