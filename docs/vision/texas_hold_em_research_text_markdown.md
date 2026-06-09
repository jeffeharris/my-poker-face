---
purpose: External research on poker-AI archetype benchmarks, player-study methodology, and believability lessons from "Stacked"/Poki and other games
type: reference
created: 2026-06-09
last_updated: 2026-06-09
---

# Building Believable Poker AI: Archetype Benchmarks, Player-Study Methodology, and Lessons from "Stacked" and Other Games

## TL;DR
- **Use the poker-tracking industry's own opportunity-based stat definitions** (VPIP, PFR, 3-bet, fold-to-3-bet, c-bet, fold-to-c-bet, AF/AFq, WTSD, W$SD exactly as PokerTracker 4 / Hold'em Manager 3 compute them) so your AI's measured numbers are directly comparable to published human benchmarks; concrete numeric target bands for Nit, TAG, LAG, Calling Station, Maniac, Rock and recreational "fish" are given below for both 6-max and full-ring.
- **To prove distinctiveness and believability to PLAYERS (not strength), run blind, within-subjects detection and A/B ablation studies** scored against ground truth (archetype-ID accuracy vs chance, tilt-detection d-prime, adaptive-layer-on vs -off forced choice), using counterbalancing and validated perception scales (Godspeed indices, BotPrize-style humanness rating) to squeeze statistically meaningful results from a small tester pool.
- **"Stacked with Daniel Negreanu" (2006) is the key precedent**: its University of Alberta "Poki" engine used per-opponent weight tables + Monte-Carlo simulation to adapt in real time, was widely praised as the best AI in a poker game yet still criticized as "pre-tuned" and readable in ~40 hands — a cautionary tale that adaptation must be *perceptible* to players, exactly as Shadow of Mordor's Nemesis System, Alien: Isolation, F.E.A.R. and Forza's Drivatars made their AI legible through memory, naming, and dialogue.

---

## Key Findings

1. **The measurement formulas matter more than the numbers.** Every serious poker stat is an *opportunity-based* percentage — numerator = times the action was taken, denominator = times the player had the specific opportunity (not total hands). If your AI measures stats by a different denominator than PokerTracker 4 (PT4) / Hold'em Manager 3 (HM3), your numbers will silently diverge from every published benchmark. The single most common error is computing fold-to-3-bet, c-bet, etc. over all hands rather than over qualifying opportunities.

2. **Archetypes are defined primarily by VPIP/PFR and the gap between them, then refined by 3-bet, aggression, and showdown stats.** 6-max ranges run roughly 4–8 points looser than full ring for the same archetype.

3. **Believability ≠ strength.** The entire game-AI believability literature (2K BotPrize, Mario AI Turing Test, agent-believability work) measures whether humans *perceive* an agent as human/alive/distinct, not whether it wins. A constrained, fallible, legible agent reads as more human than an optimal one.

4. **Small-n can be rigorous** with within-subjects designs, counterbalancing, ground-truth detection tasks, and signal-detection metrics (d-prime), plus blinding to defeat demand characteristics.

5. **Adaptation and memory must be *shown* to be felt.** The recurring lesson across Stacked, Nemesis, Alien: Isolation, F.E.A.R., and Drivatars is that under-the-hood intelligence is worthless to players unless surfaced through perceptible cues (callbacks, naming, dialogue, visible style shifts).

---

## DELIVERABLE 1 — Archetype Statistical Target Ranges + Precise Measurement Definitions

### 1A. Canonical stat definitions (PokerTracker 4 / Hold'em Manager 3)

**VPIP (Voluntarily Put $ In Pot).** Percentage of hands in which the player *voluntarily* commits chips preflop — limp/call/raise all count; posting the blind or checking the BB option does NOT count.
- Formula (modern, PT4/HM3): VPIP% = (hands player voluntarily put money in preflop) / (hands dealt − walks) × 100. Walks (everyone folds to the BB) are excluded because the BB had no opportunity to act voluntarily.
- Key exclusion: the SB completing is voluntary; the BB *checking* its option when unraised is NOT voluntary (no VPIP).

