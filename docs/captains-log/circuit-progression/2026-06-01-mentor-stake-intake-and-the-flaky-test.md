---
purpose: Narrative log of wiring Sal's mentor stake, unwedging the graduation handoff, polishing the Lucky Stack intake, and chasing a flaky test that wasn't mine
type: reference
created: 2026-06-01
last_updated: 2026-06-01
---

# The Circuit — session 3: Sal pays for the seat, the waitress learns to talk, and a flaky test eats an hour

Picked up from the session-2 handoff. The headline task was Next-step #1: **wire
Sal to actually stake the player into the home court** (the comp-return's other
half). That landed, but the session sprawled — a conservation fix that was bigger
than advertised, a graduation handoff that turned out to be wedged, a long detour
chasing a flaky test that wasn't my fault, and a lot of intake UX the user drove
in real time. Recording the wrong turns, not just the wins.

## The mentor stake, and a conservation hole bigger than the handoff said

The handoff scoped the fix narrowly: "in the mentor branch, debit Sal's bankroll
by the principal." But it pointed at a committed `xfail` test that used **Napoleon**
(a generic seated-eligible lender), not Sal. That mismatch was the tell.

I traced origination → settlement properly and found the bug was **universal, not
Sal-specific**: a personality stake set the player's table stack = principal with
**no debit anywhere**, and at settlement `staker_total` credited that principal
*back into the lender's bankroll* as permanent chips. So every personality stake
minted — Sal was just the case that couldn't hide it (non-seated lender).

This was a real fork: fix it generally (flips the Napoleon test, changes the cash
economy — lenders now actually pay principal up front) or only patch Sal. I didn't
guess; I put it to Jeff with the trade-off spelled out. He chose **general**. So
`_debit_personality_lender_principal` now debits any personality lender's projected
bankroll by the principal — a pure non-bank transfer that keeps the chip-ledger
audit flat, mirroring how seated AIs fund their own stacks. The Napoleon xfail
flipped to a real passing test.

Lesson reinforced: when a handoff's prescribed fix and the test it points at
disagree, the test is usually telling the truth. Trace the whole round trip before
trusting the framing — even your own past framing.

A satisfying detail: the audit test still showed a 15-chip "mint" after the fix.
I almost chased it as a bug. It was the **posted blinds sitting in the live pot** —
`compute_audit` sums seat stacks but not the pot, a documented v0 gap. Bounded the
assertion by the blinds instead of asserting exact zero. (Conservation: trust the
ledger, not the table.)

## The graduation handoff was wedged — and the test that wasn't mine

When Jeff played through and said "the handoff isn't working, wasn't Sal going to
stake me?" — the live DB told the story instantly: graduated, `comp_returned=False`,
and **still seated at the Scene-0 table** (an `active` cash session). Both the
comp-return and the mentor stake gate on `not has_active_session`, so a lingering
scene session wedges the *entire* post-graduation flow.

Root cause: the scene's teardown was best-effort on the *frontend* (`leaveTable()`,
45s queue-gated, silently caught) and skipped entirely on a cold-load or a manual
nav back to `/cash`. The fix is a server-side self-heal in `get_lobby`: when a
graduated player's active session is still their own Scene-0 table, settle + close
it (reusing the leave path), reload the bankroll, and let the handoff proceed.
Resilient to however the frontend leave failed.

### The hour I lost to a flake

Writing the regression test, the career-lobby suite started failing intermittently.
I built a whole theory: `pytest-randomly` shuffles order, two `unittest` classes
both clobber `flask_app.extensions` globals, the interleaving corrupts a DB read.
I added per-method ext snapshot/restore bracketing to make my class a "good
citizen." Still flaked.

Then I did what I should have done first: **measured the baseline**. Stashed *all*
my changes, ran the original file 10×. It failed **4/10 on a clean tree.**
`test_graduation_returns_the_comp_to_the_pool` is **pre-existing flaky** — and
`pytest-randomly` isn't even installed, so it was never about order. It's
**world-economy RNG churning the bank pool during `get_lobby`**, which breaks the
test's exact pool-delta assertions. My elaborate ext-bracketing was solving a
regression that didn't exist. I reverted it back to the standard pattern, kept the
one genuinely useful bit (evicting the in-memory game from the module-level
`game_state_service` store in tearDown), and isolated my new tests in their own
fresh-DB class.

