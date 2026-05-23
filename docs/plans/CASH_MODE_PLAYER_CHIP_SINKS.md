---
purpose: Single source of truth for the endgame chip-sink design space — collects every player-side sink referenced across the cash-mode docs so the prioritization conversation has one place to live.
type: design
created: 2026-05-23
last_updated: 2026-05-23
---

# Cash Mode — Player Chip Sinks

> **Why this exists:** Chip-sink ideas have been accumulating across four+ cash-mode docs without a single home, and none of them are built yet. `CASH_MODE_ECONOMY.md` calls out the player-side inflation gap as one of the four open issues, then defers to *"Part 3 of CASH_MODE_AND_RELATIONSHIPS.md"* — which lists three sinks in seven lines and moves on. `CASH_MODE_BACKING_SYSTEM_HANDOFF.md` mentions sinks in five separate places as the eventual "structural fix" for the wealthy-player-has-no-cap problem. This doc inventories what's been proposed, what's locked, and what's open — so when we're ready to prioritize, the design space is visible without spelunking.

## The structural problem

A player who climbs the stakes ladder ($2 → $10 → $50 → $200 → $1000) and grinds successfully eventually **outgrows the ladder**. Wins keep flowing in, nothing flows out, and the carry cap from staking (`10 × min_buy_in @ tier` per `CASH_MODE_BACKING_SYSTEM_HANDOFF.md`) becomes economically meaningless at high bankroll.

Three symptoms of this problem already noted in shipped docs:

1. **"Wealthy player owes $5k is a rounding error."** Carries don't threaten high-bankroll players mechanically (`CASH_MODE_BACKING_SYSTEM_HANDOFF.md:663`).
2. **Bankroll has no destination once stakes are capped.** Soft cap on the ladder at $1000 is by design — "past that bankroll, money is *only* for sinks" (`CASH_MODE_AND_RELATIONSHIPS.md:694`).
3. **Central bank is unbounded for v1**, with a note to revisit if endgame sinks don't pull chips back (`CASH_MODE_BACKING_SYSTEM_HANDOFF.md:647`, locked decision #10).

None of the proposed sinks are shipped. v1 explicitly ships none of this. Captured here so prioritization has structure when we get to it.

## Catalog of proposed sinks

Each entry: source doc, status, what it is, what it costs, what it gives.

### 1. Staking AI players ✅ **IN FLIGHT**

**Source:** `CASH_MODE_BACKING_SYSTEM_HANDOFF.md` (Phase 5)
**Status:** Phase 1+2 of the backing system shipped on `phase-1`; Phase 5 (humans as stakers) is the in-scope path that turns this into a real player sink.

**What it is.** Wealthy players offer stakes to busted or under-rolled AIs. Player puts up the buy-in chips, takes a configurable cut of upside, eats 100% of losses if the AI busts. Durable contract — emits `STAKE_OFFERED` / `STAKE_REPAID` / `STAKE_DEFAULTED` / `STAKE_FORGIVEN` `EconomyEvent`s, with `RelationshipEvent` side effects (`TRUST_EXTENDED`, `BETRAYAL`).

**What it costs the player.** Buy-in chips up-front; potential 100% loss on AI bust.
**What it gives.** Configurable share of AI winnings + relationship deepening with the staked personality.
**Why it's a sink.** Bankroll-deflation risk in exchange for upside — net negative for the player over a representative population of AI stakees.

