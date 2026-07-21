/**
 * Centralized SEO configuration for FitWright.
 *
 * Single source of truth for site identity, canonical origin, author/developer
 * (EEAT) signals, social links, and keyword groupings. Everything else in the
 * SEO layer (metadata builder, structured-data generators, sitemap, robots)
 * derives from here so there is no duplication and no drift.
 *
 * The canonical origin is read from `NEXT_PUBLIC_SITE_URL` at build/runtime so
 * previews, staging, and production each advertise the correct absolute URLs.
 * It falls back to the production domain for local/zero-config builds.
 */

/** Absolute origin (no trailing slash). Configurable per environment. */
export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL || 'https://fitwright.app').replace(
  /\/+$/,
  ''
);

export const SITE_NAME = 'FitWright';
export const SITE_TAGLINE = 'Built to fit';

/** Default, product-focused site description (≤ ~160 chars for SERP display). */
export const SITE_DESCRIPTION =
  'FitWright is an open-source, AI resume builder and tailor. Optimize your resume for any job with honest ATS scoring, cover letters, and interview prep. Bring your own API key.';

/** Developer / founder - EEAT author identity, reused across Person schema. */
export const AUTHOR = {
  name: 'Obaidullah Zeeshan',
  jobTitle: 'AI & Full-Stack Software Engineer',
  url: 'https://obaidullah-zeeshan.dev',
  linkedin: 'https://www.linkedin.com/in/obaidullah-zeeshan/',
  github: 'https://github.com/ObaidGits',
} as const;

/** Canonical project repository. */
export const GITHUB_REPO = 'https://github.com/ObaidGits/FitWRight';

/** Public social / entity links used for `sameAs` in Organization schema. */
export const SOCIAL_LINKS = [AUTHOR.github, AUTHOR.linkedin, AUTHOR.url] as const;

/**
 * Global brand keywords. Page-level keywords live in `page-keywords.ts` and are
 * merged per route to target intent without cannibalization.
 */
export const BRAND_KEYWORDS = [
  'AI resume builder',
  'ATS resume builder',
  'AI resume tailoring',
  'resume optimizer',
  'resume checker',
  'open source resume builder',
  'privacy-first resume builder',
  'cover letter generator',
  'interview preparation',
  'job description analyzer',
] as const;

/**
 * Canonical social-share image descriptors.
 *
 * These point at the dynamic `next/og` metadata routes (`/opengraph-image`,
 * `/twitter-image`). We declare them EXPLICITLY in metadata because Next.js
 * drops the auto-attached file-based image whenever a page overrides its
 * `openGraph`/`twitter` object - which every SEO page here does. Declaring them
 * centrally guarantees `og:image`/`twitter:image` on every page.
 */
export const OG_IMAGE = {
  url: '/opengraph-image',
  width: 1200,
  height: 630,
  alt: `${SITE_NAME} - ${SITE_TAGLINE}`,
} as const;

export const TWITTER_IMAGE = '/twitter-image';

/** Absolute URL helper - joins a path onto the canonical origin. */
export function absoluteUrl(path = '/'): string {
  if (!path || path === '/') return `${SITE_URL}/`;
  return `${SITE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}

/**
 * Search-engine site-verification tokens, sourced from env so they can be set
 * Google defaults to the production Search Console token and can be overridden
 * per deployment. Bing and Yandex remain unset unless configured.
 * Consumed by the root layout's `metadata.verification`.
 *
 *  - NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION  -> Google Search Console override
 *  - NEXT_PUBLIC_BING_SITE_VERIFICATION    -> Bing Webmaster Tools (msvalidate.01)
 *  - NEXT_PUBLIC_YANDEX_SITE_VERIFICATION  -> Yandex Webmaster
 */
export const VERIFICATION = {
  google:
    process.env.NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION ||
    'lV_LMnwVarz4ws2OxJ3XcNj9dqHPlNS7SXBB1M96meI',
  bing: process.env.NEXT_PUBLIC_BING_SITE_VERIFICATION || undefined,
  yandex: process.env.NEXT_PUBLIC_YANDEX_SITE_VERIFICATION || undefined,
} as const;
