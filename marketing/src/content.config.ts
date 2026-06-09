import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

// Blog posts live as plain markdown under src/content/blog/. Images are served
// from /public/blog/ (referenced by absolute path), so `hero` is a string path,
// not an Astro image asset.
const blog = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/blog' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    track: z.enum(['Devlog', 'Inside the Table']),
    date: z.coerce.date(),
    /** Lower sorts first within the series. */
    order: z.number(),
    /** Public path, e.g. /blog/hero.png */
    hero: z.string().optional(),
    heroAlt: z.string().optional(),
    excerpt: z.string(),
    draft: z.boolean().default(false),
  }),
});

export const collections = { blog };
