---
purpose: Design and implementation plan for AI vice spending — a chip-sink that doubles as a psychology-regulation mechanic, with LLM-generated flavor narration driven by character + current psych state rather than authored vice configs.
type: design
created: 2026-05-21
last_updated: 2026-05-23
---

# Cash Mode — AI Vice Spending

> **Why this exists:** `starting_bankroll` is a regen target, not a
> ceiling (`cap_clamp` was retired alongside the ceiling concept —
> `core/economy/ledger.py:43-48`, `cash_mode/bankroll.py:194`). AIs
> that win big can stay flush forever — there is no voluntary chip-
> spend behavior in the cast today. With the staking system online
> (Phase 1-4.5), economic dynamism depends on chips circulating
> between AIs; permanently rich personalities flatten the curve and
> erode the meaning of "Bezos is doing well" vs "Hemingway is broke."
> Vice spending gives the rich-and-tilted AI a way to spend down,
> with character flavor attached.

## The mechanic in one sentence

**When an AI is flush, they probabilistically blow some chips on
something character-appropriate, disappear from the lobby for a
character-chosen duration, and come back with their psyche partly
restored.**

Three load-bearing parts:

1. **Chip sink** — the primary economic role. Excess wealth flows
   back to the central bank in a way that scales with how flush the
   AI is.
2. **AI is offline during the vice** — they can't be seated,
   staked, or hand-picked while at the spa / bar / etc. The
   absence is the player-visible weight of the event.
3. **Psych recovery is a side effect, not a trigger** — confidence,
   composure, and energy drift back toward baseline on return.
   Money buying happiness is the bonus, not the gate.

The character-appropriate flavor (what they spent on, how long
they're gone) comes from an LLM call at fire time — no authored
vice fields anywhere in the personality config.

## Design tensions this resolves

Today, regen alone keeps poor AIs solvent (chips flow in toward
`starting_bankroll`). But there's no symmetric flow for rich AIs.
A Bezos who wins $30K stays at $30K indefinitely. The only existing
chip destruction is `table_rake` (per-hand pot skim, owner-neutral)
and `house_stake_settle` (specific to the staking path). Neither
scales with personality wealth or narrative state.

Vice spending fills the gap with character attached. Each event is:

- Triggered by THIS AI's specific bankroll + psych state
- Sized by THIS AI's excess + drift magnitude
- Narrated by THIS AI's voice via LLM
- Mechanically connected to THIS AI's psychology (the spending
  *helps* them stop tilting)

The dossier section "they spent $X this week" becomes a real
character signal. The lobby ticker carries flavor moments instead
of silent ledger events.

It's a **soft, voluntary spend-down** that bites the rich, ignores
the poor, and expresses the personality. Unlike `cap_clamp` (which
was a hard destruction at credit-time, now deprecated), vice fires
asynchronously in the lobby refresh and is gated on personality
state, not just chip count.

## Trigger formula

Vice has two factors: **wealth** (the gate) and **pressure** (a
multiplicative modifier). Wealth alone produces a baseline vice
rate; pressure amplifies it. Broke AIs never vice regardless of
how tilted they are.

### Wealth — concentration relative to the cast

```
concentration  = bankroll / cast_median
excess_ratio   = max(0, concentration − CONCENTRATION_FLOOR)
```

Where `cast_median` is the median bankroll across **all** AI bankroll
rows in the sandbox (a single SQL query per refresh, robust to
outliers — a $2M dominant AI doesn't pull the median up the way the
mean would).

- `CONCENTRATION_FLOOR = 2.5` — an AI must hold ≥ 2.5× the cast
  median to be vice-eligible. In a sandbox with median = $14K that's
  a $35K floor; in a $30K-median sandbox it's $75K. Scales naturally
  with the economy.
- `MIN_CAST_MEDIAN_FOR_VICE = 5_000` — when the cast median is below
  this, vice suppresses entirely. "Everyone is broke" should not
  produce vice; there's no real top to drain.

**Why concentration replaced the prior per-personality gate:** the
earlier formula used `bankroll > starting_bankroll × 1.2`, which
made a low-baseline character (e.g., Ace Ventura at $24K with a
$20K starting_bankroll) qualify for vice while objectively being
mid-pack. The concentration gate measures wealth relative to the
cast, not relative to each character's personal comfort, so vice
genuinely targets the wealthy. The per-character flavor still comes
through the LLM narration; the *trigger* is now an economic signal.

### Pressure — worst-axis psych distress

```
pressure = 1.0 − min(confidence, composure, energy)
```

"Whichever dynamic axis is in the worst shape drives the urge to
indulge." This catches a drained-but-collected Hemingway (low
energy), a confident-but-tilted Napoleon (low composure), and a
shaken-but-poised Bezos (low confidence) — all situations a single-
axis or averaged formula misses.

Only the three runtime-mutable axes (`poker/psychology_model.py:176-209`)
participate. Static identity anchors and relationship axes don't
contribute. The min is taken against the current state directly,
not against drift from baseline — see "min vs drift" in open
questions below.

**Note on the natural floor:** the three axes typically sit in the
0.5-0.9 range when nothing is wrong, so `min` lands around 0.5 for
a calm AI → pressure ≈ 0.4-0.5 even at rest. This is intentional:
every character has *some* baseline indulgent urge, spiking when an
axis goes low. The dynamic range from calm-to-tilted is roughly
0.4 → 0.9, not 0 → 1.0; `PRESSURE_BOOST` is tuned with that floor
in mind.

### Probability composition

```
vice_prob = clamp(0, MAX_PROB,
                  excess_ratio × EXCESS_WEIGHT × (1 + pressure × PRESSURE_BOOST))
```

- `EXCESS_WEIGHT = 0.04` per unit of excess_ratio
- `PRESSURE_BOOST = 0.6` — fully-pressured AI vices ~50-60% more
  often than a calm one of equal wealth
- `MAX_PROB = 0.25` — a maximally-flush, maximally-pressured AI
  vices ~25% per refresh

