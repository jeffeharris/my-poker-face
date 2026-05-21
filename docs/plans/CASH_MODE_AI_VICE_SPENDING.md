---
purpose: Design and implementation plan for AI vice spending — a chip-sink that doubles as a psychology-regulation mechanic, with LLM-generated flavor narration driven by character + current psych state rather than authored vice configs.
type: design
created: 2026-05-21
last_updated: 2026-05-21
---

# Cash Mode — AI Vice Spending

> **Why this exists:** the AI economy has multiple chip faucets
> (passive regen, AI seed, house stakes, player seeds) and only
> mechanical sinks (cap_clamp, optional table_rake). Without a sink
> that scales with wealth and feels narrative rather than punitive,
> AI bankrolls drift monotonically upward and the cast loses
> economic differentiation over time. Vice spending fills the gap.

## The mechanic in one sentence

**When an AI is flush AND psychologically drifted, they
probabilistically blow some chips on something character-appropriate,
and the act of spending pulls their drifted traits partway back to
anchor.**

The chip burn balances the economy. The psychology snap-back makes
vice feel mechanically useful, not just punitive. The character-
appropriate flavor comes from an LLM call at fire time — no
authored vice fields anywhere in the personality config.

## Design tensions this resolves

The hard `cap_clamp` (chips overflow → destroyed at credit time)
solves the runaway-bankroll problem but feels arbitrary. It fires
silently in the audit ledger; the player never sees it. It treats
every overflowing AI the same way regardless of who they are.

Vice spending solves the same economic problem **with character
attached**. Same aggregate sink magnitude (tunable), but each event
is:

- Triggered by THIS AI's specific bankroll + psych state
- Sized by THIS AI's overflow magnitude
- Narrated by THIS AI's voice via LLM
- Mechanically connected to THIS AI's psychology

The dossier section "they spent $X this week" becomes a real
character signal. The lobby ticker carries flavor moments instead
of silent ledger events.

It's a **soft cap** that bites the rich, ignores the poor, and
expresses the personality.

## Trigger formula

Vice fires when an AI has both **means** (excess bankroll) and
**motivation** (psychological pressure from being out of sync with
their anchors). Either factor alone produces modest vice probability;
having both reinforces each other multiplicatively — a tilted AI
with money to burn vices more than the sum of "tilted but broke"
plus "flush but composed."

**The two factors:**

1. **Means — bankroll excess** above their comfort floor
   - `excess_ratio = max(0, (bankroll - starting_bankroll × COMFORT_FLOOR) / starting_bankroll)`
   - `COMFORT_FLOOR` suggested 1.2 — vice ignores everything up to 20% above the personality's seed amount
   - A flush 5×-starting Bezos has `excess_ratio = 3.8`; a broke Napoleon at 0.5× starting has `excess_ratio = 0`

2. **Motivation — pressure from anchor drift**
   - For each anchor key in `(baseline_aggression, baseline_looseness, ego, poise, expressiveness, risk_identity, baseline_energy)`:
     `drift_axis = |current - anchor|`
   - `pressure = sum(drift_axis) / len(drift_axis)` — normalized to [0, 1]
   - A perfectly-at-anchor AI has pressure = 0; a heavily tilted AI has pressure = 0.4+
   - Variable name in code: `drift_magnitude` (mechanical); conceptual term: pressure

**Probability composition (additive baseline + multiplicative cross-term):**

```
vice_prob = clamp(
    MIN_PROB,
    MAX_PROB,
    BASE_PROB
        + excess_ratio × EXCESS_WEIGHT
        + drift_magnitude × DRIFT_WEIGHT
        + excess_ratio × drift_magnitude × REINFORCEMENT_WEIGHT,
)
```

The cross-term is the key design lever. Without it, the formula is
linear: a rich-and-calm AI vices roughly as often as a tilted-and-
broke AI. With it, the combo case (rich AND tilted) bumps
disproportionately — the formula captures "money + reason to burn
it = where vice really lives."

