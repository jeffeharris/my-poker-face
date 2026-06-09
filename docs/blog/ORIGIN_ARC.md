---
purpose: Grounded origin arc of My Poker Face (2023-2026), reconstructed from git history and Claude session transcripts, to give the blog a narrative spine
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# My Poker Face: The Origin Arc

This is a reconstruction of how My Poker Face actually got built, drawn from git history and Claude Code session transcripts. It is deliberately unromantic. Where the artifacts prove something, I say so; where I'm inferring, I flag it; and where only the founder can answer, I've left the question for him rather than invent a motivation.

## The three-summers rhythm

The dominant pattern in the commit log is not steady progress. It is a project that got picked up hard, then put down, then picked up again — three times across 2023, 2024, and 2025 — before settling into a sustained sprint that ran from December 2025 onward.

The shape, from git:

- **2023:** A 10-day build burst in mid-July (136 commits), then six months of silence, then two evenings in late January 2024.
- **2024:** A two-day Streamlit spurt in February, six months dark, then a real push from August through November (peaking at 117 commits in October).
- **2025:** Eleven months of nominal window with every commit — all 160 of them — packed into June 1-11.
- **2025-2026:** Six more dormant months, then a restart on December 12, 2025 that never stopped: production by January 3, and continuous heavy work through the June 2026 launch.

Each restart followed a roughly six-month gap. The artifacts can show the gaps precisely; they cannot explain them. Why the project kept going dark and kept coming back is the single biggest thing only the founder can answer, and it recurs as an interview question below.

A note on AI-assisted development: the early eras (2023 through mid-2025) predate AI pair-programming in this repo — everything was hand-typed by one person. GitHub Copilot enters heavily during the December 2025-February 2026 sprint, and rich local Claude Code transcripts only begin on 2026-05-13. So for the modern eras we have the founder's actual chat prompts; for the early eras we have only commits, code, and a one-line README.

---

## Era 1: The Prototype (July 2023 - January 2024)

The whole thing starts as a console Texas Hold'em engine in plain Python — `poker.py`, `player.py`, `cards.py` — wired to OpenAI through langchain, built in a single concentrated burst over about ten days in mid-July 2023.

The first commits are pure poker plumbing: betting rounds, dealer rotation, hand comparison, player options. The engine was genuinely hard to get right. The betting-round logic alone was rebuilt three times — the git history literally contains a version named `bad_betting_round`, then a recursive attempt, then a rotating-list version, finally landing with the commit *"changed betting_round completely... the logic for the betting round seems to work finally"* (2023-07-20).

The defining decision, though, isn't in the engine. It's that the AI players aren't generic bots — they're named personas. From the start the cast is recognizable celebrities and characters (the original 2023 list was licensed names; it lives in git history, and the shippable roster has since been replaced with public-domain figures — see `CLAUDE.md` "Persona names"). The commit *"added a random player capability to pull from a list of celbrity players for each game"* (2023-07-18, typo original) commits to opponents being recognizable characters rather than anonymous bots. **For any published post, describe this era generically and name only public-domain personas.**

The conceptual seed of everything that follows is a small one. On 2023-07-13, the commit *"Added some confidence and attitude"* replaced hardcoded values (`confidence='Unshakeable'`, `attitude='Manic'`) with live LLM calls, so an AI player describes its own mood on entry and updates it per decision. The structured JSON the model is asked to return in that era's `player.py` prompt — `hand_strategy`, `action`, `comment`, `inner_monologue`, `persona_response`, `new_confidence`, `new_attitude`, `bluff_liklihood` — is recognizably the direct ancestor of the psychology system that exists three years later. (Inference: that this was the deliberate "origin of the product" is my framing, not the founder's stated intent at the time. The commit message is modest.)

The entire vision for the project, in this era, is one line — the original README, written July 2023:

> A poker game with LLMs where you can define who you are playing against and have a conversation with them while you play.

There is no vision doc, no design doc, no architecture writing. The only prose is that README line and a captured game transcript (`poker_py_log_072023.txt`). The token-limit problem that the modern codebase still manages was already present on day one: two cleanup commits on July 28 wrestle with it.

