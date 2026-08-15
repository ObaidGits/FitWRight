/**
 * Application-form detection - the fix for "autofill does nothing on Indeed".
 *
 * THE BUG THESE PIN
 *
 * None of the ten job-board adapters implemented `formRoot()`, so autofill fell
 * back to the whole `document` and read every input on the page. On an Indeed
 * listing page the only inputs are Indeed's own "What" and "Where" search boxes,
 * which produced all three reported symptoms at once:
 *
 *   - "Could not read this form's 2 field(s)" (the two search boxes),
 *   - those two saved into the user's Answers page as questions to answer,
 *   - and "no fields require autofill" from the preview about the same page,
 *     because an unclassifiable field looks like "nothing to do" to the planner
 *     and "unreadable form" to the fill path.
 *
 * The existing adapters test claimed to cover this ("every adapter scopes filling
 * to a form root") but its loop skipped any URL that did not classify as
 * `application-form` - which is every board listing page. It therefore only ever
 * checked the ATS adapters, which already worked.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { findApplicationForm, resolveFormRoot } from '@/lib/application-form';
import { boardAdapters } from '@/adapters/boards';
import { atsAdapters } from '@/adapters/ats';
import { genericAdapter } from '@/adapters/registry';

/**
 * jsdom gives every element a zero-size bounding rect, and the detector correctly
 * ignores invisible fields - a hidden form must not count as an application. So
 * layout is stubbed here rather than weakening the production check, which is the
 * part that keeps a `display:none` template form from being filled on a real page.
 */
beforeEach(() => {
  document.body.innerHTML = '';
  Element.prototype.getBoundingClientRect = function (): DOMRect {
    return { width: 120, height: 24, top: 0, left: 0, right: 120, bottom: 24, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  };
});

describe('every adapter scopes autofill', () => {
  it('all board adapters expose a formRoot', () => {
    // Unconditional on purpose: the previous version of this check skipped any
    // adapter whose URL did not classify as an application form, which silently
    // excluded all ten boards - the exact ones that were broken.
    for (const adapter of boardAdapters) {
      expect(typeof adapter.formRoot, `${adapter.id} has no formRoot`).toBe('function');
    }
  });

  it('all ATS adapters expose a formRoot', () => {
    for (const adapter of atsAdapters) {
      expect(typeof adapter.formRoot, `${adapter.id} has no formRoot`).toBe('function');
    }
  });

  it('the generic adapter scopes too', () => {
    expect(typeof genericAdapter.formRoot).toBe('function');
  });
});

describe('a job listing page is not an application form', () => {
  it('returns null for an Indeed-style listing with only search boxes', () => {
    // This is the reported page, reduced: a job description plus Indeed's own
    // two-field search. Nothing here should ever be typed into.
    document.body.innerHTML = `
      <header>
        <form role="search" class="jobsearch-SerpJobCard">
          <input id="text-input-what" name="q" placeholder="Job title, keywords, or company" />
          <input id="text-input-where" name="l" placeholder="Where" />
        </form>
      </header>
      <main>
        <h1>Backend Engineer (India)</h1>
        <div id="jobDescriptionText">We are hiring a backend engineer...</div>
      </main>
    `;
    expect(findApplicationForm()).toBeNull();
  });

  it('reports the listing page as "not an application form" rather than failing', () => {
    document.body.innerHTML = `
      <form role="search"><input name="q" /><input name="l" /></form>
    `;
    const scope = resolveFormRoot(findApplicationForm());
    expect(scope.isApplicationForm).toBe(false);
  });

  it('ignores a lone newsletter email box', () => {
    // One email input is not an application. Treating it as one would type the
    // user's address into a marketing signup.
    document.body.innerHTML = `
      <footer><form class="newsletter"><input type="email" name="email" /></form></footer>
    `;
    expect(findApplicationForm()).toBeNull();
  });

  it('ignores a login form', () => {
    document.body.innerHTML = `
      <form id="signin"><input type="email" name="email" /><input type="password" name="password" /></form>
    `;
    expect(findApplicationForm()).toBeNull();
  });
});

describe('a real application form is found', () => {
  it('finds a form with identity fields and excludes the site search', () => {
    document.body.innerHTML = `
      <header><form role="search"><input name="q" /></form></header>
      <form id="application">
        <input name="first_name" />
        <input type="email" name="email" />
        <input name="phone" />
      </form>
    `;
    const root = findApplicationForm() as ParentNode;
    expect(root).toBeTruthy();
    expect(root.querySelector('[name="q"]')).toBeNull();
    expect(root.querySelector('[name="email"]')).not.toBeNull();
  });

  it('a resume upload alone is enough evidence', () => {
    // Nothing except an application asks you to attach a CV, so one file input
    // qualifies even before any text field is recognised.
    document.body.innerHTML = `
      <form id="apply-form">
        <input type="file" name="resume" accept=".pdf,.doc" />
        <input name="mystery_field_we_do_not_know" />
      </form>
    `;
    expect(findApplicationForm()).toBeTruthy();
  });

  it('prefers the inner form over a wrapping container', () => {
    // A page-level wrapper would drag the search box back into scope.
    document.body.innerHTML = `
      <div id="application-wrapper">
        <input name="q" placeholder="search" />
        <form id="real"><input name="first_name" /><input type="email" name="email" /></form>
      </div>
    `;
    const root = findApplicationForm() as Element;
    expect(root.id).toBe('real');
  });

  it('finds a form that names itself an application even with odd fields', () => {
    document.body.innerHTML = `
      <form class="application-form">
        <input name="candidate_reference_code" />
      </form>
    `;
    expect(findApplicationForm()).toBeTruthy();
  });
});

describe('resolveFormRoot', () => {
  it('trusts an adapter that scoped itself', () => {
    document.body.innerHTML = `<form id="given"><input name="x" /></form>`;
    const given = document.querySelector('#given') as ParentNode;
    const scope = resolveFormRoot(given);
    expect(scope.isApplicationForm).toBe(true);
    expect(scope.root).toBe(given);
  });

  it('falls back to detection when the adapter gives nothing', () => {
    document.body.innerHTML = `
      <form id="app"><input name="first_name" /><input type="email" name="email" /></form>
    `;
    const scope = resolveFormRoot(null);
    expect(scope.isApplicationForm).toBe(true);
    expect((scope.root as Element).id).toBe('app');
  });

  it('still returns a usable root when there is no form, but flags it', () => {
    // Callers read the page for job extraction even on a listing, so the root has
    // to stay usable - the flag is what stops them filling it.
    document.body.innerHTML = `<main><h1>A job</h1></main>`;
    const scope = resolveFormRoot(null);
    expect(scope.isApplicationForm).toBe(false);
    expect(scope.root).toBe(document);
  });
});
