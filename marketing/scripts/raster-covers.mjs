// Rasterize the generated cover HTML to JPEGs.
//
// Serves public/ on an ephemeral port, opens public/__covergen.html in the
// system Chrome via puppeteer-core, waits for fonts + images, and screenshots
// each .cover element to public/blog/covers/<slug>.jpg, then removes the temp
// files. Part of `npm run covers` (after gen-art + gen-covers).
//
// Uses the system Chrome (CHROME_PATH env or /usr/bin/google-chrome) so there
// is no browser download.

import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { existsSync, rmSync } from 'node:fs';
import { dirname, extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const here = dirname(fileURLToPath(import.meta.url));
const marketing = join(here, '..');
const pub = join(marketing, 'public');
const coversDir = join(pub, 'blog/covers');
const CHROME = process.env.CHROME_PATH || '/usr/bin/google-chrome';

if (!existsSync(join(pub, '__covergen.html'))) {
  console.error('public/__covergen.html missing - run gen-covers.mjs first');
  process.exit(1);
}

const MIME = {
  '.html': 'text/html',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
  '.css': 'text/css',
  '.js': 'text/javascript',
};

const server = http.createServer(async (req, res) => {
  try {
    const rel = decodeURIComponent(req.url.split('?')[0]);
    const fp = normalize(join(pub, rel === '/' ? '/index.html' : rel));
    if (!fp.startsWith(pub) || !existsSync(fp)) {
      res.writeHead(404);
      return res.end();
    }
    res.writeHead(200, { 'Content-Type': MIME[extname(fp)] || 'application/octet-stream' });
    res.end(await readFile(fp));
  } catch {
    res.writeHead(500);
    res.end();
  }
});
await new Promise((r) => server.listen(0, r));
const port = server.address().port;

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--no-sandbox', '--hide-scrollbars'],
});
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 800, deviceScaleFactor: 1 });
  await page.goto(`http://localhost:${port}/__covergen.html`, { waitUntil: 'networkidle0' });
  await page.waitForFunction(() => document.body.dataset.ready === '1', { timeout: 30000 });
  await page.waitForFunction(
    () => [...document.images].every((i) => i.complete && i.naturalWidth > 0),
    { timeout: 30000 }
  );

  const ids = await page.$$eval('.cover', (els) => els.map((e) => e.id));
  let n = 0;
  for (const id of ids) {
    const slug = id.replace('cover-', '');
    if (slug === 'CLAUDE') continue; // CLAUDE.md is not a post
    const el = await page.$('#' + id);
    await el.screenshot({ path: join(coversDir, `${slug}.jpg`), type: 'jpeg', quality: 88 });
    n++;
  }
  console.log('rasterized', n, 'covers to public/blog/covers/*.jpg');
} finally {
  await browser.close();
  server.close();
  for (const f of ['__covergen.html', '__covergen.json']) {
    const p = join(pub, f);
    if (existsSync(p)) rmSync(p);
  }
}
