---
purpose: Design for the relationship/affinity layer and a multi-table cash-game mode that consumes it
type: design
created: 2026-05-16
last_updated: 2026-05-19
---

# Cash Mode and Relationships

## Overview

Two coupled designs:

1. **Relationship layer** — per-(observer, opponent) affinity state (heat, respect, likability) updated by hand outcomes, player chat, and post-hand commentary. Persists *across sessions and games*. Feeds the tiered-bot decision pipeline as additional input to the existing exploitation layer.

2. **Cash game mode** — a new game mode alongside the existing tournament/HU flows. Persistent per-personality AI bankrolls with real-time regen, sit/leave/bust dynamics. This is where the relationship layer pays off: rivals seek you out, friends play softer, busted AI take days to return.

The relationship layer is independently useful (improves existing modes) and is a prerequisite for cash mode's full character. Ship in that order. **Cash mode v1 ships single-table first** — multi-table lobby is v2.

## Goals and non-goals

**Goals (v1)**
- AI characters that feel persistent — they remember you across sessions and games, hold grudges, build respect.
- One thin integration seam into the existing tiered-bot decision pipeline.
- All time-based effects are **pure projection on read** — no background daemons, no read-as-write surprises.
- v1 data model that admits v2/v3 (multi-table lobby, AI-vs-AI background simulation, economy) without redesign.

**Goals (v2+, design-aware but not shipped in v1)**
- Cash-mode retention via chip sinks routed through relationships (staking, unlocks, private games).
- Multi-table lobby with rivalry-driven seat dynamics.

**Non-goals (v1)**
- Multi-table lobby (deferred to v2).
- Scripted scenes, cutscenes, or affinity-gated narrative content.
- Player-initiated DMs / out-of-game character chat.
- AI-vs-AI play when player is not seated (designed *for*, not shipped).
- Player passive bankroll regen (player only refills on full bust).
- Dialogue surfaces outside the table (lobby tooltips, sit/stand beats deferred).
- Economy events (staking, unlocks, private games) — full system later, but design notes appear in Part 3.

## Part 1: Relationship Layer

### Scope

The relationship layer manages **affinity axes** only. Three axes per (observer, opponent) pair: heat, respect, likability. Everything else (cash-session bookkeeping, hand stats, narrative observations, economy contracts) lives elsewhere.

**Strict invariant**: all axis mutations go through `OpponentModelManager.record_event()`. Decay reads, dialogue reads, tier-modifier reads, economy actions, and table-selection reads each have their own APIs that **do not mutate axes**. This narrow invariant is the whole reason the system stays coherent.

### ID/name convention

**All relationship and cash-mode persistence APIs use stable IDs**, never display names. Throughout this doc, parameters named `actor_id`, `target_id`, `observer_id`, `opponent_id`, `personality_id`, and `player_id` refer to stable IDs. Display names are presentation-layer only and may collide (custom personalities, renames). The `Personality identity` section in Part 2 covers ID sourcing.

When integrating with existing in-memory structures keyed by display name (e.g., `OpponentModelManager.models[name][name]`), the v1 work introduces a name→id resolver and migrates the in-memory keys to IDs at the same time persistence is added.

### Data model

A new dataclass on `OpponentModel` (lives in `poker/memory/opponent_model.py`):

```python
@dataclass
class RelationshipState:
    # Durable affinity axes (0.0–1.0 unless noted)
    respect: float = 0.5
    heat: float = 0.0           # one-sided: 0 = neutral, 1 = nemesis
    likability: float = 0.5

    # Cross-session presence
    last_seen: Optional[datetime] = None
    last_decay_tick: Optional[datetime] = None
```

**What's intentionally NOT on this object:**
- `session_pnl` — lives in `CashSessionState` (Part 2), per (player, table_id)
- `cumulative_pnl` — cash-mode-specific concept (PnL is meaningless in tournaments where chips reset). Lives in a separate `cash_pair_stats` table keyed by `(observer_id, opponent_id)`. See "Cash pair stats" below.
- `sessions_together` — derivable from `OpponentTendencies.hands_observed` chunked by session; not load-bearing for v1
- Familiarity axis — derived on demand from `tendencies.hands_observed`, never stored

This split addresses a real concern: affinity state must survive across sessions, tables, and game modes; session-scoped and cash-mode-specific data must not pollute it.

### Cash pair stats (separate from affinity)

```python
@dataclass
class CashPairStats:
    observer_id: str
    opponent_id: str
    cumulative_pnl: int = 0           # chips, observer's lifetime net vs opponent
    hands_played_cash: int = 0
```

Persisted in its own `cash_pair_stats` table (schema in Persistence section). `cumulative_pnl` is **observer-POV**: the chips this observer has won net from this opponent across every cash-mode hand they shared. The mirror pair (`models[opp][me]`) gets the negation, written in the same transaction so the two views can't drift.

**Multiway/side-pot allocation:** at hand resolution, for each (winner, loser) pair the winner's net gain is split proportionally to each loser's chip contribution to the pots the winner collected. Side pots resolve independently — each side pot has its own (winner, loser) PnL pairs.

### Event vocabulary

`MemorableHand.memory_type: str` is renamed in code to `MemorableHand.event: RelationshipEvent` (enum). The **DB column name stays `memory_type`** (existing schema at `poker/repositories/game_repository.py:666`); serialization writes `event.value` (a string) into that column. Loading parses the column string back into the enum.

```python
class RelationshipEvent(Enum):
    # Hand-outcome events (existing memorable_hand types)
    BLUFFED_OFF        = "bluffed_off"
    HERO_CALL          = "hero_call"
    BIG_LOSS           = "big_loss"
    BIG_WIN            = "big_win"
    BAD_BEAT           = "bad_beat"
    DOMINATED_SHOWDOWN = "dominated_showdown"
    STRONG_FOLD_SHOWN  = "strong_fold_shown"

    # Chat events (categorizer output)
    TRASH_TALK         = "chat_trash_talk"
    COMPLIMENT         = "chat_compliment"
    TAUNT_POST_WIN     = "chat_taunt_post_win"
    FRIENDLY_BANTER    = "chat_friendly_banter"
    TELL_READ          = "chat_tell_read"

    # Quarantine sentinel for unknown legacy strings on load
    UNKNOWN            = "_unknown"
```

Enum `.value` strings serve as the canonical DB representation. Existing code that emits `memory_type` as a raw string is updated to import and pass the enum member; this is mechanical refactor scope (estimated <20 call sites based on a `memory_type=` grep).

