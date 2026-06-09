---
purpose: Tightening how often and how much AI players talk at the table — drama-gated commentary, a rejected hard-clamp, and a live admin dial
type: guide
created: 2026-06-09
last_updated: 2026-06-09
---

# Trimming AI talk volume (2026-06-09)

## The report

Jeff: "I have a sense the AIs are speaking too much. Too frequently and often too
verbose. Sometimes it's funny, but I want them funny with *less* words — no one is
reading all that. I try to and even I can't keep up." Asked for the current
thresholds, their history, and when they were last tuned.

## Baseline first (don't guess the feel)

Two systems: how *often* a player speaks vs how *long* each line is. Last real
volume tune was 2026-05-23 (`571734a3`) — flipped showdowns/all-ins from
"everyone always speaks" to chattiness-weighted rolls, added a 3-speaker cap, cut
the per-action gesture floor 0.40→0.15. Frequency got a haircut; **per-message
verbosity had never been deliberately tightened.**

Wrote `scripts/chat_verbosity_baseline.py` and measured `prompt_captures` on local
AND prod (Jeff offered prod ssh). The numbers backed his instinct — and the thing
he was "sure he was exaggerating" about, he wasn't:

- **End-of-hand commentary fires on ~96% of hands** (prod), ~2.0 speakers/hand.
- Action beats: prod p50 6 words, p90 **10**, max **41** — paragraph-gestures.
- Speech beats: p50 11, p90 16; sentences mostly 1 (p90 2) — so the bloat is
  *words per sentence*, not sentence count.
- Beat *count* was already fine (p50 2) — explicitly left alone.

The real culprit for "every hand" was an **unconditional `pot >= 5bb → speak`**
branch in `_should_speak`, not the drama rolls.

## Jeff's steer that shaped the fix

He pointed at the **circuit-narrative drama score** (`hand_score.score_hand`, the
0–100 scorer the journey already uses to rank a session's hands) — "that could
gauge if a hand is worth speaking about." It was the right lever: a principled,
hero-perspective drama signal already in the codebase, used for nothing at
hand-end. Calibrated a speak-probability curve against **6k real hands**
(`scripts/drama_gate_calibration.py`). Linear `1.3*(drama/100) + 0.4*(chat-0.5)`
won over hard-threshold curves — the thresholds flattened every personality to
~16% and killed the chattiness gradient that makes some characters livelier.

## The wrong turn: deterministic verbosity clamps

Built a hard clamp in the beat funnel — action beats truncated to 8 words, speech
cut to the first sentence — and wired it through the universal display chokepoint
so every controller got it. It passed the mechanical tests (counts, truncation).
Then Jeff asked the right question: **"do we still see their character coming
through?"** I had only measured rates, not voice. So I ran real prod beats through
the clamp and looked:

- Actions → dangling fragments: `*smirks at the crowd, eyes glinting like a*`,
  `*takes a stack of chips and sets it*`. Worse than the original.
- Speech → the first sentence is often the *setup*, so the cut deleted the joke:
  Chris Rock's whole bit collapsed to "Ah, nice try, Jeff."; Ms. Rachel to "Ha!";
  frida to "Ya see?".

A regex can't tell setup from payload. **Reverted both clamps entirely** (Jeff's
call: prompt-only for both). Verbosity is now a *nudge* in the prompt
("8-word actions, one short sentence") that the model composes around — zero
forced edits, character intact. Honest tradeoff: the nudge is unproven until we
re-measure on fresh hands.

## What shipped (PR #253, merged to main `da9c1be0`)

- **Frequency** — `_should_speak` gates on the drama score × chattiness. Verified
  end-to-end: routine hand (drama 8) speaks 2.5/11/19% (quiet/typical/chatty);
  dramatic hand (drama 69) 82/89/95%. From ~96% → ~44% of hands at chattiness 0.5.
- `MAX_UNCAPPED_SPEAKERS` 3 → 2.
- **Verbosity** — prompt nudges only (clamps rejected, see above).
- **Live dial** — Jeff didn't want to lose the *feel*, so `DRAMA_SPEAK_SCORE_WEIGHT`
  is now an `app_settings` value read per hand-end (`get_drama_speak_score_weight`),
  exposed as a Quieter↔Chattier slider in admin Settings → **Gameplay**. Tune by
  feel, no restart. Verified the live DB round-trip flips the gate without a
  bounce.

## Process notes / scars

- The running game did **not** pick up the Python changes until a backend restart
  (debug server wasn't reloading) — Python caches imported modules; editing on
  disk ≠ live. Confirmed local-vs-prod with Jeff before restarting his game.
- Hesitation on "feel" was legitimate and correct — frequency 96%→44% is a big
  swing, and the spam and the ambiance live on the *same* routine hands. The dial
  is the hedge: ship the direction, tune the amount by feel.

## Still open

1. **Verbosity nudge unproven** — re-run `chat_verbosity_baseline.py` after a play
   session to see if beats actually shortened.
2. **Gameplay slider never seen rendered** — type-checks + clones a working
   section, but no eyes-on yet.
3. **Production not deployed** — main has it; prod still chatty until `./deploy.sh`.