**PFR (Preflop Raise %).** Percentage of hands in which the player makes at least one preflop raise (open-raise, isolation raise, 3-bet, 4-bet all count toward PFR).
- Formula: PFR% = (hands with ≥1 preflop raise) / (hands dealt − walks) × 100.
- PFR ≤ VPIP always. The VPIP−PFR gap measures passivity.

**3-bet % (preflop).** "To three-bet is to raise after exactly one other player has raised."
- Formula: 3B% = (times 3-bet preflop) / (number of 3-bet opportunities) × 100. An *opportunity* exists when, facing exactly one prior raise, the player can reraise. Players who merely called the open or the BB are ignored. (PT4 implements this literally as `cnt_p_3bet / cnt_p_3bet_opp × 100`.)

**Fold to 3-bet %.** How often a player who *opened* (made the initial raise) folds when reraised.
- Formula: F3B% = (times folded to a 3-bet) / (opportunities to fold to a 3-bet) × 100.
- Critical opportunity rule: **only the initial raiser has a fold-to-3-bet opportunity.** If A raises, B 3-bets, and C is cold facing two raises, C does NOT get a fold-to-3-bet opportunity (cold-calling/folding two raises is filtered out as uninformative).

**C-bet (flop continuation bet) %.** How often the preflop aggressor bets the flop when given the chance (whether checked to or first to act).
- Formula: FlopCB% = (times c-bet flop) / (times had opportunity to c-bet flop, i.e., was preflop raiser and saw flop) × 100.

**Fold to flop c-bet %.**
- Formula: = (times folded to a flop c-bet) / (times faced a flop c-bet) × 100. "The key is 'Could Fold to a Continuation Bet'" — denominator is times the player actually faced a c-bet.

**Aggression Factor (AF) vs Aggression Frequency (AFq) — different formulas:**
- **AF = (Bets + Raises) / Calls.** Checking and folding do not affect it. Range 0→∞. A player who never calls has infinite AF. AF is the classic "Holy Trinity" stat (VPIP/PFR/AF) but cannot distinguish a maniac from a fit-or-fold nit (both rarely call). Hold'em Manager also offers **Aggression Percentage (Agg%) = (Bets+Raises) / (Bets+Raises+Calls+Checks)**, which counts checks as passive.
- **AFq (Aggression Frequency) = (Bets + Raises) / (Bets + Raises + Calls + Folds) × 100.** Used in PokerTracker; counts folds in the denominator. Most winning players sit ~28–41% AFq.
- *For your AI:* report BOTH AF and AFq (and per-street, since overall numbers hide flop-maniac/turn-passive patterns).

**WTSD (Went To Showdown %).** Of hands where the player *saw the flop*, the percentage that reach showdown.
- Formula: WTSD% = (hands player went to showdown) / (hands player saw the flop) × 100. NOTE the denominator is "saw flop," not "hands dealt." (Some simplified glossary sources use total hands; PT4/HM3 use saw-flop.)

**W$SD (Won $ at Showdown %).**
- Formula: W$SD% = (showdowns won) / (showdowns reached) × 100. ~49–54% is the healthy band for winning players.

> **Implementation note for the developer:** Replicate these exact numerator/denominator opportunity rules in your AI telemetry. The biggest comparability traps are (1) using total-hands denominators instead of opportunity denominators for 3-bet/fold-to-3-bet/c-bet, (2) excluding walks from VPIP/PFR, and (3) using "saw flop" (not "hands dealt") as the WTSD denominator. Match these and your AI's HUD line will be directly comparable to any published human sample.

### 1B. Archetype target bands

All bands are VPIP/PFR/3-bet plus postflop. **6-max** first, then **full-ring (FR)** where it differs. Cash-game, ~100bb. These are synthesized from PokerStrategy, Hold'em Manager forums, Beasts of Poker, Automatic Poker, PokerCopilot, and PokerCoaching benchmarks.

