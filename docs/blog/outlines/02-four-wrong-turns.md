---
purpose: Ready-to-write outline for the Devlog post "Four wrong turns on launch day" — the June 5 2026 prod cutover and four confident misdiagnoses corrected only by reproducing/measuring
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Outline — Four wrong turns on launch day

- **Working title:** Four wrong turns on launch day
- **Track:** Devlog
- **Target reader:** Solo builders / build-in-public devs who ship their own infra and want an honest debugging story, not a victory lap. People who know the feeling of being *sure* and being wrong.

## One-line hook

> The schema migration we'd feared for weeks was a 0-conflict non-event. The four things that actually went wrong were all my own confident wrong guesses — and every one was corrected only by reproducing it instead of explaining it.

## Narrative spine (section beats, in order)

1. **The thing we feared was the easy part.** Prod had been frozen on a four-month-stale schema (v70, February) while the modern stack — cash mode, circuit, ledger, tournaments, presence, renown — piled up on a branch. The v70→v151 jump was the scary item on the board for weeks. A WAL-safe dry-run on a copy of live prod (`scripts/migration_dryrun.py`) landed clean; the merge was a 0-conflict fast-forward. "Good prep beats good luck." The eventful part was everything *around* the migration.

2. **Wrong turn #1 — "prod's database is corrupt" (it wasn't; I was).** CI auto-deploy was gated red, so I deployed manually with `./deploy.sh`. It aborted three times at the backup step with `malformed database schema` and a bloated 5.5 GB backup (from a 135 MB DB). I chased corruption hard — until isolating the main DB file showed it was perfectly clean. The real cause: the deploy rsync excluded `data/*.db` but not the `-wal`/`-shm` sidecars, so it shipped my *local dev machine's* WAL over prod's clean DB; the backup then read the main file through a mismatched wal-index. Prod was never corrupt. The safety net fired correctly every time; I just misread *why*. (Fix: exclude the whole `data/` dir.)

3. **Wrong turn #2 — "it's a stale service worker" (it was a CSP bug).** The circuit went live, the user hit `Unable to preload CSS for /assets/Lobby-*.css`, and I confidently diagnosed a stale PWA service worker — even shipped a `vite:preloadError` auto-reload handler to self-heal it. But the error reproduced in a private tab (no SW, no cache), which killed the theory. Driving a real headless browser showed five CSP violations: the redesign added Google Fonts and the nginx `style-src 'self'` blocked `fonts.googleapis.com`, which crashed the lazy Lobby chunk into the ErrorBoundary despite a 200. One nginx line fixed it. The auto-reload handler was a good fix for a problem we didn't have.

4. **Wrong turn #3 — the flag audit I did one flag at a time.** I armed the circuit's feature flags, declared parity, and moved on. The user asked "is renown enabled?" — and it wasn't. I'd missed `RENOWN_V2_ENABLED` + `RENOWN_V2_PERSIST_AI`, and had a silent dependency bug: `PRESTIGE_SEEKING_ENABLED` (which I *had* set) is inert without them. Only when the user pushed — "find ALLLLL the flags" — did I do the exhaustive sweep I should have led with. Lesson: when the task is "match a config," diff the *whole* config up front.

5. **Wrong turn #4 — two assumptions that didn't survive contact.** On scaling I asserted the ~770 MB backend baseline was strategy lookup tables in memory; a code-reading agent corrected me — they're lazy-loaded (~20–50 MB parsed), the baseline is just Python + eval7 + imports. Separately I floated "share the world" as a scaling lever, and the user shot it down on product grounds: it's a casual game, the 1:1-sandbox-per-owner "your own world" feel is the whole point (and per-owner shards cleanly on `owner_id` anyway). Two more theories, both wrong.

6. **The ops coda — measuring instead of guessing.** The box (1.9 GB) was memory-pressured (84 MB free, swap 90% full). We removed a standalone Metabase container, capped the backend at `mem_limit: 1200m`, and — instead of assuming a leak — *watched* RSS for 35 minutes. It plateaued dead flat at ~551 MB. No leak; the earlier 770 MB was just five hours of warm working set.

7. **The throughline.** Four confident misdiagnoses in one day — corruption, cache, baseline, "share the world" — every one corrected by *doing the thing*: isolating the file, driving a browser, reading the code, sampling RSS over time. The instinct to explain was consistently faster than the instinct to verify, and verification won every time. The recurring concrete villain: deploying from a dev box. When you're sure, that's exactly when to go look.

## Evidence & assets

