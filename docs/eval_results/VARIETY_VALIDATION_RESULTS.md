---
purpose: Results of the variety/fish validation sweeps (short-stack safety, depth-drain curve, aggression priced vs calling fields) backing the deploy decision
type: reference
created: 2026-05-29
last_updated: 2026-05-30
---

# Variety + fish validation — results

Backs `docs/plans/VARIETY_VALIDATION_AND_DEPLOY_HANDOFF.md`. Driver:
`experiments/variety_eval.py` (sweeps A/B/D; reuses `measure_passivity`'s
per-seed worker so numbers are byte-identical to a hand `measure_passivity`
run). Heavy numbers below were produced on the Hetzner `poker-bot-optimization`
box (ccx63, 48 dedicated cores; bit-identical to local) and torn down after.

**Headline:** the shipped precedence flip (width-tier tables at all depths) is
**safe at short stacks** — no archetype spews shallow. Drain is **depth-capped**
(deeper bottom buy-in is the biggest economy-cycling lever). And the feared "aggression is only +EV vs foldy fields" caveat
is **refuted** — aggression earns *more* vs fields that call (they pay off
value); the punished trait is **passivity**, exposed by a competent *folder*,
not a caller (see B). The **punisher test** (P) closes the loop: aggression is
+EV even vs a competent folder-and-barreler, and the `over_bluff` spew lever is
**near-inert** (it can't fire on a passive base) — so there is no measurable
over-bluffing penalty anywhere; `position_blind` is the fish's real EV leak.

## A — Short-stack validation (PASS)

_Field: foldy Baseline×5. Hetzner 3000h × 8 seeds. Columns: VPIP / PFR / jam% / avgOpen(bb) / AF / bb/100._

| archetype | depth | VPIP | PFR | jam% | avgOpen | AF | bb/100 |
|---|---|---|---|---|---|---|---|
| **Nit** | 100bb | 15 | 9 | 0.1 | 4.3 | 0.29 | −25.7 |
| | 50bb | 14 | 10 | 0.1 | 3.9 | 0.35 | −9.7 |
| | 25bb | 14 | 10 | 0.1 | 3.6 | 0.35 | −0.5 |
| **Rock** | 100bb | 19 | 12 | 0.1 | 4.5 | 0.30 | −27.3 |
| | 50bb | 17 | 12 | 0.1 | 4.0 | 0.35 | −6.2 |
| | 25bb | 18 | 13 | 0.3 | 3.7 | 0.33 | +3.4 |
| **TAG** | 100bb | 23 | 19 | 0.1 | 4.6 | 0.62 | −15.5 |
| | 50bb | 18 | 16 | 0.5 | 4.5 | 0.72 | +0.6 |
| | 25bb | 14 | 13 | 1.3 | 2.5 | 0.86 | +0.7 |
| **LAG** | 100bb | 37 | 30 | 0.7 | 6.0 | 0.77 | +14.8 |
| | 50bb | 35 | 29 | 1.0 | 5.6 | 0.83 | +23.8 |
| | 25bb | 36 | 30 | 1.9 | 4.8 | 0.99 | +27.1 |
| **Calling Station** | 100bb | 44 | 15 | 0.1 | 4.9 | 0.25 | −72.8 |
| | 50bb | 40 | 16 | 0.2 | 4.4 | 0.29 | −9.3 |
| | 25bb | 41 | 17 | 1.2 | 3.8 | 0.29 | −12.3 |
| **Maniac** | 100bb | 55 | 47 | 1.6 | 6.2 | 1.26 | +52.5 |
| | 50bb | 53 | 46 | 1.9 | 5.9 | 1.30 | +50.1 |
| | 25bb | 56 | 50 | 4.3 | 4.6 | 1.31 | +28.8 |

Red-flag scan: **none**. Worst 25bb jam% is Maniac 4.3% (the rest ≤1.9%) —
nowhere near blind-shoving. (Near-zero bb/100 values at 25/50bb show per-seed
sign noise, as expected when the number is ~0; the structural metrics
VPIP/PFR/jam are rock-stable across all 8 seeds.)

**Verdict:** PASS — no fix needed. Why the flip is safe even though width
tables are depth-agnostic: the **range width** comes from the 100bb table, but
the **sizing + jam layer remains depth-aware** — avg open size shrinks with
depth across every archetype (e.g. Maniac ~6.2bb→~4.5bb, TAG ~4.5→~2.5bb), and
jam% stays low at 25bb (worst ≈ Maniac ~4–6%, nowhere near blind-shoving). A
loose 100bb range played at 25bb just means more limps/small-opens, not shoves.
Archetype identity (VPIP spread Nit ~15 → Maniac ~57) holds at all depths; the
aggressive archetypes sensibly tighten shallow.

## D — Buy-in depth diff (drain is depth-capped)

_Fish hero vs TAG-grinder×5. Hetzner 3000h × 8 seeds. bb/100, negative = fish loses._

| archetype | 40bb | 60bb | 80bb | 100bb |
|---|---|---|---|---|
| Calling Station | −7.3 | −11.4 | −74.8 | −91.3 |
| WeakFish | −38.2 | −59.8 | −126.1 | −121.5 |

The drain is **depth-capped and accelerates past ~60bb**: a Calling Station
bleeds ~12.5× faster at 100bb than at 40bb (−7 → −91), with the cliff between
60bb (−11) and 80bb (−75). WeakFish bleeds even at 40bb (−38) and saturates
around 80–100bb (~−120). _(Local 1500h×3seed pass agreed in shape: Station
−9.6→−68, ~7×.)_

**Recommendation:** the bottom
buy-in depth is the single biggest cycling lever — a shallow $2 (≈40bb) caps
the fish drain to a slow trickle, a deep one bleeds them ~7× faster. Keep $2
shallow + weak_fish for a sustainable trickle; reach for a deeper bottom buy-in
(or per-tier `MAX_BUY_IN_BB` bump in `cash_mode/stakes_ladder.py`) only if the
economy needs faster recycling. Product/economy call for Jeff — numbers above.

## B — Aggression priced across fields (the honest cost)

_Heroes: Maniac, LAG, StationPBlind (isolates position_blind), Calling Station.
Fields: FOLDY=Baseline×5 (over-folds), JEFF=Jeff_clone×5 (realistic calls-down
human, WtSD 0.59), NEVERFOLD=CallStation×5 (always_call). Hetzner 2000h × 8
seeds. bb/100._

| hero | depth | vs FOLDY | vs JEFF | vs NEVERFOLD |
|---|---|---|---|---|
| **Maniac** | 40bb | +37.2 | +218.1 | +840.0 |
| | 100bb | +72.3 | +275.3 | +1283.6 |
| **LAG** | 40bb | +25.4 | +133.8 | +511.3 |
| | 100bb | +23.1 | +169.6 | +796.2 |
| **StationPBlind** | 40bb | −16.6 | +49.2 | +291.7 |
| | 100bb | −53.1 | +73.2 | +391.2 |
| **Calling Station** | 40bb | −3.1 | +39.3 | +220.7 |
| | 100bb | −61.0 | +70.4 | +336.4 |

### The premise was backwards — and that's the finding

The handoff feared "foldy fields make aggression look +EV (overstated)." The
data says the **opposite**: every hero earns **far more** vs the calling fields
than vs the foldy field. Maniac +37 (foldy) → +218 (Jeff) → **+840** (never-fold)
at 40bb. The foldy field *understates* aggression's edge.

Why: a field that **calls** is a field that **pays off value** and **can't win
without showdown** — it's the *easiest* opponent, not the punishing one. The
"bluff gets called" cost is real but dwarfed by the "value gets paid" benefit.
The original premise conflated **bluff-EV** (yes, higher vs callers) with
**total-EV** (much higher vs callers). A pure caller is a **donor, not a
punisher.**

**The punishing direction is a competent FOLDER, not a caller.** vs the foldy
field (the closest proxy to a disciplined opponent who folds air and doesn't pay
off), the *passive* heroes bleed and bleed harder with depth — Calling Station
−3→−61, StationPBlind −17→−53 (40→100bb) — while the *aggressive* heroes stay
positive (Maniac +37→+72, LAG +25→+23). So the real skill gradient: **passivity
is the punished trait; aggression is robustly +EV** and only its *magnitude*
(not its sign) depends on how much the field pays off.

### position_blind isolation (StationPBlind − Calling Station vs FOLDY)

- 40bb: −16.6 − (−3.1) = **−13.5** → position_blind makes the fish lose MORE when shallow (more drain — good for $2).
- 100bb: −53.1 − (−61.0) = **+7.9** → position_blind makes the fish lose LESS when deep (less drain — bad if applied to deep fish).

This **validates the existing $2-only stake gate** for `position_blind`
(handoff consideration #4): it's a shallow-stack drain lever; on deep fish it
would slightly *help* them. Keep it stake-gated.

## P — The punisher test: pricing over-bluff vs a competent folder+barreler

_Closes B's one open thread. PUNISHER=Punisher_clone×5 (folds correctly AND
barrels air — the disciplined reg). FOLDY=Baseline×5 for contrast. Added a clean
`over_bluff`-only isolation (`calling_station_overbluff` profile / `StationOverBluff`
archetype, over_bluff 0.55 = the weak_fish strength) alongside the existing
position_blind isolation. Hetzner 2000h × 8 seeds. bb/100._

| hero | depth | vs PUNISHER | vs FOLDY |
|---|---|---|---|
| Calling Station | 40 / 100bb | +12.9 / +28.3 | −3.1 / −61.0 |
| **StationOverBluff** | 40 / 100bb | +13.0 / +26.7 | −2.2 / −62.5 |
| StationPBlind | 40 / 100bb | +0.3 / +20.0 | −16.6 / −53.1 |
| WeakFish | 40 / 100bb | −0.7 / +5.0 | −44.0 / −94.5 |
| LAG | 40 / 100bb | +43.6 / +70.1 | +25.4 / +23.1 |
| Maniac | 40 / 100bb | +106.9 / +98.7 | +37.2 / +72.3 |

**Lever isolation vs PUNISHER** (hero − Calling Station):

| lever | 40bb | 100bb |
|---|---|---|
| over_bluff | +0.1 | −1.7 |
| position_blind | −12.6 | −8.4 |

### There is no measurable over-bluffing penalty — because the lever barely fires

`over_bluff` is **inert** on a passive base: StationOverBluff ≈ Calling Station
vs the punisher (Δ +0.1 / −1.7) **and** vs the over-folder (−2.2/−62.5 ≈
baseline). The handler only fires on **unopened + air + turn/river** (hero must
have the betting lead with a busted hand) — a spot a passive caller rarely
reaches, so at 16k hands it still moves EV by ~0. So the "cost of over-bluffing"
**can't be priced on a station base**: the base doesn't bluff much even with the
lever maxed. The archetypes that *do* bluff a lot take the lead via
`aggression_scale` (Maniac/LAG), and they are **+EV even vs the punisher**
(Maniac +107/+99, LAG +44/+70). Net: **no hidden aggression/over-bluff cost
anywhere in the system; passivity stays the punished trait.**

`position_blind` again prices as the real (modest) drain lever: −12.6/−8.4 vs the
punisher, consistent with its B isolation. It, not over_bluff, is what makes the
weak fish bleed.

**Why even the punisher looks beatable:** every hero is ~break-even-to-+EV vs it,
because the punisher *barrels air* — and a never-folding station simply **calls
its bluffs down** (Calling Station +28 @100bb). So the punisher cleanly prices
the cost of **over-FOLDING** (it stabs your air) but is itself exploited by
stations, making it a *weak* test of over-CALLING/over-bluffing. The clone set
has no truly balanced (GTO) opponent — that's the one thing none of these fields
can price. But for the question that mattered — "does aggression secretly bleed
vs a competent opponent?" — the answer across foldy, calling, AND punisher fields
is a consistent **no**.

### Validation: does over_bluff fire on an aggressive base? (yes, but modestly)

Control for the "inert on a passive base" finding. Added `maniac_overbluff`
profile / `ManiacOverBluff` archetype (maniac base + over_bluff 0.55) and ran it
head-to-head vs plain Maniac, same foldy field, same seeds (1500h×3):

| Maniac base | air unopened bet/raise | bb/100 |
|---|---|---|
| over_bluff OFF | 45% (n=642) | +56.0 |
| over_bluff ON (0.55) | **48%** (n=642) | +51.0 |

**It fires** — air-bet% moves 45→48% on the aggressive base, vs *byte-identical*
on the station. So the lever is correctly gated, not broken: only a player who
takes the betting lead reaches the unopened-air-turn/river spot. **But the effect
is small even on a maniac** (+3pts air, EV flat within noise): the aggressive base
already bluffs near the cap (`max_per_action_shift` 0.35), so over_bluff has
little headroom. Conclusion: over_bluff is a **flavor nudge on aggressive
archetypes, not a big EV/drain lever anywhere** — and it cannot register on a
passive fish at all.

### Design takeaways

- `over_bluff` is the wrong leak for a *passive* fish (it can't reach the spot) and
  only a **modest flavor tell** on an aggressive base. To get a visibly-spewing
  fish you'd build a spewy-*aggressive* base (looser table + aggression so it
  takes the lead) — and even then, for a STRONG tell, raise strength / widen the
  gate / add the sizing tell. As-is on $2 weak_fish it's cosmetic (invisible).
- `position_blind` is the fish's real EV leak; keep it $2-stake-gated (shallow).
- Aggression (`maniac`/`lag`) is robustly +EV vs every field tested — the variety
  is safe to ship; the skill gradient lives entirely on the passive end.

## Spewy aggressive fish — can't be built on the tiered engine (finding)

Attempt to build the frat-bro spewer: a `spewy_fish` profile (loose table +
`over_bluff` 0.8 + `sticky` 0.5, cap 0.45) + `SpewyFish` sim archetype. It
**spews** as designed (VPIP 58 / PFR 49 / AF ~1.0) but it is a **universal
winner**, not a fish:

| SpewyFish vs… | bb/100 | air-bet% (unopened) |
|---|---|---|
| TAG grinders | **+67** | 43% |
| foldy (Baseline) | +48 | 42% |
| always-call | **+1426** | **14%** |
| passive fish (Calling Station) | +136 | 29% |

The tell is the **air-bet% column**: the engine's EV / math-blocking floor
**suppresses the bluffs exactly where they'd be called** (43% → 14% vs the pure
caller) and just value-bets the donors. So a chart-based aggressive bot
value-bets vs callers AND bluffs vs folders — it wins both ways. **Aggression is
EV-gated, so an aggressive bot on the tiered engine structurally cannot be a
losing fish.** (Contrast: a passive fish loses because *passivity* — checking
value, paying off — is genuinely −EV and the engine faithfully executes it. The
asymmetry: the engine lets you play too passively, but not too aggressively.)

