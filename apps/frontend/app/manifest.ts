/**
 * Web app manifest (App Router metadata route).
 *
 * Improves mobile/PWA signals and installability, which feed into mobile
 * search UX. Values derive from the central SEO config so the brand identity
 * stays consistent with metadata and structured data.
 */
import type { MetadataRoute } from 'next';
import { SITE_NAME, SITE_DESCRIPTION } from '@/lib/seo/config';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: `${SITE_NAME} - AI Resume Builder & Tailor`,
    short_name: SITE_NAME,
    description: SITE_DESCRIPTION,
    start_url: '/',
    display: 'standalone',
    background_color: '#0b1120',
    theme_color: '#1d4ed8',
    categories: ['productivity', 'business', 'utilities'],
    icons: [
      { src: '/icon.svg', type: 'image/svg+xml', sizes: 'any', purpose: 'any' },
      { src: '/logo.svg', type: 'image/svg+xml', sizes: 'any', purpose: 'maskable' },
    ],
  };
}
