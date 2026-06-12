---
purpose: Handoff for the archetype band-calibration + chart-pipeline + per-persona-knob session — state, the committed gate, load-bearing gotchas, and what's left
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

# Archetype calibration — Handoff

Pick-up doc for the 2026-06-11/12 session. Everything below is **merged to main**
unless stated. Read the "Load-bearing facts" before touching charts or the gate.

## TL;DR

Made the preflop chart pipeline **reproducible**, built a **committed band gate**,
drove **nit/rock into band** (preflop + postflop, differentiated), and wired the
**per-persona knobs** to actually do something. The believability/archetype work is
in a good state; what's left is cheap hygiene + lower-urgency tech debt.

## The one tool to know: the band gate

```
make validate-archetype-bands          # 9000 hands, ~10 min
PROBE_HANDS=1500 make validate-archetype-bands   # fast iter (VPIP stable; fold-to-3bet/postflop too low-n)
```
`scripts/archetype_mixedfield_probe.py` — deterministic (seed 4242), seats the 7
production archetypes (the mixed field `ARCHETYPE_TARGETS` is calibrated for),
scores every stat vs band, **exits non-zero on a hard fail**. Two scoping rules:
- `WARN_ONLY_ARCHETYPES = {nit, rock}` — calibration-in-progress, reported not gating.
- `HARD_FAIL_STATS = {vpip, pfr, threebet, all_in}` — only high-n (~7000+) frequency
  stats can hard-fail; low-n (fold_to_3bet/4bet ~100-160) and variance-heavy
  showdown stats (AF/AFq/WTSD/W$SD/cbet/fold_to_cbet) are WARN so noise can't redden
  a deterministic gate. Currently **GREEN**.

## What shipped (PRs, all merged)

| PR | what |
|---|---|
| #292 | push/fold Nash charts gated to `push_fold_nash` (8 curated analytical sharks) |
| #294 | `vs_squeeze` node — opener-faces-3bet vs cold-caller-faces-squeeze. Engine `PokerGameState.preflop_opener_idx` + classifier split + capped chart |
| #295 | **`build_vs_open` idempotency fix** — the value-3bet call-sliver ratchet that made the whole regen pipeline non-reproducible (+0.87pp/run). BB de-inflated to designed targets |
| #296 | `build_wider_rfi_chart` drops `vs_squeeze` → full cascade is a no-op |
| #299 | per-player bluff-aware vs_3bet exploit (`vs3bet_exploit` knob, `_apply_vs3bet_bluff_exploit`) + 2 of Jeff's call-off fixes |
| #300 | corrected a **backwards** `validate_preflop` assertion (rock is tightest by design, not nit) |
| #303 | the committed band gate (above) |
| #306 | nit/rock **preflop** into band — `_tighten_facing` (inverse of `_invent_call`), per-scenario keep pools. VPIP 20→15, fold-to-3bet 56→72 |
| #308 | nit/rock **postflop** fold lever — nit `fit_or_fold`+`give_up_turn` (bet-or-fold), rock mild `fit_or_fold`. fold-to-cbet + nit AF into band |
| #309 | docs (`ARCHETYPE_SHAPING_FINDINGS.md`, `VALIDATION_SUITE_SPEC.md`, captain's logs) |
| #311 | `vs3bet_exploit` graded by skill tier (shark 0.85 … rec 0.0) |

Full narrative: `docs/captains-log/archetype-shaping/2026-06-11-*.md` +
`2026-06-12-driving-nit-rock-into-band.md`. Findings: `ARCHETYPE_SHAPING_FINDINGS.md`
(nit/rock section).

## Load-bearing facts (do NOT relearn the hard way)

