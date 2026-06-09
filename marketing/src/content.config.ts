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
    /** Lower sorts first within the blog (and within a series). */
    order: z.number(),
    /** Optional multi-part series; posts sharing the name cross-link by `order`. */
    series: z.string().optional(),
    /** Public path, e.g. /blog/hero.png */
    hero: z.string().optional(),
    heroAlt: z.string().optional(),
    excerpt: z.string(),
    draft: z.boolean().default(false),
  }),
});

export const collections = { blog };
