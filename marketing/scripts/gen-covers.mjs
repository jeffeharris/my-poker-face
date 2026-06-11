// Blog cover generator.
//
// Builds a self-contained HTML page (public/__covergen.html) with one 1200x630
// "After Hours" branded cover per blog post. A separate step (Playwright)
// rasterizes each #cover-<slug> element to public/blog/covers/<slug>.jpg, then
// this file is removed. Run: node scripts/gen-covers.mjs
//
// Character-led covers (archetype posts) embed the matching opponent avatar;
// every other post gets a typographic cover with a rotating suit motif. Either
// way: no recycled screenshots, one unique cover per post.

import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');
const blogDir = join(root, 'src/content/blog');

// Archetype/topic post -> opponent slug (avatar lives at /avatars/<slug>.png).
const CHARACTER = {
  'how-to-beat-a-calling-station': 'a-baby',
  'how-to-beat-a-maniac': 'zeus',
  'how-to-play-against-a-nit': 'winston-churchill',
  'how-to-beat-a-tight-aggressive-player': 'sun-tzu',
  'how-to-beat-a-lag': 'blackbeard',
  'how-to-play-against-a-gto-player': 'sherlock-holmes',
  'how-not-to-be-the-fish': 'alice',
  'how-to-read-your-opponents': 'sigmund-freud',
};

const SUITS = ['♠', '♥', '♦', '♣'];

function parseFrontmatter(raw) {
  const m = raw.match(/^---\n([\s\S]*?)\n---/);
  if (!m) return {};
  const out = {};
  for (const line of m[1].split('\n')) {
    const mm = line.match(/^([A-Za-z_]+):\s*(.*)$/);
    if (!mm) continue;
    let v = mm[2].trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    out[mm[1]] = v;
  }
  return out;
}

const posts = readdirSync(blogDir)
  .filter((f) => f.endsWith('.md'))
  .map((f) => {
    const slug = f.replace(/\.md$/, '');
    const fm = parseFrontmatter(readFileSync(join(blogDir, f), 'utf8'));
    // Flagship posts get bespoke Runware art (see gen-art.mjs) used as a
    // full-bleed background; it takes precedence over a character portrait.
    const art = existsSync(join(root, 'public/blog/art', `${slug}.png`))
      ? `/blog/art/${slug}.png`
      : null;
    return {
      slug,
      title: fm.title || slug,
      track: fm.track || 'Devlog',
      series: fm.series || null,
      character: art ? null : CHARACTER[slug] || null,
      art,
    };
  })
  .map((p, i) => ({ ...p, suit: SUITS[i % SUITS.length] }));

const manifest = JSON.stringify(posts);

