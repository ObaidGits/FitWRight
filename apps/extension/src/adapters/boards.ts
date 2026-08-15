/**
 * Job board adapters - where jobs are discovered rather than submitted.
 *
 * Instahyre, Hirist and Foundit are the reason this extension exists. The
 * server-side scrapers cannot reach them: Instahyre sits behind a Cloudflare
 * challenge, Foundit behind an Akamai WAF, and both gate results on a login. In
 * the user's own browser all three problems are already solved - residential IP,
 * real fingerprint, existing session - so these adapters are plain DOM readers.
 *
 * `extractList` is the important method here: it feeds bulk scraping.
 */
import { blockText, clean, pick, pickText } from '@/lib/dom';
import { findApplicationForm } from '@/lib/application-form';
import type { CapturedJob, PageKind } from '@/lib/types';
import { toJob } from './types';
import type { SiteAdapter } from './types';

/**
 * Scope autofill to a real application form, or return null when there isn't one.
 *
 * Every board adapter uses this, and none of them used to have a `formRoot` at
 * all - which meant autofill fell back to the whole document and read the site's
 * own search boxes as if they were application fields. On an Indeed listing page
 * that produced "could not read this form's 2 fields" about the "What" and
 * "Where" search inputs, and saved them into the user's Answers page.
 *
 * Boards are listing sites: most of their pages genuinely have no application
 * form, and `null` is the correct, useful answer for those. The ATS adapters keep
 * their own hand-written selectors, which are more precise where they apply.
 */
const boardFormRoot = () => findApplicationForm();

/**
 * Walk repeated card elements and map each to a job.
 * Shared because every board renders the same shape: a list of cards, each with
 * a title link, a company line and a location line.
 */
function harvestCards(
  source: string,
  url: URL,
  cardSelector: string,
  selectors: { title: string[]; company: string[]; location: string[]; salary?: string[] },
): CapturedJob[] {
  const jobs: CapturedJob[] = [];
  const seen = new Set<string>();

  for (const card of document.querySelectorAll<HTMLElement>(cardSelector)) {
    const titleEl = pick<HTMLElement>(selectors.title, card);
    const link =
      (titleEl?.closest('a') as HTMLAnchorElement | null) ??
      card.querySelector<HTMLAnchorElement>('a[href]');

    const job = toJob(source, url, {
      title: clean(titleEl?.textContent ?? ''),
      company: pickText(selectors.company, card),
      location: pickText(selectors.location, card),
      salary: selectors.salary ? pickText(selectors.salary, card) || null : null,
      url: link?.href || url.href,
    });
    if (!job) continue;

    // Same job can appear twice in a virtualized list.
    const key = `${job.title}|${job.company}`.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    jobs.push(job);
  }
  return jobs;
}

export const indeedAdapter: SiteAdapter = {
  id: 'indeed',
  label: 'Indeed',
  readySelector: '.jobsearch-JobInfoHeader-title, [data-testid="jobsearch-JobInfoHeader-title"], #mosaic-jobResults',

  matches: (url) => url.hostname.includes('indeed.'),

  classify(url): PageKind {
    if (url.pathname.startsWith('/viewjob')) return 'job-posting';
    // The search page shows a job in a side panel, so it is both a list and,
    // when a card is selected, a posting. List wins - bulk scrape is the point.
    if (url.pathname.startsWith('/jobs') || url.searchParams.has('q')) return 'job-list';
    return 'unknown';
  },

  extractJob(url) {
    return toJob('indeed', url, {
      title: pickText([
        '[data-testid="jobsearch-JobInfoHeader-title"]',
        '.jobsearch-JobInfoHeader-title',
        'h2[data-testid="jobsearch-JobInfoHeader-title"]',
        'h1',
      ]),
      company: pickText([
        '[data-testid="inlineHeader-companyName"]',
        '[data-company-name="true"]',
        '.jobsearch-CompanyInfoContainer a',
      ]),
      location: pickText([
        '[data-testid="inlineHeader-companyLocation"]',
        '[data-testid="job-location"]',
        '.jobsearch-JobInfoHeader-subtitle div:last-child',
      ]),
      salary: pickText(['#salaryInfoAndJobType', '[data-testid="attribute_snippet_testid"]']) || null,
      description: blockText(pick(['#jobDescriptionText', '.jobsearch-JobComponent-description'])),
    });
  },

  extractList(url) {
    return harvestCards('indeed', url, '.job_seen_beacon, [data-testid="slider_item"]', {
      title: ['h2.jobTitle span[title]', 'h2.jobTitle', '[id^="jobTitle"]'],
      company: ['[data-testid="company-name"]', '.companyName'],
      location: ['[data-testid="text-location"]', '.companyLocation'],
      salary: ['[data-testid="attribute_snippet_testid"]', '.salary-snippet-container'],
    });
  },
};

