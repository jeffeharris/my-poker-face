---
purpose: Ready-to-write outline for the "Reputation: how a table treats a known villain" blog post (Inside the Table track)
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Outline — "Reputation: how a table treats a known villain"

- **Working title:** Reputation: how a table treats a known villain
- **Track:** Inside the Table
- **Target reader:** Players and build-in-public followers who want to see how the
  game grew a *world-level* memory — a standing you carry into every room — and the
  surprising amount of measurement it took to make "fame" mean something instead of
  inflating into noise.

## One-line hook (grounded)

Giving the player a reputation was the easy part; the hard part was that on the real
field *nobody could ever become a villain* — the math that scored fame had a floor
set higher than the highest score anyone could reach.

## Narrative spine (section beats, in order)

1. **Two axes, not one karma bar.** The companion to "your opponents remember you":
   that post was the table's memory of *how you play*; this one is the world's memory
   of *who you are*. The system was deliberately built as two poles — **renown** (how
   known you are) and **regard** (how warmly the room feels about you) — not a single
   good/evil scalar. The villain isn't the *opposite* of the legend; he's the
   high-renown, low-regard *quadrant*. (Source: prestige captain's log — "2D (renown +
   regard), not a single karma scalar," a decision Jeff locked, not a default.)

2. **The first version was a scoreboard on purpose.** v1 shipped as read-only: a stat,
   a ticker that recomputes it, a lobby panel, nothing that touched any AI's decision
   math. The point was to make the villain path *visible* without changing the game —
   the "legibility guardrail." It reused two existing patterns wholesale (`holdings`
   snapshots for the stat, `whereabouts` for the pure aggregator), so the new surface
   was small. The honest engineering note: regard reads the *inbound* relationship
   graph (everyone's view of me), which didn't exist as a query yet — it had to be
   added, with an index.

3. **The number that wasn't earned.** Jeff played his own sandbox and hit the first
   real design smell: maxing renown took meeting **~12 AIs** ("2 tables"), and the
   inputs were ones *he hadn't chosen and didn't care about*. That kicked off the v2
   redesign: uncap renown, make it **field-relative** (fame is your standing *vs the
   field*, not an absolute number), and make every hand move the needle instead of
   tripping binary gates. Crucially he wanted it to apply to **AIs too** — so renown
   had to be computed for every entity, not just the human.

4. **De-risking a formula before building it.** The unlock: renown is a *read-side
   projection* — nothing about scoring needs the migration, the ticker, or the UI. So
   the whole formula could be validated offline by re-scoring one frozen field log,
   which is a *perfectly paired* A/B (no RNG desync). A throwaway scorer caught a
   structural bug for the price of an afternoon: a villain who busts a 45-point legend
   was worth ~70 points *per scalp*, scoring **212 against a ~30 field** — a
   super-linear blow-up. The rule that fixed it became law for the whole system:
   anything that multiplies by another entity's renown must use a *field-relative*
   (percentile) measure, never a raw one.

5. **The villain nobody could be (the headline bug).** The badge rendered, but every
   top AI — even one "ahead of 100% of the field" — was labelled *Up-and-comer*. Zero
   figures, zero legends, zero villains. Cause: the "is this person famous?" cut was
   `max(top-20%, 3×median)`, and on the live field the `3×median` floor landed at
   **109.4 — above the field maximum of 95.9.** A multiplicative gate on a
   deliberately *concave* score (every driver is a `log` or `sqrt`, so fame is
   intentionally thin-tailed to prevent whale runaway) is a category error: it demands
   a heavy tail the scoring is specifically built never to produce, and it gets
   *harder* as the field gets more accomplished. The fix — a pure top-decile
   percentile, drop the floor — flipped figures **0 → 8** (`c266104a`).

6. **"Is the field just young?" — the experiment that inverted the question twice.**
   The natural hypothesis: maybe the field is compressed because it hasn't
   differentiated yet; let it run. A 1,500-tick sim said the opposite — a *fresh* field
   is **more** spread (5.4× max/median); **play compresses it**, locking at equilibrium
   by ~tick 400 and holding flat. Fame is structurally thin-tailed because the economy
   is self-correcting (the crown rotates). "Run it longer" wasn't a hedge here; it
   falsified the stated guess and vindicated the percentile.

7. **Earning a villain — input, not threshold.** The fix produced ~7 legends and only
   ~1 villain, because regard runs warm (86% of edges warm). The tempting fix was a
   measurement band-aid (make the warm/hostile split relative). Jeff pushed back: fix
   the *input*, not the ruler — let AIs accumulate real hostility through play and
   table talk. Wiring AI↔AI relationship events live, two **earned** villains emerged
   over 1,000 ticks (Marie Antoinette, The Rock — renowned figures turned hostile). But
   it was slow and shallow, and the diagnosis was precise: regard is the mean over
   ~4,500 **global, historical** inbound edges, so each fresh rivalry is diluted
   ~1/4500. The real unlock is sandbox-scoping and recency-weighting regard — current
   rivalries should dominate. (Deferred to its own plan, not band-aided.)

8. **What a reputation finally *does*.** Renown isn't just a badge. It feeds four
   world-response hooks (table pull, backing gating, chat tone, AI demeanor —
   kill-switched), an AI dossier badge, tournament cards that show renown alongside the
   purse, and **prestige-seeking**: status-hungry AIs are pulled toward famous tables
   (`W_MARQUEE`, calibrated to 1.5 by an event-level probe after a noisier sim metric
   misled an earlier read). The villain you've become changes how the room treats you —
   which is the whole point of carrying a name between sessions.

9. **Closing: a name you carry, not a score you farm.** Tie back to the origin —
   living personalities with moods and attitudes predate the AI pair by years. Reputation
   is that instinct turned outward: the *world* now has a memory of you, two-dimensional
   and earned, not a karma meter you grind. And the work that made it real was
   measurement discipline — an offline scorer, a falsified hypothesis, a percentile that
   beats a constant — not a bigger model.

## Evidence & assets

**Hard facts / numbers to cite (verify each against code before publishing):**
- The figure-cut bug: floor `3×median = 109.4` **above** field max `95.9`; pure
  top-decile percentile flipped figures **0 → 8** (7 Beloved Legend, 1 Infamous
  Villain). Commit `c266104a`. Source: `renown-figure-cut-and-regard.md`.
- The scalp blow-up in the offline scorer: a villain scored **212 vs a ~30 field**,
  86% from scalps, before the percentile fix. Source: `renown-v2-balance.md` (Rung 1).
- "Run it longer" inversion: fresh field max/median **5.4×**, compresses with play,
  stable by ~tick 400, flat for 1,100 more; `figs@3×med` decays 2→1 while `figs@10%`
  holds at 8. Source: `renown-figure-cut-and-regard.md`.
- Earned-villain sim: warm% **86 → 73**, hostile **11 → 32**, villains **1 → 2** over
  1,000 ticks; regard diluted ~**1/4500** by the global inbound mean. Source: same.
- Prestige-seeking: co-location of grinders with a famous AI rose **20.8% → 30.4%**
  (+9.6pp) at high W, `audit_drift=0`; `W_MARQUEE` finally set to **1.5** via an
  event-level probe (the churn A/B's "default too weak" read was decoherence noise).
  Source: `renown-v2-ai-wiring.md`.
- `build_inputs` field read optimized **~523ms → ~185ms** (SQL-aggregate + covering
  index v140) — the real ticker cost, not the AI write fan-out (~2.3ms). Source: same.
- Regard rebaseline: neutral moved **0.5 → 0.35** so respect is *climbed*, not just
  lost; prod migration v155 subtracted 0.15 from **9,106** live edges to preserve each
  edge's meaning. Source: `neutral-rebaseline.md`.
- v1 design facts: 2D (renown + regard); renown inputs deliberately exclude raw PnL
  magnitude ("don't grant fame for grinding low stakes"); append-only
  `prestige_snapshots`; renown ratchets (career peak survives a downswing). Source:
  `player-prestige-scoreboard.md`.

**Screenshots / files:**
- HERO candidate: the **ReputationPanel / "standing" crest** (commit `eb9bc354`
  "elevate ReputationPanel into a heraldic 'standing' crest"). *No screenshot of this
  is in the repo's `screenshots/` or `.images/` dirs yet* — needs one captured from a
  populated sandbox showing the villain quadrant + the field-percentile rail. FLAG as
  an asset gap (see Open gaps).
- The AI **dossier badge** (renown quadrant under an AI's name, commit `d7692491`) —
  could reuse `react/react/src/assets/screenshots/mobile-dossier.png` if it shows the
  badge; verify the badge is visible in that image before using it.
- Source docs to link/excerpt: `docs/captains-log/renown/renown-v2-balance.md`
  (the offline-validation story), `docs/captains-log/development/renown-figure-cut-and-regard.md`
  (the headline bug + the villain experiment — most of the spine), `docs/captains-log/
  renown/renown-v2-ai-wiring.md` (hooks + prestige-seeking), `docs/captains-log/regard/
  neutral-rebaseline.md` (regard becomes a ladder), `docs/captains-log/prestige/
  player-prestige-scoreboard.md` (v1 framing).

**Commits to reference (real subjects, dated):**
- `b90451e4 feat(renown): human-only v2 — field-relative uncapped gauge behind kill switch`
- `c266104a fix(renown): top-decile percentile figure cut, drop the 3x-median floor`
- `d7692491 feat(renown): B1 — AI renown badge on the character dossier`
- `a6ef0c70 feat(renown): B4 — prestige-seeking movement (marquee pull), flag-gated OFF`
- `ea93d350 feat(renown): calibrate W_MARQUEE=1.5 via event-level probe (corrects churn-noise read)`
- `88f8bc85 feat(prestige): hook 4 — AI demeanor nudges on player reputation (kill-switched)`
- `4953f17d feat(regard): rebaseline respect/likability neutral 0.5→0.35 (earned, asymmetric) (#202)`
- `417a1953 feat(prod): enable Renown-v2 flags (dev parity + prestige-seeking dep)` — the
  closest thing to a "shipped to prod" marker; confirm what state it left flags in.

## Candidate pull-quotes (verbatim)

- Jeff, the smell that started v2 (chat, prestige transcript): **"fucking 12 is the
  number yo need to meet to max renown? 2 tables? lets make based on world population
  size? what are high-stakes wins? why do i only need 10 to max my status?"** — the
  raw, unfiltered "this number isn't earned" moment. (Trim/asterisk as the blog voice
  requires; the "12... 2 tables... max my status" core is the load-bearing part.)
- Jeff, redirecting the inputs (chat): **"i didnt pick these, so i dont care about any
  of them, what are some other measures? net worth rank? should breadth be just having
  met them once or having some positive relationship or having real playing time
  together?"** — founder steering the design, not the AI.
- Jeff, the uncap + AI-symmetry decision (chat): **"its not just a count, each hand
  moves the needle, its not a binary gate... but yeah, lets uncap it. i also want it to
  be able to be equally applied to the AI."** — names the two hardest structural calls
  in one message.
- Jeff, seeing it work on his own sandbox (chat): **"i'm looking at it on my sandbox in
  the development branch/worktree. im an infamous villain at -27!"** — the human moment
  the whole feature is for.
- Commit subject: **`fix(renown): top-decile percentile figure cut, drop the 3x-median
  floor`** — the one-line statement of the headline bug's fix.

## Draft intro paragraph (post voice)

> Adding a reputation to My Poker Face was supposed to be the fun part. The plan was
> two dials, not one: *renown* for how known you are, and *regard* for how the room
> feels about you — and a villain is just the corner where one runs high and the other
> runs cold. The scoreboard went up, the badge rendered, and then I noticed something
> absurd: on the actual field, nobody could ever become a villain. Or a legend, or a
> figure of any kind. The math that decided "is this person famous?" had quietly set
> its bar *higher than the highest score anyone could possibly reach* — a floor of
> 109 on a field that topped out at 96. Fixing that, and then figuring out how a player
> *earns* a bad reputation instead of being handed one, turned out to be a series of
> small experiments that kept telling me I was wrong.

## Open gaps (need the founder or more reporting)

- **No hero screenshot exists.** The repo's `screenshots/` and `.images/` dirs have no
  ReputationPanel / standing-crest / villain-quadrant image. The single best asset for
  this post (a villain card with the field-percentile rail) needs to be captured from a
  populated sandbox with the flags on. CONFIRM/CAPTURE before publishing.
- **Production status.** `417a1953` enabled Renown-v2 flags on dev; the regard
  rebaseline (`4953f17d`/v155) *did* deploy to prod (9,106 edges shifted, per the log).
  But is the renown *villain quadrant* live for real players in prod, or dev-only behind
  the kill switch? The balance log ends with the flag committed OFF; the figure-cut and
  AI-wiring work is on `development`. Jeff must confirm the current prod flag state
  before the post claims players can be villains "in the live game."
- **The earned-villain wiring is deferred.** The two-villain result came from a *patch
  applied to a scratch worktree and then reverted*; `development` carries only the
  percentile change, and the sandbox-scoped/recency-weighted regard fix lives in a plan
  doc (`RENOWN_REGARD_VILLAIN_BALANCE.md`), not shipped. The post must be honest that
  "earned villainy" is validated-in-sim but not yet the live default — don't overclaim.
- **Quote handling.** The strongest pull-quote contains profanity; decide house style
  (verbatim, asterisked, or paraphrased) — flagging because it's the most *real* line in
  the transcript and softening it loses the point.
- **Names to double-check.** Marie Antoinette / The Rock as the two emergent villains are
  from a single sim run; fine as illustration, but label them as "in one sim" not "the
  game's villains."

## Cross-links (within the series)

- **"Your opponents remember you" (03):** the explicit companion — that post is the
  table's private read of *how you play*; this one is the world's public read of *who
  you are*. Open by referencing it; they're the two halves of "memory."
- **"Trash talk that lands" (05):** the regard side — AI-chosen sarcasm and table talk
  are literally the *input* that turns a neutral player hostile (beat 7). The
  earned-villain path runs straight through the trash-talk system.
- **Origin post (living personalities, 2023):** moods and attitudes predate the AI pair;
  reputation is that instinct turned outward into a persistent, world-level standing.
- **Cash mode / "living economy" post:** renown feeds backing gating and prestige-seeking
  table pull — it's an economic signal, not just a cosmetic badge; cross-link beat 8.
- **A "wrong turns / build-in-public honesty" post:** the 109-over-96 figure-cut bug, the
  hypothesis that inverted twice, and the decoherence-noise "default too weak" misread are
  all strong shared material.
