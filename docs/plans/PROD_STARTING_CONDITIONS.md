---
purpose: Defines the starting conditions for a fresh production sandbox — the economy seed/thermostat (the "Director") and the 76-persona launch cast.
type: design
created: 2026-06-04
last_updated: 2026-06-04
---

# Production Starting Conditions

Two halves: **the Director** (how the economy is seeded and self-regulates) and
**the Cast** (who lives in a fresh sandbox). They are coupled — every persona's
`starting_bankroll` is drawn from the central bank at genesis, so the reserve
target is defined as a % of the cast's total bankroll and auto-scales as the
roster grows.

The goal for v1: **one lived-in world you are brought into.** Replayable
multi-start variants are a later add; for now a single, hand-tuned starting state.

---

## Part 1 — The Director (economy thermostat)

The "chairman"/Director is not an entity with a balance. It is a pure read-model
over the chip ledger (`core/economy/economy_signal.py`) that computes
`r = reserves / holdings` each tick and drives four levers from it. Reserves and
holdings are ledger-derived (single source of truth, drift = 0).

### 1.1 Genesis: seed the whole economy from the central bank

Mint the **entire economy** into `bank()` at sandbox creation, then pay each
persona's `starting_bankroll` *out* of the bank as a draw. After seeding, the
bank holds `E − Σ(bankrolls)` = the reserve — literally "chips not yet dealt to
players." This makes the reserve a natural % of the cast and conservation-clean.

- Set `E = 1.05 × Σ(bankrolls)` → **reserves start at ~5% of holdings.**
- At launch holdings ≈ **$2.64M**, so seed reserve ≈ **$132k**.
- That 5% clears every casino spawn threshold ($2/$10/$50 need pool ≥
  5k/50k/100k) so the world boots up lived-in (casinos churning fish → grinders),
  but sits **below** the tournament trigger so the first Main Event has to be
  *earned* over the opening day of play.
- Implementation: a production genesis path mirroring `seed_bank_pool()`
  (`cash_mode/closed_economy.py:172`), which today runs only in sims.

### 1.2 The reserve-band table

All four levers key off `r = reserves / holdings`:

