---
purpose: Narrative log of fixing the Scene-0 rig, rebuilding the tutorial around real famous hands, hardening cold-load, generalizing the scene system, and cleaning up the chat-bubble UX
type: reference
created: 2026-05-31
last_updated: 2026-05-31
---

# The Circuit — session 2: making Scene 0 actually work (and actually good)

Picked up from the M1 handoff. The headline task was "verify the live rig," but
the session turned into a real reckoning with both *correctness* and *quality*.

## The rig bug I'd misdiagnosed

I came in confident: "continuous play works; only cold-load breaks the rig." I'd
reasoned it from the code (the deck flag lives in-memory, survives the
get/set cache) and was ready to harden cold-load. Jeff reset and played a fresh
run — and got **random cards** (A7s, T6o, 72o) anyway. My analysis was wrong, and
he disproved it in about ninety seconds.

Ground truth from the live DB: the rig *was* firing — but the cards were going to
the **wrong seats**. The hero got the mentor's junk; the fish got the hero's
monster. Root cause: `reset_game_state_for_new_hand` **rotates the players tuple
every hand** to move the button (`new_players[dealer:] + new_players[:dealer]`),
but `_scene0_seats` was captured **once at init** and reused for every rigged
deck. By the teaching hand the snapshot was stale, so the seat-indexed deck dealt
the hero's `AK` to whoever now sat in seat 0 (Larry). Lesson: I'd reasoned about
the wrong layer entirely. The fix was a **name-keyed** deck seam
(`provide_hand_holes`) resolved against the live post-rotation seating — immune to
the button moving. Added a direct rotation regression test.

## "literally all the made hands you give me are AK… this is just poor effort"

Fair, and stated plainly. I'd shipped the plumbing and three teaching hands that
*all* dealt the hero `AK` with four terse Sal lines — none of the comedy, none of
the fish voice, none of the one beat that makes Sal *Sal*. I wrote an honest gap
audit (plumbing built; soul missing) rather than defend it.

Then Jeff steered the content, in four sharp messages that each reshaped the plan:
- **"find interesting hands by searching the internet… don't come up with your
  own scenarios."** Right — real famous hands have texture mine never would.
- **"grab a bunch — might use them at different times."** → a tagged library.
- **"solid lessons, not blind luck."** This filtered hard: Brunson 10-2 (sucked
  out a boat), Dwan-Ivey $1.1M (Ivey drew dead — a cooler), Mabuchi quads-vs-royal
  — all *lore*, not lessons. Tagged LORE, never used as a teaching spot.
- **"keep in mind we're playing a fish."** The killer steer. The famous hands are
  all pro-vs-pro; a fish tutorial can't hinge on reading a balanced opponent. So
  the pro reads (Robl folding the second nuts) became *later-arc* material, and
  the Scene-0 hands got cast so the **hero does what the legend got wrong** —
  Farha's fold (you call), Seidel's trapped call (you fold). That framing only
  emerged because of the constraints; it's better than anything I'd have invented.

Sourced exact cards with web search; flagged honestly that the Moneymaker–Farha
*board suits* genuinely conflict across retellings (one says "a fourth club" on an
all-spade board) and recorded only the reliable parts. Library lives in
`CASH_MODE_FAMOUS_HANDS_LIBRARY.md` with a SKILL/LORE + fish/pro tagging.

## "Sal said I flopped a 7 but he can't see my card"

In the rebuild I'd written the value-hand setup as "Look at that — you flopped a
set, kid." Jeff caught it immediately: Sal **can't see the hero's hole cards**.
A clean diegetic break I'd introduced. Fixed all three setups to pure principle,
and tightened pass/fail to respect *showdown visibility* — Sal only names what
Larry tabled when there actually was a showdown (i.e. when the hero didn't fold);
on a fold he speaks to the *tendency* ("a fish that barrels is usually air"), not
the mucked hand.

## Cold-load, generalization, and the bug cleanup

With the rig correct, the rest landed cleanly:
- **Cold-load durability**: scene position persisted to
  `career_progress.scene_progress[scene_id]`; `_init_scene` restores it on a
  cold-load instead of restarting at hand 0. Residual: a sub-second deal-window
  race that self-heals — documented, not chased.
- **Generalization**: lifted the Scene-0-specific handler into a reusable
  `TableScene` descriptor + registry (`cash_mode/table_scenes.py`); Scene 0 is
  now just the first consumer. A new scripted scene is a registration, not a
  rewrite. (The frontend floater is still Sal-name-specific — noted as a small
  follow-up.)
- **Two UX bugs Jeff spotted while playing**: (1) Sal's lines were being *dropped*
  — the delivery forwarded only the last AI message of a batch and the floater had
  a single slot, so the 3-line graduation reveal lost two lines. Made Sal a queue.
  (2) the floater rendered at a raw `z-index: 1200` (above modals) over the action
  bar; moved it to the HUD layer and raised the action bar above it. Also
  suppressed the cast's *regular* commentary during a scene (it was doubling with
  the scripted lines) and made inline `*blub*` render italic in the bubbles.

## Where it landed

Committed `1144fc2c` (40 files, +4547/−33), cash bucket green, TS + eslint clean,
not pushed. The plumbing from session 1 all stood; this session made it correct
(the rig), good (real hands + voice), durable (cold-load), and reusable (scenes).

Recurring lesson for the log: I was twice too confident — the rig diagnosis and
the "good enough" content. Both got corrected fast by a reset-and-play and a blunt
read. Cheaper to have played it once myself before claiming either.

## Next

Push; then M2 (real relationship-driven `vouch_ready`) — starting with verifying
the regard-edge instrumentation exists so the thresholds are tuned from data.
