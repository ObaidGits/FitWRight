/**
 * Public portfolio page (P8): /p/[slug]/portfolio
 *
 * A projects-first public view derived from the same profile projection as the
 * public profile (no duplicated data or rendering logic). Server-rendered with
 * SEO metadata; visibility-gated by the backend (private → notFound). Reuses the
 * public profile view's presentational sections for consistency.
 */
import { cache } from 'react';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { PublicProfileView } from '@/components/public/public-profile-view';
import { getPublicProfilePage, publicVcardUrl } from '@/lib/api/professional-profile';
import { JsonLd } from '@/lib/seo/json-ld';
import { breadcrumbSchema, collectionPageSchema } from '@/lib/seo/structured-data';

export const dynamic = 'force-dynamic';

type Params = { params: Promise<{ slug: string }> };

const loadPage = cache((slug: string) => getPublicProfilePage(slug).catch(() => null));

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { slug } = await params;
  const page = await loadPage(slug);
  if (!page) return { title: 'Portfolio not found', robots: { index: false, follow: false } };
  const name = page.profile.identity.name ?? 'Portfolio';
  const title = `${name} — Portfolio`;
  const description = page.profile.summary || `${name}'s portfolio and selected work.`;
  const images = page.profile.identity.avatarUrl
    ? [{ url: page.profile.identity.avatarUrl }]
    : undefined;
  return {
    title,
    description,
    robots: page.indexable ? { index: true, follow: true } : { index: false, follow: false },
    alternates: { canonical: `/p/${slug}/portfolio` },
    openGraph: {
      title,
      description,
      type: 'profile',
      url: `/p/${slug}/portfolio`,
      images,
    },
    twitter: {
      card: 'summary',
      title,
      description,
      images: page.profile.identity.avatarUrl ? [page.profile.identity.avatarUrl] : undefined,
    },
  };
}

export default async function PublicPortfolioPage({ params }: Params) {
  const { slug } = await params;
  const page = await loadPage(slug);
  if (!page) notFound();

  const name = page.profile.identity.name || 'Portfolio';

  return (
    <div className="atelier">
      {page.indexable && (
        <JsonLd
          data={[
            collectionPageSchema({
              name: `${name} — Portfolio`,
              path: `/p/${slug}/portfolio`,
              about: page.json_ld,
            }),
            breadcrumbSchema([
              { name: 'Home', path: '/' },
              { name, path: `/p/${slug}` },
              { name: 'Portfolio', path: `/p/${slug}/portfolio` },
            ]),
          ]}
        />
      )}
      {/* Single rendering path: the public view already gives projects a
          prominent card grid, so the portfolio reuses it (no duplicated layout). */}
      <PublicProfileView
        profile={page.profile}
        vcardUrl={publicVcardUrl(slug)}
        theme={page.theme}
      />
    </div>
  );
}