| Band | `r` | Rake | Vice (AI→pool refill) | Side-hustle (pool→AI drain) | Casinos | Tournament |
|---|---|---|---|---|---|---|
| **Critical** | < 0.03 | $1000 **+ $200 + $50** | cranked | **choked** (escrow can't fund) | roll up low-stake | none |
| **Low** | 0.03–0.06 | $1000 **+ $200** | elevated | reduced | hold (no new spawns) | none |
| **Healthy** | 0.06–0.12 | **$1000 only** | low / off | normal | spawn per thresholds | none (climbing) |
| **Trigger** | ≥ 0.12 | $1000 only | off | generous | all open | **fire Main Event**, drain to 0.06 floor |

The sawtooth: reserves climb **0.06 → 0.12** on the rake+vice faucet over ~a day
of play, cross the freeroll line, fire **one** Main Event with prize ≈
`(0.12 − 0.06) × holdings` (~$148k at launch holdings, capped at `OVERLAY_CAP`),
drain back to **0.06** (keeps half — never the whole reserve), climb again.

### 1.3 Tournament cadence — play-based, not wall-clock

"1–2 per day" is an **emergent target**, not a scheduler. The economic trigger is
already play-coupled (reserves only climb when hands are dealt). Idle sandbox →
no climb → no tournament, which is correct.

- Split **trigger (0.12)** from **floor (0.06)** — today `economy_signal.py` uses
  one number (`FLUSH_SETPOINT = 0.08`) for both, which makes prizes thin. The
  split makes prizes meaningful while keeping the Director from draining the bank.
- Change `MAIN_EVENT_COOLDOWN_SECONDS` (currently wall-clock 1800s) to a
  **play-measured cooldown** (hands or active world-ticks since last event), so a
  heavy session can earn two and a light day earns zero.
- **Tuning is sim-validated:** set the rake+vice faucet rate so one
  floor→trigger climb ≈ a typical day's hand volume. We can only *know* it lands
  at ~1–2/day by simming expected hands/day × per-hand reserve climb. (EXP-style
  run before flipping the thermostat on.)

### 1.4 Rake — two layers (the Director gets a lever, not the wheel)

Rake's primary job is **leveling per-player runaway winnings + acting as a money
sink** — this is orthogonal to *aggregate* reserves (one AI can run hot while the
bank is healthy). So:

- **Structural rake (always on, NOT Director-controlled):** the $1000 table rake
  and its throttle/sink role. Permanent gameplay rule.
- **Director rake (the only economy-gated part):** the *extra* $50/$200 rake that
  switches on only in Low/Critical bands.

Today `RAKE_STAKE_BIG_BLINDS = {1000}` is a static frozenset
(`economy_flags.py:158`); the low-stake additions become derived from the
economy signal (hang it on the existing `rake_schedule` policy slot).

### 1.5 Vice ↔ Side-hustle symmetry + the escrow fix

Vice and side-hustle are **opposite faucets** and should ride the same band:
- **Vice** = AI → pool (refill). Used to fire whenever cast median ≥ $5k
  (`ai_vice_spending.py:56`) — i.e. always, from tick one, which read as an
  arbitrary tax. **Shipped 2026-06-04 (flag `VICE_RESERVE_GATED`, default OFF):**
  `reserve_vice_multiplier(ratio)` scales the whole vice pass by the bank-pool
  deficit — 0 at/above the healthy floor (0.06; a flush bank stops taxing the
  field), 1 at/below critical (0.03; crank the refill), linear between. The
  primary idle-pool path is gated; the seated→leave intercept (`commit_leave_vice`)
  is a documented follow-up. Flip on with the rest of the thermostat after sim.
- **Side-hustle** = pool → AI (drain). Broke AIs get paid *from reserves*.

**Bug (must fix): side-hustle has no escrow.** No chips move at departure — "the
payout lands at expiry" (`ai_side_hustle.py:281`) — and the payout is a pool
draw (`side_hustle_earning`, `ledger.py:208`). Between leave and return the bank
can spend those reserves elsewhere; at expiry the pool is empty, the AI's
bankroll still force-credits (`ai_side_hustle.py:635`), and reserves go
underwater. **Symptom:** "AI leaves for a side hustle, comes back, no money in
the bank."

**Fix (shipped 2026-06-04) — pay up front, no escrow account needed.** Simpler
than a `side_hustle:<pid>` escrow and achieves the same guarantee: the payout is
drawn from the pool and credited to the AI's bankroll **at departure** (when the
bank still has the chips), and the AI is off-grid for the duration with its
earnings already banked. Expiry is now a pure off-grid→idle return that moves no
chips. The row is inserted *before* the credit so a credit failure leaves the AI
off-grid having earned nothing (drift stays 0), never a payment without an
off-grid row. Reserve-aware for free: with a ledger present, a hustle the pool
can't fund simply doesn't fire (the AI stays idle and retries) — see
`resolve_ai_side_hustle` / `tick_side_hustle_expirations` in
`cash_mode/ai_side_hustle.py`.

### 1.6 Open tuning questions (need a sim before flipping on)

1. Faucet rate vs. floor→trigger gap → validate ~1–2 tournaments/day.
2. `OVERLAY_CAP` vs. holdings: at $2.64M, 0.12 = $317k > current 250k cap — the
   cap would bind. Either raise the cap to ~6% of holdings or accept that very
   flush banks drain over two events.
3. Casino spawn thresholds are absolute (5k/50k/100k); consider making them % of
   holdings for consistency as the roster grows.

---

## Part 2 — The Cast (76 personas)

### 2.1 Composition & math