For reference, the **rule-based** `Fish-Spew` bot (no EV floor → unconditional
spew) does lose to TAG (−54 bb/100) — i.e. the *losing* spewer needs the rule
path, not the tiered engine. (Caveat: the tiered passivity harness can't read a
rule bot's actions faithfully — that −54's VPIP/AF readout is an artifact — so
treat it as suggestive.)

**Implication / open decision:** a "spewy aggressive fish that drains" isn't
achievable on the unified tiered engine. Three paths: (A) drop it — keep fish
passive (the engine fights aggression-as-leak); (B) deploy the tiered SpewyFish
as a *winning* grinder-PUNISHER (it beats TAG grinders — a natural counter to the
grinder-hoard problem — but it's not a fish and would accumulate chips); (C)
re-introduce the rule-based spew path for aggression-leak personas, or add a
per-profile "bypass EV floor" flag (invasive). `spewy_fish`/`SpewyFish` are kept
as **measurement-only** (NOT wired into `build_fish_controller`) pending that
call. The over_bluff fish leaks (`spews_bluffs`, `spite_raises_when_losing`)
remain near-inert on the passive station base today.

## Is aggression counterable? (field-tuning, not an engine flaw)

The spewy-fish finding (aggression is +EV everywhere it was tested) raised the
worry: does the engine fail to punish aggressive/spewy play, so "just bet"
dominates? Tested directly.

**A lone Maniac beats a FIELD of tight bots** (1 Maniac + 5 X, bb/100 to the
hero X, vs Maniac×5):

| X (hero) vs Maniac×5 | bb/100 | X's VPIP | X's fold-to-bet |
|---|---|---|---|
| TAG | −44 | 15% | 35% |
| Baseline | −37 | 16% | 40% |
| Nit | −50 | 10% | 33% |
| Calling Station | −29 | 35% | 35% |
| Defender (balanced) | −43 | 16% | 36% |

Every grinder loses. The mechanism is **preflop**: they play 10–16% VPIP vs the
maniac and **fold their blinds to its relentless steals**. So yes — against a
*passive multiway field*, relentless aggression is +EV. (My `Defender` archetype
— call-down + trap levers — FAILED identically, because those levers are
postflop; the leak is preflop blind-theft, which they don't address.)

**But heads-up, the SAME bots BEAT the maniac:**

| X vs Maniac (heads-up) | bb/100 | X's VPIP HU |
|---|---|---|
| Baseline | +8 | 53% |
| TAG | +17 | 53% |
| Defender | +8 | 53% |

Isolated, the competent bot **widens to 53% VPIP** (the HU chart defends
correctly) and the maniac's edge **evaporates**. So the engine's decision layer
is fully capable of beating aggression — it defends well when it's the only one
who has to.

**Conclusion: not an engine flaw — a field-composition + static-strategy issue.**
The maniac's +57 is **blind-theft from a passive multiway field** (exactly real
poker — a maniac prints at a table of nits). The personalities play a **fixed
tight range regardless of how loose the table is**, so they don't widen their
defense when one opponent is a maniac. The counter is the same as real poker:
defend wider, 3-bet/raise back, isolate — which the engine demonstrably *can* do
(HU proves it), it just isn't *adapting* in multiway.

**Two fixes, both already partly in the codebase:**
1. **Field variety (just shipped).** A table with LAGs/Maniacs/aggressive AIs
   isn't a passive steal-target — the vulnerable case is an ALL-passive field.
   Variety dilutes the problem on its own.
2. **Adaptive defense** (`hyper_aggressive` exploitation counter: detect high
   aggression → widen calls/defense, 3-bet back) — **TESTED, and it does NOT
   work.** `exploit_bb100` (TAG and Nit, ON vs OFF twin, **Maniac×4 backdrop**,
   opponent-model ON, 8000h × 8 seeds):

   | hero | ON bb/100 | OFF bb/100 | paired edge | 95% CI | verdict |
   |---|---|---|---|---|---|
   | TAG | −13.3 | −4.0 | **−9.3** | [−22.3, +3.7] | inconclusive/null |
   | Nit | −9.7 | −7.8 | **−1.9** | [−12.2, +8.3] | null |

   The counter flips an action on 7–11% of hands but moves **no net EV** (both CIs
   span 0; per-seed signs disagree). So the engine's intended adaptive defense is
   **inert vs aggression** — re-confirming EXP_004/005. _(A 2-seed smoke showed a
   misleading +36 paired edge; it was noise — the per-seed-sign-disagreement trap.
   8 seeds killed it.)_

### WHY the counter is inert (code-grounded)

`exploitation.py:13-15` — the `hyper_aggressive` rule by design **(a) tightens
your own opens** and **(b) widens calls vs all-ins / big bets.** But the maniac's
+57 comes from **min-raising your blinds and you folding** — neither an all-in nor
your own open. The rule has **no blind/steal-defense** component, and the code
flags it explicitly (`exploitation.py:121`): *"Placeholder proxy until
`fold_to_open` lands — see PHASE_8_1 doc for the proper fix."* So the
"defend-blinds-wider-vs-a-loose-opener" rule is **unimplemented** — the counter
defends the wrong street, which is why it flips ~10% of actions but moves no EV.
(HU it doesn't matter: the wide HU chart already defends blinds, which is why the
bots beat the maniac HU.)

### Archetype-shift as a counter (Jeff's idea) — tested

Does shifting to a different archetype vs the maniac help more than the logit
offset? Each hero vs Maniac×5:

| hero | bb/100 | VPIP | note |
|---|---|---|---|
| Nit / Rock | −50 | 10–13% | tightest = worst |
| LAG | −49 | 29% | a *moderate* widen does NOT help |
| TAG / Defender / Baseline | −37…−44 | 15–16% | |
| Calling Station | −29 | 35% | looser folds fewer blinds → least-bad of the losers |
| **Maniac (mirror)** | **+0.2** | 43% | **only a full maniac breaks even** |

The pattern is sharp and true to real poker: at a table of maniacs you either
**match the aggression fully** (the maniac mirror breaks even — fights back,
defends wide, 3-bets light) or **get run over** — and a half-measure (LAG) is the
worst of both (tightens to 29% under the onslaught, still folds blinds). So
"shift archetype wider" works, but only at the **full-maniac** extreme, which
brings coin-flip variance — not a clean fix.

### So: the levers, ranked

1. **Field variety (shipped) — the reliable one.** A maniac vs a passive field is
   +57; a maniac in a field that *includes* aggressive players is held to ~0 (the
   mirror result). So a casino/career table that isn't all-passive caps a lone
   maniac's edge automatically. Don't let tables be all-passive.
2. **Implement the missing blind-defense rule** (`fold_to_open` / PHASE_8_1): defend
   blinds wider vs a detected high-PFR opener. This is the **surgical** version of
   "shift wider" — it plugs the exact leak (preflop steals) without the
   full-maniac variance. The codebase already scoped it; it's just not built.
3. **The existing `hyper_aggressive` logit counter: don't rely on it** — it's inert
   here (defends the wrong street).

**For the game:** a human maniac at a passive AI table *will* print, and the
current adaptive counter won't stop them. Fixes #1 (already done) and #2 (a
scoped, real project) are the answers; #3 is a dead end as-is.

