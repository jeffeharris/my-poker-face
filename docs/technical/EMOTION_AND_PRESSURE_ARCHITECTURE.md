---
purpose: Integrating map of the three equity-driven emotion/pressure tracks and how they wire into the live game
type: architecture
created: 2026-05-29
last_updated: 2026-06-03
---

# Emotion & Pressure Architecture (Integrating Map)

> **Why this doc exists.** Several systems compute "how does this AI feel about
> what just happened" from poker equity, and they are easy to conflate. The
> individual pieces are documented elsewhere (see [Related Docs](#related-docs)),
> but no doc explained how they relate or where each one actually fires in a
> live Flask + React game. This is that map. Call sites are cited so it can be
> re-verified; line numbers were last re-verified against `game_handler.py`
> (~4600 lines) on 2026-06-03.

## TL;DR

There are **three independent tracks**, all fed by poker equity but serving
different purposes and running at different times:

| Track | Module | When | Output | Persists? |
|---|---|---|---|---|
| **1. Runout reactions** | `poker/runout_reactions.py` | During an **all-in runout**, per street | Avatar face (`avatar_emotion`) shown live in the UI | No — cleared at hand end |
| **2. Psychology pipeline** | `poker/psychology_pipeline.py` → `pressure_detector.py` → `player_psychology.py` | **After** the hand completes | Pressure events that nudge confidence/composure/energy axes | Yes — saved to DB, reloaded next hand |
| **3. Drama coloring** | `poker/moment_analyzer.py` | At **decision time**, before the LLM call | Prompt response-style text (how theatrical to be) | No — per-decision |

They share an equity foundation (eval7) but **never combine**. The only place
two of them meet is the display-emotion selector, where Track 1 strictly
*overrides* Track 2 (see [The Seam](#the-seam-display-emotion-selection)).

This was historically perceived as a single "big moment detector." It is not —
it is three systems with one common input.

```
                        poker equity (eval7)
                                 │
        ┌────────────────────────┼────────────────────────────┐
        │                        │                             │
        ▼                        ▼                             ▼
  TRACK 1                   TRACK 2                       TRACK 3
  runout_reactions          psychology_pipeline           moment_analyzer
  EquityCalculator          EquityTracker                 (no equity calc;
  (live Monte Carlo,        (.calculate_hand_              uses pot/stack
   2000 iters)               equity_history,               ratios + hand
        │                     retrospective)                strength)
        │                        │                             │
  per-street face          post-hand events               drama level →
  → avatar_emotion         → axis deltas                  prompt style text
  (UI only, ephemeral)     (persisted, shapes             (shapes the LLM
        │                   FUTURE hands)                   response, this hand)
        │                        │
        └────────► merges at ◄───┘
            display-emotion selector
        (Track 1 overrides Track 2)
```

## Track 1 — Runout Reactions (visual, real-time)

**Module:** `poker/runout_reactions.py`
**Purpose:** Show an avatar's face change in real time as the board runs out on
an all-in, mapping equity swings directly to emotions.

**Wiring (live):** Fires **only when `game_state.run_it_out` is true** — i.e. an
all-in where remaining streets are auto-dealt.

- `flask_app/handlers/game_handler.py:3957` — `compute_runout_reactions(game_state, ai_controllers)` is called once, at the hole-card reveal (inside the `progress_game` loop, gated on `not game_state.has_revealed_cards`), pre-computing the whole schedule (the deck is already shuffled, so all future cards are known).
- The schedule is stored in `game_data['runout_reaction_schedule']` (`:3960`); reactions are emitted per street via `_emit_avatar_reaction()` (`game_handler.py:222`, called at `:4048`) and at showdown (`:4070`).
- Chosen emotions are written to `game_data['runout_emotion_overrides']` (initialised empty at `:3999`; per-street writes at `:4047`/`:4049`, showdown at `:4069`/`:4073`).
- Overrides are cleared when the hand ends (`game_handler.py:3516` — `game_data.pop('runout_emotion_overrides', None)`).

**Algorithm:**

- Equity is computed with `EquityCalculator` (Monte Carlo, `EQUITY_ITERATIONS = 2000`), **not** the `HandEquityHistory` used by Track 2.
- A reaction fires when `abs(delta) >= threshold`, where the per-player threshold is personality-modified (`_get_reaction_threshold`): base `0.15`, shifted `-0.05` for volatile (high aggression / loose) and `+0.05` for stoic (low aggression / tight) personalities.
- Three emotion mappers (`runout_reactions.py`):
  - `_equity_to_initial_emotion` (`:448`) — absolute equity at reveal → `smug`/`confident`/`happy`/`nervous`/`thinking`/None.
  - `_equity_to_emotion` (`:408`) — per-street **delta** → `elated` (Δ>0.30), `angry` (Δ<-0.30), `happy`/`frustrated` (±0.18), else position-based.
  - `_equity_to_showdown_emotion` (`:475`) — final equity → `elated`/`happy`/`angry`/`frustrated`.

**Important property:** This track **intentionally bypasses the dimensional
EmotionalState model** (see the module docstring, `runout_reactions.py:9-12`). It
is a fast, ephemeral UI overlay. It does **not** touch the psychology axes and
has **no pot-significance gating** — any sufficiently large equity swing produces
a face, regardless of pot size.

### Delivery & timing (how the face actually reaches the client)

This is subtle and was historically buggy (fixed 2026-05-29). The reaction
emotion travels to the browser on **two** channels, and only one of them is
real-time:

1. **`avatar_update` socket event** (`_emit_avatar_reaction`, `game_handler.py:222`)
   — emitted the instant a reaction fires, during the run-out's per-street holds.
   Carries `is_reaction: true`. The frontend handler (`usePokerGame.ts` ~`:641`)
   applies the emotion **immediately** for `is_reaction` payloads. (For payloads
   *without* the flag — late-arriving generated avatar images — it deliberately
   does **not** change the displayed emotion, to avoid clobbering a face that has
   since moved on.)
2. **Full game-state push** (`update_and_emit_game_state`, `:515`) — carries
   `player_dict['avatar_emotion']` derived from `runout_emotion_overrides`
   (`:574-587`). This only fires at the **top of the progress loop**
   (`update_and_emit_game_state(game_id)`, `:3923`), i.e. at the *start of the
   next street*.

> **Historical bug (fixed).** The `avatar_update` handler was originally written
> only for async image generation and did **not** apply `avatar_emotion` for
> reactions. That left channel #2 as the only emotion carrier — so each street's
> face arrived one street late ("off a beat"), and the showdown face was pushed
> in the same loop iteration that emits `winner_announcement`, so it was instantly
> covered by the hand-over screen ("cut off"). The `is_reaction` flag closes this
> by making channel #1 authoritative. Note the run-out block itself emits **no**
> full game-state push (`:3944-4091` only call `game_state_service.set_game`,
> which writes the in-memory service, not the socket).

## Track 2 — Psychology Pipeline (behavioral, post-hand)

**Modules:** `poker/psychology_pipeline.py` (orchestrator) →
`poker/pressure_detector.py` (detectors) → `poker/player_psychology.py` (axis
application). Events catalog: `docs/technical/PRESSURE_EVENTS.md`.

**Purpose:** Decide how the hand's outcome should change each AI's internal state
(confidence / composure / energy), which in turn shapes how they play **future**
hands.

**Wiring (live):** Runs synchronously inside `handle_evaluating_hand_phase`
(`game_handler.py:3140`; reached from the progress loop's `elif` when the phase
is `EVALUATING_HAND`, `:4098`), **after** the winner announcement is emitted
(`:3333`) and **before** async commentary.

- Detector instantiated per game: `PressureEventDetector()` in `flask_app/routes/cash_routes.py:883`, `flask_app/routes/game_routes.py:686` and `:1717`, stored as `game_data['pressure_detector']`.
- Equity history is built retrospectively: `EquityTracker().calculate_hand_equity_history(hand_in_progress)` at `game_handler.py:3273`, **only if** `hand_in_progress.hole_cards` exists. This produces a `HandEquityHistory` (see `poker/equity_snapshot.py`) of per-street snapshots.
- The pipeline runs at `game_handler.py:3344-3406` — gated on `'pressure_detector' in game_data and ai_controllers` (`:3344`). Stages (`psychology_pipeline.py:69`, `process_hand` at `:108`): **detect → resolve → persist → callback → update_composure → recover → save.**

**What gets detected** (`psychology_pipeline._detect_events`, `:236`) — six categories, not just equity:

1. **Showdown events** (`detect_showdown_events`, `:267`) — win/loss/bluff/etc. Always runs.
2. **Equity-shock events** (`detect_equity_shock_events`, `:275`) — `bad_beat` / `cooler` / `suckout` / `got_sucked_out`. **Gated** on `ctx.equity_history and ctx.equity_history.snapshots and ctx.hand_start_stacks` (`:273`). This is the classic "big moment" detector. Full spec: `docs/technical/EQUITY_PRESSURE_DETECTION.md`.
3. **Stack events** (`detect_stack_events` + short-stack survival, `:288`/`:298`).
4. **Streak events** (`detect_streak_events`, `:309`) — from DB-backed session stats.
5. **Nemesis events** (`detect_nemesis_events`, `:331`).
6. **Big-pot involvement** (`:340-342`) — pressure/fatigue for everyone in a big pot.

**Equity history has a second consumer.** The same `equity_history` is also
forwarded to `memory_manager.on_hand_complete(...)` (`game_handler.py:3288`)
so the **relationship layer** can fire `BAD_BEAT` events — the only relationship
event needing pre-river equity.

**Persistence:** The pipeline is constructed with `persist_controller_state=False`
(`:3227`) because the game handler persists psychology itself, **per-decision**:
after each AI acts, `game_repo.save_controller_state(...)` writes
`controller.psychology.to_dict()` into the `controller_state.psychology_json`
column (`game_handler.py:4501`; deferred emotional-narration prose is flushed the
same way at `:2813`). The old `game_repo.save_emotional_state(...)` call and the
standalone `emotional_state` table were **removed in schema v136** (the table is
dropped in `poker/repositories/schema_manager.py:_migrate_v136_drop_4d_emotion`,
migration registered at `:2235`). On game restore, psychology is rehydrated from
`psychology_json` via `PlayerPsychology.from_dict(...)` (`game_handler.py:479`);
narrative/inner_voice now ride along in that same JSON (`:493-495`). Pressure
events themselves are written via `PressureEventRepository`.

> **Note — two different "emotional state" columns.** The live Flask path above
> uses `controller_state.psychology_json`. The off-screen sim/cash-persona path
> is separate: it persists psychology to `ai_bankroll_state.emotional_state_json`
> via `bankroll_repository.save_emotional_state_json` (`bankroll_repository.py:219`,
> added v97). Both survive the v136 table drop; neither is the retired
> `emotional_state` *table*.

## Track 3 — Drama Coloring (prompt-shaping, decision-time)

**Module:** `poker/moment_analyzer.py`. Documented in `poker/CLAUDE.md` ("Drama
Detection System").

**Purpose:** Tell the LLM *how theatrical* to be for the decision it is about to
make. It does **not** display a face or change axes — it produces prompt text.

- Detects boolean factors (`all_in`, `big_pot`, `big_bet`, `showdown`, `heads_up`, `huge_raise`, `late_stage`) from game state — **no equity simulation**, just pot/stack ratios and hand strength.
- Maps to a level (`routine` / `notable` / `high_stakes` / `climactic`) and tone, which `prompt_manager.py` turns into `DRAMA_CONTEXTS` response-style instructions.
- Consumed in `poker/controllers.py` (prompt assembly), `poker/tiered_bot_controller.py`, `poker/memory/commentary_generator.py`, and `poker/memory/hand_outcome_detector.py`.
- Also note: `pressure_detector.py` reuses `MomentAnalyzer.is_big_pot()` so Track 2's "big pot" threshold stays consistent with Track 3's.

## Narration: the surviving role of `emotional_state.py`

`poker/emotional_state.py` is **not** a fourth track — it produces no face and
changes no axis. It is the **prose layer that rides on Track 2**: given the
quadrant state Track 2 already computed (`confidence × composure`), it makes one
cheap LLM call to render two display strings and nothing else. After the 4D
dimensional model (valence/arousal/control/focus) was retired in schema v136,
this is the module's *only* remaining job — see the class docstrings at
`emotional_state.py:96-99` and `:151-160`, and the rationale in
`PSYCHOLOGY_OVERVIEW.md:110-130`.

> **Design origin (planning doc, not code).** `docs/plans/POKER_NATIVE_PSYCHOLOGY.md`
> (undated, pre-header) called for exactly this — it lists "`poker/emotional_state.py`
> — Simplify: derive from 2 traits", i.e. derive the emotion from the
> confidence × composure quadrant and reduce the module to surface text. The
> current narration-only shape is that plan realized. (Plan claim; the code above
> is the verification.)

**Two output strings** (the `EmotionalState` dataclass, `emotional_state.py:91`):

| Field | Person | Meaning |
|---|---|---|
| `narrative` | third | A concrete physical tell an opponent would catch |
| `inner_voice` | first | One sharp thought in the character's own voice |

Plus metadata: `generated_at_hand`, `source_events`, `created_at`,
`used_fallback`. `to_dict()`/`from_dict()` are deliberately tolerant of legacy
rows that still carry the dropped 4D scalar keys — they are silently ignored
(`emotional_state.py:126-140`).

**Generator + LLM tier.** `EmotionalStateGenerator` (`emotional_state.py:151`)
holds a single `StructuredLLMCategorizer` built from
`EMOTIONAL_NARRATION_SCHEMA` — two string fields, no scalars
(`emotional_state.py:29-49`). `StructuredLLMCategorizer.categorize` always issues
`CallType.CATEGORIZATION` (`core/llm_categorizer.py:272`), which maps to the
**Fast** tier (small/cheap model). Default timeout is **3.0s**
(`EmotionalStateGenerator.__init__`, `emotional_state.py:171`). On LLM failure
the generator sets `used_fallback=True` and returns a fixed generic line
(`_generate_narration_fallback`, `emotional_state.py:376-378`); if the whole call
raises, `PlayerPsychology._generate_emotional_state` catches it and installs its
own *quadrant-keyed* static prose (`player_psychology.py:1737-1742`).

### Where it fires (it's the async half of the post-hand pipeline)

Track 2's `_update_composure` (`psychology_pipeline.py:348`) splits the post-hand
work into a **synchronous play-affecting half** (`update_composure_only`,
`:423`) and a **deferred display-only half** (narration). The narration jobs are
*not* run inline — they are gated, packaged into `NarrationRequest` objects, and
returned for the caller to run off the critical path.

> **Why the split (architecture rationale).** Composure affects the next hand, so
> it must settle before play continues; the narration is display-only, so its LLM
> latency must **not** gate the next hand. The `defer_narration=True` flag exists
> for exactly this — see the Track 2 wiring above and the TL;DR table.

The deferred path, step by step:

1. `game_handler.handle_evaluating_hand_phase` constructs the pipeline with
   `enable_emotional_narration=True, defer_narration=True`.
2. `_update_composure` runs `update_composure_only` synchronously, then **gates**
   narration (next section) and appends a `NarrationRequest` per surviving player
   to its `pending` list (`psychology_pipeline.py:453-454`).
3. If `pending` is non-empty, the handler dispatches
   `socketio.start_background_task(_run_async_narration, ...)`.
4. `_run_async_narration` (`game_handler.py:2790`) calls
   `controller.psychology.generate_narration(**req.kwargs)` and **persists the
   result itself** via `game_repo.save_controller_state(...)`
   (`:2813-2818`) — the old synchronous emotional-state save was removed with the
   deferral.
5. `generate_narration` (`player_psychology.py:866`) → `_generate_emotional_state`
   (`:1704`) → `_emotional_generator.generate(...)` (`:1722`).

### Who gets narration (the `USES_EMOTIONAL_NARRATION` gate)

Narration is generated only for players who actually **consume** the prose. The
gate (`psychology_pipeline.py:439-443`) is: `enable_emotional_narration` is on
**AND** (`controller.USES_EMOTIONAL_NARRATION` is truthy **OR** the table is
heads-up). Heads-up is `ai_seat_count <= 1` (`:368-369`) — the lone AI's prose is
shown in the React opponent panel regardless of its bot type.

| Controller class | `USES_…` | Why |
|---|---|---|
| `AIPlayerController` (chaos) | `True` (`controllers.py:639`) | Injects prose into decision prompt |
| `HybridAIController` (standard) | inherits `True` | Same |
| `LeanBoundedController` (lean) | `False` (`lean_bounded_controller.py:62`) | Options-only prompt; prose unread |
| `TieredBotController` (sharp) | `False` (`tiered_bot_controller.py:231`) | Solver path; prompt unread |
| `RuleBotController` (casebot/gto_lite) | `False` (`rule_bot_controller.py:49`) | Pure rules; prompt unread |

### Where the two strings are consumed

1. **Decision-prompt injection** (chaos / standard). Gated on
   `prompt_config.emotional_state`, `controllers.py` prepends
   `psychology.get_prompt_section()` to the decision message
   (`controllers.py:1078-1081`). That section
   (`player_psychology.get_prompt_section`, `:1226`) returns `""` when the player
   `is_severely_tilted` (composure < 0.4) — the zone-effects pathway voices the
   tilt instead, to avoid double-voicing — otherwise emits a
   `[YOUR EMOTIONAL STATE]` block carrying `narrative`, the quadrant feeling, the
   composure category, the energy band, and `inner_voice`.
2. **Heads-up opponent panel** (React). `game_handler.py:622-623` copies
   `psych.emotional.narrative` / `.inner_voice` into `player_dict['psychology']`
   on the game-state push; `HeadsUpOpponentPanel.tsx:134-137` renders them
   (`emotional-narrative` and `inner-voice`), shown only when at least one is
   non-empty.

### Persistence (two columns, both survive v136)

The strings ride **inside** existing psychology blobs — there is no dedicated
table after v136.

| Scope | Column | Path |
|---|---|---|
| Live game session | `controller_state.psychology_json` | `PlayerPsychology.to_dict()` nests `emotional` under key `'emotional'` (`player_psychology.py:1603`); rehydrated by `from_dict` (`:1679-1680`). Written by `game_repo.save_controller_state` |
| Cash persona continuity | `ai_bankroll_state.emotional_state_json` (v97) | `cash_mode/psychology_persistence.py` `flush_persona_psychology` / `hydrate_persona_psychology` via `bankroll_repository.save_emotional_state_json` |

The v136 migration (`schema_manager.py:_migrate_v136_drop_4d_emotion`) dropped the
standalone `emotional_state` *table* and the 4D columns from
`player_decision_analysis`; it did **not** touch either column above.

> **Known gap (log, not code).** `docs/captains-log/tournaments/persona-identity-and-psychology-continuity.md:53-74`
> records gap T3-77: live games historically built every opponent at baseline
> (never reading the persona blob) and never wrote evolved mood back, leaving the
> lobby-sim ↔ live-game ↔ lobby-sim emotional loop open. The
> `psychology_persistence.py` helpers were built to close it; the log indicates
> the live-game-side wiring was incomplete at the time of that entry. Treat the
> closure status as unverified here.

## The Seam: Display-Emotion Selection

The single place where tracks meet is the per-player display-emotion selector in
the game-state push, `flask_app/handlers/game_handler.py:574-586`:

```python
# Run-out reaction overrides take priority over baseline emotion
runout_overrides = current_game_data.get('runout_emotion_overrides', {})
if player_name in runout_overrides:
    display_emotion = runout_overrides[player_name]                # Track 1 wins
elif controller.psychology is not None:
    display_emotion = controller.psychology.get_display_emotion()  # Track 2 baseline
else:
    display_emotion = 'confident'                                  # Default for RuleBots
...
player_dict['avatar_emotion'] = display_emotion
```

So the face the player sees (`avatar_emotion`, rendered as `data-emotion` on the
avatar in React `components/game/WinnerAnnouncement/WinnerAnnouncement.tsx:309,405`) is:

- **During an all-in runout** → Track 1's per-street emotion.
- **Otherwise** → Track 2's *baseline* `get_display_emotion()` (`player_psychology.py:1576`), which returns `poker_face` inside the poker-face zone, else the true emotion dampened by the `expressiveness` anchor and `energy` axis via the expression filter.

The two are never blended — Track 1 is a hard override while it is present.

## Architectural Seams & Things To Know

These are not bugs to fix blindly; they are intentional-or-incidental design
edges worth understanding before changing anything.

1. **Two separate equity engines.** Track 1 uses `EquityCalculator` (live Monte
   Carlo) and Track 2 uses `EquityTracker.calculate_hand_equity_history`
   (retrospective). Both wrap eval7 but are computed independently, at different
   times, and can disagree at the margins. There is no shared cache.

2. **Track 1 only exists for all-in runouts.** A normal showdown (no
   `run_it_out`) produces **no** runout reactions; the displayed face there comes
   entirely from Track 2's baseline `get_display_emotion()`. If "showdown
   emotions feel flat on non-all-in hands," that is why.

3. **Display vs. event divergence on small pots.** Track 1 reacts per-street on
   `abs(delta) >= ~0.10–0.20` with **no pot gating**. Track 2's equity-shock
   detector requires `weighted_delta = delta × pot_significance × street_weight`
   to reach `±0.30` (`pressure_detector.py:298,316,330`), where
   `pot_significance = pot_size / player_start_stack` is computed **per player**
   (`:274`). Consequences:
   - A deep-stacked caller in an all-in (low `pot_significance`) can show a
     *devastated face* (Track 1) while firing **no** psychology event (Track 2) —
     looks crushed, doesn't tilt — even as the short-stack shover in the same
     hand gets both.
   - **The explicit `POT_SIGNIFICANCE_MIN = 0.15` gate (`:275`) is effectively
     dead code.** The weighted-delta formula already imposes a stricter implicit
     floor: even a near-total river swing (`delta ≈ 0.85`, weight `1.4`) needs
     `pot_significance ≥ 0.25` to fire; a flop swing needs `≥ 0.35`. So the
     nominal `0.15` never binds — lowering it changes nothing. Tune the weighted
     threshold or `pot_significance` handling instead.
   - `pot_significance` is an **unclamped** linear multiplier, so a big multiway
     pot (`pot_significance` 2–3×) fires events on trivial `0.10` equity wobbles
     (`0.10 × 3 × 1.4 = 0.42`). Arguably noise. (See `EQUITY_PRESSURE_DETECTION.md`.)

4. **Track 1 is ephemeral; Track 2 is durable.** Runout overrides are wiped at
   hand end and never feed the axes. Only Track 2 changes how the AI plays later.

5. **Silent degradation.** If equity history fails to build (e.g. missing
   hole cards), the equity-shock branch is simply skipped (the gate at
   `psychology_pipeline.py:273`, wrapped in try/except at `:282`). The hand still
   resolves; you just lose the bad_beat/cooler/suckout events with only a
   warning log.

## Where Each Piece Lives (quick index)

| Concern | File |
|---|---|
| Live runout face computation | `poker/runout_reactions.py` |
| Runout emission + display selector | `flask_app/handlers/game_handler.py` (`:222`, `:574`, `:3957`) |
| Post-hand orchestration | `poker/psychology_pipeline.py` |
| Pressure event detectors | `poker/pressure_detector.py` |
| Axis application / display baseline | `poker/player_psychology.py` (`get_display_emotion` `:1576`) |
| Retrospective equity history | `poker/equity_tracker.py`, `poker/equity_snapshot.py` |
| Equity math (eval7) | `poker/equity_calculator.py` |
| Decision-time drama | `poker/moment_analyzer.py` |
| Detector wiring per game | `flask_app/routes/{cash,game}_routes.py` |

## Related Docs

- `docs/technical/EQUITY_PRESSURE_DETECTION.md` — spec for the equity-shock detector (Track 2's coolers/suckouts/bad-beats).
- `docs/technical/PRESSURE_EVENTS.md` — full catalog of pressure events, axis impacts, resolution.
- `docs/technical/PSYCHOLOGY_OVERVIEW.md` / `PSYCHOLOGY_DESIGN.md` / `PSYCHOLOGY_ZONES_MODEL.md` — the axes/zones model Track 2 feeds; `PSYCHOLOGY_OVERVIEW.md:110-130` documents the v136 4D retirement that left `emotional_state.py` narration-only.
- `docs/plans/POKER_NATIVE_PSYCHOLOGY.md` — planning doc (undated) that called for simplifying `emotional_state.py` to derive emotion from the 2-trait quadrant.
- `docs/technical/PRESSURE_STATS_SYSTEM.md` — the `pressure_stats` UI aggregation fed by the pipeline callback.
- `poker/CLAUDE.md` — drama detection (Track 3) reference, kept next to the code.
