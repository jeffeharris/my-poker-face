---
purpose: External research on live-vs-online aggression benchmarks and how to design a believable (non-caricature) aggressive poker archetype via conditioning, variance, and tilt
type: reference
created: 2026-06-09
last_updated: 2026-06-09
---

# Live vs. Online Aggression Benchmarks & Designing a Believable Aggressive Poker Archetype

## TL;DR
- **Your instinct is half right.** The online-tracker benchmarks ARE the wrong reference population — solid *live* players genuinely 3-bet meaningfully more than their online counterparts, and live fields are far looser and more extreme. But your top-tier numbers (maniac ~37% 3-bet, LAG ~25%) sit at or above the realistic *sustained* ceiling even for live recreational play; expert consensus puts a genuine live maniac's durable 3-bet at roughly 15–25%. Your bands are directionally faithful, but the extreme tier is caricature-high as a long-run average.
- **The fix is not to lower the mean — it's to add conditioning and variance.** The 2006 "Stacked" AI was readable in ~40 hands precisely because aggression was monotonic and context-free. Real aggressive players vary aggression by position, opponent type, stack depth, table image, recent history, and emotional state. A 37% momentary 3-bet spike is realistic; 37% as a flat constant is a caricature.
- **Lean into your differentiators.** Tilt (which genuinely spikes VPIP/3-bet/aggression), visible avatar emotion, and table talk are exactly the systems that turn a high-frequency number into a *legible, exploitable, alive* opponent rather than a one-note "aggressive guy." Make the aggression spike *caused* by observable events the player can read and punish.

---

## Key Findings

### Deliverable 1 — Empirical aggression by format and population

**1. Online benchmarks (the reference population you used):**
- Population average 3-bet ≈ 7–8%; winning regs ~6–10%. Full-ring winning regs run VPIP/PFR around 11/8 to 16/14; 6-max regs ~26/21 (or 18–25 VPIP). Per Hand2Note's analysis, "the 3-bet frequency of regular players usually ranges from 6% to 10%."
- By archetype (online): TAG 3-bets ~5–6%, LAG ~8–12%, online "maniac" ~12–25% — with 15% already considered spewy/meaningless and ~25% the practical self-exploitation ceiling.

**2. Live cash data genuinely differs — and supports a meaningful upward shift:**
- The most substantial tracked live dataset that exists — Hand2Note's **Live Poker Database, comprising 972,577 hands scraped from Hustler Casino Live and PokerStars Live broadcasts** — found that for 242 winning tight-aggressive players (~500,000 hands), "VPIP/PFR of a winning player is 31/20 on average... win rate is 51 EV bb/100... Aggression was 1.6 and WWSF was 40% — both much lower than online." Crucially, **winning live TAGs 3-bet ~13%, vs. ~8–10% online.** The site explicitly attributes this to wide cold-call ranges: 3-betting is the main way to isolate weak players and avoid multiway pots. (Garrett "Gman" Adelstein, one of the strongest pros in that database, runs 34/24 over a 9,000-hand sample.)
- Live is structurally looser and higher-variance: ~20–25 hands/hour, deep stacks (typically 300–1000bb), frequent straddles, lots of multiway pots (~55% of live flops are 3+ way), and players rarely fold to 3-bets (all opponents fold to a live TAG's 3-bet only ~26% of the time).
- Live low-stakes recreational looseness is real and extreme. Steve Selbrede (author of *The Statistics of Poker*), in CardsChat's "Cash Game Strategy for the Rest of Us," found Vegas $1/$2 players "play about 70% more hands than the average online player (**37% vs. 22%** VPIP), and even those online players are too loose" — with average pots of 25 BBs (vs. 15 online) and 4.0 players seeing each flop (vs. 2.7). Genuine calling-station "fish" "typically have a VPIP of 40% or higher and a PFR lower than 10%," and extreme fish can be flagged at 80% VPIP over ~50 hands.

**3. The critical population nuance for your tuning:**
There are TWO different "live" reference points, and they point in opposite directions on 3-bet specifically:
- *Solid/thinking* live players 3-bet MORE than online (~13%) — they use 3-bets to isolate.
- The *recreational population average* 3-bets LESS, because weak live players express looseness by *limping and calling*, not reraising. Per Hand2Note, a "typical passive fish 3-bets in 2.1% of cases" (essentially JJ+/AKs only). When a passive live fish 3-bets, it's almost always a premium.

This means looseness (VPIP) and reraise-aggression (3-bet) are not the same axis. Your "calling station" archetype should have very high VPIP (50–70%+) with a *tiny* 3-bet (~2–5%); your "maniac" is the rarer player who expresses extremity through reraising.

**4. Verdict on your specific numbers:**
- **TAG at ~16% 3-bet** vs. an online benchmark of ~5–9%: **faithful to live** (live solid players ~13%, and a recreational-leaning live TAG could be higher). Keep it.
- **LAG at ~25%** vs. ~9–14% online: **at the high edge but defensible** for a live recreational LAG, especially over short samples or in straddled/short-handed spots. This is near the realistic ceiling.
- **Maniac at ~37%** vs. ~12–25% online: **above the realistic *sustained* ceiling.** Hard live data doesn't exist (live isn't auto-tracked), but expert estimate puts a genuine live maniac's durable 3-bet at ~15–25%. A 37% rate is believable as a *momentary, tilt- or dynamic-driven spike*, not as a season-long average. Recommendation: set the maniac's baseline around 20–25% and let conditioning/tilt push it transiently into the 30s.

