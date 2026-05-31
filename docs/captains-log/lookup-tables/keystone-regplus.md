---
purpose: Narrative log of building RegPlus — the competent-opponent keystone that beats CaseBotV2
type: design
created: 2026-05-31
last_updated: 2026-05-31
---

# Keystone: RegPlus — a competent opponent that beats CaseBotV2

## The setup

Per `docs/plans/BUILD_A_BETTER_BOT.md`, every "better bot" claim from the prior
session was secretly measuring *fish-hunting*, because the eval pool is all fish.
You cannot prove a bot is robust without an opponent that punishes "play 95% and
call down." So the keystone, before any adaptive bot: build that opponent.

The plan nominated the existing `Reg` (`_reg_decision`) as a starting point but
flagged it as "passive postflop — needs initiative + correct folding." I started
by *measuring* it rather than trusting the label.

## The baseline was worse than advertised

The plain `Reg` doesn't just fail to punish CaseBotV2 — it gets demolished:

- Reg vs CaseBotV2 **HU: −88 bb/100** (all 3 seeds −84 to −94)
- Reg vs 5×CaseBotV2 **6max: −126 bb/100**
- CaseBotV2 vs 5×Reg **6max: +378 bb/100**

A "tight reg" losing 126 bb/100 to a 95%-VPIP calling station is backwards from
real poker, where a TAG destroys a station. That gap *is* the diagnosis. Three
compounding leaks:

1. **Under-extraction.** Reg value-bets 0.66 pot. A station calls an overbet — so
   0.66 leaves a fortune on the table. (This is the exact leak CaseBotV2 was built
   to fix; Reg never got the memo.)
2. **Pays off the overbets.** Facing CaseBotV2's 1.2-pot polarized overbet, Reg
   calls "medium" by raw pot odds — not seeing that a value-heavy bettor's big bet
   is strength. It pays off the nuts with second pair.
3. **Nits out preflop.** Reg folds ~65%, posting blinds and folding them at a table
   where money is made *in* pots with a range edge. A nit at a fish table earns
   nothing.

## The fix: extract like CaseBotV2, but add a fold button

The wrong move (proven five times in the dead-ends) is to *balance* CaseBotV2 —
tighten it, make it accurate. Balance under-exploits the leaky pool. The right
move is to **keep the extraction and add discipline**:

`RegPlus` (`_strategy_reg_plus`):
- **Extract:** overbet premium (1.1×) / strong (0.85×) when checked to — the pool
  calls anyway. Same edge that makes CaseBotV2 print vs fish.
- **Discipline (the new part):** facing a bet, fold everything but the nuts to a
  *polarized big bet* (`bet_over_pot ≥ 0.8`). Bluff-catch only small/thin bets by
  price. **Never bluff-barrel a caller** — give up air rather than spew.
- **Don't bleed:** iso the limpers for value preflop (3× BB), widen in position so
  it isn't folding the fish table away — but **tighten vs a raise** (a value-heavy
  opponent's raise is a real range; 3-bet premium, flat strong, fold the rest).

The asymmetry is the whole point: **when RegPlus overbets, the station pays; when
the station overbets, RegPlus folds.** CaseBotV2 calls down, so it pays off
RegPlus's value and never collects on its own. That one-way street is how a
competent player beats a station.

## The result

RegPlus flips every cell and is positive vs the entire field:

| Cell | RegPlus | plain Reg |
|---|---|---|
| HU vs CaseBotV2 | **+102** | −88 |
| 6max vs 5×CaseBotV2 | **+38** | −126 |
| HU vs jeff_clone | **+115** | — |
| 6max vs 5×jeff_clone | **+192** | — |
| HU vs punisher_clone | **+60** | — |
| 6max vs 5×punisher_clone | **+120** | — |
| Gauntlet worst cell (6max vs 5×TAG) | **+0.0** | — |
| Gauntlet mean (11 cells) | **+67** | — |

Inverse: CaseBotV2 vs a 5×RegPlus table = **−199 bb/100** (was +378 vs Reg).

