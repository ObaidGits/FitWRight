/**
 * Deciding whether this page actually has an application form.
 *
 * WHY THIS EXISTS
 *
 * Autofill used to fall back to the whole `document` whenever an adapter did not
 * scope it. The ATS adapters (Greenhouse, Lever, Workday, iCIMS...) all implement
 * `formRoot()`, but none of the ten JOB BOARD adapters did - so on Indeed,
 * LinkedIn, Naukri, Glassdoor and the rest, autofill read every input on the page.
 *
 * On a job listing page the only inputs are usually the site's own search boxes.
 * The result was a perfect storm of wrong: the extension reported "could not read
 * this form's 2 fields" (those two being Indeed's "What" and "Where" search
 * boxes), saved them into the user's Answers page as questions to answer, and the
 * preview said "no fields require autofill" about the same page - because a
 * search box classifies as nothing, which the preview reads as "nothing to do"
 * and the fill path reads as "unreadable form".
 *
 * So the fix is not better field matching. It is knowing the difference between
 *
 *   - a page with an application form,
 *   - a page with no application form at all, and
 *   - an application form whose fields we genuinely cannot read.
 *
 * Those are three different messages to a user, and only the third is a bug in
 * our adapters. Conflating them is what made a listing page look broken.
 *
 * EVIDENCE, NOT GUESSWORK
 *
 * A container qualifies only on positive evidence of an application: a resume
 * upload, or several identity fields together, or a form that names itself an
 * application. Anything inside search/nav/header/footer is rejected outright.
 * Erring towards `null` is deliberate - refusing to fill and saying why is much
 * better than typing a user's phone number into a site's search bar.
 */
import { isFillable } from '@/lib/dom';
import { classify } from '@/lib/fields';
import type { FieldKey } from '@/lib/fields';

/** Keys that, seen together, mean "this is asking who you are". */
const IDENTITY_KEYS: ReadonlySet<string> = new Set<FieldKey | string>([
  'full_name',
  'first_name',
  'last_name',
  'email',
  'phone',
]);

/** Regions that are never an application form, however many inputs they hold. */
const EXCLUDED_ANCESTORS = 'header, nav, footer, [role="search"], [role="navigation"], [role="banner"]';

/** Names/ids that mark an input as site search rather than an application field. */
const SEARCH_FIELD = /(^|[-_])(q|query|search|keyword|keywords|what|where|loc|location_?search)([-_]|$)/i;

/** A form that names itself an application, by any of the attributes sites use. */
const APPLICATION_FORM = /(appl(y|ication)|candidate|submission|jobseeker)/i;

/** A form that names itself search, which must never be filled. */
const SEARCH_FORM = /(search|filter|newsletter|subscribe|login|signin|sign-in)/i;

function attrs(el: Element): string {
  return [
    el.getAttribute('id'),
    el.getAttribute('name'),
    el.getAttribute('class'),
    el.getAttribute('action'),
    el.getAttribute('data-testid'),
    el.getAttribute('aria-label'),
  ]
    .filter(Boolean)
    .join(' ');
}

/** Inside a header/nav/search region, or itself a search input? */
function isExcluded(el: Element): boolean {
  if (el.closest(EXCLUDED_ANCESTORS)) return true;
  const name = `${el.getAttribute('name') ?? ''} ${el.getAttribute('id') ?? ''}`;
  if (SEARCH_FIELD.test(name)) return true;
  const form = el.closest('form');
  if (form && SEARCH_FORM.test(attrs(form)) && !APPLICATION_FORM.test(attrs(form))) {
    return true;
  }
  return false;
}

/** A file input that plausibly takes a CV. */
function hasResumeUpload(root: ParentNode): boolean {
  return Array.from(root.querySelectorAll<HTMLInputElement>('input[type="file"]')).some(
    (input) => {
      if (isExcluded(input)) return false;
      const hay = `${attrs(input)} ${input.getAttribute('accept') ?? ''}`;
      return /resume|cv|curriculum|attach|upload|document/i.test(hay) ||
        /pdf|doc/i.test(input.getAttribute('accept') ?? '');
    },
  );
}