| Archetype | VPIP | PFR | 3-bet% | Fold-to-3B | C-bet% | Fold-to-CB | AF / AFq | WTSD% | W$SD% |
|---|---|---|---|---|---|---|---|---|---|
| **Nit** | 6max 12–17 / FR 8–13 | 10–15 / 6–11 | 2–4 | 65–80% | 55–70 | 55–70% | AF 2–4 | 20–24 | 52–58 |
| **TAG** | 6max 20–25 / FR 14–19 | 17–22 / 11–16 | 5–9 | 50–62% | 55–70 | 45–55% | AF 2–3 / AFq 35–45 | 26–29 | 52–56 |
| **LAG** | 6max 26–32 / FR 20–26 | 22–28 / 17–22 | 9–14+ | 35–50% | 60–75 | 40–50% | AF 3–5 / AFq 40–50 | 27–31 | 48–52 |
| **Calling Station / loose-passive (fish)** | 6max 40–60 / FR 35–55 | 5–13 | 0–3 | 25–45% | 25–45 | 20–35% | AF <1 / AFq <25 | 32–45 | 44–50 |
| **Maniac** | 6max 45–70 / FR 40–65 | 35–55 | 12–25+ | 15–35% | 75–95 | 25–40% | AF 5–10+ / AFq >50 | 30–40 | 40–48 |
| **Rock** | 6max 10–14 / FR 6–11 | 6–10 / 4–8 | 1–3 | 70–85% | 45–60 | 55–70% | AF 1–2 | 20–24 | 54–60 |
| **Weak recreational "fish" w/ leaks** | 6max 30–45 / FR 25–40 | 10–18 | 2–5 | 30–50% | 40–60 | 30–45% | AF 1–2 | 30–38 | 45–50 |

**Notes:**
- **Nit vs Rock:** both ultra-tight; the Rock is even more passive (lower PFR, lower AF, more limping of big hands then re-popping) while the Nit is "tight but plays its range aggressively." Stats overlap; the differentiator is the aggression profile, not the looseness.
- **Calling Station vs Maniac:** both have very high VPIP; PFR splits them — station PFR is low (huge VPIP−PFR gap), maniac PFR is high. High WTSD + low W$SD is the calling-station signature ("too many weak calls"). A typical station is ~40/13; a typical maniac ~50/40.
- **6-max vs full-ring:** the same archetype runs ~4–8 VPIP points looser in 6-max. A "TAG" is ~20/18 in 6-max but ~15/13 in full ring (a common quote: "a TAG at full ring has a VPIP of about 15% and a TAG at 6max is usually closer to 20%"); a "nit" is ~13/11 6-max, ~10/8 FR.
- **Sample-size caveat to bake into your validation:** preflop stats (VPIP/PFR) stabilize by ~100–300 hands; 3-bet/fold-to-3-bet need 1,000+; postflop WTSD/W$SD/c-bet need several thousand (multiple sources cite ~8,000 hands for reliable WTSD/W$SD). Generate large simulated samples per archetype before claiming a number is "in band."

---

## DELIVERABLE 2 — Methodology for Human Player Studies (Distinctiveness, Believability, Exploitability)

### 2.0 Core principle
You are NOT measuring whether the AI wins. The BotPrize/believability tradition is explicit: the goal is *human-likeness / distinctiveness / "aliveness" as perceived by players*, and an over-optimal agent reads as a bot. In the 2012 2K BotPrize (Unreal Tournament 2004), two bots finally crossed the "50% humanness barrier": **MirrorBot (Mihai Polceanu) scored 52.2%** and **UT^2 (Risto Miikkulainen, Jacob Schrum, Igor Karpov, UT Austin) scored 51.9%** — both *higher than the ~40% average humanness rating earned by the actual human players* — and split the A$7,000 prize. The reason they read as human was engineered *constraints*, not skill. As UT^2 co-creator Jacob Schrum put it: *"If we just set the goal as eliminating one's enemies, a bot will evolve toward having perfect aim, which is not very human-like. So we impose constraints on the bot's aim, such that rapid movements and long distances decrease accuracy."* Design your study questions around perception and discrimination, not win-rate.

### 2.1 Detection / discrimination tasks with ground truth

**(a) Archetype identification task.** Show each tester a fixed set of hands/sessions played by one AI archetype (label hidden). Ask them to classify it (Nit/TAG/LAG/Station/Maniac…) from a closed list. Score accuracy vs **chance = 1/(number of archetypes)** (e.g., 1/6 ≈ 16.7%). Above-chance classification = distinctiveness is *legible*. Use a confusion matrix to see which archetypes blur (e.g., Nit↔Rock, Station↔fish) — that tells you which behaviors to exaggerate.