- **76 authored personas** (IP-vetted: dead historical figures, public-domain
  literary/mythological, and original comical archetypes).
- **All 76 circulate at launch.** Cold-start seats ~40 (see 2.4); the remaining
  ~34 are the live **bench** that fills seats as AIs move/bust/get called up to
  tournaments. "Circulating" = eligible to be seated, not seated-at-once. The
  `circulating` flag (DB-only, schema v123+) is the live tuning knob — demote to
  bench if the lobby ever feels crowded; never delete a character you liked.
- **9 fish** stay casino-only (pool-funded on spawn, not in this count).
- Σ(bankrolls) ≈ **$2.64M** · median ≈ **$22k** (bottom-anchored so the human
  starts small and climbs) · ceiling **$120k** (down from $250k — Zeus/Scrooge
  are aspirational bosses, no longer 15× the field).

### 2.2 IP policy

Safe buckets: **dead historical figures**, **public-domain literary/mythological**
(US PD), **original archetypes**. Excluded for IP: Dr. Seuss, Bob Ross (litigious
estates), Steamboat Willie / Winnie-the-Pooh (PD-but-trademark-shadowed — left
out until we can clear them cleanly), Muhammad (per owner). Henry Ford dropped in
favor of Andrew Carnegie (no real-world baggage, better arc).

### 2.3 Roster

Bankrolls assigned within tier bands; `comfort` = `stake_comfort_zone`.

**Boss — comfort $1000**
| Persona | Bankroll | Source | Archetype |
|---|---|---|---|
| Zeus | 120000 | myth | King of gods — thunderous overbets |
| Ebenezer Scrooge | 118000 | PD (Dickens) | Nitty hoarder, hates parting with chips |
| King Midas | 112000 | myth | Overvalues every hand — all gold to him |
| Genghis Khan | 106000 | historical | Conquering aggression, takes territory |
| Alexander the Great | 110000 | historical | Undefeated conqueror — brilliant audacious aggression |
| Julius Caesar | 100000 | historical | *Veni vidi vici* — imperial overextension |

**High — comfort $200/$1000**
| Persona | Bankroll | Source | Archetype |
|---|---|---|---|
| Cleopatra | 95000 | historical | Seductive manipulator |
| King Arthur | 90000 | PD legend | Noble, chivalrous, round-table fair |
| King Tut | 68000 | historical | Boy king on inherited gold — naive, lavish, sticky |
| Louis XIV | 86000 | historical | The Sun King — grandiose |
| Andrew Carnegie | 80000 | historical | Robber baron who can't stop giving it away |
| Machiavelli | 75000 | historical | Calculating, deceptive |
| Dracula | 70000 | PD (Stoker) | Patient predator, drains stacks |
| Captain Ahab | 65000 | PD (Melville) | Obsessive — won't fold his white whale |
| Queen of Hearts | 60000 | PD (Carroll) | Volatile tyrant — "off with their stack" |

**Upper-mid — comfort $50/$200**
| Persona | Bankroll | Source | Archetype |
|---|---|---|---|
| Sherlock Holmes | 55000 | PD (Doyle) | Reads tells, deductive |
| Sun Tzu | 52000 | historical | Positional warfare, patient |
| P.T. Barnum | 50000 | historical | Showman hustler — "sucker born every minute" |
| George Washington | 48000 | historical | Disciplined general, never bluffs |
| Queen Elizabeth I | 46000 | historical | Shrewd, patient ruler |
| Sigmund Freud | 44000 | historical | Reads your subconscious tells |
| Napoleon | 43000 | historical | Aggressive expansion, overreaches |
| Wyatt Earp | 41000 | historical | Lawman-gambler (pairs w/ Doc Holliday) |
| King Henry VIII | 40000 | historical | Domineering, eliminates rivals |
| Benjamin Franklin | 38000 | historical | Canny pragmatist, value-bettor |
| Blackbeard | 37000 | historical | Intimidating pirate, fear equity |
| Wild Bill Hickok | 35000 | historical | The "dead man's hand" gunfighter-gambler |