Properties:
- **Concentration gate** stays hard: below 2.5× cast median ⇒
  `vice_prob = 0` regardless of pressure
- **Wealth dominates**: a calm flush AI still vices regularly
- **Pressure amplifies**: same wealth, tilted = ~1.5× the calm rate
- **Tilted + concentrated is the protagonist** of vice events; calm
  flush is the supporting cast; mid-pack and broke go unmedicated
- **Self-balancing**: in a wealthy sandbox the threshold rises with
  the median; in a poor sandbox it falls (until the median dips
  below `MIN_CAST_MEDIAN_FOR_VICE`, at which point vice suppresses
  entirely)

Worked examples (cast median = $14K, threshold = 2.5 × $14K = $35K):

| AI | bankroll | concentration | excess | conf | comp | energy | pressure | vice_prob |
|---|---|---|---|---|---|---|---|---|
| Median AI | $14K | 1.00 | 0.0 | 0.70 | 0.70 | 0.60 | 0.40 | **0.00** |
| Ace @ $24K (mid-pack) | $24K | 1.71 | 0.0 | 0.70 | 0.70 | 0.60 | 0.40 | **0.00** |
| AI @ $37K (just over) | $37K | 2.64 | 0.14 | 0.70 | 0.70 | 0.60 | 0.40 | 0.006 × 1.24 = **0.007** |
| AI @ $50K | $50K | 3.57 | 1.07 | 0.70 | 0.90 | 0.60 | 0.40 | 0.043 × 1.24 = **0.05** |
| Bezos @ $80K calm | $80K | 5.71 | 3.21 | 0.70 | 0.90 | 0.60 | 0.40 | 0.128 × 1.24 = **0.16** |
| Bezos @ $80K rough | $80K | 5.71 | 3.21 | 0.20 | 0.85 | 0.70 | 0.80 | 0.128 × 1.48 = **0.19** |
| $2M outlier | $2.17M | 155 | 153 | * | * | * | * | **0.25** (capped) |
| Broke AI | $5K | 0.36 | 0.0 | * | * | * | * | **0.00** |

