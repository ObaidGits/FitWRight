/**
 * sitemap.xml (App Router metadata route).
 *
 * Enumerates the canonical, indexable public routes with intent-appropriate
 * priorities and change frequencies. Per-user public profiles (`/p/[slug]`) are
 * intentionally excluded: they are visibility-gated, `force-dynamic`, and have
 * no public listing endpoint, so enumerating them would risk leaking unlisted
 * slugs. They remain individually crawlable via inbound links + their own
 * canonical/robots metadata.
 */
import type { MetadataRoute } from 'next';
import { absoluteUrl } from '@/lib/seo/config';
import { CAPABILITY_SLUGS } from '@/components/marketing/capabilities-data';
import { RESUME_TEMPLATES } from '@/lib/resume/template-catalog';
import { RESUME_SAMPLES } from '@/lib/resume/sample-catalog';

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();

  const routes: Array<{
    path: string;
    changeFrequency: MetadataRoute.Sitemap[number]['changeFrequency'];
    priority: number;
  }> = [
    { path: '/', changeFrequency: 'weekly', priority: 1.0 },
    // Feature landing pages (topic-cluster spokes).
    ...CAPABILITY_SLUGS.map((slug) => ({
      path: `/${slug}`,
      changeFrequency: 'monthly' as const,
      priority: 0.9,
    })),
    // Template & sample libraries (collection pages) + their detail pages.
    { path: '/templates', changeFrequency: 'weekly', priority: 0.8 },
    { path: '/samples', changeFrequency: 'weekly', priority: 0.8 },
    ...RESUME_TEMPLATES.map((t) => ({
      path: `/templates/${t.id}`,
      changeFrequency: 'monthly' as const,
      priority: 0.6,
    })),
    ...RESUME_SAMPLES.map((s) => ({
      path: `/samples/${s.id}`,
      changeFrequency: 'monthly' as const,
      priority: 0.6,
    })),
    { path: '/connect', changeFrequency: 'monthly', priority: 0.7 },
    { path: '/contact', changeFrequency: 'monthly', priority: 0.6 },
    { path: '/privacy', changeFrequency: 'yearly', priority: 0.3 },
    { path: '/terms', changeFrequency: 'yearly', priority: 0.3 },
  ];

  return routes.map((r) => ({
    url: absoluteUrl(r.path),
    lastModified: now,
    changeFrequency: r.changeFrequency,
    priority: r.priority,
  }));
}
