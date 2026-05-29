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

## 2026-05-29 (later still) — the live-human seam, headless first

**Built `TournamentSession` and resisted the urge to start with the UI.** The
temptation with "let's go" on Phase 2 was to start wiring React + Flask + sockets
so there'd be something to look at. I deliberately didn't — the hard, risky part
of Phase 2 is the *seam*: pacing the AI field to the human, pausing the world when
they step away, and relocating the human across table breaks without resurrecting
the ghost-seat bug class. So I built that as a pure, headless-testable coordinator
and simulated the human with the same FakeHandResolver, deferring all UI.

**The relocation worry evaporated — by design, not luck.** I'd flagged human
relocation as the top risk (it's the cash-mode ghost-seat class). But because the
whole engine is field-as-source-of-truth, the human is just another entry in the
one seating model; moving them is the *same* atomic op as moving any AI, and
`seating.table_for(human)` is always the truth. There was no special human-move
code to get wrong. The earlier architectural bet paid a second dividend here.

**Pacing + the blind clock after the human busts.** Two things needed care.
(1) The 0/1/2 burst can bust a player mid-burst, which would leave a dead seat in
the table while the next hand in the burst tried to compute a dealer index over
it — so a burst stops the moment a hand busts someone (the dead seat is cleared at
round end). (2) "Human hand = the clock tick" is true while the human plays, but
after they bust I still need blinds to keep rising as the field fast-forwards — so
the clock runs off a single `rounds` counter that keeps incrementing through
`play_out()`, not off the human's hand count. Small, but either would have been a
subtle bug.

**Defined the standings data contract now, the UI later.** `standings_view()` /
`human_table_view()` return plain dicts (field counts, blind level, per-seat
stacks with button + human flags, recent knockouts) — pure reads that never
advance anything, which is also the world-pause guarantee in code form. Building
the contract before the React layer means the UI and the (still-deferred)
persistence schema can be shaped against real, tested output rather than a guess.

**Verified with the real engine, not just the fake.** Ran a session where both
the human callback and the AI tables go through `EngineHandResolver`: an 8-player
event ran 48 human hands, the human actually won, and conservation held every
round. Good enough confidence that the seam is sound before any wiring.

## 2026-05-29 (evening) — API, then UI; recon first, reuse always

**Reconnaissance before the API, on the user's nudge.** The user reframed the
scope crisply: the single-table game is already built and reused by cash + the
old single-table tournaments; the director/session is *only* the meta-layer
(seating, chip movement, standings, clock). "Use the same patterns unless
something's wrong." So before writing a line of API code I sent an explorer to
map exactly how cash mode coordinates the human's live table with the rest of
the world. The payoff was precise: the hook point is the *same*
`handle_evaluating_hand_phase` hand-boundary seam cash uses; my `EngineHandResolver`
is architecturally identical to `cash_mode/full_sim.py` (so: keep it, don't
reinvent); and relocating the human means *building a fresh game* at the new
table (the player set is per-`game_id`), with the seating model only telling you
*where*. That last point retired a worry — no risky mutation of a live game's
roster.

**API layer (2a).** Mirrored cash's shape: an in-memory `tournament_registry`
(twin of `game_state_service`) and `tournament_routes` (register / lobby /
standings / advance / play-out / leave). The director/session stays pure; the
routes are a thin shell over `standings_view()`. Deferred the deep game-handler
bridge to 2c per the agreed ordering — until then advance/play-out auto-resolve
the human's table so the UI has live data.

**A dead fixture, found the honest way.** The route tests tripped on the shared
`flask_app`/`flask_client` conftest fixtures — they patch a
`flask_app.extensions.persistence` attribute that doesn't exist, so they've been
quietly unusable. Rather than resurrect them, I did what the cash route tests do:
build the app via `create_app()` in a local fixture. My routes are DB-free so
that's clean. Noted the dead fixtures in the commit rather than pretending they
worked.

**UI (2b) via the frontend-design skill.** Committed to a single bold direction:
a *broadcast tournament clock / leaderboard* — deep felt-charcoal, championship
gold, knockout crimson, condensed Bebas Neue for the clock and tabular JetBrains
Mono for chip counts. The "field paused" pulse makes player-gated time visible
in the chrome itself. Mobile-first, two-up tables on desktop.

**Seeing it, since the dev server won't show it.** `/tournament` is auth-gated
and this project's dev server returns blank docs to headless browsers — a
documented gotcha. So I verified the design the reliable way: a standalone static
preview of the real CSS, served over `http.server`, screenshotted at phone and
desktop widths. It looked the part on the first pass — the clock band, the
"3rd / 28,450" hero, the gold-bordered YOUR TABLE with dealer-button discs and
the highlighted YOU seat, the crimson KO feed. tsc + eslint clean; preview
artifacts removed, not committed.

**Naming collision caught in passing.** The home menu already calls cash mode
"The Circuit," so I renamed the multi-table lobby to "The Main Event" and hung a
"Main Event (Beta)" entry off the existing single-table tournament menu rather
than inventing a new top-level card — least-surprise wiring.

## 2026-05-29 (night) — starting 2c by de-risking, not by diving in

The live bridge is the hardest, highest-blast-radius step — it edits the
production `game_handler`, the same hand-boundary machinery cash and single-table
games run through. This project's scar tissue (ghost seats, cold-load divergence)
is almost all from that surface. So I deliberately started 2c by building and
testing the *brain* in isolation rather than rushing edits into the handler:

- `apply_live_round(human_table_result)` on the session — the live analog of
  `play_round`, where the human's hand is already played by the real game and we
  just fold the result in, then pace the AI tables and settle. `_round` grew a
  `human_result` branch so the headless and live paths share one settle/rebalance
  body (no second implementation to drift).
- `tournament_handler.coordinate_after_human_hand` — a *pure* classifier:
  continue / relocated / human_out / complete. And `human_table_seat_specs` — the
  seat contract the builder and the continue-sync will both consume. Pure means I
  could unit-test relocation detection, the human-out guards, and conservation
  across a whole simulated event with a real session and plain dicts — 16 tests,
  no Flask, no browser.

What I intentionally did **not** do yet: touch `handle_evaluating_hand_phase`, or
write `_build_tournament_game`. Those need an *in-process integration test* (build
a tournament game, drive human actions through `progress_game`, assert the
boundary coordinates correctly) — not a browser, which this project can't drive
headlessly anyway. Wiring them blind into the production handler and hoping is how
the ghost-seat bugs got written; the tested brain + the seat contract are the
foundation that makes the wiring safe to add next.
