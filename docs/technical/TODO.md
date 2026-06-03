---
purpose: Tracking list for technical-documentation staleness, gaps, and header compliance in docs/technical/
type: reference
created: 2026-06-03
last_updated: 2026-06-03
---

# Technical Documentation TODO

Tracks staleness, gaps, and standards-compliance for `docs/technical/`. Originally
seeded from a code-cross-checked survey on **2026-06-03** (37 docs vs `development`
at schema **v148**). A multi-agent refresh batch landed the same day — see
**Completed** below. Remaining work is under **Still open**.

Legend: 🔴 obsolete/wrong · 🟠 stale · 🟡 minor · 🟢 fresh

---

## ✅ Completed — 2026-06-03 refresh batch

A 7-agent rewrite team + a verify-gated workflow refreshed/rewrote **24 docs**
against current code (schema v148); every claim verified with `file:line`, with an
adversarial falsify pass on the new gap docs.

**P1 — obsolete rewritten:**
- `FRONTEND.md` — React-Context fiction → real **Zustand** stores; regenerated component tree; corrected stack (React 19 / Vite 7 / Zustand 5).
- `AI_PLAYER_SYSTEM.md` + `AI_PROMPT_ARCHITECTURE.md` — killed the fictional 5-trait model → real `anchors` schema; phantom `core/assistants.py` → `core.llm.Assistant`; default model groq/llama-3.1-8b; persona count 45→62.
- `EQUITY_PRESSURE_DETECTION.md` — deleted dead `EquityTracker` API/constants → `pressure_detector.detect_equity_shock_events`; repo method + thresholds fixed; v68→v69.

**Archived** to `docs/archive/` (with banner): `BETTING_UI_IMPROVEMENTS.md`, `PHASE1_HANDOFF.md`, `RANGE_GATE_NEXT_STEPS.md`.

**P2 — refreshed:**
- `SYSTEM_ARCHITECTURE.md` — §3 psychology rewritten around `psychology_model.py`; §6 routes corrected to real flat `/api/...` paths; schema→v148.
- `PSYCHOLOGY_ZONES_MODEL.md` — phantom `ANCHOR_FLOOR/CEILING` translation model → real `compute_baseline_*` formulas (worked examples re-derived).
- `EMOTION_AND_PRESSURE_ARCHITECTURE.md` — ~30 drifted `game_handler.py` line refs corrected; Track-2 persistence rewritten to `controller_state.psychology_json` (the `emotional_state` table was dropped in v136).
- `COACH_PROGRESSION_ARCHITECTURE.md` / `COACH_PROGRESSION_REQUIREMENTS.md` — reframed plan→as-built; corrected field names, persistence layout, RBAC reality.
- `RATE_LIMITING.md` — real `;`-delimited limits + `RATE_LIMIT_POLLING` + socket limiter.
- `LOOKUP_TABLE_PROVENANCE.md` — "removed" slices reframed as cut-from-play/eval-harness; added the 4 archetype width-tier charts. **Found:** `steal_pressure` rule was removed (EXP_005).

**P3 — minor fixes:** `PSYCHOLOGY_OVERVIEW`/`PSYCHOLOGY_DESIGN` (10th anchor `self_belief` + formula), `PROMPT_CONFIG_REFERENCE` (added `hu_equity_offset`/`relationship_context`/`show_ev_labels`, `option_order` migration, `competitive` deprecation), `TIERED_BOT_ARCHITECTURE` (banner + file-structure strike + date fix), `TIEREDBOT_DECISION_QUALITY` (§5.5 → live 7-tuple `RULE_ORDER`), `COACH_SYSTEM` (teaching→learn, Assistant-tier note, sub-feature surface), `CROSS_SESSION_OPPONENTS` (§4.3 `_load_cross_session_historical`), `SCALING` (v148), `HYBRID_V2_ARCHITECTURE` (trimmed dangling TODO).

**New gap docs authored (verify-gated):**
- `REPOSITORIES.md` — repositories-layer index (BaseRepository pattern, per-repo table ownership, SchemaManager/v148). **Supersedes `README_PERSISTENCE.md`, now removed.**
- `PERSONALITY_ANCHORS.md` — `personalities.json` schema (anchors + skill/bankroll_knobs/staker_profile).

**Header compliance:** all 13 missing headers added + `TIERED_BOT_ARCHITECTURE.md` malformed date fixed. Directory is now **0-missing** (`CLAUDE.md` excepted).

---

## ✅ Completed — 2026-06-03 gap-doc batch (code-explorer + captain-log → draft → verify)

The held cash docs and the gap docs were written via two verify-gated workflows
using `feature-dev:code-explorer` for architecture mapping + captain's logs for
design rationale. All claims adversarially verified against code.

- **NEW:** `CHIP_CUSTODY_LEDGER.md`, `PRESENCE_WHEREABOUTS.md`, `BACKING_AND_STAKING.md`, `POSTFLOP_OVERRIDES.md`, `SOCIAL_DYNAMICS.md`, `TOURNAMENTS.md`, `CSRF.md`
- **REFRESHED:** `CASH_MODE_ECONOMY.md` (chip-custody section), `CASH_MODE_FULL_SIM.md` (signature + `psychology_persistence.py` + keying + decay), `EMOTION_AND_PRESSURE_ARCHITECTURE.md` (`EmotionalStateGenerator` narrative role), `CASH_MODE_SEATING_ATTRACTIVENESS.md` (whereabouts cross-link)
- **Provenance:** `LOOKUP_TABLE_PROVENANCE.md` gained a "Build artifacts, config & generators" section accounting for all of `poker/strategy/data/`. `SECURITY_POSTURE.md` + `REPOSITORIES.md` got cross-links to the new docs.

This closes the High/Medium/Low gap list from the original survey.

## Still open

### Small follow-ups (not full docs)
- 🟡 **`react/CLAUDE.md` is stale** (auto-loaded): says "React 18" (actual 19) and lists a non-existent `contexts/` dir — same two errors fixed in `FRONTEND.md`. Fix at the source.
- 🟡 **`PROMPT_CONFIG_REFERENCE.md` field catalog drifts when fields ship** (`show_ev_labels` was undocumented until this pass) — periodically re-verify against the `prompt_config.py` dataclass `fields()`.
- 🟡 **`psychology.traits` backward-compat shim** (old 5-key dict derived from the anchors) — worth a one-line note in `PSYCHOLOGY_OVERVIEW.md` (also tracked as legacy LC-01 in `docs/TRIAGE.md`).
- 🟡 **`fish_loadout.py` / `preflop_isolate.py`** (in `poker/strategy/`, not `data/`) — strategy code modules not yet covered by `POSTFLOP_OVERRIDES.md`; minor, fold in if that doc is next touched.

### Code-comment drift → `docs/TRIAGE.md` candidate (not a doc issue)
- `schema_manager.py:622` labels the `hand_equity` bootstrap table "(v68)" but it's added by `_migrate_v69_add_hand_equity`.