**Unknown string quarantine.** Existing `memorable_hands` rows may contain strings not in the enum (older event names, custom strings). On load, an unknown string is coerced to a `RelationshipEvent.UNKNOWN` sentinel that has **zero entries in both dispatch tables** (no axis impact, no mirror impact). A WARN-level log emits the offending string and row id once per process. This lets old data load cleanly while never silently moving axes from values we can't account for. A one-shot migration script enumerates the corpus and either maps strings to enum members or drops the rows — runs out-of-band, not in the request path.

**`RelationshipEvent` is intentionally narrow.** Economy events (staking outcomes, unlocks, private-game invites) get a separate `EconomyEvent` taxonomy when those systems ship. Some economy events will *emit* `RelationshipEvent`s as side effects (e.g., a player staking an AI emits `TRUST_EXTENDED`; their bust on the player's stake emits `BETRAYAL` — to be added in Phase 5 when needed).

### Event → axis shift dispatch

Axis shifts per event from the **actor's POV**. Numbers are starting values; tunable from play data.

| Event | Δheat | Δrespect | Δlikability |
|---|---|---|---|
| `BLUFFED_OFF` | +0.20 | −0.05 | −0.02 |
| `HERO_CALL` | −0.05 | −0.10 | +0.01 |
| `BIG_LOSS` | +0.15 | +0.08 | −0.05 |
| `BIG_WIN` | −0.10 | −0.05 | +0.02 |
| `BAD_BEAT` | +0.30 | −0.15 | −0.10 |
| `DOMINATED_SHOWDOWN` | 0 | −0.15 | 0 |
| `STRONG_FOLD_SHOWN` | 0 | +0.10 | 0 |
| `TRASH_TALK` | +0.10 | 0 | −0.05 |
| `COMPLIMENT` | 0 | +0.03 | +0.05 |
| `TAUNT_POST_WIN` | +0.20 | 0 | −0.10 |
| `FRIENDLY_BANTER` | 0 | 0 | +0.03 |
| `TELL_READ` | 0 | +0.05 | 0 |

A symmetric **mirror table** declares the target's-POV shifts for each event. Lives in code next to the actor table. Example: `BAD_BEAT` against actor → mirror entry for target: heat 0, respect +0.05 (feared), likability −0.05 (unearned win).

### Symmetry: bilateral updates

A poker outcome is one event with two views. `OpponentModelManager.record_event(actor, target, event, ...)` updates **both pair entries** in a single call so they cannot drift:

- `models[actor][target]` ← actor's-POV shifts
- `models[target][actor]` ← mirror shifts

Chat events update only the speaker's pair entry by default; the target's mirror is much smaller (witnessing trash talk doesn't move respect, but it dings likability slightly).

### Decay

Plateau-then-exponential, **pure projection on read**. The `last_decay_tick` field advances only when `record_event` writes (which always writes `last_decay_tick = now` after applying shifts). Reads compute the projected value without persisting.

```python
def project_heat(state: RelationshipState, now: datetime,
                 plateau_days=7, half_life_days=14, snap_threshold=0.05) -> float:
    if state.last_decay_tick is None:
        return state.heat
    days = (now - state.last_decay_tick).total_seconds() / 86400
    if days <= plateau_days:
        return state.heat
    decay_days = days - plateau_days
    projected = state.heat * 0.5 ** (decay_days / half_life_days)
    return 0.0 if projected < snap_threshold else projected
```

- Plateau at peak for **7 days** after the most recent event.
- Exponential decay with a **14-day half-life** afterward.
- Snap to **0.0** below **0.05**.
- Respect and likability **do not decay** (earned state).

Reads (`get_tier_modifier`, dialogue tone, lobby indicators) all use `project_heat()`. The persisted `heat` value is only updated when `record_event` runs.

### Persistence

The existing `opponent_models` table is **per-game-id** (`poker/repositories/game_repository.py:625-660`). Relationship state must be **cross-session and cross-game** — same observer/opponent pair accumulates across every game they share.

**Schema changes required:**

```sql
-- Cross-session affinity state
CREATE TABLE relationship_states (
    observer_id TEXT NOT NULL,
    opponent_id TEXT NOT NULL,
    heat REAL NOT NULL DEFAULT 0.0,
    respect REAL NOT NULL DEFAULT 0.5,
    likability REAL NOT NULL DEFAULT 0.5,
    last_seen TIMESTAMP,
    last_decay_tick TIMESTAMP,
    PRIMARY KEY (observer_id, opponent_id)
);

-- Cash-mode-specific pair statistics (cumulative PnL etc.)
CREATE TABLE cash_pair_stats (
    observer_id TEXT NOT NULL,
    opponent_id TEXT NOT NULL,
    cumulative_pnl INTEGER NOT NULL DEFAULT 0,
    hands_played_cash INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (observer_id, opponent_id)
);
```

`memorable_hands` table stays as-is; its existing `memory_type TEXT` column now holds enum `.value` strings. No column rename required.

**Stable identity**: rows key on `observer_id` / `opponent_id` — stable IDs from the Personality identity section in Part 2. Display names are never persisted as keys.

**Repository read returns projected, not raw.** Default repository read methods (`load_relationship_state`, `load_all_relationships`) return `RelationshipState` instances whose `heat` field has been passed through `project_heat(state, now)`. **Raw column reads are admin-only** and explicitly named (`load_raw_relationship_state`) to surface that the value is "heat as of last event," not current. Analytics queries that hit the table directly should aware of this distinction; we add a `current_heat AS (project_heat ...)` view for SQL-side consumers if needed in v2.

**Repository changes:**
- New methods on `GameRepository` (or a new `RelationshipRepository`):
  - `save_relationship_state(observer_id, opponent_id, state)`
  - `load_relationship_state(observer_id, opponent_id) -> RelationshipState` *(projection applied)*
  - `load_raw_relationship_state(...)` *(raw values, admin only)*
  - `load_all_relationships(observer_id) -> Dict[opponent_id, RelationshipState]` *(projection applied)*
  - `save_cash_pair_stats(observer_id, opponent_id, stats)`
  - `load_cash_pair_stats(observer_id, opponent_id) -> CashPairStats`
- `OpponentModelManager.from_dict` / `to_dict` no longer covers relationship state; the manager gains explicit `save_relationships(repo)` / `load_relationships(repo)` methods called at session boundaries.
- `MemorableHand` serialization writes `event.value` into the existing `memory_type` column; loading parses back to enum via `RelationshipEvent(string)` with unknown-string quarantine (see Event vocabulary).

### Manager API

```python
class OpponentModelManager:
    def record_event(self, actor_id: str, target_id: str,
                     event: RelationshipEvent,
                     impact_score: float = 1.0,
                     context_multiplier: float = 1.0,
                     narrative: str = "",
                     hand_summary: str = "",
                     hand_id: Optional[int] = None,
                     now: Optional[datetime] = None) -> None:
        """Single entrypoint for all RelationshipState axis mutations.

        IDs only — never display names.

        Project-first-then-apply ordering (load-bearing):
        1. Resolve now = now or datetime.utcnow().
        2. For each pair entry to update:
             a. Project state.heat through decay to `now`
                (so a refresh event after 30 days doesn't reset stale heat
                 back to its day-0 peak).
             b. Apply event-table shifts (× context_multiplier × any chat
                diminishing-returns factor).
             c. Clamp each axis to its valid range [0,1].
             d. Persist updated state with last_decay_tick = last_seen = now.
        3. Apply actor's-POV shifts to relationship[actor_id][target_id].
        4. Apply mirror shifts to relationship[target_id][actor_id].
        5. If impact_score >= MEMORABLE_HAND_THRESHOLD, record a
           MemorableHand on the actor's side.

        DOES NOT mutate anything outside RelationshipState + MemorableHand.
        Decay reads, cash-session state, cash_pair_stats, and economy
        events use their own APIs.
        """
```

### Tier modifier seam — exact insertion point

The tiered-bot decision pipeline in `TieredBotController.decide_action` (`poker/tiered_bot_controller.py:489-588`) is:

1. Lookup base strategy (preflop chart or postflop equivalent)
2. Personality distortion (anchors + emotional shift)
3. **Phase 6: opponent exploitation** — `_apply_exploitation(...)` already does opponent-aware probability shifts
4. Phase 6.5: strong-hand value override
5. Phase 6 Step B: short-stack heuristic
6. Math floor

**Relationship modifiers enter as additional input to step 3 (`_apply_exploitation`).** The exploitation layer is already designed for per-opponent adjustment and selects a primary aggressor in multiway pots; relationship modifiers extend its existing transforms rather than adding a new layer.

```python
@dataclass
class RelationshipModifier:
    bluff_freq_mult: float = 1.0           # >1 = bluff more vs this opponent
    fold_to_pressure_mult: float = 1.0     # <1 = harder to bluff off
    call_threshold_offset: float = 0.0     # absolute equity-required adjustment

def get_relationship_modifier(
    manager: OpponentModelManager,
    observer_id: str,
    target_opponent_id: str,
    now: datetime,
) -> RelationshipModifier:
    """Pure pairwise read. Projects heat through decay, maps axes to modifiers.

    Strictly pairwise — does NOT do multiway target selection. The caller
    (Phase 2: _apply_exploitation) picks the target_opponent_id from game
    state and calls this reader once with the chosen target.
    """
```

**Note for Phase 2 implementation**: the exact existing function/variable that each modifier scales (named conceptually in the table above) must be verified against `poker/tiered_bot_controller.py` and `poker/strategy/exploitation.py` before coding. The conceptual targets are correct; the precise call-site names may differ.

**Where each multiplier lands in `_apply_exploitation`:**

| Modifier | Existing exploitation knob it scales | Composition order |
|---|---|---|
| `bluff_freq_mult` | Scales the bluff-probability shift applied when an exploit pattern recommends bluff frequency adjustment (in postflop branches of `_apply_exploitation`). | After exploit-pattern detection sets the base shift; before clamp/gating. |
| `fold_to_pressure_mult` | Scales the fold-probability offset added when facing aggression from an opponent flagged as a bluff-prone exploit. Composes with the existing fold-equity logic. | After exploit detection's base offset; before clamp. |
| `call_threshold_offset` | Absolute add to the call-equity threshold used in the value-vs-station preflop classifier path (existing `_classify_preflop_hand_strength` consumer). | Added directly to threshold before comparison. |

`call_threshold_offset` replaces the prior `value_threshold_shift` proposal because `call_threshold_offset` names where it lives: the existing exploitation-layer call-threshold logic that's already aware of opponent strength classifications. No new offset is invented; the modifier piggybacks on the layer's existing threshold knob.

**Composition order inside `_apply_exploitation`:**
1. Existing pattern detection runs (unchanged) and produces base offsets.
2. `get_relationship_modifier()` is called once with the selected target.
3. Multipliers scale the pattern-derived offsets; `call_threshold_offset` is added to the threshold.
4. Existing clamp / gating runs (unchanged).
5. Trace appends `relationship_modifier` field for replay/debug.

Initial axis → modifier mapping (tunable):
- `project_heat() > 0.5` → `bluff_freq_mult = 1.3`, `call_threshold_offset = -0.03` (chase rivals harder)
- `respect > 0.7` → `fold_to_pressure_mult = 0.7`
- `likability > 0.7` → `bluff_freq_mult = max(0.85, bluff_freq_mult * 0.85)` (soft on friends)

Modifiers compose multiplicatively when multiple axes are high. Defaults preserve current behavior when no relationship state exists.

### Multiway target selection (Phase 2 — controller-side, not the reader)

`get_relationship_modifier` is **strictly pairwise**. Picking *which* opponent to read against in a multiway pot is a Phase 2 controller concern (the reader has no game state) and lives in `_apply_exploitation`'s call site. Rules:

- **Eligible opponents** = opponents currently in the hand who are NOT folded AND NOT all-in.
  - All-in exclusion rationale: when an opponent is all-in, the action set facing the bot is reduced to call/fold (no bluff possible, no pressure applicable), so the relationship multipliers — which scale bluff frequency and fold-to-pressure offsets — have no meaningful target. The math floor and value-override layers already own these decisions. If facing an all-in is the *only* live action, the modifier reduces to the no-op default. (If facing an all-in plus a still-active opponent, the active one is the target.)
- **Heads-up (post-fold collapse counts):** single eligible opponent.
- **Multiway with a clear aggressor** (an eligible opponent has bet/raised on this street): use the aggressor. **Reuse** the existing `_apply_exploitation` primary-aggressor / `_select_exploitation_stats_from_spots` selection at `tiered_bot_controller.py:1753` rather than implementing a parallel one.
- **Multiway with no aggressor** (open or checked-around spots): use heat-max — the eligible opponent with the highest **projected** heat. Ties: max-respect; further ties: alphabetical by `opponent_id` (deterministic, matters for replay/test stability).
- **No-op conditions** (modifier is default, no relationship effect):
  - No eligible opponents have any relationship state in the manager, OR
  - The computed `RelationshipModifier` equals the defaults (all multipliers = 1.0, offset = 0.0).
  
  Note: heat being zero is NOT sufficient — respect and likability can still produce non-default modifiers (e.g., high respect → `fold_to_pressure_mult` < 1.0). The check is on the produced modifier object, not on heat alone.

Documented in `_apply_exploitation`'s docstring and verified by tests.

### Input sources

Three event sources funnel into `record_event`:

#### 1. Hand outcomes

A `HandOutcomeDetector` runs at hand resolution and emits `RelationshipEvent`s. **Adapter pattern**: where existing pressure/equity events already detect a moment (e.g., `MomentAnalyzer` flags a `big_pot` showdown), the detector maps them to `RelationshipEvent` rather than re-detecting.

**Adapter table with actor/target rules:**

| Source signal | Event | Actor (record_event actor_id) | Target (record_event target_id) | Notes |
|---|---|---|---|---|
| Pressure `big_loss` (per loser per pot) | `BIG_LOSS` | the loser | the winner of the pot they paid into | Multiway: one event per (loser, winner) pair from chip-flow allocation |
| Pressure `big_win` (per winner per pot) | `BIG_WIN` | the winner | the largest contributing loser to that pot | Multiway: emit per (winner, loser) pair |
| `MomentAnalyzer.is_bad_beat()` (favorite lost) | `BAD_BEAT` | the favorite (loser) | the suckout winner | Single pairing; heads-up only signal in v1 to avoid multiway ambiguity |
| Caller correctly called a bluff at showdown | `HERO_CALL` | the caller | the bluffer | Requires showdown card visibility |
| Folder folded to opp who showed a bluff | `BLUFFED_OFF` | the folder | the bluffer | Only fires when the bluff is voluntarily revealed at showdown or hand-end |
| Showdown reveal: opp shown with trash | `DOMINATED_SHOWDOWN` | the observer who reached showdown | the trash-handed opp | One event per observer who saw the showdown |
| Folder shown to have correctly folded a strong hand | `STRONG_FOLD_SHOWN` | the observer | the disciplined folder | Rare — requires explicit "would have won" reveal |

**Multiway PnL-pair allocation rule:** for hands that finish multiway, the chip-flow allocation (winner's net split proportionally to losers' contributions to the pots the winner collected) determines (actor, target) pairs for `BIG_WIN` / `BIG_LOSS` events. Each side pot resolves independently. This matches the `cumulative_pnl` allocation rule for `cash_pair_stats` — same allocation feeds both consumers.

**Deduplication:** detector is called once at hand-end; each emitted event is uniquely keyed by `(hand_id, actor_id, target_id, event)` and skipped if already recorded.

#### 2. Player chat

Player types → categorizer (`CallType.CATEGORIZATION`, Fast tier) with `prompt_template='relationship_chat_categorization'` (distinct from generic CATEGORIZATION calls for analytics separation) → returns `(category, target_id | None, confidence)`. Below a confidence floor (default 0.6), category defaults to `noise / low_effort` (no axis shift).

The categorizer receives recent hand context (last winner, bluff visibility, etc.) so it can distinguish "nice fold" (compliment) from "nice fold, idiot" (trash_talk).

If `target_id` is `None` (table-at-large chat), the shift fans out: **one shift per opponent at the table, applied at half magnitude, consuming each opponent's own SessionChatState bucket** (not the "None" bucket). This means broadcast chat is subject to the same diminishing-returns and axis caps as targeted chat — a player cannot bypass the per-target cap by chatting to the table at large. The "None" key is for routing, not for accounting.

#### 3. Post-hand commentary

Same pipeline as #2, but the categorizer receives the just-resolved hand context and emits **post-hand variants** with **boosted axis impact** via `context_multiplier`:

| Pure chat category | Post-hand variant | Context multiplier |
|---|---|---|
| `TRASH_TALK` | trash_talk_after_loss | 1.5× |
| `TAUNT_POST_WIN` | taunt_after_win | 2.0× |
| `COMPLIMENT` | genuine_compliment_after_loss | 2.0× |
| `FRIENDLY_BANTER` | console_after_their_bad_beat | 1.5× |

Same dispatch table; `record_event` accepts the `context_multiplier` and scales shifts at the call site.

#### Abuse prevention (chat only)

`SessionChatState` is an in-memory map keyed by `(player_id, target_id)` per active session. **"Session" = from sit-down to stand-up at any cash table** (or from game start to game end in non-cash modes). Stand-up resets the counter. Not persisted.

State per (player, target):
- `message_count_this_session: int`
- `axis_movement_this_session: Dict[axis, float]`

Guards:
1. **Diminishing returns:** Nth chat message contributes `1 / (1 + N*k)` of base impact (k = 0.5 starting value).
2. **Per-session axis cap from chat:** chat alone can move likability by at most ±0.20 in a session. Once hit, further chat events on that axis no-op.

Hand-outcome events are **not** subject to these caps.

### What AI chat does

AI output (existing `dramatic_sequence` beats) **reads** projected relationship axes to color tone — high-heat AI talks trash, high-likability AI is gracious. It does **not** move axes (one-directional). Avoids feedback loops where the AI taunts itself into a heat spiral.

## Part 2: Cash Game Mode

### Architecture (as shipped — revised post-rewrite)

Cash mode is implemented as a **flavor of the existing tournament
game flow**, gated by a `cash_mode=True` boolean on
`game_state_service`'s `game_data` dict. Cash games use the same
`StateMachineAdapter`, the same `HybridAIController` (and other bot
types), the same `progress_game` action loop, the same SocketIO
emits, the same React UI at `/game/<game_id>`, the same
`/api/game/<id>/action` route. The only deltas vs tournament games:

- **No `tournament_tracker`** — so `handle_eliminations` and
  `check_tournament_complete` naturally no-op via their existing
  no-tracker early-return.
- **Bankroll integration** at sit-down / leave / topup / between-hand
  AI refill — via `/api/cash/*` routes and the `cash_mode/`
  package's pure accounting helpers.
- **AI refill** between hands when a non-human seat busts —
  `_refill_cash_seats` in `flask_app/handlers/game_handler.py`.
- **`game_id` "cash-" prefix** so the continue-games list filters
  cash sessions out (they don't belong in the saved-game flow).
- **`cash_mode` info block** in the SocketIO state emit, so the
  React UI can render bankroll + buy-in caps without a second
  fetch.

The earlier-drafted `cash_mode/session.py` orchestrator was
replaced by direct tournament-flow integration in commit
`b2a0ad36` (May 2026). See `CASH_MODE_V1_WIRING_PLAN.md` for the
superseded design and the lessons that prompted the rewrite.

### Why it's a separate "mode" anyway

Even though it shares the tournament infrastructure, cash mode
is a distinct **product** mode:

- Tables with their own persistent state (`CashTable.stacks` lives
  on the state machine's player tuple; bankrolls live in
  `player_bankroll_state` / `ai_bankroll_state`)
- Sit-down / leave / top-up mid-session (between hands)
- Persistent bankrolls across sessions and games
- (v2) Lobby with concurrent tables

Lives in a `cash_mode/` package for the bankroll dataclasses +
pure accounting helpers + (mostly vestigial) `CashTable` dataclass.
**The hand engine and game orchestration are shared verbatim with
tournament mode.**

### v1 scope (single-table foundation) — SHIPPED

**Status**: Shipped on the `phase-1` branch in two arcs:
  - First arc (commits `613c0e9b` → `bcfe4a69`, May 2026): parallel `CashSession` orchestrator + dedicated routes.
  - Second arc (commits `b2a0ad36` → `08b50900`, May 2026): rewrite to tournament-flavor architecture above; deleted the parallel orchestrator (-2815 LoC net).

Per-personality bankroll knobs tuned for all 53 seeded personalities; 95 tests passing (down from 117 since the rewrite deleted the parallel-orchestrator tests that no longer apply). v2 unblocked.

**Per codex review, v1 cash mode is a single cash table with persistent bankroll.** This proves bankroll/stack accounting, persistence semantics, and the integration with the relationship layer's tier-modifier seam before adding the multi-table lobby complexity.

v1 ships:
- One cash table (size, stakes selectable at "start cash session" entry, no concurrent tables)
- Persistent player bankroll (fresh-grant on full bust)
- Persistent per-personality AI bankrolls + regen
- Sit/leave/top-up between hands
- Mid-hand quit → forfeit table stack
- Bust handling
- AI session behavior: bust-only (no stop-loss/stop-win in v1)
- Relationship layer integration (rivalry-seek deferred until lobby exists in v2)

v2 adds: multi-table lobby, AI table selection priorities, rivalry-seek seating, stop-loss/stop-win knobs.

v3 adds: AI-vs-AI background simulation when player not seated.

### Data model

```python
@dataclass
class CashTable:
    table_id: str
    stake_label: str              # e.g. "$10 table"
    big_blind: int                # in chips
    min_buy_in: int               # default 40 BB
    max_buy_in: int               # default 100 BB
    seats: List[Optional[str]]    # personality_id or "player" or None
    seat_count: int               # 6 (6max)
    hand_in_progress: bool        # blocks sit/leave/topup

@dataclass
class AIBankrollState:
    personality_id: str           # stable ID, not display name
    chips: int                    # current bankroll
    last_regen_tick: datetime     # for projection-on-read

@dataclass
class CashSessionState:
    player_id: str
    table_id: str
    session_pnl: float = 0.0
    sessions_together: Dict[str, int] = field(default_factory=dict)  # opp_id -> count
    started_at: datetime = field(default_factory=datetime.utcnow)
    # NOT persisted across stand-up

@dataclass
class PlayerBankrollState:
    player_id: str
    chips: int
    starting_bankroll: int        # fresh-grant amount on full bust
```

### Personality identity

**Stable `personality_id`** is required before cash persistence. Display names are not unique — custom personalities can collide.

**Migration / source-of-truth rules:**
- **Existing personalities in `personalities.json`** get `personality_id` backfilled as a one-time slug of the display name (e.g., `"Donald Trump"` → `"donald_trump"`). Slug collisions in the seed corpus are resolved by an explicit `_v2` / `_v3` suffix at seed time; once an ID is assigned to a row it never changes.
- **Renamed personalities** keep their original `personality_id`. Renames only touch the display-name field; persisted state (bankrolls, relationships) stays attached to the ID.
- **AI-generated / user-created personalities** are assigned a UUID-based `personality_id` at creation time. The personality manager UI is the authoritative ID issuer for these.
- **DB-seeded personality records** carry the same `personality_id` as the JSON entry that seeded them. The seed migration is idempotent — re-seeding an existing personality is a no-op if the ID already exists.
- **References from active saved games:** existing `opponent_models` rows persist display names. The v1 migration adds a `personality_id` column to that table (nullable initially) and a backfill pass that maps existing names → IDs via the seed table. Rows that can't be mapped are flagged for manual review, not silently dropped.
- **Player IDs** follow the same pattern but live in the existing user/auth system. The relationship/cash mode work assumes a `player_id` is already available; no new identity scheme is introduced player-side.

All cash-mode persistence (`AIBankrollState`, `relationship_states`, `cash_pair_stats`) and the in-memory `OpponentModelManager.models` map key on `personality_id` / `player_id`. Display name is for UI only.

### Bankroll knob storage

`personalities.json` is the **authoring source**. On startup / re-seed, the existing personality loader copies values into the DB-backed `PersonalityRecord`. Cash mode reads `bankroll_cap`, `bankroll_rate`, `buy_in_multiplier`, `stop_loss_buy_ins`, `stop_win_buy_ins`, `stake_comfort_zone` from the DB-backed record at runtime — same pattern as other personality knobs. Updating JSON requires re-seed (existing convention).

### Bankroll regen (pure projection on read)

```python
def project_bankroll(state: AIBankrollState, cap: int, rate: int, now: datetime) -> int:
    elapsed_days = (now - state.last_regen_tick).total_seconds() / 86400
    return min(cap, state.chips + int(rate * elapsed_days))
```

The persisted `chips` and `last_regen_tick` only update when an explicit write happens (AI sits down at a table — chips become the projected value, `last_regen_tick = now`; AI wins/loses chips during play — chips updated, `last_regen_tick = now`).

Same pattern as `project_heat`. Eligibility checks call `project_bankroll`. No background timer.

### Stakes ladder

| Label | Big blind | Min buy-in (40 BB) | Max buy-in (100 BB) |
|---|---|---|---|
| $2 | $0.02 | $0.80 | $2 |
| $10 | $0.10 | $4 | $10 |
| $50 | $0.50 | $20 | $50 |
| $200 | $2.00 | $80 | $200 |
| $1000 | $10.00 | $400 | $1000 |

UI uses the friendly "$X table" notation, not online-poker NL-X shorthand. v1 entry screen lets the player pick a stake before sitting (single-table mode).

### Player flow (v1)

```
main menu
  → cash game mode → pick stake & seat (single table)
      → choose buy-in (table min..max)
          → play poker
              → leave table (between hands) → cash mode home with stack returned to bankroll
              → bust at table:
                  → if bankroll ≥ any stake's min_buy_in → cash mode home
                  → if bankroll fully busted → fresh bankroll grant → cash mode home
              → top up (between hands) — pull from bankroll up to max_buy_in total stack
```

### Bust semantics

> **Shipped, v1 (sponsorship model) + Path B (AI-personality sponsors).** The original "auto fresh-grant on full bust" rule was retired during playtest — too generous, no stakes texture. Replaced by the **sponsorship loan flow** (see `CASH_MODE_SPONSORSHIP_HANDOFF.md` for v1 and `CASH_MODE_PATH_B_HANDOFF.md` for AI lender extension). Summary below.

- **Hard bust (AI):** AI loses entire bankroll, leaves the table, ineligible to play until `project_bankroll` brings them above some stake's `min_buy_in × buy_in_multiplier`. Real-time gated.
- **Hard bust (player) — in-table:** between hands, when player's `Player.stack == 0`, the server emits a `cash_rebuy_needed` or `cash_bust` SocketIO event.
  - **`cash_rebuy_needed`** (bankroll ≥ this table's `min_buy_in` and no active loan): modal offers Rebuy / Rebuy max / Leave. Player chooses to keep playing here or stand up.
  - **`cash_bust`** (bankroll too low for this table's min, OR active loan): modal forces Leave — must return to `/cash` to pick a lower stake or take a sponsor at a higher one.
- **Player sponsor loan (replaces auto fresh-grant):** when `bankroll < this tier's min_buy_in` AND `bankroll ≥ prev tier's min_buy_in`, the stake picker at `/cash` shows the tier as "Sponsor required." Tapping opens the **SponsorModal** with up to 3 mixed offers — Path B preferentially surfaces **AI-personality lenders** (each with their `lender_profile` knobs and relationship-aware terms), filling any remaining slots with anonymous house archetypes. The loan lands directly on the table stack — never in bankroll — closing the "pocket the spare loan" exploit.
- **AI-personality lender path:** when the player accepts a personality offer, `active_loan_lender_id` on `player_bankroll_state` is set to that AI's `personality_id`. The route emits `RelationshipEvent.SPONSORSHIP_OFFERED` (small respect + likability bump in both directions — the AI extended trust, the player accepted it). At leave-time, `settle_loan_on_leave` credits `sponsor_total` back to the AI lender's persistent bankroll (clamped to their cap, mirroring Path A's cash-out rule), and fires `LOAN_REPAID` or `LOAN_DEFAULTED` based on whether `chips_at_table` covered the floor. Defaulting is the sharpest negative event in the relationship calibration — `respect -0.30, heat +0.30, likability -0.20`.
- **Leave-time loan settlement:** see `cash_mode/loan_settlement.py:settle_loan_on_leave`. Player's `chips_at_table` first pays the floor (`int(amount × repayment_floor)`); whatever's left has the sponsor's cut applied (`int(remaining × rate)`); the residue returns to bankroll. Edge cases: chips < floor → all to sponsor, balance forgiven (v1 — no reputation hit yet for full busts even on AI loans); chips_at_table = 0 → no event fires; no active loan → existing chips return to bankroll verbatim. Loan fields always reset on leave (session-scoped), including `active_loan_lender_id`.
- **Tier-climbing rule:** sponsor-eligible iff `bankroll < this tier's min` AND (`tier is lowest` OR `bankroll ≥ prev tier's min`). Step-by-step; can't jump $2 → $1000 with one Whale Backer. Volatile (current bankroll only); no persistent unlock tracking in v1.
- **Mid-hand quit (deliberate stand-up):** forfeit **entire table stack** to the pot, split among players who finish the hand. Bankroll back home is untouched.
- **Disconnect:** **60-second reconnect grace window** (starting value, tunable). The hand auto-checks/auto-folds on the player's turn during the window. Reconnect within window → resume seated with current stack. Window expires → treated as mid-hand quit, table stack forfeit. The grace window prevents punishing transient network failures while still preventing reconnect-as-fold-equity-saving over multiple hands.

### Bankroll accounting order

Chips move between `PlayerBankrollState.chips` (bankroll) and the player's seat stack at the table on the following events. Order matters; v1 implementation must follow these exactly to avoid duplication/loss bugs.

| Event | Bankroll | Table stack | Notes |
|---|---|---|---|
| Sit down (buy-in) | debit buy-in amount | set to buy-in amount | Atomic; rolls back together if seat allocation fails |
| Top up (between hands) | debit top-up amount | credit top-up amount | Capped so resulting stack ≤ `max_buy_in` |
| Leave table (between hands) | credit current stack | set to 0 | Stack returns home in full |
| Bust at table (lost final chips in hand) | unchanged | set to 0 | Bankroll was already debited at buy-in/top-up; nothing returns |
| Full bankroll bust | **see Bust semantics §** — sponsor loan flow replaces the old auto fresh-grant | n/a | Player is between tables when this fires; `/cash` entry shows sponsor offers for the next-tier-up stake |
| Mid-hand quit | unchanged | forfeit to pot (set 0) | Stack lost to opponents in the hand |
| Disconnect timeout | unchanged | forfeit to pot (set 0) | Identical to mid-hand quit after grace window expires |
| Hand settlement (winnings) | unchanged | credit winnings (or debit losses) | Settlement happens before next sit/leave/top-up can fire |

### Player bankroll edge cases

- **Side pots:** when player goes all-in for less than other players' bets, the standard side-pot resolution applies. Player wins only the main pot they're eligible for. Bankroll accounting: winnings credited at hand resolution; no special case.
- **Partial all-in survival:** if player has 0 chips on the table but the hand is still live (waiting for showdown), they remain seated. If they win, winnings credit to their table stack. They cannot top up until between hands.
- **Mid-hand sit-out request:** ignored. Sit/leave/topup blocked while `hand_in_progress` is true (matches the data model). Player must wait for hand-end to act on the table.

### Sit / leave rules

- Between hands only — `CashTable.hand_in_progress` blocks sit/leave/topup.
- No standup cooldown — player stands up between hands, can sit again at any eligible table.
- Top up between hands: pull from bankroll up to `max_buy_in` total stack.

### AI session behavior (v1)

- **Bust-only**: AI plays until losing the entire table stack.
- `stop_loss_buy_ins` / `stop_win_buy_ins` per-personality knobs **deferred to v2** — adds session-length tracking complexity not justified for single-table v1.
- AI does not stand up mid-session unless busted.

### AI table selection — as shipped (v1.5 + full sim)

> **Updated 2026-05-19:** lobby v1.5 + full sim shipped. The
> selection and seating-cadence subsystems below are live. See
> [CASH_MODE_FULL_SIM.md](../technical/CASH_MODE_FULL_SIM.md)
> for the technical reference on what runs per lobby read.

**Roster maintenance** runs inside
`cash_mode.lobby.refresh_unseated_tables`, called from
`GET /api/cash/lobby`. For every table without a human seated:

1. **Sim hand cadence**:
   - Gap < 30 s since last refresh → probability-gated single
     hand (`hand_sim_prob`, default 0.25).
   - Gap ≥ 30 s → burst-tick `floor(gap / 20s)` hands, capped
     at 30 per table per refresh.
2. **Per hand**: rotate dealer to next occupied seat, run
   `play_one_hand` (TieredBotController-driven), mutate seat
   chips, persist dealer position to `cash_tables.dealer_idx`.
3. **Movement decisions** evaluated against the post-sim chip
   counts (`refresh_table_roster`):
   - **Affordable** — `project_bankroll() >= min_buy_in × buy_in_multiplier`.
   - **Forced leave** — chips ≤ 0.3 × buy_in (bust + recovery).
   - **Stake up** — chips ≥ 2.0 × buy_in AND can afford next tier.
   - **Take break** — same big-win threshold, smaller probability.
   - **Bored move** — base-rate cycling (0.015 per refresh).
4. **Live-fill** rolls on open seats — AIs from the idle pool or
   the broader eligible pool walk up. Personality bankroll is
   debited into the seat.

**Still on the v2/v3 wishlist**:

- **Stake comfort zone bias** — `stake_comfort_zone` knob exists
  but isn't yet used to bias movement decisions.
- **Highest affordable upward drift** — current stake-up is
  one-tier; "shop up multiple tiers when bankroll explodes"
  isn't wired.
- **Rivalry seek** — `project_heat(player) > threshold` biases
  toward player's table. **The cash-mode payoff of the
  relationship layer.** Not yet implemented; would slot into
  `evaluate_ai_movement` as a new decision option.

### v1 architectural invariants (so v2/v3 don't require redesign)

These constraints make v1 a foundation, not a dead-end:

- **Tables are first-class objects** with their own state. v1 has one, v2 has many. No assumption of "the current game."
- **Hand orchestration is decoupled from player presence.** v1 only runs hands when the player is seated; v3 will run hands without humans. The function signature accepts "seated players are X, Y, Z" — doesn't care which are human.
- **AI bankrolls live in their own state object** (`AIBankrollState` keyed by `personality_id`), not buried in game state. Survives across tables, sessions, games.
- **All time-based effects are pure projection on read.** `project_heat`, `project_bankroll`. Persistence writes only on real events.
- **Cash-session state is separate from relationship state.** Sessions reset; relationships don't.

## Part 3: Endgame Economy (design intent only — separate system)

The endgame economy is **its own product slice**, not an extension of the relationship system. v1 ships none of it; this section captures intent and dependencies so we don't paint into a corner.

### Problem

A player who reaches the top of the stakes ladder ($1000 table) and grinds successfully will accumulate chips indefinitely. We need chip sinks, non-chip progression, and a soft cap on stakes.

### Chip sinks (each is a real durable contract, NOT mostly UI)

1. **Staking busted AI** — durable contract: who owes whom, split %, settlement on next bankroll event. Emits its own `EconomyEvent`s; some emit `RelationshipEvent`s as side effects (`TRUST_EXTENDED`, `BETRAYAL`).
2. **Private home game** — player owns a table with custom invite list. Durable ownership state. Per-session run costs.
3. **Character unlocks** — durable availability flag per personality_id per player_id. Cost paid in chips at unlock.

Each of the above needs its own design pass when prioritized.

### Non-chip progression

- **Affinity completion** — track per-personality max-affinity milestones. Pure read on `RelationshipState`.
- **Heads-up gauntlet** — defeat-every-celebrity-in-HU achievement. Tracks per-personality wins.
- **Hand-of-fame** — auto-saved legendary hands. Already mostly captured by `MemorableHand`.

The first two need their own progress tracking; the third is UI on existing data.

### Soft cap on stakes

**Don't have infinite stakes.** Cap the ladder at $1000. Past that bankroll, money is *only* for sinks. This is fine because the sinks above are good.

## Part 4: Dialogue Scope (v1)

- **At-table only.** All character voice happens during hands. Lobby is silent / functional. (v1 has no lobby anyway.)
- **AI output (`dramatic_sequence`) reads projected relationship axes** to color tone — high-heat AI taunts, high-likability AI is gracious. Doesn't move axes.
- **Player chat is one-directional input** to the relationship system (categorizer → `RelationshipEvent`). AI chat back is output only.
- **Deferred:** lobby tooltip lines, pre-/post-session greeting beats (these depend on the v2 lobby existing).

## Implementation order

### Phase 1: Relationship layer foundation (independent of cash mode)

1. **`RelationshipEvent` enum + dispatch tables (including UNKNOWN sentinel).** Actor's-POV and mirror tables. No behavior yet; just the vocabulary.
2. **Rename `MemorableHand.memory_type` → `event: RelationshipEvent`.** Update emitters to pass enum values. DB column name stays `memory_type` — serialization writes `event.value`. Load coerces unknown strings to `UNKNOWN` with a WARN log.
3. **Personality identity migration.** Add `personality_id` to `personalities.json`, backfill existing entries, add `personality_id` column to `opponent_models`, run name→id backfill pass for active games. (Done early so the rest of Phase 1 keys on IDs from day one.)
4. **`RelationshipState` dataclass + `project_heat`.** Added to `OpponentModel` as a sibling field. In-memory only at this step.
5. **New `relationship_states` table + repository methods (projection on read by default; admin raw-read variant).** Cross-session keyed by `(observer_id, opponent_id)`.
6. **`OpponentModelManager.record_event()`.** Single choke point. Project-first-then-apply. Bilateral updates. Hooks the existing `MemorableHand` path when impact is high enough. Keys on IDs.
7. **`get_relationship_modifier()` reader — strictly pairwise.** Pure projection. Returns `RelationshipModifier` for a single (observer_id, target_opponent_id) pair. **Does not do multiway target selection** — that's Phase 2's job because it needs game state. Not wired into the controller yet.

Seven small commits, each shippable. None touch the controller's decision path.

### Phase 2: Wire the modifier seam

**Prerequisite (before coding):** verify the exact existing offset variables / function call sites in `poker/tiered_bot_controller.py` and `poker/strategy/exploitation.py` that each modifier should scale. The conceptual targets in the Tier modifier seam table are correct; the precise names may differ from "bluff probability shift" / "fold-to-aggression offset" / "call-equity threshold."

8. **Multiway target selection at the call site.** In `_apply_exploitation`, select the target opponent per the rules in "Multiway target selection" — eligible-opponent filter, aggressor reuse via `_select_exploitation_stats_from_spots`, heat-max fallback with deterministic tie-breaks. The reader itself stays pairwise.
9. **`_apply_exploitation` accepts a `RelationshipModifier`.** Calls `get_relationship_modifier(observer_id, selected_target_id, now)` once with the chosen target. Scales the verified existing offsets. Composition order: pattern detection → modifier scaling → clamp/gating. Trace gains a `relationship_modifier` field.

### Phase 3: Hand-outcome detector

10. **`HandOutcomeDetector` with adapter table.** Maps existing pressure/equity signals to `RelationshipEvent`. Multiway (actor, target) pairs use the same chip-flow allocation that drives `cumulative_pnl`. Dedup keyed by `(hand_id, actor_id, target_id, event)`.

After Phase 3 the relationship layer is fully load-bearing on hand outcomes alone — chat is not a critical path dependency.

### Phase 4: Cash mode v1 (single-table foundation)

11. **`cash_mode/` package with single-table state.** `CashTable`, `AIBankrollState`, `PlayerBankrollState`, `CashSessionState`, `CashPairStats`. Persistence repos.
12. **Bankroll regen (`project_bankroll`) — projection on read, project-first-then-apply on writes.**
13. **Bankroll knob loading** through DB-backed `PersonalityRecord`.
14. **Sit / leave / top-up orchestrator** — between-hand only. Follow the bankroll accounting order table exactly.
15. **Bust handling** — AI hard bust, player fresh-grant on full bust.
16. **Mid-hand quit + disconnect grace window** — 60s reconnect window, auto-fold during window, forfeit on timeout.
17. **Side-pot accounting verification** — tests.
18. **`cumulative_pnl` / `cash_pair_stats` updates at hand settlement** — chip-flow allocation feeds both `BIG_WIN` / `BIG_LOSS` events and the stats table.

### Phase 5: Chat inputs (additive, post-cash-v1)

19. **Chat categorizer** with `prompt_template='relationship_chat_categorization'`. Hand-context-aware. Confidence floor defaults to `noise` (no shift).
20. **`SessionChatState` + abuse-prevention guards.** Diminishing returns and per-session axis caps. Untargeted chat fans out to each opponent's bucket (not a "None" bucket).
21. **Post-hand commentary context multipliers.**

### Phase 6: Cash mode v2 (multi-table lobby)

22. Multi-table lobby state + list UI.
23. AI table selection: affordable + comfort zone + upward drift.
24. Rivalry-seek seating (the relationship-layer payoff in cash mode).
25. `stop_loss_buy_ins` / `stop_win_buy_ins` per-personality knobs.

### Phase 7: Endgame economy (each is its own design pass)

26. `EconomyEvent` taxonomy + persistence.
27. Staking contracts (durable, settlement).
28. Hand-of-fame UI (pure read).
29. Heads-up gauntlet progression.
30. Character unlocks (durable availability).
31. Private home games (largest, most content-bound).

### Phase 8: Future (deferred)

- AI-vs-AI background simulation (v3 cash mode).
- Lobby ambient lines, pre-/post-session beats.
- Scripted scenes, DMs, story content.

## Open questions / deferred

- **Tuning the dispatch table.** Starting numbers are guesses. Once Phase 1 ships and gets hand volume, axis shifts will need calibration. Add an `experiments/` config that lets us A/B different shift magnitudes.
- **Mirror table for `record_event`.** Each event needs its target's-POV shifts declared explicitly. Listed conceptually; needs enumeration in Phase 1 implementation.
- **Initial player bankroll value.** $100 (50 buy-ins at lowest stake) is the starting recommendation but is a UX call best made when v1 is playable.
- **Chat categorizer confidence floor.** Default 0.6 is a starting value; tune from real chat data.
- **Reconnect grace window length.** 60 seconds is a starting value. Real users on flaky networks may need longer; abuse-watchers will want shorter. Tune from telemetry.
- **`STRONG_FOLD_SHOWN` detection.** Requires explicit "would have won" reveal which the engine doesn't currently surface. Either add the reveal in Phase 3 or drop this event from v1.

## Related files

| File | Role |
|---|---|
| `poker/memory/opponent_model.py` | Existing — gets `RelationshipState` field, `RelationshipEvent` enum, `record_event` API; `MemorableHand.memory_type` renamed to `event` |
| `poker/tiered_bot_controller.py` | Existing — `_apply_exploitation` accepts `RelationshipModifier` (Phase 2) |
| `poker/repositories/game_repository.py` | Existing — new `relationship_states` + `cash_pair_stats` tables + repository methods (projection on read); `memorable_hands.memory_type` column stays, holds enum `.value` strings with `UNKNOWN` quarantine for legacy rows; `opponent_models` gets `personality_id` column |
| `poker/personalities.json` | Existing — gets bankroll knobs + stable `personality_id` |
| `cash_mode/` (new) | Single-table state, bankroll regen, orchestrator, event detector |
| `core/llm/tracking.py` | Existing — `CallType.CATEGORIZATION` reused with `prompt_template='relationship_chat_categorization'` |
| `poker/moment_analyzer.py` | Existing — `is_bad_beat` / showdown detection adapted by `HandOutcomeDetector` |
