/**
 * How wide a page's content column is allowed to be.
 *
 * These were previously ad-hoc per page: `max-w-2xl`, `3xl`, `4xl`, `5xl`, `6xl`,
 * `lg` and `[1500px]` were all in use, so the content column jumped around as
 * the user moved between pages - a large part of why individually-fine pages
 * still felt unrelated to each other. Worse, the shell caps content at `6xl`
 * (1152px), so Tailor's `max-w-[1500px]` was silently dead: it had been asking
 * to be wider than the shell would ever allow.
 *
 * Three tiers, chosen by what the content needs rather than by page:
 *
 *  - `NARROW`  one short column, read top-to-bottom. Onboarding, upload, a
 *              blocked-state card. Long measure hurts readability here.
 *  - `CONTENT` reading and list surfaces - agenda, answers, settings.
 *  - `WIDE`    dense or multi-column surfaces. This is the shell default, so a
 *              page in this tier declares nothing.
 *
 * Editor surfaces (`/builder`, `/tailor`) are handled by the shell instead - see
 * `EDITOR_ROUTES` in components/layout/app-shell.tsx. They need more room than
 * the reading-width column, and constraining them is what made the two-pane
 * layouts cramped.
 */
export const PAGE_WIDTH = {
  NARROW: 'mx-auto w-full max-w-2xl',
  CONTENT: 'mx-auto w-full max-w-4xl',
  /** The shell already applies this; a WIDE page adds nothing. */
  WIDE: '',
} as const;
