/**
 * The enterprise and mid-market ATS platforms.
 *
 * The gap these close: the extension covered Greenhouse, Lever, Ashby, Workday
 * and SmartRecruiters - which is most of *tech* hiring, and a minority of hiring.
 * On an iCIMS, Taleo or SuccessFactors form the extension was not worse, it was
 * absent: no content script, no popup action, nothing. A user applying through a
 * large employer saw a dead extension and concluded it did not work.
 *
 * These adapters are deliberately thinner than the tech-ATS ones. Filling does not
 * need per-site knowledge - the field classifier reads labels, and labels are
 * labels everywhere. What an adapter adds is:
 *
 *  1. recognising that a page IS an application form, so the popup offers the
 *     action and the wizard watcher starts;
 *  2. scoping the fill to the form, so a site-search box is never touched;
 *  3. a best-effort title/company for the record of what was applied to.
 *
 * Selectors here are attribute-substring based rather than exact class names,
 * because these platforms are heavily themed per customer and a class that exists
 * on one tenant will not exist on the next. Where a platform's DOM could not be
 * verified against a live posting, the adapter says so rather than pretending.
 */
import { blockText, pick, pickText } from '@/lib/dom';
import type { PageKind } from '@/lib/types';
import { toJob } from './types';
import type { SiteAdapter } from './types';

/** Paths that mean "this is the form", across every one of these platforms. */
const APPLY_PATH = /(apply|application|candidate|onlineapplication|jobapply)/i;

