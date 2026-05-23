---
purpose: Replace the random-among-qualified matching in `find_ai_staker_for` with weighted selection driven by staker incentives — primarily wealth-overflow pressure and skill belief built from per-pair stake history.
type: design
created: 2026-05-21
last_updated: 2026-05-21
---

# Cash Mode — AI Staker Incentives

> **Why this exists:** Phase 4's `find_ai_staker_for` filters
> candidates by lender willingness, capacity, and relationship axes,
> then picks one at random. That's structurally permitted staking —
> "you may stake" — not actively motivated staking — "you want to."
> Stakes today feel like charity loans dispensed by lottery. This
> doc replaces the lottery with a weighted selection where each
> qualified candidate's pick-probability reflects their actual
> incentive to deploy capital.

## The framing shift

In real-world staking, two questions decide who funds whom:

1. **Why does this staker want to deploy capital right now?**
   (Boredom, excess capital with nowhere to go, returns hunt, etc.)

2. **Why do they prefer this borrower over others?** (Skill belief,
   relationship trust, terms.)

Phase 4 doesn't ask either question. `find_ai_staker_for` answers
"who CAN stake" (capacity + relationship floor + willing flag), then
flips a coin. This doc adds the "why this staker, why this borrower"
layer on top.

The mechanism is intentionally a **scoring + weighted-random
selection**, not a deterministic "best candidate always wins." Real
poker staking has signal AND noise; pure deterministic picking
would feel mechanical and lock the cast into staking pairs over
time. Weighted random keeps the system probabilistic while letting
the design heavily favor "the AI who should pick this stake."

## Two new drivers (skip boredom for v1)

### 1. Wealth-overflow pressure

Without a hard chip cap (now removed), there's still soft pressure
on AIs sitting above their starting bankroll — vice spending will
chunk into them, and "having too much" is structurally undesirable
when each personality has a calibrated comfort point. **Staking is
an investment vehicle that puts chips out where they can earn
return rather than sitting overflow.**

Score contribution:

```
excess_pressure = clamp(0, MAX_EXCESS_BONUS,
                        excess_ratio × EXCESS_INCENTIVE_WEIGHT)
```

Where `excess_ratio` is the same metric vice spending uses:

```
excess_ratio = max(0, (bankroll - starting_bankroll) / starting_bankroll)
```

Note: comfort-floor is gone here vs the vice doc. We want even
modestly-flush AIs to feel some pull toward staking — staking
isn't "blowing money on a yacht," it's "putting chips to work."
The 1.0× floor (just above starting) is the threshold.

Suggested constants:
- `EXCESS_INCENTIVE_WEIGHT = 0.4` — meaningful weight bump per
  multiple of starting bankroll above the floor
- `MAX_EXCESS_BONUS = 2.0` — caps the contribution so a runaway-
  rich AI doesn't always win every match

A Bezos at 5× starting (excess_ratio = 4.0): `4.0 × 0.4 = 1.6` weight
contribution. A Napoleon at 1.2× starting (excess = 0.2):
`0.2 × 0.4 = 0.08`. A normal-bankroll AI: 0.

> **Bankroll vs net worth.** `excess_ratio` uses `bankroll.chips` —
> the operational chip count — NOT the `net_worth` value surfaced
> by the `/api/cash/net-worth` route. `net_worth` is a display-only
> derived value (`bankroll + receivables − payables`) computed at
> read time; nothing in the game state reads it back. Bankroll
> moves on real chip events (gameplay, sit-down debits, leave-time
> settlements, voluntary payoff, top-up). Carries are IOUs on the
> books that don't subtract from or add to bankroll — the chips
> tied to a carry are already wherever gameplay put them. The
> right metric for "does this AI have capacity to deploy capital
> right now?" is bankroll. A wealthy AI with $50k bankroll and
> $30k of receivables-as-staker has $50k of *real* capacity; the
> receivables are IOUs that may or may not resolve and aren't theirs
> to spend. Same for payables: they're debts on the books but the
> chips already left when the original stake busted.
>
> One subtlety: **active stake principal** physically leaves the
> staker's bankroll at deal time and sits in the borrower's seat.
> So an AI's `bankroll.chips` already reflects "chips deployed via
> active stakes." There's no need to subtract active principal from
> bankroll when computing excess — the bankroll number is already
> post-deployment. The audit's `actual_outstanding` invariant holds
> because seat chips count toward the borrower's seat ledger, not
> double-counted against the staker.

### 2. Skill belief from per-pair history

