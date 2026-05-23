---
purpose: Closed-loop thesis for the cash-mode economy — frames vice, tourists, staking, and the stakes ladder as one self-balancing system with two control knobs, identifies the gaps between current docs and the closed-system claim, and lists the sim experiments needed to validate it.
type: design
created: 2026-05-23
last_updated: 2026-05-23
---

# Cash Mode — Closed Economy

> **Why this exists:** The cash-mode docs each describe a slice of the chip flow — `CASH_MODE_ECONOMY` documents the ledger as built, `CASH_MODE_AI_VICE_SPENDING` designs the AI-side sink, `CASH_MODE_PLAYER_CHIP_SINKS` catalogs the player-side sinks, `CASH_MODE_AND_RELATIONSHIPS` Part 3 sketches the soft cap. None of them assert the closed-loop claim. This doc does: with the right combination of vice (sink + redistribution), tourists (injection), staking (intra-cast redistribution), and a small stakes-ladder soft cap, the cash economy is **a closed system with two primary tunable knobs** — vice rate and tourist rate. Inflation only enters via explicit player additions. Everything else recycles.
>
> This is a *thesis*, not an implementation handoff. None of the new mechanics described as "gaps" below are built. The point is to write down what we're aiming at so each subsequent implementation pass can be measured against it.

## The closed-loop claim

The cash economy is closed if and only if every chip that enters the universe either (a) stays in player + AI bankrolls in a bounded steady-state distribution, or (b) is destroyed in a way that's matched by a controllable injection elsewhere. The diagram the design is reaching for:

```
                      vices                 tourists
   big-money players  ────►   central bank  ────►  casino tier
        ▲       │                 ▲                     │
        │       │ staking         │ vices               │ winnings
        │       │                 │                     ▼
        │       ▼                 │              broke / casino
   winnings    medium players ────┤                grinders
                  ▲       │       │                     ▲
                  │       │       └────── vices ────────┘
                  │       ▼ staking                     │
                  │     broke players ──── winnings ────┘
                  │       ▲
                  └───────┘ winnings

   • Staking flows DOWN the tier ladder (big → medium → broke)
   • Winnings flow UP (broke → medium → big), modulated by skill + variance
   • Vices flow from anyone with excess UP to the bank
   • Tourists are injected by the bank into the casino tier
```

Two primary control knobs:

- **Vice rate** — how aggressively chips are drained from the top
- **Tourist rate** — how aggressively chips are reinjected at the bottom

A third lever, **new-player injection**, is the *only* way the universe grows. It's intentional, infrequent, and exposed to the operator (not to players).

If vice rate ≈ tourist rate (over a long enough window), the bank stays bounded and the chip universe stays bounded too. The wealth distribution between tiers is shaped by skill, variance, staking, and personality — but the *aggregate* is stable.

## How current systems map to the loop

| Diagram element | Current status | Doc |
|---|---|---|
| Big → medium → broke staking | Phase 1+2 shipped; Phase 5 (humans as stakers) in progress | `CASH_MODE_BACKING_SYSTEM_HANDOFF.md` |
| Backing with "cut over X hands" terms | Designed (`stake_terms` shipped) | same |
| Soft cap at $1000 (top of ladder) | Designed, not enforced — ladder ships up to $1000 but nothing prevents bankroll growth past it | `CASH_MODE_AND_RELATIONSHIPS.md:692` |
| AI vice (rich AI bankrolls drained) | Designed, not built — wealth × pressure trigger | `CASH_MODE_AI_VICE_SPENDING.md` |
| Player chip sinks (7 cataloged) | None built; staking is the only one in flight | `CASH_MODE_PLAYER_CHIP_SINKS.md` |
| Casino as starting tier | Stakes ladder $2 / $10 / $50 / $200 / $1000 — the bottom two are "casino" conceptually | `CASH_MODE_ECONOMY.md` |
| Country clubs / private tables (mid + top venues) | Designed in one sentence each; undesigned beyond that | `CASH_MODE_PLAYER_CHIP_SINKS.md` #2, #6 |
| Rake (per-hand pot skim) | Built (`table_rake` ledger reason) | `CASH_MODE_ECONOMY.md` |
| Central bank as pass-through | Conceptually present (no row, just ledger source/sink); currently a pure sink for vice/rake — not a pump for tourists | `CASH_MODE_ECONOMY.md` |