/** How many distinct identity fields this container asks for. */
function identityFieldCount(root: ParentNode): number {
  const seen = new Set<string>();
  for (const el of root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
    'input, textarea, select',
  )) {
    if (isExcluded(el)) continue;
    if (!isFillable(el)) continue;
    const key = classify(el);
    if (key && IDENTITY_KEYS.has(key)) seen.add(key);
  }
  return seen.size;
}

/** Does this container hold enough evidence to be an application form? */
function isApplicationLike(root: Element): boolean {
  if (isExcluded(root)) return false;
  // A resume upload is the strongest single signal: nothing but an application
  // asks you to attach a CV.
  if (hasResumeUpload(root)) return true;
  // Two or more identity fields together (name + email, email + phone...). One
  // alone is not enough - a newsletter box asks for an email.
  if (identityFieldCount(root) >= 2) return true;
  // A form that says it is an application, and has something to fill.
  if (APPLICATION_FORM.test(attrs(root))) {
    return Array.from(root.querySelectorAll('input, textarea, select')).some(
      (el) => !isExcluded(el) && isFillable(el as HTMLInputElement),
    );
  }
  return false;
}

/**
 * The application form on this page, or `null` when there isn't one.
 *
 * `null` is a first-class answer, not a failure: it is what lets the caller say
 * "this is a job listing, open the application form first" instead of pretending
 * the page was unreadable.
 *
 * Prefers the SMALLEST qualifying container so the scope is tight - a page-level
 * `<main>` would drag the site's search box back in.
 */
export function findApplicationForm(root: ParentNode = document): ParentNode | null {
  const direct = findInDocument(root);
  if (direct) return direct;

  // Some ATS flows embed the real form in an iframe (Greenhouse and Workday
  // embeds, parts of Indeed's apply flow). Reaching into SAME-ORIGIN frames costs
  // nothing and finds those.
  //
  // Cross-origin frames are silently skipped - the browser forbids reading them,
  // and the honest consequence is that autofill reports "no application form"
  // rather than appearing to work. Running the content script in every frame
  // (`all_frames`) would reach them, but it also registers a second message
  // listener per frame, so one popup click would get answered by whichever frame
  // replied first. Not worth trading correct messaging for.
  if (root === document) {
    for (const frame of Array.from(document.querySelectorAll('iframe'))) {
      let doc: Document | null = null;
      try {
        doc = frame.contentDocument;
      } catch {
        continue; // cross-origin
      }
      if (!doc) continue;
      const found = findInDocument(doc);
      if (found) return found;
    }
  }
  return null;
}

function findInDocument(root: ParentNode): ParentNode | null {
  const candidates: Element[] = [
    ...Array.from(root.querySelectorAll<Element>('form')),
    // Modern ATS embeds frequently do not use a <form> at all, so these are
    // checked too - after real forms, which are the better answer when present.
    ...Array.from(
      root.querySelectorAll<Element>(
        '[class*="application" i], [id*="application" i], [data-testid*="application" i], ' +
          '[class*="apply" i], [id*="apply" i], [data-testid*="apply" i]',
      ),
    ),
  ];

  const qualifying = candidates.filter(isApplicationLike);
  if (!qualifying.length) return null;

  // Smallest by field count, so a wrapper never wins over the form inside it.
  qualifying.sort(
    (a, b) =>
      a.querySelectorAll('input, textarea, select').length -
      b.querySelectorAll('input, textarea, select').length,
  );
  return qualifying[0];
}

/**
 * What the caller should act on, and whether it is a real application form.
 *
 * Adapters that scope themselves (the ATS ones) are trusted as-is. Everything
 * else has to earn it, which is what stops a board's listing page from being
 * treated as a form.
 */
export function resolveFormRoot(adapterRoot: ParentNode | null | undefined): {
  root: ParentNode;
  isApplicationForm: boolean;
} {
  if (adapterRoot) return { root: adapterRoot, isApplicationForm: true };
  const found = findApplicationForm();
  if (found) return { root: found, isApplicationForm: true };
  // Returning `document` keeps callers simple - they can still read the page for
  // a job extraction - but the flag tells them not to fill it.
  return { root: document, isApplicationForm: false };
}
