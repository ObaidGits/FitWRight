/**
 * Translatable strings.
 *
 * Chrome's own i18n, not a bundled library: the catalogue lives in
 * `public/_locales/<lang>/messages.json`, the browser picks the locale from the
 * user's own settings, and the manifest's own name and description become
 * translatable too - which no runtime library can do.
 *
 * English is the complete locale and the fallback. Adding a language is dropping in
 * one file beside it; no code changes. Deliberately shipped with English only
 * rather than machine translations nobody on this project can verify - a plausible
 * but wrong translation of "Nothing is submitted" would be worse than English.
 *
 * `t()` falls back to the key's English default when a message is missing, so a
 * partially translated locale degrades to English per string instead of rendering
 * blanks.
 */

/** Message keys, so a typo is a compile error rather than an empty string. */
export type MessageKey =
  | 'panelLabel'
  | 'panelHide'
  | 'panelNotHere'
  | 'panelNotHereTitle'
  | 'panelDragTitle'
  | 'panelJumpHelp'
  | 'panelNothingOutstanding'
  | 'panelSaveAnswers'
  | 'panelNeverSubmits'
  | 'badgeLabel'
  | 'badgeHide'
  | 'badgeMatchUnavailable'
  | 'toastSignInFirst'
  | 'toastSaved'
  | 'toastAlreadySaved'
  | 'toastTailoredResumeAttached'
  | 'toastMasterResumeAttached'
  | 'toastMarkedApplied'
  | 'toastNothingNewOnStep';

/**
 * English text for every key.
 *
 * Duplicated from the catalogue on purpose: it is the fallback when
 * `chrome.i18n` is unavailable (a unit test, a stripped environment) and it keeps
 * the strings readable at the call site during review.
 */
const FALLBACK: Record<MessageKey, string> = {
  panelLabel: 'FitWright autofill summary',
  panelHide: 'Hide the FitWright panel',
  panelNotHere: 'Not here',
  panelNotHereTitle: 'Do not show this panel on this site again',
  panelDragTitle: 'Drag to move',
  panelJumpHelp:
    'Click a question to jump to it. Answer them here, then save so the next form fills itself.',
  panelNothingOutstanding: 'Nothing left unanswered on this step.',
  panelSaveAnswers: 'Save my answers to FitWright',
  panelNeverSubmits: "Nothing is submitted. Review, then press the employer's submit button.",
  badgeLabel: 'FitWright resume match',
  badgeHide: 'Hide the FitWright match badge',
  badgeMatchUnavailable: 'Match unavailable - add a parsed resume in FitWright.',
  // `$1` is the site name, substituted by chrome.i18n.
  toastSignInFirst: 'Sign in to $1, then run this again',
  toastSaved: 'Saved to FitWright',
  toastAlreadySaved: 'Already saved',
  toastTailoredResumeAttached: 'tailored resume attached',
  toastMasterResumeAttached: 'master resume attached',
  toastMarkedApplied: 'Marked as applied in FitWright',
  toastNothingNewOnStep: 'Nothing new to fill on this step',
};

/** Look up a message in the user's locale, falling back to English. */
export function t(key: MessageKey, substitutions?: string[]): string {
  try {
    const message = chrome.i18n?.getMessage(key, substitutions);
    if (message) return message;
  } catch {
    /* i18n unavailable - fall through */
  }
  // Substitute into the fallback as well, or a missing catalogue would render a
  // literal "$1" to the user.
  let text = FALLBACK[key];
  (substitutions ?? []).forEach((value, index) => {
    text = text.replace(new RegExp(`\\$${index + 1}`, 'g'), value);
  });
  return text;
}