## Gaps between current design and the closed-loop claim

The four mechanics below are what the diagram asserts but the docs don't currently cover. Listed in priority order — gap #1 is the one that *makes* the system closed; the others extend or refine the loop.

### Gap 1 — Vice as redistribution, not destruction

**Current design:** `record_vice_spending(ai → central_bank)` destroys chips. The ledger entry shrinks the universe; nothing re-creates the chips.

**Closed-loop design:** vice payments accumulate in a logical bank pool (still no DB row — the bank is virtual, see `CASH_MODE_ECONOMY.md`'s conservation invariant). A new `central_bank → casino_seat` flow ("tourist injection") draws from that accumulated pool. Net universe change: zero. Net behavior: chips move from rich AIs to the casino tier where broke grinders can take them.

**Implementation surface:**

- New ledger reason `tourist_injection` (`central_bank → ai_seat` or `central_bank → ai_bankroll`, scoped to casino-tier AIs)
- New process — call it `release_tourists` — that runs on lobby refresh, similar to vice resolution. Reads accumulated vice/rake bank pool, decides whether to inject, picks a casino-tier seat to fill, writes the ledger entry.
- Tourist NPCs need to be modeled. Two shapes:
  - **Synthetic AIs** — temporary personalities, short-lived, lose to grinders by design. Bankrolls = injection amount; they bust out and disappear.
  - **Existing personality regen** — instead of `ai_regen` always being free chip creation, regen draws from the bank pool. Bounds the regen system too, which is currently unbounded per `CASH_MODE_ECONOMY.md` known issues.
- Bank pool needs an audit-visible computed value: `accumulated_vice_chips - released_tourist_chips`. Stays non-negative if release_rate ≤ vice_rate. Surface in `/api/admin/chip-ledger/audit`.

**Open question:** does the operator dial tourist rate directly, or does the system auto-balance (e.g., release more tourists when bank pool exceeds a target reserve)? Auto-balance is simpler to ship but loses operator control. Direct dial gives playtest leverage.

### Gap 2 — Player-side vice (involuntary progressive tax on hot streaks)

**Current design:** `CASH_MODE_PLAYER_CHIP_SINKS.md` catalogs 7 sinks, all voluntary (you choose to stake, host, unlock, create). Nothing involuntarily drains a winning player.

**Closed-loop design:** the same wealth × pressure trigger as AI vice fires on players. Differences from AI vice:

- Trigger is **velocity-based**, not just absolute wealth. "You've won $X in the last N hands" → vice rolls. This matches the gambling-fantasy framing of "running too hot" better than a static threshold and avoids the obvious feel-bad of "I crossed $5K once, now I'm bleeding."
- No psychology pressure modifier for players (we don't model the player's confidence/composure/energy). Wealth-velocity is the whole trigger.
- The narrative wrapper is heavier. AI vice narration is one LLM line on the ticker; player vice should be a moment — a notification, possibly a choice ("you've been winning hard; pay $X for a celebration / lose the chip" framing). Mechanically the same destruction, narratively interactive.
- Player vice chips flow into the same accumulated bank pool that funds tourists. Same redistribution mechanism, just sourced from above.

**Implementation surface:**

- New ledger reason `player_vice_spending`
- New `cash_mode/player_vice.py` mirroring `cash_mode/ai_vice_spending.py` but with player-specific trigger
- Per-player velocity tracker (chips won in last N hands) — likely already derivable from `chip_ledger_entries`
- Frontend treatment for the vice event (modal, ticker, dossier)

**Open question:** is player vice opt-in (a "casual mode" / "hard mode" choice) or always-on? Opt-in adds settings UI but lets risk-averse players avoid it. Always-on is closer to the gambling fantasy.

### Gap 3 — Tourists as a modeled economic actor

**Current design:** no tourist concept. Bottom-of-ladder AIs are existing personalities with `ai_regen` topping them up from the void.

**Closed-loop design:** tourists are a distinct cohort. They're funded from the bank pool (gap #1), they sit at casino-tier tables, they lose disproportionately to skilled grinders (by design — they're the *fish*), they bust out and disappear. Mechanically:

