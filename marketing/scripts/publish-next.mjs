// Publish the next queued blog post (for the scheduled trickle).
//
// Finds the draft:true post with the lowest `order`, flips it to draft:false,
// and stamps today's real date. Prints a marker line the caller can act on:
//   PUBLISHED <slug> (YYYY-MM-DD)   -> commit + push to main to deploy it
//   NOTHING_TO_PUBLISH              -> queue is empty, do nothing
//
// Covers already exist for every post (drafts included), so flipping the flag
// is all that's needed; no regeneration. Run: node scripts/publish-next.mjs

import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(dirname(fileURLToPath(import.meta.url)), '..', 'src/content/blog');
const today = new Date().toISOString().slice(0, 10);

const drafts = readdirSync(dir)
  .filter((f) => f.endsWith('.md') && f !== 'CLAUDE.md')
  .map((f) => {
    const raw = readFileSync(join(dir, f), 'utf8');
    return {
      file: f,
      raw,
      order: Number(raw.match(/^order:\s*(\d+)/m)?.[1] ?? Number.MAX_SAFE_INTEGER),
      isDraft: /^draft:\s*true\s*$/m.test(raw),
    };
  })
  .filter((p) => p.isDraft)
  .sort((a, b) => a.order - b.order);

if (drafts.length === 0) {
  console.log('NOTHING_TO_PUBLISH');
  process.exit(0);
}

const p = drafts[0];
const updated = p.raw
  .replace(/^draft:\s*true\s*$/m, 'draft: false')
  .replace(/^date:.*$/m, `date: ${today}`);
writeFileSync(join(dir, p.file), updated);
console.log(`PUBLISHED ${p.file.replace(/\.md$/, '')} (${today})`);
