---
purpose: Implementation handoff for cash mode Path B — AI personalities as sponsors, replacing or augmenting the anonymous house sponsors with named characters whose loan offers depend on their bankroll, relationship to the player, and personality
type: guide
created: 2026-05-18
last_updated: 2026-05-18
---

# Cash Mode — Path B Handoff: AI-Opponent Sponsorship

This is **the high-leverage cash-mode feature**. v1 sponsorship
ships anonymous "house" sponsors (Friendly Boost, Loan Shark, etc.).
Path B replaces or augments those with **specific AI personalities**
offering loans on terms shaped by their character and their
relationship to the player.

> **Napoleon will lend you $500 at 35%, but only if he has the
> chips and you haven't burned him before. He's at the table; you
> see his face on the offer card. You take it. Now you owe him,
> specifically — and if you bust without paying, his `respect`
> toward you drops, his `heat` rises, and next time he sees you
> at his stake he plays harder.**

This is where cash mode + the relationship layer compound into the
sandbox the vision doc describes.

## Prerequisites

**Path A must ship first.** Lender-eligibility ("does Napoleon have
$500 to lend?") reads `load_ai_bankroll_current`. Without A, AI
bankrolls are roughly fictional — they don't reflect winnings — so
the lender economy runs on noise.

## What's already in place (v1 sponsorship)

- `cash_mode/sponsor_offers.py` — anonymous archetypes pool,
  `SponsorOffer` dataclass with `archetype_id` / `amount` / `floor`
  / `rate` / `flavor`.
- `cash_mode/loan_settlement.py:settle_loan_on_leave` — leave-time
  math (floor + cut + remainder).
- `player_bankroll_state.active_loan_*` columns (schema v89).
- `/api/cash/sponsor-offers` + `/api/cash/sponsor-and-sit` routes.
- `SponsorModal` + `BustModal` frontend.
- Relationship layer (Phase 1+) — `RelationshipEvent` enum,
  `RelationshipState`, `record_event`, `project_heat`. Cash mode
  already wires `cash_mode=True` into the relationship repo.

Path B extends all of these.

## Design — Path B

### B.1: Lender profile on personality

Add `lender_profile` to each personality's `config_json` (alongside
`bankroll_knobs`):

```jsonc
{
  "lender_profile": {
    "willing": true,                // does this personality lend at all?
    "max_loan_pct_of_bankroll": 0.10,  // largest loan as fraction of bankroll
    "floor_anchor": 1.10,           // their default floor multiplier
    "rate_anchor": 0.25,            // their default cut rate
    "respect_floor": -0.3,          // refuse if respect(player) < this
    "heat_ceiling": 0.6             // refuse if heat(player) > this
  }
}
```

**Defaults** (when `lender_profile` absent): `willing=true`,
`max_loan_pct_of_bankroll=0.05`, `floor_anchor=1.20`, `rate_anchor=0.30`,
`respect_floor=-0.5`, `heat_ceiling=0.7`. Conservative defaults so
new personalities default to cautious lenders.

**Reading**: extend `BankrollRepository` with `load_lender_profile(personality_id)`
mirroring `load_personality_knobs` — same `config_json` round-trip,
same per-field fallback.

**Personality archetypes** (how to tune the seed values):

| Personality vibe | `willing` | `max_loan_pct` | `floor_anchor` | `rate_anchor` |
|---|---|---|---|---|
| Generous philanthropist (Lincoln, Buffett) | true | 0.15 | 1.00 | 0.15 |
| Hard-nosed business (Trump, Bezos) | true | 0.10 | 1.20 | 0.35 |
| Predatory (mob, hustlers) | true | 0.08 | 1.40 | 0.45 |
| Casual buddy (Bezos, dad-jokes guy) | true | 0.05 | 1.10 | 0.20 |
| Won't lend (Mime — chaos chars) | false | — | — | — |

### B.2: Offer generation — eligibility + materialization

Replace the random-archetype sampling at sponsor-modal-fire with a
**relationship-and-bankroll-aware** generator:

```python
def compute_personality_offers(
    *,
    player_owner_id: str,
    stake_label: str,
    candidate_personalities: list[str],  # who's at the table OR available
    bankroll_repo, relationship_repo, now,
    count: int = 3,
) -> list[PersonalitySponsorOffer]:
    """For each candidate, check willingness + capacity + relationship.
    Return up to `count` qualifying offers, sorted by capacity desc."""
```

