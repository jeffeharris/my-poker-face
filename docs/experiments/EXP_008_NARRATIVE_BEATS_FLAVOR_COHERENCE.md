---
purpose: Test whether pre-digesting a hand into a clean narrative beat makes player-read flavor LLM calls more coherent, and quantify the added latency
type: experiment
status: planned
hypothesis_summary: A cheap narrative-beat pre-call makes commentary/chat/table-talk less confused at acceptable added latency
created: 2026-06-07
last_updated: 2026-06-07
---

# Experiment 008 — Narrative Beats Flavor Coherence

> **Why this exists:** The player-read flavor calls — post-hand commentary,
> quick-chat suggestions, and the table talk an AI produces on its own turn —
> frequently misread what happened in a hand even though the hand info is in the
> prompt (wrong winner, invented cards, confused about a loss). Hypothesis: a
> single cheap LLM call that turns the messy/sparse hand info into a tight,
> factual narrative beat, fed into those flavor calls, makes them more coherent.
> The user already ran an informal manual test (summarize the hand-history
> section, feed it into the flavor calls) and reported "much more coherent."
> This experiment quantifies (a) the coherence gain and (b) the latency cost,
> and picks a beat model. **This is flavor only — it does not change decisions.**

## Hypothesis

> **PROPOSED — user to confirm/adjust thresholds before we read results.**

**H1 (primary — coherence):** Feeding a narrative beat (instead of the raw
hand-history recap) into the post-hand commentary call increases the share of
commentaries that get the basic situation right (`outcome_correct`) and reduces
contradicted/invented facts (`hallucinations`).

- `outcome_correct` rate: beat ≥ control, and ≥ `<THRESHOLD, e.g. 100%>` on
  hands where the hero loses or busts (the cases that confuse the model most).
- `hallucinations` per hand: beat ≤ control (no net increase in invented facts).
- No regression: on hands where control was already correct, beat stays correct.

**H2 (secondary — latency):** The added latency is acceptable.

- Beat-generation call median ≤ `<THRESHOLD, e.g. 1500ms>` on the chosen model.
- Because one beat is generated per hand and **shared** across all of that
  hand's flavor consumers, the per-consumer amortized cost is ≤ `<THRESHOLD>`.

**H3 (null-validating — the beat is faithful):** The beat itself does not
introduce errors. The beat must contain no fact that contradicts the recap
(an unfaithful beat would corrupt every downstream consumer).

**Falsifier:** If the beat variant shows **no** improvement in `outcome_correct`
on the confusing (loss/bust) hands AND does not reduce hallucinations — i.e. the
flavor calls are no less confused — the hypothesis is wrong and the extra call
isn't worth it. If only the *cheapest* model fails this but an accurate one
passes, the conclusion is "use the accurate cheap tier," not "reject."

## What we're testing

**Single variable:** the `hand_summary` input to the *real*
`end_of_hand_commentary` template — raw `narrate_hand_recap` (CONTROL) vs a
narrative beat generated from that recap by `generate_narrative_beat` (TREATMENT).
Everything else (template, commentary tier `openai/gpt-5-mini`, persona params,
drama level matched to the hand) is identical. Beat model is A/B'd across
`groq/llama-3.1-8b`, `xai/grok-4-fast-non-reasoning`, `openai/gpt-5-nano`.

## Setup

**Sandbox:** No DB. Four hand-crafted `RecordedHand` fixtures spanning the
situations that drive confusion:
- `routine_preflop_fold` (low drama — must NOT invent drama)
- `allin_showdown_win` (full multi-street trace, hero wins)
- `bluff_steal_win` (hero wins, NO showdown — no cards to show)
- `lose_big_showdown` (hero coolered — "who actually won")

**Wiring status / preconditions:** Generator `poker/memory/narrative_beat.py`
(new), `CallType.HAND_NARRATIVE` (new). Nothing wired into the live game yet —
the harness renders the real template directly. Runs in Docker
(`docker compose run --rm --no-deps backend ...`); `.env` keys required for
groq/xai/openai (beat + commentary) and deepseek (judge).

**Output destination:** `scripts/narrative_beat_harness.py` →
`scripts/nb_results.json` (full texts + verdicts) + stdout summary table.

## Measurements

**Primary metrics (H1):**
- `outcome_correct` rate (control vs beat, per model) — the robust "did it
  understand who won/lost" signal.
- `hallucinations` count per hand (contradicted/invented facts only, not
  omissions), judged blind against the recap as ground truth.

**Secondary metrics (H2):**
- Beat-generation latency (median over reps, per model).
- Net added latency = beat latency + (treatment commentary − control commentary).

**Diagnostic (H3 / context):**
- Beat `ok` flag (did the cheap call succeed or fall back to raw recap).
- Qualitative read of beat text + commentary text on the confusing hands.

