/**
 * Public profile page (P7): /p/[slug]
 *
 * Server-rendered so crawlers see real content + metadata. Fetches the
 * visibility-gated public projection from the backend; a private/unknown slug
 * 404s. SEO is first-class: title/description/OpenGraph/Twitter come from the
 * projection, `unlisted` profiles are marked `noindex` (link-only), and a
 * schema.org Person JSON-LD block is emitted for rich results. All rendering
 * flows through the Profile projection - no duplicated resume/profile logic.
 */
import { cache } from 'react';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { PublicProfileView } from '@/components/public/public-profile-view';
import { getPublicProfilePage, publicVcardUrl } from '@/lib/api/professional-profile';
import { JsonLd } from '@/lib/seo/json-ld';
import { breadcrumbSchema } from '@/lib/seo/structured-data';

export const dynamic = 'force-dynamic';

type Params = { params: Promise<{ slug: string }> };

// Dedupe the backend call across generateMetadata + the page render within a
// single request (React request-scoped memoization) - one fetch, not two.
const loadPage = cache((slug: string) => getPublicProfilePage(slug).catch(() => null));

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { slug } = await params;
  const page = await loadPage(slug);
  if (!page) {
    return { title: 'Profile not found', robots: { index: false, follow: false } };
  }
  const id = page.profile.identity;
  const title = id.name ? `${id.name}${id.headline ? ` - ${id.headline}` : ''}` : 'Profile';
  const description = page.profile.summary || id.headline || `${id.name}'s professional profile.`;
  const images = id.avatarUrl ? [{ url: id.avatarUrl }] : undefined;
  return {
    title,
    description,
    // Unlisted profiles are reachable by link but must not be indexed.
    robots: page.indexable ? { index: true, follow: true } : { index: false, follow: false },
    alternates: { canonical: `/p/${slug}` },
    openGraph: {
      title,
      description,
      type: 'profile',
      url: `/p/${slug}`,
      images,
    },
    twitter: {
      card: 'summary',
      title,
      description,
      images: id.avatarUrl ? [id.avatarUrl] : undefined,
    },
  };
}

export default async function PublicProfilePage({ params }: Params) {
  const { slug } = await params;
  const page = await loadPage(slug);
  if (!page) notFound();

  const name = page.profile.identity.name || 'Profile';

  return (
    <div className="atelier">
      <script
        type="application/ld+json"
        // JSON-LD is server-serialized structured data (schema.org Person),
        // authored by the backend projection.
        dangerouslySetInnerHTML={{ __html: JSON.stringify(page.json_ld) }}
      />
      {/* Breadcrumb trail (Home -> profile) for rich results + crawl context. */}
      <JsonLd
        data={breadcrumbSchema([
          { name: 'Home', path: '/' },
          { name, path: `/p/${slug}` },
        ])}
      />
      <PublicProfileView
        profile={page.profile}
        vcardUrl={publicVcardUrl(slug)}
        theme={page.theme}
      />
    </div>
  );
}
