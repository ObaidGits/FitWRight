/**
 * ATS adapters - the sites where applications are actually submitted.
 *
 * These matter most for autofill: Greenhouse, Lever, Ashby, Workday and
 * SmartRecruiters carry the large majority of tech applications, and each has a
 * consistent DOM across every company that uses it. That consistency is what
 * makes autofill reliable here in a way it can never be on a bespoke form.
 *
 * Each adapter also exposes `formRoot()` so autofill is scoped to the
 * application form and cannot touch a site-search or newsletter input.
 */
import { blockText, clean, pick, pickText } from '@/lib/dom';
import type { CapturedJob, PageKind } from '@/lib/types';
import { toJob } from './types';
import type { SiteAdapter } from './types';

/** Company slug from a hosted ATS path like /company-name/jobs/123. */
function slugCompany(url: URL): string {
  const segment = url.pathname.split('/').filter(Boolean)[0] ?? '';
  if (!segment || segment.length > 40) return '';
  return segment
    .replace(/[-_]+/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

export const greenhouseAdapter: SiteAdapter = {
  id: 'greenhouse',
  label: 'Greenhouse',
  readySelector: '#application, #header, .app-title, [class*="job__title"]',

  matches: (url) => url.hostname.endsWith('greenhouse.io'),

  classify(url): PageKind {
    // Greenhouse embeds the form on the posting itself, so a job page is both.
    if (/\/jobs?\/\d+/.test(url.pathname) || pick(['#application'])) {
      return pick(['#application, form[action*="applications"]'])
        ? 'application-form'
        : 'job-posting';
    }
    if (/\/(embed\/)?job_board/.test(url.pathname)) return 'job-list';
    return 'unknown';
  },

  extractJob(url) {
    return toJob('greenhouse', url, {
      title: pickText(['.app-title', 'h1.section-header', '[class*="job__title"] h1', 'h1']),
      company:
        pickText(['.company-name', '[class*="company-name"]', 'span.company']) ||
        slugCompany(url),
      location: pickText(['.location', '[class*="job__location"]', '.main-location']),
      description: blockText(pick(['#content', '.job__description', '[class*="job__description"]'])),
    });
  },

  extractList(url) {
    const rows = document.querySelectorAll<HTMLElement>('.opening, [class*="job-post"]');
    const jobs: CapturedJob[] = [];
    for (const row of rows) {
      const link = row.querySelector<HTMLAnchorElement>('a[href]');
      const job = toJob('greenhouse', url, {
        title: clean(link?.textContent ?? ''),
        company: slugCompany(url),
        location: clean(row.querySelector('.location, [class*="location"]')?.textContent ?? ''),
        url: link?.href,
      });
      if (job) jobs.push(job);
    }
    return jobs;
  },

  formRoot: () => pick(['#application', 'form[action*="applications"]', 'form#application_form']),
};

export const leverAdapter: SiteAdapter = {
  id: 'lever',
  label: 'Lever',
  readySelector: '.posting-headline, .application-form, [class*="posting"]',

  matches: (url) => url.hostname.endsWith('lever.co'),

  classify(url): PageKind {
    if (url.pathname.includes('/apply')) return 'application-form';
    // /{company}/{uuid} is a posting; /{company} alone is the board.
    const segments = url.pathname.split('/').filter(Boolean);
    if (segments.length >= 2) return 'job-posting';
    if (segments.length === 1) return 'job-list';
    return 'unknown';
  },

  extractJob(url) {
    return toJob('lever', url, {
      title: pickText(['.posting-headline h2', 'h2[data-qa="posting-name"]', '.posting-headline h1', 'h2']),
      company:
        pickText(['.main-header-logo img[alt]', '[class*="company"]']) ||
        pick<HTMLImageElement>(['.main-header-logo img'])?.alt ||
        slugCompany(url),
      location: pickText([
        '.posting-categories .location',
        '[class*="location"]',
        '.sort-by-time posting-category',
      ]),
      description: blockText(pick(['.section-wrapper.page-full-width', '[data-qa="job-description"]', '.content'])),
    });
  },

  extractList(url) {
    const rows = document.querySelectorAll<HTMLElement>('.posting');
    const jobs: CapturedJob[] = [];
    for (const row of rows) {
      const link = row.querySelector<HTMLAnchorElement>('a.posting-title, a[href]');
      const job = toJob('lever', url, {
        title: clean(row.querySelector('h5, .posting-title h5')?.textContent ?? ''),
        company: slugCompany(url),
        location: clean(row.querySelector('.location, [class*="location"]')?.textContent ?? ''),
        url: link?.href,
      });
      if (job) jobs.push(job);
    }
    return jobs;
  },

  formRoot: () => pick(['.application-form', 'form[action*="apply"]', 'form']),
};

export const ashbyAdapter: SiteAdapter = {
  id: 'ashby',
  label: 'Ashby',
  readySelector: '[class*="_jobPostingHeader"], h1, form',

  matches: (url) => url.hostname.endsWith('ashbyhq.com'),

  classify(url): PageKind {
    if (url.pathname.includes('/application')) return 'application-form';
    const segments = url.pathname.split('/').filter(Boolean);
    if (segments.length >= 2) return 'job-posting';
    return 'job-list';
  },

  extractJob(url) {
    return toJob('ashby', url, {
      title: pickText(['[class*="_jobPostingHeader"] h1', 'h1']),
      company:
        pick<HTMLImageElement>(['[class*="logo"] img'])?.alt?.replace(/\s*logo\s*/i, '') ||
        slugCompany(url),
      location: pickText(['[class*="_jobPostingHeaderDetails"]', '[class*="location"]']),
      description: blockText(pick(['[class*="_descriptionText"]', '[class*="jobDescription"]', 'main'])),
    });
  },

  formRoot: () => pick(['form[class*="application"]', 'form']),
};

export const workdayAdapter: SiteAdapter = {
  id: 'workday',
  label: 'Workday',
  // Workday renders everything client-side; wait for its automation ids.
  readySelector: '[data-automation-id="jobPostingHeader"], [data-automation-id="jobTitle"]',

  matches: (url) => /myworkdayjobs\.com$|workday\.com$/.test(url.hostname),

  classify(url): PageKind {
    if (/\/apply/i.test(url.pathname)) return 'application-form';
    if (/\/job\//i.test(url.pathname)) return 'job-posting';
    return 'job-list';
  },

  extractJob(url) {
    return toJob('workday', url, {
      title: pickText([
        '[data-automation-id="jobPostingHeader"]',
        '[data-automation-id="jobTitle"]',
        'h1',
      ]),
      // Workday is always hosted on the employer's own subdomain, which is a
      // more reliable company signal than anything in the DOM.
      company: clean(url.hostname.split('.')[0].replace(/[-_]+/g, ' ')),
      location: pickText([
        '[data-automation-id="locations"]',
        '[data-automation-id="jobPostingLocation"]',
      ]),
      description: blockText(
        pick(['[data-automation-id="jobPostingDescription"]', '[data-automation-id="richTextArea"]']),
      ),
      posted_at: pickText(['[data-automation-id="postedOn"]']) || null,
    });
  },

  formRoot: () => pick(['[data-automation-id="applyFlow"]', 'form', 'main']),
};

export const smartRecruitersAdapter: SiteAdapter = {
  id: 'smartrecruiters',
  label: 'SmartRecruiters',
  readySelector: 'h1, .job-title, [class*="jobTitle"]',

  matches: (url) => url.hostname.endsWith('smartrecruiters.com'),

  classify(url): PageKind {
    if (url.pathname.includes('/apply')) return 'application-form';
    const segments = url.pathname.split('/').filter(Boolean);
    return segments.length >= 2 ? 'job-posting' : 'job-list';
  },

  extractJob(url) {
    return toJob('smartrecruiters', url, {
      title: pickText(['h1.job-title', 'h1', '[class*="jobTitle"]']),
      company: pickText(['.company-name', '[class*="companyName"]']) || slugCompany(url),
      location: pickText(['[class*="job-location"]', 'spl-job-location', '[class*="location"]']),
      description: blockText(pick(['.job-sections', '[class*="jobDescription"]', 'main'])),
    });
  },

  formRoot: () => pick(['form[name="application"]', 'form']),
};

export const atsAdapters: SiteAdapter[] = [
  greenhouseAdapter,
  leverAdapter,
  ashbyAdapter,
  workdayAdapter,
  smartRecruitersAdapter,
];