**(b) Tilt / emotional-state detection.** Insert sessions where the emotion layer is triggered (e.g., post-bad-beat tilt) vs neutral baseline. Use a **yes/no (A–Not-A) or 2-alternative forced-choice (2AFC)** task: "Is this player on tilt?" Score with **signal detection theory**: compute **d-prime (d′)** from hit and false-alarm rates to separate true sensitivity from response bias. Typical well-designed 2AFC studies target 60–90% accuracy (d′ ≈ 0.5–2.5) to avoid floor/ceiling. d′ ≈ 0 means tilt is imperceptible; d′ ≥ 1 means players reliably feel it.

**(c) Adaptation / memory detection.** Have testers play two matched sessions — adaptive-opponent-modeling layer ON vs OFF — then ask, in 2AFC, "In which session did opponents adjust to you / remember you?" Above-chance identification is direct evidence the adaptive layer is *perceptible*. Pair with open-ended "what made you say that?" to capture which cues fired.

### 2.2 Blind A/B and ablation design

- **Ablation conditions to compare:** (1) full system; (2) charts-only baseline (archetype preflop charts + injected behaviors, psychology/memory/adaptive layers OFF); (3) adaptive-layer-ON vs -OFF; (4) psychology/emotion-ON vs -OFF. Each toggle isolates one feature's contribution to perceived believability/distinctiveness — the standard ablation logic of game-AI feature evaluation.
- **Double-blind:** testers must not know which build is which (label builds "A"/"B"); whoever administers/scores should also be blinded to condition order. This defeats demand characteristics (players "rewarding" the build they think is the fancy one).
- **Counterbalance order** with a balanced Latin square so that fatigue/practice effects don't confound which build was seen first.

### 2.3 Getting statistical power from a small pool