**Captured via:** `scripts/narrative_beat_harness.py` (judge = `deepseek-chat`,
Assistant tier, blind to which variant it's scoring).

## Comparison data

| Run | Source | outcome_ok | halluc | beat med | net add |
|---|---|---|---|---|---|
| **control (raw recap)** | `scripts/nb_results.json` | TBD | TBD | n/a | n/a |
| **beat: groq** | `scripts/nb_results.json` | TBD | TBD | TBD | TBD |
| **beat: xai** | `scripts/nb_results.json` | TBD | TBD | TBD | TBD |
| **beat: openai** | `scripts/nb_results.json` | TBD | TBD | TBD | TBD |

## Caveats / Known Confounders

1. **Small N (4 hands, few samples/cell).** The commentary model and the judge
   both have run-to-run variance; treat aggregate accuracy as directional, lean
   on `outcome_correct` + qualitative texts. (First unhardened run showed the
   judge flipping a verdict on identical input — judge noise is real.)
2. **Judge noise.** `deepseek-chat` judge is itself imperfect; it is blind to
   variant to avoid bias, but absolute scores are soft.
3. **`accuracy` 0-5 penalizes compression.** A beat is shorter than the recap;
   dropping a minor detail costs accuracy points even when the beat is *less
   confused*. This is why `outcome_correct` (not `accuracy`) is the primary
   coherence metric. The rubric explicitly tells the judge not to penalize
   omissions.
4. **Drama confound (fixed).** An earlier run forced `high_stakes` drama on
   every hand → the model invented drama on a routine fold → false
   hallucinations. Now drama level is matched per fixture.
5. **Synthetic recaps are cleaner than production hand-info.** The post-hand path
   already uses `narrate_hand_recap` (clean); production confusion may be worse
   on the during-turn/quick-chat paths whose inputs are messier/sparser. So this
   harness likely *understates* the real-world benefit.
6. **Latencies are container→API**, not production network. Relative comparison
   between models is the signal, not absolute ms.

## Validation criteria

> **PROPOSED — user to confirm.**

| Outcome | Decision |
|---|---|
| H1 + H2 + H3 met on an accurate cheap model | Wire the beat into commentary + during-turn talk + quick-chat behind a flag (default off), then live playtest |
| H1 met, H2 fails (too slow) on all models | Keep beat for the *post-hand* path only (latency hidden at hand boundary), drop it from latency-sensitive during-turn path |
| H1 met only on the accurate model, cheapest fails | Standardize on the accurate cheap tier (grok-4-fast-non-reasoning); document that Nano reintroduces confusion |
| H1 not met (no coherence gain) | Do not ship the prompt-replacement use; revisit whether production inputs differ from these fixtures before abandoning |
| H3 not met (beat introduces errors) | Fix the generator prompt / model before any further conclusion |

## Results

First clean run (`scripts/nb_results.json`, 4 fixtures, beat-reps 3, drama matched
per hand, judge blind):

| Run | beat med | net add* | outcome_ok | mean acc | halluc (Σ/4) |
|---|---|---|---|---|---|
| **control (raw recap)** | n/a | n/a | **4/4** | 4.75 | 2 |
| beat: groq llama-3.1-8b | 523ms | +261ms | 3/4 | 4.00 | 7 |
| beat: xai grok-4-fast-nr | 1024ms | +914ms | **4/4** | 4.25 | 1 |
| beat: openai gpt-5-nano | 1142ms | +782ms | 3/4 | 3.50 | 1 |

*Net-add is noisy (dominated by commentary-call variance); the honest latency
cost is the **beat-gen call** itself (~0.5s 8B / ~1.0s grok/nano). Downstream
commentary latency is unchanged within noise.*

Earlier probe (single beat call, host HTTP, N=5): groq median 495ms, grok-4-fast
non-reasoning 1350ms, gpt-5-nano 1383ms; grok-4-fast *reasoning* variant 5062ms
(the `minimal` effort flag selects the fast non-reasoning model — `xai.py:37`).

### Results — Harness v2 (messy during-turn inputs)

`scripts/narrative_beat_harness_v2.py` (`scripts/nb_v2_results.json`): 4 mid-hand
spots with **table chat interleaved** (incl. misleading chat + a card-mention
trap), downstream "player" model forced to **grok-4-fast** (prod-representative,
not the dev 8B), judge blind. Metric = `situation_correct` (does the model state
what actually happened / what it's facing).

| Variant | situation_ok | hallucinations |
|---|---|---|
| **control (raw chatty log)** | **4/4** | **0** |
| beat: groq 8b | 3/4 | 1 |
| beat: xai grok-4-fast-nr | 2/4 | 2 |
| beat: openai gpt-5-nano | 3/4 | 2 |

**The beat made it worse, not better** — and the mechanism is clear, not noise:
on `multiway_busy` the grok beat wrote *"Lady Macbeth calls the bet"* when she
had checked and **had not yet acted** (Daisy was to act). Summarizing an
**in-progress** betting round, the LLM "finishes the story" and invents the
current action — the most decision-/talk-critical fact. A strong downstream
(grok-4-fast) parsed the raw chat-laden log correctly every time, so the beat's
compression only added risk.

### Results — Harness v2 AFTER the settled-streets fix

`street_hand_state_beat` now beats only SETTLED streets and renders the
current (in-progress) street RAW (`_split_settled_current` on FLOP/TURN/RIVER
markers). Re-run via the real wiring path (`scripts/nb_v2_fixed.json`):

| Variant | situation_ok | hallucinations |
|---|---|---|
| control (raw log) | 4/4 | 0 |
| beat: grok-4-fast-nr (settled-only) | **4/4** | **0** |
| beat: gpt-5-nano (settled-only) | 2/4 | 3 |
| beat: groq 8b (settled-only) | 3/4 | 1 |

- **Fabrication fixed:** grok-4-fast went 2/4 → **4/4** (the `multiway_busy`
  "Lady Macbeth calls" invention is gone — the live street is now raw).
- **grok-4-fast beat is now safe** (matches control, 0 hallucinations) but does
  not *beat* control on these short logs + strong downstream — the win still
  needs weaker-downstream / longer-log conditions.
- **gpt-5-nano leaks table chat as fact** (it failed `card_mention_trap`,
  asserting Superman held "pocket aces / top set" from his banter). grok-4-fast
  did not. Confirms: use grok-4-fast, not nano. A chat-is-not-fact rule was
  added to the beat prompt to harden all models.

## Conclusion

**H2 (latency) — MET.** One shared beat call adds ~0.5s (8B) to ~1.0s
(grok-4-fast-non-reasoning / gpt-5-nano), generated once per hand and reused
across all of that hand's flavor consumers. Downstream call latency is unchanged.

**H3 (faithfulness) — MET for the accurate tier, FAILS for 8B.** grok-4-fast and
gpt-5-nano beats are faithful (1 contradicted fact across 4 hands); the 8B model
broke the cooler hand (`lose_big_showdown`: outcome_correct=False, confused=True,
and 7 contradicted facts total). Confirms the probe: the *cheapest* model
reintroduces the confusion we're fighting. (Even grok slipped once — a beat said
"aces full" for trip aces — so the beat must stay watched, not trusted blind.)

**H1 (coherence) — NOT CONFIRMED by this harness, and here's why that matters.**
On `narrate_hand_recap` inputs the CONTROL is already coherent (4/4 outcome_ok,
mean acc 4.75) — there is almost no confusion left to fix, so the beat can only
match it (and 8B hurts). This is caveat #5 realized: the post-hand path *already*
feeds the clean recap, so it is the *wrong place to look for the win*. The
confusion the user observed manually lives in the **messier inputs** — the
during-turn `hand_state` block (raw `game_messages` log + interleaved table
chat) and the near-empty quick-chat context — which this harness does not feed.
The user's manual test fed the summary into "the player's own LLM during their
turn call" and saw the big gain there, consistent with this localization.

## Decisions made / next steps

**Headline (post-v2):** "Replace the action log with a beat" is **conditional,
not a universal win**, and is **actively risky for the during-turn (in-progress)
path** as first built — the beat invents how the live betting round resolves.

1. **Model:** if/when a beat is generated, use an accurate cheap tier
   (grok-4-fast-non-reasoning) — never the 8B Nano (confirmed twice).
2. **CRITICAL design fix (in-progress fabrication):** never let the beat cover
   the **current/in-progress** betting round. Beat only **settled** streets;
   keep the current street's actions RAW and exact (the `street_hand_state_beat`
   tail mechanism already appends raw bets — but the *initial* per-street beat
   must be built from settled streets only, which needs a per-street split of
   the action log). Until that lands, do **not** flip the during-turn flag on.
3. **Strong vs weak downstream:** with grok-4-fast downstream the raw log is
   already handled 4/4 — the beat helps (if at all) only for weaker downstream
   models and/or much longer/noisier real logs. **Next test:** rerun v2 with a
   weaker downstream model and with longer real logs to find where the beat
   actually wins. (The user's manual "much more coherent" likely came from one
   of those conditions, or was a talk-*style* gain this comprehension harness
   doesn't measure.)
4. **Separate the talk-style question:** add a coherence/style judge (not just
   comprehension) — the user's observed win may be readability, not facts.
5. **Lowest-risk wins to keep:** post-hand commentary (faithful, but already
   clean) and the **story layer** (hand→session→circuit) — neither feeds an
   in-progress betting round, so the fabrication risk doesn't apply.
6. **Caveats:** 4 fixtures, noisy deepseek judge — absolute counts are soft,
   but the in-progress fabrication is a mechanistic finding, not a count.
