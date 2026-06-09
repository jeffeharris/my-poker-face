---
purpose: Session log — prod performance triage + full reboot, then the bankroll cache self-heal fix (PR #256)
type: guide
created: 2026-06-09
last_updated: 2026-06-09
---

# Prod perf triage → reboot → bankroll cache self-heal (2026-06-09)

Started as a routine "sync with main," turned into a prod ops pass and a chip-custody
fix. Recording it because two of the turns are the kind that read as clean in hindsight
but weren't at the time: a `reset --hard` on a branch another session was actively
moving, and an agent review that was technically right but easy to over-fix.

## How it started

Synced `chat/drama-gated-talk-volume` with `origin/main` (clean ort merge, 9 commits,
no conflicts). Then: "take a look at production for performance issues."

## Prod triage

Box is a 2GB / 2-core Hetzner instance, 156 days uptime. Health 200, external TTFB
~230ms — fine at the edge. The real signals were internal:

- **Single gunicorn worker (`-w 1`, gevent-websocket).** Gevent buys async I/O
  concurrency for idle sockets but does nothing for CPU-bound work. AI decision
  computation (tiered bot / bounded options / psychology) is CPU-bound Python holding
  the GIL, so an AI turn pegs one core while the second sits idle. That's the
  0% → 100% → 0% `docker stats` oscillation, and it's why `POST /action` was taking
  5.5s, 11.9s, 14.4s — one game's AI turn stalls every other client behind the one
  worker. The highest-impact issue, but not a one-line bump (multi-worker needs the
  Socket.IO Redis message queue + sticky sessions, and memory is tight).
- **Upstream LLM latency.** One commentary call ran 21.5s on gpt-5-mini, 2 timeouts +
  a retry. The 8s commentary guard is doing its job. The drama-gate work already on the
  branch reduces this pressure by cutting commentary volume.
- **Bankroll cache divergence spam.** 33 identical `[CHIP_CUSTODY] player bankroll
  cache divergence` WARNINGs in 4000 log lines, all one player, same 657-chip delta.
- **Memory pressure.** 95Mi free, 763Mi swap on the 2GB box after 156 days up.

## The reboot

User chose a full OS reboot (there was a live player mid-hand; flagged it, they
accepted). Verified containers were all `unless-stopped` and docker `enabled` before
pulling the trigger, so they'd self-start. Came back in ~55s, health 200. Swap
763Mi → 23Mi. Honest framing given to the user: the reboot bought headroom but fixed
**none** of the root causes — the worker bottleneck and the cache spam will recur.

## The cache fix (PR #256)

Diagnosis: `_derived_or_cached_player_chips` already *returned* the ledger-derived
balance (the authority) on every read, but never wrote it back to the denormalized
`player_bankroll_state.chips` cache. So every read re-derived and re-warned for the
same stale row forever. The displayed value was never wrong; the bug was purely
read-side noise + wasted compute.

Fix: when divergence is detected, reconcile the row in place on the caller's already-
open connection (joins the in-flight read transaction — no second connection, no lock
contention). Warning now fires once per drift instead of forever. Added a behavioural
test asserting the underlying row is rewritten (not just the returned value) and
`starting_bankroll` is preserved.

**The honest-question moment.** User asked: "is it hiding something or is it an actual
fix?" Good challenge. Answer: actual fix, but read-side only. The value served was
always ledger-correct, so nothing about the displayed balance is being papered over.
And it doesn't silence a *recurring* desync — a fresh drift still warns each time
(heal-then-warn), so monitoring keeps working. What it explicitly does **not** do is
fix whatever write path drifted the cache int from the ledger in the first place
(a separate latent bug, possibly related to the in-flight house-stake / aspire-funding
reconcile work). Said so plainly rather than claiming a complete fix.

## Wrong turn #1: reset --hard on a contested branch

Cherry-picked the fix onto `fix/bankroll-cache-self-heal` off `main`, then went back to
`chat/drama-gated-talk-volume` and ran `git reset --hard HEAD~1` to remove the fix from
there. The branch top wasn't what I expected — a **parallel session** had, while I was
working, committed `f69274b5` (live admin talk-volume dial) and amended the sync merge
to `ecd93bb8`. So my fix's parent was `f69274b5`, not the merge commit I assumed.

The reset still did the right thing (HEAD~1 from my fix landed exactly on `f69274b5`),
but I only *confirmed* that after the fact via `git reflog` + `git branch --contains`.
Lesson: `reset --hard` on a branch with concurrent activity is a coin-flip unless you
read the reflog first. Verify what HEAD~1 actually is before destroying it, not after.
No work was lost, but it was luck-adjacent.

## Wrong turn #2 (averted): the over-fixable agent review

An AI review flagged that the heal logs past-tense `"reconciled"` *before* the write's
transaction commits — so a commit failure (disk full / I/O) would roll back after a
success-sounding line was already emitted. Technically correct on the mechanics.

The trap was to "fix" it by restructuring to log post-commit, which would mean either a
second connection in the hot read path or committing the cache write independently of an
enclosing `transaction()` (breaking atomicity). All of that to chase a self-correcting,
loud-on-real-failure observability nit. Resisted. The proportionate fix was wording:
past-tense "reconciled" → present-tense "drift … healing on read transaction." Describes
the in-flight attempt, asserts no durable persist, zero structural change. Recorded the
severity calibration (low) in a PR comment so the next reader doesn't re-litigate it.

## State at end of session

- Prod rebooted, swap cleared, all containers healthy. Root causes unaddressed by design.
- PR #256 open against `main`: self-heal fix + log-wording follow-up, tests green.
- Follow-ups noted, not done: the worker-count bottleneck; the upstream write-path desync
  that creates the drift; the identical warn-forever bug on the **AI** bankroll read path
  (`bankroll_repository.py` ~line 104), left out to keep the PR scoped to the prod symptom.