There's also an abandoned thread worth noting honestly: a 2023-07-21 refactor to `AIPokerPlayer`/`PokerPlayer` subclasses "so I don't have to untangle that later for other games" was committed and then reverted twice — a stab at premature generality that didn't take.

After July, the project goes almost completely dark. Two stray commits on July 28, then nothing for six months. It picks back up for two evenings on January 29-30, 2024, and the one real change is ripping langchain out and replacing it with hand-written `LLMAssistant` / `OpenAILLMAssistant` classes: *"remove langchain and switch to new assistants class"* (2024-01-29). (Inference: this reads like a deliberate decision that langchain was more friction than help, but only the founder can confirm the motivation, and whether OpenAI's then-new Assistants API prompted it.)

---

## Era 2: The Web Rewrite (February - December 2024)

This is the year the project tried to stop being a terminal toy.

It opens with a short February burst — 32 commits, all on February 12-13 — building a Streamlit front-end over the existing console engine: *"add streamlit app and supporting refactoring in the cards and poker modules"* (2024-02-12). Then it goes silent for roughly six months again.

Work resumes in August with a decisive pivot away from Streamlit toward Flask. *"Refactor project structure and add Flask app"* (2024-08-13) moves the engine into a `core/` package, and *"Add SocketIO for real-time updates and refactor game state management"* (2024-08-21) is the real-time backbone that makes a multiplayer-feeling table possible. From there the work is heavy, sustained refactoring of the poker engine — serialization, betting rounds, pot and side-pot logic, hand evaluation — interleaved with UI work, peaking at 117 commits in October.

The celebrity hook is reborn quietly inside this churn. On 2024-10-15, *"Update AI player generation to use celebrities"* swaps the generic `get_ai_players` for `get_celebrities(shuffled=True)[:3]`, drawing from a hardcoded `CELEBRITIES_LIST` (a mix of licensed and public-domain names at the time; only the public-domain ones, like Socrates and Dracula, survive in today's roster). It's a one-function change with a modest message — "introduces variety and more engaging opponents" — but it re-establishes the product's identity after the rewrite reset it.

Two structural rewrites in late October introduce patterns the project still uses: *"Refactor Player data from dictionaries to dataclasses"* and *"Add PlayerController classes and PokerStateMachine"* (both 2024-10-23), plus *"Introduced GamePhase enum for clearer game state transitions"* (2024-10-22). The `funct-prog-poker` branch family shows this was a deliberate move toward the immutable functional-core architecture the project documents today. The headline milestone is *"Fully working end to end game logic with AI"* (2024-10-19).

A characteristic working pattern shows up here: rewriting in place via an `old_files/` graveyard rather than deleting. Commits like *"Refactor: Move poker-related modules to old_files directory"* (2024-10-14) sit alongside transitional imports like `from old_files.poker_game import PokerGame`. There's also a side detour — a whole second card game (Spades) built in Flask over September 13-16, including AI bidding. The commits don't explain why.

The era ends abruptly on 2024-11-12 with *"Rename and move streamlit_app files to old_files directory"* — the original front-end formally retired.

**Correction to the original brief:** this era was described to me as a "Flask + SocketIO + React rewrite." The git evidence shows no React in 2024 at all — it's Flask + SocketIO + server-rendered Jinja templates and vanilla JS (`init.js`, `messages.js`). React appears to enter later. When React actually arrived is a question for the founder.

---

## Era 3: Personalities Grow Up (June 2025)

The window for this era is nominally eleven months. Every one of its 160 commits lands in a single tight burst, June 1-11, 2025. The rest is silence on both ends.

This is the burst where the AI personalities stopped being static config. It opens with a real prompt-management system — *"Add comprehensive prompt management system for AI players"* (`ec8b6d9f`, 2025-06-01): `PromptManager` + `PromptTemplate`, a 7-personality `personalities.json`, plus golden-path tests — and a runtime personality generator — *"Add AI personality generator with dynamic generation"* (`afaea485`) — that calls OpenAI to synthesize a full trait profile (`play_style`, `confidence`, `attitude`, `bluff_tendency`/`aggression`/`chattiness`/`emoji_usage`, plus verbal and physical tics) from just a character name. Two days later came a web personality tester and a separate `personality_manager` for editing traits (`0620e31d`, 2025-06-03), turning tuning into a hands-on loop instead of hand-editing JSON.

The conceptual centerpiece is the personality elasticity system (`1fc0415d`, 2025-06-04): a ~1,976-line drop spanning `elasticity_manager.py`, `pressure_detector.py`, and full docs and tests, hardened the next day with `pressure_stats.py` (`c8986df7`). Traits become `ElasticTrait` values with an anchor, an elasticity band, and accumulating pressure — so an AI can deviate under game stress but recovers toward its baseline identity. This is the era's largest single commit and the direct mechanism behind the "living personalities" idea.

The back half of the burst (June 6-9, the two heaviest days at 35 and 45 commits) is productization rather than personality depth: a personality database layer, a full Docker Compose setup (`c2869e3b`), a feature-based React reorg, AI-powered quick-chat suggestions (`9ebe2815`), betting-UI work, a v1.0 release candidate, basic auth plus guest login, and Render.com deployment config. (Note: this is the first React evidence — confirming React arrived after 2024, though pinning the exact first appearance is a founder question.)

The founder documented intent up front here for the first time: `GAME_VISION.md`, `FEATURE_IDEAS.md`, and `PERSONALITY_ELASTICITY.md` were all created 2025-06-03, and `AI_GENERATION_PLAN.md` laid out a three-phase generation roadmap (single-shot → targeted refinement → full chat). (Inference: this suggests the personality arc was planned deliberately rather than stumbled into.)

The era ends abruptly on June 11 with two small fixes, then silence. Whether the later phases of the generation plan ever got built in this era, and whether the v1.0 Render config was ever flipped live, the artifacts can't say.

---

## Era 4: The Sprint Ignites (December 2025 - February 2026)

After roughly six dormant months — the last commit before this window is 2025-06-11; the next is 2025-12-12 — the project restarts and does not slow down. This restart is the ignition the era is named for.

December opens with small fixes, then a multi-day burst (Dec 29-31, about 60 commits) lays systems that define the game's identity: an AI memory and learning system (*"feat: add AI memory and learning system"*, 2025-12-30, ~2,400 lines across `poker/memory/`), a tilt system that degrades AI play when losing (2025-12-31, ~900 lines), PWA support, and an LLM-generated emotional-state system (2026-01-01).

January is the obsessive month. Production goes live on Hetzner with HTTPS on 2026-01-03 (`1bec7b7a`), and the same day `flask_app` is modularized into routes/handlers/services. The next day, a clean `core/llm` package with per-call usage and cost tracking lands (`6d0144d3`, 2026-01-04). (Inference, but well-supported by the sequence: this boring infrastructure commit is what made everything after cheap — nine days later, six providers were added in two days: Groq, Anthropic, DeepSeek, Mistral, Google, xAI, 2026-01-13/14.)

From there the work fans out: a prompt debugger and decision-quality analyzer with equity-vs-range calculation and an INTERROGATE mode (2026-01-07); an AI-tournament experiment framework with A/B testing and parallel execution (`963d66c0`, 2026-01-16, ~1,700 lines); a unified admin dashboard, Google OAuth, RBAC, and a landing page; and a tournament end-review experience (2026-01-06).

February turns toward depth over breadth. A real-time coaching assistant (2026-01-30) grows into a multi-milestone coach-progression system. The ad-hoc emotion code is rebuilt into a formal, phased Psychology System v2 (energy, poker-face zones, event sensitivity, zone gravity) with its own PRD and a trajectory viewer for debugging. And the AI decision engine pivots from free-form prompting toward the bounded-options architecture — rule-based bots (CaseBot/ManiacBot/BluffBot), then *"feat: add hybrid bounded-options AI system"* (2026-02-10) and a v2 case-matrix (2026-02-12) — where the LLM picks from EV-labeled options rather than reasoning about poker directly. That direction is still core to the project.

One process note: on 2026-01-28, a "Ralph Wiggum" autonomous triage agent was added (`9acdf96d`) — a bash loop running headless Claude Code against a pre-approved triage spec, an early bet on agent-driven maintenance distinct from the Copilot-assisted PR loop used all month.

**A data caveat I can't resolve:** the era brief cites a "108 → 988 (Jan) → 355" commit progression. The marketing-repo git log for this window shows roughly 1,451 commits total, with January alone well over 900. What the 108/988/355 figures count (a different repo, net-of-merges, or squashed history) needs the founder to clarify. And because no local Claude Code transcripts exist before 2026-05-13, the decision rationale for this whole sprint lives only in the founder's memory or in Copilot/web-Claude history not captured here — so the human-intent quotes that ground the later eras simply aren't available for this one.

---

## Era 5: Building the Living Economy (March - May 2026)

The window name is partly misleading, and it's worth being honest about why: git shows almost nothing for the first two-thirds of it. Commits stop on 2026-02-17 and don't resume until 2026-05-12. March and April 2026 are empty across all branches. The era as actually recorded is a single ~9-day burst, May 12-20, after a roughly 2.5-month silence.

That burst did two things in parallel.

**First, it ground the TieredBot up into a genuinely competitive opponent.** This was a long, instrumented slog through numbered phases — c-bet exploitation, opponent spots, adjustment-layer widening, intervention traces, playstyle rule families — heavily reviewed by Codex round after round before code was written, and repeatedly validated *or rejected* by simulation rather than intuition. That last part matters and the founder kept it honest: Phase 8.1b fold-mass suppression was reverted because it regressed bb/100 in sims, and a defense-floor jam-price row was "rejected by sim" (`196f53ad`). Validation overrode design intent more than once. The grind also hit an honest plateau, in the founder's own words:

> i cant beat casebot still, as me or with the tieredbot. tieredbot gets beat by chasing raises but casebot onoybraises when theyvhave nuts or very strong hands i think by definition.

> seems like we're not improving the play of the tiered bot anymore

**Second, it laid the foundations of the living economy.** This is where the local Claude Code transcripts begin, so for the first time we have the founder's actual prompts driving the design. It started from a product question, not a tech one:

> how might we build a fun/addictive hook into this game? like what keeps you coming back? what kind of progression or RPG like features could there be?

> how might a cash game mode work? players could come/go from the table, have a bank roll at home they can tap into or they can earn money back over time. the AI would still have that memory. i just dont know how the memory aspect surfaces in an interesting way

On May 17 the relationship layer landed: schema v85-v87 gave every personality a stable `id`, added observer/opponent identity to opponent models, and added `relationship_states` (heat/respect/likability, cross-session, with heat decaying over real time) and `cash_pair_stats` (cumulative PnL per pair). On May 17-18 cash mode v1 followed — schema v88 bankroll tables, then a rapid sequence: AI sit-down, seat-filling, a hand-orchestration loop, mid-hand quit forfeit, a bankroll HUD, sponsorship loans, bust/rebuy modals, a multi-table lobby, and AI cash-out closing the loop. May 18 alone carried 99 commits, the densest day in the window.

Several decisions in this sprint were the founder's explicit calls, on the record:

- **Refuse legacy code.** *"i dont want to support 'legacy' if at all possible. i want a clean code base and clear naming"* — which drove renaming `MemorableHand.memory_type` into a `RelationshipEvent` enum (`34ebe0dd`) rather than bolting it on.
- **Reuse the existing game UI.** After a 500 on `/api/cash/start` and a non-functional first attempt: *"cant we use the same poker game interface we have today?"* and *"i don't understand why we can't better reuse this from te tournament gameplay"*. The result was `b2a0ad36`, building cash mode on the tournament flow and dropping a parallel orchestrator — followed by *"it works! game runs."*
- **Sponsorship loans, not free grants.** *"i dont want it to be auto given $5k on loss... you could be sponsored and have to give up 20% of your earnings until you pay off"* — which set the starting bankroll to 200 chips (`c6ecf4d7`).

(Inference: that the CaseBot plateau is what shifted energy toward the economy layer is suggested by the prompt sequence, not stated outright. Whether the $200 / 20% numbers were gut-feel or playtested, and whether the heat-decay timing was ever validated against real play, are founder questions — the commit messages describe intent but don't show data behind it.)

---

## Era 6: Launch & the Captain's Logs (May 21 - June 9, 2026)

This is the window where the project actually went live in its modern form.

The headline event was the 2026-06-05 production cutover, which moved prod from a four-month-stale schema (v70, February) to the full modern stack — cash mode, circuit economy, ledger, tournaments, presence, renown — and turned the circuit on with a freshly-minted economy. The commit cadence shows the run-up: heavy daily work through late May into early June (a 198-commit spike on 05-29, 146 on 06-01, 96 on 06-03), then a deliberate quiet on launch day itself (21 commits on 06-05) as the work shifted from coding to deploying and firefighting.

The honest story the founder chose to keep is in `docs/captains-log/development/launch-day-cutover-and-four-wrong-turns.md`. The thing feared for weeks — the schema migration — was a non-event:

> The merge and the migration were the easy part, which surprised me... a 0-conflict fast-forward. So the schema cutover — the thing we feared — was a non-event.

Everything *around* the migration was the hard part. Launch day produced four confident misdiagnoses in a row, each fixed only by reproducing or measuring rather than theorizing:

1. **"prod's DB is corrupt"** — it wasn't. `deploy.sh` rsync was shipping the dev machine's WAL sidecar files over prod's clean database. Fixed by excluding `data/` from rsync (`3be5648f`, `db026939`). Standing lesson: deploying from a dev box is a footgun, and the safety net fired correctly — the operator just misread why.
2. **"it's a stale service worker"** — driving a real headless browser (Playwright) revealed five CSP violations from the redesign's Google Fonts. The fix was a one-line nginx CSP change (`3463afd3`). The PWA auto-reload patch built for the wrong theory (`34c397c3`) was kept anyway because it was independently useful.
3. **An incomplete feature-flag audit** — the founder forced it exhaustive (*"is renown enabled?"* then *"find ALLLLL the flags"*), which exposed that `PRESTIGE_SEEKING_ENABLED` was silently inert without the renown flags.
4. **Two scaling assumptions** corrected by measurement — including watching RSS plateau flat at ~551MB for 35 minutes before capping backend memory at 1200m (`e945fecf`), rather than chasing a phantom leak.

A related discovery just before cutover (06-03): a renown sim died on `no such column: entity_kind` because renumbered migrations (v139/v140 inserted below a live DB's version) never ran. The lesson — *"schema_version = N does not prove the schema is complete"* — produced a completeness-check script and a CI migration-contiguity guard before anyone touched prod.

This era is also where the build-in-public discipline took shape: roughly 35 dated captain's-log entries across about 19 worktree folders, written to record wrong turns and corrections rather than gloss them.

There was a narrative pivot before release, too. The founder explicitly backed off the deep lore:

> the fish thing might go ove rpeoples heads and we're not making the fish a focus of the game. i think i over rotated there and it got too cute. what are some other ideas?

…and chose a fuzzier "circuit" framing to tease what's there without committing.

The window closes with playtesting-driven fixes (*"whats the deal with vacation greg? he is crushing people haha"*), a CI/deploy hardening pass after a runner ran the box out of disk (moving to a GitHub Container Registry pipeline — *"i'll pay for the stoarge if needed, want to keep it private for now"*), and, on the marketing branch on 06-09, an editorial "After Hours" landing-page redesign built on real screenshots (`1e332cd8`).

(Uncertainties for the founder: the captain's log names the target schema v151, while project memory references v155/v157 — the versions kept moving via a migration-squash effort, so the exact prod version at cutover needs confirming. Prod already existed at v70, so the 06-05 event was a major-update cutover of an already-deployed site, not necessarily a first-ever public launch — what "launch" meant publicly is the founder's to define.)

---

## What the AI pair changed

The honest version: the modern era was built with Claude Code as a pair, and you can see exactly where in the artifacts.

For the first three eras — 2023 through mid-2025 — there is no AI pair. The code is hand-typed by one person, often in the small hours, and the only documentation is a one-line README and some captured transcripts. The 2023 betting-round rewrite, the langchain removal, the Streamlit-to-Flask pivot, the dataclass/state-machine refactor, the elasticity system — all of that predates AI-assisted development in this repo.

The shift begins in the December 2025-February 2026 sprint, but indirectly: that era leaned on **GitHub Copilot** (dozens of "address Copilot review feedback" commits and `copilot/*` PR branches) and introduced the "Ralph Wiggum" autonomous triage agent. We can see the *output* of AI assistance there, but not the conversation — the local Claude Code transcripts don't exist before 2026-05-13.

From mid-May 2026 onward, the pairing is directly visible in the chat. The May economy sprint and the June launch are full of the founder's actual prompts steering the work — and, importantly, steering it *against* the AI's first instinct more than once. *"cant we use the same poker game interface we have today?"* killed a parallel orchestrator the AI had started building. *"find ALLLLL the flags"* forced an audit the first pass had done incompletely. The launch-day captain's log even credits a "code-architect" agent with correcting a memory-baseline assumption — but it also records four confident wrong diagnoses, some of them the AI's, that were only resolved by the founder insisting on reproducing or measuring rather than theorizing.

What's fair to say: the AI pair sped up the modern eras and changed the *texture* of the work — heavier review loops (Codex round after round before code), more documentation, agent-driven triage, captain's logs. What's not fair to say is that it changed the project's identity or its core ideas. The premise was fixed in one README line in July 2023, the "living personalities" mechanism was hand-built in 2023 and matured in mid-2025 before the Claude Code transcripts begin, and the hardest calls in the modern era — refuse legacy, reuse the UI, sponsorship not grants, drop the fish lore, measure don't guess — were the founder's, on the record, sometimes overriding the AI. The pair was a force multiplier on a direction that was already set.

(How much of the cutover was genuinely agent-driven vs. founder-driven is something only he can weigh accurately for an honest build-in-public account — flagged as an interview question.)

---

## Founder corrections & voice (interview 2026-06-09)

The reconstruction above is from artifacts. The founder interview
(`FOUNDER_INTERVIEW.md`) resolves the open questions and corrects a few framings.
Where this section and the reconstruction differ, **this section wins** — it's the
primary source.

- **The gaps were life + frustration, not lost interest.** Two kids, job
  changeover, and a project obsessive enough that he had to step away. He also
  stepped away when he "couldn't find a good use for it" or was "so frustrated with
  how bad the LLMs were at playing poker."
- **Employment framing (use this publicly):** following the **acquisition of
  Yotascale (August 2025)**, he moved into **freelance consulting with early-stage
  founders on LLM and B2B products**, alongside stay-at-home parenting — which is
  why Dec 2025 onward had the time. Not "unemployed."
- **The spine is the bot journey, and it's a change-of-mind story, not a triumph.**
  **ChaosBot** (one big prompt — great demo, but statistically random: AA 40% / 22
  57%, uniform, model-fragile, "emotional and dramatic"; *and the same equity calc
  made and graded the decisions*) → **HybridBot** (bounded "choose-your-own-
  adventure" menu; still folds the nuts; *"I was just making the decision for the
  LLM… which defeated the purpose"*) → **TieredBot** (deterministic decisions/
  weights/exploitation, **LLM demoted to narration**). The deterministic pivot is
  what made the game **affordable to actually run** and made sim harnesses, MTTs,
  and shapeable AIs possible.
- **The GTO road not taken:** nearly built a solver (~$50k compute; plan doc still
  in the repo) before concluding **an unbeatable bot isn't fun — people want to find
  leaks.** That's the game's thesis.
- **The hook, in his words:** "play against anyone you want, and they have a memory
  and learn how you play over time… put the table on hold and come back and
  everyone's still there. The hook is progression and feeling like the world is
  alive." Cash mode "makes the stakes feel more real." The coach came from a long
  hunt for "a reason someone would play it more than once."
- **Where it stands:** live and shareable; so far only his **brother and dad** have
  looked. The blog is the first deliberate step outward.
- **What the 2023 origin really was:** "there was no Claude Code, so I actually wrote
  the functional-programming core poker engine myself," then used ChatGPT/other
  tools for the frontend. Started on OpenAI; a mix of LLMs now. (Confirms the
  reconstruction's "core identity predates the AI pair.")
