---
purpose: Grounded narrative log of building the chart-graded preflop coach (find → nudge → drill → measure)
type: reference
created: 2026-05-31
last_updated: 2026-05-31
---

# Captain's log — the chart-graded coach (training-room worktree)

Honest record of turning the coach from a per-hand oracle into a leak loop:
diagnose your real preflop leaks against the bot's solver charts, nudge you in
the moment, let you drill the exact spot, and measure whether any of it helped.
Newest entries at the bottom. Spec: `docs/plans/COACH_CHART_LEAKS.md`.

---

## 2026-05-31 — the question that flipped the design

Started from "what about grading the *other* preflop spots — facing a raise,
3-bets?" and "would building those charts help the TieredBot too?"

The answer inverted the whole thing. The bot **already has** full
`vs_open`/`vs_3bet`/`vs_4bet` solver charts (2,535 hands each, 15 matchups, mixed
frequencies, multiple depths). It's the *source* of good facing-action charts,
not a beneficiary. The real gap was that the **coach** wasn't using them — it
graded against a crude opening-only TAG chart (`hand_ranges.OPENING_RANGES`).

So the build was wiring, not authoring: point the grader at the bot's charts.
I'd told the user the prior turn that our reference was "opening-only, can't
grade facing-a-raise" — that was wrong, and I'd been looking at the wrong chart.
Logged the correction openly.

## 2026-05-31 — the spike earned its keep twice

Before committing to a grader shape, I spiked reconstruction on 85 real seeded
hands. Two findings that would have shipped a broken feature:

1. **The 100bb reference I proposed was wrong.** The real hands are short — 58%
   ≤15bb, **zero** at 100bb. Grading short-stack play against the deep chart
   would have invented false leaks on nearly every hand. Fix: depth-aware
   selection mirroring the bot's own `_select_preflop_table` (HU / 25 / 50 /
   100bb), so we grade against what the bot would actually play at that depth.

2. **Exact-hand grouping is a lie at this volume.** 36 gradeable decisions
   fragmented into 36 distinct `(scenario, position, hand)` groups — every one
   n=1, none past the sample gate. "0 leaks → nice discipline" was a
   *sample-fragmentation lie*, not cleanliness. Pivoted the primary unit to the
   `(scenario, position)` aggregate (chart expectation normalized to the hands
   actually held). That surfaced a real leak the fold/VPIP framing structurally
   couldn't see: **open-limping from the SB (22%)** — calling where the chart
   raises-or-folds. Added a `limp` kind for it.

Lesson re-learned: validate the gate *and* the data against reality. A sensitive
grader against the wrong reference (or too-sparse grouping) still lies.

## 2026-05-31 — completing the loop

With the engine honest, built the rest of the loop:

- **Capture-forward (v123):** store the exact `preflop_node_key` at decision time
  via the bot's `build_preflop_node` from the *pre-action* state (game_handler
  already snapshots it). Backfill stays approximate; new hands are exact.
- **In-game recall:** swapped the live nudge off the old VPIP set onto the chart
  leaks — keyed on the live `(scenario, position)`, confirmed-only, throttled
  once per spot per session. The nudge changed character from "you overplay K2o"
  to "you're in the SB — you tend to limp here; raise or fold?" — fires on
  entering the recurring spot, which is when it bites.
- **Drill:** the missing action half. A stateless quiz keyed to the leak spot,
  graded against the same chart (limp reads as a leak, raise/fold as solid). No
  game engine, no state reconstruction — sidestepped Phase 3.5's hard part.
- **Measure (v124):** `coach_tips` logs every *served* proactive tip + the nudge
  that fired; `get_tip_effectiveness` joins to the decision that followed. Honest
  about being compliance, not a causal nudged-vs-not cut.

Gotcha worth noting: the dev backend auto-reload crashed twice mid-migration —
editing the migration *dict entry* before the *method* existed left an
intermediate state the reloader couldn't recover (AttributeError on the
not-yet-defined `_migrate_vNNN`). A manual `docker compose up -d backend` after
all edits landed fixed it both times. Order migration edits method-first next
time, or expect the restart.

## 2026-05-31 — release readiness

Full quick suite green (5474) + TS clean — no regressions from the shared-path
changes (the `known_preflop_leak` reshape, `analyze_player_decision`,
`coach_ask`). Surfaced effectiveness on the player review panel (self) and a new
admin "Coach Metrics" tab (global) — both honest empty states, since no coached
game has been played through the stack yet.

The one real gap I couldn't fully close without a browser / coached playthrough:
nothing has been exercised by *actually playing* a hand. Closed most of it with
an integration test that drives `_annotate_known_preflop_leak` over a real-shaped
preflop state, so the live node-build + recall path executes (not just seeded
data). The browser-only bits (drill UX, admin tile render) still need eyes once,
and the effectiveness numbers stay empty until coached games are played.
