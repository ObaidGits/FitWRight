/**
 * The builder's workspace modes.
 *
 * Extracted from the builder component so the routing can be tested without
 * mounting the whole editor tree.
 *
 * `resume` is the CONTENT mode and keeps that id for URL compatibility: it is
 * linked as `?tab=resume` from elsewhere, and an unknown tab falls back silently
 * rather than erroring, so renaming it would break those links invisibly.
 *
 * `design` is separate from `resume` because the appearance controls used to sit
 * stacked above the content form in the same scrolling column - a user editing a
 * bullet scrolled past margins and font sizes, and a user looking for a margin
 * had no reason to know it was in there. The resume editor deep-links into this
 * mode for "Fine-grained formatting".
 */
export type TabId =
  | 'resume'
  | 'design'
  | 'cover-letter'
  | 'outreach'
  | 'interview-prep'
  | 'jd-match';

/** Content and design lead: they are the two everyday modes. */
export const TAB_IDS: TabId[] = [
  'resume',
  'design',
  'cover-letter',
  'outreach',
  'interview-prep',
  'jd-match',
];

export const getTabFromSearchParams = (searchParams: Pick<URLSearchParams, 'get'>): TabId => {
  const tab = searchParams.get('tab');
  return TAB_IDS.includes(tab as TabId) ? (tab as TabId) : 'resume';
};