Stakers learn from past outcomes. An AI who has staked Hemingway
three times and been repaid each time develops a belief that
Hemingway is a good investment. An AI burned by a default from
Napoleon develops the opposite. The matching weight reflects this
learned trust.

Score contribution computed per (staker, borrower) pair:

```
belief_score = (
    settled_count × SETTLED_WEIGHT
    + carry_count × CARRY_WEIGHT
    + defaulted_count × DEFAULTED_WEIGHT
)
skill_belief = clamp(-MAX_BELIEF_BONUS, +MAX_BELIEF_BONUS,
                     belief_score × BELIEF_SCALE)
```

Suggested constants:
- `SETTLED_WEIGHT = +1.0` (clean repayment is the gold standard)
- `CARRY_WEIGHT = -0.5` (natural carry is bad but not character-defining)
- `DEFAULTED_WEIGHT = -1.5` (explicit default is the strongest negative signal)
- `BELIEF_SCALE = 0.3` (per-event contribution before clamp)
- `MAX_BELIEF_BONUS = 1.5` (belief can shift weight meaningfully but can't dominate other factors)

Examples:
- 3 settled, 0 carry, 0 defaulted: `(3)(1.0) × 0.3 = 0.9` weight bump.
  The "this person reliably pays me back" pattern.
- 1 settled, 0 carry, 1 defaulted: `(1 - 1.5) × 0.3 = -0.15`.
  Net slight negative — the default outweighs the settle.
- 2 settled, 1 carry: `(2 - 0.5) × 0.3 = 0.45`. Generally positive.
- No history: 0. Cold-start case (see "Cold-start handling" below).

## The new matching function

Replace `rng.choice(qualified)` with weighted random:

```python
def _candidate_weight(
    *, candidate, borrower_id, history, relationship_axes,
) -> float:
    """Composite weight for one candidate's pick-probability."""
    base = BASE_WEIGHT  # everyone starts above zero — small floor
    excess_part = clamp(0, MAX_EXCESS_BONUS,
                        candidate.excess_ratio × EXCESS_INCENTIVE_WEIGHT)
    belief_part = _belief_score(history.get((candidate.pid, borrower_id)))
    rel_part = _relationship_warmth(relationship_axes, candidate.pid, borrower_id)
    return max(MIN_WEIGHT, base + excess_part + belief_part + rel_part)


# Selection:
weights = [_candidate_weight(...) for c in qualified]
selected = rng.choices(qualified, weights=weights, k=1)[0]
```

The `BASE_WEIGHT` floor prevents zero-weight starvation — even an
AI with no incentive bonuses still has *some* chance, capturing the
"sometimes a stranger ends up being your backer" texture. Suggested
`BASE_WEIGHT = 1.0`, `MIN_WEIGHT = 0.01` (safety floor for clamped
negatives).

Final composition example:

| AI | excess | history vs borrower | relationship | total weight |
|---|---|---|---|---|
| Bezos (flush, good repayment from this borrower) | 4.0 | 3 settled | warm | 1.0 + 1.6 + 0.9 + 0.6 = **4.1** |
| Napoleon (normal bankroll, no history) | 0.2 | none | neutral | 1.0 + 0.08 + 0 + 0.3 = **1.4** |
| Buddha (modest excess, one default from this borrower) | 0.5 | 1 defaulted | neutral | 1.0 + 0.2 − 0.45 + 0.3 = **1.05** |
| Hemingway (no excess, no history, friend of borrower) | 0 | none | hot+friendly | 1.0 + 0 + 0 + 0.5 = **1.5** |

In this match, Bezos wins ~58% of the time, Napoleon ~19%, Hemingway
~21%, Buddha ~14%. Not deterministic but clearly weighted — the
"who SHOULD pick" answer dominates while the cast still sees
variety.

## Cold-start handling

A new borrower (or any borrower with no prior history with this
staker) has `belief_score = 0` — the contribution is neutral. This
is the right default: no positive evidence, no negative evidence,
so no bias.

A slight bias toward "give new borrowers a chance" could be added
via `COLD_START_BONUS = +0.2` for pairs with zero history, but it
risks creating a perverse incentive ("hop between borrowers, never
build a track record"). Probably not worth it for v1. Stick with
0-bias cold-start.

## Decay (deferred)

A stake from 6 months ago shouldn't weigh the same as one from
yesterday. Time-decay on belief is the natural follow-up, but adds
complexity and v1 doesn't need it — at the current cadence of
gameplay, "all-time history" is probably ~10-30 events per pair
maximum. Skip decay for v1; revisit if playtest shows belief feels
stale or sticky.

If/when we add decay, the simplest shape is exponential:
`weight = exp(-age_days / HALF_LIFE_DAYS)` per event. Half-life
~30 days probably feels right. The query becomes more expensive
(needs per-event age, not just per-status count) — that's the cost
to weigh.

## Data layer

New `StakeRepository` method:

```python
@dataclass(frozen=True)
class StakerHistoryStats:
    """Aggregated stake outcomes between one staker and one borrower."""
    settled_count: int
    carry_count: int
    defaulted_count: int

def aggregate_history_for_staker(
    self, staker_id: str,
) -> Dict[str, StakerHistoryStats]:
    """Return per-borrower outcome counts for this staker.

    Returns `{borrower_id: StakerHistoryStats}` covering every
    borrower this staker has interacted with. Uses a single SQL
    aggregate so the cost is one query per staker regardless of
    history depth.
    """
    with self._get_connection() as conn:
        rows = conn.execute(
            """
            SELECT borrower_id, status, COUNT(*) AS n
            FROM stakes
            WHERE staker_id = ?
              AND status IN ('settled', 'carry', 'defaulted')
            GROUP BY borrower_id, status
            """,
            (staker_id,),
        ).fetchall()
    by_borrower: Dict[str, Dict[str, int]] = {}
    for row in rows:
        by_borrower.setdefault(row["borrower_id"], {})[row["status"]] = row["n"]
    return {
        bid: StakerHistoryStats(
            settled_count=counts.get("settled", 0),
            carry_count=counts.get("carry", 0),
            defaulted_count=counts.get("defaulted", 0),
        )
        for bid, counts in by_borrower.items()
    }
```

**Caching:** the matching function may be called multiple times
per lobby refresh (one per busting AI). For each refresh, build a
per-staker history cache up-front so we don't query the same
staker's history multiple times. The cache lives in a closure
inside `refresh_unseated_tables` — disposed at end of refresh.

```python
_history_cache: Dict[str, Dict[str, StakerHistoryStats]] = {}

def history_for(staker_id: str) -> Dict[str, StakerHistoryStats]:
    if staker_id not in _history_cache:
        _history_cache[staker_id] = stake_repo.aggregate_history_for_staker(staker_id)
    return _history_cache[staker_id]
```

For ~30 AIs and a typical history depth of <20 borrowers per
staker, that's <600 row reads per refresh — comfortably within
budget.

## Relationship warmth contribution

Already computed inside `find_ai_staker_for` for the respect_floor /
heat_ceiling gates. Extract into a separate scoring helper rather
than re-querying:

```python
def _relationship_warmth(rel: Optional[Tuple[float, float, float]]) -> float:
    if rel is None:
        return 0.3  # neutral baseline for "no prior interaction"
    likability, respect, heat = rel
    warmth = (likability + respect) / 2 - heat × 0.4
    return clamp(0, MAX_WARMTH_BONUS, warmth × WARMTH_WEIGHT)
```

`WARMTH_WEIGHT = 1.0`, `MAX_WARMTH_BONUS = 1.0`. A perfectly
neutral pair contributes 0.3 (the no-prior-interaction baseline).
A genuinely friendly pair (likability + respect = 1.5, no heat)
contributes ~0.75. Hostile (high heat) drops toward 0.

## Implementation commits

Two commits, sequenced by dependency:

**Commit 1: `aggregate_history_for_staker` + StakerHistoryStats dataclass**
- New `cash_mode/staker_history.py` module holds the dataclass + scoring helpers (`_belief_score`, `_relationship_warmth`, `_candidate_weight`)
- Pure functions; no I/O at scoring time
- Repo method on `StakeRepository`
- Tests:
  - Aggregation query returns correct counts across borrower / status combinations
  - Belief score math: pure settled positive; pure default negative; mix nets out
  - Cold start (zero history) → 0
  - Clamp bounds respected at extreme inputs

**Commit 2: Wire weighted selection into `find_ai_staker_for`**
- `find_ai_staker_for` takes a new optional `history_lookup: Callable[[str], Dict[str, StakerHistoryStats]]` parameter
- When provided, use weighted selection per the formula
- When None, fall back to current `rng.choice(qualified)` behavior — preserves the pure-helper testability
- `cash_mode/lobby.py:refresh_unseated_tables` builds the cache + passes the lookup
- Tests:
  - Same `qualified` list + deterministic rng → wealthy AI wins more often than poor AI
  - Repeated-history bias: 5 settled vs 0-history candidates → repeated-history wins more often
  - Defaulted candidate has lower weight than no-history candidate
  - Backward compat: `history_lookup=None` produces the old random behavior

A reasonable v1 stopping point is here. The mechanic is complete
and AI cast develops real staking-relationship history over time.

## Locked decisions

1. **Two drivers for v1: wealth-overflow + skill belief.** Boredom-
   driven staking was considered (lobby's idle-pool dwell time as
   a signal) and deferred. The two drivers we keep are the highest-
   signal ones from real-world staking and they cover most of the
   "who should pick this stake" answer.

2. **Weighted random, not deterministic.** Top-candidate-always-
   wins would lock the cast into stable staking pairs. The
   weighted-random selection preserves variety while letting
   incentives clearly dominate.

3. **No belief decay for v1.** All historical events weigh equally.
   Revisit if playtest shows belief feels stale (decay needed) or
   feels too volatile (longer memory needed). The aggregate-query
   shape supports either future addition.

4. **Comfort floor at 1.0× starting** for the excess driver (not
   1.2× like vice spending). Staking is "investment" — even
   modestly flush AIs should feel some pull. Vice spending is
   "burn money" — that needs a higher buffer before kicking in.

5. **Backward compat: `find_ai_staker_for` callers that don't pass
   `history_lookup` still get random selection.** Pure-helper
   testability preserved.

## Open questions for playtest

1. **Do the constants balance?** EXCESS_INCENTIVE_WEIGHT = 0.4
   might be too dominant — playtest will tell us if Bezos always
   wins everything. The MAX_EXCESS_BONUS cap is the safety valve;
   tune it if one wealthy AI monopolizes all stakes.

2. **Does belief feel sticky?** Without decay, a 2-year-old default
   (in game time) weighs the same as a yesterday default. If the
   cast accumulates enough history, belief becomes noise rather
   than signal. The cap (`MAX_BELIEF_BONUS = 1.5`) bounds the
   damage but doesn't fix the noise.

3. **Should belief affect terms, not just selection?** Currently
   the cut is `staker.rate_anchor` regardless of belief. A natural
   extension: high-belief borrowers get a discount on the cut.
   Powerful but adds a negotiation surface — probably v2.

4. **Should we surface belief in the dossier?** "Bezos has staked
   Hemingway 4 times: 3 settled, 1 carry" is more interesting than
   just totals. Probably yes — extends the existing dossier stake
   summary without much code.

## Why this matters

Phase 4 made AIs *able* to stake each other. This doc makes them
*want* to, with reasons that come from their economic state and
their lived history with each borrower. Combined with vice
spending (the wealth-sink with character flavor), the cast starts
to behave like real economic agents — wealthy AIs deploy capital
into stakes they believe in, accumulate trust through repayment,
and burn excess on personality-appropriate indulgences when none
of that absorbs it.

The first time a player notices "Bezos picked Hemingway again,
even though Napoleon has a better current relationship — must be
because Hemingway always pays him back" is the moment the AI
economy stops being a substrate and becomes a story.

## Files this plan touches

| File | Change |
|---|---|
| `cash_mode/staker_history.py` (new) | StakerHistoryStats dataclass + scoring helpers |
| `cash_mode/movement.py` | `find_ai_staker_for` accepts optional `history_lookup`; uses weighted selection when provided |
| `cash_mode/lobby.py` | Build per-refresh history cache; pass lookup callable into `refresh_table_roster` |
| `poker/repositories/stake_repository.py` | New `aggregate_history_for_staker` method |
| `tests/test_cash_mode/test_staker_history.py` (new) | Unit tests for scoring + aggregation |
| `tests/test_cash_mode/test_take_stake.py` | Add cases for weighted selection vs the legacy random path |

No schema migrations. No new tables. The `stakes` table already
records everything needed; this is purely read-side aggregation +
matching policy.

## Spec status

Parked alongside the vice spending doc as a sibling AI-behavior
enhancement. Suggested ship order:

1. Finish the backing system through Phase 4.5 + Phase 5
2. **Staker incentives Commit 1+2** — replaces the random
   matching; AIs develop real preferences over time
3. **Vice spending** — wealth sink that interacts with the
   incentive system (an AI burning chips via vice has less excess,
   which lowers their `excess_pressure` weight in future matching)

The two systems compound nicely: vice spending drains rich AIs;
staking incentives let rich AIs deploy capital before vice gets
it. The "rich + tilted" AI has two outlets — burn it (vice) or
invest it (staking) — and the same psychology + bankroll state
drives both decisions, just with different mechanics.
