---
purpose: Blog content plan for mypokerfacegame.com — two tracks (Devlog + Inside the Table) mined from the captain's logs, analysis reports, technical docs, and game vision
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Blog content plan

Two parallel tracks, both mined from the same source material so each
engineering story has a player-facing twin. Target: a steady biweekly series
of ~12 posts (a publishable backlog), alternating tracks.

**Sources**
- `docs/captains-log/**` — honest build narratives ("wrong turns kept in"). The voice and the story spine.
- `docs/analysis/**` — sim/eval reports. The *numbers* that make claims credible.
- `docs/technical/**` — as-built system references. The diagrams and mechanics.
- `docs/vision/**` — `GAME_VISION.md`, `FEATURE_IDEAS.md`. The positioning and north star.

**The two tracks** *(audiences confirmed with founder, 2026-06-09)*
- **Devlog → career + creator-community.** This is primarily a **credibility play** — show judgment and range, not just war stories. Honest, measured, evidence-backed.
- **Inside the Table → players.** Anything about gameplay or features. Translates the engineering into "look how alive these opponents are." Drives signups.

Many posts pair: the same system told two ways. Cross-link the twins.

**Read first:** `FOUNDER_INTERVIEW.md` (the human layer / voice) and `ORIGIN_ARC.md`
(the 2023–2026 spine). The honest framing — see the interview — is: *liked poker +
puzzles + building; spent years fighting a pure-LLM agent that was "INSANE and
terrible at poker even spoon-fed the options"; nearly gave up; the unlock was
building **shapeable, deterministic bots** and demoting the LLM to narration — which
also made the game **affordable to actually run.*** Frame employment as freelance
LLM/B2B consulting post-Yotascale-acquisition (Aug 2025), not "no job."

> **Plan changed after the founder interview.** The single strongest Devlog story
> turned out to be the **bot journey** (ChaosBot → HybridBot → TieredBot) — added
> below as **flagship A0**. Posts A4 and A6 are now *chapters* of it, not standalone
> peers. See the new "Devlog flagship" section.

---

## Recommended publish order (biweekly, alternating)

| # | Track | Working title | Why this slot |
|---|-------|---------------|---------------|
| 1 | Inside the Table | **Poker where the opponents are alive** | Anchor/manifesto. Sets the premise the whole blog rests on. |
| 2 | **Devlog** | **Why I gave up on the LLM playing poker** (A0) | **New flagship.** The judgment arc — credibility piece. ChaosBot→HybridBot→TieredBot. |
| 3 | Inside the Table | **Your opponents remember you** | Strongest player hook; differentiates from every other poker app. |
| 4 | Devlog | **Four wrong turns on launch day** | Relatable, high share potential. (Reframe: a major update to an *already-live* site, not a first launch.) |
| 5 | Inside the Table | **Trash talk that actually lands** | Fun, screenshottable, shareable. |
| 6 | Devlog | **The bot that learned to beat the calling station** | Chapter 2 of A0. Data-rich, big technical ceiling. |
| 7 | Inside the Table | **Reputation: how a table treats a known villain** | Deepens the "living world" promise. |
| 8 | Devlog | **Making an AI hard to read — with no human to test against** | Chapter 3 of A0. Clever, self-contained. |
| 9 | Inside the Table | **The Main Event: AIs that leave for glory** | Feature spotlight; a living circuit. |
| 10 | Devlog | **A double-entry ledger for a game economy** | Serious-engineering credibility piece. |
| 11 | Inside the Table | **A coach that grades you against the charts** | Converts the "I want to get better" crowd. (Honest origin: a "why play twice?" experiment.) |
| 12 | Devlog | **WebSocket bugs you only see in production** | Practical, war-story, evergreen SEO. |

Backlog (rotate in): *Migrating a 4-month-stale schema without losing a chip*; the
feature-flag registry; run-out presentation sequencer; desktop↔mobile parity.