- **Use within-subjects (repeated-measures) designs.** Each tester experiences every condition and serves as their own control, removing between-person variance — this is the single biggest lever for power at small n, and "this reduction in error variance often means smaller sample sizes are sufficient."
- **Control for poker skill:** measure each tester's skill (self-rating + a short standardized hand-quiz or their tracked win-rate) and use it as a covariate / blocking variable, or stratify testers into skill tiers. Skill strongly moderates both detection ability and perceived believability.
- **Beat variance/luck:** poker outcomes are noisy, so (i) score *perception/decisions*, not money won; (ii) use **duplicate/mirrored hands** (same hole cards & board across conditions, à la duplicate bridge) to cancel card luck; (iii) increase *trials per participant* (many hands/decisions each) rather than only participants — power rises with both stimuli and subjects.
- **Appropriate tests:** paired t-test / Wilcoxon signed-rank for two within-subject conditions; repeated-measures ANOVA / Friedman for 3+; report **confidence intervals and effect sizes (Cohen's d / dz)**, not just p-values, since small-n p-values are unstable. For accuracy-vs-chance use a binomial test; for detection use d′.
- **Pre-register** the hypotheses, primary metric, and analysis plan to avoid p-hacking with a small sample.

### 2.4 Validated instruments for believability / "humanness" / distinctiveness

- **Godspeed questionnaire series** (Bartneck, Kulić, Croft & Zoghbi) — five validated 5-point semantic-differential indices: **Anthropomorphism** (fake/natural, machinelike/humanlike, unconscious/conscious, artificial/lifelike, moving rigidly/elegantly), **Animacy** (dead/alive, stagnant/lively, mechanical/organic, inert/interactive, apathetic/responsive), **Likeability** (unfriendly/friendly, unkind/kind, unpleasant/pleasant, awful/nice), **Perceived Intelligence** (incompetent/competent, ignorant/knowledgeable, irresponsible/responsible, unintelligent/intelligent, foolish/sensible), **Perceived Safety**. Reported reliabilities are high — in the original validation, Anthropomorphism Cronbach's α = 0.929 (human stimulus), 0.923 (android), 0.856 (masked android); an independent validation (Castro-González et al., 2016) reported Anthropomorphism 0.89, Animacy 0.91, Likeability 0.90, Perceived Intelligence 0.81, Perceived Safety 0.80. Adapt "robot"→"opponent." Best used comparatively across your ablation conditions. (Watch the known miscoded "surprised/quiescent" item in the Perceived Safety subscale — reverse-code or drop it, as prior studies do.)
- **BotPrize-style humanness rating / confederate Turing setup:** include a human-controlled opponent and an AI opponent in mixed sessions; have judges label each "human or bot" and rate humanness on a scale, recording free-text justifications. The fraction of judges fooled is your headline believability metric.
- **Behavioral-characterization / behavioral-diversity measures:** quantify how *different* archetypes are in behavior space (e.g., distance between their stat vectors / action-distribution divergence). This is the objective complement to the subjective distinctiveness ratings — show both that archetypes are statistically separable AND that players can tell them apart.
- **Avoid leading questions:** use neutral semantic differentials and balanced scales; randomize item order; never ask "How realistic was the impressive adaptive AI?"

### 2.5 A concrete, runnable plan for an indie team (n ≈ 12–20)
1. Recruit 12–20 testers spanning skill tiers; capture skill covariate.
2. Within-subjects: every tester plays counterbalanced blocks of duplicate-hand sessions across the 4 ablation conditions.
3. After each block: Godspeed + custom distinctiveness items.
4. Embedded detection tasks: archetype-ID (vs 1/6 chance), tilt 2AFC (d′), adaptation 2AFC.
5. Optional confederate-Turing block for a humanness number.
6. Analyze with paired/repeated-measures tests + effect sizes + d′; report confusion matrix and CIs.
7. Decision rule: ship/iterate per the thresholds in Recommendations.

---

## DELIVERABLE 3 — Deep Dive: "Stacked with Daniel Negreanu" (2006) and the Poki Engine

### 3A. What it was
"Stacked with Daniel Negreanu" (2006), developed by 5000ft Inc., published by Myelin Media, on Xbox/PS2/PSP/PC, Texas Hold'em only. Its selling point over the era's shovelware poker games was the **Poki AI engine**, licensed from the University of Alberta Computer Poker Research Group (CPRG) — at the time the world's leading academic poker-AI lab. The game implemented Poki as **eight distinct "personalities"** from very conservative to extremely aggressive, plus a "Stacked Poker School" with full-motion-video tutorials and an in-hand **"Ask Daniel"** advice feature. It featured digital likenesses of pros (Negreanu, Evelyn Ng, David Williams, Josh Arieh, Jennifer Harman, Erick Lindgren, Carlos Mortensen). The Xbox version shipped with a serious Xbox Live bug that reset player data (later patched). It received mixed review scores but was repeatedly singled out for its AI.

### 3B. How Poki actually worked (technical)

Poki descended from the CPRG's "Loki" program. Core mechanisms (from the CPRG's own papers):

- **Per-opponent weight table.** Poki maintains, for every opponent, a probability distribution over all 1,081 possible two-card starting hands. From "Opponent Modeling in Poker" (Billings, Papp, Schaeffer & Szafron, AAAI 1998): *"Each opponent is assigned an array of weights indexed by the two-card starting hands. Each time an opponent makes a betting action, the weights for that opponent are modified… a raise increases the weights for the strongest hands… and decreases the weights for the weaker hands."* A floor weight (0.01) keeps any hand from being deemed impossible.
- **Generic vs specific modeling.** A "generic" model treats all opponents alike for a situation; the "specific" model keeps a separate weight array per opponent based on their observed betting history — the actual opponent modeling. In self-play tests, the specific-opponent-modeling version measurably outperformed non-modeling versions over large samples.
- **Re-weighting by Effective Hand Strength.** After each board card, weights are scaled by the hand's Effective Hand Strength relative to the opponent's inferred thresholds, so the model "zeroes in" on a narrow plausible range by the river.
- **Simulation-based decisions (selective sampling).** From "Using Probabilistic Knowledge and Simulation to Play Poker" (Billings, Peña, Schaeffer & Szafron, AAAI 1999): Poki/Loki-2 estimates the expected value of check/call vs bet/raise by *playing out the hand many times* — but the opponents' hole cards in each rollout are *sampled biased by the weight table*, not uniformly. ~500 trials per decision balanced stability against speed; an "obvious move" cutoff stopped early when one action dominated. Notably, "simulations can result in the emergence of advanced betting tactics like a check-raise" not explicitly programmed.
- **Probability triples [fold, call, raise].** Decision-making and model updates both use these; after each opponent action, weights are multiplied by the relevant triple component (worked example: a call when the context triple gives raise=0.8 sharply downweights monster hands).

