/**
 * Detect a login wall, so "nothing happened" can be replaced with "you are
 * signed out of this site".
 *
 * Why this matters more than it looks: when a board hides its results behind a
 * sign-in, harvesting returns zero rows and filling finds no form. Both look
 * exactly like a broken extension from the outside, and a tool that fails
 * silently gets uninstalled - the user has no way to know the fix is one click
 * of "Sign in" on a tab they already have open.
 *
 * The hard part is not detecting a login page, it is NOT crying wolf. Nearly
 * every job board renders a "Sign in" link in its header while you are signed
 * in, and half of them keep a hidden login modal in the DOM at all times. So
 * only two signals are trusted here, both of which are near-impossible on a
 * signed-in results page:
 *
 *   1. the URL is an authentication page - which is what a redirect leaves
 *      behind, and the single most reliable signal there is;
 *   2. a *visible* password field - you are not asked for a password on a page
 *      that already knows who you are.
 *
 * A header link, the word "login" in body text, and hidden markup are all
 * deliberately ignored. This function answering "no" is not proof the user is
 * signed in; it only means we have no honest evidence they are not, and callers
 * must not turn a "no" into a positive claim.
 */

/** Path shapes that mean "you were sent to authenticate". */
const AUTH_URL = /(^|\/)(login|signin|sign-in|log-in|auth|account\/login|sessions\/new)(\/|$)/i;

/**
 * Is this element actually on screen? `offsetParent` is null for anything
 * `display:none` or detached, which is how the always-present login modals
 * these sites ship are excluded.
 */
function isVisible(el: HTMLElement): boolean {
  if (el.offsetParent === null) return false;
  const rect = el.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return false;
  const style = getComputedStyle(el);
  return style.visibility !== 'hidden' && style.opacity !== '0';
}

/** Does the page ask for a password right now? */
export function hasVisiblePasswordField(root: ParentNode = document): boolean {
  const fields = Array.from(root.querySelectorAll<HTMLInputElement>('input[type="password"]'));
  return fields.some(isVisible);
}

/**
 * Does this URL look like somewhere a site sends you to authenticate?
 *
 * Path only. A `?next=` or `?returnUrl=` parameter is tempting as a second
 * signal, but plenty of signed-in pages carry one, and every auth route worth
 * catching already says so in its path.
 */
export function isAuthUrl(url: URL): boolean {
  return AUTH_URL.test(url.pathname);
}

/**
 * Best evidence that the user is signed out of the current site.
 *
 * Returns `false` when unsure. Callers should treat `true` as "tell the user to
 * sign in" and `false` as "no idea", never as "signed in".
 */
export function looksSignedOut(url: URL = new URL(location.href)): boolean {
  return isAuthUrl(url) || hasVisiblePasswordField();
}

/** Why a page yielded nothing, in the order of how much we can say for sure. */
export type EmptyReason = 'signed-out' | 'empty';

/**
 * Classify an empty harvest. Separated from the message text so the content
 * script stays a DOM reader and the wording lives with the UI.
 */
export function classifyEmpty(url: URL = new URL(location.href)): EmptyReason {
  return looksSignedOut(url) ? 'signed-out' : 'empty';
}
