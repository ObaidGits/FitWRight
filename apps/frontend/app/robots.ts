/**
 * robots.txt (App Router metadata route).
 *
 * Public marketing + public-profile surfaces are crawlable. Authenticated app
 * surfaces, admin, auth flows, print views, and the API proxy are disallowed -
 * they contain private/per-user content that must never enter a search index.
 * The sitemap is advertised so crawlers discover canonical public URLs.
 */
import type { MetadataRoute } from 'next';
import { SITE_URL } from '@/lib/seo/config';

const DISALLOW = [
  '/home',
  '/resumes',
  '/import',
  '/tailor',
  '/applications',
  '/wizard',
  '/settings',
  '/profile',
  '/agenda',
  '/admin',
  '/builder',
  '/print',
  '/api',
  '/login',
  '/signup',
  '/forgot',
  '/reset',
  '/verify',
  '/verify-email',
];

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        disallow: DISALLOW,
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
