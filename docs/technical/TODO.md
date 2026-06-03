---
purpose: Tracking list for technical-documentation staleness, gaps, and header compliance in docs/technical/
type: reference
created: 2026-06-03
last_updated: 2026-06-03
---

# Technical Documentation TODO

Tracks staleness, gaps, and standards-compliance for the docs in `docs/technical/`.
Findings from a code-cross-checked survey on **2026-06-03** (37 docs, verified
against `development` at schema **v140**). Each item cites the code evidence that
makes the doc wrong so a fix can be scoped without re-deriving it.

Legend: 🔴 obsolete/wrong · 🟠 stale (real drift) · 🟡 minor (counts/refs) · 🟢 fresh (no action)

---

## P1 — Obsolete or actively wrong (rewrite / archive first)

| Doc | Issue | Evidence | Action |
|-----|-------|----------|--------|
| 🔴 `README_PERSISTENCE.md` | Whole doc assumes the deleted `GamePersistence` god-object. All examples (`from poker.persistence import GamePersistence`, `persistence.save_game(...)`) are dead code. | `poker/persistence.py` deleted (T3-35/36); `save_game`/`load_game`/`list_games` now on `GameRepository` (`game_repository.py:55,123,257`). Only the schema section survives. | Rewrite as a **repositories-layer overview** (the ~30 domain repos) or delete and fold the schema bit into `SYSTEM_ARCHITECTURE.md §7`. Add header. |
| 🔴 `FRONTEND.md` | State-management section flat wrong: says React Context / `GameProvider` / `contexts/`. App uses **Zustand**; there is no `contexts/` dir. Component tree (~7 dirs) misses ~12 actual dirs (cash, auth, character, mobile, training, …). Framed "Migration Complete (Jan 2025)". | `react/react/src/stores/gameStore.ts`, `zustand ^5.0.11` in package.json; `components/` has ~19 dirs. | Rewrite state-mgmt → Zustand; regenerate component tree; drop migration-history framing. Add header. |
| 🔴 `AI_PLAYER_SYSTEM.md` | "5-trait poker-native model (tightness/aggression/confidence/composure/table_talk)" is fiction — never existed in `personalities.json`. Says OpenAI assistant. | Actual schema = `anchors` block (`baseline_aggression`, `baseline_looseness`, `ego`, `poise`, `expressiveness`, `risk_identity`, `adaptation_bias`, `baseline_energy`, `recovery_rate`, `self_belief`) + `skill`/`bankroll_knobs`/`staker_profile`. Uses `core.llm.Assistant` (`poker_player.py:7`). | Rewrite personality + trait sections against the anchors model; replace OpenAI refs. Add header (`architecture`). |
| 🔴 `AI_PROMPT_ARCHITECTURE.md` | Phantom file `core/assistants.py` (`OpenAILLMAssistant`); wrong default model ("GPT-5-nano/GPT-4o"); "45+ personas"; claims `elasticity_config`/5-trait model. | Real class `Assistant` in `core/llm/assistant.py`; default tier groq/`llama-3.1-8b-instant` (`core/llm/config.py:33-34`); 62 personas; `elasticity_config` 0 hits. Template names + psychology layering ARE still accurate. | Fix assistant class/path, default model, persona count, trait model. Add header. |
| 🔴 `EQUITY_PRESSURE_DETECTION.md` | Large block documents a removed API: `EquityTracker.calculate_showdown_equity_history` / `.detect_equity_events` / `_detect_cooler/_detect_suckout` and constants `COOLER_MIN_EQUITY`/`SUCKOUT_THRESHOLD`/`BAD_BEAT_THRESHOLD` — none exist. Repo method name + thresholds wrong; schema "v68". | `equity_tracker.py` only has `calculate_hand_equity_history`/`calculate_from_recorded_hand`; detection moved to `pressure_detector.detect_equity_shock_events`; repo method is `get_equity_history`; `find_suckouts` default 0.40 (`hand_equity_repository.py:229`); hand_equity is v69 (`schema_manager.py:59`). | Delete dead pseudocode + constants, point at `pressure_detector`, fix repo names, v68→v69. The current weighted-delta section is fine. |
| 🔴 `BETTING_UI_IMPROVEMENTS.md` | Historical changelog (✅ bullets, "Next Steps", "Screenshots"); zero file/symbol refs to maintain. | — | **Archive** to `docs/archive/` (or delete). Not a living doc. |
| 🔴 `PHASE1_HANDOFF.md` | Feb-16 "Phase 1 DONE" snapshot; integration claim wrong (`'tiered'` now aliases `→ 'sharp'` via `tiered_factory.build_controller`, `game_handler.py:374-379`); "postflop is check/fold fallback" long superseded. | Superseded by `TIEREDBOT_DECISION_QUALITY.md` + `LOOKUP_TABLE_PROVENANCE.md`. | **Archive.** |
| 🔴 `RANGE_GATE_NEXT_STEPS.md` | Feb 13-14 experiment journal; step 1 already `✓ DONE`, steps 2-5 months-old speculation; `POSITION_CLAMPS` "replaced" claim partly outdated (still exists as legacy compat). | `range_guidance.py:41`. | **Archive** (historical artifact, not a maintained reference). |

