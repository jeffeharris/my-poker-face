# CLAUDE.md — blog drafting conventions

Guidance for writing blog content in this directory. These rules exist because
surface tells get otherwise-good content dismissed as AI-written before anyone reads
it. The voice should read as the founder's: grounded, honest, unembellished.

## Voice & tone

- **Grounded, not dramatic.** No hero's-journey framing, no inflated stakes, no
  "passion project that became...". Just what happened, when, and why.
- **Honesty is the brand.** Keep the wrong turns in. Real quotes, real numbers, real
  commit subjects. Build-in-public credibility comes from candor, not polish.
- **Ground every claim** in a real artifact (commit, doc, transcript, analysis number).
  When inferring or unsure, flag it inline with `[VERIFY: …]` rather than asserting.
  Use `[ASSET: …]` for screenshots/figures that need capturing.

## Style rules (anti-AI-tells)

- **No em-dashes (`—`).** Reword, or use commas / periods / colons / parentheses.
  This applies to the body, headings, frontmatter, and comments.
- **No en-dash numeric ranges (`–`).** Write "85 to 100%", not "85–100%".
- **Go easy on "it's not X, it's Y" antithesis.** A couple of earned ones per post is
  fine; frequent or blatant ones read as machine-written. Reduce and reword.
- **Don't over-explain low-value mechanics.** Keep player-facing posts at the level
  that serves the reader. Push deep engineering detail (transaction ordering, schema
  versions, exact formulas) to a Devlog post or an optional "how it works" sidebar.
- **On review, grep before publishing:** `grep -rnE '—|–' .` should return nothing,
  and scan for repetitive "not … , …" cadence.

## Two tracks (audiences)

- **Devlog → career + creator-community.** A credibility play. Show judgment and
  range. Honest, measured, evidence-backed. Cite real bb/100 figures from `analysis/`.
- **Inside the Table → players.** Gameplay and features. Translate engineering into
  "look how alive these opponents are." Keep formulas out of the player voice.

## Persona names (legal)

- **Only name personas that exist in `poker/personalities.json` (game repo) AND are
  unambiguously public domain.** Never use licensed characters (e.g. Batman, Triumph
  the Insult Dog, Jon Stewart) even as throwaway examples.
- **Prefer `circulating: true` personas** and clearly public-domain figures. Avoid
  estate-controlled names that happen to be in the file (e.g. Salvador Dali, Dr.
  Seuss are present but set `circulating: false` and are not safe to feature).
- **Recurring blog cast** (coherent across posts): Sherlock Holmes (calculating),
  Blackbeard (aggressive, the recurring rival), Cleopatra (bold), The Mad Hatter
  (surreal, bizarre bet sizes). Pull more from the roster as needed: Dracula,
  Napoleon, Mark Twain, Robin Hood, Don Quixote, Edgar Allan Poe, etc.
- **Historical caveat:** the 2023 prototype really did use licensed characters. An
  origin-story post must describe that era generically ("celebrity and literary
  characters") rather than naming the licensed ones.

## Framing notes

- Employment context: **freelance consulting with early-stage founders on LLM and B2B
  products, after the Yotascale acquisition (Aug 2025)** — not "no job / unemployed".
- Keep private unless the founder opts in: the ~3-month B2B-SaaS / movie-producing
  detour before the Dec 2025 restart.

## Where the source material lives

- `CONTENT_PLAN.md` — the 12-post, two-track plan and publish order.
- `ORIGIN_ARC.md` — the 2023→2026 project arc (reconstructed from git + transcripts).
- `FOUNDER_INTERVIEW.md` — the founder's voice and the honest story spine.
- `outlines/` — per-post outlines (hook, beats, real numbers, pull-quotes, gaps).
- `drafts/` — full drafts.
- **Image assets live in `marketing/public/blog/`** (the Astro site), served at `/blog/<file>`. Reference them in drafts as `/blog/<file>`, not a repo-relative path. Captured so far: `dossier-edgar-allan-poe.png` (public-domain dossier hero), `cash-table-the-garage.jpeg` + `cash-table-flop.jpeg` (table shots), `tell-your-story.png` (session recap).
- Upstream: `../captains-log/**` (narratives), `../analysis/**` (numbers),
  `../technical/**` (mechanics), `../vision/**` (positioning).