**Mid — comfort $10/$50**
| Persona | Bankroll | Source | Archetype |
|---|---|---|---|
| Ernest Hemingway | 32000 | historical | Terse macho bravado, grace under pressure |
| Winston Churchill | 31000 | historical | Stubborn grinder, never surrenders |
| Nikola Tesla | 30000 | historical | Eccentric genius, brilliant unpredictable lines |
| Leonardo da Vinci | 29000 | historical | Inventive, unpredictable |
| Marie Curie | 28000 | historical | Methodical, calculated risk |
| William Shakespeare | 27000 | historical | Theatrical, dramatic bluffs |
| Dr. Jekyll & Mr. Hyde | 26000 | PD (Stevenson) | Split personality — flips nit ↔ maniac |
| Harry Houdini | 25000 | historical | Escapes impossible spots, misdirection |
| Baron Munchausen | 24000 | PD | The master bluffer — impossible tall tales |
| Abraham Lincoln | 23000 | historical | Honest, straightforward — rarely bluffs |
| Mark Twain | 22000 | historical | Folksy needler, table talk |
| Socrates | 22000 | historical | Questions everything, slow & methodical |
| Doc Holliday | 21000 | historical | Sickly but lethal professional gambler |
| Joan of Arc | 21000 | historical | Fearless, faith-driven all-ins |
| Oscar Wilde | 20000 | historical | Witty needler supreme |
| Don Quixote | 20000 | PD (Cervantes) | Tilts at windmills, chases bad draws heroically |
| Robin Hood | 19000 | PD legend | Takes from rich tables |
| William Wallace | 19000 | historical | Fearless freedom-fighter all-ins |
| Santa Claus | 18000 | folklore/PD | Jolly, generous — gives chips away |
| Edgar Allan Poe | 18000 | historical | Gloomy, paranoid, hero-folds |

**Low-mid — comfort $10**
| Persona | Bankroll | Source | Archetype |
|---|---|---|---|
| Fyodor Dostoevsky | 16000 | historical | Wrote *The Gambler*, was one — tilt incarnate |
| Salvador Dalí | 15000 | historical | Surreal, bizarre, unreadable lines |
| Cheshire Cat | 15000 | PD (Carroll) | Grinning, fades in/out — unreadable |
| Long John Silver | 14000 | PD (Stevenson) | Charming pirate hustler |
| Friar Tuck | 14000 | PD legend | Jolly drunk monk, loose/jovial |
| Paul Bunyan | 13000 | PD folklore | Oversized, swings big |
| Confucius | 13000 | historical | Proverbial, disciplined |
| Buddha | 12000 | rec | Serene, unbothered, zen calls |
| Frankenstein's Monster | 12000 | PD (Shelley) | Lumbering, misunderstood, erratic |
| The Mad Hatter | 11000 | PD (Carroll) | Chaotic, nonsensical lines |
| The Very-Mean Person | 11000 | original | Needler — tilts the table |
| Bigfoot | 10000 | cryptid folklore | Elusive nit — rarely shows a hand |
| The Headless Horseman | 10000 | PD (Irving) | Charges recklessly |
| Jesus | 10000 | rec | Forgiving, turns the other cheek (passive) |
| Pinocchio | 9000 | PD (Collodi) | Terrible liar — bluffs are obvious tells |
| A Mime | 9000 | original | Silent, unreadable, no table talk |
| A Guy Who Tells Too Many Dad Jokes | 9000 | original | Chatty, distracting |
| Rip Van Winkle | 9000 | PD (Irving) | Sleepy, slow, misses spots |
| Alice | 9000 | PD (Carroll) | Curious, naive — completes the Wonderland table |

