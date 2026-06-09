---
purpose: Ready-to-write outline for the Devlog post on the v70 to v151 prod schema cutover and the four wrong turns around it
type: vision
created: 2026-06-09
last_updated: 2026-06-09
---

# Outline — Migrating a 4-month-stale schema without losing a chip

- **Working title:** Migrating a 4-month-stale schema without losing a chip
- **Track:** Devlog
- **Target reader:** Solo devs / small teams who run a real production database and fear the "big migration" — people who want a grounded account of how the scary part went easy and the boring parts went sideways.

## One-line hook (grounded)

The schema migration we feared for weeks — production at v70, four months stale, jumping to v151 — landed as a 0-conflict non-event. Everything *around* it is what nearly broke launch.

## Section beats (narrative spine, in order)

1. **The setup: prod was frozen at v70 (Feb), dev was at v151.** Production had been running a four-month-stale schema while a 9-day May sprint built cash mode, the circuit, the ledger, tournaments, presence, and renown on top of it. The June 5 cutover had to walk roughly 80 migrations forward on a live 135 MB DB with real player chips in it — losing or minting a chip was the unacceptable outcome.

2. **The landmine found by accident: `schema_version = N` doesn't prove the schema is complete.** Days before the deploy, a renown-tuning sim died on `no such column: entity_kind` against a dev DB stamped v148 — a column the v139 migration should have added. The migrations 139/140 had been *renumbered* on a branch merge to numbers that DB had already passed, and the walk only runs versions `> MAX(version)`, so they never ran. (Cite the renumber commit `4cd2f88e`: "renumber tournament+avatar migrations 132-138 -> 141-147 to clear the development collision.")

3. **The wrong turn avoided: "systemic, prod is at risk" vs. one drifted DB.** First instinct was that the base `CREATE TABLE` was broken so every fresh build was broken. Building a fresh DB via `ensure_schema()` and checking — it *had* the column — flipped the diagnosis in one step from "systemic" to "drift on one long-lived DB." Worth stating plainly as the post's first lesson: the check that costs two minutes is the one that reframes the whole risk assessment.

4. **What we built so "probably safe" became a checkable invariant.** A completeness gate (`scripts/schema_completeness_check.py`) that diffs any DB against a fresh canonical build and fails on any missing table/column/index; a CI contiguity guard so a future renumber can't silently leave a hole; a migration dry-run (`scripts/migration_dryrun.py`) against a WAL-safe copy of the live prod DB. (Commit `e5834799`: "harden the migration path — completeness gate + contiguity guard (prod-cutover prep)".)

5. **Launch day: the migration was the easy part.** The dry-run landed clean at v151, integrity ok, only the accepted v142 game deletes and 18 cosmetic avatar re-key drops; the merge itself was a 0-conflict fast-forward. "Good prep beats good luck." This is the pivot — name explicitly that the feared thing was a non-event, and the rest of the post is the un-feared things that weren't.

6. **The four wrong turns, each corrected by measuring not theorizing.** (1) "Prod's DB is corrupt" — actually `deploy.sh` rsync shipped the dev box's `-wal`/`-shm` sidecars over prod's clean main file, producing a 5.5 GB torn backup from a 135 MB DB; the abort-on-bad-backup safety net was *right* every time. (2) "Stale service worker" CSS preload error — actually a CSP bug; it reproduced in a private tab (no SW), and a real headless browser showed five CSP violations from the redesign's Google Fonts. (3) The feature-flag audit done one flag at a time, missing `RENOWN_V2_ENABLED` (which made the flag I *had* set silently inert) until the user said find them all. (4) Two scaling assumptions (the 770 MB baseline, "share the world") that didn't survive contact with the code or the product.

7. **The throughline + the sequel.** The recurring villain was deploying from a dev box, which is exactly why the CI clean-checkout pipeline exists; we reconciled `main` back onto it and fixed CI's own rsync `--delete` landmine before letting it run. And the migration chain we'd just walked got squashed days later — v1..v157 collapsed to a generated baseline — which is the natural follow-up post.

## Evidence & assets

**Real numbers / facts to cite (all from primary docs):**
- Prod schema **v70 (Feb) → v151** on 2026-06-05; ~four months stale.
- Drifted dev DB: `schema_version` history had **146 rows, max 148 — missing exactly rows 139 and 140**; drift was **one missing column + two missing indexes**.
- Corrupt-backup wrong turn: **5.5 GB** torn backup from a **135 MB** DB; aborted **three times**.
- CSP wrong turn: **five CSP violations**; `style-src 'self'` blocked `fonts.googleapis.com`; one nginx line fixed it.
- Migration result: clean dry-run, integrity ok, only v142 game deletes + **18** cosmetic avatar re-key drops; **0-conflict** fast-forward merge.
- Memory baseline: asserted ~**770 MB** = lookup tables; actually lazy-loaded **~20–50 MB**; RSS watched **35 minutes**, plateaued flat at **~551 MB** (no leak).