Lesson (again): **establish the baseline before theorizing about a regression.**
I burned real time inventing causality for a flake I introduced zero of.

## "Larry's gone, it's dad jokes now"

Jeff: the Scene-0 fish seat had `a_guy_who_tells_too_many_dad_jokes` instead of
Loose Larry. The DB made it obvious. Two compounding causes:

1. **`_refill_cash_seats` didn't skip scripted tables.** When Larry busts — the
   finale busts him to 0 *by design*, or a short-stack mid-tutorial — the generic
   in-game refill replaced his seat with a random eligible persona. The world's
   lobby refresh already skipped scripted tables; this in-game path didn't.
2. **`loose_larry`'s global `circulating` flag had drifted to 1** (the JSON says
   `False`), so the eligible pool could pull the scene-only fish.

Fixed both (the refill now no-ops for scene games; pinned the flag), made
`reset_career.py` actually *rebuild* the cast (the old reset only normalized Sal
and left whatever stranger was in Larry's chair), and added a regression test. This
is the [[cash_seat_double_seat_recurrence]] family — a class, not an instance.

## The intake became a real conversation (Jeff drove this hard)

A long, satisfying back-and-forth turning the Lucky Stack cold-open from a form
into a scene:

- **The waitress floats and talks.** Reused the in-game "print style" beat
  renderer (actions fade in, speech types out). I extracted it from `FloatingChat`
  into a shared `DramaticText` so the waitress, Sal, the seat bubble, and the chat
  all share one renderer. Sal's lines now type out too, sentence-split for a beat
  after each.
- **Two layout wrong turns.** First I floated her *above* the modal; then, asked
  to put her "over the text," I overlapped her on the speech bubble's right — which
  read as "hovering awkwardly to the side." Jeff sketched what he wanted in ASCII
  and I finally got it: a clean vertical stack, centered portrait above a
  full-width speech box. Should have asked for the sketch sooner.
- **The replies stopped being a mechanic.** The Friendly/Cocky/Ruthless tiers
  mapped to a quick-chat setting nobody read (the `localStorage` write was dead).
  Jeff wanted *plain character flavor we can call back to later* — and crucially,
  **innocent of poker** (the newcomer doesn't know what game's in the back). Now
  three lines (coffee / game-for-anything / "folks say I'm hard to read"), the
  verbatim reply persisted as `intake_reply` for later callbacks.
- **Small real bugs he caught:** the name box "defaulted" to a greyed *placeholder*
  "Jeff" that wasn't a value (now prefilled from the account name); the "Sit down"
  button was wrong for answering a waitress ("Tell her"); typing reflowed the card
  (a hidden full-text ghost now reserves the height); and multi-beat text overlapped
  because the timing was **pre-computed from estimates** — made it event-driven so
  each beat waits for the previous to actually finish.
- **Land in the lobby, not the game.** Finally, the intake now drops the player into
  the lobby on the *single populated Scene-0 table* (keyring already filters to one;
  hid the venue tabs so an empty "Casino (0)" doesn't undercut it) rather than
  auto-sitting them into the hand.

## Where it stands

Pushed 10 commits to `origin/circuit-progression`. The mentor stake works
end-to-end (carve-out + general conservation fix + lobby payload + frontend route +
isolated tests). The graduation handoff self-heals. Larry stays Larry. The intake
is a scene.

Two honest loose ends:
- `test_graduation_…` is pre-existing flaky (world-economy RNG); it'll occasionally
  red the cash bucket until someone makes its pool assertion churn-tolerant.
- A `chore` commit bumped the dev docker subnet — environment-specific; revert it
  before any PR if it was only my machine's conflict.

Next from the handoff is still M2 (real `vouch_ready` over the relationship graph)
and a live finale verification. But the Act-1 spine — wander in, get christened,
play the room, graduate, get backed — is now playable front to back.
