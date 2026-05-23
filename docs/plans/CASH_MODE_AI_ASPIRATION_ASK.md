---
purpose: Add an AI-initiated stake request mechanic so AIs can seek leverage to play at higher tiers without waiting to bust — the mirror of the Phase 5 player-as-staker flow.
type: design
created: 2026-05-21
last_updated: 2026-05-22
---

# Cash Mode — AI Aspiration-Driven Staking

> **Why this exists:** Phase 4 lets AIs stake each other on bust.
> Phase 5 lets the human player stake AIs upward through tiers. The
> missing direction is **AI-initiated upward mobility**: an AI who is
> doing well at their current tier deciding "I want to play higher,
> let me find a backer." Without it, the only path from $5 to $20 is
> winning enough chips to self-fund — and the sim shows that takes a
> long time, often longer than rake takes to grind a player back
> down. This doc adds the trigger + flow so AIs can climb the ladder
> with leverage, the same way real players do.

## The framing — what existing systems do vs. what's missing

| System | Direction | Trigger | Selection |
|---|---|---|---|
| **Phase 4 take_stake** | AI ↔ AI | Bust at current tier | Weighted random (staker incentives) |
| **Phase 5 player-staking** | Human → AI | UI click | Human picks from `stakable-ai` |
| **Aspiration-ask (this doc)** | AI ↔ AI/Human | Win-streak + wealth-gap | Weighted random + optional player surface |

The selection mechanism (weighted random by staker incentives) is
already shipped. The acceptance math is already shipped. The
infrastructure for stake creation + bankroll transfer + carry
settlement is already shipped. **The genuinely new piece is the
trigger — when does an AI decide to ask?**

## Why we can't just reuse Phase 5's stakable-ai pipeline

The 11 gates in `/api/cash/stakable-ai` look like they'd transfer,
and most of them do. The ones that **don't** transfer cleanly:

1. **Gate 5 (player bankroll floor)** — applies to the staker, not
   the borrower. For aspiration-ask the borrower asks first; we
   then *find* a staker who clears their own bankroll floor. Same
   math, different actor.

2. **Trigger surface** — Phase 5 fires from a UI click. There's no
   equivalent for AI-initiated; we need a probability-driven trigger
   inside the movement loop.

3. **Selection direction** — Phase 5 returns a *list* of candidates
   for the human to pick from. Aspiration-ask must *commit* to one
   staker (or no staker, deferring) inside one refresh tick.