**Low — comfort $2/$10**
| Persona | Bankroll | Source | Archetype |
|---|---|---|---|
| The Tooth Fairy | 8000 | folklore | Small, steady collector |
| A Caricature Tech Bro | 7000 | original | Overconfident, disrupts |
| An Over-Caffeinated Barista | 7000 | original | Jittery, fast, loose |
| A Conspiracy Theorist | 6000 | original | Sees patterns, over-reads |
| A Soap-Opera Villain | 6000 | original | Dramatic, telegraphs |
| A Disgraced Weatherman | 5000 | original | Confident bad predictions |
| An Alien | 5000 | original | Inscrutable alien logic — unpredictable |
| A Baby | 5000 | original | Plays on pure instinct |
| Diogenes | 4000 | historical | The cynic — nothing to lose, fearless, insults all |
| The Gingerbread Man | 4000 | PD fairy tale | "Can't catch me" — folds to aggression |

**Wonderland table** (themed cluster): Alice · Queen of Hearts · Mad Hatter ·
Cheshire Cat.

### 2.4 Cold-start table layout (concentrated for liveliness)

Down from 11 tables → ~8, seated fuller, leaving a real bench
(`cash_mode/lobby_config.py`):

| Stake | Tables | AI seated | Role |
|---|---|---|---|
| $2 | 1 | 4–5 | where the human starts |
| $10 | 2 | 8–10 | early grind |
| $50 | 2 | 8–10 | mid |
| $200 | 2 | 8–10 | high |
| $1000 | 1 | 4–5 | the boss table |

≈ **32–40 seated** at cold start; ~34 on the bench → enough to field an 18-player
Main Event without emptying the lobby.

### 2.5 Worked-example JSON (format lock)

Three examples spanning the range, matching the `personalities.json` schema. The
remaining 71 to be generated in tier batches.

```json
"Julius Caesar": {
  "skill": "reg",
  "play_style": "imperial aggression — conquers pots and overextends his lines",
  "default_confidence": "commanding",
  "default_attitude": "imperious",
  "anchors": {
    "baseline_aggression": 0.78,
    "baseline_looseness": 0.5,
    "ego": 0.9,
    "poise": 0.55,
    "expressiveness": 0.7,
    "risk_identity": 0.75,
    "adaptation_bias": 0.45,
    "baseline_energy": 0.7,
    "recovery_rate": 0.2
  },
  "verbal_tics": ["'Veni, vidi, vici.'", "'The die is cast.'", "'I came to win, not to fold.'"],
  "physical_tics": ["*drums fingers like a war drum*", "*surveys the table like a battlefield*"],
  "bankroll_knobs": {"starting_bankroll": 100000, "bankroll_rate": 1200, "buy_in_multiplier": 2.0, "stake_comfort_zone": "$1000"},
  "id": "julius_caesar",
  "staker_profile": {"willing": true, "max_loan_pct_of_bankroll": 0.2, "floor_anchor": 2.0, "rate_anchor": 0.2, "respect_floor": -0.5, "heat_ceiling": 0.9},
  "borrower_profile": {"willing": false}
}
```

```json
"Harry Houdini": {
  "skill": "reg",
  "play_style": "escape artist — wriggles out of bad spots with misdirection bluffs",
  "default_confidence": "showy",
  "default_attitude": "theatrical",
  "anchors": {
    "baseline_aggression": 0.55,
    "baseline_looseness": 0.45,
    "ego": 0.6,
    "poise": 0.82,
    "expressiveness": 0.65,
    "risk_identity": 0.6,
    "adaptation_bias": 0.7,
    "baseline_energy": 0.68,
    "recovery_rate": 0.4
  },
  "verbal_tics": ["'Nothing is impossible to escape.'", "'Watch closely — or don't.'", "'You'll never see it coming.'"],
  "physical_tics": ["*rolls up his sleeves slowly*", "*makes a chip vanish and reappear*"],
  "bankroll_knobs": {"starting_bankroll": 25000, "bankroll_rate": 500, "buy_in_multiplier": 1.4, "stake_comfort_zone": "$50"},
  "id": "harry_houdini",
  "staker_profile": {"willing": true, "max_loan_pct_of_bankroll": 0.1, "floor_anchor": 1.0, "rate_anchor": 0.18, "respect_floor": -0.6, "heat_ceiling": 0.8},
  "borrower_profile": {"willing": false}
}
```