### The counter DOES exist in code — CaseBot proves it (the resolution)

The decisive test: **CaseBot** (`_strategy_case_based`) **demolishes the maniac
field — +175 bb/100 vs Maniac×5**, where every tiered archetype LOST −44…−50.
(Not "any rule bot": GTO-Lite gets crushed −480 vs the same field.) CaseBot is
exactly the two behaviors the tiered bots lack:
- **Wide preflop defense** — it calls preflop whenever equity clears the pot
  odds, so it plays ~everything and **never gets blind-stolen** (the maniac's
  whole edge).
- **Adaptive call-down vs aggression** — *"Calls lighter vs aggressive opponents
  (aggression > 2.0)"*, `call_adjust = −0.08` (`rule_strategies.py:474/507`) →
  it **catches the over-bluffs** instead of folding.

So the engine *can* punish relentless aggression; the tiered **personalities**
just don't implement the counter (they over-fold preflop + don't adapt).

**Caveat — CaseBot is a universal winner, not an aggression specialist:** TAG
+60, Nit +60, Station +340, Maniac +175, mixed +127/+264. It steals blinds from
tight fields (it's aggressive too) AND calls down loose ones, so it beats every
static archetype. Honest read: **CaseBot is just a stronger adaptive bot than our
personality caricatures** (which carry deliberate leaks) — competent adaptive
play beats static caricatures, which also implies a *skilled human* beats the AI
field (probably fine — the AIs are characters; the challenge is variety +
psychology, not GTO-resistance).

**Updated lever list:** in addition to #1 (variety) and #2 (blind-defense rule),
the cleanest options are: **(2b)** port CaseBot's call-down + wide-defense into
some tiered archetypes (tuned so they punish maniacs without becoming universally
dominant), or **(3b)** just **seat `casebot`-type players** in some tables as the
built-in maniac-punisher — it already exists as a bot type.

**Bottom line for the worry:** NOT an engine flaw. The engine can punish
aggression (CaseBot +175 vs the maniac); the static personality archetypes just
don't, because they over-fold preflop and don't adapt.

## E — Recurring eval: ON-DEMAND (no schedule, per Jeff 2026-05-29)

No cron/routine. The sweeps are DB-free and bit-identical local↔box, so fresh
numbers are one command away whenever wanted (below). A standing schedule was
declined to avoid Hetzner teardown risk / idle billing.

## How to reproduce

```bash
# local dev-first pass (sweeps: A short-stack, B pricing, D depth, P punisher)
docker compose exec -T backend python -m experiments.variety_eval all --hands 1500 --seeds 42,3042,6042
docker compose exec -T backend python -m experiments.variety_eval P    --hands 1500 --seeds 42,3042,6042

# Hetzner heavy pass (see docs/EVAL_RUNNER.md; poker-bot-optimization only, tear down after)
ssh root@<box> 'cd /root/poker && docker compose run --rm --no-deps backend \
  python -m experiments.variety_eval all --hands 3000 --seeds 42,142,242,342,442,542,642,742'
```

`variety_eval` sweeps: `A` (short-stack), `B` (pricing vs foldy/calling/neverfold),
`D` (depth drain), `P` (punisher/over-bluff), `all` (= A+D+B).