- Tourist seat fills at a casino-tier table look identical to a normal AI fill, but the underlying personality is either a short-lived synthetic or a flagged "tourist mode" of an existing personality.
- Skill profile: weakest tier, no exploitation logic, predictable patterns. They're the fish that's supposed to lose.
- Tourist bankrolls aren't capped via `bankroll_cap` — when they bust, they're done. No regen.
- Tourist injection rate is the second control knob. Operator-tunable. Drives bottom-tier chip liquidity.

**Why this matters for the closed loop:** without tourists, the only way chips re-enter the bottom of the system is via player losses there. That's variance-driven, not controllable. With tourists, chip liquidity at the casino tier is a *dialable property*, not an emergent one.

**Open question:** are tourists implemented as ephemeral synthetic AIs (new personalities created/destroyed) or as a behavior mode on existing personalities? Synthetic is cleaner (no cross-contamination with the cast economy) but adds personality lifecycle code. Behavior mode is lighter but couples tourist liquidity to which personalities happen to be available.

### Gap 4 — Rake replaced or redirected

**Current design:** `table_rake` is per-hand pot skim, destroys chips. Hits everyone proportionally to pot size. Regressive: mid-tier and broke players who can least afford it pay rake on every hand.

**Closed-loop design (option A — replace):** drop rake entirely. Casino tier becomes pure redistribution from tourists to grinders. The bank pool grows only from vices. This is the simplest closed-loop form.

**Closed-loop design (option B — redirect):** rake stops being destruction and instead feeds the bank pool. Mechanically: `table_rake` source/sink changes from `seat → central_bank (destroy)` to `seat → central_bank (accumulate)`, i.e. same accounting but tagged as recyclable. Rake then funds tourists, same as vices do. Effect: rake is no longer a pure leak; it's a tax that recycles back to the casino tier where the rake was paid.

**Either way:** the progressive intent ("punish mid-tier players less, take from the rich") needs vice to do the heavy lifting. Replacing rake (option A) makes that explicit. Redirecting rake (option B) is conservative — same code path, different semantic — but mid-tier players still pay rake on every hand.

Recommendation: option B for v1 (cheap to ship — just change which pool the chips notionally go to), revisit option A after vice is live and we can see whether rake is still pulling its weight as a chip drain.

## Control knobs and equilibrium

Under the closed-loop design, three knobs govern the entire chip universe:

| Knob | Effect | Where it lives |
|---|---|---|
| **Vice rate** | How aggressively top-tier bankrolls drain. Higher = more chips into bank pool, more aggressive redistribution. | `VICE_PROB` formula constants in `cash_mode/ai_vice_spending.py` + (new) `cash_mode/player_vice.py` |
| **Tourist rate** | How fast bank pool is paid out to casino tier. Higher = more chip liquidity at the bottom, faster grinder advancement. | (new) `release_tourists` parameter |
| **New-player injection** | One-shot chip creation per new player. Sole inflation knob. | `player_seed` ledger reason (already shipped) |

The system is in equilibrium when:

```
new_player_injection_rate ≈ 0   (no new players entering)
vice_rate ≈ tourist_rate        (bank pool stays bounded)
⇒ chip universe stable
```

