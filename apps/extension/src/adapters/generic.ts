/**
 * Generic adapter - the fallback that covers far more sites than any
 * hand-written one.
 *
 * Most job pages publish schema.org `JobPosting` JSON-LD because Google for Jobs
 * requires it for indexing. That gives us title, company, location, salary and
 * posting date as structured data, with no per-site selectors to maintain. When
 * the JSON-LD is absent we degrade to OpenGraph/meta tags and finally to the
 * page's own headings.
 *
 * This adapter is tried LAST for matching but is the reason an unknown company
 * career page still works.
 */
import { blockText, clean, pick, pickText, readJsonLd } from '@/lib/dom';
import type { CapturedJob, PageKind } from '@/lib/types';
import { toJob } from './types';
import type { SiteAdapter } from './types';

/** Pull a plain string out of a JSON-LD value that may be nested or an array. */
function ldString(value: unknown): string {
  if (typeof value === 'string') return clean(value);
  if (typeof value === 'number') return String(value);
  if (Array.isArray(value)) return ldString(value[0]);
  if (value && typeof value === 'object') {
    const node = value as Record<string, unknown>;
    for (const key of ['name', 'value', '@value', 'title']) {
      if (node[key] != null) return ldString(node[key]);
    }
  }
  return '';
}

/** Flatten schema.org `jobLocation` into a display string. */
function ldLocation(node: Record<string, unknown>): string {
  const raw = node.jobLocation;
  const first = Array.isArray(raw) ? raw[0] : raw;
  if (!first) {
    // Fully remote postings often use applicantLocationRequirements instead.
    return ldString(node.applicantLocationRequirements);
  }
  const address = (first as Record<string, unknown>).address as
    | Record<string, unknown>
    | undefined;
  if (!address) return ldString(first);
  const parts = [
    ldString(address.addressLocality),
    ldString(address.addressRegion),
    ldString(address.addressCountry),
  ].filter(Boolean);
  return parts.join(', ');
}

/** Format schema.org `baseSalary` into something a human reads. */
function ldSalary(node: Record<string, unknown>): string | null {
  const base = node.baseSalary as Record<string, unknown> | undefined;
  if (!base) return null;
  const currency = ldString(base.currency) || ldString(base.salaryCurrency);
  const value = base.value as Record<string, unknown> | undefined;
  if (!value) return null;

  const min = ldString(value.minValue);
  const max = ldString(value.maxValue);
  const single = ldString(value.value);
  const unit = ldString(value.unitText).toLowerCase();

  const amount = min && max ? `${min}-${max}` : single || min || max;
  if (!amount) return null;
  return [currency, amount, unit && `per ${unit}`].filter(Boolean).join(' ');
}

/** Strip HTML from a JSON-LD description, which is usually markup. */
function ldDescription(value: unknown): string {
  const raw = ldString(value);
  if (!raw) return '';
  if (!/[<>]/.test(raw)) return raw;
  const holder = document.createElement('div');
  holder.innerHTML = raw;
  return blockText(holder);
}

function metaContent(names: string[]): string {
  for (const name of names) {
    const el =
      document.querySelector<HTMLMetaElement>(`meta[property="${name}"]`) ??
      document.querySelector<HTMLMetaElement>(`meta[name="${name}"]`);
    if (el?.content) return clean(el.content);
  }
  return '';
}

/** Heuristic: does this page look like a single job posting at all? */
function looksLikeJobPage(): boolean {
  if (readJsonLd('JobPosting')) return true;
  const signals = `${document.title} ${metaContent(['og:title'])}`.toLowerCase();
  if (/\b(job|career|opening|position|vacancy|hiring)\b/.test(signals)) return true;
  // An apply button is the strongest DOM-level signal.
  return Boolean(
    pick([
      'button[class*="apply" i]',
      'a[class*="apply" i]',
      '[data-testid*="apply" i]',
      'form[action*="apply" i]',
    ]),
  );
}

export const genericAdapter: SiteAdapter = {
  id: 'extension',
  label: 'This page',

  // Matched explicitly by the registry as the last resort, never by URL.
  matches: () => false,

  classify(): PageKind {
    if (looksLikeJobPage()) return 'job-posting';
    return 'unknown';
  },

  extractJob(url: URL): CapturedJob | null {
    const ld = readJsonLd('JobPosting');

    if (ld) {
      return toJob('extension', url, {
        title: ldString(ld.title) || ldString(ld.name),
        company: ldString(ld.hiringOrganization),
        location: ldLocation(ld),
        description: ldDescription(ld.description),
        salary: ldSalary(ld),
        posted_at: ldString(ld.datePosted) || null,
        is_remote:
          ldString(ld.jobLocationType).toLowerCase().includes('telecommute') || null,
      });
    }

    // No structured data - fall back to metadata, then to the DOM.
    const title =
      metaContent(['og:title', 'twitter:title']) ||
      pickText(['h1', '[class*="job-title" i]', '[class*="jobTitle" i]']) ||
      clean(document.title);

    const company =
      metaContent(['og:site_name']) ||
      pickText(['[class*="company-name" i]', '[class*="companyName" i]', '[itemprop="hiringOrganization"]']);

    const description =
      blockText(
        pick([
          '[class*="job-description" i]',
          '[class*="jobDescription" i]',
          '[data-testid*="description" i]',
          '[itemprop="description"]',
          'article',
          'main',
        ]),
      ) || metaContent(['og:description', 'description']);

    return toJob('extension', url, {
      title,
      company,
      location: pickText(['[class*="location" i]', '[itemprop="jobLocation"]']),
      description,
    });
  },
};
