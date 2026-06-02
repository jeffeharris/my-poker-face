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