```json
"An Alien": {
  "skill": "rec",
  "play_style": "plays by inscrutable alien logic — unpredictable, ignores convention",
  "default_confidence": "oblivious",
  "default_attitude": "curious",
  "anchors": {
    "baseline_aggression": 0.5,
    "baseline_looseness": 0.7,
    "ego": 0.4,
    "poise": 0.6,
    "expressiveness": 0.55,
    "risk_identity": 0.65,
    "adaptation_bias": 0.2,
    "baseline_energy": 0.6,
    "recovery_rate": 0.3
  },
  "verbal_tics": ["'On my planet, this is a war crime.'", "'Take me to your dealer.'", "'Beep boop... all in?'"],
  "physical_tics": ["*tilts head 90 degrees at the cards*", "*photographs the flop with three eyes*"],
  "bankroll_knobs": {"starting_bankroll": 5000, "bankroll_rate": 100, "buy_in_multiplier": 1.0, "stake_comfort_zone": "$2"},
  "id": "an_alien",
  "staker_profile": {"willing": false},
  "borrower_profile": {"willing": false}
}
```

---

## Implementation checklist

**Director (economy):**
- [ ] Production genesis path: mint `E = 1.05 × Σ(bankrolls)` into bank, pay
      bankrolls out as draws (reserve starts at ~5% of holdings).
- [ ] Split tournament trigger (0.12) from floor (0.06) in `economy_signal.py`.
- [ ] Play-measured Main Event cooldown (hands/active-ticks, not wall-clock).
- [ ] Two-layer rake: structural $1000 always-on; Director $50/$200 band-gated.
- [x] Re-gate vice intensity on reserve deficit (`VICE_RESERVE_GATED`, default
      OFF; `reserve_vice_multiplier`). Done 2026-06-04. Follow-up: gate the
      `commit_leave_vice` seated→leave intercept too.
- [x] **Side-hustle pay-up-front** — credit at departure (drift-safe, reserve-aware),
      expiry is a no-chip off-grid→idle return. Done 2026-06-04 (tests green).
- [ ] Raise `OVERLAY_CAP` or accept two-event drain (cap binds at $2.64M).
- [ ] Sim-validate faucet rate → ~1–2 tournaments/day.

**Cast:**
- [x] Refine the personality generator: add `self_belief` (10th anchor) +
      `spot_tendencies` generation (registry-validated) + `generate_from_spec()`
      pinned-mechanics mode. Done 2026-06-04 (tests green).
- [x] Generate all 76 personas into `personalities.json` via
      `scripts/seed_prod_roster.py` (LLM flavor around pinned tier anchors +
      bankrolls + signature tendencies). Done 2026-06-04 — Σ(bankrolls)=$2.459M,
      median $21.5k, ceiling $120k. File now 105 personas (76 cast + 9 fish + 20
      extras/bots); removed 5 name-variant duplicates.
- [ ] **Seed-time circulating policy:** flag all 76 cast `circulating=1`; fish
      casino-only; set `circulating=0` for control bots (CaseBot/GTO-Lite/
      BaselineSolver) and IP-risk holdovers (Bob Ross, Dr. Seuss). The ~15 legit
      extras (Annie Oakley, Calamity Jane, Frida Kahlo, Marie Antoinette, Medusa,
      archetype originals) are optional bench.
- [ ] Concentrate cold-start tables to ~8 (update `lobby_config.py`).
- [ ] Verify Σ(bankrolls) and the 5% reserve seed against the live ledger.
