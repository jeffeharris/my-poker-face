---
purpose: Design for a poker "tells" system — learnable, exploitable, per-personality signals that leak an AI's hidden state, built on the psychology/expression engine
type: design
created: 2026-05-25
last_updated: 2026-05-25
---

# Tells System

## Concept

A **tell** is a consistent, decodable mapping from an AI's *hidden* state
(hand strength, emotion, or intent) to an *observable* behavior — signal
leaking through. The player can **learn** a tell by watching an opponent
and **exploit** it for an edge. The depth comes from tells being
**per-person, noisy, learned against a baseline, and sometimes faked**.

Tells are the marquee high-value intel in the opponent dossier (see
`OPPONENT_DOSSIER_PROGRESSION.md`) — the hardest-earned, most valuable bit.

**Anti-goal:** a universal "quiet = strong" cheat sheet that's solved in
one session. The whole point is that you have to study *this* opponent.

## Design principles (how tells work with people)

1. **Baseline + deviation.** A tell is only readable relative to a
   person's *normal*. You can't spot a deviation until you know the
   baseline — which is exactly why learning a tell takes observation.
2. **Per-person.** "Goes quiet" = monster for one player, nerves/bluff for
   another. Tells are individual, not universal.
3. **Reliability varies / never 100%.** A tell is `p(honest | trigger)`,
   not a certainty. Some fire reliably, some are noisy, some only in big
   pots.
4. **Deception / reverse tells.** Skilled opponents *fake* tells to punish
   anyone reading them. This creates levels of player and a reason to keep
   studying.
5. **Timing & sizing over twitches.** With people, the *real* tells are
   bet timing (snap vs. tank) and sizing patterns — and those are the
   channels a card game can model honestly. Physical twitches are
   flavor-only here.
6. **Degrade once acted on (optional).** Exploit a tell visibly and a
   sharp opponent adjusts.

## What the engine already gives us (substrate)

The pieces exist; nothing decodes them into a learnable signal yet.

- **Hidden state** per AI: `confidence`, `composure`, `energy`,
  `emotional_intensity`, `zone` (`poker/psychology_model.py`,
  `poker/emotional_state.py`). Plus the actual hand strength the bot knows.
- **Leak filter**: `poker/expression_filter.py:calculate_visibility(
  expressiveness, energy)` — how much internal state escapes into visible
  output. The code says outright: *"low expressiveness = poker face."*
- **Observable surfaces**: the `dramatic_sequence` (table talk + actions),
  bet **sizing** (chosen in the decision path), and decision **timing**
  (would need a synthetic "thinking time" signal).
- **Anchors that should generate a tell profile**: `poise` (composure
  stability → involuntary leakage), `expressiveness` (loud vs. poker
  face), `ego` / `adaptation_bias` (capacity for deliberate deception).

Today the leakage is **flavor** (mood-colored prose), not a **decodable,
ground-truth-linked** signal. Turning it into the latter is the feature.

## Anatomy of a tell (data model)

```
Tell {
  trigger:    what hidden fact it correlates with
              — hand_class (monster | value | marginal | air/bluff)
              — or state (tilted | confident | scared)
              — or intent (about_to_bluff)
  channel:    how it manifests
              — talk_tone | talk_content | chattiness_shift
              — bet_sizing | bet_timing | emote
  mapping:    the correlation, e.g. "quiet → strong", "fast/large → bluff"
  reliability: p(tell fires honestly | trigger)   # 0..1, < 1 always
  salience:    how noticeable → drives how many observations to learn it
  is_reverse:  character sometimes/always fakes this one
}
```

Each personality has a **tell profile**: 0..N tells.

### Key scoping decision: profile is global, knowledge is per-sandbox

The **tell profile is part of the personality** — Napoleon gets chatty
when bluffing in *every* save. That's consistent with the dossier doc's
principle that the personality is the one thing shared across sandboxes.
What the player has **learned** about Napoleon's tells is **per-sandbox
dossier intel** (`(sandbox_id, observer, opponent)`). Clean split:
character owns the tells; the save owns your knowledge of them.

## Generating a profile (from anchors, + hand-authored)

Default **procedural** profile derived from anchors, so every personality
has plausible tells for free:

- **Low poise** → involuntary leakage tells (nervous, higher reliability,
  easier to spot). The leaky fish.
- **High expressiveness** → talk-channel tells (chatter you can mine) —
  but for skilled characters this becomes deliberate / reverse.
- **High poise + low expressiveness** → few or no tells = the poker face,
  the genuinely hard read (a "boss" opponent).
- **High ego / adaptation_bias** → reverse tells, tell-hiding, tells that
  shift once you exploit them.

**Hand-authored overrides** for signature characters (a celebrity who
*always* does a specific thing) layer on top of the procedural default.

## Manifestation (engine integration)

When the AI acts, `(hidden state + hand class)` feeds the tell profile; a
fired tell colors the chosen observable channel:

