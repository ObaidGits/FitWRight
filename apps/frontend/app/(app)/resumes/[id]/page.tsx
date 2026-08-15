import { redirect } from 'next/navigation';

/**
 * Retired route - the app now has ONE resume editor.
 *
 * This used to be a second, separate editor. Having two was not just untidy:
 * they disagreed about where a resume's appearance was stored, and the builder's
 * copy silently discarded formatting work (see lib/resume/appearance-storage.ts).
 * Everything this page could do the builder now does, in named modes:
 *
 *   content editing        -> Content mode (ResumeForm - richer: rich text,
 *                             drag-and-drop reorder, every field)
 *   appearance + template  -> Design mode (FormattingControls + the full
 *                             template catalogue, ported here)
 *   version history        -> builder header (ported here)
 *   cover letter/outreach  -> their own modes (full editors, not summary cards)
 *   interview prep + PDF   -> Interview Prep mode (export ported here)
 *   JD match               -> JD Match mode
 *   custom sections        -> Content mode (AddSectionButton + SectionHeader)
 *   unsaved-changes guard  -> ported to the builder, which only had a
 *                             `beforeunload` handler and so did not catch an
 *                             in-app click on the sidebar
 *
 * Kept as a redirect rather than deleted because this URL is bookmarkable and is
 * still referenced by resume notifications created before the merge. A permanent
 * redirect is wrong here - it would be cached by the browser and outlive any
 * future change - so this is the default temporary one.
 */
export default async function RetiredResumeEditorPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/builder?id=${encodeURIComponent(id)}`);
}
