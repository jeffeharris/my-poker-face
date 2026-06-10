// Flagship hero art via Runware FLUX.2 [dev] (runware:400@1).
//
// Generates a bespoke "After Hours" noir image for each flagship post and saves
// it to public/blog/art/<slug>.png. gen-covers.mjs then uses these as the cover
// background (with a scrim + the brand text overlay) so flagships look richer
// while staying consistent with the rest of the blog. Run: node scripts/gen-art.mjs
//
// Reads RUNWARE_API_KEY from the repo-root .env. Cost: ~$0.003/image (FLUX.2 dev).

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const marketing = join(here, '..');
const repoRoot = join(marketing, '..');

const env = readFileSync(join(repoRoot, '.env'), 'utf8');
const KEY = (env.match(/^RUNWARE_API_KEY=(.*)$/m) || [])[1]?.trim().replace(/^["']|["']$/g, '');
if (!KEY) {
  console.error('RUNWARE_API_KEY not found in .env');
  process.exit(1);
}

const MODEL = 'runware:400@1'; // FLUX.2 [dev]
const W = 1216;
const H = 640; // ~1200x630 ratio, multiples of 64
const NEGATIVE =
  'text, words, letters, typography, watermark, logo, signage, ui, interface, cartoon, anime, deformed hands, extra fingers, low quality, blurry';

const STYLE =
  'cinematic editorial photograph, film noir casino, dark teal and warm gold palette, single dramatic overhead lamp, deep chiaroscuro shadows, atmospheric haze, shallow depth of field, fine film grain, ultra detailed, moody, no text';

const PROMPTS = {
  'llms-cant-play-poker':
    `a sleek humanoid robot with a faint glowing visor sitting at a green felt poker table, holding playing cards a little awkwardly, scattered poker chips, ${STYLE}`,
  'stacked-daniel-negreanu-poker-ai':
    `a nostalgic early-2000s poker video game mood, the soft glow of a vintage CRT monitor over a green felt table with cards and gold chips, retro simulation atmosphere, ${STYLE}`,
  'poker-where-the-opponents-are-alive':
    `a lively high-stakes poker table surrounded by a diverse cast of vivid expressive characters in dramatic rim lighting, green felt, gold chips, painterly, ${STYLE}`,
  'your-opponents-remember-you':
    `a sharp-eyed opponent studying the viewer intently across a green felt poker table, stacks of chips, smoke haze, intense psychological mood, ${STYLE}`,
};

mkdirSync(join(marketing, 'public/blog/art'), { recursive: true });

for (const [slug, prompt] of Object.entries(PROMPTS)) {
  const out = join(marketing, 'public/blog/art', `${slug}.png`);
  if (existsSync(out) && !process.env.FORCE) {
    console.log('skip (exists, set FORCE=1 to regenerate):', slug);
    continue;
  }
  const payload = [
    {
      taskType: 'imageInference',
      taskUUID: randomUUID(),
      positivePrompt: prompt,
      negativePrompt: NEGATIVE,
      width: W,
      height: H,
      model: MODEL,
      numberResults: 1,
    },
  ];
  const res = await fetch('https://api.runware.ai/v1', {
    method: 'POST',
    headers: { Authorization: `Bearer ${KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (data.errors) {
    console.error(slug, 'ERROR', JSON.stringify(data.errors));
    continue;
  }
  const url = data?.data?.[0]?.imageURL;
  if (!url) {
    console.error(slug, 'no imageURL:', JSON.stringify(data).slice(0, 200));
    continue;
  }
  const buf = Buffer.from(await (await fetch(url)).arrayBuffer());
  writeFileSync(join(marketing, 'public/blog/art', `${slug}.png`), buf);
  console.log('saved', slug, `${(buf.length / 1024).toFixed(0)}KB`);
}
console.log('done');