---

## P2 — Stale (real content drift, refresh in place)

| Doc | Issue | Evidence | Action |
|-----|-------|----------|--------|
| 🟠 `SYSTEM_ARCHITECTURE.md` | §3 Psychology cites non-existent `poker/elasticity_manager.py`, `poker/trait_converter.py`, `ElasticPersonality`/`ElasticityManager`. §6 route table wrong (`/api/pokergame/*`). | Psychology rebuilt around `poker/psychology_model.py` (`EmotionFamily`); real routes `game_bp = Blueprint('game')`, `/api/new-game`, `/api/game/<id>/action` (`game_routes.py:1485,1833`). | Rewrite §3 psychology + §6 route prefixes; verify trait model; "30+ tables" → v140. Add header. |
| 🟠 `CASH_MODE_ECONOMY.md` | No mention of **chip-custody** (now chip authority on dev); "every pure transfer writes NO ledger entry" now conditionally false; dead movement constants; v94-era schema framing. | `CHIP_CUSTODY_ENABLED`/`CHIP_CUSTODY_DERIVE_READS` (`economy_flags.py:253,265`); `bankroll.py:202` records `seat:ai→ai` under custody; `DEFAULT_STAKE_UP_PROB`/`TAKE_BREAK`/`BORED_MOVE` gone; `DEFAULT_LIVE_FILL_PROB`=0.05 not 0.15 (`movement.py:111`). | Add chip-custody section; fix/remove movement constants; flag v94 drift/sizing numbers as historical. |
| 🟠 `CASH_MODE_FULL_SIM.md` | `play_one_hand` signature wrong; psychology hydrate/flush **relocated** to `cash_mode/psychology_persistence.py`; emotional state now keyed `(personality_id, sandbox_id)`; decay-on-read shipped (doc lists it as a limitation). | `full_sim.py:595` (required `sandbox_id` + `max_pot_bb`/`chip_ledger_repo`/…); `full_sim.py:42-44` imports relocated fns; `psychology_persistence._apply_idle_energy_recovery`; `bankroll_repository.py:204-260`. | Refresh signature, add `psychology_persistence.py` to file index, fix keying, move decay to implemented. (Note: `full_sim.py` under active uncommitted edit.) |
| 🟠 `PSYCHOLOGY_ZONES_MODEL.md` | "Translation Model" (lines ~248-264) documents an `ANCHOR_FLOOR=0.35`/`ANCHOR_CEILING=0.85` linear remap that **does not exist** (0 grep hits). No `self_belief`, no emotion families. | Real derivation = weighted `compute_baseline_confidence/composure` in `psychology_model.py`. | Replace Translation Model section with real baseline formulas; cross-link emotion families. |
| 🟠 `EMOTION_AND_PRESSURE_ARCHITECTURE.md` | Nearly every `game_handler.py` line ref drifted (file grew ~500 lines); Track-2 persistence describes `game_repo.save_emotional_state(...)` + rehydrate from `emotional_states` table — **table DROPped in v136**. | `schema_manager.py:2146`; state now in `controller_state.psychology_json`; e.g. `handle_evaluating_hand_phase` :2679→:3140. Three-track structure itself is accurate/valuable. | Re-verify all line refs; fix save/rehydrate description to `psychology_json`. |
| 🟠 `COACH_PROGRESSION_ARCHITECTURE.md` | M1 shipped past this build-plan: field names wrong (`id/trigger_phase/depends_on` → `skill_id/phases/tags`); "modify `poker/persistence.py`, migration v63" — file gone, schema v140; misses `coach_models.py`/`context_builder.py` split; `EvidenceRules.window_size` default 50→20 (ships 30). | `skill_definitions.py:23-31`, `coach_models.py:52`, `coach_repository.py`. | Add header; reframe "M1 plan" → "as-built"; correct field names, persistence layout, schema version, EvidenceRules defaults. |
| 🟠 `COACH_PROGRESSION_REQUIREMENTS.md` | Marked `Status: Draft for review` with M1-M5 as future, but M1 (+ parts of M2-M3) shipped; §1.4 "coach ungated until M5" but `can_access_coach` RBAC already wired. | `coach_routes.py:27-28`. | Add header; either keep as `spec` with corrected Status, or demote to historical design + point to as-built. |
| 🟠 `RATE_LIMITING.md` | Default values wrong ("200/day, 50/hour" vs actual `10000/day;1000/hour;100/minute`); wrong delimiter (`,` vs `;`); missing `RATE_LIMIT_POLLING='600 per minute'`; no socket rate-limit coverage. | `config.py:81,83,95`; `flask_app/socket_rate_limit.py`. | Add header; fix defaults/delimiter; document `RATE_LIMIT_POLLING`, Redis fail-closed-in-prod, and socket limiter. |
| 🟠 `LOOKUP_TABLE_PROVENANCE.md` | "Removed charts" section wrong — `postflop_strategies_3bp/low_spr.json` + generators were **re-added** (`dd098d13`, "restore cut slices + attribution harness"); they're cut from *play*, not removed. Shipped-tables list omits the archetype width-tier charts. | `ARCHETYPE_WIDTH_TABLE` → `preflop_100bb_6max_loose/_loose_mid/_station/_weak_station.json` (`deviation_profiles.py:238-256`). Doc explicitly promises currency, so drift matters most here. | Add width-tier charts w/ provenance; rewrite "Removed" → "Cut from play, retained as eval harness"; re-run live `.size` counts. |