const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,opsz,wght@0,6..96,400;0,6..96,600;1,6..96,500&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet" />
<style>
  * { box-sizing: border-box; margin: 0; }
  body { background: #1a1a1a; padding: 24px; }
  .cover {
    position: relative;
    width: 1200px;
    height: 630px;
    margin-bottom: 24px;
    overflow: hidden;
    background: radial-gradient(120% 90% at 78% -10%, rgba(212,165,116,0.20), transparent 55%),
                radial-gradient(120% 90% at -10% 120%, rgba(13,59,46,0.55), transparent 60%),
                linear-gradient(160deg, #0b0c11, #060608);
    color: #f8fafc;
    font-family: 'Bodoni Moda', Georgia, serif;
    isolation: isolate;
  }
  .cover__suit {
    position: absolute; right: -40px; top: -120px;
    font-size: 620px; line-height: 1;
    font-family: 'JetBrains Mono', monospace;
    color: rgba(248,250,252,0.035); user-select: none;
  }
  .cover__inner {
    position: absolute; inset: 0;
    padding: 78px 84px;
    display: flex; flex-direction: column; justify-content: space-between;
  }
  .cover__eyebrow {
    font-family: 'JetBrains Mono', monospace;
    font-size: 19px; font-weight: 500; letter-spacing: 0.26em; text-transform: uppercase;
    color: #d4a574; display: flex; align-items: center; gap: 14px;
  }
  .cover__eyebrow b { color: rgba(248,250,252,0.5); font-weight: 500; }
  .cover__title {
    font-weight: 600; line-height: 1.04; letter-spacing: -0.015em;
    max-width: var(--tw, 900px);
  }
  .cover__brand {
    font-family: 'JetBrains Mono', monospace;
    font-size: 18px; letter-spacing: 0.2em; text-transform: uppercase;
    color: rgba(248,250,252,0.55); display: flex; align-items: center; gap: 12px;
  }
  .cover__brand span { color: #d4a574; }
  .cover__portrait {
    position: absolute; right: 84px; top: 50%; transform: translateY(-50%);
    width: 340px; height: 340px; border-radius: 50%;
    object-fit: cover;
    border: 2px solid rgba(212,165,116,0.55);
    box-shadow: 0 0 0 10px rgba(212,165,116,0.06), 0 30px 80px -20px rgba(0,0,0,0.9);
  }
  .cover--portrait .cover__title { --tw: 580px; }

  /* Flagship art covers: full-bleed Runware image + bottom scrim + bottom copy */
  .cover--art { background-size: cover; background-position: center; }
  .cover__scrim {
    position: absolute; inset: 0;
    background: linear-gradient(0deg, rgba(6,6,8,0.97) 4%, rgba(6,6,8,0.58) 30%, rgba(6,6,8,0) 62%);
  }
  .cover__brand--top { position: absolute; top: 60px; left: 84px; z-index: 2; }
  .cover__artcopy { position: absolute; left: 84px; right: 84px; bottom: 64px; z-index: 2; }
  .cover__artcopy .cover__eyebrow { margin-bottom: 18px; }
</style>
</head>
<body>
<div id="covers"></div>
<script>
  const posts = ${manifest};
  const root = document.getElementById('covers');
  for (const p of posts) {
    const portrait = p.character
      ? '<img class="cover__portrait" src="/avatars/' + p.character + '.png" alt="" />'
      : '';
    // Long titles shrink so they always fit the 630px canvas.
    const len = p.title.length;
    const size = len > 64 ? 46 : len > 46 ? 56 : 68;
    const eyebrow = p.series
      ? p.track + ' &nbsp;<b>&middot; ' + p.series + '</b>'
      : p.track;
    if (p.art) {
      root.insertAdjacentHTML('beforeend',
        '<div class="cover cover--art" id="cover-' + p.slug + '" style="background-image:url(' + p.art + ')">' +
          '<div class="cover__scrim"></div>' +
          '<div class="cover__brand cover__brand--top"><span>' + p.suit + '</span> My Poker Face</div>' +
          '<div class="cover__artcopy">' +
            '<div class="cover__eyebrow">' + eyebrow + '</div>' +
            '<h1 class="cover__title" style="font-size:' + size + 'px">' + p.title + '</h1>' +
          '</div>' +
        '</div>'
      );
    } else {
      root.insertAdjacentHTML('beforeend',
        '<div class="cover ' + (p.character ? 'cover--portrait' : '') + '" id="cover-' + p.slug + '">' +
          '<div class="cover__suit">' + p.suit + '</div>' +
          portrait +
          '<div class="cover__inner">' +
            '<div class="cover__eyebrow">' + eyebrow + '</div>' +
            '<h1 class="cover__title" style="font-size:' + size + 'px">' + p.title + '</h1>' +
            '<div class="cover__brand"><span>' + p.suit + '</span> My Poker Face</div>' +
          '</div>' +
        '</div>'
      );
    }
  }
  document.fonts.ready.then(() => { document.body.dataset.ready = '1'; });
</script>
</body>
</html>`;

writeFileSync(join(root, 'public/__covergen.html'), html);
// Emit the slug list so the rasterizer knows what to capture.
writeFileSync(join(root, 'public/__covergen.json'), JSON.stringify(posts.map((p) => p.slug)));
console.log('Wrote public/__covergen.html with', posts.length, 'covers');
console.log('character covers:', posts.filter((p) => p.character).length);