**Commits to reference (verified in main-project git log):**
- `e5834799` — harden the migration path: completeness gate + contiguity guard (prod-cutover prep)
- `4cd2f88e` — renumber tournament+avatar migrations 132-138 -> 141-147 to clear the development collision (the renumber that caused the drift)
- `1f74f67c` — renumber self-heal for old-tournament DBs + isolate v147 migration test
- `3463afd3` / `f8f10bb1` — CSP fix: allow Google Fonts (the wrong-turn #2 fix)
- `ba7390bd` — self-host fonts via Fontsource, drop Google Fonts + CSP exceptions (#206) (the permanent fix that retired the exception)
- `db026939` / `3be5648f` — exclude entire data/ from deploy rsync (the sidecar footgun fix)
- `b0deec9d` — squash v1..v157 migration chain to a generated baseline (#241) (the sequel)

**Docs:** `docs/captains-log/tournaments/schema-drift-and-migration-path.md`; `docs/captains-log/development/launch-day-cutover-and-four-wrong-turns.md`; referenced in-repo: `PROD_MERGE_PLAN.md`, `MERGE_TO_MAIN_PUNCHLIST.md`.

**Screenshots/images:** This is an infra/ops post — no obvious in-product screenshot fits. Candidate visuals are *terminal artifacts*, which the founder would need to provide or recreate (see Open gaps): the dry-run output landing at v151, the completeness-gate "0 missing" pass, or the RSS-over-35-minutes plateau. The asset dirs (`react/react/src/assets/screenshots`, `.images`) hold product UI shots (coach-tip, range-explorer, preflop-leaks, mobile-*) — none directly relevant. Recommend a plain code/terminal block instead of a UI screenshot.

## Candidate pull-quotes (verbatim)

**From the human (tournaments transcript, 2026-06-03/04):**
- > "let me know what i need to do to fix it, we will be deploying to prod this week hopefully and want to have that migration path ironed out"
- > "yeah save it to memory and proceed with the avatar legacy-kill. create a plan doc for merging dev into prod, just put a section for things like this we need to be aware if when we finally merge"
- > "lets renumber our migrations, then pull in development"

**From the docs (verbatim lines worth lifting):**
- > "`schema_version = N` does not prove the schema is complete — that was the real lesson, and it matters because prod is about to migrate."
- > "The merge and the migration were the *easy* part, which surprised me. We'd treated the v70→v151 jump as the scary thing for weeks."
- > "The instinct to explain was consistently faster than the instinct to verify, and the verification consistently won."

## Draft intro paragraph (post voice)

> For weeks the scariest item on the launch checklist was a number: production was pinned at schema v70 from February, and the code we wanted to ship was at v151. Cash mode, the circuit, the ledger, tournaments — all of it sat on top of roughly eighty migrations that had never run against the live database, the one with real player chips in it. I budgeted my anxiety for the migration. I budgeted wrong. The migration was a 0-conflict non-event; the four things I was *sure* about around it — a corrupt database, a stale cache, a memory leak, a baseline number — were each wrong, and each one only got fixed when I stopped explaining and went and measured.

## Open gaps (need founder)

- **Terminal artifacts for visuals.** The best assets here (dry-run output, completeness-gate pass, RSS plateau graph) aren't in the repo's image dirs. Founder to confirm whether logs/screenshots from the June 5 cutover still exist, or whether to recreate a clean run.
- **The "find ALLLLL the flags" quote.** The launch-day log paraphrases it; the verbatim human prompt is in a June 5 transcript dir I didn't locate (prep-for-main is May 29; tournaments is June 3-4). Founder can confirm exact wording or point at the dir if they want it quoted directly.
- **Exact migration count.** I say "roughly eighty" (v70→v151 ≈ 81 versions). Confirm whether to state the precise span or keep it round.
- **Chip-conservation proof.** The title promises "without losing a chip." The docs prove the *schema* migrated cleanly (integrity ok, accepted deletes only), but I did not find an explicit post-cutover chip-conservation / ledger-drift check in these two docs. If one was run, citing it would land the title; if not, the title is rhetorical and should be softened or the claim grounded elsewhere. Founder to confirm.

## Cross-links

- **The migration-chain squash post** (sequel) — `b0deec9d` collapsing v1..v157 to a generated baseline; the natural "what we did after walking the chain once" follow-up.
- **A CSP/fonts post** if one exists in the series — wrong turn #2 connects to the self-hosting-fonts fix (`ba7390bd`, #206).
- **Any "living economy / cash mode" post** — this migration is what carried cash mode, the ledger, and the circuit to production; this is the deploy-side bookend to the feature-build story.
- **A broader "reproduce before you fix" / debugging-discipline post** — the four wrong turns are a self-contained case study that could anchor or feed such a piece.
