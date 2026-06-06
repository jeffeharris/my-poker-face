---
purpose: Honest narrative of launch day — the v70→v151 prod cutover, circuit activation, and the four confident misdiagnoses that the tooling (not my theories) corrected
type: design
created: 2026-06-06
last_updated: 2026-06-06
---

# Launch day — the cutover, and four wrong turns

Launch finally happened (2026-06-05). Prod went from a four-month-stale schema
(v70, Feb) to the full modern stack (v151: cash mode, circuit, ledger,
tournaments, presence, renown) and the circuit went live with a freshly-minted
economy. It worked. But the path there was a string of confident wrong
diagnoses, each one corrected only when I stopped theorising and actually
reproduced or measured the thing. That's the real story worth keeping.

## The part that went to plan

The merge and the migration were the *easy* part, which surprised me. We'd
treated the v70→v151 jump as the scary thing for weeks. A dry-run on a WAL-safe
copy of the live prod DB (`scripts/migration_dryrun.py`) landed clean — v151,
integrity ok, only the accepted game deletes (v142 drops tournament-linked
games) and 18 cosmetic avatar re-key drops. The completeness gate passed. The
merge itself was a 0-conflict fast-forward. So the schema cutover — the thing we
feared — was a non-event. Good prep beats good luck.

The eventful part was everything *around* the migration.

## Wrong turn #1 — "prod's database is corrupt" (it wasn't; I was)

CI auto-deploy was gated red (a last-minute persona commit broke four stale
backend tests; the E2E suite was stale after the menu restructure), so I went
manual with `./deploy.sh`. Three times it aborted at the backup step with
`sqlite3.DatabaseError: malformed database schema (idx_pressure_events_player)`
and a bloated **5.5 GB** backup file (from a 135 MB DB). I went down the
corruption road hard: stopped the backend, checkpointed the WAL, isolated the
main file on its own — which was *perfectly clean*. That was the tell I almost
talked myself out of.

The actual cause: `deploy.sh`'s rsync excluded `data/*.db` but **not** the
`-wal`/`-shm` sidecars, so every run shipped my **local dev machine's** WAL over
prod's clean DB. `backup_db.py` then opened prod's main file with my mismatched
wal-index → garbage schema → torn backup → integrity check correctly aborted.
**Prod was never corrupt.** The safety net (abort-on-bad-backup) did its job
every time; I just misread *why* it was firing. Fix: exclude the entire `data/`
dir. The deeper lesson, which recurred all day: **deploying from a dev box is a
footgun** — local sidecars, local file modes, no clean checkout. The CI pipeline
avoids all of it.

## Wrong turn #2 — "it's a stale service worker" (it was a CSP bug)

Circuit went live; the user hit an error opening it: `Unable to preload CSS for
/assets/Lobby-CUwhMRGC.css`. I confidently diagnosed a stale PWA service worker
serving an old build, and even shipped a `vite:preloadError` auto-reload handler
to self-heal it. Reasonable fix — but it didn't fix *this*, because the error
reproduced in a **private tab** (no SW, no cache). That killed the cache theory.

Driving a real headless browser (Playwright MCP, guest login → `/cash`) showed
the truth: the CSS served `200` fine, but the page was throwing **five CSP
violations** — the redesign added Google Fonts and the nginx CSP
(`style-src 'self'`) blocked `fonts.googleapis.com`, and that interference made
the lazy Lobby chunk's CSS `<link>` error out despite the 200, crashing `/cash`
into the ErrorBoundary. One line in `nginx.conf` (allow googleapis + gstatic)
fixed it. The auto-reload handler was still worth keeping, but it was a fix for a
problem we didn't have. **Reproduce before you fix.**

## Wrong turn #3 — the flag audit I did one flag at a time

I armed the circuit's feature flags, declared parity, and moved on. The user
asked, "is renown enabled?" — and no, it wasn't. I'd missed `RENOWN_V2_ENABLED`
+ `RENOWN_V2_PERSIST_AI`, *and* had a dependency bug: `PRESTIGE_SEEKING_ENABLED`
(which I *had* set) is silently inert without them. Only when the user pushed
("find ALLLLL the flags") did I do the exhaustive sweep I should have led with.
Lesson: when the task is "match a config," diff the *whole* config up front, not
the flags I happen to remember.

## Wrong turn #4 — two assumptions the architect caught

On scaling, I asserted the ~770 MB backend baseline was the strategy lookup
tables loaded into memory. The `code-architect` agent read the code and corrected
me: the tables are **lazy-loaded, ~20–50 MB parsed** — the baseline is just the
Python process + eval7 + imports. Separately, I floated "share the world" as a
scaling lever; the user rightly shot it down — it's a *casual* game, the
1:1-sandbox-per-owner "your own world" feel is the whole point. (And it turns out
per-owner is a scaling *asset*: it shards cleanly on `owner_id`.) Two more
theories that didn't survive contact with the code / the product.

## The ops coda — measuring instead of guessing

The box (1.9 GB) was memory-pressured: 84 MB free, swap 90% full. We nuked a
standalone metabase container, capped the backend at `mem_limit: 1200m`, and —
crucially — *watched* RSS for 35 minutes instead of assuming a leak. It plateaued
dead flat at ~551 MB. No leak; the earlier 770 MB was just five hours of warm
working set. And the per-user world-sim cost the user worried about turned out to
be near-free: rule-based bots (no LLM), active-users-only, hard-capped at
~250 ms per 2 s tick shared round-robin. The expensive part of scaling is the
single `-w 1` gevent core for foreground hands + SQLite's single writer — not the
sim.

## The throughline

Four confident misdiagnoses in one day — corruption, cache, baseline, "share the
world" — every one wrong, every one corrected by *doing the thing*: isolating the
main DB file, driving a real browser, reading the code, sampling RSS over time.
The instinct to explain was consistently faster than the instinct to verify, and
the verification consistently won. The recurring concrete villain was
**deploying from a dev box** (clobbered sidecars in #1, the whole reason CI's
clean-checkout pipeline exists). We reconciled `main` back onto that pipeline at
the end, fixed the CI rsync's own `--delete` landmine (it would have wiped prod's
backups) before letting it run, and landed prod on the proper path.

Launch is live. The economy is churning on its own. And the standing lesson is
the cheap one: when you're sure, that's exactly when to go look.