**Pokibot vs Sparbot vs Vexbot (the broader Poker Academy lineage).** Poker Academy Pro (CPRG's commercial trainer, via BioTools, launched Dec 2003) bundled three different AIs that became standard research benchmarks:
- **Pokibot/Poki** — statistics + simulation, multiplayer ring-game engine (the one in Stacked); exploitative but heuristic.
- **Sparbot (= PsOpti)** — a near-Nash, game-theoretic *pseudo-optimal heads-up limit* player; static and non-adaptive (theoretically hard to exploit but doesn't punish weak play).
- **Vexbot** — an *adaptive game-tree best-response* agent (Miximax/Miximix search + real-time opponent model) that "defeats PsOpti convincingly" and exploits weak players, with a known "zero-frequency problem" (weak in unobserved situations until it gathers data).

Both Sparbot and Vexbot were so respected that academic competitors (e.g., CMU's GS1, AAAI 2006) used them as the benchmark to beat.

**Difference from modern GTO/solver approaches.** Poki is *exploitative opponent modeling* — it tries to read each specific opponent and deviate to punish them. Modern solver/GTO play computes a balanced, unexploitable equilibrium strategy that deliberately *ignores* the specific opponent. Sparbot/PsOpti was an early bridge toward the GTO side. The lineage then produced **Polaris**, which at the Second Man-Machine Poker Championship (Las Vegas, July 3–6, 2008) defeated six professionals across six duplicate 500-hand matches (6,000 hands; three wins, two losses, one tie; +195 big blinds overall) — described by the CPRG as "the first time that a poker program has statistically defeated a group of human professionals." It culminated in **Cepheus**: Bowling, Burch, Johanson & Tammelin, "Heads-up Limit Hold'em Poker is Solved," *Science* 347(6218):145–149 (Jan 9, 2015), which announced that "heads-up limit Texas hold'em is now essentially weakly solved," using the CFR+ algorithm trained on more than a billion billion hands across 4,000+ CPUs over two months. For a believability-focused *game*, Poki's exploitative, opponent-reading paradigm is far more relevant than a solver, because *adapting to the human is what feels alive.*

### 3C. What players and reviewers liked / disliked

**Liked:**
- **AI quality (comparative).** GameSpot: *"Stacked's AI is the best currently available in a console or PC poker game. It plays a smart, varied game that can be challenging if your game isn't tight."* GamesRadar called the adaptive Poki AI "the real star." Multiple outlets called it the best poker video game then available.
- **Perceived adaptation.** Reviewers liked that the AI "learns what you do as you play and reacts" — e.g., it learns if you fold to re-raises after betting weak, and exploits it.
- **The "Ask Daniel" coaching / Poker School** was widely seen as a genuine learning tool, with Negreanu's video tutorials taking players from basics to advanced strategy. GameSpot: the in-hand advice is "usually… good information."

**Disliked:**
- **"Pre-tuned" / readable archetypes.** The most damning verdict came from a self-identified serious player at Gaming Nexus: *"I played a limited number of hands... It took me only about 40 hands to figure out exactly which of the personalities I was up against... The conservative players played ultra conservative, and the aggressive players are ultra aggressive. The Poki AI bots in Stacked seemed to be pre-tuned, so while they appeared to be learning my tendencies, it didn't really seem like it was making all that much of a difference in their decisions."*
- **Occasional irrational moves.** GameSpot's preview and the A.V. Club both flagged AI calling all-ins with junk (Kx, Qx, "3-4 suited in a multi-way pot") and folding to small reraises in obvious-call spots.
- **Coaching out of sync.** The A.V. Club: Negreanu "pops in to give situational advice, much of it out of sync with the action," and sometimes tells you to shove rags.
- **Thin everything-else:** weak presentation, shallow character customization, Hold'em-only, a data-wiping Xbox Live bug. These — not the AI — sank it commercially.

### 3D. Why it's still cited as having among the best poker AI despite failing commercially
Three reasons: (1) it carried **genuine world-leading academic AI** (CPRG) in a ~$30 console product — rare then and now; (2) the **opponent-modeling/adaptation paradigm** was a real differentiator versus scripted poker games; (3) **comparative scarcity** — the field of poker video games was (and remains) weak, so "best AI in a poker game" is a comparative crown. Importantly, the CPRG's own papers hedge that the strong opponent-modeling results were largely **self-play simulation**, not conclusively proven vs humans, and the Stacked engine (Poki) was *not* the lab's strongest bot (Sparbot/Vexbot were). The actionable lesson: Stacked proves that adaptive opponent modeling is a powerful *marketing and experiential* hook — but only if the adaptation is made *perceptible* and the archetypes don't collapse into easily-read caricatures within an orbit.

---

## DELIVERABLE 4 — How Believable/Distinct NPC AI Has Been Evaluated/Praised in Other Games

**Shadow of Mordor — Nemesis System (memory/relationship precedent).** Procedurally generated orcs remember prior encounters, scar, taunt you with callbacks, and rise through ranks — turning "anonymous greenskins into believable, detestable tormentors" (GamesRadar). Design director Michael de Plater (DICE Summit) framed it explicitly around player psychological "needs" and making narrative out of gameplay. The praised believability came not from smarter combat AI but from **persistent memory + naming + personalized callbacks** that exploit the endowment effect and social memory. *Lesson for your memory layer: surface the memory as explicit callbacks ("you folded to me last time") — perceived memory is what creates the relationship.*

**Alien: Isolation — adaptive two-tier AI.** A "Director AI" (omniscient, manages a "menace gauge"/tension budget) feeds hints to a "Xenomorph AI" (a behavior tree that must find you using its own senses, never cheating). Crucially, advanced behaviors are **gated**: locker-searching/flanking sub-trees unlock only after the player repeatedly uses those tactics, *creating the illusion of learning* without true learning. Widely praised (Best Audio, GDC Choice Awards 2015; "one of the best games ever made"), but Kotaku noted that when the alien's "approximate knowledge of your location" became too obvious it "start[ed] to feel fake" — i.e., believability is fragile. *Lessons: (1) a director/tension layer + an actor layer maps cleanly onto your adaptive layer + archetype layer; (2) progressive unlocking of behaviors is a cheap, robust way to make adaptation feel emergent; (3) avoid "tells" that expose the machinery.*

**F.E.A.R. — perceived intelligence via dialogue.** Its Goal-Oriented Action Planning let soldiers replan dynamically (flank, take cover, flip tables). But Jeff Orkin's own retrospective ("Combat Dialogue in F.E.A.R.") stresses that what players actually remembered was the **squad dialogue** ("He's flanking!", "I need reinforcements!") — *"it is obvious from the reviews and forum chatter that the coordinated squad behaviors are what stood out,"* and language elevates perceived intelligence "at a subconscious level." *Lesson: your psychology/emotion layer should be voiced/surfaced through table talk and tells — narration of internal state is what players credit as intelligence.*

**Forza Drivatars — machine-learned behavioral clones.** Microsoft Research's Drivatars learn individual players' driving styles (braking points, racing lines, aggression) and race as believable stand-ins for absent humans, making single-player "feel alive" (Xbox Wire; AI and Games). Two cautionary findings: (1) early versions learned players' **bad habits** and became "dirty"/over-aggressive, forcing a "Limit Aggression" toggle and eventually a switch to training on clean expert data; (2) **rubber-banding** is perceived as unfair. *Lessons: behavioral fidelity to humans is a strong believability driver, but unfiltered imitation imports anti-social behavior, and visible "rubber-banding"/difficulty-cheating breaks trust — keep your adaptive layer's catch-up mechanics subtle and fair.*

**Cross-game synthesis for your project:** believable, distinct AI is consistently produced by (1) **persistent memory with explicit callbacks** (Nemesis), (2) **a director/tension layer separate from the actor layer** (Alien), (3) **legibility of internal state via dialogue/tells** (F.E.A.R.), and (4) **behavioral fidelity without importing exploitative or "cheating" behavior** (Drivatars). Evaluation in all cases leaned on *player perception* (reviews, forum sentiment, emergent-story sharing) more than on objective skill — reinforcing Deliverable 2's perception-first methodology.

---

## Recommendations

**Stage 1 — Instrument to industry definitions (before any playtest).**
- Implement the exact opportunity-based formulas in §1A in your telemetry. Validation gate: generate ≥10,000 hands per archetype and confirm each archetype's measured VPIP/PFR/3-bet/c-bet/WTSD/W$SD lands inside the §1B bands for the chosen format (6-max vs FR). If a stat is out of band, it's a behavior bug, not a labeling choice.
- Report AF *and* AFq, per street.

**Stage 2 — Objective distinctiveness check (no humans yet).**
- Compute pairwise behavioral distance between archetypes' action distributions. Threshold to change plan: if any two archetypes are statistically inseparable in behavior space, exaggerate the distinguishing stat (usually PFR or AF) before testing with players — otherwise players will confuse them too.

**Stage 3 — Small-n believability/distinctiveness study (§2.5).**
- Run the within-subjects, counterbalanced, blinded ablation with 12–20 skill-stratified testers and duplicate hands.
- **Decision thresholds:**
  - Archetype-ID accuracy **significantly > 1/6 chance** (binomial test) → distinctiveness is legible; if at/below chance → exaggerate behaviors.
  - Tilt-detection **d′ ≥ ~1.0** → emotion layer is perceptible; if d′ ≈ 0 → make tells more overt.
  - Adaptation 2AFC **significantly > 50%** → adaptive layer is felt; if ≈ 50% → surface adaptation via explicit callbacks/visible style shifts (the Stacked failure mode).
  - Godspeed Anthropomorphism/Animacy **significantly higher for full system vs charts-only baseline** → the psychology/memory/adaptive stack adds perceived life; report effect sizes + CIs.

**Stage 4 — Make adaptation and memory legible (act on cross-game lessons).**
- Add explicit memory callbacks (Nemesis), voiced emotional tells/table talk (F.E.A.R.), and progressive behavior unlocking so adaptation reads as emergent (Alien). Keep any catch-up/difficulty scaling subtle and fair (Drivatars/rubber-banding warning).
- Re-test the adaptation 2AFC and Godspeed deltas after these changes; this is the highest-leverage work because Stacked proves under-the-hood adaptation is worthless to players if imperceptible.

**Stage 5 — Marketing proof points (audience = players).**
- Publish the side-by-side stat table showing your archetypes hit industry benchmark bands, plus the player-study results ("X% of players correctly identified archetypes; players reliably detected adaptation/tilt; full AI rated significantly more 'alive' than charts-only"). These are concrete, player-credible believability claims — exactly the framing that made Stacked's AI its headline feature.

---

## Caveats
- **Numeric bands are synthesized ranges, not a single canonical source.** Different strategy sites and tracker forums give slightly different cutoffs; archetype labels are inherently fuzzy and overlap (Nit↔Rock, Station↔fish). Treat the §1B table as well-supported *targets*, not laws, and prefer the underlying formulas (§1A), which ARE canonical.
- **WTSD denominator ambiguity:** a few popular glossary sources define WTSD over "total hands" while PT4/HM3 use "saw flop." Match the tracker definition for comparability; note which you used.
- **Stat stabilization:** postflop and 3-bet-family stats need large samples (1,000–8,000+ hands) before a measured value is trustworthy — applies to validating your AI too.
- **Believability metrics are comparative, not absolute.** Godspeed and BotPrize-style scores are meaningful as *differences across conditions*, not as absolute "humanness" values; cultural background and prior experience shift baselines.
- **Stacked/Poki evidence limits:** the CPRG's strongest opponent-modeling results were largely self-play simulation, not proven vs humans; surviving *serious-player* (TwoPlusTwo/Reddit) verbatim discussion from 2006 is scarce/unarchived, so the "serious player" reception here leans on a small number of named retrospective and review sources rather than a broad forum sample.
- **Small-n studies remain underpowered for small effects.** Within-subjects designs and duplicate hands help, but you will only reliably detect medium-to-large perceptual effects with n≈12–20; report effect sizes and treat null results as "not detected at this power," not "no effect."
- **Forward-looking items flagged:** Alien: Isolation 2 (revealed June 2026) and ongoing Drivatar/GT Sophy ML approaches are recent/announced developments, not established evaluation precedents.