Suggested starting values (tunable from playtest):
- `MIN_PROB = 0.0` — broke + perfectly-anchored AI never vices
- `MAX_PROB = 0.4` — flush + heavily-pressured AI vices ~40% per tick
- `BASE_PROB = 0.0`
- `EXCESS_WEIGHT = 0.03` (per unit of excess_ratio — lighter than before since cross-term picks up slack)
- `DRIFT_WEIGHT = 0.25` (per unit of pressure — also lighter)
- `REINFORCEMENT_WEIGHT = 0.40` (the cross-term that captures "both factors present")

Worked examples:

| AI | excess | pressure | linear part | cross-term | total |
|---|---|---|---|---|---|
| Bezos (flush, calm) | 3.8 | 0.05 | 0.114 + 0.012 | 0.076 | **0.20** |
| Hemingway (broke, tilted) | 0.1 | 0.4 | 0.003 + 0.100 | 0.016 | **0.12** |
| Napoleon (flush, tilted) | 2.5 | 0.4 | 0.075 + 0.100 | 0.400 → clamped | **0.40** (max) |
| Buddha (flush, composed) | 1.5 | 0.05 | 0.045 + 0.012 | 0.030 | **0.09** |
| Average AI | 0.5 | 0.15 | 0.015 + 0.037 | 0.030 | **0.08** |

The combo case (Napoleon) hits the cap easily because both factors
present in real magnitudes. The single-factor cases (Bezos with
only excess, Hemingway with only pressure) probability roughly half
the combo case. The cross-term is doing the work of saying "vice
is where money and pressure meet."