/** A company name from the subdomain, which is how these platforms are tenanted. */
function subdomainCompany(url: URL): string {
  const [first] = url.hostname.split('.');
  if (!first || ['www', 'careers', 'jobs', 'career', 'apply'].includes(first)) return '';
  return first.replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Shared extraction: these platforms all put the title in an h1 or a testid. */
function genericJob(source: string, url: URL) {
  return toJob(source, url, {
    title: pickText([
      'h1',
      '[data-testid*="title" i]',
      '[class*="jobTitle" i]',
      '[class*="job-title" i]',
      '[id*="jobtitle" i]',
    ]),
    company:
      pickText(['[class*="companyName" i]', '[data-testid*="company" i]']) ||
      subdomainCompany(url),
    location: pickText([
      '[class*="location" i]',
      '[data-testid*="location" i]',
      '[id*="location" i]',
    ]),
    description: blockText(
      pick(['[class*="jobDescription" i]', '[id*="jobdescription" i]', 'main', '#content']),
    ),
  });
}

/**
 * iCIMS. Tenanted as `<company>.icims.com`, and the apply flow lives under
 * `/jobs/<id>/<slug>/login` or `/candidate`.
 */
export const icimsAdapter: SiteAdapter = {
  id: 'icims',
  label: 'iCIMS',
  readySelector: 'h1, .iCIMS_JobHeader, [class*="iCIMS"], form',

  matches: (url) => url.hostname.endsWith('icims.com'),

  classify(url): PageKind {
    if (APPLY_PATH.test(url.pathname)) return 'application-form';
    if (/\/jobs?\//i.test(url.pathname)) return 'job-posting';
    return 'job-list';
  },

  extractJob(url) {
    return genericJob('icims', url);
  },

  // iCIMS renders the form inside an iframe on some tenants; when it is inline
  // this scopes correctly, and when it is framed the content script runs in the
  // frame and `form` is the right root there too.
  formRoot: () => pick(['form[name*="application" i]', '.iCIMS_MainWrapper', 'form', 'main']),
};

/**
 * Oracle Taleo. Two shapes in the wild: the older `*.taleo.net` career sections
 * and Oracle Cloud Recruiting on `*.oraclecloud.com`.
 */
export const taleoAdapter: SiteAdapter = {
  id: 'taleo',
  label: 'Taleo',
  readySelector: 'h1, #requisitionDescriptionInterface, form',

  matches: (url) =>
    url.hostname.endsWith('taleo.net') || url.hostname.endsWith('oraclecloud.com'),

  classify(url): PageKind {
    if (APPLY_PATH.test(url.pathname) || /careersection/i.test(url.pathname)) {
      // Taleo's career section hosts both the posting and the form; the presence
      // of a file input is the reliable tell.
      return pick(['input[type="file"]']) ? 'application-form' : 'job-posting';
    }
    return 'job-list';
  },

  extractJob(url) {
    return genericJob('taleo', url);
  },

  formRoot: () => pick(['form[name*="application" i]', '#requisitionDescriptionInterface', 'form']),
};

/** SAP SuccessFactors. Tenanted per customer, `career*` hosts and `/careers` paths. */
export const successFactorsAdapter: SiteAdapter = {
  id: 'successfactors',
  label: 'SuccessFactors',
  readySelector: 'h1, [data-automation-id], form',

  matches: (url) =>
    url.hostname.endsWith('successfactors.com') ||
    url.hostname.endsWith('successfactors.eu') ||
    url.hostname.endsWith('sapsf.com'),

  classify(url): PageKind {
    if (APPLY_PATH.test(url.pathname)) return 'application-form';
    if (/\/job\b|jobdetail/i.test(url.pathname)) return 'job-posting';
    return 'job-list';
  },

  extractJob(url) {
    return genericJob('successfactors', url);
  },

  formRoot: () => pick(['form', '[role="main"]', 'main']),
};

/** Workable. `<company>.workable.com`, apply at `/j/<id>/apply`. */
export const workableAdapter: SiteAdapter = {
  id: 'workable',
  label: 'Workable',
  readySelector: 'h1, [data-ui="job-title"], form',

  matches: (url) => url.hostname.endsWith('workable.com'),

  classify(url): PageKind {
    if (/\/apply\b/i.test(url.pathname)) return 'application-form';
    if (/\/j\//i.test(url.pathname)) return 'job-posting';
    return 'job-list';
  },

  extractJob(url) {
    return toJob('workable', url, {
      title: pickText(['[data-ui="job-title"]', 'h1']),
      company: pickText(['[data-ui="company-name"]']) || subdomainCompany(url),
      location: pickText(['[data-ui="job-location"]', '[class*="location" i]']),
      description: blockText(pick(['[data-ui="job-description"]', 'main'])),
    });
  },

  formRoot: () => pick(['form[data-ui="application-form"]', 'form', 'main']),
};

/** Jobvite. `jobs.jobvite.com/<company>/job/<id>`. */
export const jobviteAdapter: SiteAdapter = {
  id: 'jobvite',
  label: 'Jobvite',
  readySelector: 'h1, .jv-job-detail-title, form',

  matches: (url) => url.hostname.endsWith('jobvite.com'),

  classify(url): PageKind {
    if (APPLY_PATH.test(url.pathname)) return 'application-form';
    if (/\/job\//i.test(url.pathname)) return 'job-posting';
    return 'job-list';
  },

  extractJob(url) {
    return toJob('jobvite', url, {
      title: pickText(['.jv-job-detail-title', 'h1']),
      company: pickText(['.jv-company-name']) || subdomainCompany(url),
      location: pickText(['.jv-job-detail-meta', '[class*="location" i]']),
      description: blockText(pick(['.jv-job-detail-description', 'main'])),
    });
  },

  formRoot: () => pick(['form.jv-form', 'form', 'main']),
};

/** BreezyHR. `<company>.breezy.hr/p/<id>`, apply on the same page. */
export const breezyAdapter: SiteAdapter = {
  id: 'breezy',
  label: 'Breezy',
  readySelector: 'h1, .position-title, form',

  matches: (url) => url.hostname.endsWith('breezy.hr'),

  classify(url): PageKind {
    if (APPLY_PATH.test(url.pathname)) return 'application-form';
    // Breezy puts the form under the posting, so a position page is both.
    if (/\/p\//i.test(url.pathname)) {
      return pick(['form']) ? 'application-form' : 'job-posting';
    }
    return 'job-list';
  },

  extractJob(url) {
    return toJob('breezy', url, {
      title: pickText(['.position-title', 'h1']),
      company: subdomainCompany(url),
      location: pickText(['.position-location', '[class*="location" i]']),
      description: blockText(pick(['.description', 'main'])),
    });
  },

  formRoot: () => pick(['form#application', 'form', 'main']),
};

/** Recruitee. `<company>.recruitee.com/o/<slug>`. */
export const recruiteeAdapter: SiteAdapter = {
  id: 'recruitee',
  label: 'Recruitee',
  readySelector: 'h1, form',

  matches: (url) => url.hostname.endsWith('recruitee.com'),

  classify(url): PageKind {
    if (APPLY_PATH.test(url.pathname)) return 'application-form';
    if (/\/o\//i.test(url.pathname)) return 'job-posting';
    return 'job-list';
  },

  extractJob(url) {
    return genericJob('recruitee', url);
  },

  formRoot: () => pick(['form', 'main']),
};

/** Teamtailor. `<company>.teamtailor.com/jobs/<slug>`. */
export const teamtailorAdapter: SiteAdapter = {
  id: 'teamtailor',
  label: 'Teamtailor',
  readySelector: 'h1, form',

  matches: (url) => url.hostname.endsWith('teamtailor.com'),

  classify(url): PageKind {
    if (APPLY_PATH.test(url.pathname)) return 'application-form';
    if (/\/jobs?\//i.test(url.pathname)) return 'job-posting';
    return 'job-list';
  },

  extractJob(url) {
    return genericJob('teamtailor', url);
  },

  formRoot: () => pick(['form', 'main']),
};

/** BambooHR. `<company>.bamboohr.com/careers/<id>`. */
export const bambooAdapter: SiteAdapter = {
  id: 'bamboohr',
  label: 'BambooHR',
  readySelector: 'h1, form',

  matches: (url) => url.hostname.endsWith('bamboohr.com'),

  classify(url): PageKind {
    if (APPLY_PATH.test(url.pathname)) return 'application-form';
    if (/\/careers\//i.test(url.pathname)) return 'job-posting';
    return 'job-list';
  },

  extractJob(url) {
    return genericJob('bamboohr', url);
  },

  formRoot: () => pick(['form', 'main']),
};

/**
 * Every enterprise/mid-market adapter, in the order the registry should try them.
 *
 * Order is not significant between these - each `matches` on its own hostname -
 * but keeping the list explicit means adding a platform is one line here and one
 * line in the manifest, which is the whole point of splitting this file out.
 */
export const enterpriseAtsAdapters: SiteAdapter[] = [
  icimsAdapter,
  taleoAdapter,
  successFactorsAdapter,
  workableAdapter,
  jobviteAdapter,
  breezyAdapter,
  recruiteeAdapter,
  teamtailorAdapter,
  bambooAdapter,
];
