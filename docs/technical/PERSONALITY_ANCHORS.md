---
purpose: Schema reference for personalities.json — the anchors identity model and the other per-persona fields that drive psychology, skill, and the cash economy
type: reference
created: 2026-06-03
last_updated: 2026-06-03
---

# Personality Schema (`personalities.json`)

This is the field catalog for an AI persona as authored in
`poker/personalities.json` (62 personas at time of writing) and stored in the
`personalities` table's `config_json` column. It exists so you can answer "what
goes in a persona, and what does each field do" without grepping the loaders.

The strategy- and psychology-relevant heart of a persona is the **`anchors`**
block — the *Identity Layer* that replaced an older, partly-fictional
"5-trait" model (`bluff_tendency` / `aggression` / `emoji_usage` + elasticity).
For how anchors animate into runtime emotion (axes, zones, emotion families),
see [`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md) — this doc covers the
*schema*, not the runtime model, and does not duplicate the zone math.

> **Migration note.** `personality_traits` has **zero** occurrences in
> `personalities.json` — the strategy/psychology layer is fully on anchors. The
> generator still *emits* a `personality_traits` blob for table-talk flavor
> (`poker/personality_generator.py:107-119`), but no strategy code reads it.

---

## 1. The `anchors` block (Identity Layer)

Canonical definition: the frozen dataclass `PersonalityAnchors`
(`poker/psychology_model.py:136-212`). Anchors "define WHO the player
fundamentally is and never change during a session. They act as gravity,
pulling dynamic state back toward baseline." All values are floats in
`[0.0, 1.0]` inclusive; `__post_init__` validates type and range, raising
`TypeError` / `ValueError` otherwise (`:163-181`).

Nine anchors are always present in the JSON; `self_belief` is optional (the 10th
anchor, present on only the 9 fish personas in the current file). Omitted keys
fall back to per-field defaults in `PersonalityAnchors.from_dict`
(`:198-212`).

| Anchor | Meaning (0 → 1) | `from_dict` default |
|---|---|---|
| `baseline_aggression` | Default bet/raise frequency (passive → aggressive) | 0.5 |
| `baseline_looseness` | Default hand-range width (tight → loose) | 0.3 |
| `ego` | Confidence sensitivity to outplay events (stable → brittle) | 0.5 |
| `poise` | Composure resistance to bad outcomes (volatile → stable) | 0.7 |
| `expressiveness` | Emotional transparency (poker face → open book) | 0.5 |
| `risk_identity` | Variance tolerance (risk-averse → risk-seeking) | 0.5 |
| `adaptation_bias` | Opponent adjustment rate (static → adaptive) | 0.5 |
| `baseline_energy` | Baseline energy level (reserved → animated) | 0.5 |
| `recovery_rate` | Axis decay speed (slow → fast) | 0.15 |
| `self_belief` *(optional)* | Felt confidence independent of skill/ego — the "bravado/delusion" dial; 0.5 = neutral, >0.5 = overconfident | 0.5 |

`self_belief` is the only anchor with a dataclass-level default
(`= 0.5`, `:161`); the other nine are positional/required on the dataclass and
only defaulted when entering through `from_dict`. It is deliberately decoupled
from `ego`: it lets felt confidence rise **without** raising ego (which would
also make the player anger-prone/brittle) — see the field docstring
(`psychology_model.py:156-161`).

> **Docstring caveat.** `PlayerPsychology.from_personality_config`'s docstring
> still says "Requires 9-anchor format" (`poker/player_psychology.py:318`) — it
> predates `self_belief`. The 10th anchor flows through `from_dict` correctly;
> treat the model as "9 core + 1 optional."

---

## 2. How anchors feed runtime psychology (brief)

At load (`PlayerPsychology.from_personality_config`,
`poker/player_psychology.py:307-364`): if the config has an `anchors` key it is
parsed via `from_dict` (`:321-322`); otherwise a fallback 9-arg
`PersonalityAnchors` is built and a warning is logged advising
`seed_personalities.py --force` (`:324-338`). The anchors then seed the initial
emotional axes:

| Derived baseline | Formula (clamped) | Source |
|---|---|---|
| `baseline_confidence` | `0.3 + 0.25·baseline_aggression + 0.20·risk_identity + 0.25·ego + 0.4·(self_belief−0.5)` | `psychology_model.py:464-494` |
| `baseline_composure` | `0.25 + 0.50·poise + 0.15·(1−expressiveness) + 0.3·(risk_identity−0.5)` | `psychology_model.py:497-518` |

Both are clamped to keep the starting state out of the penalty zones — confidence
outside TIMID/OVERCONFIDENT (`get_zone_param('PENALTY_TIMID_THRESHOLD')` /
`'PENALTY_OVERCONFIDENT_THRESHOLD'`, ±0.10 margin), composure above
`PENALTY_TILTED_THRESHOLD`+0.05. The threshold constants come from
`zone_config.get_zone_param`; this doc references the formula and clamp behavior,
not the numeric thresholds. The resulting `EmotionalAxes(confidence, composure,
energy=baseline_energy)` is the runtime starting point (`:340-349`).

Anchors also drive (all in `psychology_model.py`, full model in
[`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md)):