This shape encourages the **narrative arc** the design is reaching
for: AIs vice most when they're flush AND under psychological
pressure. The wealthy AI on tilt is the protagonist of every vice
event. The broke-tilted AI vices occasionally (Hemingway self-
medicates even with no money — captured by the linear pressure
term). The flush-composed AI rarely vices (the linear excess term
catches some, but they don't NEED catharsis).

## Amount formula

The vice cost scales with both factors but has hard floors and caps:

```
raw_amount = bankroll × (
    BASE_FRACTION
    + excess_ratio × EXCESS_FRACTION_WEIGHT
    + drift_magnitude × DRIFT_FRACTION_WEIGHT
) × random.uniform(0.5, 1.5)
```

Suggested:
- `BASE_FRACTION = 0.01` (1% baseline)
- `EXCESS_FRACTION_WEIGHT = 0.02`
- `DRIFT_FRACTION_WEIGHT = 0.05`
- Random multiplier on top spreads the per-event amount so vice
  events aren't visually uniform.

**Constraints applied after raw calculation:**

1. **Minimum:** `MIN_VICE_AMOUNT = 50` — if raw_amount < 50 chips,
   skip the event entirely (no chip move, no ticker, no LLM).
   Sub-50 vice events are noise.
2. **Maximum per event:** `min(raw_amount, bankroll × MAX_VICE_FRACTION)`
   where `MAX_VICE_FRACTION = 0.15` — never blow more than 15% in
   one event. Keeps even a fully tilted Hemingway from drinking his
   whole roll on one bad night.
3. **Floor protection:** post-vice bankroll must stay above
   `starting_bankroll × 0.5`. Vice never takes an AI from above-half
   to below-half in a single event. The cast doesn't bankrupt itself
   through indulgence.

Result: vice events range from ~$100 chunks (Hemingway tilted on
his way back down) to ~$5,000 chunks (Bezos celebrating a major
win) depending on the combo of excess + drift.

## Psychology regulation effect

When vice fires successfully (i.e., not skipped), the targeted
psychology pull-back runs immediately. The recovery factor scales
**logarithmically** with the amount spent so rich vices are only
*slightly* more beneficial than modest ones — money can only buy so
much happiness:

```
recovery_factor = BASE_RECOVERY + AMOUNT_BONUS × log10(amount / MIN_VICE_AMOUNT)
recovery_factor = min(recovery_factor, MAX_RECOVERY)

for each anchor_key in tracked anchors:
    delta = anchor - current
    current = current + delta × recovery_factor
```

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

The recovery hits **all drifted traits**, not just one. The act of
indulgence is general catharsis — tilted Hemingway who buys a
fishing boat is comforted across composure, ego, AND energy, not
just one axis. This avoids needing per-vice "psych_target" mappings
(consistent with the doc's "no authored vice config" principle).

**Stratification is real but soft.** A wealthy tilted AI vices more
often (higher probability) → heals through frequency. A poor tilted
AI vices rarely → heals through patience (energy + regen handle the
recovery the vice isn't covering). The advantage compounds slowly,
not steeply. Combined with the staking system, the cast still gets
"fortunes diverge over time" arcs — but no one is permanently locked
into tilt because they can't afford a yacht. The therapeutic ceiling
is the same; only the frequency differs.

## LLM narration

The flavor lives entirely at fire time via an LLM call. No authored
vice categories, no per-personality vice mappings, no template
strings.

### Prompt shape

```
You are writing one sentence for the cash-mode lobby ticker about
a fictional poker AI character indulging in a personal vice.

Character:
  Name: {personality.name}
  Style: {personality.play_style}
  Attitude: {personality.attitude}
  Anchors: {curated anchor block — aggression, ego, poise, ...}
  Verbal tics: {personality.verbal_tics}

Current state:
  Bankroll: ${bankroll}
  Excess over comfort floor: ${excess_amount}
  Off-anchor traits: {list of (trait, current, anchor) for drifted axes}
  Just spent: ${amount}

Generate ONE sentence describing what they spent the money on, in
character. Be specific. Be slightly cheeky. The sentence should
make sense given which traits are off-anchor (e.g., a tilted
character self-medicates; an inflated-ego character flaunts).

No quotation marks. No preamble. No explanation. Just the sentence.
```

Expected output examples:

- *Napoleon (tilted, drift on poise + composure): "Napoleon
  commissioned an oversized bronze bust of himself to remind everyone
  he is still winning."*
- *Hemingway (low energy + low composure, just lost): "Hemingway
  closed the bar tab in cash and tipped twice the bill again."*
- *Buddha (high poise drift after a frustrating session): "Buddha
  donated to the temple's silent retreat fund — for himself, mostly."*
- *Bezos (high excess, otherwise composed): "Bezos pre-ordered a
  private flight he won't be on for two years."*

### Call site and tier

- New `CallType.VICE_NARRATION` in `core/llm/tracking.py`
- Routes to the FAST tier (cheap, low-latency, character-tolerant)
- Synchronous from the lobby refresh path so the ticker event surfaces
  on the same tick as the chip move
- ~300ms expected latency on Fast tier — acceptable inside the lobby
  refresh budget

### Threshold for narration

Not every vice event gets an LLM call. Match the lobby's existing
ticker-event threshold pattern (`AI_STAKE_TICKER_THRESHOLD` = 2000
chips in Phase 4 Commit 5):

- **`amount >= VICE_NARRATION_THRESHOLD`** (suggested 500 chips):
  full path. LLM call, ticker event, narrated.
- **`amount < threshold`**: silent. Chip moves, psych regulates, no
  ticker, no LLM call.

This bounds LLM cost. A typical refresh might fire 5-10 vice events;
maybe 1-2 cross the narration threshold. At ~13K lobby refreshes
per day (assuming continuous play), that's ~5K LLM calls/day. At
fast-tier pricing, single-digit dollars per day. Order of magnitude
cheaper than chat features.

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

LLM call failures (network, rate limit, malformed response) fall
back to a templated string:

> `"{name} burned ${amount:,} on something character-appropriate"`

The vice still fires economically + psychologically. Only the
ticker flavor is lost. Logs the failure for monitoring but doesn't
block.

## Where the mechanic runs

Vice rolls happen inside `cash_mode/lobby.py:refresh_unseated_tables`,
similar to how Phase 4.5 carry-resolution behaviors are designed.

For each AI in the lobby's union of:
- Currently seated AIs (across all unseated tables)
- Idle pool AIs
- Eligible-never-seated AIs whose `stake_comfort_zone` was active
  in this lobby's recent history (gates the cost — never vice
  someone who'd never play at this lobby's stakes)

...roll the vice probability. If fires, compute amount, apply chip
flow, apply psych regulation, optionally LLM-narrate, record event.

In-session AIs (at the player's active cash table) are **excluded**.
Vice firing mid-hand would be confusing — chips don't disappear from
seats; the AI's bankroll moves while they're playing. Defer until
they leave the table.

## Chip flow

Single ledger entry per vice event:

```
chip_ledger.record_vice_spending(
    repo,
    personality_id=pid,
    amount=amount,
    context={
        "site": "lobby_refresh_vice",
        "excess_ratio": excess_ratio,
        "drift_magnitude": drift_magnitude,
        "sandbox_id": sandbox_id,
    },
    sandbox_id=sandbox_id,
)
```

`vice_spending` is a new ledger reason (sink). Add to
`LEDGER_REASONS` frozenset in `core/economy/ledger.py`. Reuses the
same audit dispatch pattern as `cap_clamp`, `table_rake`,
`forgive_balance` — destination is central_bank (sink), source is
the AI's bankroll.

The audit's `actual_outstanding` invariant holds: chips leave the
ai_bankroll pool, balanced by a ledger entry against central_bank.
Same shape as `cap_clamp`.

## Implementation commits

Three commits, ordered by dependency:

**Commit 1: Mechanic + psych regulation, no LLM**
- New `cash_mode/vice.py`:
  - `compute_vice_probability(bankroll, starting_bankroll, anchors, psychology)` — pure formula
  - `compute_vice_amount(bankroll, starting_bankroll, anchors, psychology, rng)` — pure formula, returns 0 when below threshold
  - `apply_psychology_recovery(psychology, anchors, recovery_factor=0.3)` — pure pull-toward-anchor
- New ledger reason `vice_spending`; `chip_ledger.record_vice_spending` helper
- Vice fire loop in `refresh_unseated_tables` (per AI in the union pool above)
- No LLM call yet — vice events are silent on the ticker for this commit. Templated log only.
- Tests:
  - Probability formula: broke + anchored AI → 0; flush + drifted → high
  - Amount caps: minimum skip; maximum 15%; floor protection
  - Psych recovery: traits move toward anchor by factor; no over-shoot
  - Ledger entry fires with correct amount + reason

**Commit 2: Lobby ticker integration**
- New `EVENT_VICE_SPENDING` constant in `cash_mode/activity.py`
- `format_vice_message(name, amount)` templated fallback formatter
- Threshold gate (`VICE_NARRATION_THRESHOLD = 500`): below threshold
  remains silent; above threshold emits ticker event using the
  templated message
- Frontend `LobbyEvent.type` union extended; `ActivityTicker` icon
  selection (suggest the `Sparkles` or `Flame` lucide icon for vice)
- Tests:
  - Above-threshold vice emits ticker event
  - Below-threshold vice does not emit
  - Event uses correct `sandbox_id` (per-sandbox filtering)

**Commit 3: LLM narration**
- New `CallType.VICE_NARRATION` in `core/llm/tracking.py`
- `cash_mode/vice_narration.py:narrate_vice(personality, psychology_state, bankroll, amount) -> str`
  - Prompts the FAST tier
  - Returns the narrated sentence on success
  - Falls back to templated string on any failure (network, parse,
    rate limit, etc.)
- Replace the templated message in the ticker emit with the narrated
  version when amount >= threshold
- Tests:
  - Mock LLMClient: returns expected narrated string
  - Failure mode: returns templated fallback, event still fires
  - Threshold respected: no LLM call below threshold

A reasonable stopping point is after commit 2 — the mechanic + psych
effect + visible ticker exists, just with bland flavor text. Commit
3 adds the character-driven flavor on top. Independent rollout if the
LLM cost feels uncomfortable.

## Locked decisions

1. **No authored vice configs.** No `vice` block in personality
   JSON. No per-personality vice categories or psych-target
   mappings. The LLM infers everything from character + current
   state. Configuration burden = zero.

2. **Vice recovery hits ALL drifted traits.** Not a per-axis
   targeting system. Spending is general catharsis; the act of
   indulgence soothes the character globally.

3. **Money can only buy so much happiness.** The per-event psych
   recovery scales logarithmically with amount spent, ceilinged at
   `MAX_RECOVERY = 0.40`. A $50 vice and a $5,000 vice differ by
   only ~10pp in therapeutic effect (0.25 → 0.35). The wealthy heal
   faster only because they vice more **often**, not because each
   indulgence is more powerful. Stratification stays soft — no AI
   is permanently locked in tilt for lack of yacht money.

4. **In-session AIs are exempt.** Vice only fires for idle / between-
   session AIs. Mid-hand vice would be visually confusing.

5. **LLM narration is fail-soft.** Any failure falls back to
   templated text. The chip + psych effect always fires.

6. **No caching of narration strings.** Each above-threshold event
   gets a fresh LLM call. Cost is bounded by the threshold, not by
   a cache.

7. **Threshold for narration (500 chips) ≠ threshold for vice
   firing (50 chips minimum amount).** Small vice events happen
   silently; only big ones get narrated. Decouples economic effect
   from narrative volume.

## Open questions for playtest

These are real unknowns that won't resolve until the mechanic runs
against actual cast economics:

1. **Is `MAX_PROB = 0.4` too high?** Heavily drifted flush AIs would
   vice every ~3 ticks. That might feel relentless. Could be 0.2
   in v1 and bumped up if vice feels too rare.

2. **Does the lobby ticker get drowned in vice events?** With 30
   AIs and 0.1 average vice probability per tick, that's 3 vice
   events per refresh on average. Combined with stake events + sim
   events, the ticker could feel busy. May need an upper cap on
   vice events per refresh (e.g., max 1-2 ticker-visible vices per
   refresh, others silent).

3. **Does `VICE_RECOVERY_FACTOR = 0.3` make tilt too easy to escape?**
   The Phase 4.5 forgiveness path also pulls relationship axes;
   stacking too much recovery could neutralize the psychology
   system's tilt mechanics. Watch for "AIs never stay tilted long
   enough to matter."

4. **Should vice fire for ALL eligible AIs each tick, or sample N?**
   Walking every AI's vice roll each tick scales linearly with cast
   size. At 50+ personalities + 8s refresh cadence that's ~50 rolls
   per tick, which is fine. At 200+ personalities it gets noisy.
   The eligibility filter (stake_comfort_zone is "active") should
   bound this for now.

5. **Is LLM narration latency acceptable inside the lobby refresh
   path?** ~300ms per call × maybe 2 narrated vices per refresh
   = 600ms added latency. Refresh budget is currently ~500ms. May
   need to make narration async (event fires immediately with
   templated text; narration replaces it on next refresh when ready).

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
| `cash_mode/vice.py` (new) | Pure formulas + chip-flow application |
| `cash_mode/vice_narration.py` (new, Commit 3) | LLM call + fallback |
| `cash_mode/activity.py` | New `EVENT_VICE_SPENDING` constant + formatter |
| `cash_mode/lobby.py` | Vice fire loop inside `refresh_unseated_tables` |
| `core/economy/ledger.py` | New `vice_spending` reason + `record_vice_spending` helper |
| `core/llm/tracking.py` | New `CallType.VICE_NARRATION` (Commit 3) |
| `react/.../cash/types.ts` | Extend `LobbyEvent.type` union |
| `react/.../cash/ActivityTicker.tsx` | Icon selection for vice events |

No schema migrations. No new tables. Vice is purely behavioral on
top of existing infrastructure.

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
