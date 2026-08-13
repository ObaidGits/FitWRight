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
 * a silent success is still tracked. Over-reporting is the right failure mode
 * here - a job wrongly marked applied is a one-click fix in the pipeline, while
 * a missed one silently rots in the feed.
 */
import { clean } from '@/lib/dom';

/** Button labels that mean "send the application". */
const SUBMIT_VERBS =
  /^(submit|submit application|apply|apply now|send application|finish|complete application|i'm interested|easy apply)$/i;

/** Phrases that appear after a successful submission. */
const CONFIRMATION =
  /(application (was )?(submitted|received|sent|complete)|thank you for (applying|your application)|we('| ha)ve received your application|your application is on its way|successfully applied|applied successfully)/i;

export interface TrackingOptions {
  /** Called once when submission is believed to have happened. */
  onSubmitted: () => void;
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

  /** Record an attempt and arm a delayed report if no confirmation arrives. */
  const noteAttempt = (): void => {
    if (attempted || reported) return;
    attempted = true;
    // Long enough for a redirect or async POST to land and produce a real
    // confirmation, short enough that the user has not navigated away.
    fallbackTimer = window.setTimeout(report, 6000);
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
        const text = node.innerText;
        if (text && text.length < 4000 && CONFIRMATION.test(text)) {
          report();
          return;
        }
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });

  // The page may already be a confirmation page (post-redirect).
  if (CONFIRMATION.test(document.body.innerText.slice(0, 8000))) {
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
