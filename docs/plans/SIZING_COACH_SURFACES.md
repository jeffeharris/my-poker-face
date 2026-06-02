---
purpose: Design for surfacing size→strength readability in the coach/dossier with progress-and-change-over-time, reusing the existing leak-loop trend engine
type: design
created: 2026-06-01
last_updated: 2026-06-02
---

> **Status (2026-06-02): BOTH SURFACES + KILL SWITCH BUILT.**
> - **Surface B** — backend (`coach_sizing_tells.py` + `GET /api/coach/opponent-tells`)
>   + `OpponentSizingTell` dossier card (mounted in `CharacterDetailCard`). Validated
>   on live DB: production tiered bots all read `balanced` (sizing not face-up —
>   confirms the river-readability work); `face_up`/`mixing` reserved for genuinely
>   readable/adapting opponents.
> - **Kill switch** — `OpponentTendencies.sizing_tell_is_mixing()` (recency window) +
>   `_resolve_sizing_defense_polar` pauses the bot's sizing-defense on a `mixing` read.
>   Honest limit: only updates on the opponent's big bets that reach *showdown*.
> - **Surface A** — `load_owner_bet_decisions` + `GET /api/coach/sizing-readability`
>   (reuses B's core, self-framed) + `SizingReadability` card on the Preflop Game coach
>   page. Sparse on real human data (busiest owner-seat = 6 bets) → coarse blocks +
>   "keep playing" the common state, as designed.
>
> Remaining: scouting-economy reconciliation for the dossier card (currently un-gated);
> per-street breakdown + a sizing drill (both volume-gated); recency-weighting the
> underlying read (the full counter-adaptation fix — bigger memory-layer change).

# Sizing coach surfaces — "readability over time"

Two coach/dossier surfaces that take the **size→strength tell map** (how much a
player's bet *size* leaks their hand *strength*) and show it **over time** using
the coach's existing leak-loop trend machinery. One points the lens at *you*
(self-coaching), one at *each opponent* (intel).

This is the "progress and change over time" half of the original idea —
*"incorporate the tell map into the coach like we did with leaks."* The tell map
is the instrument (`experiments/measure_passivity.py:print_tell_map`,
`docs/plans/OVERBET_BALANCING.md` §5c); the coach already has the trend half built
for preflop chart leaks; these surfaces wire them together.

## Background / what already exists

- **The tell map** measures, per (street, bet-size bucket), the hand-class
  composition of a betting range and its **bluff share vs the GTO-unexploitable
  target `s/(1+2s)`**. A face-up bettor collapses to ~0% bluffs at big sizes → a
  reader folds for free. Reusable on a human's hand history (§5c says so outright).
- **The opponent read** `sizing_polarization_score` (= big-bet equity − small-bet
  equity) already exists per-opponent in `poker/memory/opponent_model.py`,
  accumulates cross-game, and is surfaced statically in the dossier
  ("Sizing tell" scouting tier) and the coach assistant. See
  `docs/plans/SIZING_AWARE_OPPONENT_MODELING.md` (Phase A) and
  `docs/plans/TELLS_SYSTEM.md`.
- **The bot consumer** (Phase B sizing-defense) folds more vs a detected face-up
  bettor (`poker/strategy/value_override.py:compute_sizing_defense_strategy`,
  `tiered_bot_controller.py:_apply_sizing_defense`), default-OFF, per-personality
  opt-in. Measured **+4.27 bb/100 [−8.20, +16.74]** vs a maximally face-up bot —
  real but marginal (CI spans 0). Surface B below doubles as its **kill switch**.
- **The trend engine** is built (for preflop chart leaks) in
  `flask_app/services/coach_chart_leaks.py`:
  - `recent_slice(decisions, n_hands)` — last-N-hands recency window.
  - `compute_slice_diff(all, recent, …)` — recent-vs-all-time → trend state
    (`shrinking` / `persistent` / `worsening` / `cleared` / `emerging`).
  - `compute_leak_trend(decisions, …, blocks=6)` — 6 equal volume-blocks
    oldest→newest → a gap **sparkline**.
  - `depth_slice(decisions, band)` — deep (≥35bb) vs short.
  - `coach_drill.py` turns a confirmed leak into a practice quiz (the training-room
    loop reuses the leak set + skill progression).
  These functions are **generic over a `decisions → metric` grading core**
  (`_grade_groups` + injected `resolve_ref`). They don't care that today's metric
  is preflop frequency-deviation.

## Shared foundation (one new piece)

Both surfaces draw from the **same table** — `player_decision_analysis` (has
`equity`/`relative_strength`, `pot_total`, `raise_amount`, `phase`, `created_at`,
`player_name`) — and **reuse the trend/slice/sparkline functions verbatim**. The
only new code is a **sizing grading core** mirroring `_grade_groups`:

```
_grade_sizing_groups(decisions, *, group_by) -> {group_key: metric_dict}

  metric per group = bluff_share − gto_target          # gto_target = s/(1+2s)
  bluff_share      = #(low-equity bets) / #(bets) in the bin   # equity < ~0.45
  group key (COARSE — survives sparse data):
    A (self):     (street_collapsed, size_bin)   size_bin ∈ {big ≥0.75pot, small}
    B (opponent): (opponent_name,    size_bin)
  status = 'confirmed' if n >= CONFIRM_MIN_SEEN else 'watching'   # reuse gate
```

That's ~one service file. The trend, slice-diff, recency, depth, and sparkline
functions then operate on it unchanged.

### The sparsity rule (load-bearing)

Preflop leaks trend cleanly because every hand is one decision. Sizing readability
is **postflop-aggressive-bets-binned-by-size** — sparse — so the metric MUST use
**coarse blocks**: street-collapsed by default (per-street only at high volume),
**2 size bins** (big/small, not 6), and a binary "did you ever bluff a big bet"
framing. Below ~15 big-bet showdowns, surfaces show "keep playing — not enough big
bets to read yet"; thin trend blocks render as gaps in the sparkline (existing
behavior). Never claim GTO precision we haven't measured — same honesty tiering as
the preflop leak finder.

---

## Surface A — "Your sizing is getting less readable" (self-coaching)

The truest leak-loop parallel: grade the player's OWN big-vs-small bets for balance
and show them fixing it over time. The leak is "your big bets are always value."

- **Scope:** owner seat only. Sparse → coarse blocks mandatory.
- **Metric:** per (street-collapsed, big/small), bluff_share vs gto_target.
- **Trend:** reuse `compute_leak_trend` + `compute_slice_diff` directly.

**API** — `GET /api/coach/sizing-readability` (mirrors the preflop-leak payload):

```json
{
  "enough_data": true,
  "readability": [
    { "street": "all", "size_bin": "big",
      "your_bluff_share": 0.08, "gto_target": 0.33, "gap": -0.25,
      "n": 41, "status": "confirmed", "verdict": "face_up",
      "recent": { "gap": -0.14, "trend": "shrinking" },
      "trend": { "series": [-0.31, -0.28, -0.22, -0.19, -0.14, null] } }
  ],
  "summary": "Your big bets are face-up — you almost never bluff them. Improving."
}
```

**UI** — a card in the coach panel, same visual language as the leak cards:

```
┌─ SIZING READABILITY ──────────────────────────── confirmed ─┐
│ Your BIG bets (>=3/4 pot)         face-up -> improving  ^    │
│ bluff share  8%   ·  balanced ~ 33%   ·  gap -25pts         │
│ .:|||  -31 -> -14   "you're starting to bluff big more"     │
│ ----------------------------------------------------------- │
│ Your SMALL bets                   balanced  OK              │
│ [ Drill: bluff a big bet here -> ]                          │
└─────────────────────────────────────────────────────────────┘
```

---

## Surface B — "How readable is this opponent" (intel)

Each opponent's `sizing_polarization_score` over time-blocks. "Progress" reframes
as **read confidence + stability** — is the tell holding, or are they starting to
mix?

- **Scope:** per opponent, recomputed per time-block from *their* rows.
- **Data:** rich — the AIs have thousands of postflop aggressive decisions.
- **Bonus — fixes a real blind spot:** the live read is a *lifetime cumulative
  mean* (no decay; and the bot folding to big bets suppresses the showdowns the
  read needs), so it flips off slowly if an adversary adapts. Recomputing
  per-block from raw rows here **shows the tell decaying** instead of freezing it
  in an average. The `stability` axis is the **kill-switch signal** for the bot's
  Phase B sizing-defense.

**API** — `GET /api/coach/opponent-tells?opponent=Batman`:

```json
{
  "opponent": "Batman",
  "tells": [
    { "axis": "sizing", "label": "Overbets = nuts",
      "score": 0.41, "confidence": "high", "n_showdowns": 63,
      "exploit": "Fold marginal hands to his big bets",
      "stability": "stable",
      "trend": { "series": [0.38, 0.40, 0.39, 0.42, 0.41, 0.41] } }
  ]
}
```

**UI** — a panel in the opponent dossier (not the self-coach):

```
┌─ READING: Batman ─────────────────────────── 63 showdowns ─┐
│ * Sizing tell        STRONG        stability: stable        │
│   "His big bets are the nuts."                             │
│   ___:_  score 0.41 (face-up >= 0.15)                      │
│   -> Exploit: fold your bluff-catchers to his overbets     │
│   ---------------------------------------------------------│
│   ! if this line starts dropping, he's mixing - stop folding│
└────────────────────────────────────────────────────────────┘
```

---

## A vs B at a glance

| | A — your progress | B — opponent tell |
|---|---|---|
| Scope | owner seat | per opponent |
| Lives in | coach panel | opponent dossier |
| Data density | sparse (coarse blocks required) | rich (AIs have volume) |
| "Over time" means | you improving | tell stable vs decaying |
| Bonus | self-coaching loop + drill | feeds the bot's Phase B kill switch |

## Recommendation / sequence

1. **Build B first.** The data supports it, it's visually compelling, and its
   `stability` axis is the missing piece that addresses the counter-adaptation
   hole in the bot's sizing-defense (the lifetime-mean read that flips off slowly).
2. **A as a fast-follow** once B's grading core + trend wiring are proven — A reuses
   the same `_grade_sizing_groups` with `group_by='self'`, so most of B's code is A.
3. **Deferred:** per-street (vs collapsed) breakdown, 4-bin sizing, and a sizing
   drill, all gated on real player volume.

## Open questions

- **Equity at the bet action.** `_grade_sizing_groups` needs the bettor's equity
  *at the moment of the bet* (showdown-revealed). Confirm `player_decision_analysis`
  rows carry usable equity for non-owner seats at bet/raise actions, or join to the
  showdown-equity machine (`memory_manager._record_showdown_equity_at_actions`).
- **"Bluff" threshold.** `equity < 0.45` as the bluff/thin cut is a starting
  heuristic; calibrate against the tell-map's hand-class definition (`air*` vs
  `nuts/strong_made`).
- **Stability classifier (B).** Define `stable` / `mixing` / `insufficient` off the
  block series (e.g. last-block vs trailing-mean drop > delta) — and decide whether
  it auto-pauses the bot's sizing-defense or just warns.
