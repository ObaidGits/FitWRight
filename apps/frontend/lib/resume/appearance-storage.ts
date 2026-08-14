/**
 * Where a resume's appearance (template + margins/fonts/spacing/colour) lives.
 *
 * This exists because the two editors disagreed about it, and the disagreement
 * lost user work. The resume editor stored appearance per resume on the server;
 * the builder stored it under a single global browser key and never wrote it to
 * the resume at all. So a margin change made in the builder vanished on another
 * device AND leaked onto every other resume in this one, while a template chosen
 * in the resume editor was ignored the moment the builder opened.
 *
 * The contract both editors now share:
 *
 *  1. The backend `template_settings` column is the SOURCE OF TRUTH. It survives
 *     devices, export and duplication.
 *  2. localStorage is only a per-resume cache, used until the fetch resolves.
 *  3. Appearance never bumps the content version, so persisting it can be
 *     fire-and-forget and can never conflict with an in-flight content save.
 *  4. A server value is adopted at most once per resume, and never after the
 *     user has changed the appearance themselves in this session.
 *
 * Keeping the key in one place is the point: two editors computing
 * `fitwright-template-${id}` independently is how they drifted apart before.
 */

/** Cache key for a resume's appearance. */
export function templateSettingsCacheKey(resumeId: string): string {
  return `fitwright-template-${resumeId}`;
}

/**
 * Cache key for an editor session with no resume id yet (a brand-new resume that
 * has never been saved). Shared deliberately - there is only ever one of these,
 * and it is discarded as soon as the resume gets an id.
 */
export const UNSAVED_TEMPLATE_SETTINGS_KEY = 'resume_builder_settings';

/** Cache key for either state. */
export function appearanceStorageKey(resumeId: string | null | undefined): string {
  return resumeId ? templateSettingsCacheKey(resumeId) : UNSAVED_TEMPLATE_SETTINGS_KEY;
}