**Eligibility gates per candidate** (all must pass):
1. `profile.willing == True`
2. `project_bankroll(...) >= loan_amount_for_this_table` (loan amount
   = candidate's `max_loan_pct_of_bankroll × projected_bankroll`,
   clamped to table's `[min_buy_in, max_buy_in]`)
3. `relationship.respect >= profile.respect_floor`
4. `relationship.projected_heat <= profile.heat_ceiling`
5. **No outstanding unpaid loan from THIS sponsor** (one loan per
   sponsor per player at a time)

**Term adjustment by relationship:**
- High likability (>0.5): `floor -= 0.05`, `rate -= 0.05`. "Friend tax."
- High heat (>0.4): `floor += 0.10`, `rate += 0.10`. "I'll lend, but you'll pay."
- High respect (>0.5): `floor -= 0.03`, `rate -= 0.03`. "I think you'll win — fair terms."

Clamp adjusted values: `floor ∈ [1.00, 1.50]`, `rate ∈ [0.00, 0.55]`.

**Pool selection** for "candidate_personalities":
- v1 of Path B: include all personalities **at the player's current
  table** (their seat means they're present and can offer the loan
  in-character).
- v2 of Path B: extend to AIs **not at the table but available** —
  "Bezos isn't at this table but he's heard about you; he sends a
  message offering a loan." Adds atmosphere.

### B.3: Data model — the loan now references a lender

Schema v90: add `active_loan_lender_id TEXT` column to
`player_bankroll_state`. NULL = anonymous house loan (v1
sponsorship), else = `personality_id` of the AI lender.

```sql
ALTER TABLE player_bankroll_state
    ADD COLUMN active_loan_lender_id TEXT DEFAULT NULL;
```

Extend `PlayerBankrollState` dataclass with `active_loan_lender_id:
Optional[str] = None`. `BankrollRepository.save/load_player_bankroll`
round-trips it.

### B.4: Relationship events

Emit on loan creation and settlement:

| Trigger | Event |
|---|---|
| Player accepts AI sponsor offer | `SPONSORSHIP_OFFERED` (observer=AI, target=player) — small respect bump (AI extended trust) |
| Player repays loan in full at leave | `LOAN_REPAID` — likability +, respect + |
| Player partial-repays (chips < floor) | `LOAN_DEFAULTED` — respect −−, heat ++, likability − |
| Player bankrolls > 0 but walks away without paying floor (only possible if they leave WITH chips < floor while having other chips in bankroll — currently impossible because chips at table go through settlement first; defer this edge case) | n/a v1 |

Add the enum entries to `poker/memory/relationship_events.py` (or
wherever `RelationshipEvent` lives) with dispatch table entries
specifying the axis shifts.

**AI behavioral reaction** lands automatically — the relationship
layer's modifier seam already feeds tier shifts into the controller,
so a defaulting borrower triggers Napoleon to play sharper against
them in future hands without explicit wiring.

### B.5: Leave-time settlement — extend for lender-specific credit

`settle_loan_on_leave` currently routes the sponsor's take into the
ether (anonymous). Path B routes it to the AI lender's bankroll:

```python
def settle_loan_on_leave(bankroll, chips_at_table, bankroll_repo=None):
    # ... existing math ...
    if bankroll.active_loan_lender_id and bankroll_repo:
        # Credit sponsor_total back to the AI lender's bankroll,
        # clamped to their cap.
        _credit_ai_lender(
            bankroll.active_loan_lender_id, sponsor_total, bankroll_repo,
        )
    # ... rest of settlement ...
```

If `lender_id` is NULL → anonymous loan → no AI credit. Backward-
compatible with v1 sponsorship (NULL = house loan).

### B.6: Frontend — sponsor modal upgrade

Current `SponsorModal.tsx` shows anonymous offers. Path B variant:

- Show the AI's **avatar + display name** at the top of each offer
  card.
- Subtitle: "Offering you a loan." (gracious lender) or "Eyes you
  warily — you defaulted on Bezos last time." (sketchy lender,
  shown when relationship.respect is low).
- Show **relationship hint**: a small indicator near the AI's name
  ("trusted", "watching you", "wants their money back") that
  surfaces the underlying axes without exposing raw numbers.
- The loan-amount and terms still show as before; flavor text is
  now per-personality (read from a `lender_lines` field in personality
  config, or generated on the fly).

The frontend type changes: `SponsorOffer` gains `lender_id?: string`
(omitted for house offers), `lender_name?: string`,
`lender_avatar_url?: string`, `relationship_hint?: string`.

### B.7: Mixed pool — house and personalities together

The sponsor modal shows **mixed offers**: some anonymous, some from
specific AIs. Anonymous offers stay as a fallback when no
personality qualifies (relationship too damaged, all AIs at cap of
their lending, nobody at the table is willing).