### Deliverable 2 — Making aggression feel alive, not caricature

**1. Why "Stacked" failed (the thing to avoid):** The Gaming Nexus review of *Stacked with Daniel Negreanu* is explicit: "It took me only about 40 hands to figure out exactly which of the personalities I was up against in the players in front of me. The conservative players played ultra conservative, and the aggressive players are ultra aggressive." Aggression was a flat constant, decoupled from context. This is textbook **flanderization** — a single trait exaggerated until it consumes the character and becomes a predictable caricature.

**2. What real aggressive players actually do — aggression is *textured*, not uniform.** Across coaching sources (888poker, PokerCoaching, SplitSuit, BlackRain79, Bart Hanson/Crush Live Poker), good LAGs/maniacs vary aggression by:
- **Position:** open and 3-bet far wider in CO/BTN/SB than early position.
- **Opponent type:** punish tight/foldy players relentlessly; STOP bluffing/3-bet-bluffing calling stations (they can't be moved); the single biggest tell of a *good* aggressive player is that they dial it back against someone who fights back.
- **Table image & recent history:** if their bluffs just got snapped off, a thinking aggressor tightens; if the table is folding too much, they escalate.
- **Stack depth:** deeper stacks → more speculative aggression; the math of fold equity changes.
- **The good-LAG vs. maniac line:** a good LAG "has a plan behind every barrel"; a maniac "fires because firing is their default setting." That distinction — *conditioned* vs. *unconditioned* aggression — is precisely your design lever.

**3. Game-AI design principles that resolve the tension (readable AND not caricature):**
- Enemy/agent design literature stresses **readability** (the player must be able to perceive the agent's internal state and intent) and **consistency** (clear patterns to recognize and exploit) — e.g., the principle of "orthogonal unit differentiation" (Matthias Worch, GDC 2014), where each enemy type occupies a distinct, recognizable behavioral niche.
- The failure mode of pure difficulty-tuning (just cranking a scalar) is what the academic literature calls "predictable and lifeless behaviors." The fix in modern agent design is assigning distinct *decision-making traits and tactical priorities* (style variants) rather than a single aggression number.
- Resolution for you: keep the archetype **identifiable** ("that's the aggressive guy") via a high *mean* and signature behaviors, but generate **variance around that mean** through context-conditioning. The player should be able to learn the *rule* ("he 3-bets me relentlessly when I open the button, but folds when I show strength twice") — that's readable AND deep. Readability is about legible *conditions*, not a flat *rate*.

**4. Tilt is your single best tool for believable variance (and it's grounded in real psychology):**
- Jared Tendler's *The Mental Game of Poker* (the standard reference on tilt) catalogs **seven distinct tilt types: running-bad tilt, injustice tilt (bad beats/coolers), hate-losing tilt, mistake tilt, entitlement tilt ("Classic Phil Hellmuth tilt... caused by believing that you deserve to win"), revenge tilt (triggered by disrespect or a specific opponent's repeated aggression), and desperation tilt.** Each has a different *trigger* — gold for a memory/adaptive layer.
- Tilt's behavioral signature is real and observable: players "spew," widen their range, and play more aggressively after losses; the Yerkes-Dodson relationship means over-arousal degrades decisions. Bart Hanson explicitly coaches that at low/mid stakes you should "pay attention to who is tilting in the short term."
- Design implication: a loss, a bad beat, a needling opponent, or a punishing reraiser should *move* the archetype's frequencies in a direction consistent with its tilt type — and the avatar's visible emotion should telegraph it, so the spike is *earned and readable* rather than random.

**5. Table talk and visible tells are what make an opponent feel "alive" — and make their style legible in a fun way:**
- Live poker's social layer is widely described as the thing that makes opponents feel human: immediate reactions, vocal changes, and the inability to perfectly control responses create both character and exploitable information. Verbal-tells work (per Zachary Elwood, the recognized authority and author of the *Reading Poker Tells* trilogy) is about reading confidence, emotional consistency, and timing — exactly what an avatar + table-talk system can simulate.
- Table talk also shapes *dynamics*: friendly banter can make opponents less likely to 3-bet you (2013 Main Event champion Ryan Riess's stated strategy); needling (Tony G style) is used to induce tilt; declaring a fake strong hand and showing a bluff is "instantly memorable." These are concrete behaviors you can script to make an aggressive character's mental state and image-management visible.

---

## Details

**On the population-mismatch hypothesis:** You are correct that PokerTracker/Hold'em Manager benchmarks are drawn from online cash populations that are tighter, shallower, more heads-up, and more reraise-disciplined than a live $2–$1000 card-room table. The single cleanest data point: the Hand2Note live database shows winning live regs at ~13% 3-bet vs. ~8–10% online — a ~1.5x lift for *solid* players, driven by the need to isolate weak limpers/callers in deep, multiway, straddled pots where folds to 3-bets are rare. So a 1.5–2x lift on your *baseline* aggressive tiers over online benchmarks is well-justified. The place the justification runs out is the very top: a sustained 37% maniac 3-bet has no empirical support even in live play and reads as caricature.

**Why high frequency alone reads as fake:** A constant 37% 3-bet is information-free — it conveys nothing about the game state, so the human brain pattern-matches it to "robot" within a few orbits (the Stacked problem). The same 37% becomes *believable* when it's the visible *output* of a state: he's 3-betting 37% right now *because* he just lost a big pot, *because* you've been opening his button relentlessly, *because* the table is playing scared. Variance with legible causes = perceived agency.

**The readability/depth sweet spot:** You want the player's experience to be: "Within 40 hands I know he's the aggressive guy (readable archetype). After 200 hands I've learned he 3-bets my steals but folds to my 4-bets when he's calm — so I can exploit him. But when he tilts, that read inverts and he won't fold, so I have to re-read him." That arc — identifiable, then exploitable, then dynamically re-exploitable under emotional state — is the antidote to both caricature and unreadable randomness.

## Recommendations

**Stage 1 — Re-anchor the means (do this first):**
- Keep TAG (~16%) and LAG (~25%) baselines — they're defensible for a live recreational population.
- Lower the maniac's *baseline* 3-bet to ~20–25%, but make 30–40% reachable as a *conditioned/tilted state* (Stage 2). This preserves the "feels more aggressive than online benchmarks" faithfulness while removing the flat-caricature liability.
- Split looseness from reraise-aggression: give "calling station" archetypes VPIP 50–70% with 3-bet ~2–5%. Don't let high VPIP automatically imply high 3-bet.

**Stage 2 — Add conditioning layers (the core of believability):** Make each aggressive archetype's 3-bet/aggression a function, not a constant. Condition on, in rough priority order:
1. **Opponent-specific memory:** 3-bet more vs. players who fold a lot; dial back vs. players who punish them (4-bet/snap them off). This single rule produces the "alive" feeling more than any other.
2. **Position:** wide in late position, much tighter UTG.
3. **Emotional state/tilt:** route recent results into a tilt variable that pushes frequencies up — and, per Tendler, give each archetype a *tilt type* with a specific trigger (bad-beat/injustice, being needled/revenge, being out-aggressed, entitlement).
4. **Table image & recent history:** tighten after getting caught; escalate when the table over-folds.
5. **Stack depth/straddle:** more speculative aggression when deep or when straddles inflate pots.

**Stage 3 — Make the state observable (your differentiators):**
- Tie avatar emotion and table talk directly to the tilt/image variables so the frequency spike is *telegraphed*. A maniac who just took a bad beat should *look* steamed and *say* something needly — then spew. That makes the 37% spike feel earned and gives the player a fair read.
- Use banter to express dynamics (friendly toward players he's not targeting; hostile/needling toward a rival he's on revenge-tilt against).

**Benchmarks/thresholds that should change the design:**
- If playtesters identify any archetype's style in **<40 hands and it never surprises them again**, aggression is still too monotonic — add or strengthen a conditioning layer.
- If players **can't articulate a reliable exploit after ~200 hands**, the variance is too noisy/random — tighten the conditions so the rules are learnable.
- If a maniac's *session-average* 3-bet exceeds ~25% across many sessions (not just spikes), it's drifting back toward caricature — cap the baseline and let only transient states exceed it.
- Track whether tilt spikes are **attributable** by players to a visible cause; if not, strengthen the emotion/table-talk telegraphing.

## Caveats
- **Live data is sparse and not auto-tracked.** The strongest live numbers (the ~13% reg 3-bet, ~31/20 VPIP/PFR) come from a single tracked database built largely from *high-stakes streamed* games (Hustler Casino Live, PokerStars Live), not low-stakes recreational full-ring; treat them as directionally reliable, not precise for $1/$2. Much live "data" is necessarily expert estimate and forum consensus.
- **The ~37% maniac question can't be settled empirically** because no public dataset tracks low-stakes live maniacs over meaningful samples. The "above realistic sustained ceiling" verdict is an expert-synthesis judgment, not a measured fact — but it's well-supported by the absence of any tracked figure that high even online (where 15% is already "spewy" and ~25% is the self-exploitation ceiling).
- **Looseness ≠ aggression.** Be careful not to conflate VPIP and 3-bet; the live population is loose primarily via calling/limping, so a faithful live table needs mostly loose-passive fish with a *few* genuinely aggressive outliers, not a table full of high-3-bet maniacs.
- **This is a game, not a real-money trainer.** Perceived believability and fun legibility matter more than statistical fidelity to the decimal; where the two conflict, optimize for "reads as a real person you can learn and beat."
