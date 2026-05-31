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

## Next

§3 — the adaptive B-via-C bot, on the opponent-model harness (`exploit_bb100` /
`full_sim`). Likely default = RegPlus, with a maniac/over-aggression read that
*widens* call-downs to patch the over-fold-to-bluffs leak. That inverts the plan's
original "default CaseBotV2, switch to Reg+" because the data says RegPlus
dominates CaseBotV2 everywhere.
