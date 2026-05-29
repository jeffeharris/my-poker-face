---
purpose: Grounded narrative log of building the headless multi-table tournament engine (branch tournaments)
type: reference
created: 2026-05-29
last_updated: 2026-05-29
---

# Captain's log — multi-table tournament engine (tournaments worktree)

Honest record of starting the WSOP-style multi-table tournament capability from
`docs/plans/MULTI_TABLE_TOURNAMENT_PLAN.md`. Newest entries at the bottom. Wrong
turns and corrections kept in, not just the wins.

---

## 2026-05-29 — brainstorm → plan → headless engine core

**Started as a brainstorm, not a build.** The ask was big and open ("tournaments,
eventually circuit/daily, buy-ins, staking, prestige, achievements, the ticker
ties it together"). Rather than guess, I fanned out three explorers over the
existing systems first. The useful finding that shaped everything: this is an
**assembly job, not a greenfield build** — the sandbox is already an isolated
world, `cash_tables` is already N-tables-per-sandbox with a `table_type`
discriminator, `TournamentTracker` already models eliminations→standings, the
world ticker + `lobby:{owner_id}` room is already a broadcast bus, the stakes
system already has the buy-in vocabulary. The missing piece is an orchestration
layer above the per-table loop. That reframing kept the plan honest about scope.

**Locked the decisions before designing.** Via a few rounds of questions the user
pinned: headless AI-only engine *first* (then wire the human in), 18–24 field /
3–4 tables, tiered/rule bots with **0 LLM cost**, no economy in v1, funny-money
chips, two decoupled ledgers (social carried in/out for career mode, chips always
isolated), and a live-pacing model of 0/1/2 AI hands per human hand. One thing I
got slightly wrong in the first question pass: I bundled "table fidelity" as the
headline fork, but the user's real framing was "multi-table *simulation* first" —
which made fidelity moot for v1 (all tables equally simulated). Re-asked and the
build order fell out cleanly.

**The architectural bet: field-as-source-of-truth + a pluggable resolver.** The
hard part the user wanted right is balancing/shuffling — a between-hands concern.
So I made the seating and standings layers pure data with zero engine dependency,
and made hand-playing a `HandResolver` interface. A `FakeHandResolver`
(deterministic, chip-conserving "everyone posts the blind, stack-weighted winner
takes it") runs whole tournaments without the engine or any LLM, so the
orchestration is testable and reproducible in 0.14s. Reading the engine first
paid off: `simulate_bb100.run_6max_matchup` *already* rebuilds the game state +
controllers fresh each hand from per-seat data — exactly the pattern I needed,
which de-risked "how do I move players between tables" (you don't; the field owns
seating, each hand is built from it).

**Then the conservation invariant earned its keep on the very first engine run.**
The fake path was green; the real-engine path immediately tripped the
`sum(stacks) == field_size * starting_stack` assertion (in=60105, out=59301). My
first instinct — "the tournament layer has a bug" — was wrong. The leak was in
**core `poker_game.py::determine_winner`**, pre-existing, invisible to every
existing eval because `simulate_bb100`/`sng_runner` use equal stacks every hand
and never assert exact conservation. The plan had flagged this invariant as the
safety net for exactly this; nice to see it fire on day one.

**A wrong turn chasing it — bad instrument.** My first diagnostic monkeypatched
`poker.poker_game.determine_winner` with a leak-detecting wrapper and ran the
leaking seeds. It reported *zero* leak — which would have sent me looking in the
wrong place. The wrapper was never actually called: the leak is real (a full
manual reproduction confirmed 1500 chips vanishing), but my patched module
attribute wasn't the reference the internal caller used. Echo of an earlier log's
lesson: verify the instrument before trusting its silence. The manual repro —
build the exact 6-seat state, run one hand, print every stack — was authoritative
and pinpointed it.

**Root cause + fix.** When an all-in short stack creates a main pot and only
*one* live player remains eligible for the side tier, the old code returned only
that lone player's own excess and stopped — but folded players had left "dead
money" in that tier, and since folded players never enter the active loop, those
chips stranded. Fix: the lone live player **wins** the folded dead money (capped
at their contribution); only genuinely uncalled excess is returned; plus a
post-loop safety sweep returns any residual folded over-contribution above all
live players (a broader instance of the same leak). I had to be careful to
*preserve* `test_multiple_side_pots_three_all_ins`, which deliberately asserts the
silent-return behavior — but that case has no folded dead money, so the fix
distinguishes them correctly. This touches **all** game modes (cash, SNG), not
just tournaments — a latent chip leak fixed everywhere.

**Verification.** 23 new tournament unit tests; 79 green across pot-distribution
(with a new regression test), tournament-flow, functional-poker, chip-flow; 59
more `determine_winner`-dependent tests green. 18- and 24-entrant tournaments run
to a single winner on both resolvers with conservation asserted every round (the
24-entrant engine run held across all 60 rounds of unequal-stack all-ins).

**Deliberately deferred.** Per-table dead-button realism, eliminator attribution,
results persistence, and the live-human seam — all Step 2+ in the plan. Kept this
first cut to: prove the orchestration, prove conservation, fix what conservation
exposed. Nothing committed yet; the engine fix arguably wants its own commit given
its blast radius.

## 2026-05-29 (later) — commit, event log, then realism

**Committed in three slices**, with the core engine fix on its own (`96d6f7d0`)
ahead of the scaffold (`3223043d`) precisely because it touches every game mode,
not just tournaments — a reviewer should see it isolated.

**Event log + eliminator attribution (`ec99187c`).** Added a `RoundReport` per
round (level, eliminations, seat moves). The seat moves were already computed by
the rebalance and silently discarded — capturing them is the raw feed the ticker
and the standings view will render. Eliminator attribution is a deliberate
heuristic: the biggest live chip-gainer at the busted player's table that hand.
It's not always the literal knockout in a multiway pot, but it's resolver-agnostic
(works for fake and engine alike without reaching into either) and good enough for
v1 prestige. Flagged the heuristic in the code rather than pretending it's exact.

**The persistence fork — and a good user call.** I'd lined up results persistence
as the next step. The user pumped the brakes: don't persist yet, because we don't
yet know the *shape* of the data we'll want — and that shape will fall out of the
standings UX (an in-game tournament menu you back out to). Correct instinct;
building a schema before the read patterns exist is how you get a migration you
regret. Logged it as deferred.

**A real design decision surfaced: player-gated time.** The user added that when
you back out to the standings menu, the **whole world pauses** — no other table
advances, the blind clock stops, nobody busts. That's the *opposite* of the
cash/career world ticker (which keeps ticking while your table waits). It's the
right model for a tournament — your position is too consequential to move while
you're reading — and it happens to be exactly what the director already is: a
round only advances by an explicit step, no background thread. Recorded it in the
plan so Phase 2 doesn't accidentally bolt a ticker onto tournaments.

**Realism now, since persistence is on hold (`4e086536`).** Reworked the table
model from an occupied-only list to **fixed seat positions** with a **seat-based
button** that moves forward to the next occupied seat (snapping past a seat a
player just vacated). Stopped short of full casino dead-button: the engine derives
blinds from the dealer index over the seated players, so a button resting on an
empty seat would fight it for no v1 benefit — documented the limit rather than
half-implementing it. The one test that had asserted `button < size` was now wrong
by construction (button is a seat index, not bounded by the occupied count); fixed
it to assert the *resolved dealer index* is valid instead — a small reminder that
when the model gets more realistic, the old invariants need re-reading, not just
re-running.