Backlog / overflow (pull forward if a topic lands): feature-flag registry,
run-out presentation sequencer, desktop↔mobile parity, the chip-economy
wealth levers.

---

## Devlog flagship (new — from the founder interview)

### A0. Why I gave up on the LLM playing poker
- **Source:** `FOUNDER_INTERVIEW.md` (primary — this story lives in his head, not the captain's logs); `analysis/CHAOSBOT*`, `HYBRID_*`, `BOUNDED_*`, `TIERED_BOT_ARCHITECTURE.md`, `analysis/PROMPT_BLOAT_EXPERIMENT_REPORT.md` (the experiments-platform evidence).
- **Why it's the flagship:** It's the most honest, most resonant story in the set, and it's the credibility piece — it shows the *judgment* arc, not just a bug fix. It's the "I spent years on the obvious approach, proved it doesn't work with data, and changed my mind" post that the career/creator audience respects.
- **The arc (his three architectures):**
  1. **ChaosBot** — one big LLM prompt. Great demo, statistically random: AA played 40%, 22 played 57%, uniform across hands; fell apart across models; "emotional and dramatic," not strategic. *And the measurement was suspect — the same equity calc made and graded the decisions.*
  2. **HybridBot** — poker logic narrows the LLM to a bounded "choose-your-own-adventure" menu. Slightly better, still folds the nuts — and the realization that kills it: *"I was just making the decision for the LLM and trying to get it to pick what I wanted. Which defeated the purpose."*
  3. **TieredBot** — decisions/weights/exploitation go fully deterministic; **the LLM is demoted to narration.** The payoffs: the game becomes *affordable to actually play*, and now sim harnesses, multi-table tournaments, and shapeable AIs are possible.
- **The kicker:** nearly built a GTO solver (~$50k compute; plan doc still in the repo "just in case") before realizing **an unbeatable bot isn't fun — people want to find leaks.** That line is the thesis of the whole game.
- **Honesty note:** this cuts against the AI-hype grain ("I removed the LLM from the decision") — which is exactly why it lands with a technical audience.
- **A4 and A6 become chapters of this** (or tight follow-ups): A4 = "how the deterministic bot actually beats a calling station," A6 = "making that bot hard to read." Sequence A0 → A4 → A6 as a mini-arc.

---

## Track A — Devlog

### A1. Four wrong turns on launch day
- **Source:** `captains-log/development/launch-day-cutover-and-four-wrong-turns.md`
- **Angle:** Launch worked — but the path there was four confident misdiagnoses, each corrected only by *doing the thing* instead of theorising (isolate the DB file, drive a real browser, read the code, sample RSS). "When you're sure, that's exactly when to go look."
- **Beats:** the corruption that wasn't (dev-box WAL clobbering prod) → "stale service worker" that was a CSP bug (reproduced in a private tab) → the one-at-a-time flag audit → two theories the code/agent killed. The recurring villain: deploying from a dev box.
- **Evidence/assets:** the 5.5 GB-from-135 MB backup detail; the one-line nginx CSP fix.
- **CTA:** "We build in the open — read the rest of the devlog."

### A2. The bot that learned to beat the calling station
- **Source:** `captains-log/lookup-tables/keystone-regplus.md`, `eval-harness-and-exploitation.md`, `skill-spectrum-and-sizing-defense.md`; `analysis/TIERED_VS_RULE_BOTS_REPORT.md`, `CASEBOT_EXPERIMENT_REPORT.md`; `technical/TIERED_BOT_ARCHITECTURE.md`.
- **Angle:** You can't prove a bot is good against a pool of fish — fish-hunting and real skill look identical. So before the "smart" bot, we built a *competent opponent* that punishes "play 95% and call down." The twist: balancing the bot made it worse; the win was extract-like-a-maniac-but-add-a-fold-button.
- **Beats:** the eval blind spot → RegPlus baseline losing 126 bb/100 to a calling station (backwards from real poker) → the asymmetry fix (when it overbets the station pays; when the station overbets it folds) → measured result.
- **Evidence/assets:** the bb/100 tables from the eval report; the 3-layer architecture diagram.
- **Pairs with:** B1 (same system, the "personality" lens).

### A3. Making an AI hard to read — with no human to test against
- **Source:** `captains-log/lookup-tables/river-readability-and-the-adaptive-reader.md`; `technical/TIEREDBOT_DECISION_QUALITY.md`, `POSTFLOP_OVERRIDES.md`.
- **Angle:** "Hard for humans" sounds unmeasurable with no human data — until you realise theory tells you exactly what a thinking opponent exploits: a bet size that leaks strength, a size that's never a bluff, over-folding. All measurable from the bot's own decisions.
- **Beats:** the reframe that unblocked it → the tell-map instrument → the finding: the *river* is the one face-up leak (turn was already balanced) → fixing it without spewing.
- **Evidence/assets:** the per-(street, size) bluff-vs-value composition chart; GTO bluff target `s/(1+2s)`.

### A4. A double-entry ledger for a game economy
- **Source:** `captains-log/development/chip-custody-cutover.md`, `chip-custody-atomic-writes.md`; `technical/CHIP_CUSTODY_LEDGER.md`, `CASH_MODE_ECONOMY.md`.
- **Angle:** A closed economy means chips can't appear or vanish — so we made the ledger the single source of truth and derived every balance from it. The story is the bugs that proved why: phantom chips, a disproven hypothesis, a deadlock that reshaped the plan, and the reconcile safety net that caught mints.
- **Beats:** int↔derived divergence → atomic-write unit-of-work → the deadlock → conservation invariant + audit.
- **Evidence/assets:** the conservation-invariant formula; a drift-caught-by-alarm anecdote.

### A5. WebSocket bugs you only see in production
- **Source:** `captains-log/websocket-review/2026-06-07-websocket-hardening.md`, `bug-fix-tournament/2026-06-07-two-hand-flicker.md`; `technical/RATE_LIMITING.md`, `FRONTEND_RENDERING.md`.
- **Angle:** Three real production socket bugs and what each taught: a missing effect teardown leaked a second socket per game ("two hands flickering"); `async_mode=threading` under a gevent worker dropping frames; a "frozen" game that was just a rate-limited poll.
- **Beats:** symptom → misread → root cause → the server-side hardening pass (frame-version guard, rate-limit, default error handler).

### A6. Migrating a 4-month-stale schema without losing a chip
- **Source:** `captains-log/tournaments/schema-drift-and-migration-path.md`, launch-day log (migration section); memory: schema-squash (PR #236/#241).
- **Angle:** The v70→v151 jump everyone feared was a non-event — because of prep: a dry-run on a WAL-safe copy of the *live* DB, a completeness gate, an applied-set loader that killed worktree collisions. Then we squashed 8,300 lines of migration chain into a generated baseline (and learned baselines drop seed rows).
- **Beats:** the fear → the dry-run harness → the gate → the squash + the seed-row lesson.

### A7. (backlog) Feature flags: from scattered env vars to a registry
- **Source:** `captains-log/featuring-flags/2026-06-07-feature-flag-registry.md`; `technical/FEATURE_FLAGS.md`.
- **Angle:** Launch day had a silent-inert-flag dependency bug. The fix was a central registry with lifecycle stages and per-env defaults. Short, practical, links back to A1.

---

## Track B — Inside the Table

### B1. Poker where the opponents are alive (anchor / manifesto)
- **Source:** `vision/GAME_VISION.md`; `technical/PSYCHOLOGY_OVERVIEW.md`, `EMOTION_AND_PRESSURE_ARCHITECTURE.md`.
- **Angle:** Most poker apps give you a difficulty slider. We give you characters — with moods that shift, traits that evolve, and memories that accumulate. "The best hand doesn't always win; sometimes the best story does." This is the post every other Inside-the-Table piece links back to.
- **Beats:** drama over math → an Eeyore who turns aggressive after a heater → the table as a living world.
- **Assets:** the existing landing screenshots; an emotion/zone diagram.

### B2. Your opponents remember you
- **Source:** `captains-log/dossiers/opponent-dossier-progression.md`; `technical/CROSS_SESSION_OPPONENTS.md`.
- **Angle:** Sit down a second time and the table already knows you. AIs accumulate behavioral reads *across games* — and you can scout them back, earning a dossier section by section (grind it out or buy the intel from an informant).
- **Beats:** the read that persists → the dossier UI (PROFILE / BEHAVIORAL INDEX / STANDING / TRACK RECORD) → scouting as a meta-game.
- **Pairs with:** A2/A3 (the reads are real, here's how they're computed).

### B3. Trash talk that actually lands
- **Source:** `captains-log/temperament/social-temperament-and-sarcasm.md`; `technical/SOCIAL_DYNAMICS.md`.
- **Angle:** Needle a tilting player and it stings; needle a stoic one and it bounces. Every character receives trash talk *in character* (energized / stung / stoic) and it moves the durable relationship between you.
- **Beats:** the same jab, three receptions → a gloat at a tilter is the sharpest social move in the game → relationships that carry.
- **Assets:** quick-chat screenshots; a before/after relationship-axis readout.

### B4. Reputation: how a table treats a known villain
- **Source:** `captains-log/renown/renown-v2-balance.md`, `renown-figure-cut-and-regard.md`; `captains-log/prestige/player-prestige-scoreboard.md`; `captains-log/regard/neutral-rebaseline.md`.
- **Angle:** Win loud and the room learns your name. Renown makes some players *figures*; bad behavior can mint an earned villain — and the table's demeanor toward you actually shifts (chat tone, who'll back you, who wants the seat).
- **Beats:** earning renown → figure vs villain → the world responds (kill-switch honesty: it's a real, tunable system).

### B5. The Main Event: AIs that leave the table for glory
- **Source:** `captains-log/tournaments/tournaments-as-a-draw.md`, `multi-table-tournament-engine.md`; `technical/TOURNAMENTS.md`.
- **Angle:** The cash table isn't the whole world. A live circuit pulls characters away — they'll vacate a cash seat for a shot at a prize and the renown of a title. The lobby feels like a scene that exists whether you're playing or not.
- **Beats:** reserve → vacate → spawn → the prize+renown draw → a headless engine running the field.

### B6. A coach that grades you against the charts
- **Source:** `captains-log/training-room/chart-graded-coach.md`; `technical/COACH_SYSTEM.md`, `COACH_PROGRESSION_ARCHITECTURE.md`.
- **Angle:** A practice mode that finds your preflop leaks against real charts, nudges you, drills the spot, and measures whether you fixed it — find → nudge → drill → measure.
- **Beats:** the leak it caught → the drill → the measured improvement.
- **Assets:** range-grid screenshots (already in `react/.../screenshots/`).

### B7. (backlog) The personalities, up close
- Character-spotlight format using `technical/PERSONALITY_ANCHORS.md` + `personalities.json`. Evergreen, repeatable, low-effort once templated.

---

## Production notes

- **Lead with the screenshot.** Several assets already live in `react/react/src/assets/screenshots/` (range-explorer, preflop-leaks, coach-tip, desktop-table) and `.images/`. Inside-the-Table posts should open visual.
- **Honesty is the brand.** The captain's-log voice — "wrong turns kept in," verify-the-premise, measure-don't-guess — *is* the differentiator for the Devlog. Don't sand it into a press release.
- **Back every claim with a number.** The `analysis/` reports exist precisely so a "our bot is good" line can cite bb/100. Use them.
- **Cross-link the twins** (A2↔B1, A2/A3↔B2). Each track feeds the other's curiosity.
- **One CTA per post:** Devlog → "play it / read the rest"; Inside the Table → "start a table."
