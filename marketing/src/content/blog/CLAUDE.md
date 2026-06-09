# Blog content — conventions

Posts are Astro content-collection Markdown in this directory. The frontmatter
schema is enforced by `marketing/src/content.config.ts`. Series plans live in
`docs/blog/` (e.g. `SERIES_PLAYING_BETTER_POKER.md`).

## Frontmatter

Required: `title`, `description`, `track`, `date`, `order`, `excerpt`, `draft`.
Optional: `series`, `hero`, `heroAlt`.

- `title` — keyword-targeted; renders as `{title} | My Poker Face`.
- `description` — **this is the meta description.** ~150-160 chars, lead with the
  target keyword/benefit. Quote the value if it contains a colon.
- `track` — `Devlog` (credibility / creator audience) or `Inside the Table`
  (players, SEO, signups).
- `order` — sorts the blog index and the position within a `series`.
- `draft: true` — previewable in `npm run dev`, but NOT built or sitemapped in
  prod. Flip to `false` to publish.
- `series` — posts sharing the exact string auto-cross-link via the in-post
  series nav (wired in `src/pages/blog/[...slug].astro`), ordered by `order`.
- `hero` — public path under `/blog/...`; `heroAlt` is its alt text.

## Voice (non-negotiable — surface AI-tells get content dismissed as AI-written)

- **No em-dashes (—) or en-dashes (–). Ever.** Use periods, commas, colons,
  parentheses. Verify before committing: `grep -c $'—\|–' *.md` must be 0 on
  every file.
- Go easy on the "not X, it's Y" antithesis — at most one per post.
- Grounded, not dramatic. Plain, confident, specific. Match the existing posts.
- Don't over-explain low-value mechanics.

## SEO

- One clear target keyword per post; put it in the `title`, the `description`,
  and the first paragraph.
- Internal-link liberally and with trailing slashes: opponent pages
  `/opponents/<slug>/` (slug from `marketing/src/data/opponents.json` — verify it
  exists), sibling posts `/blog/<slug>/`, and the hub `/opponents/`.
- One CTA per post: Inside the Table → `/login` (play); Devlog → play or read-on.

## Grounding (this is what makes the posts credible instead of generic)

- Ground every game claim in real data/features: the opponent **playing-profile
  meters** (Looseness / Aggression / Bluffing / Adaptability), the skill tier,
  the coach, the Range Explorer, the dossier / cross-session memory, the
  sticky-tilt emotion system.
- Name real characters and link them. Pull their traits from `opponents.json` /
  `poker/personalities.json` — do not invent play styles or tiers.
- Verify external/historical facts against sources (e.g. `docs/vision/*`). Do not
  assert unsupported claims.

## Poker accuracy

- The advice has to be correct. Avoid misleading absolutes ("never", "always",
  "not capable"). State the real exception (e.g. against a maniac you mostly trap,
  but you still bet a true monster on a wet board to charge draws).
- Before publishing a multi-post series, get an independent review (`/codex-assist`
  ask + a subagent), fix the findings, then flip `draft: false`.

## Mechanics

- Verify a draft renders: `cd marketing && npm run dev` (includes drafts), or
  `npx astro build` (excludes drafts, mirrors prod).
- Publishing is a deploy: flip `draft: false`, commit, and ship `marketing` →
  `main` (auto-deploy rebuilds the marketing image). Caddy already routes
  `/blog/*` to the marketing container, so no infra change is needed.
