/**
 * Application submission detection.
 *
 * There is no reliable signal that an application was submitted - ATS platforms
 * variously redirect, swap the DOM in place, or show a modal. So this watches
 * three independent signals and treats any one as a submit:
 *
 *  1. A `submit` event on a form that contained the application fields.
 *  2. A click on a button whose text is an apply/submit verb.
 *  3. A confirmation phrase appearing in the DOM afterwards.
 *
 * Signal 3 is the confirming one: 1 and 2 mean "the user tried", 3 means "it
 * worked". We report on 3 when we see it, and fall back to 1/2 after a delay so
 * a silent success is still tracked.
 *
 * That fallback used to fire unconditionally, on the reasoning that over-reporting
 * was the safer failure - a job wrongly marked applied being a one-click fix while
 * a missed one rots in the feed. That reasoning was sound for the product it was
 * written in and is no longer: a false "applied" now suppresses the duplicate
 * guard's advice to re-apply, and is counted as sent by the reply-rate view,
 * corrupting the one number that exists to tell the user which resume works.
 *
 * So the fallback now looks for evidence it FAILED before reporting - a form still
 * sitting there with invalid fields, or a validation message - and cancels instead.
 * Cancelling also re-arms: the user fixes the error and submits again, and that
 * second attempt is watched like the first. Previously a retry could never report
 * at all, because the one-shot guard had already been spent on the failed try.
 */
import { clean } from '@/lib/dom';

/** Button labels that mean "send the application". */
const SUBMIT_VERBS =
  /^(submit|submit application|apply|apply now|send application|finish|complete application|i'm interested|easy apply)$/i;

/** Phrases that appear after a successful submission. */
const CONFIRMATION =
  /(application (was )?(submitted|received|sent|complete)|thank you for (applying|your application)|we('| ha)ve received your application|your application is on its way|successfully applied|applied successfully)/i;

/**
 * Phrases a form shows when it refused the submission.
 *
 * Kept narrow on purpose. A form that merely *mentions* required fields ("fields
 * marked * are required") is not an error, so this matches the language of a
 * rejection rather than of instruction.
 */
const VALIDATION_ERROR =
  /(please (enter|select|complete|provide|fill)|is required\b|required field|cannot be (empty|blank)|invalid (email|phone|date|format)|fix the (errors?|following)|there (was|were) (an? )?(error|problems?)|not a valid)/i;

export interface TrackingOptions {
  /** Called once when submission is believed to have happened. */
  onSubmitted: () => void;
}

/**
 * Is there positive evidence the submission was rejected?
 *
 * Two independent signals, either of which is enough:
 *
 *  1. A field the browser or the site marks invalid. `:invalid` covers native
 *     constraint validation; `aria-invalid` covers the custom widgets these
 *     platforms build instead of using it.
 *  2. Visible text that reads like a rejection, near the form rather than
 *     anywhere on the page - a site-wide cookie banner mentioning "required"
 *     must not look like a validation failure.
 *
 * Returns false when unsure, because the cost of a wrong "it failed" is a missed
 * application record, and the cost of a wrong "it succeeded" is a corrupted
 * pipeline plus corrupted metrics.
 */
/**
 * Readable text from an element.
 *
 * `innerText` is the right property - it respects what is actually visible - but it
 * is a rendering-dependent nicety, absent in some environments and undefined on a
 * detached node. The previous code called `.slice()` on it directly, which throws
 * and aborts tracking setup entirely rather than degrading. `textContent` is the
 * honest fallback: it includes hidden text, which risks a false positive, so it is
 * only ever the second choice.
 */
function readableText(el: HTMLElement | null): string {
  if (!el) return '';
  return el.innerText ?? el.textContent ?? '';
}

function looksRejected(): boolean {
  const invalid = document.querySelector(
    'form :invalid, form [aria-invalid="true"], [role="alert"]',
  );
  if (invalid instanceof HTMLElement) {
    // A hidden invalid field is not shown to the user and cannot be what
    // stopped them - wizards keep earlier steps mounted and empty.
    const visible = invalid.offsetParent !== null || invalid.getClientRects().length > 0;
    if (visible) return true;
  }

  const form = document.querySelector('form');
  if (!form) return false;
  const text = readableText(form as HTMLElement);
  return text.length < 20000 && VALIDATION_ERROR.test(text);
}

/**
 * Start watching the page. Returns a teardown function.
 */
export function watchForSubmission(options: TrackingOptions): () => void {
  let reported = false;
  let attempted = false;
  let fallbackTimer: number | undefined;

  const report = (): void => {
    if (reported) return;
    reported = true;
    teardown();
    options.onSubmitted();
  };

  /**
   * The delayed report. Checks for a rejection first: a form still on screen with
   * an invalid field is the signature of a submission that did not happen.
   */
  const reportUnlessRejected = (): void => {
    if (reported) return;
    if (looksRejected()) {
      // Not submitted. Re-arm so a corrected retry is watched properly instead of
      // being ignored because the first try spent the one-shot guard.
      attempted = false;
      fallbackTimer = undefined;
      return;
    }
    report();
  };

  /** Record an attempt and arm a delayed report if no confirmation arrives. */
  const noteAttempt = (): void => {
    if (attempted || reported) return;
    attempted = true;
    // Long enough for a redirect or async POST to land and produce a real
    // confirmation, short enough that the user has not navigated away.
    fallbackTimer = window.setTimeout(reportUnlessRejected, 6000);
  };

  const onSubmit = (event: Event): void => {
    const form = event.target as HTMLElement | null;
    // Ignore search boxes and newsletter forms: only a form carrying a file
    // input or several text fields is plausibly an application.
    if (form instanceof HTMLFormElement && looksLikeApplicationForm(form)) noteAttempt();
  };

  const onClick = (event: Event): void => {
    const target = event.target as HTMLElement | null;
    const button = target?.closest('button, input[type="submit"], a[role="button"], [role="button"]');
    if (!button) return;
    const label = clean(
      (button as HTMLElement).innerText ||
        button.getAttribute('value') ||
        button.getAttribute('aria-label') ||
        '',
    );
    if (SUBMIT_VERBS.test(label)) noteAttempt();
  };

  document.addEventListener('submit', onSubmit, true);
  document.addEventListener('click', onClick, true);

  // Watch for a confirmation message appearing anywhere in the page.
  const observer = new MutationObserver((records) => {
    if (reported) return;
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (!(node instanceof HTMLElement)) continue;
        const text = readableText(node);
        if (text && text.length < 4000 && CONFIRMATION.test(text)) {
          report();
          return;
        }
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });

  // The page may already be a confirmation page (post-redirect).
  if (CONFIRMATION.test(readableText(document.body).slice(0, 8000))) {
    // Defer so the caller finishes wiring before the callback fires.
    window.setTimeout(report, 0);
  }

  function teardown(): void {
    document.removeEventListener('submit', onSubmit, true);
    document.removeEventListener('click', onClick, true);
    observer.disconnect();
    if (fallbackTimer !== undefined) window.clearTimeout(fallbackTimer);
  }

  return teardown;
}

/** Heuristic: is this form an application rather than a search box? */
function looksLikeApplicationForm(form: HTMLFormElement): boolean {
  if (form.querySelector('input[type="file"]')) return true;
  const textFields = form.querySelectorAll(
    'input[type="text"], input[type="email"], input[type="tel"], textarea',
  );
  return textFields.length >= 3;
}
