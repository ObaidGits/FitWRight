/**
 * The site adapter contract.
 *
 * One adapter per job platform. Adapters are pure DOM readers: they extract, and
 * never network or store. That keeps them cheap to fix when a site redesigns -
 * which they all do - because a broken selector is contained to one file and
 * cannot take the rest of the extension down with it.
 */
import type { CapturedJob, PageKind } from '@/lib/types';

export interface SiteAdapter {
  /** Stable id used as the `source` on captured jobs. Matches the backend's. */
  readonly id: string;
  /** Human label for the popup. */
  readonly label: string;

  /** Does this adapter handle the current location? */
  matches(url: URL): boolean;

  /** What kind of page is this? Decides which features light up. */
  classify(url: URL): PageKind;

  /**
   * Extract the single job this page is about.
   * Returns null when the page is not a job posting or the DOM has moved on -
   * callers fall back to the generic adapter rather than showing wrong data.
   */
  extractJob(url: URL): CapturedJob | null;

  /** Extract every job on a search/list page, for bulk scraping. */
  extractList?(url: URL): CapturedJob[];

  /**
   * Scope autofill to the application form when the page has other forms
   * (search bars, newsletter signups) that must not be touched.
   */
  formRoot?(): ParentNode | null;

  /** Selector to await before extracting, for SPA pages. */
  readonly readySelector?: string;
}

/** Shared helper: coerce a partial extraction into a valid job or null. */
export function toJob(
  source: string,
  url: URL,
  parts: Partial<CapturedJob> & { title?: string },
): CapturedJob | null {
  const title = (parts.title ?? '').trim();
  // A job without a title is a failed extraction, not an empty job.
  if (!title) return null;
  return {
    title,
    company: (parts.company ?? '').trim(),
    location: (parts.location ?? '').trim(),
    url: parts.url ?? url.href,
    source,
    description: parts.description ?? null,
    salary: parts.salary ?? null,
    posted_at: parts.posted_at ?? null,
    is_remote:
      parts.is_remote ??
      (/\bremote\b/i.test(`${parts.location ?? ''} ${title}`) ? true : null),
  };
}