The keystone success test ("a field/opponent that beats CaseBotV2, or holds it to
~0") is met with room to spare. And RegPlus is itself *robust*: worst cell is
break-even against a full table of competent TAGs, and it beats the most competent
clone we have (+120 vs 5 punishers).

## What this changes about the plan

The plan's §5 thesis — "off-the-shelf sophistication pulls toward balance, and
balance under-exploits a leaky pool" — is correct about *balance*. But RegPlus
shows discipline ≠ balance: a static bot can be **both** robust **and** a
fish-extractor, as long as it keeps the overbet-value and only adds a fold button.
That re-opens a question the plan had closed: maybe the adaptive bot's *default*
profile should be RegPlus (robust + extracts), not CaseBotV2 (which RegPlus beats
−199 at its own table).

## The honest caveat

RegPlus folds medium to overbets because *nothing in our eval overbet-bluffs*
(CaseBotV2 only overbets value; the punisher barrels but RegPlus's call-down
catches it). A thinking human who notices RegPlus over-folds to big bets would
overbet-bluff it. That residual leak is unmeasurable in the current pool and is
exactly what the adaptive aggression-read (§3) is for. So: RegPlus is a strong
static better-bot today AND the competent profile the adaptive bot becomes — but
"un-runnable-over by a human" still needs the read, and the read needs the
opponent-model harness (§4), which `measure_passivity` does not provide.

## I tried to break RegPlus — and couldn't. (The §3 thesis is refuted.)

Before building the §3 adaptive layer, I did the honest thing: built the opponent
that *should* exploit RegPlus's residual leak, the same way RegPlus was built to
exploit CaseBotV2. Two attackers (`_strategy_tricky_reg`, `_strategy_tricky_aggro`),
both designed to overbet-bluff the spot where RegPlus folds all-but-strong to a
polarized big bet (`bet_over_pot ≥ 0.8`):

1. **TrickyReg** — disciplined reg that polarizes big (overbet nuts + air) when
   checked to in position. Result: RegPlus +83 (6max) / +160 fair. No exploit —
   RegPlus holds the initiative (iso-raises), so TrickyReg rarely gets to overbet
   *into* it.
2. **TrickyAggro** — seizes the initiative (wide 3-bets) then overbet-barrels
   polarized on every street. Result: **RegPlus +213 (6max) / +290 fair**;
   TrickyAggro vs a RegPlus table = **−340**. A bloodbath for the attacker.

**Why no static bot can exploit the leak:** RegPlus *calls down* with strong+
(those don't fold to overbets — strong calls, premium raises). A polarized bettor's
air half runs straight into those snap-offs; RegPlus folds only its junk, which is
correctly behind the value half. So an opponent that overbets air *unconditionally*
just spews. The ONLY thing that beats the fold rule is overbetting air *exactly
when RegPlus's line has capped its range to medium/weak* — i.e., a **range-reading
opponent** (opponent modeling). No static rule-bot does that, and our eval cannot
produce it. **The leak is human-only and currently unmeasurable.**

### What this means for the plan

The plan's §1 thesis — "(B) robust requires (C) adaptive, because a single static
strategy under-extracts from fish" — is **refuted by RegPlus**:
- RegPlus is a *single static* strategy that is **both** robust (un-exploited by
  every opponent we can build: stations, maniacs, regs, overbet-bluffers,
  initiative-barrelers — all positive fair) **and** a fish-extractor (+115…+269
  fair vs the leaky fields). Discipline ≠ balance.
- The adaptive (C) mechanism was meant to defend the over-fold leak. There is **no
  measurable exploit to defend** — building the §3 classifier now would be building
  blind, the exact trap this whole effort keeps warning against ("the gate AND the
  opponent must match reality").

### Recommendation / fork

RegPlus is the better bot. The disciplined next steps are EITHER:
- **(a) Ship it** — promote RegPlus to a production bot type (currently only an eval
  archetype) and call target B met within our tooling; OR
- **(b) Build a range-reading attacker** (opponent-modeling, overbets only into a
  capped line) — the genuinely competent opponent that *could* exploit RegPlus —
  and only then build §3 to defend it. This is a real research lift (the opponent
  must model RegPlus's range from its line), not a quick classifier.

§3 as originally scoped (an outcome-based maniac/aggression read) is **not
justified by current evidence** and is parked. The honest headline: we set out to
build a competent opponent so we could build a robust bot, and the competent
opponent we built (RegPlus) turned out to already *be* the robust bot.