1. **The regen pipeline IS reproducible now** (post #295). `build_vs_open` was a
   ratchet: value-3bet hands carry a `call` sliver that wasn't charged against the
   defend budget, so re-running widened width ~0.87pp/run. Fixed:
   `call_budget = defend_total − placed_defend`. **Re-running the full cascade is a
   no-op.** If a regen suddenly diffs, suspect a NEW ratchet, not "it was always
   like this." `build_vs3bet_defense --squeeze-only` injects vs_squeeze without a
   full vs_3bet regen.
2. **nit and rock SHARE `preflop_100bb_6max_tight_rfi.json`.** You cannot make rock
   stricter than nit *preflop via the chart* — the keep pool is shared. Their
   differentiation is the **deviation profiles** (`deviation_profiles.py`).
3. **The band probe builds controllers via `__new__`** (`make_controller` in
   `simulate_bb100.py`), bypassing `__init__`. So `vs3bet_exploit` is **OFF in the
   gate** — it's a live-`__init__`-path-only layer. Changing it can't move the bands.
4. **rock is the TIGHTEST archetype by design** (tight-PASSIVE); nit is tight-
   AGGRESSIVE (a notch wider, played hard). Bands confirm it (rock VPIP 8-15 < nit
   10-16). `validate_preflop` asserts `rock < nit`.
5. **The fold-to-3bet bands INTEND over-folding** (above MDF — the scared-tight
   leak). The chart was defending too GTO. This is a "band-not-bug **inverse**" —
   the behaviour was too correct. Same shape as the rock-band-inversion / maniac
   WARNs: check the band's intent before assuming either side is wrong.
6. **nit/rock WTSD ~33 is a structural WARN** (band 22-28/22-30), accepted. A
   VPIP-15 nit folds weak hands preflop → strong flop range → shows down (and wins,
   W$SD in band). Not jointly satisfiable with the tight VPIP band without absurd
   river folds. Left warn-only.
7. **`push_fold_nash` is OFF skill (curated few); `vs3bet_exploit` is ON skill
   (graded).** Binary elite weapon vs continuous exploitation dial — deliberate, see
   #292 vs #311 commit messages.

## What's left (priority order)

**Cheap hygiene:**
- **Flaky test** `tests/test_psychology_v2.py::test_apply_zone_effects_tilted_player`
  — needs "thoughts" on `>10` of 20 probabilistic trials, lands on exactly 10. Seed
  it or widen the margin; it intermittently reds *any* PR (cost 2 CI reruns this
  session).
- **`_chart_gen.py` hoist** — `_playability` / `_norm` / bluff pools are duplicated
  across `build_vs_open` / `build_vs3bet_defense` / `build_vs4bet_defense`
  (rule-of-three). Pure refactor.
- **Archive `PREFLOP_REGEN_HANDOFF.md`** to `docs/archive/` — its open items
  (squeeze, "vs_3bet drift" = the vs_open ratchet, bluff-aware taper, nit/rock
  tighten) all shipped this session.

**Tech debt (lower urgency):**
- **`DEPTH_INTENT_TAG_TECHDEBT.md`** — migrate to an explicit `intent: value|bluff`
  tag to retire the `vs_open` weight-cliff implicit API.
- **Squeeze v1 refinements** (#294): per-opener squeeze key (currently a mean over
  openers), and the jam tier reaching down to 99/TT.

**Postflop calibration (optional, believability):**
- nit/rock WTSD (structural warn, see fact #6) — only if a play-test says they show
  down too much; would mean revisiting the band or a later-street fold lever.

## Workflow notes

- Everything via PRs + auto-merge. The merge loop distinguishes the **known flaky
  test** (rerun once) from a **real failure** (stop, don't merge) — it caught a real
  one (#308 pinned `test_rock_carries_passive_postflop`).
- **Pre-push hook reformats** (ruff/black/isort) and aborts the push, leaving a
  working-tree diff → stage + `commit --amend` + re-push. Expect this dance.
- `scripts/` is gitignored; specific scripts (incl. the probe) are force-added — use
  `git add -f` if a fresh one needs committing.
- Tests run in Docker. Probe ~10 min at 9000. **Iterate at 1500** for VPIP (n~1234,
  stable); fold-to-3bet/postflop stats are low-n even at 9000 — only trust them at
  the full N, and treat single-stat deltas under ~CI as noise.
- `[EMOTIONAL] Failed to read zone effects` + eval7 pyparsing lines flood probe
  output — `grep -avE "EMOTIONAL|Pyparsing|setParse|delimited|rangestring"`.