In a *growing* server (real-world):

```
new_player_injection > 0
total_chips = initial + cumulative_player_seeds
```

The two recycling knobs (vice + tourist) determine the *distribution* across tiers but don't change the total.

**The interesting failure modes — these are what to watch for in sim:**

1. **Bank pool runaway.** If `vice_rate > tourist_rate` indefinitely, bank pool grows without bound. Chips that "should" be in circulation are stuck in the pool. Players feel the system is draining them with no return. Mitigation: tourist rate auto-scales with bank pool depth.
2. **Bank pool exhaustion.** If `tourist_rate > vice_rate` for long enough, the pool empties. Tourists stop appearing. Casino tier becomes a desert. Players in early/mid game stall. Mitigation: tourist rate auto-caps based on pool reserves.
3. **Equilibrium with unhealthy distribution.** Total chips bounded, but 95% of chips are at the top. Vice rate isn't high enough relative to skill-driven climb. Players plateau. Measurable: Gini-like inequality metric over bankroll distribution.
4. **Equilibrium that suppresses character signal.** Vice rate so high that nobody stays rich long enough for "Bezos is doing well" to be a real character beat. Vice frequency outruns narrative pacing. Measurable: mean time between vice events per AI vs hand cadence.

Auto-balance is the conservative move. A bank-pool-aware tourist release rule (something like `tourist_rate = base_rate × clamp(0, 1, pool_chips / target_pool)`) prevents failure modes 1 and 2 without operator vigilance. The operator still picks `base_rate`; the system damps it.

## Sim experiments to validate

The harness should run these in order. Each is a falsifiable check against the closed-loop claim.

### EXP 1 — Closed system holds under steady state

**Setup:** Full cast, no human players, no new injections, vice + tourist injection live, rake redirected to bank pool (option B above).

**Hypothesis:** `total_chips` stays constant ± floating-point noise over N=100k hands.

**Pass:** drift < 0.1% of universe size, audit reports `drift == 0`.

**Fail signal:** if universe drifts, something's leaking — likely an unwired ledger entry on tourist injection or vice flow. This is the basic "did we wire it right" check.

### EXP 2 — Wealth distribution stabilizes

**Setup:** Same as EXP 1.

**Hypothesis:** Gini coefficient of cast bankroll distribution converges to a stable value within N=10k hands, not monotonically increasing.

**Pass:** rolling-window Gini variance < 0.05 over the last 20% of hands.

**Fail signal:** monotonic Gini growth means vice isn't pulling hard enough against skill-driven concentration. Tune vice constants.

### EXP 3 — Bank pool stays bounded

**Setup:** Same as EXP 1.

**Hypothesis:** `accumulated_vice_chips - released_tourist_chips` oscillates around a target value, doesn't grow without bound.

**Pass:** pool size stays within [0.5×target, 2×target] over the last 50% of hands.

**Fail signal:** pool grows monotonically (vice > tourists) or empties (tourists > vice). Indicates either the constants are mistuned or the auto-balance rule isn't working.

### EXP 4 — Casino tier liquidity holds

**Setup:** Same as EXP 1. Add a "starving grinder" metric: a synthetic player at casino tier with realistic skill, measure their bankroll trajectory.

**Hypothesis:** the synthetic grinder can advance from $0 to $200 tier in finite time (N=5k hands) without busting permanently, given tourists are present.

**Pass:** grinder reaches $200 tier in at least one of 10 trials within N hands.

**Fail signal:** no advancement → tourist liquidity is too low or grinder skill model is wrong. Tells us whether the bottom rung is climbable.

### EXP 5 — Stratification persists

**Setup:** Same as EXP 1, run for N=100k hands.

**Hypothesis:** at the end of the run, the top 10% of bankrolls are still meaningfully ahead of the bottom 10% (ratio > 5×), i.e. vice doesn't *flatten* the cast.