---

## P3 — Minor (counts, line refs, small additions)

| Doc | Fix |
|-----|-----|
| 🟡 `PSYCHOLOGY_OVERVIEW.md` | Anchor count 9→**10** (add `self_belief`, `psychology_model.py:161`); `baseline_confidence` formula missing `+ (self_belief-0.5)*0.4` (`:481-487`); `get_display_emotion` ref :1347→:1576. |
| 🟡 `PSYCHOLOGY_DESIGN.md` | "9 anchors/traits" → 10; optional emotion-families note. |
| 🟡 `PROMPT_CONFIG_REFERENCE.md` | Add `hu_equity_offset` + `relationship_context` rows; replace `randomize_option_order` field with `option_order` (note legacy alias); mark `competitive` deprecated→`pro`; note UI exposes only `casual`. Bump date. |
| 🟡 `TIERED_BOT_ARCHITECTURE.md` | **Fix malformed `last_updated: 2026-05-12T15:30:00`** → `YYYY-MM-DD`. Add banner: "v1 design; exploitation (Phase 6) has shipped — see `TIEREDBOT_DECISION_QUALITY.md`." Correct/strike obsolete File-Structure block (`board_texture.py`/`heuristics.py`/`pio/` don't exist). |
| 🟡 `TIEREDBOT_DECISION_QUALITY.md` | Reconcile §5.5 budget table (lists 8) against live 7-tuple `RULE_ORDER` (`exploitation.py:222`). Bump date. |
| 🟡 `COACH_SYSTEM.md` | Add header (`architecture`). Fix §5 example (`'coaching_mode': 'teaching'` — no such mode; real enum LEARN/COMPETE/SILENT, `coach_models.py:34-39`). Add note on Assistant-tier LLM. Document leaks/drills/sizing-tells/metrics surface. |
| 🟡 `CROSS_SESSION_OPPONENTS.md` | Add header (`spec`). Update §4.3: `_get_opponent_stats` now delegates to `_load_cross_session_historical()` (`coach_engine.py:279`). |
| 🟡 `CASH_MODE_SEATING_ATTRACTIVENESS.md` | Add a note that seating now feeds the unified `whereabouts.py` world-state. (Constants all verified correct.) |
| 🟡 `SCALING.md` | Add header (`guide`). Update migration counts (schema v140). |
| 🟡 `IMAGE_PROMPT_FACTORY.md` | Add header (`reference`) only — content verified accurate. |
| 🟡 `HYBRID_V2_ARCHITECTURE.md` | Keep as honest historical record; optionally trim the dangling "Status → Next" checklist (lines ~516-521). |

### 🟢 Fresh — verified current, no action
`SECURITY_POSTURE.md` · `PRESSURE_EVENTS.md` · `PRESSURE_STATS_SYSTEM.md` · `PROMPT_PRESENTATION_MAP.md` · `FISH_BOT_SYSTEM.md` · `FISH_PERSONALITY_CONFIGURATION.md` · `BOUNDED_OPTIONS_DECISION_FRAMEWORK.md` · `CASH_MODE_WEALTH_LEVERS.md`

---

## Header-compliance checklist (CLAUDE.md mandates YAML header on every .md)

**✅ DONE 2026-06-03** — all 13 missing headers added + malformed date fixed; the
whole directory now passes (0 missing, 0 malformed across 37 docs). `created`
dates were taken from each file's first git commit; `last_updated` was set to the
file's last *content* change (not today) so the dates honestly reflect staleness.

**Missing header entirely (13):**
- [x] `AI_PLAYER_SYSTEM.md` (architecture)
- [x] `AI_PROMPT_ARCHITECTURE.md` (architecture)
- [x] `BETTING_UI_IMPROVEMENTS.md` (guide — still an archive candidate)
- [x] `COACH_PROGRESSION_ARCHITECTURE.md` (architecture)
- [x] `COACH_PROGRESSION_REQUIREMENTS.md` (spec)
- [x] `COACH_SYSTEM.md` (architecture)
- [x] `CROSS_SESSION_OPPONENTS.md` (spec)
- [x] `FRONTEND.md` (architecture)
- [x] `IMAGE_PROMPT_FACTORY.md` (reference)
- [x] `RATE_LIMITING.md` (reference)
- [x] `README_PERSISTENCE.md` (architecture — header added; content rewrite still P1)
- [x] `SCALING.md` (guide)
- [x] `SYSTEM_ARCHITECTURE.md` (architecture)

**Malformed `last_updated` (1):**
- [x] `TIERED_BOT_ARCHITECTURE.md` — `2026-05-12T15:30:00` → `2026-05-12`

---

## Documentation gaps — systems in code with no technical doc

High priority (major shipped systems, zero coverage):
- [ ] **Chip-custody / ledger-as-authority** — `chip_ledger_repository.py`, `CHIP_CUSTODY_ENABLED`; now the chip authority on dev and **contradicts** `CASH_MODE_ECONOMY.md`. Only plan docs exist.
- [ ] **Presence / whereabouts machine** — `cash_mode/whereabouts.py`, `presence.py`, `presence_sweep.py`, `presence_transitions.py`, `entity_presence` table. Cutover shipped; no technical doc.
- [ ] **Repositories-layer overview/index** — ~30 domain repos with no map (the closest, `README_PERSISTENCE.md`, is obsolete). Pairs with the README rewrite above.
- [ ] **Anchors-based personality schema** — no authoritative doc for the `anchors`/`skill`/`bankroll_knobs`/`staker_profile` structure (two docs still describe the defunct 5-trait model).

Medium priority:
- [ ] **Backing / staking / loans** — `player_staking.py`, `stake_settlement.py`, `sponsor_offers.py`, `staker_profile.py` (only "Known issues" mentions in ECONOMY).
- [ ] **Coach sub-features** — drills (`coach_drill.py`), leak detection (`coach_leaks.py`, `coach_chart_leaks.py`), sizing tells (`coach_sizing_tells.py`), prefetch, `/api/coach/metrics/*`; plus `coach_models.py`/`context_builder.py` split and the **Assistant-tier LLM** decision.
- [ ] **Archetype width-tier table mapping** — `ARCHETYPE_WIDTH_TABLE` ("envelope = preflop table, flavor = distortion") has only inline comments.
- [ ] **Controller selection / `tiered_factory.build_controller`** — bot-type aliasing (`hybrid→standard`, `tiered→sharp`), live dispatch path (`game_handler.py:374`).
- [ ] **Postflop strategy modules** — `value_override.py`, `induce_override.py`, `overbet_context.py`, `multistreet_context.py` (large, recent, undocumented).
- [ ] **Social layer** — trash-talk reception (`_classify_social_disposition`, `mirror_shift_override`), sarcasm-detection gate, flattery classification in `player_psychology.py`.
- [ ] **`emotional_state.py` surviving role** — `EmotionalStateGenerator` LLM narrative/inner-voice (persisted via `psychology_json`); docs only describe the retired 4D model.

Low priority / stubs:
- [ ] **Tournaments-as-a-draw / cash-circuit** — `tournament_ticker.py`, `tournament_repository.py`, `tournament_renown`; flag-gated WIP — a stub is fine until the flag flips.
- [ ] **CSRF system** — `flask_app/csrf.py` (double-submit, `CSRF_PROTECTION_ENABLED`); only described inside `SECURITY_POSTURE.md`.
- [ ] **Socket rate limiting** — `flask_app/socket_rate_limit.py` (Flask-Limiter doesn't cover Socket.IO); fold into `RATE_LIMITING.md`.
- [ ] **`raise_utils.py`** — cross-link from `BOUNDED_OPTIONS_DECISION_FRAMEWORK.md` Key Files (extracted to break the controllers import cycle).

---

## Suggested archive set

Move to `docs/archive/` (historical, not worth maintaining): `BETTING_UI_IMPROVEMENTS.md`,
`PHASE1_HANDOFF.md`, `RANGE_GATE_NEXT_STEPS.md`. `HYBRID_V2_ARCHITECTURE.md` stays (it is
an honest, self-labeled historical record) — just trim its dangling TODO list.