**Locked design notes** from the backing handoff:
- House stake economics: unbounded central bank for v1 (locked decision #10) — sinks are the path to making this bounded later.
- Player-created custom personalities (see #4 below) are auto-staked by the creator; counted against creator bankroll the same as any AI stake.

### 2. Private home games

**Source:** `CASH_MODE_AND_RELATIONSHIPS.md` Part 3 (line 679)
**Status:** Designed in 1 sentence; no further spec.

**What it is.** Player owns a table with a custom invite list. Durable ownership state stored per player.
**What it costs the player.** Per-session run costs (table maintenance? hosting fee per AI invited?).
**What it gives.** Curated lobby experience — pick exactly who you want to face. Likely also a status/identity component (your table, your name on it).
**Why it's a sink.** Recurring chip drain proportional to play time at the owned table.

**Open design questions:**
- Flat per-session fee or per-AI-invited fee?
- Can other humans join your private table? If yes, does the host get a rake cut?
- Persistent table layout (always there) or set-up-per-session?
- Branding/cosmetics tied to ownership (rename the table, custom felt color, etc.)?

### 3. Character unlocks

**Source:** `CASH_MODE_AND_RELATIONSHIPS.md` Part 3 (line 680)
**Status:** Designed in 1 sentence; no further spec.

**What it is.** Durable availability flag per `(personality_id, player_id)`. Cost paid in chips at unlock time.
**What it costs the player.** One-time chip cost per personality.
**What it gives.** Access to a personality otherwise gated (premium / hidden / themed). Could also stack with #4 (player-created) — you "unlock" your own creations the same way.
**Why it's a sink.** Pure one-time burn; chips destroyed (or routed to a creator if the personality was player-created).

**Open design questions:**
- Pricing tier per personality (rare → expensive)? Or flat?
- Subset of the existing pool gated, vs gating new content as it's added?
- Is unlock per-server-instance, per-account, or some other scope?

### 4. Player-created custom personalities ✅ **LOCKED, POST-PHASE-5**

**Source:** `CASH_MODE_BACKING_SYSTEM_HANDOFF.md` locked decision #9
**Status:** Design locked, implementation deferred to post-Phase-5.

**What it is.** Player creates a custom personality via the existing personality manager. Auto-seeded into the AI pool. Counts against the creator's bankroll the same as staking any other AI (uses the existing staking machinery — this is a sink because the player auto-stakes their creation).

**What it costs the player.** Buy-in for the new personality (per the standard staking model).
**What it gives.** A personality that starts with a higher-affinity bond toward the creator (representing "I created you"), then evolves naturally. Pride of ownership + the chance to deploy a character tuned to your taste.

**Locked design notes:**
- Player-created personalities are **private to the server instance** (host's decision, not per-user).
- No special pricing — the staking machinery is the sink mechanism. The creation itself is "free"; the deployment costs the same as any stake.

### 5. Clone yourself (late-game unlock)

**Source:** `docs/vision/FEATURE_IDEAS.md` (added 2026-05-23)
**Status:** Vision / brainstorm. Cloning infrastructure (`poker/human_clone.py`) is shipped; economy mechanic is open.

**What it is.** Player crosses a cash / hands threshold, unlocks the ability to deploy a clone of themselves built from their `hand_history`. Clone plays autonomously; winnings flow back to the user's bankroll (cut / cap TBD).
**What it costs the player.** Unlock fee + likely deployment fee per session.
**What it gives.** Passive bankroll grind while offline + identity content (other players sit with your clone). Educational mirror potential ("your clone folded that 73% of the time when it should've called").
**Why it's a sink (potentially).** Deployment fee burns chips; winnings cap or upkeep cost keeps it from being pure passive income.

**Open design questions** (from FEATURE_IDEAS.md):
- Unlock gate, earnings split, loss handling, naming, freeze-vs-evolve update frequency, anti-abuse caps.

### 6. Hosting tables

**Source:** Mentioned in `CASH_MODE_BACKING_SYSTEM_HANDOFF.md` lines 623, 663, 727 as a future sink. No standalone design.
**Status:** Named but undesigned.

Likely closely related to #2 (private home games) — possibly the same feature, possibly distinct in some way (e.g., hosting a tournament vs running a cash table). Worth merging or distinguishing when prioritized.

### 7. Appearance fees

**Source:** Mentioned in `CASH_MODE_BACKING_SYSTEM_HANDOFF.md` line 623 as a future sink. No design.
**Status:** Named only.

Likely the inverse of staking — pay a celebrity AI to show up at your private table. Could be a one-time chip cost per session per AI. No further detail in any doc.

---

## Adjacent: AI-side sinks (not player sinks but relevant)

### AI vice spending ✅ **DESIGNED, NOT BUILT**

**Source:** `docs/plans/CASH_MODE_AI_VICE_SPENDING.md`
**Status:** Full design doc; not yet implemented.

A chip sink that drains AI bankrolls rather than player bankrolls — wealth + pressure triggers an AI vice (gambling, drinking, etc.) with LLM-generated flavor narration. Doubles as a psychology-regulation mechanic.

**Why it's listed here:** the player-side inflation problem is one of TWO inflation gaps the economy has. AI vice spending closes the AI side; player sinks close the player side. Both are required for a bounded long-term economy. The mechanics are independent but the design philosophy (durable contracts emitting EconomyEvents, narrative-aware, opt-out where it makes sense) carries over.

---

## Cross-cutting design questions

**Q1. Which sinks are the priority?**

The current implicit priority is **staking (#1)** because it's already in flight under the backing system. After that, the queue is unclear. Considerations:

- **#2/#6 (private/hosting tables)** are likely the biggest UI/infrastructure lift but also the most "feel-like-a-game" sink.
- **#3 (character unlocks)** is probably the simplest to ship — a flag + a chip price + a check at lobby spawn time.
- **#5 (clone yourself)** has the most narrative pop but depends on staking infrastructure being mature.
- **#4 (player-created personalities)** is locked to post-Phase-5; not on the near-term path.

**Q2. Should sinks destroy chips or transfer them?**

The cash mode uses a credit-debit ledger (`chip_ledger_entries`). Some sinks naturally destroy chips (unlock fees → chips just gone). Others transfer between roles (staking is intra-game between roles; appearance fees are intra-game between player and AI).

Implication: per-sink decision in the design pass. Pure destruction is cleanest economically but feels less rich than seeing chips move into someone else's bankroll. Mixed-mode probably right.

**Q3. How do sinks interact with the soft stake cap?**

`CASH_MODE_AND_RELATIONSHIPS.md:692` proposes capping the stakes ladder at $1000 — past that, money is *only* for sinks. If sinks under-deliver (don't pull enough chips), the cap forces a feel of pointlessness for high-bankroll players. If sinks over-deliver (drain too fast), top-of-ladder play stops being aspirational.

Needs calibration once 2+ sinks are live and there's enough player-side hand history to model "what fraction of incoming chips do sinks pull in steady state."

**Q4. Are sinks tied to relationships?**

Several proposed sinks (staking, custom personalities, clone-of-self) are inherently relational — they create or deepen bonds with AI personalities. Others (unlocks, hosting) are more mechanical. The doc note in `CASH_MODE_AND_RELATIONSHIPS.md:670-671` calls the endgame economy "its own product slice, not an extension of the relationship system" — but in practice, the most narratively-rich sinks ride on relationship machinery.

Worth being explicit: which sinks are **relational** (lean on `RelationshipState`, emit `RelationshipEvent`s) vs **transactional** (pure chip flow). This determines reuse vs new infrastructure.

**Q5. Sink unlock gating model?**

Two basic shapes:
- **Cash gate**: bankroll ≥ X → sink available
- **Achievement gate**: completed-condition → sink available (e.g., "defeat all $1000-tier celebrities" unlocks hosting)

The achievement shape adds a non-chip-progression layer that doubles as content — `CASH_MODE_AND_RELATIONSHIPS.md:684-690` already sketches affinity completion / heads-up gauntlet / hand-of-fame as progression candidates. Could pair achievement-based gating with each sink to give the unlock its own moment.

---

## Status summary

| Sink | Designed | Built | Priority signal |
|---|---|---|---|
| 1. Staking AI players | ✅ (in backing handoff) | Phase 1+2 shipped; Phase 5 pending | In flight |
| 2. Private home games | One-sentence | No | Open |
| 3. Character unlocks | One-sentence | No | Open — likely cheapest first ship |
| 4. Player-created personalities | ✅ locked | No (post-Phase-5) | Locked, deferred |
| 5. Clone yourself | Brainstorm | No (infra shipped) | Open — depends on staking maturity |
| 6. Hosting tables | Named only | No | Open — possibly same as #2 |
| 7. Appearance fees | Named only | No | Open |
| _(AI vice — adjacent, not player sink)_ | ✅ full design | No | Independent track |

## Source docs

- `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 3 — original "Chip sinks" section (line 676), soft stake cap proposal, non-chip progression sketches
- `docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md` — Phase 5 (staking), locked decision #9 (player-created personalities), references to hosting tables / appearance fees, the wealthy-player problem statement
- `docs/technical/CASH_MODE_ECONOMY.md` — player-side inflation gap as one of the four open issues; points at `CASH_MODE_AND_RELATIONSHIPS.md` Part 3 for the fix
- `docs/plans/CASH_MODE_AI_VICE_SPENDING.md` — AI-side sink (adjacent)
- `docs/vision/FEATURE_IDEAS.md` — "Clone Yourself (Late-Game Unlock)" section
