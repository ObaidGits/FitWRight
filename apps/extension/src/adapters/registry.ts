/**
 * Adapter registry.
 *
 * Resolution order is deliberate: site-specific adapters first (they extract
 * better data), generic JSON-LD last (it extracts *something* almost anywhere).
 * A site adapter that matches but fails to extract still falls through to
 * generic, so a redesign degrades quality rather than breaking capture outright.
 */
import { atsAdapters } from './ats';
import { boardAdapters } from './boards';
import { enterpriseAtsAdapters } from './enterprise-ats';
import { genericAdapter } from './generic';
import type { SiteAdapter } from './types';

/**
 * Every site-specific adapter, in match priority order.
 *
 * ATS platforms before job boards: a form is where an application is actually
 * submitted, and an ATS adapter scopes the fill correctly where a board adapter
 * would classify the page `unknown`. The enterprise platforms sit with the other
 * ATS entries for the same reason.
 */
export const adapters: SiteAdapter[] = [
  ...atsAdapters,
  ...enterpriseAtsAdapters,
  ...boardAdapters,
];

export { genericAdapter };

/** The adapter for a URL, or the generic fallback. */
export function resolveAdapter(url: URL): SiteAdapter {
  for (const adapter of adapters) {
    try {
      if (adapter.matches(url)) return adapter;
    } catch {
      /* a broken matcher must not block the rest of the registry */
    }
  }
  return genericAdapter;
}

/** Look up an adapter by its id (used by background scraping). */
export function adapterById(id: string): SiteAdapter | null {
  return adapters.find((a) => a.id === id) ?? null;
}

/**
 * Search URL for a board, used to drive background scraping.
 * Only boards with a stable, query-parameterized search URL are listed - the
 * ATS sites have no cross-company search to point at.
 */
export function searchUrlFor(id: string, query: string, location = ''): string | null {
  const q = encodeURIComponent(query);
  const l = encodeURIComponent(location);
  switch (id) {
    case 'indeed':
      return `https://www.indeed.com/jobs?q=${q}&l=${l}`;
    case 'linkedin':
      return `https://www.linkedin.com/jobs/search/?keywords=${q}&location=${l}`;
    case 'instahyre':
      return `https://www.instahyre.com/search-jobs/?search=${q}`;
    case 'hirist':
      // Verified live: /jobfeed is the logged-in feed and /j/<slug>-jobs 404s.
      // The public keyword listing is /k/<keyword>-jobs.
      return `https://www.hirist.tech/k/${hiristKeyword(query)}-jobs`;
    case 'foundit':
      return `https://www.foundit.in/srp/results?query=${q}&locations=${l || 'india'}`;
    case 'ycombinator':
      // Work at a Startup filters by role slug, not a free-text query, so map
      // the query onto the closest role lane and let the harvest cover the rest.
      return `https://www.workatastartup.com/jobs/l/${ycRoleSlug(query)}`;
    case 'naukri':
      // Naukri's public listings are slug paths: /python-developer-jobs, and
      // /python-developer-jobs-in-bangalore when a location is given.
      return (
        `https://www.naukri.com/${slugify(query)}-jobs` +
        (location ? `-in-${slugify(location)}` : '')
      );
    case 'zip_recruiter':
      return `https://www.ziprecruiter.com/jobs-search?search=${q}&location=${l}`;
    case 'glassdoor':
      // The keyword search entry point; Glassdoor redirects it to the canonical
      // SRCH_IL… results URL itself, which its own filters then own.
      return `https://www.glassdoor.co.in/Job/jobs.htm?sc.keyword=${q}&locT=&locId=`;
    case 'google':
      // udm=8 is the jobs surface; a plain query returns web results.
      return `https://www.google.com/search?q=${q}${l ? `+in+${l}` : ''}+jobs&udm=8`;
    default:
      return null;
  }
}

/** `Senior Python Developer` -> `senior-python-developer`, for slug-path boards. */
function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/**
 * Hirist's keyword listings are single-token skill slugs (`/k/python-jobs`,
 * `/k/django-jobs`), not phrases, so a multi-word query is reduced to its most
 * distinctive token - the first one that is not a generic job word.
 */
function hiristKeyword(query: string): string {
  const GENERIC = /^(developer|engineer|senior|junior|lead|manager|jobs?|remote|full|stack)$/i;
  const tokens = query
    .toLowerCase()
    .split(/[^a-z0-9+#.]+/)
    .filter(Boolean);
  return tokens.find((t) => !GENERIC.test(t)) ?? tokens[0] ?? 'software';
}

/**
 * Map a free-text query onto one of Work at a Startup's role lanes.
 *
 * YC exposes `/jobs/l/<role>` rather than a keyword search, so an unrecognised
 * query falls back to the engineering lane - by far the largest - instead of
 * returning nothing.
 */
function ycRoleSlug(query: string): string {
  const q = query.toLowerCase();
  if (/design|ux|ui\b/.test(q)) return 'designer';
  if (/product manager|\bpm\b|product/.test(q)) return 'product-manager';
  if (/recruit|talent|hr\b/.test(q)) return 'recruiting';
  if (/sales|account executive|business development/.test(q)) return 'sales';
  if (/market/.test(q)) return 'marketing';
  if (/operation|ops\b/.test(q)) return 'operations';
  if (/scien|research|\bml\b|machine learning/.test(q)) return 'science';
  return 'software-engineer';
}

/** Boards that background scraping can drive. */
export const SCRAPEABLE_BOARDS = [
  'indeed',
  'linkedin',
  'instahyre',
  'hirist',
  'foundit',
  'ycombinator',
  'naukri',
  'zip_recruiter',
  'glassdoor',
  'google',
] as const;

/**
 * Boards the FitWright server cannot reach on its own - Cloudflare, an Akamai
 * WAF, a recaptcha, or a login wall - and which therefore depend on this
 * extension. The web app reads this set through the bridge so its Discovery page
 * can route these sites here instead of asking the backend to try and fail.
 *
 * Each entry was confirmed to fail server-side and succeed in a real browser:
 * ZipRecruiter answers 403, Naukri 406 "recaptcha required", Glassdoor 400 on
 * every location shape, and Google returns nothing at all.
 */
export const EXTENSION_ONLY_BOARDS = [
  'instahyre',
  'hirist',
  'foundit',
  'ycombinator',
  'naukri',
  'zip_recruiter',
  'glassdoor',
  'google',
] as const;
