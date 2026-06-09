---
purpose: Building a standalone Astro SEO/marketing site (landing + opponent pages) and wiring it into the Caddy/GHCR prod deploy
type: guide
created: 2026-06-09
last_updated: 2026-06-09
---

# A marketing site that isn't the game (2026-06-09)

Started as a question — "find some SEO opportunities" — and ended as a separate
static site, a new prod container, and a PR (#257). The interesting part is the
two wrong turns in the middle, both caught by the user, both about the same
mistake: shipping something half-real and calling it done.

## The strategy call: don't chase the head terms

The obvious keywords ("free texas holdem", "play poker against computer") are
owned by Arkadium / 247freepoker / AARP / a wall of app-store clones. Unwinnable,
and the intent is generic anyway. A few web searches turned up the actual opening:

- **Character-exact search volume is ~0.** "Play poker against Sherlock Holmes"
  returns *cooking games*. Nobody searches it. So character pages are **not** a
  capture-existing-demand play.
- **But the category is validated — by chess.** Chess.com / Chessiverse / SparkChess
  all ship "play against named bots with personalities," and rank for *"human-like
  bots" / "chess personality."* Poker has no equivalent. That's the niche.

So the plan became **hub-and-spoke demand-creation**: a hub targeting "play poker
against AI personalities," and 76 character spokes that own a tiny long-tail each
with zero competition, build topical authority, and convert paid/social traffic.
Honest framing to the user up front: this *creates* demand, it doesn't capture it.

The roster turned out to be a gift — it's mostly **public-domain figures** (Lincoln,
Napoleon, Cleopatra, Sherlock), which carry real name search volume and zero
trademark risk, unlike the Batman/Ramsay examples in the README.

## Rendering: a separate site, not a retrofit

The game is a client-rendered SPA — fine for a game, bad for pages you want found.
Walked the user (non-frontend) through three options in plain language; they picked
**Astro** because the blog is already Markdown and Astro can't break the game (it's
a separate build). The game stays a SPA; marketing is its own thing. This decision
held up — everything after was cleaner for it.

## Wrong turn #1: shipping a stub and a data-dump

First pass, I built the opponent pages by **dumping `personalities.json` fields
verbatim** and left the homepage as a **placeholder hero**. The data is *game-tuning
and image-generation input*, not page copy — so the pages read like a spec sheet:
"calculating and selectively aggressive, exploiting reads with precision," and an
`appearance`/`apparel` blob that is literally an avatar-generator prompt ("lean
build, dark curly hair... deerstalker hat, smoking jacket").

User: *"not feeling this at all... the verbatim copy is not working either."* And,
revealingly: *"the whole landing site is ported to astro? because it's not at all.
i don't understand what you think you were supposed to do."*

Two lessons, one root cause:
1. **Never publish internal data fields as copy.** They were written for a different
   consumer.
2. **Don't show stubs.** The placeholder homepage made the whole thing read as broken
   and muddied what "done" meant — the user couldn't tell the placeholder from the
   real deliverable, which read as "did you misunderstand the task?"

Fix: stop showing stubs. Port the **real** "After Hours" landing and fix the copy,
then ask for feedback on something finished.

## The landing port (the satisfying part)

The real landing is a 675-line React component (scroll-reveal, slot-machine reel,
keyboard lightbox) + 1400 lines of CSS. Rather than rewrite it in `.astro`, reused
it **as a React island**: Astro server-renders it to static HTML at build (crawlable)
and hydrates it for the interactions. Changes were mechanical: strip `react-router`
(`navigate()` → links to the live app), swap bundled asset imports for `public/`
paths, and inline the design tokens it pulled from the app's global stylesheet
(scoped to `.lp`). No `framer-motion` dependency — the animations were hand-rolled.

Copy fix: dropped the image-prompt fields entirely, led with the genuinely-written
`circuit_hook` flavor, framed `play_style` as a real sentence, kept the `verbal_tics`
as in-character pull-quotes (those always read well). Also caught an "a/an" grammar
bug ("a analytical streak") and just removed the fragile clause.

## Avatars: the prod endpoint was right there

Only 11 of 76 personas had bundled avatars; the rest fell back to monograms (which
read as cheap). The user's nudge — *"you can find some avatars on prod"* — was the
unlock. Avatars are served from a **public** endpoint: `GET /api/avatar/<name>/<emotion>`
(DB-backed, 256×256, emotion falls back). A small fetch script (`npm run avatars`)
pulled all **76/76** over HTTP — no SSH, no DB dump. They're committed as the build
input (see deploy note). One small leak this surfaced: a raw skill tier `weak_reg`
showing on a card → mapped to "Improving."

## Wrong turn #2 (smaller): the play buttons would have looped

When wiring the deploy, the marketing "Play" CTAs still pointed at
`https://mypokerfacegame.com/` — which the marketing site now *owns*. Clicking
"Play" would have looped back to the marketing landing instead of the game. Caught
it before the PR; repointed every CTA to `/login` (the app's real entry).

## Deploy: it's Caddy, not nginx

The README/CLAUDE docs say "nginx"; the actual reverse proxy is **Caddy**
(`hetzner-infra/Caddyfile`). Verify the real config, don't trust the doc.

Wired a new `poker-marketing` nginx container (mirrors the frontend: GHCR image,
`web` + `poker-network`, healthcheck), a CI build-push step, and Caddy routes for
the paths marketing owns (`/`, `/opponents/*`, `/_astro/*`, `/avatars/*`,
`/screenshots/*`, `/menu-banner.webp`, `/sitemap-*`, `/robots.txt`) — everything
else falls through to the game. `handle /` matches the homepage *exactly*, so the
SPA keeps `/menu`, `/login`, etc.

Two build decisions worth recording:
- **The Docker build runs `npx astro build`, not `npm run build`.** The latter first
  runs the sync script, which reads `poker/personalities.json` — not in the scoped
  `./marketing` build context. So the build consumes the **committed**
  `opponents.json` + `public/avatars/` instead. That's why avatars are committed
  (un-ignored): the build must be hermetic and can't phone prod at build time.
- **Deploy order is load-bearing:** the marketing container must be up *before*
  Caddy reloads the new config, or those routes 502 in the gap.

Validated the whole thing with a real `docker build` + `docker run`: every route
200/404 as expected, CSP header present, clean URLs working.

## Friction

- This worktree's `.ruff_cache` is **root-owned** (from Docker runs), so pre-commit
  and pre-push ruff hooks failed on "permission denied" until run with
  `RUFF_CACHE_DIR=/tmp/...`. Worth fixing the perms.
- The Playwright MCP browser kept wedging ("browser already in use"); clearing the
  `SingletonLock` between runs was the workaround.

## State

PR **#257** → `main` (the Astro site + Flask `/api/character-requests` + compose +
CI). The **Caddyfile lives in a different repo** and is *not* in that PR. Open:
deploy + Caddy reload (in order), refresh avatars when the roster changes, the
optional offline-LLM pass for bespoke per-character copy, and the Devlog blog
(in progress).
