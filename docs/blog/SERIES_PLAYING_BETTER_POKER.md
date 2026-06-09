---
purpose: Draft content plan for the second blog series, "Playing Better Poker" — fundamentals/strategy concepts, the SEO companion to the "Know Your Opponent" archetype series
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Series plan: "Playing Better Poker"

The second blog series. Where **Know Your Opponent** profiles *who* you are
playing (the archetypes), this series covers *how* to play: the fundamentals
and strategy concepts a player searches for directly. It is the highest-volume
evergreen SEO vein in poker (people search "pot odds", "poker position",
"when to bluff" constantly), and almost every concept has a real hook in the
game (the coach, the Range Explorer, per-decision EV, the dossier, the emotion
system, the table seat UI, the Circuit's stack dynamics).

**Track:** Inside the Table (player-facing, search-driven, conversion-oriented).
**Series field:** `series: Playing Better Poker` (reuses the in-post series-nav
infra built for the archetype series). Suggested `order:` 20–31 so it sorts
after the archetype set.
**House rules:** same voice as the existing posts (no em-dashes; go easy on the
"not X, it's Y" antithesis; grounded, not dramatic). Every post: a "how this
shows up in My Poker Face" beat, internal links to opponent pages / the
archetype series / the coach, and one CTA (`/login` to play, or open practice
mode for the coach-heavy ones).

## Why a second series

- **Different search intent.** Archetype posts catch "how to beat a [type]".
  These catch "how to [concept]" — a larger, more evergreen pool.
- **They cross-feed.** Exploitative play *is* the archetype series' thesis;
  the bluffing post points at "never bluff a station"; the reading-opponents
  post sells the dossier. Each series is the other's internal-link reservoir.
- **Coach funnel.** Several of these (pot odds, starting hands, tilt) lead
  naturally to practice mode, which is the strongest conversion surface.

## Posts (suggested publish order: fundamentals → advanced)

| # | Working title | Primary keyword(s) | Angle | MP F hook | Links to |
|---|---|---|---|---|---|
| 1 | **Why Position Is the Most Underrated Edge in Poker** | poker position explained; playing in position; the button | Acting last = free information; the single cheapest edge to add | The table seat layout; how the same hand plays differently by seat | Exploitative, Bet sizing |
| 2 | **Which Hands to Play: a Preflop Starting-Hand Guide** | poker starting hands; which hands to play; preflop ranges | What to play before the flop, and why "too many hands" is the root leak | Range Explorer grid + the coach's preflop-leak drills | Fish capstone, Pot odds |
| 3 | **Pot Odds and Equity: the Only Poker Math You Need** | pot odds poker; how to calculate pot odds; poker equity | The math of calling, made simple; price vs. chance of winning | The game computes per-decision EV/equity under the hood; the coach shows the call | Starting hands, Bet sizing |
| 4 | **Bet Sizing: How Much to Bet, and Why** | poker bet sizing; how much to bet | Value vs. protection vs. bluff sizing; sizing as a tell | Readability thesis: "a size that's never a bluff"; opponents whose sizing leaks (links to archetypes) | Bluffing, Reading opponents |
| 5 | **When (and When Not) to Bluff** | how to bluff in poker; when to bluff | Fold equity; a bluff is a bet that makes a better hand fold | Bluffing keyed to opponent type — the bridge back to the archetype series | Station, Nit, Maniac posts |
| 6 | **Exploitative Play: Play the Player, Not the Cards** | exploitative poker; exploitative vs GTO | Adjust to leaks; the meta-skill the whole archetype series teaches | The dossier / cross-session opponent model; the coach | **Whole archetype series**, GTO post |
| 7 | **Defensive Play: How Not to Get Run Over** | defending blinds poker; pot control; stop getting exploited | The flip side of exploiting: don't be the one being exploited; balance + folding discipline | Resisting tilt-driven hero calls; the bots that hunt your leaks | Fish capstone, TAG post |
| 8 | **Stack Depth: Why Short and Deep Stacks Are Different Games** | short stack strategy; deep stack poker; SPR (stack-to-pot ratio) | Strategy changes with stack size; shove/fold short, implied odds deep | Cash vs. tournament stack dynamics; the Circuit; rebuy/short-stack spots | Position, Pot odds |
| 9 | **How to Read Your Opponents (and Their Tells)** | poker tells; how to read opponents; online poker tells | Timing/sizing tells; thinking in ranges; the read that persists | **Signature piece:** emotion-is-visible, the dossier, the persistent cross-session read | Reading-opponents ↔ the manifesto + "remember you" posts |
| 10 | **Tilt and the Mental Game** | how to stop tilting; poker mental game; playing on tilt | Variance, tilt, and the discipline to not let one beat change your game | The sticky-tilt emotion system; opponents that punish (and feel) tilt | Fish capstone, the manifesto |

### Backlog / optional (pull in if a topic lands)
- **Bankroll management** — kw: poker bankroll management. Hook: the Circuit's
  bankroll-as-hero + staking economy. More meta than tactical.
- **Continuation betting (c-bets)** — kw: continuation bet strategy. Hook: the
  `auto_cbet` spot tendency some opponents carry. Narrower but high volume.
- **Cash vs. tournament poker** — kw: cash game vs tournament. Hook: the game
  runs both; ICM/bubble dynamics. Pairs with the stack-depth post.

## SEO priority (if drafting in order of payoff, not reading order)

1. **Pot odds** (3) — enormous evergreen volume, clean coach tie-in.
2. **When to bluff** (5) — huge volume + best cross-link to the archetype series.
3. **Position** (1) — foundational, high volume, easy win.
4. **Reading opponents/tells** (9) — strong volume *and* the best brand fit.
5. **Starting hands** (2) — high volume; coach/Range-Explorer conversion.

The rest (exploitative, defensive, stack depth, bet sizing, tilt) are solid but
lower-volume or more conceptual; they round out the series and deepen the
internal-link web more than they pull cold traffic on their own.

## Production notes
- Reuse the archetype template: define the concept → the core idea → "how it
  shows up in My Poker Face" → the one mistake everyone makes → CTA.
- Ground every claim the game can back (EV, Range Explorer, dossier, emotion
  system) and link the relevant opponent pages / archetype posts.
- Ship `draft: true`; flip to `draft: false` + deploy when ready.
- Cross-link aggressively with **Know Your Opponent** — the two series should
  feel like one body of work, each sending readers into the other.