**Hard numbers to cite (all from the captain's-log primary source):**
- Schema jump: **v70 (Feb) → v151** on the June 5 2026 cutover; merge was a **0-conflict fast-forward**.
- Wrong turn #1: backup bloated to **5.5 GB** from a **135 MB** DB; aborted **3 times**.
- Wrong turn #2: **5 CSP violations**; CSS served **200** but page crashed to ErrorBoundary.
- Wrong turn #4 / coda: claimed **~770 MB** baseline → actually **~20–50 MB** parsed tables; box is **1.9 GB**, **84 MB free**, swap **90% full**; capped backend at **1200 MB**; RSS watched **35 min**, plateaued flat at **~551 MB**.

**Commits to reference (real subjects, verified in `git log`):**
- `db026939` — `fix(ci): exclude entire data/ from deploy rsync (protect prod DB + backups)` (wrong turn #1 fix)
- `3be5648f` — `fix(deploy): exclude data/, caches, and .env from rsync` (related #1)
- `34c397c3` — `fix(pwa): auto-reload on vite:preloadError to self-heal stale deploys` (the #2 fix that fixed the *wrong* problem)
- `3463afd3` — `fix(csp): allow Google Fonts (style-src/font-src) — unbreaks the circuit` (the actual #2 fix)
- `f8f10bb1` / `ba7390bd` — CSP connect-src hotfix, then self-hosting fonts via Fontsource (the durable follow-up; ties to the CSP-fonts MEMORY note)
- `417a1953` — `feat(prod): enable Renown-v2 flags (dev parity + prestige-seeking dep)` (wrong turn #3 fix)
- `4cdbcfda` — `feat(prod): arm cash-economy/circuit launch flags in compose`
- `e945fecf` — `ops(prod): cap backend memory (mem_limit 1200m) on the 1.9G box` (coda)
- `983f2b38` / `ae20932b` — build off-box → GHCR → prod pulls (the structural answer to "deploying from a dev box is a footgun")

**Screenshots/images available (use sparingly; this is a debugging post, not a feature post):**
- `react/react/src/assets/screenshots/desktop-table.png` — the live circuit/table the cutover shipped (establishing "this is what went live").
- No infra/terminal screenshots exist in the repo. The deploy/RSS/CSP moments would need fresh terminal captures from the founder if we want visual evidence — text/commit citations carry the post fine without them.

## Candidate pull-quotes (verbatim)

Real human chat prompts (extracted from June 5–6 2026 transcripts):
- > "find ALLLLL the flags" — the push that triggered the exhaustive flag sweep (wrong turn #3).
- > "whats the sim per user cost us? i jusy dont see a way to share the world, its supposed to be casual and that seems like it would make it competitive and it's not going to fit the game." — the user killing the "share the world" idea (wrong turn #4). *Typos verbatim; clean up only if house style requires.*
- > "add the backend mem_limit and watch RSS for a leak and remove metabase. that seems like a lot of RAM" — the "measure, don't assume" instruction behind the coda.

Real commit subject as a pull-quote:
- > `fix(pwa): auto-reload on vite:preloadError to self-heal stale deploys` immediately followed by `fix(csp): allow Google Fonts — unbreaks the circuit` — the two-commit fingerprint of fixing the wrong thing, then the right thing.

## Draft intro paragraph (post voice)

> For weeks the scary item on the launch board was the database migration — a four-month-stale production schema (v70) that had to jump all the way to v151: cash mode, a circuit economy, a chip ledger, tournaments, the works. On launch day it landed in zero conflicts and a clean dry-run. It was a non-event. What actually ate the day was a string of things I was *sure* about and wrong about: a "corrupt" database that was pristine, a "stale cache" bug that was a CSP rule, a feature-flag audit I did one flag at a time, and a memory leak that was just a warm working set. Each one only resolved when I stopped explaining it and went and reproduced it. This is the honest version of that day.

## Open gaps (need the founder or more reporting)

- **Exact date/time framing.** The doc dates the cutover 2026-06-05 in the body but the header says created 2026-06-06; confirm whether the post should say "launch day (June 5)" and treat the 6th as cleanup, or fold both into one "launch day."
- **Whether the "share the world" exchange belongs.** It's a product decision, not strictly a *debugging* wrong turn — it strengthens the "theories that didn't survive contact" throughline but slightly widens scope. Founder call on keeping it as #4 vs. trimming to three pure-debugging turns plus the coda.
- **CI red-gate detail.** The doc says a last-minute persona commit broke four stale backend tests and the E2E suite was stale after a menu restructure. If we want to name that, confirm it's fine to surface (ties to the FloatingChat reduced-motion fix and E2E gate being turned off — see MEMORY notes).
- **Visuals.** No terminal/infra screenshots exist; decide whether the founder grabs a couple (the 5.5 GB backup, the RSS plateau, the CSP console errors) or we run text-only.
- **"What I changed afterward."** A short closing on process changes the founder actually adopted (CI off-box builds via GHCR; "reproduce before fix" as a rule) would land the post — confirm which of these are real standing changes vs. one-offs.

## Cross-links (series)

- **Post 01 (Devlog / origin arc)** — the "began as a console engine in a 10-day July 2023 burst" piece. This post is the far end of that arc: the same solo builder, now with an AI pair, shipping to prod. Link back on "the core identity predates the AI pair; the pair was a force multiplier."
- **A future "AI pair / how I actually work with Claude Code" post** — every wrong turn here was *caught by the loop* (user pushes, agent reads code, I reproduce). This is the concrete case study for that post; foreshadow it.
- **A "living economy / what keeps you coming back" post** — the cutover is what put cash mode + circuit + renown in front of real users; this post can hand off to the design story of *why* those systems exist.
