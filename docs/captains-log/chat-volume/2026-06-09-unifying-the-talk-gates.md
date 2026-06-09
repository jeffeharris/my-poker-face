---
purpose: Making the talk-volume dial live, unifying the in-hand + post-hand speak gates, and the git-hygiene cleanup that surfaced along the way
type: guide
created: 2026-06-09
last_updated: 2026-06-09
---

# Unifying the talk gates + a git cleanup (2026-06-09)

Continues [the first entry](2026-06-09-drama-gated-talk-volume.md) (post-hand
drama gate, the rejected hard clamp, the first live dial).

## The live dial

Jeff didn't want to lose the *feel*, so instead of guessing a constant we made
the post-hand weight a runtime `app_settings` value (`DRAMA_SPEAK_SCORE_WEIGHT`,
read per hand-end) with an admin Settings → Gameplay slider — tunable without a
restart. Round-trip verified: writing the DB value flips the gate live.

## Wrong turn: the second registry (and a port goose-chase)

Jeff: "I'm 100% sure it is not there." He was right. I'd added the `gameplay`
category to `UnifiedSettings` (tab showed up) but missed a **second** list —
`VALID_SETTINGS_CATEGORIES` in `AdminRoutes.tsx`, the route guard. Clicking
Gameplay navigated to `/admin/settings/gameplay`, the guard didn't recognise it,
and it silently fell back to Models. Same class of miss as any "add to the enum
AND the registry" bug — I verified code/served-bundles instead of the rendered
page, twice, before actually looking.

Then a compounding confusion: it's an **admin** setting, not a player-facing one
(Jeff was looking in the player gameplay menu), AND the dev env was on the wrong
port — this host runs ~5 worktree stacks (5174 base, 5176 marketing, 5177
archetype, 5181 circuit…), and 5173 belonged to a different stack without the
work. Lesson: when "it's not there," confirm *which* surface and *which* port
before re-verifying code.

## The unification (the real ask)

Two systems decided "should this AI talk," computed completely differently. We
collapsed them onto one model (`poker/speak_gate.py`):

    speak_prob   = clamp(weight·drama + 0.4·(chattiness−0.5) + callout)
    gesture_prob = clamp(weight·drama + 0.3·(energy−0.5))

- drama is 0..1: `hand_score/100` post-hand; `MomentAnalyzer` level mapped
  (routine 0.10 → climactic 0.90) in-hand.
- Jeff's gesture insight was the unlock: a routine `*mucks*` is a **wasted LLM
  call** (on the tiered path, speak+gesture both False skips the expression call
  entirely). So gestures are now drama-gated too — routine fold/check ≈13%
  (was ~45% speech / up to ~65% gesture) → quieter table, faster play, lower
  cost. "check" / "mucks" stops being a paid beat.
- Dropped the `long_silence` boost (it literally rewarded talking in dead
  moments) and the dead `CONTEXT_MODIFIERS` dict. Kept rate-limiting,
  per-personality overrides, mime case.
- Callout exception: a player addressed/needled recently may still respond on a
  routine spot (`find_callouts` → `CALLOUT_SPEAK_BONUS`).
- Two live dials in the Gameplay panel: after-hand + in-hand. Shipped via #261.

## Git-hygiene episode (worth recording)

Closing out, the working tree was a mix of my changes and Jeff's parallel work,
and a few things needed care — all surfaced rather than steamrolled:

- **Staged deletions** of the PDA-completeness-monitor files + the
  metrics-hardening handoff doc showed up (not mine — a stale-branch artifact
  from the session's branch-switching). They were intact on `origin/main`;
  restored all four with `git restore`.
- **`vite.config.ts` looked like a change but was an accidental revert** — it
  removed the service-worker `navigateFallbackDenylist` for the marketing routes
  (the #260 fix that stops the PWA shadowing landing/opponents/blog). Caught it
  from the diff and restored main's version instead of committing the regression.
  Lesson: read the diff before committing "someone else's" staged change; if it
  contradicts how it's described, surface it.
- **Duplicate flag commit**: I direct-pushed the `TILT_CONDITIONING_ENABLED →
  BETA` promotion (`c5d90a00`), and PR #263 then did the identical change.
  Idempotent, so main is correct, but it's a redundant pair — the downside of
  direct-to-main vs a PR.

Built the #261 PR in an **isolated detached worktree off `origin/main`**, copying
in only my files, so none of Jeff's parallel work got swept into it.

## Still open
1. **Calibrate the in-hand dial by feel** — `MIDGAME_SPEAK_WEIGHT` default (1.3)
   and the level→drama map are an educated guess, not validated against a real
   `MomentAnalyzer` level distribution (post-hand was validated against 6k hands).
2. **Verbosity measurement** — re-run `scripts/chat_verbosity_baseline.py` after
   a session to confirm the prompt nudge actually shortened beats.
3. **Production deploy** — all merged to `main`; prod still on old behaviour.