- **talk**: bias the `dramatic_sequence` content/tone (prompt hint or
  post-generation filter).
- **sizing**: nudge the bet size within the already-allowed set.
- **timing**: synthesize a thinking-time delay.

Gated by `calculate_visibility(expressiveness, energy)` and a reliability
roll (the tell may not fire, or may fire **falsely** = a reverse tell).

**Non-negotiable:** a tell must be **ground-truth-linked** — it actually
correlates with the hidden fact — or it's just noise and can't be learned.
This is the core engineering constraint: the manifestation step has to key
off the real hidden state, not decoration applied afterward.

## Learning (ties to the dossier)

- **Observation accrual**: each hand, record `(observed channel signal,
  revealed hidden fact)`. Confirmation mostly comes at **showdown** (when
  the hidden fact is revealed); pre-showdown is weaker inference.
- A tell **unlocks** when confidence crosses a threshold (enough
  confirming observations) — the **grind** path. Or **buy** it from the
  informant — the shortcut path.
- **Baseline first**: you need enough observations of the character's
  *normal* before a deviation reads as signal — which is why the earliest
  hands reveal nothing (and dovetails with the dossier's minimum-hands
  floor).
- Depends on the dossier's **Phase 1 persistence** (per-sandbox observation
  storage) — tells are just a structured, high-value class of that intel.

## Exploitation & counterplay

- A **known** tell surfaces a real-time hint at the table ("Napoleon went
  quiet — strong here, ~85%") and acting on it is +EV (fold to the
  monster-tell, call the bluff-tell).
- **Reverse tells** punish over-reliance: a tell you "know" that's actually
  faked burns you until you learn *that* it's a reverse tell (a meta-unlock
  — you discover the character Hollywoods).
- **Adaptation**: high-ego/adaptation characters can notice you exploiting
  and shift or burn the tell — optionally relationship-driven (the more you
  punish them, the more they adjust).

## Scope — what to build first

Prove the loop end-to-end with **one channel** before fanning out. Channel
trade-offs:

- **Sizing tell** *(recommended first)* — crisp numeric signal, the most
  "real poker," trivially correlatable, and the bot already chooses sizes.
  Easiest to make ground-truth-linked and easiest for the player to verify.
- **Talk tell** — most on-brand (uses the existing `dramatic_sequence`),
  but decoding fuzzy prose is harder for the player and harder to score for
  the learning loop.
- **Timing tell** — needs a synthetic thinking-time system first.

Lean: ship the **sizing** channel first to validate manifest → learn →
exploit, then add talk and timing.

## Open questions

1. **v1 channel(s)**: sizing only, or sizing + a coarse talk tell?
2. **Profile source**: procedural-from-anchors, hand-authored, or both
   (recommended both)?
3. **Learning signal**: showdown-only confirmation, or also non-showdown
   inference (riskier, noisier)?
4. **Self-awareness**: which archetypes can *deliberately* reverse their
   tells — all, or only high-ego/adaptation "skilled" ones?
5. **Reliability display**: show the player a % confidence, or keep it
   fuzzy ("usually")?
6. **Reverse tells**: always-on per character, or situational?
7. **Bot-type coverage**: do all bot types (`lean`/`standard`/`sharp`/
   `casebot`) get tells, or only the ones with a psychology object?
   (RuleBot/fish have limited psychology — see existing notes on
   `enable_psychology` and CaseBot.)
8. **Do tells apply to fish/whales?** A fish's loose play is itself a
   "tell" of sorts; do they get extra leaky tells (easy marks), and does
   that pair with the archetype-bit reveal in the dossier?

## Phasing

1. **Tell data model + per-personality profile** (procedural from anchors;
   global, part of the personality).
2. **One channel manifest** (sizing) wired into the decision path,
   ground-truth-linked to hidden state + hand class.
3. **Observation accrual + learning + dossier unlock** (needs dossier
   Phase 1 persistence).
4. **Real-time hint UI** when a tell is known.
5. **Reverse tells + adaptation/counterplay.**
6. **More channels** (talk, timing).

## Relationship to other docs

- `OPPONENT_DOSSIER_PROGRESSION.md` — tells are the top-tier Exploit intel;
  the learn/unlock loop reuses the dossier's per-sandbox persistence and
  grind/informant mechanics.
- Engine substrate: `poker/psychology_model.py`,
  `poker/emotional_state.py`, `poker/expression_filter.py`,
  `poker/controllers.py` (dramatic_sequence), the bot decision/sizing path.

## The insight, restated

Tells are worth building only if they're **per-person, noisy, learned
against a baseline, and occasionally faked** — and if they're
**ground-truth-linked** so they're genuinely learnable. The tell *profile*
belongs to the (global) personality; the player's *knowledge* of it is
per-sandbox. That combination is what makes studying a specific opponent
pay off and keeps tells from collapsing into a solved cheat sheet.
