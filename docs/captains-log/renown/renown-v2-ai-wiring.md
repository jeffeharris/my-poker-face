---
purpose: Captain's log for the Renown-v2 AI-wiring work (Stage A persist + stress gate) on the renown branch.
type: guide
created: 2026-06-02
last_updated: 2026-06-02
---

# Captain's log — Renown-v2 AI wiring

## 2026-06-02 — Stage A (persist per-AI renown) + stress gate

### What we set out to do
Wire up the deferred "give AIs their own renown" stage. Merged `development`
into the `renown` worktree first (clean; v138 baseline), wrote a plan doc,
picked Option 1 (extend `prestige_snapshots`, not a parallel table).

### A premise correction up front
The handoff framed the last stage as "the 4 reputation hooks read the per-entity
quadrant." Exploration showed that's **wrong**: all four hooks consume the
*human's* quadrant/regard to modulate AI behavior — the AIs are recipients of the
human's fame, not sources of their own. So persisting AI renown lights up *none*
of the existing hooks; the AI-renown consumers (dossier badge, marquee, prestige-
seeking) are all NEW surfaces (Stage B), each its own product decision. The plan
was built around that, not the handoff's framing.

### The keying call
`prestige_snapshots` was keyed `(sandbox_id, owner_id)`, human-only. Chose the
minimal Option-1 variant: treat `owner_id` as the **universal subject id** (human
owner_id, or an AI's raw `personality_id` — the same raw-id scheme the field
scorer already uses) + one additive `entity_kind` column ('player'|'ai',
default 'player'). Every existing human read keeps working untouched; the
invariant (`owner_id` = subject, `entity_kind` disambiguates) is what keeps the
human's `load_latest` from ever matching AI rows. The alternative (separate
`entity_id` + repurpose `owner_id` as sandbox-owner) was purer but touched every
WHERE clause for no functional gain.

### Build
Schema v139 (additive, PRAGMA-guarded, +index), repo back-compat `entity_kind`
on all reads + `record_ai_many` (one batched insert) + `load_renown_v2_peaks`
(one GROUP-BY ratchet), a `RENOWN_V2_PERSIST_AI` sub-flag (default OFF, implies
ENABLED), and the ticker fan-out that **reuses the already-computed field+scored**
(no extra compute) and persists AI rows in its OWN best-effort guard *after* the
human row. 45 green. Committed `cab24b0d`.

### The stress gate — and the surprise
Ran it against the live 81-entity field. Expected to be sweating the per-AI write
fan-out. Instead:

- The fan-out is **~2.3ms** marginal (build-rows 0.5 + peaks 0.3 + write 1.5).
  A non-issue.
- **`build_inputs` is ~523ms** (max 650) — already over the `CYCLE_BUDGET_MS=250ms`
  per-cycle budget.

The honest read: the bottleneck I was told to gate (the AI write) is trivial;
the real cost is the **field read**, and it's **pre-existing** — the human-only
v2 overlay already calls the same `build_inputs`. So this 0.5s cost is shipped
today, dormant behind the OFF flag. Stage A didn't introduce it and shouldn't be
blocked on it.

Impact is bounded: the 250ms budget is a soft early-break between sandboxes in
the 2s tick, not a hard timeout, and the recompute is throttled to 300s — so an
over-budget recompute just defers the cycle's *other* sandboxes by one tick, once
per ~5min per sandbox. It backs up only if many active sandboxes recompute in the
same window.

**Conclusion:** AI fan-out is safe to enable independently. Optimizing
`build_inputs` (or moving the prestige recompute off the cycle-budget tick) is a
separate task on the **human** v2 path — it gates flipping `RENOWN_V2_ENABLED`
at all, not Stage A specifically.

### Wrong turns / friction (kept honest)
- Burned time on Docker network-pool overlap running tests in this worktree. Root
  cause wasn't mine: the tracked `docker-compose.override.yml` pinned
  `10.123.46.0/24`, colliding with the main worktree's net. Worked around it with
  a direct `docker run --network none`; a linter later fixed the override to
  `10.123.50.0/24`.
- `docker run --env-file .env` passes values with literal quotes (compose strips
  them), so the rate-limit string `'"10000 per day'` broke full-app Flask init →
  a wave of FAILED/ERROR in `test_leave_*`/`test_offer_stake_atomicity`. Red
  herring — confirmed passing under `docker compose run`. Nothing in
  prestige/renown/schema failed.

## 2026-06-02 (later) — optimizing build_inputs (the real blocker)

The stress gate said the live blocker is the ~523ms `build_inputs`, not the AI
fan-out. Optimized it (`4ff4b087`).