export const linkedinAdapter: SiteAdapter = {
  id: 'linkedin',
  label: 'LinkedIn',
  readySelector: '.jobs-unified-top-card, .job-details-jobs-unified-top-card__job-title, .top-card-layout__title',

  matches: (url) => url.hostname.endsWith('linkedin.com'),

  classify(url): PageKind {
    if (!url.pathname.includes('/jobs')) return 'unknown';
    if (url.pathname.includes('/jobs/view/')) return 'job-posting';
    if (url.pathname.includes('/jobs/search') || url.pathname.includes('/jobs/collections')) {
      return 'job-list';
    }
    return 'unknown';
  },

  extractJob(url) {
    return toJob('linkedin', url, {
      title: pickText([
        '.job-details-jobs-unified-top-card__job-title h1',
        '.job-details-jobs-unified-top-card__job-title',
        '.jobs-unified-top-card__job-title',
        '.top-card-layout__title',
        'h1',
      ]),
      company: pickText([
        '.job-details-jobs-unified-top-card__company-name a',
        '.job-details-jobs-unified-top-card__company-name',
        '.jobs-unified-top-card__company-name',
        '.topcard__org-name-link',
      ]),
      location: pickText([
        '.job-details-jobs-unified-top-card__tertiary-description-container span:first-child',
        '.jobs-unified-top-card__bullet',
        '.topcard__flavor--bullet',
      ]),
      description: blockText(
        pick(['.jobs-description__content', '#job-details', '.description__text', '.jobs-box__html-content']),
      ),
    });
  },

  extractList(url) {
    return harvestCards(
      'linkedin',
      url,
      '.job-card-container, .jobs-search-results__list-item, [data-job-id]',
      {
        title: ['.job-card-list__title', '.job-card-container__link', 'a.job-card-list__title--link'],
        company: ['.job-card-container__primary-description', '.artdeco-entity-lockup__subtitle'],
        location: ['.job-card-container__metadata-item', '.artdeco-entity-lockup__caption'],
      },
    );
  },
};