4. **Acceptance evaluation** — Phase 5 evaluates the AI's
   willingness via `willingness_threshold + cut_penalty - desperation`.
   For aspiration-ask the *staker* is doing the evaluating, and the
   existing `staker_profile` (Phase 4's lender_profile rename)
   already covers that path — capacity + relationship gates + the
   new incentive scoring.

5. **Cooldowns** — Phase 5's 7-day default cooldown is per
   (AI, player) pair. Aspiration-ask needs per-AI cooldown after a
   successful ask (don't ladder-climb every tick) and per
   (AI, staker) cooldown after refusal (don't spam the pool).

So we **reuse** the existing matching engine + acceptance logic +
stake-creation flow, **borrow** the relationship/gate structure
from Phase 5, and **add** trigger logic + a per-AI ask cooldown.
Net new code is small; integration is the work.

## The trigger — when does an AI decide to ask?

Each refresh tick, for each seated AI not currently a borrower in an
active stake, roll an aspiration probability:

```
P_ask(ai) = clamp(0, MAX_ASPIRATION_PROB,
                  base_rate
                  × aspiration_bias_factor(ai)
                  × wealth_gap_factor(ai))
```

> **v1 deferral: `winning_momentum_factor` is not yet implemented.**
> The spec originally included a third multiplier driven by recent
> win-streak. Implementing it would require new psychology tracking
> (per-AI win-streak counter, persisted across hands), which is a
> yak-shave for the first cut. Without it, the formula relies on
> aspiration_bias + wealth_gap to do the work — both of which are
> already populated for every personality. The hooks for adding
> momentum back exist; revisit once we have sim data showing where
> the gap matters.

### `base_rate`

The floor probability per tick if all factors are 1.0. Suggested
**0.005** (0.5%). At 22 ticks/s in the sim that's ~10 asks per
second of simulated time across 80 AIs — too high without the
factor multipliers below pulling it down hard.

### `aspiration_bias_factor`

A new per-personality knob in `borrower_profile`:

```python
@dataclass(frozen=True)
class BorrowerProfile:
    willing: bool                           # existing
    willingness_threshold: float = 0.30     # existing
    aspiration_bias: float = 0.5            # NEW (0..1)
```

Mapped to the trigger as `aspiration_bias_factor = 2.0 × aspiration_bias`:
0.0 → 0× (never), 0.5 → 1× (baseline), 1.0 → 2× (eager climber).

**Derivation pattern** (for personalities not explicitly tuned):
derive from the existing curated anchors `ego` and `risk_identity`
(both 0..1, present on every personality with anchors):

```
aspiration_bias = clamp(0, 1, 0.6 × ego + 0.4 × risk_identity)
```

High-ego + high-risk → climber. Humble + cautious → grinder. Both
anchors are already in production data for 53 of 83 personalities;
the rest fall through to the flat default (0.5).

**Sample calibrations** (verified against actual anchor values):

- Lincoln (ego 0.36, risk_identity 0.38) → ~0.37: rarely aspires
- Baseline (no anchors) → 0.50
- High-ego, high-risk archetype (ego 0.85, risk 0.80) → 0.83
- Napoleon-class (ego 0.86, risk 0.90) → ~0.88

Note: the spec previously named anchors `ambition` and `composure`
that don't exist in this codebase. The derivation above uses the
canonical anchor names. `poise` (the "composed" anchor) was
considered as the inverse signal but `risk_identity` is the more
direct fit for aspirational gambling behavior.

Personalities with `willing=False` (the 4 refusers) automatically
have `aspiration_bias = 0` regardless of derivation — they refuse
ALL stakes, including ones they'd initiate.

### `winning_momentum_factor`

Recent equity trend. Pulled from the same psychology surface that
already tracks `last_hand_equity` and `confidence_anchor`. Shape:

```python
def winning_momentum_factor(ai) -> float:
    """1.0 baseline; 0..3.0 range. Recent wins boost."""
    streak = ai.psychology.recent_win_streak  # hands won in a row
    momentum = min(1.0, streak / 5.0)         # 5+ hands → maxed
    return 1.0 + 2.0 * momentum               # 1× to 3×
```

A 5-hand winning streak triples the aspiration probability. A
neutral or losing AI stays at 1.0. This captures the "I'm running
hot, time to take a shot" intuition.

`recent_win_streak` is a new psychology field — straightforward to
maintain (increment on win, reset on loss). Could also be derived
from the existing `recent_pnl` window if streak feels too binary.

### `wealth_gap_factor`

How rolled is this AI for the next tier? Defined as a bell-curve
against a **safely-rolled target** (multiple buy-ins), not the
min-buy-in alone:

```python
SAFE_BUY_IN_COUNT = 5

def wealth_gap_factor(bankroll, target_min_buy_in) -> float:
    """Peak at 0.5× safe-roll: 'half-way to feeling rolled'."""
    target = SAFE_BUY_IN_COUNT * target_min_buy_in  # 5 buy-ins of cushion
    ratio = bankroll / target  # 0.5 = halfway to safe-roll
    return max(0.0, 1.0 - 4.0 * abs(ratio - 0.5)) * 2.0  # peak = 2.0
```

> **Calibration note: SAFE_BUY_IN_COUNT=5 (was 1 in the original
> spec).** The first cut compared bankroll directly against
> `min_buy_in`, which produced near-zero fire rate: most AIs in our
> production data have bankrolls of 10-100× the next-tier min buy-in,
> so the ratio was ≥1 (self-fundable) and the factor sat at 0
> universally. Real poker bankroll management needs **multiple
> buy-ins** to feel rolled — 5 is a conservative-but-not-paranoid
> heuristic that put the trigger rate at ~7 fires per 1000 ticks
> (a sustainable rate that produces visible variety without spam).

The bell shape captures three regimes:

- **Far below target** (ratio < 0.25): bankroll is too thin to
  commit. Even with leverage, the AI can't sit comfortably; stakers
  would balk at the capacity gate anyway. Factor → 0.
- **Around half-way** (ratio ≈ 0.5): they're close. A stake bridges
  the gap. **This is the sweet spot for aspiration.** Factor = 2.
- **At or above target** (ratio ≥ 1.0): they can climb on their own
  via the normal `stake_up` movement path. Factor → 0.

The factor returns 0 outside the [0.0, 1.0] band, naturally
suppressing asks from AIs too poor or too rich for them to make
sense.

### Compound probability example

Napoleon (`aspiration_bias = 0.85`, `winning_momentum = 1.4` after a
2-hand streak, `wealth_gap = 1.8` at ratio 0.6):

```
P_ask = 0.005 × (2 × 0.85) × 1.4 × 1.8 = 0.0214 per tick = 2.1%
```

At 22 ticks/s, Napoleon would aspire ~once per second of sim time.
Across 50,000 hands (the 10k-tick run scale), he'd ask ~1100 times.
That's likely too often; tune `base_rate` down to **0.001** and the
expected ask rate drops to ~220 over the same sim — closer to the
~7 take_stake fires we observe today.

Buddha (`aspiration_bias = 0.05`, momentum 1.0, wealth_gap 0): never.

Lincoln (`aspiration_bias = 0`, willing=False): never.

## The ask — terms the AI offers

When an aspiration_ask fires, the AI proposes a stake with:

- **Target tier**: `comfort_zone + 1` (same as Phase 5 — one step up)
- **Principal**: `min_buy_in @ target tier` (lowest commitment)
- **Cut**: derived from personality, default **0.30**

The cut is the share the staker takes of post-floor winnings (same
as Phase 5's `FAIR_CUT_REFERENCE`). Personality bias:

```python
def offered_cut(ai) -> float:
    """Proud AIs offer less; humble AIs offer more."""
    base = FAIR_CUT_REFERENCE  # 0.30
    ego = ai.anchors.ego or 0.5
    # ego 0.5 → 0.30, ego 0.86 (Napoleon) → 0.245, ego 0.36 (Lincoln) → 0.34
    return clamp(0.15, 0.50, base - (ego - 0.5) × 0.15)
```

The cut is bound [0.15, 0.50] so proud AIs can't offer almost
nothing and humble AIs can't offer everything. Players accepting an
AI's aspiration_ask offer get a similar split shape to what they'd
offer themselves.

## The selection — finding a backer

Reuse `find_ai_staker_for` (the same function the bust-stake path
uses) with the borrower set to the asking AI. Critically:

- **`history_lookup` is passed** so weighted selection fires (the
  staker_incentives mechanism does its job here)
- **`candidate_pids` includes** all AIs at OTHER tables plus the
  idle pool — same shape as bust-stakes
- The wealth-overflow weight pulls flush AIs toward backing
  aspirational asks naturally

The aspirational borrower's relationship axes matter for selection
the same way they would for a bust-stake — a staker with positive
history toward this AI is more likely to back them.

**Additional ask: the player.** If the asking AI has met the
human and the human's projected bankroll covers `1.5 × principal`,
emit a lobby ticker event:

```python
EVENT_AI_ASPIRATION_ASK = {
    type: "ai_aspiration_ask",
    personality_id: <pid>,
    target_stake_label: "$200",
    suggested_principal: 50000,
    offered_cut: 0.30,
    message: "Napoleon wants a backer for $200 — 30% cut",
    expires_at: <now + 30s>,
}
```

The player has ~30 seconds to swoop in via a new endpoint
`POST /api/cash/aspiration-ask/<event_id>/accept` before either an
AI backer commits or the event expires. This is the "you can step
into the deal" affordance that makes aspiration_asks feel like
events the player can participate in, not just background ticker
spam.

(If both an AI staker AND the player accept simultaneously, the
human wins — same precedence rule as elsewhere in the cash flow.)

## The acceptance — staker evaluation

For an AI staker considering the ask, evaluation reuses the existing
`find_ai_staker_for` gates:

- `staker_profile.willing` must be True
- Capacity: `bankroll >= principal / max_loan_pct_of_bankroll`
- Relationship axes: `respect >= respect_floor`, `heat <= heat_ceiling`

Plus one new aspiration-specific gate:

- **Cut-acceptance**: the offered cut must be `>= staker.min_cut`.
  A staker with `min_cut = 0.30` won't accept Napoleon's 0.245
  offer. This gives proud-AI asks a friction surface — they can
  offer skimpy terms but they'll have fewer willing backers.

If no AI staker passes and no human steps in within the window: the
ask fails silently. The AI stays at their comfort tier, takes the
per-AI cooldown, and may try again later. No event spam — failed
asks don't ticker.

## Lifecycle — cooldowns + seat transitions

### Cooldowns

- **Per-asker**: 60 simulated seconds after a triggered ask (success
  or fail). Prevents ladder-climb spam.
- **Per (asker, refusing_staker)**: 30 minutes simulated. Don't
  re-ask the same staker who just refused. Stored as a relationship
  side-channel (no new table).

Both cooldowns checked at trigger time, *before* the probability
roll. AIs in cooldown don't even contribute to the rolled
population.

### Seat transitions

When an aspiration_ask succeeds:

1. **Create stake row** — status=active, both kinds=personality,
   principal=offered, cut=offered.
2. **Debit staker bankroll** by principal.
3. **The asking AI leaves their current table** via a new movement
   decision: `aspiration_climb`. The seat opens; their chips go to
   bankroll via normal `from_seat` flow.
4. **The asking AI is placed in the idle pool** with
   `target_stake = target_tier`. Next refresh's live-fill picks
   them up at the target tier's table.
5. **Emit lobby ticker event** `EVENT_AI_STAKE_UP_ASPIRATION` so
   players see "Napoleon climbing to $200 (backed by Bezos)".

The principal sits with the asker's idle-pool entry (or is held
in escrow on the stake row) until live-fill seats them at the new
table — at which point it funds the seat directly. Same
chip-conservation invariant as Phase 4 stakes.

### Settlement

When the asking AI eventually leaves their target-tier seat —
busted, voluntarily, or via further movement — settlement is the
same as any other active stake. The staker recovers via
`build_stake_settlement_flows`. Carry/default semantics unchanged.

## New per-personality knob

Single new field on `BorrowerProfile`:

```python
@dataclass(frozen=True)
class BorrowerProfile:
    willing: bool
    willingness_threshold: float = 0.30
    aspiration_bias: float = 0.5  # NEW
```

For backwards compatibility, every existing personality without an
explicit `aspiration_bias` in their JSON gets one derived from
anchors at load time (see `aspiration_bias_factor` above). Explicit
JSON overrides win — same pattern as Phase 5's
`willingness_threshold`.

## Implementation commits

Six commits, ordered for incremental shippability:

**Commit 1: BorrowerProfile.aspiration_bias + anchor derivation**
- Add field to dataclass
- Anchor-derivation helper (`compute_default_aspiration_bias`)
- Loader reads JSON override OR derives from anchors
- Tests: explicit override wins, derivation handles missing anchors,
  willing=False forces aspiration_bias=0

**Commit 2: Trigger probability helpers (pure, no DB)**
- `aspiration_bias_factor`, `winning_momentum_factor`,
  `wealth_gap_factor`, `compound_aspiration_probability`
- Tests: each factor at boundaries; compound produces sane
  rates across calibration examples

**Commit 3: Per-AI cooldown tracking**
- New SQLite table `ai_aspiration_cooldowns` or column on
  ai_bankroll_state — TBD by perf; probably the column is simpler
- Read at trigger time, write on every triggered ask
- 60-second simulated cooldown
- Per (asker, refusing_staker) cooldown via relationship side
  channel (probably reuse `relationship_states.last_event_at`)
- Tests: cooldown blocks within window, expires past it,
  failed asks still consume cooldown

**Commit 4: Aspiration trigger inside refresh_table_roster**
- New code path before bust-check: if not busted and aspiration
  trigger fires, attempt aspiration_ask flow
- Calls `find_ai_staker_for` with the existing weighted-selection
  machinery
- On success: emits StakeCreationChange + IdlePoolChange
- On fail: stamps cooldown, otherwise no-op
- Tests: high-bias + winning-streak AI fires asks; low-bias never
  fires; capacity-poor AIs skipped early; weighted selection picks
  flush stakers over poor ones

**Commit 5: Player-surfaced asks via lobby ticker**
- Emit `EVENT_AI_ASPIRATION_ASK` when the asking AI has met the
  player and player bankroll allows
- New endpoint `POST /api/cash/aspiration-ask/<event_id>/accept`
- 30-second simulated TTL on the event
- Precedence: human accept wins if both accept within window
- Tests: ticker fires when conditions met, doesn't fire otherwise;
  endpoint creates correct stake; race between AI and human
  resolves to human

**Commit 6: Settlement + idle-pool seat transition**
- New movement decision: `aspiration_climb`
- Idle pool entry includes the stake_id reference
- Live-fill at the target tier consumes the entry + funds seat from
  the stake principal
- Tests: end-to-end — Napoleon asks, Bezos backs, Napoleon seats at
  $200 with $40k buy-in, busts, settles cleanly

A reasonable stopping point is **after Commit 4** — at that point
AIs are climbing autonomously through AI-only stakes. Commits 5+6
polish the player-facing affordance and the seat transition.

## Locked decisions

1. **One step at a time.** Aspiration_ask only targets
   `comfort_zone + 1`. No jumping from $1 directly to $1000. The
   ladder is climbed one rung per ask.

2. **Trigger via probability, not deterministic threshold.** A
   deterministic "AI with >0.5 bias and ≥5-hand streak always asks"
   would create lockstep mass-asks at predictable moments. The
   probabilistic trigger preserves variety + emergence.

3. **`aspiration_bias = 0` for `willing=False` personalities.**
   AIs who refuse stakes outright don't get to ask for them either
   — preserves character consistency. Buddha is internally
   consistent.

4. **Wealth-gap factor peaks at 0.5× target.** The shape
   intentionally suppresses asks at the extremes (too poor → can't
   commit; rich enough → self-fund). This is what produces the
   "rising star" feel rather than "anyone with chips asks for
   leverage."

5. **Player can swoop in but isn't required.** Asks resolve
   to AI stakers by default. The player layer is the affordance,
   not the load-bearing path. If we shipped without Commit 5 the
   mechanic still works; players just don't get to participate.

6. **No belief decay (consistent with staker_incentives).** Match
   the staker_incentives doc's "v1: no decay" position.

## Open questions

1. **Cooldown durations** — 60s/30min are first-pass guesses. The
   sim is the right tool to tune these. Run a baseline; if the
   ladder churns too fast (everyone climbing every minute), raise
   the per-asker cooldown; if it's too sticky, lower.

2. **Does `wealth_gap_factor` need a different shape?** Bell-curve
   centered at 0.5 is the intuition; a flat threshold "ratio
   between 0.3 and 0.7" might be simpler and good enough. The bell
   curve gives smooth probability gradients near the boundaries.

3. **Should `aspiration_climb` failures bust the asker?** Currently
   no — the AI just takes the cooldown and tries later. An alternative:
   an AI who tries and is refused multiple times in a row takes a
   confidence hit (psychology). Adds spice; adds complexity. v1 skip.

4. **Should the offered cut be negotiable?** Currently the asker
   sets a single cut and stakers either take it or leave it. A
   counter-offer surface (staker proposes a different cut) would
   feel more like real backstage staking conversations. Probably
   v2.

5. **Do we want a separate movement decision for "tried to aspire
   but failed"?** Currently failed asks are silent. Surfacing them
   (`aspiration_failed` in the decisions dict) would give sim
   observability — "Napoleon tried to aspire 14 times in 1000 ticks
   but never found a backer." Probably worth doing in Commit 2 just
   for telemetry.

6. **Should we expose aspiration_bias in the personality manager?**
   Probably yes — it's a flavor knob admins should be able to tune.
   Same shape as the existing `willingness_threshold` editor.

## Why this matters

Today's cash mode has two staking surfaces and one upward-mobility
surface:

```
        Player → AI  (Phase 5: human stakes AI up a tier)
AI bust → AI       (Phase 4: peer bailout, same tier)
```

The diagonal is missing: there's no path for an AI to *initiate*
upward mobility on their own. The sim shows the consequence —
60-100% of AIs settle at their starting comfort zone and grind
there forever. Wealthy AIs accumulate at the top; poor AIs
stratify at the bottom; the middle is static.

Aspiration_ask closes the diagonal. The full mobility graph
becomes:

```
        Player → AI  (Phase 5)
AI bust ↔ AI      (Phase 4)
AI ask → AI/Player (this doc)
```

With all four arrows alive, the lobby becomes a real economic
graph — AIs climb when they're hot, get bailed out when they bust,
get sponsored by the human, and (rarely) borrow from each other to
take a shot. Combined with the staker_incentives mechanism, the
WHO of every stake is shaped by lived history.

The first time the player sees "Napoleon, riding a 7-hand streak,
is asking for $200 backing — Bezos took it" in the lobby ticker is
the moment the AI economy starts telling stories. Today's static
roster doesn't generate those stories because no AI ever asks. This
doc fixes that.

## Files this plan touches

| File | Change | Commit |
|---|---|---|
| `cash_mode/staker_profile.py` | Add `aspiration_bias` to `BorrowerProfile` + derivation helper | 1 |
| `poker/repositories/bankroll_repository.py` | Loader/saver reads/writes `aspiration_bias` | 1 |
| `cash_mode/aspiration.py` (new) | Trigger probability helpers (pure functions) | 2 |
| `poker/repositories/schema_manager.py` | Migration: `ai_bankroll_state.aspiration_cooldown_until` column | 3 |
| `cash_mode/movement.py` | New `aspiration_climb` decision + trigger inside `refresh_table_roster` | 4 |
| `cash_mode/lobby.py` | Wire trigger into the refresh; emit ticker events | 4-5 |
| `cash_mode/activity.py` | `EVENT_AI_ASPIRATION_ASK` constant + format helper | 5 |
| `flask_app/routes/cash_routes.py` | `POST /api/cash/aspiration-ask/<event_id>/accept` | 5 |
| `cash_mode/aspiration_settlement.py` (new) | Idle pool entry funding when live-fill consumes it | 6 |
| `tests/test_cash_mode/test_aspiration.py` (new) | Pure-function tests for triggers + factors | 2 |
| `tests/test_cash_mode/test_aspiration_integration.py` (new) | End-to-end: ask → backer → climb → settle | 4-6 |

One schema migration (Commit 3). No new top-level tables — the
cooldown column lives on the existing `ai_bankroll_state` row.

## Spec status

Parked alongside CASH_MODE_AI_STAKER_INCENTIVES.md and
CASH_MODE_AI_VICE_SPENDING.md. Suggested ship order:

1. **Finish backing system Phase 4.5 + Phase 5** — confirms the
   stake settlement and player flows are stable
2. **AI staker incentives** ✅ already shipped
3. **This plan: Commits 1-4** — autonomous AI climbing via
   AI-to-AI aspiration stakes
4. **Sim baseline** — re-run the no-regen 10k baseline to measure
   what aspirational mobility does to wealth distribution
5. **This plan: Commits 5-6** — player-facing affordance for asks
6. **Vice spending** — tunable against the new equilibrium

The first sim re-run after Commits 1-4 will be the key
measurement. If the no-regen 10k Gini drops from 0.80 toward
~0.65 with everything else unchanged, we know aspiration_ask is
doing real mobility work. If it doesn't move the needle, the
trigger constants need re-tuning before further mechanics layer
on top.
