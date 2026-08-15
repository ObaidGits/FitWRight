/**
 * Adapter resolution and page classification.
 *
 * These are the twelve-plus files most exposed to other people's redesigns, and
 * they had no tests at all. What is pinned here is not extraction quality - that
 * needs real pages - but the decisions that make the extension appear to work or
 * not at all:
 *
 *  1. the right adapter claims a URL (an ATS must win over a board, or an apply
 *     page gets classified `unknown` and the popup offers nothing);
 *  2. an apply URL classifies as `application-form`, because that is what starts
 *     the wizard watcher and lights up the popup action;
 *  3. every adapter scopes filling to a form root, so a site-search box can never
 *     be typed into;
 *  4. an unknown host still resolves to the generic adapter rather than throwing.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { genericAdapter, resolveAdapter } from '@/adapters/registry';

function at(href: string) {
  return new URL(href);
}

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('adapter resolution', () => {
  it('claims each ATS host with its own adapter', () => {
    const cases: [string, string][] = [
      ['https://boards.greenhouse.io/acme/jobs/123', 'greenhouse'],
      ['https://jobs.lever.co/acme/abc-123', 'lever'],
      ['https://jobs.ashbyhq.com/acme/123', 'ashby'],
      ['https://acme.wd1.myworkdayjobs.com/en-US/careers/job/123', 'workday'],
      ['https://careers.smartrecruiters.com/Acme/123', 'smartrecruiters'],
      ['https://smartapply.indeed.com/beta/indeedapply/form/questions', 'indeed_apply'],
      // The platforms this change adds. Before it, every one of these fell
      // through to a board adapter or nothing at all.
      ['https://acme.icims.com/jobs/1234/engineer/login', 'icims'],
      ['https://acme.taleo.net/careersection/ex/jobdetail.ftl?job=1', 'taleo'],
      ['https://career5.successfactors.com/careers?company=acme', 'successfactors'],
      ['https://acme.workable.com/j/ABC123/apply', 'workable'],
      ['https://jobs.jobvite.com/acme/job/oABC', 'jobvite'],
      ['https://acme.breezy.hr/p/abc123-engineer', 'breezy'],
      ['https://acme.recruitee.com/o/senior-engineer', 'recruitee'],
      ['https://acme.teamtailor.com/jobs/1234-engineer', 'teamtailor'],
      ['https://acme.bamboohr.com/careers/42', 'bamboohr'],
    ];

    for (const [href, expected] of cases) {
      expect(resolveAdapter(at(href)).id, href).toBe(expected);
    }
  });

  it('falls back to generic on an unknown host rather than throwing', () => {
    expect(resolveAdapter(at('https://careers.some-random-company.example/jobs/1')).id).toBe(
      genericAdapter.id,
    );
  });

  it('never lets a board adapter claim a dedicated apply origin', () => {
    // The regression this pins: the Indeed *board* adapter matched
    // `hostname.includes('indeed.')`, claimed SmartApply, and classified the
    // application form as `unknown`.
    expect(resolveAdapter(at('https://smartapply.indeed.com/beta/form')).id).toBe('indeed_apply');
  });
});

describe('apply pages classify as forms', () => {
  it('recognises the apply path on each new platform', () => {
    const cases = [
      'https://acme.icims.com/jobs/1234/engineer/candidate/apply',
      'https://acme.workable.com/j/ABC123/apply',
      'https://jobs.jobvite.com/acme/job/oABC/apply',
      'https://acme.recruitee.com/o/engineer/apply',
      'https://acme.teamtailor.com/jobs/1234/application',
      'https://acme.bamboohr.com/careers/42/apply',
    ];
    for (const href of cases) {
      const url = at(href);
      expect(resolveAdapter(url).classify(url), href).toBe('application-form');
    }
  });

  it('treats a posting without a form as a posting', () => {
    const url = at('https://acme.recruitee.com/o/senior-engineer');
    expect(resolveAdapter(url).classify(url)).toBe('job-posting');
  });

  it('treats a Breezy position page with a form as a form', () => {
    // Breezy renders the application under the posting, so the DOM decides.
    document.body.innerHTML = '<form><input name="name" /></form>';
    const url = at('https://acme.breezy.hr/p/abc-engineer');
    expect(resolveAdapter(url).classify(url)).toBe('application-form');
  });
});

describe('fill scoping', () => {
  it('every adapter that can claim a form also scopes the fill', () => {
    // The real invariant. A board adapter is a list scraper and needs no form
    // root - but any adapter that reports `application-form` is telling autofill
    // to type into that page, and without a root it types into the whole
    // document, site-search boxes included.
    const applyUrls = [
      'https://acme.icims.com/jobs/1/x/apply',
      'https://acme.workable.com/j/A/apply',
      'https://acme.taleo.net/careersection/apply',
      'https://career5.successfactors.com/careers/apply',
      'https://jobs.jobvite.com/acme/job/o1/apply',
      'https://acme.recruitee.com/o/x/apply',
      'https://acme.teamtailor.com/jobs/1/application',
      'https://acme.bamboohr.com/careers/1/apply',
      'https://acme.breezy.hr/p/x/apply',
      'https://boards.greenhouse.io/acme/jobs/1',
      'https://jobs.lever.co/acme/x/apply',
      'https://smartapply.indeed.com/beta/form',
    ];

    for (const href of applyUrls) {
      const url = at(href);
      const adapter = resolveAdapter(url);
      // NOT skipped on classification any more. The previous version bailed with
      // `if (classify(url) !== 'application-form') continue`, which quietly
      // excluded every job-board listing URL - i.e. exactly the ten adapters that
      // had no formRoot at all. The check claimed to cover "every adapter" while
      // only ever reaching the ATS ones. Scoping is required unconditionally:
      // an adapter with no form root fills the page's search box.
      expect(typeof adapter.formRoot, `${adapter.id} claims ${href}`).toBe('function');
    }
  });

  it('a form root prefers the form over the whole page', () => {
    document.body.innerHTML = `
      <input id="site-search" placeholder="Search jobs" />
      <form id="app"><input name="first_name" /></form>
    `;
    const url = at('https://acme.workable.com/j/ABC/apply');
    const root = resolveAdapter(url).formRoot?.();
    expect(root).toBeTruthy();
    expect((root as ParentNode).querySelector('#site-search')).toBeNull();
    expect((root as ParentNode).querySelector('[name="first_name"]')).not.toBeNull();
  });
});

describe('extraction is defensive', () => {
  it('returns null rather than a titleless job on an empty page', () => {
    const url = at('https://acme.icims.com/jobs/1/engineer');
    expect(resolveAdapter(url).extractJob(url)).toBeNull();
  });

  it('reads a title and falls back to the subdomain for the company', () => {
    document.body.innerHTML = '<h1>Senior Data Engineer</h1>';
    const url = at('https://globex.icims.com/jobs/1/engineer');
    const job = resolveAdapter(url).extractJob(url);
    expect(job?.title).toBe('Senior Data Engineer');
    expect(job?.company).toBe('Globex');
  });
});
