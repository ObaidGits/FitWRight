/**
 * Reusable metadata builder.
 *
 * `buildMetadata` centralizes the correct-by-construction pattern for every
 * public page: a relative canonical (resolved against `metadataBase` in the
 * root layout), matching OpenGraph + Twitter cards, and per-page keywords.
 * Pages pass only what differs; sensible product defaults fill the rest.
 *
 * `NOINDEX` is the shared robots directive for private/authenticated surfaces
 * (app shell, admin, auth flows, print views) so they never enter the index.
 */
import type { Metadata } from 'next';
import { SITE_NAME, SITE_DESCRIPTION, OG_IMAGE, TWITTER_IMAGE } from './config';

type BuildMetadataInput = {
  /** Page title (without the site-name suffix - the template appends it). */
  title: string;
  description?: string;
  /** Site-relative path, e.g. `/contact`. Used for canonical + OG url. */
  path: string;
  keywords?: readonly string[];
  /** OpenGraph type. Defaults to `website`. */
  ogType?: 'website' | 'article' | 'profile';
  /** Override the OG/Twitter title when it should differ from `<title>`. */
  socialTitle?: string;
  /** Set true to keep the page out of the index (private surfaces). */
  noindex?: boolean;
};

/** Shared robots directive for non-indexable, private surfaces. */
export const NOINDEX: Metadata['robots'] = {
  index: false,
  follow: false,
  googleBot: { index: false, follow: false },
};

export function buildMetadata({
  title,
  description = SITE_DESCRIPTION,
  path,
  keywords,
  ogType = 'website',
  socialTitle,
  noindex,
}: BuildMetadataInput): Metadata {
  const canonicalPath = path === '/' ? '/' : path.replace(/\/+$/, '');
  const ogTitle = socialTitle ?? `${title} - ${SITE_NAME}`;

  return {
    title,
    description,
    keywords: keywords ? [...keywords] : undefined,
    alternates: { canonical: canonicalPath },
    robots: noindex ? NOINDEX : undefined,
    openGraph: {
      title: ogTitle,
      description,
      url: canonicalPath,
      siteName: SITE_NAME,
      type: ogType,
      images: [OG_IMAGE],
    },
    twitter: {
      card: 'summary_large_image',
      title: ogTitle,
      description,
      images: [TWITTER_IMAGE],
    },
  };
}