A flush AI rolls multiple times before vice fires (vice_prob 0.20 →
expected gap ~5 refreshes ≈ ~40s of player-attention time), then
disappears for a meaningful chunk of wall-time (see "Vice state
model"). Once they return with less money — and possibly with the
worst axis pulled back toward baseline by the recovery — vice_prob
drops, and they re-enter the rotation.

This produces the **narrative arc** the design is reaching for:
rich AIs cycle between flush and spending; tilted rich AIs cycle
faster. No AI is locked into "perpetually rich and unmoving." The
cast economy stays dynamic because vice creates regular drains
proportional to wealth + distress.

## Amount formula

The vice cost scales with excess; the random multiplier keeps
events visually varied:

```
raw_amount = bankroll × (BASE_FRACTION + excess_ratio × EXCESS_FRACTION_WEIGHT)
              × random.uniform(0.5, 1.5)
```

Suggested:
- `BASE_FRACTION = 0.02` (2% baseline once vice is triggered)
- `EXCESS_FRACTION_WEIGHT = 0.03`
- Random multiplier ±50% so vice events aren't visually uniform.

**Constraints applied after raw calculation:**

1. **Minimum:** `MIN_VICE_AMOUNT = 50` — if raw_amount < 50 chips,
   skip the event entirely (no chip move, no ticker, no state).
   Sub-50 vice events are noise. Note: this is a backstop — by
   the time vice_prob is non-trivial, raw_amount is well above 50.
2. **Maximum per event:** `min(raw_amount, bankroll × MAX_VICE_FRACTION)`
   where `MAX_VICE_FRACTION = 0.15` — never blow more than 15% in
   one event. Keeps even a flush AI from spending themselves down
   in a single trip.
3. **Floor protection:** post-vice bankroll must stay above
   `starting_bankroll × 0.5`. The vice loop already gates on
   `excess_ratio > 0` (i.e. bankroll above `starting × COMFORT_FLOOR`),
   so reaching half-starting through one vice would require an
   `excess_ratio` that produces ≥ 70% spending, far beyond what
   the formula generates. Floor protection is a guardrail in case
   tuning shifts.

Result: vice events range from ~$300 (Hemingway barely above the
floor) to ~$5,000+ (Bezos at 5×) per fire, scaling smoothly with
how flush the AI is.

## Psychology regulation effect (side benefit)

Vice's pressure factor amplifies probability for tilted AIs — and
the act of vicing then pulls them back toward baseline. The two
sides lock into a feedback loop: distress increases vice rate,
vice reduces distress, the AI cycles back toward composure.

A successful vice fire applies a one-shot pull-toward-baseline on
the three dynamic axes (`confidence`, `composure`, `energy`) at
vice end. For an already-recovered AI this is a near no-op; for a
tilted AI it's meaningful recovery. The benefit scales with whoever
needs it most.

The recovery factor scales **logarithmically** with the amount spent
so rich vices are only *slightly* more beneficial than modest ones —
money can only buy so much happiness:

```
recovery_factor = BASE_RECOVERY + AMOUNT_BONUS × log10(amount / MIN_VICE_AMOUNT)
recovery_factor = min(recovery_factor, MAX_RECOVERY)

# Apply to the three dynamic axes only (confidence, composure, energy).
# Anchors themselves are immutable identity — never modified by vice.
new_confidence = current.confidence + (baseline_conf - current.confidence) × recovery_factor
new_composure  = current.composure  + (baseline_comp - current.composure)  × recovery_factor
new_energy     = current.energy     + (baseline_energy - current.energy)   × recovery_factor
```

Where `baseline_conf` and `baseline_comp` are computed via
`compute_baseline_confidence(anchors)` and
`compute_baseline_composure(anchors)`; `baseline_energy` is
`anchors.baseline_energy` directly.

Suggested constants:
- `BASE_RECOVERY = 0.25` — every vice gives this much pull-back
- `AMOUNT_BONUS = 0.05` — modest scaling above baseline
- `MAX_RECOVERY = 0.40` — hard ceiling
- `MIN_VICE_AMOUNT = 50` (already defined above)

This produces a deliberately flat-ish curve:

| Amount spent | log10(amount / 50) | Recovery factor |
|---|---|---|
| $50 (minimum)  | 0.0 | 0.25 |
| $500           | 1.0 | 0.30 |
| $5,000         | 2.0 | 0.35 |
| $50,000        | 3.0 | 0.40 (capped) |

A 100× larger vice spend buys roughly 1.6× the psych recovery, not
100×. The wealthy still recover faster because they vice **more
often** (the probability formula), but each individual event has
similar therapeutic effect across the wealth range.

The recovery is **a one-shot impulse on top of the existing
`PlayerPsychology.recover()` cadence** — it does NOT replace normal
recovery. Normal recovery uses an asymmetric pull (stickier below
baseline) at `anchors.recovery_rate` per-hand inside
`psychology_pipeline`; vice adds a separate symmetric pull-toward-
baseline that fires once at vice-event time. The two stack
multiplicatively in the limit: a tilted AI gets per-hand drift back
toward baseline AND a chunk of immediate recovery at vice fire.

The recovery hits **all three dynamic axes**, not just one. The act
of indulgence is general catharsis — tilted Hemingway who buys a
fishing boat is comforted across confidence, composure, AND energy,
not just one axis. This avoids needing per-vice "psych_target"
mappings (consistent with the doc's "no authored vice config"
principle).

**Relationship axes (`respect`, `heat`, `likability` in
`opponent_model.py:867`) are NOT touched by vice recovery.** Those
are inter-personality state and live in a separate repository.
"Buddha spent $X on a retreat" should not improve their respect for
Napoleon. Only `PlayerPsychology` axes are in scope.

**Stratification is real but soft.** A wealthy tilted AI vices more
often (higher probability) → heals through frequency. A poor tilted
AI vices rarely → heals through patience (existing recover() +
regen handle the recovery the vice isn't covering). The advantage
compounds slowly, not steeply. Combined with the staking system,
the cast still gets "fortunes diverge over time" arcs — but no one
is permanently locked into tilt because they can't afford a yacht.
The therapeutic ceiling is the same; only the frequency differs.

### Timing

The recovery applies at **vice end**, not at vice start. The
narrative reads: "Bezos went off, returned refreshed." Mechanically,
this also means the AI's psychology is frozen during the vice
window (they're not playing hands, so the per-hand `recover()`
doesn't fire either). All psych state change happens in one impulse
when vice expires.

### Persistence

Psychology lives at `(personality_id, sandbox_id)` in
`ai_bankroll_state.emotional_state_json` (schema v97). The active-
session cache (`cash_mode/full_sim._get_default_controller_cache`)
holds controllers with mutable `PlayerPsychology`; the persisted
blob is the cold-state copy. Vice-end recovery may run when the
controller is either active (cache hit) or idle (cache miss).

The implementation must:
1. On cache hit: mutate the cached controller's psychology, then
   call `bankroll_repo.save_emotional_state_json(...)` to flush.
2. On cache miss: load the blob, deserialize via
   `PlayerPsychology.from_dict()`, apply the one-shot pull, save
   the new blob. Do not warm the cache (idle AIs stay idle).

## Vice state model

Vice is a **status**, not an atomic event. While an AI is on a
vice, they're physically absent from the lobby: not in any seat,
not eligible to be staked, not eligible to be seated. The state
table tracks the window.

### Schema (new migration)

```sql
CREATE TABLE ai_vice_state (
    personality_id TEXT NOT NULL,
    sandbox_id     TEXT NOT NULL,
    started_at     TIMESTAMP NOT NULL,
    ends_at        TIMESTAMP NOT NULL,
    amount         INTEGER NOT NULL,
    duration_bucket TEXT NOT NULL,   -- 'short' | 'medium' | 'long'
    narration      TEXT NOT NULL,    -- the LLM line (or template fallback)
    PRIMARY KEY (personality_id, sandbox_id)
);
CREATE INDEX idx_vice_ends_at ON ai_vice_state(sandbox_id, ends_at);
```

The PK guarantees an AI can only be on one vice at a time. The
index supports the per-refresh expiry scan.

### Duration buckets

The LLM picks the bucket as part of its narration response (see
"LLM narration"). Each bucket maps to a wall-time range with light
jitter so two same-bucket vices don't land on the same second:

| Bucket | Range | Median |
|---|---|---|
| `short` | 15 - 30 min | ~22 min |
| `medium` | 30 - 90 min | ~60 min |
| `long` | 90 - 240 min | ~165 min |

Implementation: `ends_at = now + uniform(low, high)` for the chosen
bucket. Caller controls determinism via rng (tests can fix it).

If the LLM call fails or returns an unrecognized bucket, default to
`medium`. A vice always has a real duration; the system never
degrades to "instant vice."

### Lifecycle, per lobby refresh

The refresh path adds two passes around the existing table loop:

1. **Expiry pass — start of refresh** (`tick_vice_expirations`):
   bulk-select rows where `ends_at <= now AND sandbox_id = ?`.
   For each row:
   - Apply the one-shot psych recovery (see "Psychology regulation
     effect")
   - Emit `EVENT_VICE_END` ticker row
   - Delete the row
   - The AI becomes immediately eligible for seating in this same
     refresh

2. **Start pass — post-loop, after carry resolution**
   (`resolve_ai_vice_spending`): for each eligible candidate, roll
   vice_prob; on a fire:
   - Compute amount
   - Make the **synchronous** LLM call to get `{narration, duration_bucket}`
   - Insert `ai_vice_state` row with `ends_at` from the bucket
   - Apply chip move + ledger entry (`vice_spending`)
   - Emit `EVENT_VICE_START` ticker row
   - Do NOT apply psych recovery yet (deferred to expiry)

### Candidate set (revised)

Vice candidates are **idle pool only**. Sim-seated AIs (those at
AI-vs-AI tables during the refresh) are deferred until they
naturally rotate to idle. Reasons:

- AIs at sim tables are mid-burst when vice resolution runs (post-
  loop). Forcibly removing them from a seat mid-refresh would
  fight the existing churn pattern in `refresh_table_roster`.
- The natural seat churn rotates AIs in and out of the idle pool
  regularly — a rich AI at a sim table won't be stuck there for
  long.
- Personalities at human-seated tables are already exempt (same
  rule as before).

Concretely:

```python
candidates = {
    entry.personality_id
    for entry in idle_pool
    if entry.personality_id not in on_vice  # already vicing
}
```

Where `on_vice` is the set of personality_ids with an active
`ai_vice_state` row (loaded once at refresh start).

### Visibility — where the player sees vice

A vice creates four distinct surfaces:

1. **Lobby ticker** — two events per vice (start + end). Start
   shows the narration ("Napoleon commissioned an oversized bronze
   bust..."); end is shorter ("Napoleon is back").
2. **Lobby personality cards / idle-pool view** — AIs on vice
   appear in a separate "Away" group (or are filtered out of the
   active list, depending on the chosen surface). Each card shows
   the vice narration as a subtitle and a relative ETA ("back in
   23 min"). They're not seat-clickable while away.
3. **Dossier** — the personality's dossier entry shows current
   vice state when applicable, with ETA. Surfaces "ah, they're at
   the spa right now" when the player checks.
4. **Sit-down / staking flows** — both check active vice state
   and refuse the action with a clear message ("Bezos is at the
   private airfield for another 47 min"). Prevents the player
   from racing against vice timing.

### Eligibility integration

The vice state acts as a blocking filter on every "is this AI
available?" check:

- `idle_pool` listings: filter out personalities with active vice
- Seating offers: same filter
- Staking eligibility (both as borrower and as staker): same
  filter
- Cross-table roster refresh: vice-active personality_ids are not
  candidates for empty seats

The simplest implementation: a `is_on_vice(personality_id,
sandbox_id, now) -> bool` helper in `cash_mode/ai_vice_spending.py`
that wraps a lookup against the active state, called from every
gate above.

## LLM narration

The flavor lives entirely at fire time via an LLM call. No authored
vice categories, no per-personality vice mappings, no template
strings.

### Prompt shape

The LLM returns a structured JSON response with both the narration
and a duration bucket. The character picks how long they're gone —
Buddha goes for a long retreat, Hemingway hits the bar for a short
visit, Bezos books a long private trip.

```
You are writing flavor for a fictional poker AI character who is
about to disappear from the cash-mode lobby for a while to indulge
in a personal vice. Return JSON with two fields:

  - "narration": ONE sentence describing what they're doing.
  - "duration": one of "short", "medium", or "long" — how long
    they'll be away.

Character:
  Name: {personality.name}
  Style: {personality.play_style}
  Attitude: {personality.attitude}
  Anchors: {curated anchor block — aggression, ego, poise, ...}
  Verbal tics: {personality.verbal_tics}

Current state:
  Bankroll: ${bankroll}
  Just spent: ${amount}
  Recent psych snapshot: {confidence, composure, energy values}

Duration guidance:
  - "short" — a quick indulgence: a bar visit, a haircut, a meal
  - "medium" — an afternoon: a shopping trip, a massage, a concert
  - "long" — a real getaway: a private trip, a retreat, a commission

Pick the duration that matches the character and what they're
indulging in. Be specific. Be slightly cheeky.

No quotation marks in the narration. No preamble. No explanation.
JSON only, in the form: {"narration": "...", "duration": "..."}
```

Expected output examples:

- Napoleon (high ego anchor, currently confident): `{"narration": "Napoleon commissioned an oversized bronze bust of himself to remind everyone he is still winning.", "duration": "long"}`
- Hemingway (low poise, drained composure): `{"narration": "Hemingway closed the bar tab in cash and tipped twice the bill again.", "duration": "short"}`
- Buddha (high poise, calm): `{"narration": "Buddha donated to the temple's silent retreat fund — for himself, mostly.", "duration": "long"}`
- Bezos (high excess, otherwise composed): `{"narration": "Bezos pre-ordered a private flight he won't be on for two years.", "duration": "long"}`

### Call site and tier

- New `CallType.VICE_NARRATION` in `core/llm/tracking.py`
- Routes to the FAST tier (cheap, low-latency, character-tolerant)
- **Synchronous**: the call has to complete before `ai_vice_state`
  can be written, because the duration bucket comes from the
  response. The vice can't go on-the-books without a real
  `ends_at`.
- Uses `json_format=True` to enforce the response shape (matches
  existing pattern at `core/llm/__init__.py` and `controllers.py`).
- ~300ms expected FAST-tier latency. With `VICE_VISIBLE_EVENTS_PER_REFRESH`
  capping start-events per refresh to a small number, total added
  refresh latency is ≤ 1-2 calls' worth (~600ms worst case).

### Per-refresh cost gate

A new constant caps how many vice STARTS can fire per refresh:
`VICE_STARTS_PER_REFRESH = 2`. If more candidates roll positive in
a single refresh, the top 2 by amount fire; the rest re-roll next
refresh. This bounds:

- Latency added to refresh path (LLM calls are synchronous)
- Ticker noise (start events show with full narration)
- LLM cost per active player

Vice-end events have no cap; they're cheap and happen on a real
timer.

### Threshold for narration

Every vice that fires gets the LLM call — there's no "tiny vice
fires silently" path anymore, because the LLM is the source of the
duration. If we want to keep the small-vice-as-noise behavior, we
can do it by raising `MIN_VICE_AMOUNT` (currently 50 chips). Below
that floor, vice doesn't fire at all.

This is simpler than the previous two-threshold model. Either a
vice happens (chips + state + LLM + ticker), or nothing happens.
The `MIN_VICE_AMOUNT = 50` floor catches degenerate amounts; the
amount formula's `BASE_FRACTION = 0.02` means a fire on a $5K
bankroll produces ~$100 minimum (well above the floor).

### Cost estimate

Refresh fires on every `GET /api/cash/lobby` from an active player
(8s poll while the lobby is open — `Lobby.tsx:57`). For a single
player with the lobby open all day: 24×3600/8 ≈ 10.8K refreshes.
With `VICE_STARTS_PER_REFRESH = 2` capping the LLM calls per
refresh, upper bound is 21.6K LLM calls/day per always-on player.
Realistic single-player session-based load is ~500-1000 calls/day.

At FAST-tier pricing, single-digit dollars/day across the user
base. Cheaper than chat suggestions today.

### No caching

The plan does **not** cache narration strings. Reasons:

- The prompt includes current psych state + amount, both of which
  vary per event. Cache key space is too large for meaningful hit
  rate.
- The character flavor is the point — repeating the same line
  defeats it.
- Cost is already bounded by the threshold. Caching layer adds
  complexity without proportional savings.

If playtest reveals the cost is uncomfortable, the simplest
mitigation is raising `VICE_NARRATION_THRESHOLD`, not adding a cache.

### Graceful degradation

LLM call failures (network, rate limit, malformed response, unknown
duration value) fall back to:

- Narration: `"{name} stepped out to spend ${amount:,} on something"`
- Duration: `"medium"` (default bucket)

The vice still fires economically + psychologically. The AI still
goes off-grid for a real duration. Only the character-specific
flavor is lost. The failure is logged but doesn't block the
refresh. The state row is created with the fallback values so the
expiry path works normally.

## Where the mechanic runs

Vice rolls happen inside `cash_mode/lobby.py:refresh_unseated_tables`,
in the post-loop block at `lobby.py:1287-1347`, after
`resolve_ai_carries` and before `return out`. This mirrors the Phase
4.5 carry-resolution structure exactly:

- New module `cash_mode/ai_vice_spending.py` exposes
  `resolve_ai_vice_spending(*, ...) -> ViceSpendingBatch`.
- Lobby calls it once per refresh, then `_emit_vice_spending_events`
  pushes ticker rows for above-threshold results.
- Best-effort `try/except`: a vice failure must not break the
  lobby refresh (same pattern carry resolution uses at line 1344).

### Refresh cadence reality check

`refresh_unseated_tables` runs **lazily on every `GET /api/cash/lobby`
call** (cash_routes.py:3367), not on a background daemon. The React
poller fires every 8s (`Lobby.tsx:57 LOBBY_REFRESH_INTERVAL_MS`),
but only while a player has the lobby visible. Idle AIs do **not**
vice when nobody is watching — vice activity is implicitly gated on
"the player is here to see it." This is a feature, not a bug: it
keeps cost proportional to player attention.

### Candidate set

Build from data the refresh already has loaded (no new query):

```python
seated_ais = _global_seated_set(tables)          # already at lobby.py:416
idle_ais = {entry.personality_id for entry in idle_pool}
candidates = seated_ais | idle_ais
```

Excludes the third pool (`list_eligible_for_cash_mode` returns
visible-but-never-touched personalities) — these have no
`ai_bankroll_state` row yet, so they have no bankroll to spend and
no psychology to recover. Including them would just no-op.

### In-session exclusion

Define "in-session" precisely: a personality whose `personality_id`
sits in a seat on any table where `table.human_seat_index() is not
None`. Those tables are skipped by the per-table loop already
(lobby.py:623), but the candidate set (seated + idle) above includes
seats from human tables too. The vice loop must filter them out:

```python
human_session_ais = {
    seat.personality_id
    for table in tables
    if table.human_seat_index() is not None
    for seat in table.seats
    if seat.kind == 'ai' and seat.personality_id
}
candidates -= human_session_ais
```

Reason: a personality whose chips are visibly committed at the
player's table shouldn't see their bankroll spontaneously move
mid-session. Defer vice until they leave the table (next refresh
once they're back in the idle pool or AI-vs-AI tables).

## Chip flow

Single ledger entry per vice event. Helper signature mirrors
`record_cap_clamp` (`core/economy/ledger.py:288`) — the closest
precedent shape (single-personality destruction):

```python
def record_vice_spending(
    repo: Optional[ChipLedgerRepository],
    *,
    personality_id: str,
    amount: int,
    context: Optional[Dict[str, Any]] = None,
    sandbox_id: Optional[str] = None,
) -> Optional[int]:
    """ai → central_bank for a vice spend.

    Fired by `resolve_ai_vice_spending` when an AI's excess + drift
    crosses the probability threshold. No-op when amount <= 0.
    """
    if repo is None or amount <= 0:
        return None
    return record(
        repo,
        source=ai(personality_id),
        sink=bank(),
        amount=amount,
        reason='vice_spending',
        context=context,
        sandbox_id=sandbox_id,
    )
```

Typical call-site context dict:

```python
context = {
    "site": "lobby_refresh_vice",
    "excess_ratio": round(excess_ratio, 3),
    "drift_magnitude": round(drift_magnitude, 3),
    "vice_prob": round(prob, 3),  # for post-hoc tuning
}
# NB: sandbox_id is a separate top-level kwarg, NOT inside context
# (matches every existing record_* helper).
```

`vice_spending` is a new ledger reason. Add to `LEDGER_REASONS`
frozenset in `core/economy/ledger.py:34-54` — that's the entire
registration surface (no enum, no dispatcher).

The audit's `actual_outstanding` invariant
(`flask_app/services/chip_ledger_audit.py:128`) holds:
`ai_bankrolls_stored` shrinks by `amount`, `ledger_outstanding`
shrinks by the same amount via the new destruction row. Drift stays
zero. Mirror the test pattern at `tests/test_chip_ledger_audit.py:113`.

### Applying the chip move

The vice loop does its own write rather than going through
`credit_ai_cash_out` (which is the cash-out-from-seat path and
expects a seat amount). The shape:

```python
stored = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
knobs = bankroll_repo.load_personality_knobs(pid)
projected = project_bankroll(stored, knobs.starting_bankroll,
                             knobs.bankroll_rate, now)

# Fire the regen ledger row if projected > stored (same pattern as
# try_ai_voluntary_payoff at ai_carry_resolution.py:354).
if projected > stored.chips:
    chip_ledger.record_ai_regen(...)

new_chips = max(0, projected - amount)
bankroll_repo.save_ai_bankroll(
    AIBankrollState(personality_id=pid, chips=new_chips,
                    last_regen_tick=now),
    sandbox_id=sandbox_id,
)
chip_ledger.record_vice_spending(
    chip_ledger_repo, personality_id=pid, amount=amount,
    context=context, sandbox_id=sandbox_id,
)
```

The `starting_bankroll` used by `excess_ratio` comes from the same
`knobs.starting_bankroll` value — `BankrollKnobs` lives in
`personalities.config_json.bankroll_knobs` (default 10,000, alias
`bankroll_cap` legacy-accepted at `bankroll_repository.py:417`).

## Implementation commits

Four commits, ordered by dependency. Commits 1-2 deliver the
mechanic with degraded flavor (no LLM); 3 adds the LLM; 4 wires the
frontend surfaces.

**Commit 1: State schema + economic event + eligibility filter**
- New migration in `poker/repositories/schema_manager.py` for
  `ai_vice_state` table (PK `(personality_id, sandbox_id)`, index
  on `(sandbox_id, ends_at)`).
- New repo `poker/repositories/vice_state_repository.py`:
  - `insert_vice_state(personality_id, sandbox_id, started_at, ends_at, amount, duration_bucket, narration)`
  - `list_expired(sandbox_id, now) -> list[ViceState]`
  - `list_active(sandbox_id, now) -> list[ViceState]`
  - `delete(personality_id, sandbox_id)`
  - `is_on_vice(personality_id, sandbox_id, now) -> bool`
- New module `cash_mode/ai_vice_spending.py` (mirrors
  `cash_mode/ai_carry_resolution.py` structure):
  - Pure formulas:
    - `compute_excess_ratio(bankroll, starting_bankroll) -> float`
    - `compute_pressure(axes: EmotionalAxes) -> float` — returns `1 − min(conf, comp, energy)`
    - `compute_vice_probability(excess_ratio, pressure) -> float`
    - `compute_vice_amount(bankroll, excess_ratio, rng) -> int`
    - `duration_for_bucket(bucket, rng) -> timedelta`
    - `compute_recovered_axes(axes, anchors, recovery_factor) -> EmotionalAxes`
  - Dataclasses: `ViceStartResult`, `ViceEndResult`, `ViceSpendingBatch`
  - `tick_vice_expirations(*, vice_repo, bankroll_repo, sandbox_id, psych_lookup, psych_writer, now) -> list[ViceEndResult]`
  - `resolve_ai_vice_spending(*, candidates, vice_repo, bankroll_repo, chip_ledger_repo, sandbox_id, narrate_fn, rng, now) -> list[ViceStartResult]`
    - `narrate_fn` returns `(narration, duration_bucket)`. In this
      commit, `narrate_fn` is a stub that always returns the
      templated narration + `"medium"`. Commit 3 plugs in the real LLM.
- New ledger reason `vice_spending` + helper `record_vice_spending` in `core/economy/ledger.py`.
- Add `is_on_vice` filter at every existing eligibility gate:
  - `cash_mode/movement.py` roster refresh (idle pool selection)
  - `cash_mode/lobby.py` candidate set construction
  - `cash_mode/stakes.py` staking-eligibility checks (borrower + staker)
- Lobby refresh wiring:
  - Add `tick_vice_expirations` near the start of `refresh_unseated_tables` (before the table loop), inside the same try/except shape.
  - Add `resolve_ai_vice_spending` after `_emit_carry_resolution_events`.
  - Build `candidates = idle_pool_pids - active_vice_pids`.
- Tests:
  - Probability formula (broke=0, flush=MAX_PROB)
  - Amount formula (caps, floor protection)
  - Vice state insert / list / delete round-trip
  - Eligibility filter blocks vicing AI at every gate
  - Expiry: psych recovery applies once and is bounded
  - Ledger entry: source/sink correct, audit invariant holds
  - Candidate set: idle-only, vicing AIs excluded

**Commit 2: Ticker events (templated)**
- New `EVENT_VICE_START` and `EVENT_VICE_END` in `cash_mode/activity.py`.
- Formatters `format_vice_start_message` (narration-based) and `format_vice_end_message` ("{name} is back").
- New `VICE_STARTS_PER_REFRESH = 2` constant.
- `_emit_vice_spending_events(starts, ends, personality_repo, ...)` helper in `lobby.py`, mirroring `_emit_carry_resolution_events`.
- Best-effort emission wrapped in `try/except` per the activity-emit pattern.
- Tests:
  - Vice start emits ticker row with narration
  - Vice end emits ticker row
  - `VICE_STARTS_PER_REFRESH` cap respected (extra starts skipped this refresh)
  - Sandbox isolation: events emitted for sandbox A don't surface in sandbox B

**Commit 3: LLM narration with duration**
- New `CallType.VICE_NARRATION` in `core/llm/tracking.py`.
- New `cash_mode/vice_narration.py`:
  - `narrate_vice(personality, psychology_snapshot, bankroll, amount) -> tuple[str, str]`
    - Returns `(narration, duration_bucket)` on success.
    - Falls back to templated narration + `"medium"` on any
      failure (network, parse, unknown bucket value).
  - Prompts the FAST tier with `json_format=True`.
- Plug `narrate_vice` into `resolve_ai_vice_spending` (replaces the
  Commit 1 stub).
- Tests:
  - Mock LLMClient returns expected `{narration, duration}`; vice state row gets correct `ends_at`
  - Unknown bucket value falls back to `"medium"`
  - Network failure → fallback path; vice still fires; state row written
  - `VICE_STARTS_PER_REFRESH` bounds LLM call count per refresh

**Commit 4: Frontend surfaces**
- Extend `LobbyEvent.type` union in `react/react/src/components/cash/types.ts:177` with `'vice_start'` and `'vice_end'`.
- `ActivityTicker.tsx:74` icon dispatch:
  - `vice_start` → suggest `Sparkles` icon
  - `vice_end` → suggest `DoorOpen` or default dot
- Lobby personality cards: separate "Away" group (or grayed-out
  badge on the existing card) showing narration + ETA. Source of
  truth: a new field on `LobbyResponse` exposing
  `active_vices: VictiveEntry[]` per sandbox (`vice_state_repository.list_active`).
- Dossier integration: surface active vice on the personality
  dossier route.
- Sit-down + stake-create routes: refuse with a clear message when
  the target personality is on vice.
- Tests:
  - Type-check passes
  - Lobby API exposes active-vice data when present
  - Sit-down route refuses with appropriate status
  - Visual: personality card shows away state (manual verification)

A reasonable stopping point is after commit 3 — the full mechanic is
live and visible on the ticker, just without the dedicated frontend
treatment for "away" state. Commit 4 polishes the UI.

## Locked decisions

1. **Vice has a wealth-concentration gate and a pressure modifier.**
   The gate is cast-relative: an AI must hold ≥ `CONCENTRATION_FLOOR
   × cast_median` chips to be eligible. This replaced the prior
   per-personality `starting_bankroll × 1.2` gate, which let
   low-baseline characters (e.g., Ace @ $24K) qualify while being
   mid-pack by cast standards. Pressure
   (`1 − min(confidence, composure, energy)`) amplifies the
   probability when present, capped via `PRESSURE_BOOST`. Wealth
   concentration dominates; pressure shapes the cadence. Psych
   recovery is a side benefit applied at vice end, not part of the
   firing condition, but the formula's pressure factor means tilted
   AIs vice more often and therefore recover more often. When the
   cast median itself is below `MIN_CAST_MEDIAN_FOR_VICE`, the
   entire pass short-circuits (no top to drain in a uniformly poor
   cast).

2. **Vice is a state, not an atomic event.** AIs on a vice are
   physically unavailable: not seatable, not stakeable in either
   direction, not visible in the active personality list.

3. **Vice duration comes from the LLM.** The narration response
   includes a `duration` bucket (short/medium/long) that maps to
   wall-time ranges (15-30min / 30-90min / 90-240min). The
   character expresses their own typical timescale through the
   choice.

4. **LLM narration is synchronous, not async-replace.** The
   duration value must be known before the state row is written,
   so we can't fire-and-forget. The `VICE_STARTS_PER_REFRESH = 2`
   cap keeps the per-refresh LLM cost bounded.

5. **No authored vice configs.** No `vice` block in personality
   JSON. No per-personality vice categories. The LLM infers
   everything from character + current state. Configuration
   burden = zero.

6. **Vice duration IS the cooldown.** No additional post-vice
   buffer. When `ends_at` passes, the AI is immediately back in
   the eligibility pool. The vice itself is the unavailability
   window.

7. **Vice recovery hits all three dynamic psych axes
   (confidence, composure, energy).** Not a per-axis targeting
   system. The act of indulgence soothes the character globally.
   Static identity anchors and relationship axes are NOT touched
   by vice. Recovery applies at vice END.

8. **Money can only buy so much happiness.** The per-event psych
   recovery scales logarithmically with amount spent, ceilinged at
   `MAX_RECOVERY = 0.40`. The wealthy don't heal faster per event —
   they vice more often.

9. **Personalities seated at human tables are exempt** (deferred
   until they leave), and sim-seated AIs are exempt (deferred
   until they naturally rotate to idle). Only idle-pool AIs are
   eligible candidates.

10. **LLM narration is fail-soft.** Any failure falls back to
    templated text + medium duration. The vice still fires;
    state row, chip move, and expiry path all work normally.

11. **No caching of narration strings.** Each vice gets a fresh
    LLM call. Cost is bounded by `VICE_STARTS_PER_REFRESH`, not
    by a cache.

12. **Single starts-per-refresh cap.** `VICE_STARTS_PER_REFRESH = 2`
    bounds both LLM latency added to the refresh path AND ticker
    noise. Ends have no cap (they're cheap, timer-driven).

13. **Mirrors the Phase 4.5 carry-resolution structure.** New module
    `cash_mode/ai_vice_spending.py` with `resolve_ai_vice_spending`
    + `tick_vice_expirations`, called from the same lobby refresh
    path. New `vice_state_repository.py` parallels `stake_repository`.

## Open questions for playtest

These are real unknowns that won't resolve until the mechanic runs
against actual cast economics:

1. **Is `MAX_PROB = 0.25` calibrated right?** A flush AI rolls a
   vice every ~4 refreshes at the cap (~32s of attention). With
   medium-bucket duration (~1hr median), they're absent more than
   they're present. Could feel like rich AIs are "always gone" or
   could feel like a real consequence of being rich. Adjust against
   playtest feel.

2. **Do the duration buckets feel right?** 15-30 / 30-90 / 90-240
   min ranges are a guess. Long-bucket vices in particular (up to
   4hr) may feel like the AI is "gone" rather than just "out." If
   casual players don't keep the lobby open that long, they may
   never see the return event for long vices. Could shrink long
   to 90-150 min.

3. **Does the recovery curve neutralize tilt too quickly?** A flush
   AI vices often; their composure rarely strays from baseline
   because every vice-end resets them. Mitigation if needed: lower
   `BASE_RECOVERY` from 0.25 to 0.15.

4. **Is the LLM call cost-acceptable at sync timing?**
   `VICE_STARTS_PER_REFRESH = 2` × ~300ms ≈ 600ms added to refresh
   in the worst case. If refresh latency pinches, options: (a) cap
   to 1, (b) batch the LLM call (one prompt covering N AIs),
   (c) move to async-replace and accept the deferred-duration
   complexity (state row would write with a default duration that
   the LLM response can update).

5. **Where exactly should vice surface in the lobby UI?** Options:
   "Away" sidebar list, grayed-out personality card with a return
   ETA, or just relying on the ticker. Depends on whether the
   player's attention is "who's playing right now" or "what's
   happening in the world."

6. **What about broke-and-tilted AIs?** They never vice under this
   design — broke + tilted is not a trigger. Their recovery happens
   via the normal per-hand `recover()` path. Is that OK? Or do we
   want a non-economic tilt-relief surface too? (Could be a future
   feature: "Hemingway stepped out for a walk" — no chip cost,
   short duration, mild recovery. Out of scope here.)

7. **Sim-seated AIs are exempt — does that distort the cast economy?**
   A perpetually sim-busy AI never enters the idle pool, never
   vices, accrues chips. Solution if it shows up: more aggressive
   seat churn in `refresh_table_roster`, or a periodic "step away
   from the sim table" rotation rule.

8. **Is `CONCENTRATION_FLOOR = 2.5` the right multiple?** A 2.5×
   floor gives ~10% of the cast vice eligibility in a typical
   sandbox (top decile). Lower (e.g., 1.5) gives a broader vice
   population — more frequent events, more diffuse drain. Higher
   (e.g., 3.0–4.0) restricts vice to the truly dominant. Adjust
   from playtest by watching: how often does vice fire per refresh,
   and does it correctly track "the AIs the player perceives as
   running away with the economy"?

9. **Pressure: min vs drift?** Current design uses
   `1 − min(conf, comp, energy)`, which gives every character a
   baseline pressure floor (~0.4 when nothing is wrong, because
   axes typically sit around 0.5-0.7). Alternative:
   `max(baseline_axis − current_axis)` over the three axes, which
   puts a perfectly-anchored AI at pressure = 0 cleanly. The min
   has the nice property that inherently-stressed characters
   (e.g. low energy baseline) carry a slight chronic
   vice-proneness — character expression baked into the math.
   Choose based on playtest feel.

9. **Does the pressure→recovery feedback loop create equilibria?**
   Tilted AI vices more → recovers more → vices less. Stable. But
   could it produce a "vice every time tilt approaches" treadmill
   that makes tilt mechanically invisible? Watch for AIs never
   sustaining tilt past their next vice. If observed, the
   mitigation is the same as Q3 (lower `BASE_RECOVERY`).

## Why this is interesting beyond chip-sink

This is the first AI behavior in the game that uses the LLM **for
descriptive generation rather than decision-making**. Existing LLM
calls (player decisions, commentary, chat suggestions) generate
mechanically-impactful output. Vice narration generates pure flavor
that doesn't affect game state.

If the pattern works — small, cheap, fail-soft, context-rich LLM
calls that just generate believable character text — it becomes a
template for other "make the world feel alive" surfaces:

- AI memorable-hand descriptions on the dossier
- Personality "weekly recap" summaries
- Pre-session AI mood snippets ("Napoleon arrived in a sour mood
  today")
- Custom personality flavor text on first sit-down

Vice spending is a proving ground for that pattern. The economy
effect justifies the cost regardless of whether the narrative side
works; the narrative side is upside.

## Files this plan touches

| File | Change |
|---|---|
| `poker/repositories/schema_manager.py` | New migration: `ai_vice_state` table + index |
| `poker/repositories/vice_state_repository.py` (new) | CRUD + `is_on_vice`, `list_active`, `list_expired` |
| `cash_mode/ai_vice_spending.py` (new, Commit 1) | Pure formulas, dataclasses, `tick_vice_expirations`, `resolve_ai_vice_spending` |
| `cash_mode/lobby.py` (around lines 1287-1348) | Wire `tick_vice_expirations` near refresh start, `resolve_ai_vice_spending` post-loop; build idle-only candidate set; helper closures (`psych_lookup`, `psych_writer`) |
| `core/economy/ledger.py` (lines 34-54, ~288 area) | Add `vice_spending` to `LEDGER_REASONS`; add `record_vice_spending` helper |
| `cash_mode/movement.py` | Add `is_on_vice` filter to idle-pool selection / roster refresh |
| `cash_mode/stakes.py` | Add `is_on_vice` filter to staking eligibility checks (borrower + staker) |
| `cash_mode/activity.py` (Commit 2) | New `EVENT_VICE_START` / `EVENT_VICE_END`; formatters; `VICE_STARTS_PER_REFRESH` |
| `core/llm/tracking.py` (Commit 3) | New `CallType.VICE_NARRATION` |
| `cash_mode/vice_narration.py` (new, Commit 3) | LLM call returning `(narration, duration_bucket)` + fallback |
| `flask_app/routes/cash_routes.py` (Commit 4) | Surface `active_vices` in lobby response; refuse sit-down + stake-create on vicing AIs |
| `react/react/src/components/cash/types.ts` (line 177) | Extend `LobbyEvent.type` union with `'vice_start'` and `'vice_end'`; new `ActiveVice` type |
| `react/react/src/components/cash/ActivityTicker.tsx` (line 74) | Icon dispatch for `vice_start` (suggest `Sparkles`) and `vice_end` |
| `react/react/src/components/cash/Lobby.tsx` | Render "Away" group / personality card vice state |
| `tests/cash_mode/test_ai_vice_spending.py` (new) | Probability, amount, expiry, ledger, audit-invariant, candidate-set tests |
| `tests/cash_mode/test_vice_state_repository.py` (new) | Repository CRUD + isolation tests |
| `tests/cash_mode/test_vice_narration.py` (new, Commit 3) | Mocked LLMClient, fallback, duration parsing |

One schema migration (`ai_vice_state` table). The `emotional_state_json`
blob continues to handle psych persistence (no change).

## Spec status

This plan is parked alongside
[`CASH_MODE_BACKING_SYSTEM_HANDOFF.md`](./CASH_MODE_BACKING_SYSTEM_HANDOFF.md)
as a sibling chip-economy concern. Suggested ship order:

1. Finish the backing system through Phase 4.5 (carry resolution)
2. Phase 5 (humans as stakers) — currently in progress
3. **Vice spending Commit 1+2** — mechanic shipped without LLM,
   tunable in playtest
4. **Vice spending Commit 3** — LLM narration on top once balance
   feels right

Vice spending doesn't depend on any backing-system phase strictly
— it could land any time. But it's economically meaningful enough
that you'd want to land it when the backing system's chip flow is
stable, so you can attribute economic shifts to the right cause.