Default mix: prefer personality offers (1-3 of them based on who
qualifies) plus fill with anonymous offers if fewer than 3 qualify.

## Suggested commit breakdown (~7 commits)

**Commit 1: Schema v90 + lender_id field**
- ALTER TABLE migration.
- Extend `PlayerBankrollState` + `BankrollRepository` round-trip.
- Tests: schema shape, default NULL for legacy rows.

**Commit 2: `lender_profile` config + repo**
- `LenderProfile` dataclass in `cash_mode/lender_profile.py`.
- `BankrollRepository.load_lender_profile` (with defaults).
- Seed `lender_profile` entries in `personalities.json` for all 53
  personalities (use the archetype table above as starting tuning).

**Commit 3: Personality offer generator**
- `cash_mode/sponsor_offers.py` gains `PersonalitySponsorOffer` +
  `compute_personality_offers(...)`.
- Pure function tests: eligibility gates, term adjustments by
  relationship axes, capacity clamp to bankroll.

**Commit 4: Sponsor route — personality + house mixed offers**
- `/api/cash/sponsor-offers` now returns mixed offers.
- `/api/cash/sponsor-and-sit` accepts either `archetype_id` (house)
  or `lender_id` (personality) — picks the right code path.
- Tests: house path still works; personality path writes
  `lender_id` to bankroll.

**Commit 5: Leave-time AI lender credit + relationship events**
- Extend `settle_loan_on_leave` to credit AI bankroll when lender_id
  is set.
- Emit `SPONSORSHIP_OFFERED` at sponsor-and-sit time, `LOAN_REPAID`
  or `LOAN_DEFAULTED` at leave time based on settlement outcome.
- Add the `RelationshipEvent` enum values + dispatch entries.
- Tests: math + events fire correctly across the three outcomes
  (full / partial / no winnings).

**Commit 6: Frontend — sponsor modal personality cards**
- `SponsorOffer` type gains optional lender fields.
- `SponsorModal.tsx` renders avatar + name + relationship hint when
  `lender_id` is set; falls back to anonymous flavor for house offers.
- TypeScript check.

**Commit 7: Docs sweep + Path B marked shipped**
- Update `CASH_MODE_AND_RELATIONSHIPS.md` §"Bust semantics" to
  reflect personality sponsors.
- Mark this handoff "Status: shipped" with commit range.

## Open questions

1. **Should the player see the AI's bankroll number when picking
   a loan?** Showing it makes the "can I default safely?" calculation
   explicit. Hiding it keeps the relationship-mystery alive. v1 of
   Path B: hide. v2: maybe a vague indicator ("Napoleon looks
   well-stocked").

2. **What if the AI lender BUSTS at the table after lending?**
   AI table stack hits 0 but their loan to the player is outstanding.
   `_refill_cash_seats` replaces them. The loan should remain
   collectible — the player owes Napoleon, even if Napoleon's not
   at this table anymore.

3. **One loan per session vs many?** v1 of Path B: one loan per
   session per AI lender (already in eligibility gate). v2: could
   allow concurrent loans from different lenders.

4. **Should defaulting affect ALL AIs' willingness to lend, or just
   the defaulted-on one?** Just the one (per the v1 spec above) is
   the right starting point. v2: reputation bleeds — defaulting on
   Napoleon makes Trump warier too ("word gets around").

5. **AI-to-AI lending** (sketched in this conversation as a future
   item) — out of scope for Path B. Path C territory.

## Path C dependencies on B

When Path C (multi-table lobby + background sim) ships, AI-AI
lending becomes meaningful: a busted AI at one table can be
sponsored by a winning AI at another, keeping them in circulation.
The data model already supports this via `active_loan_lender_id`
on a future `ai_bankroll_state`-side debt column (or via reusing
the player_bankroll table pattern). Designing for that now would
add scope; deferred.

## Files to read first

1. **This doc** — design above.
2. **`docs/plans/CASH_MODE_SPONSORSHIP_HANDOFF.md`** §"v2 deferred" —
   the original sketch this expands.
3. **`cash_mode/sponsor_offers.py`** — the archetype pool this
   extends.
4. **`cash_mode/loan_settlement.py:settle_loan_on_leave`** — the
   settlement function gaining AI-credit logic.
5. **`poker/memory/relationship_events.py`** (or wherever
   `RelationshipEvent` lives) — new enum values land here.
6. **`react/react/src/components/cash/SponsorModal.tsx`** — UI to
   extend with personality cards.
7. **`poker/personalities.json`** — `lender_profile` seeds land
   here.

## Why ship B after A

(Already covered in Path A handoff §"Why ship A before B".) Short
version: B's lender capacity check reads AI bankrolls; without A,
that number is fictional.