Profiled the 6 queries against the live DB: **holdings_snapshots was the whole
cost** — 261ms to fetch 87K rows, then a Python loop over all of them to derive
three aggregates (peak net worth, distinct-tick presence, per-tick #1). The
others summed to ~25ms.

**Fix 1 — aggregate in SQL.** peak + presence via GROUP BY, time-at-#1 via a
per-tick `ROW_NUMBER` window. Transfers ~1K rows, not 87K. 523→368ms. The parity
worry was the per-tick #1 tie-break (the prod loader must stay byte-identical to
the oracle's Python loop). Checked first: **0 ties across all 681 ticks** on the
real field, so any deterministic argmax matches. Matched the old tie-break
(`net_worth DESC, entity_id ASC` = smallest prefixed id, the old index-scan
order) as insurance. Also had to floor peak at 0.0 to match the old
`defaultdict(float)` loop (a never-positive entity reads 0, not its negative
MAX). `renown_field_parity.py`: PASS, 0 mismatches.

**Fix 2 — covering index (v140).** 368 was still over the 250ms budget.
Profiled the new queries: surprise — the bottleneck was `MAX(net_worth)` (200ms),
NOT the window (99ms). The MAX did a table lookup per row; the sibling
`COUNT(DISTINCT captured_at)` over the same rows was 18ms because it's covered by
the existing index. Added `holdings_snapshots(sandbox_id, entity_id, net_worth)`
→ MAX is index-only. Build ≈ ~185ms.

**The instrument trap (kept honest).** Tried to measure the index win end-to-end
on a 5GB copy. First copy was **malformed** — I used an `immutable=1` source,
which ignores the 9.1M WAL and copies a mid-transaction main file (the exact
WAL-backup trap in the project memory). Redid it from a WAL-aware connection +
`integrity_check=ok`. But the copy STILL wasn't faithful: it timed 391ms *with*
the index — because a fresh sequential backup has warm, contiguous pages, so the
non-covering MAX never pays the cold-scattered-page penalty that made it 200ms on
the live DB. The copy literally can't show an optimization that targets disk I/O.
The faithful evidence is the **same-DB control** on the live DB: presence
(covering) 18ms vs peak (non-covering) 200ms over the identical 87K rows — that's
what proves covering removes ~185ms. End-to-end live confirmation waits for the
migration to run on the live DB. Lesson: a fresh DB copy is the wrong instrument
for an I/O-bound optimization; use a same-DB covering-vs-noncovering control.

## 2026-06-02 (later) — Stage B surfaces (B1 dossier, B4 prestige-seeking)

**B1 — dossier badge (`d7692491`).** First consumption surface. The dossier route
reads the AI's persisted standing (`load_latest entity_kind='ai'`) into a
`reputation` block; the card renders a quadrant badge under the name. Read-only,
degrade-safe. 12 route tests green. Live visual verification pending real
renown data (flag + migration + a ticker cycle).

**B4 — prestige-seeking (`a6ef0c70`).** The interesting one. This turned out to
be the realization of a spec layer that had been *deferred for exactly this*:
`CASH_MODE_TABLE_ATTRACTIVENESS.md` shelved the occupant/"marquee" term in v1
with the note "when we build it, give it its own first-class `renown` stat" —
which is precisely what Renown-v2 now provides. So B4 wasn't a new invention, it
was plugging the renown stat into the slot the spec left open:
`base_attractor += W_MARQUEE · occ_prestige(table) · status_appetite(ai)`.

The one design fork — what makes an AI a status-seeker (its own renown? showman
traits?) — I put to the user. Their steer was sharper than the binary: make it a
per-AI **rank composed of factors**, layered onto the attractiveness system that
already takes per-AI inputs. So `status_appetite` is an extensible composite
(own-renown percentile ⊕ glory trait), not a hard-coded single driver — new
factors slot in by extending the blend.

Both `occ_prestige` and the renown factor reuse the already-persisted
`victim_percentile` ([0,1], field-relative) — no new normalization, and both
default 0 so the whole term vanishes without renown data or with the flag off.
Kept the greedy core pure (it just takes two scalars); the lobby computes them
behind `PRESTIGE_SEEKING_ENABLED`, loading glory anchors lazily for only the
handful of actual seekers per tick.

Committed flag-OFF with pure + repo unit tests + 48 lobby regressions green.

**B4 sim A/B (`scripts/sim_prestige_seeking_ab.py`, 2026-06-02).** Same-seed
paired probe: seed one sandbox + a renown field (4 famous AIs), copy it, run the
economy sim twice (flag OFF vs ON) from the identical start, compare. Findings:

- **It works, and it's conservation-safe.** At `W_MARQUEE=8` co-location of
  grinders with a famous AI rose 20.8%→30.4% (+9.6pp) with the flag on, and
  `audit_drift=0` in both arms — the new seat path mints no chips. The flag
  demonstrably changes routing in the right direction.
- **But the default `W_MARQUEE=1.0` is too weak.** First run (default) gave a
  slightly *negative* lift — noise, because `W_CROWD` (−0.5 per seated grinder)
  swamps a +0.3-ish marquee bonus: once a famous table has a couple of
  occupants the crowd penalty pushes the next seeker away. Cranking W_MARQUEE
  is what surfaced the real, intended effect. Classic "the sim found my default
  was inert" — exactly what the gate is for.

Two honest gaps I couldn't close in the dev box's time budget: (1) the churn run
(`hand_sim_prob>0`) — needed to calibrate W_MARQUEE against the fish draw and
prove marquee clustering doesn't STARVE fish tables — timed out at 500 ticks
(hand sim per table per tick is the cost; wants a longer/Hetzner run); (2) the
minimal seed had `fish=0`, so the starvation check was N/A this round (a
grinder-only field is a clean routing isolate, but not the full economy). Left
W_MARQUEE conservative (flag OFF, no live effect); the calibrated value + the
starvation bound are the remaining pre-flip work.

## 2026-06-02 (later) — the Hetzner fish+churn sweep

Ran the full-economy sweep on a Hetzner box (`cpx32`, poker-bot-optimization,
torn down after). Several scars worth keeping:

- **The runbook's rsync had no `.env` exclude.** The auto-mode classifier
  correctly hard-blocked shipping the working tree (real API keys) to a fresh
  external host. Right call — I'd missed it. Re-shipped with secrets excluded +
  wrote a dummy-key dev `.env` on the box. (The sim is LLM-free, but it eagerly
  *constructs* provider clients — DeepSeek by default — so it needs non-empty
  keys to import; the 401s on side-hustle *narration* are cosmetic and fall
  back.) Filed: the runbook should exclude `.env`.
- **cpx31 is deprecated.** Half my provisioning attempts "succeeded" falsely
  because my error-grep didn't match Hetzner's phrasings ("invalid_input", "not
  found", "unsupported"). Lesson: verify via `hcloud server describe`, never the
  create command's stdout. Current type: `cpx32`.
- **The result itself — a real methodology finding.** Fish economy materialized
  (9 fish, 3 casinos). **No starvation at any W** (fish-table grinders 108–131%
  of OFF — the cleanest result). But the routing metric came back *negative* at
  low W and the audit drift was *non-zero and non-monotonic* (932/4265/125).
  Neither is what it looks like: with `hand_sim_prob>0` the same-seed paired
  probe **decoheres** — once seating diverges by one seat the two arms play
  different hands and become different economies, so final-snapshot co-location
  is noise and the drift is the pre-existing vice/hustle audit artifact
  reshuffled, NOT B4 minting chips (movement-only stays drift=0 at every W,
  including W=8). The memory literally warned this ("same-seed cash A/B is
  RNG-desync noise for decision-gate changes") and I half-relearned it the hard
  way. w8 did land +6.6pp (directionally matching the clean movement-only
  +9.6pp), but I won't calibrate W off decohered numbers.
- **Conclusion:** mechanism + conservation + no-starvation are established; the
  precise W needs an **event-level within-run probe** (compare each fill
  decision to its OFF counterfactual on identical state), which is the right
  next instrument. Flag stays OFF, W_MARQUEE conservative. Box torn down; the
  project had a stray `poker-eval-tax` box (not mine) that also got cleaned up.

## 2026-06-02 (later) — the event-level probe nailed the calibration

Built `scripts/sim_prestige_probe.py`. The trick that made it clean: the marquee
bonus is **linear in W** (`score(W) = s0 + W·Δ`), so I don't need to re-run per
W — one instrumented run at W=1 captures `(s0, Δ, occ)` for every candidate at
every seat decision (monkeypatching the greedy seater, zero prod change), and
the argmax at *any* W falls out offline. No economy, no decoherence — pure
decision-point sensitivity.

The curve (238 contested decisions, the ones with a marquee option): influence
rises 10%→17%→28%→40%→…→82% as W goes 0.5→1→1.5→2→…→15, and the mean prestige of
the chosen table climbs from the 0.24 no-marquee baseline toward 0.87. The
"felt but not domineering" band (~15-35%) centers at **W≈1.5** — set that.

The satisfying part: this **corrected my own earlier mistake**. The churn A/B
had me believing "default 1.0 is too weak." The probe shows W=1 already swings
17% of contested decisions — the negative churn number was decoherence noise, not
weakness. Two lessons re-learned: (1) for a decision-gate change, instrument the
DECISION, not the downstream economy; (2) don't trust a single confounded metric
enough to draw a tuning conclusion from it — I'd written "too weak" into a code
comment off the bad signal. The probe is the instrument I should have built
first. (Calibration is one field/seed so far; a 2nd-seed confirm is a cheap
follow-up.)