export const instahyreAdapter: SiteAdapter = {
  id: 'instahyre',
  label: 'Instahyre',
  // Angular app: results appear well after load, so wait on a real result node.
  readySelector: '.employer-job-name, .company-name, .employer-info',

  matches: (url) => url.hostname.endsWith('instahyre.com'),

  classify(url): PageKind {
    if (url.pathname.includes('/search-jobs') || url.pathname.includes('/jobs')) return 'job-list';
    if (/\/opportunit(y|ies)\//.test(url.pathname)) return 'job-posting';
    return 'unknown';
  },

  extractJob(url) {
    return toJob('instahyre', url, {
      title: pickText(['.employer-job-name', '[class*="job-title"]', '[class*="designation"]', 'h1', 'h3']),
      company: pickText(['.employer-company-name', '.company-name', '[class*="companyName"]']),
      location: stripInstahyrePrefix(pickText(['.employer-locations', '[class*="location"]'])) || 'India',
      description: blockText(pick(['[class*="job-description"]', '.employer-notes', 'main'])),
    });
  },

  /**
   * Instahyre renders every result twice - a desktop row (`.employer-details`)
   * and a mobile row (`.employer-details-mobile`), both present in the DOM and
   * switched by CSS - so only the desktop variant is read. Taking both yielded
   * each job twice under two different shapes, which de-duplication could not
   * collapse because the titles genuinely differ between them.
   *
   * The desktop heading is "Company - Role" (the mobile one drops the company
   * entirely and there is no separate company node), so the pair is split here.
   * Cards carry no per-job anchor - navigation is an Angular click handler - so
   * rows inherit the search URL; the backend fingerprints on title, company and
   * location as well, so they do not collapse into one another.
   */
  extractList(url) {
    const jobs: CapturedJob[] = [];
    const seen = new Set<string>();

    for (const card of document.querySelectorAll<HTMLElement>('.employer-details')) {
      const heading = clean(card.querySelector('.employer-job-name')?.textContent ?? '');
      if (!heading) continue;

      const dash = heading.indexOf(' - ');
      const company = dash > 0 ? heading.slice(0, dash).trim() : '';
      const title = dash > 0 ? heading.slice(dash + 3).trim() : heading;

      const job = toJob('instahyre', url, {
        title,
        company,
        location:
          stripInstahyrePrefix(clean(card.querySelector('.employer-locations')?.textContent ?? '')) ||
          'India',
        url: card.querySelector<HTMLAnchorElement>('a[href]')?.href || url.href,
      });
      if (!job) continue;

      const key = `${job.title}|${job.company}`.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      jobs.push(job);
    }
    return jobs;
  },
};

/** `Job available in Gurgaon` -> `Gurgaon`. */
function stripInstahyrePrefix(text: string): string {
  return clean(text).replace(/^jobs?\s+available\s+in\s+/i, '');
}

export const hiristAdapter: SiteAdapter = {
  id: 'hirist',
  label: 'Hirist',
  readySelector: 'a[href^="/j/"], h1',

  matches: (url) => url.hostname.endsWith('hirist.tech') || url.hostname.endsWith('hirist.com'),

  classify(url): PageKind {
    // Postings are /j/<slug>-<id>; listings are /k/<keyword>-jobs (keyword),
    // /c/<category>-jobs (category) and the logged-in /jobfeed.
    if (url.pathname.startsWith('/j/')) return 'job-posting';
    if (
      url.pathname.startsWith('/k/') ||
      url.pathname.startsWith('/c/') ||
      url.pathname.includes('/jobfeed') ||
      url.pathname.includes('/jobs')
    ) {
      return 'job-list';
    }
    return 'unknown';
  },

  extractJob(url) {
    return toJob('hirist', url, {
      title: pickText(['[class*="jobTitle"]', '[class*="designation"]', 'h1']),
      company: pickText(['[class*="companyName"]', '[class*="company"]']),
      location: pickText(['[class*="location"]']) || 'India',
      description: blockText(pick(['[class*="jobDescription"]', '[class*="jd"]', 'main'])),
    });
  },

  /**
   * Hirist renders with MUI, so every card class is a generated hash
   * (`div.MuiBox-root.mui-style-1e1em8u`) that changes on any restyle - useless
   * as a selector. Anchor on meaning instead: each result is a `/j/<slug>-<id>`
   * link, and that link wraps the whole card.
   *
   * Because the anchor wraps everything, its text runs together as
   * "Capgemini - MLOps Engineer - Python6 - 14 yrsMultiple LocationsPython...".
   * The experience range is the reliable boundary - every card has one - so the
   * heading is whatever precedes it, and Hirist publishes headings as
   * "Company - Role" (the same shape the server-side connector stored).
   */
  extractList(url) {
    const jobs: CapturedJob[] = [];
    const seen = new Set<string>();

    for (const link of document.querySelectorAll<HTMLAnchorElement>('a[href^="/j/"]')) {
      const full = clean(link.textContent ?? '');
      if (!full || full.length < 6) continue;

      // "6 - 14 yrs" / "4-10 yrs" ends the heading.
      const experience = full.match(/\d+\s*-\s*\d+\s*yrs?/i);
      const heading = experience ? full.slice(0, experience.index).trim() : full;

      const dash = heading.indexOf(' - ');
      const company = dash > 0 ? heading.slice(0, dash).trim() : '';
      const title = dash > 0 ? heading.slice(dash + 3).trim() : heading;

      const job = toJob('hirist', url, {
        title,
        company,
        location: hiristLocation(link, heading),
        url: link.href,
      });
      if (!job) continue;

      const key = `${job.title}|${job.company}`.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      jobs.push(job);
    }
    return jobs;
  },
};

/**
 * The location line inside a Hirist result card.
 *
 * The card is one big anchor whose paragraphs include the heading itself, so the
 * heading text is skipped, as are the "Posted N days ago" stamps. What remains
 * is the place line ("Multiple Locations", "Bengaluru").
 */
function hiristLocation(link: HTMLElement, heading: string): string {
  for (const p of link.querySelectorAll<HTMLElement>('p')) {
    const text = clean(p.textContent ?? '');
    if (!text || text.length > 60) continue;
    if (heading.includes(text) || text.includes(heading)) continue;
    if (/^posted\b/i.test(text) || /\byrs?\b/i.test(text)) continue;
    return text;
  }
  return 'India';
}

export const founditAdapter: SiteAdapter = {  id: 'foundit',
  label: 'Foundit',
  readySelector: '.cardContainer, #jobCardTitle, .jobTitle',

  matches: (url) => url.hostname.endsWith('foundit.in') || url.hostname.endsWith('monsterindia.com'),

  classify(url): PageKind {
    if (url.pathname.includes('/srp/results')) return 'job-list';
    if (url.pathname.includes('/job/') || url.pathname.includes('/jd/')) return 'job-posting';
    return 'unknown';
  },

  extractJob(url) {
    return toJob('foundit', url, {
      title: pickText(['#jobCardTitle', '.jobTitle', 'h1']),
      company: pickText(['.companyName p', '.companyName', '[class*="company"]']),
      location: pickText(['[class*="location"]', '.details .loc']) || 'India',
      description: blockText(pick(['[class*="jobDescription"]', '#jobDescription', 'main'])),
    });
  },

  extractList(url) {
    // Verified against Foundit's rendered DOM: div.cardContainer wraps each
    // result, with div.jobTitle, div.companyName > p and div.location inside.
    // The cards hold no per-job anchor, so rows inherit the search URL.
    return harvestCards('foundit', url, '.cardContainer, .srpResultCardContainer', {
      title: ['.jobTitle', '#jobCardTitle'],
      company: ['.companyName p', '.companyName'],
      location: ['.location', '[class*="locationExp"]'],
      salary: ['[class*="salary"]'],
    });
  },
};

/**
 * Y Combinator's Work at a Startup.
 *
 * Unlike the other boards here, the card fields are not addressable by class:
 * the markup is Tailwind utility soup (`div.flex.h-full.cursor-pointer`) that
 * changes whenever the layout is touched. So this adapter anchors on structure
 * that carries meaning instead - a job card is whatever element contains both a
 * `/jobs/<numeric-id>` link (the role) and a `/companies/<slug>` link (the
 * startup). That survives restyling in a way a class list does not.
 *
 * Public without a login: the listing renders for anonymous visitors, only
 * applying requires an account.
 */
export const ycombinatorAdapter: SiteAdapter = {
  id: 'ycombinator',
  label: 'YC Startups',
  readySelector: 'a[href*="/jobs/"], .company-logo',

  matches: (url) =>
    url.hostname.endsWith('workatastartup.com') || url.hostname.endsWith('ycombinator.com'),

  classify(url): PageKind {
    // /jobs/123456 is one role; /jobs and /jobs/l/<role-slug> are listings.
    if (/^\/jobs\/\d+$/.test(url.pathname)) return 'job-posting';
    if (url.pathname === '/jobs' || url.pathname.startsWith('/jobs/l/')) return 'job-list';
    if (url.pathname.startsWith('/companies/')) return 'job-list';
    return 'unknown';
  },

  extractJob(url) {
    return toJob('ycombinator', url, {
      title: pickText(['h1', '.job-title', '[class*="title"]']),
      company: stripYcBatch(pickText(['.company-name', 'span.font-bold', 'h2 a'])),
      location: pickText(['[class*="location"]', '.job-details']),
      description: blockText(pick(['.job-description', '[class*="description"]', 'main'])),
    });
  },

  extractList(url) {
    const jobs: CapturedJob[] = [];
    const seen = new Set<string>();

    for (const link of document.querySelectorAll<HTMLAnchorElement>('a[href*="/jobs/"]')) {
      const href = link.getAttribute('href') ?? '';
      // Only numeric ids are roles - `/jobs/l/software-engineer` is a filter.
      if (!/^\/jobs\/\d+$/.test(href)) continue;

      const card = findYcCard(link);
      if (!card) continue;

      const title = clean(link.textContent ?? '');
      const company = stripYcBatch(
        clean(card.querySelector<HTMLElement>('span.font-bold')?.textContent ?? '') ||
          card.querySelector<HTMLImageElement>('img[alt]')?.alt ||
          '',
      );

      const job = toJob('ycombinator', url, {
        title,
        company,
        location: ycFieldFrom(card, 'location'),
        salary: ycFieldFrom(card, 'salary') || null,
        url: link.href,
      });
      if (!job) continue;

      const key = `${job.title}|${job.company}`.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      jobs.push(job);
    }
    return jobs;
  },
};

/**
 * Walk up from a role link to the element that also holds the company link.
 * Bounded to a few levels so a page-wide container never counts as a card.
 */
function findYcCard(link: HTMLElement): HTMLElement | null {
  let node: HTMLElement | null = link.parentElement;
  for (let depth = 0; node && depth < 6; depth += 1) {
    if (node.querySelector('a[href^="/companies/"]')) return node;
    node = node.parentElement;
  }
  return null;
}

/**
 * Pull location or salary out of a YC card's chip row.
 *
 * The chips are unlabelled sibling spans - `Fulltime`, `Remote (US)`,
 * `Full stack`, `$124K - $188K CAD` - so they are told apart by shape rather
 * than position: money starts with a currency figure, and a location either
 * says remote or carries comma-separated place names.
 *
 * Only leaf elements outside the company link are considered. Verified against
 * the live page: the company line - name, bullet, one-line pitch - all sits
 * inside the `/companies/<slug>` anchor, and its pitch ("Marketing automation
 * for event promoters (email, sms, ads, CRM)") otherwise wins the location slot
 * on comma count. Ancestor divs are skipped too, because they yield run-together
 * text like "FulltimeBengaluru, KA, IN".
 */
function ycFieldFrom(card: HTMLElement, want: 'location' | 'salary'): string {
  const EMPLOYMENT = /^(fulltime|full[- ]time|parttime|part[- ]time|intern(ship)?|contract|co[- ]?founder)$/i;

  const chips = [...card.querySelectorAll<HTMLElement>('span, div, p')]
    .filter((el) => el.children.length === 0 && !el.closest('a[href^="/companies/"]'))
    .map((el) => clean(el.textContent ?? ''))
    .filter((text) => text && text.length < 80);

  for (const chip of chips) {
    const isMoney = /^[$€£₹]\s?\d/.test(chip) || /\d+\s?K\s*-\s*\$?\d+\s?K/i.test(chip);
    if (want === 'salary') {
      if (isMoney) return chip;
      continue;
    }
    if (isMoney || EMPLOYMENT.test(chip)) continue;
    // Remote, or comma-separated place names ("Bengaluru, KA, IN").
    if (/\bremote\b/i.test(chip) || chip.split(',').length >= 2) return chip;
  }
  return '';
}

/** `Hive (S14)` -> `Hive`. The batch tag is metadata, not part of the name. */
function stripYcBatch(name: string): string {
  return clean(name).replace(/\s*\((?:[WSXFI]{1,2}\d{2}|[A-Z]{1,3}\d{2,4})\)\s*$/, '');
}

/**
 * Naukri - India's largest board, and the one the server cannot touch at all
 * (its API answers `406 recaptcha required` to anything datacenter-shaped).
 * Server-friendly markup though: real class names and an absolute job href.
 */
export const naukriAdapter: SiteAdapter = {
  id: 'naukri',
  label: 'Naukri',
  readySelector: 'div.srp-jobtuple-wrapper, .styles_jd-header-title__rZwM1',

  matches: (url) => url.hostname.endsWith('naukri.com'),

  classify(url): PageKind {
    if (url.pathname.startsWith('/job-listings-')) return 'job-posting';
    if (/-jobs(-|$)/.test(url.pathname) || url.pathname.includes('/jobs')) return 'job-list';
    return 'unknown';
  },

  extractJob(url) {
    return toJob('naukri', url, {
      title: pickText(['.styles_jd-header-title__rZwM1', 'h1']),
      company: pickText(['.styles_jd-header-comp-name__MvqAI a', '.comp-name', 'a.comp-name']),
      location: pickText(['.styles_jhc__location__W_pVs', 'span.locWdth', '[class*="location"]']),
      salary: pickText(['.styles_jhc__salary__jdfEC', 'span.sal-wrap span', 'span.sal']) || null,
      description: blockText(pick(['.styles_JDC__dang-inner-html__h0K4t', '[class*="job-desc"]', 'main'])),
    });
  },

  extractList(url) {
    return harvestCards('naukri', url, 'div.srp-jobtuple-wrapper', {
      title: ['a.title'],
      company: ['a.comp-name', '.comp-name'],
      location: ['span.locWdth', '[class*="loc"]'],
      salary: ['span.sal-wrap span', 'span.sal'],
    });
  },
};

/**
 * ZipRecruiter. Blocked server-side by Cloudflare (`403 forbidden aa`).
 *
 * Each result is rendered twice for responsive layout, so the two-pane wrapper
 * is used as the card rather than `article`, which matches both copies. The
 * cards carry no per-job href - the detail opens in a pane via JS - so rows
 * inherit the search URL.
 */
export const zipRecruiterAdapter: SiteAdapter = {
  id: 'zip_recruiter',
  label: 'ZipRecruiter',
  readySelector: 'div.job_result_two_pane_v2, [data-testid="job-card-company"], h1',

  matches: (url) => url.hostname.endsWith('ziprecruiter.com'),

  classify(url): PageKind {
    if (url.pathname.startsWith('/jobs-search') || url.pathname.startsWith('/candidate/search')) {
      return 'job-list';
    }
    if (url.pathname.startsWith('/c/') || url.pathname.includes('/Job/')) return 'job-posting';
    return 'unknown';
  },

  extractJob(url) {
    return toJob('zip_recruiter', url, {
      title: pickText(['h1', '[data-testid="job-title"]']),
      company: pickText(['[data-testid="job-card-company"]', '[data-testid="company-name"]']),
      location: pickText(['[data-testid="job-card-location"]', '[data-testid="job-location"]']),
      description: blockText(pick(['[data-testid="job-description"]', '.job_description', 'main'])),
    });
  },

  extractList(url) {
    const jobs: CapturedJob[] = [];
    const seen = new Set<string>();

    for (const card of document.querySelectorAll<HTMLElement>('div.job_result_two_pane_v2')) {
      const title = clean(card.querySelector('h2')?.textContent ?? '');
      if (!title) continue;

      // Pay is an unlabelled line; it is the only one shaped like money.
      const salary =
        (card.innerText || '')
          .split('\n')
          .map((line) => line.trim())
          .find((line) => /^[$€£₹]\s?[\d,]/.test(line)) ?? null;

      const job = toJob('zip_recruiter', url, {
        title,
        company: clean(
          card.querySelector('[data-testid="job-card-company"]')?.textContent ?? '',
        ),
        location: clean(
          card.querySelector('[data-testid="job-card-location"]')?.textContent ?? '',
        ),
        salary,
        url: url.href,
      });
      if (!job) continue;

      const key = `${job.title}|${job.company}`.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      jobs.push(job);
    }
    return jobs;
  },
};

/**
 * Glassdoor. Its server-side location API answers `400` to every query shape,
 * but the rendered page is the friendliest of the four: stable `data-test`
 * hooks on every field.
 *
 * The employer element carries the rating on a second line ("Aristocrat\n3.2"),
 * so only the first line is the company name.
 */
export const glassdoorAdapter: SiteAdapter = {
  id: 'glassdoor',
  label: 'Glassdoor',
  readySelector: '[data-test="jobListing"], [data-test="job-title"], h1',

  matches: (url) => url.hostname.includes('glassdoor.'),

  classify(url): PageKind {
    if (url.pathname.includes('/Job/') || url.pathname.includes('/Jobs/')) return 'job-list';
    if (url.pathname.includes('jobListing.htm')) return 'job-posting';
    return 'unknown';
  },

  extractJob(url) {
    return toJob('glassdoor', url, {
      title: pickText(['[data-test="job-title"]', 'h1']),
      company: firstLine(pickText(['[data-test="employerName"]', '[class*="EmployerProfile"]'])),
      location: pickText(['[data-test="location"]', '[data-test="emp-location"]']),
      salary: pickText(['[data-test="detailSalary"]']) || null,
      description: blockText(pick(['[data-test="description"]', '.JobDetails_jobDescription__uW_fK', 'main'])),
    });
  },

  extractList(url) {
    const jobs: CapturedJob[] = [];
    const seen = new Set<string>();

    for (const card of document.querySelectorAll<HTMLElement>('[data-test="jobListing"]')) {
      const title = clean(card.querySelector('[data-test="job-title"]')?.textContent ?? '');
      if (!title) continue;

      const link = card.querySelector<HTMLAnchorElement>('[data-test="job-link"]');
      const job = toJob('glassdoor', url, {
        title,
        company: firstLine(
          clean(
            card.querySelector('[class*="EmployerProfile_compactEmployerName"]')?.textContent ??
              card.querySelector('[data-test="employerName"]')?.textContent ??
              '',
          ),
        ),
        location: clean(card.querySelector('[data-test="emp-location"]')?.textContent ?? ''),
        salary: clean(card.querySelector('[data-test="detailSalary"]')?.textContent ?? '') || null,
        url: link?.href || url.href,
      });
      if (!job) continue;

      const key = `${job.title}|${job.company}`.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      jobs.push(job);
    }
    return jobs;
  },
};

/**
 * Google Jobs (`/search?...&udm=8`). Returns nothing server-side.
 *
 * Google randomises every class name per deployment and exposes no data-test or
 * aria hook on a result, so there is nothing to select on. What *is* stable is
 * the text shape of an entry:
 *
 *     Python Developer - Azure & Devops Focus (Kolkata)
 *     Sandhata Technologies private
 *     Kolkata, West Bengal • via Shine
 *     17 hours ago
 *
 * The third line's " via " attribution is the distinguishing marker - Google
 * always credits the board it syndicated from - so entries are found by that
 * rather than by markup. Nested wrappers repeat the same text, which the
 * title|company de-duplication collapses.
 */
export const googleJobsAdapter: SiteAdapter = {
  id: 'google',
  label: 'Google',
  readySelector: 'div[role="main"], #search, #rso',

  matches: (url) => /(^|\.)google\.[a-z.]+$/.test(url.hostname),

  classify(url): PageKind {
    // Only the jobs surface, never an ordinary search.
    if (url.pathname !== '/search') return 'unknown';
    const isJobs = url.searchParams.get('udm') === '8' || /htl;jobs/.test(url.search);
    return isJobs ? 'job-list' : 'unknown';
  },

  extractJob(url) {
    // The detail pane repeats the list entry; the list path covers it.
    return toJob('google', url, { title: pickText(['h1', 'h2']) });
  },

  extractList(url) {
    const jobs: CapturedJob[] = [];
    const seen = new Set<string>();

    for (const el of document.querySelectorAll<HTMLElement>('div, span, li')) {
      const lines = (el.innerText || '')
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
      if (lines.length < 3 || lines.length > 5) continue;

      const viaLine = lines[2];
      if (!/\s(?:•|·|\|)\s*via\s/i.test(viaLine)) continue;

      const [title, company] = lines;
      if (!title || title.length > 140) continue;

      const key = `${title}|${company}`.toLowerCase();
      if (seen.has(key)) continue;

      const job = toJob('google', url, {
        title,
        company,
        // "Kolkata, West Bengal • via Shine" -> "Kolkata, West Bengal"
        location: viaLine.split(/\s(?:•|·|\|)\s*via\s/i)[0].trim(),
        url: url.href,
      });
      if (!job) continue;

      seen.add(key);
      jobs.push(job);
    }
    return jobs;
  },
};

/** `Aristocrat\n3.2` -> `Aristocrat`. The rating is not part of the name. */
function firstLine(text: string): string {
  return clean((text || '').split('\n')[0] ?? '');
}

export const boardAdapters: SiteAdapter[] = [
  indeedAdapter,
  linkedinAdapter,
  instahyreAdapter,
  hiristAdapter,
  founditAdapter,
  ycombinatorAdapter,
  naukriAdapter,
  zipRecruiterAdapter,
  glassdoorAdapter,
  googleJobsAdapter,
  // Every board gets the evidence-based form detector. Attached here rather than
  // repeated in each adapter so a new board cannot be added without it - the
  // omission is exactly what broke autofill on all ten of them.
].map((adapter) => (adapter.formRoot ? adapter : { ...adapter, formRoot: boardFormRoot }));