- **Emotion family** from `ego`+`expressiveness` — `get_emotion_family` (`:542-570`).
- **Poker-face zone radii** from `ego`/`poise`/`risk_identity` — `create_poker_face_zone` (`:438-461`).
- **Per-session drift** — `apply_session_drift` (`:56-94`); `drift_strength = (1−poise)·(1−recovery_rate)` (floored at `DRIFT_STRENGTH_FLOOR=0.05`); `poise` and `recovery_rate` themselves never drift.

---

## 3. Other top-level persona fields

Top-level keys verified across all 62 personas:

| Key | Presence | Type | Purpose |
|---|---|---|---|
| `id` | 62 | slug str | Stable persona slug (e.g. `"abraham_lincoln"`); maps to `personality_id` |
| `play_style` / `default_confidence` / `default_attitude` | 62 each | str | Legacy freeform behavioral text (flavor) |
| `anchors` | 62 | sub-dict | Identity layer (§1) |
| `verbal_tics` / `physical_tics` | 62 each | list[str] | Table-talk / tell flavor |
| `bankroll_knobs` | 62 | sub-dict | Cash self-roll economy (§3.1) |
| `staker_profile` | 62 | sub-dict | Lending behavior to other AIs (§3.2) |
| `skill` | 50 | str | Bot skill tier — **derived from `adaptation_bias`**, absent on 12 (§3.3) |
| `nickname` | 27 | str | Optional short display name |
| `borrower_profile` | 11 | sub-dict | Borrowing behavior when bust (§3.2) |
| `archetype` | 9 | str `"fish"` | Casino/bot-routing stamp — fish only (§3.4) |
| `visual_identity` / `rule_strategy` / `fish_leak` | 9 each | mixed | Fish companions of `archetype` (present on exactly the 9 fish) |
| `spot_tendencies` | 9 | dict | Drill/spot flavor — 9 personas, but **not** the fish set (7 fish + Don Quixote + The Grandmother; 2 fish lack it) |
| `adaptive_overbet` | 1 | flag | One-off persona flag |

> **Not in the JSON:** `visibility` and `circulating` are **DB columns**, not
> authored keys (§3.5). Do not add them to `config_json`.

### 3.1 `bankroll_knobs` (cash-mode self-roll)

Sub-dict; per-field meanings authored in the generator prompt
(`poker/personality_generator.py:146-160`), consumed with per-field fallback to
`BANKROLL_KNOB_DEFAULTS` in `poker/repositories/bankroll_repository.py`
(schema note `:91`) plus `cash_mode/bankroll.py`, `player_staking.py`,
`staker_profile.py`.

| Field | Range / values | Meaning |
|---|---|---|
| `starting_bankroll` | tier-anchored ($2: 4–8k … $1000: 90–250k) | Chips at world-start |
| `bankroll_rate` | 100–3500 | Chips/day income regen toward `starting_bankroll` |
| `buy_in_multiplier` | 1.0 (min) – 2.5 (ego) | Overbuy vs table min buy-in |
| `stake_comfort_zone` | one of `"$2"/"$10"/"$50"/"$200"/"$1000"` | Preferred stake tier |

Tier distribution in the current file: $50×15, $10×14, $2×14, $200×11, $1000×8.

### 3.2 `staker_profile` (and `borrower_profile`)

`staker_profile` governs lending to other AIs (generator
`poker/personality_generator.py:162-173`; consumed in `cash_mode/staker_profile.py`,
`sponsor_offers.py`, `movement.py`, `lobby.py`, `bankroll_repository.py`):

| Field | Range | Meaning |
|---|---|---|
| `willing` | bool (default true) | Will lend at all; false only for ascetic/cruel personas |
| `max_loan_pct_of_bankroll` | 0.03–0.20 | Cap of bankroll lent |
| `floor_anchor` | 1.0–1.5 | Repayment floor multiple |
| `rate_anchor` | 0.10–0.50 | Expected interest |
| `respect_floor` | −1.0..0.0 | Min relationship-respect to lend |
| `heat_ceiling` | 0.4–1.0 | Max active conflict tolerated |

`borrower_profile` (the dual — behavior when THEY are bust; present on 11/62,
generator `:175-185`): `willing` (default true), `willingness_threshold` 0.15–0.50.

### 3.3 `skill` (derived bot tier)

Named skill tiers for the tiered (`sharp`) bot, defined in
`poker/strategy/skill_tiers.py`: `SKILL_TIERS` keys `'shark'` / `'reg'` /
`'weak_reg'` / `'rec'` (sharpest → weakest, `:63-99`).