**Pass:** top-decile / bottom-decile bankroll ratio > 5 in the final 10% of hands.

**Fail signal:** vice is over-aggressive — the cast economy loses character differentiation. "Bezos" and "Buddha" end up with the same bankroll. Lower vice rate or sharpen wealth gate.

These five together verify: (1) the system is closed, (2) the distribution converges, (3) the bank pool is bounded, (4) the bottom rung is climbable, (5) the top still feels like the top. Failure on any one points at a specific knob.

## Locked claims

These are the design positions this doc commits to, distinguishing them from open questions.

1. **The bank is a pass-through, not a destination.** Vice and rake accumulate in the bank pool; tourists draw from it. Chips don't permanently leave the universe except via explicit one-time mechanics that aren't part of the loop.

2. **Two recycling knobs, one inflation knob.** Vice rate and tourist rate manage the recycle. New-player injection is the only universe-growth lever, exposed to operators not players.

3. **Player vice is involuntary and velocity-triggered.** Hot streaks → vice rolls. Static wealth thresholds are not the primary trigger (matches the gambling-fantasy framing).

4. **Tourists are a modeled cohort, not metaphor.** They fund chip injection at the casino tier and have a distinct lifecycle (appear → lose → disappear).

5. **Casino tier is the graduation venue.** Tourists feed grinders; grinders climb the stakes ladder until they outgrow the casino. Up-tier mobility is the player progression story.

6. **Auto-balanced tourist release is the v1 shape.** Operator picks a base rate; the system damps based on bank pool reserves. Avoids both runaway and exhaustion failure modes without operator vigilance.

7. **Soft cap on stakes ladder remains.** Top of ladder is $1000 (per `CASH_MODE_AND_RELATIONSHIPS.md:692`). Bankroll past that is *only* for sinks — including vice as the involuntary one.

8. **Rake redirects, doesn't destroy (v1).** Rake feeds the bank pool same as vice. Revisit dropping rake entirely after vice is live and the casino tier's chip flow is measurable.

## Source docs

- `docs/technical/CASH_MODE_ECONOMY.md` — implemented ledger surface, pools, conservation invariant. The closed-loop design must preserve `drift == 0`.
- `docs/plans/CASH_MODE_AI_VICE_SPENDING.md` — AI-side vice design. This doc's gap #1 (vice as redistribution) requires changing the AI vice doc's locked decision that vice destroys chips.
- `docs/plans/CASH_MODE_PLAYER_CHIP_SINKS.md` — player-side sinks. This doc's gap #2 (player vice) becomes entry #8 on that catalog.
- `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` — staking flow, the down-tier redistribution lever. Phase 5 (humans as stakers) is in flight.
- `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 3 — endgame economy intent, soft cap proposal. This doc operationalizes Part 3.
- `docs/plans/CASH_MODE_ECONOMY_SIM.md` — sim harness this doc's experiments will run against.
- `docs/vision/GAME_VISION.md` — the broader narrative frame that makes "tourists" and "vice" feel right thematically.

## Status

This is a thesis doc. Implementation order follows from gap priority:

1. **Gap 1** (vice as redistribution + tourist injection) is the closure mechanism — without it, the system isn't closed. Ship after AI vice (`CASH_MODE_AI_VICE_SPENDING` commits 1-2) is live so vice is producing real bank-pool deposits to recycle.
2. **Gap 3** (tourists modeled as a cohort) ships alongside gap 1 — they're the consumption side of the same loop.
3. **Gap 4** (rake redirect) is a small accounting change that lands when gap 1 lands.
4. **Gap 2** (player vice) is the last leg. The system can run closed with only AI-side vice if player numbers are small; player vice closes the symmetric case.

EXP 1-5 above run after gap 1+3+4 are wired, before gap 2 ships. They tell us whether the AI-side loop alone holds the closed-system claim — if it does, gap 2 is additive polish.