`skill` is **not independent** — it is derived from `anchors.adaptation_bias`
via `_ADAPTATION_BIAS_BANDS` → `skill_tier_for_adaptation_bias`
(`skill_tiers.py:113-137`): `≥0.60 → shark`, `≥0.45 → reg`, `≥0.225 → weak_reg`,
else `rec`; `None` → `DEFAULT_SKILL_TIER = 'shark'` (`:101`, `:119-137`). The
generator computes it from anchors and never asks the LLM
(`personality_generator.py:557-561`, fallback `:613`).

`DEFAULT_SKILL_TIER = 'shark'` is a deliberate **no-op** — it equals the validated
`TieredBotController` ceiling, so `apply_skill_tier` short-circuits on shark
(`:156`). `apply_skill_tier` raises `KeyError` on an unknown tier (fail loud,
`:140-158`). This is why 12 personas omit `skill` — it is reconstructed at load.

Distribution in the file: `weak_reg`×23, `reg`×14, `rec`×7, `shark`×6, absent×12.
Provenance: `docs/plans/PLAYER_SKILL_SPECTRUM.md` (Phase 4).

### 3.4 `archetype` (top-level fish stamp)

Top-level `archetype: "fish"` is a **casino-economy / bot-routing** stamp — it is
distinct from the *derived* archetype computed from anchors
(`poker/archetypes.py:30-40`, `classify_from_anchors`, thresholds
`ANCHOR_TIGHT=0.45` / `ANCHOR_LOOSE=0.65` / `ANCHOR_AGGRESSIVE=0.50`). Only the
JSON top-level field is documented here as a schema key.

Present on exactly the 9 fish personas, co-occurring with the fish-only fields
`visual_identity`, `rule_strategy: "fish"`, and `fish_leak` (each also on exactly
those 9). (`spot_tendencies` is *not* a clean fish marker — it sits on 7 of the 9
fish plus 2 non-fish, so it is listed separately in §3, not here.) Consumers:

- `poker/player_psychology.py:1543` — `is_fish = config.get('archetype') == 'fish'`, gates a sheepish emotion-family behavior (`:1564`).
- `poker/repositories/personality_repository.py:510-511` — fish are **excluded** from the auto-seeded eligible opponent pool (`json_extract(config_json,'$.archetype') != 'fish'` in `list_eligible_for_cash_mode`); the inverse `list_fish_for_cash_mode` selects them with `... = 'fish'` at `:571`.
- `cash_mode/closed_economy.py:86,241` — fish are donors, **excluded from the grinder set** (`archetype != 'fish'`). The dual fish-engine *dispatch* (route fish through the tiered `calling_station` path) is keyed on the same stamp at `cash_mode/full_sim.py:411` (`is_fish = archetype == 'fish' or rule_strategy == 'fish'`).
- `cash_mode/movement.py`, `presence_transitions.py`, `tables.py:106` — seat-level `archetype='fish'` drives RETURN_TO_POOL / rebuy preservation.

### 3.5 `visibility` and `circulating` (DB columns, NOT JSON)

These are columns on the `personalities` table, set/resolved by the repository —
**not** authored in `personalities.json` (zero occurrences of either key in the
file). They are documented here because callers often conflate them with persona
config.

| Column | Added | Values / default | Meaning |
|---|---|---|---|
| `visibility` | migration v64 (`schema_manager.py:54`) | `'public'`/`'private'`/`'disabled'`; default `'public'` (`personality_repository.py:57,140`) | Who can **see/pick** the persona |
| `circulating` | migration v123 (`schema_manager.py:216-218`, fn registered `:2093,2191`) | 0/1; default **0** on programmatic save (`personality_repository.py:64` doc, `:155` the `= 0` fallback) | Whether the seat-filler **auto-seats** it |

The two are independent: a row can be `public` (pickable) yet `circulating=0`
(never auto-seeded) (`personality_repository.py:67-68`). Auto-generated personas
save `visibility = private if owner_id else public`, `circulating = False`
(`personality_generator.py:434-441`). v123 backfilled all existing public rows
to circulate to preserve prior behavior.

---

## 4. Storage & schema

The `personalities` base table (`schema_manager.py:581-592`) columns:
`id, name (UNIQUE), config_json TEXT NOT NULL, created_at, updated_at,
is_generated, source, times_used, elasticity_config,
personality_id TEXT UNIQUE`. `personality_id` (stable cross-session ID) was added
in v85 (`schema_manager.py:76`). `visibility` / `circulating` / `owner_id` are
added by later `ALTER` migrations (v64, v123), not in the base `CREATE`.

`anchors` and `bankroll_knobs` are nested **sub-dicts inside `config_json`**, not
columns (`schema_manager.py:90-91,156,578-579`).

Current schema: **`SCHEMA_VERSION = 148`** (`poker/repositories/schema_manager.py:321`).

---

## See also

- [`PSYCHOLOGY_OVERVIEW.md`](PSYCHOLOGY_OVERVIEW.md) — runtime model: axes, zones, emotion families, drift (the "what anchors become at the table").
