'use client';

import * as React from 'react';
import FileText from 'lucide-react/dist/esm/icons/file-text';
import Resume, { type ResumeData } from '@/components/dashboard/resume-component';
import { useResume } from '@/features/resumes/hooks';
import { DEFAULT_TEMPLATE_SETTINGS } from '@/lib/types/template-settings';
import { stripHiddenItems } from '@/lib/utils/hidden-items';

/** Rendered width of the thumbnail in px. A4 is 210mm wide, so the scale below
 *  crops to roughly the top of page one - enough to recognise a document by its
 *  shape and header, which is the whole job. */
const THUMB_WIDTH = 96;
const A4_WIDTH_PX = 794; // 210mm at 96dpi, matching the preview's page maths
const SCALE = THUMB_WIDTH / A4_WIDTH_PX;

/**
 * A real preview of the resume, small.
 *
 * The library used to render every resume as an identical grey row with a
 * generic file icon, which is unusable once a user has a dozen tailored
 * variants with near-identical generated names. This renders the actual
 * document with its actual template, so they are told apart by sight.
 *
 * Two deliberate choices about cost:
 *  - the resume body is NOT in the list response, so content is fetched per
 *    card; the fetch is therefore deferred until the card is near the viewport
 *    (IntersectionObserver), so a long library does not fire N requests on load.
 *  - it renders the plain `Resume` renderer rather than the paginated preview:
 *    pagination measures content in a hidden copy, which is real work we do not
 *    need when the result is 96px wide and cropped.
 */
export function ResumeThumbnail({
  resumeId,
  ready,
  className,
  fluid = false,
}: {
  resumeId: string;
  /** Only a processed resume has structured content worth rendering. */
  ready: boolean;
  className?: string;
  /** Fill the container's width instead of the fixed 96px, for the grid view. */
  fluid?: boolean;
}) {
  const ref = React.useRef<HTMLDivElement | null>(null);
  const [inView, setInView] = React.useState(false);

  React.useEffect(() => {
    const el = ref.current;
    if (!el || inView) return;
    // rootMargin starts the fetch just before the card scrolls in, so the
    // thumbnail is usually already there by the time it is looked at.
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setInView(true);
      },
      { rootMargin: '200px' }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [inView]);

  const query = useResume(inView && ready ? resumeId : '');
  const data = query.data?.processed_resume as ResumeData | undefined;
  const template = query.data?.template_settings?.template ?? DEFAULT_TEMPLATE_SETTINGS.template;

  // MEASURED width, not a CSS container-query expression.
  //
  // The first attempt used `transform: scale(calc(100cqw / 794))`, which is INVALID -
  // `scale()` needs a plain number and dividing a length by a number yields a length.
  // The browser dropped the transform silently, so every page rendered at its full
  // 794px inside a ~200px card and was simply clipped: titles cut mid-word, right
  // margin gone. It looked like a cropping choice rather than a broken rule, which is
  // why nothing flagged it.
  //
  // A ResizeObserver costs one callback per card on mount and on resize, which is
  // cheaper than being wrong.
  const [boxWidth, setBoxWidth] = React.useState(0);

  React.useEffect(() => {
    if (!fluid) return;
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      // Only commit real changes: sub-pixel jitter during layout would otherwise
      // re-render the whole grid repeatedly.
      setBoxWidth((current) => (Math.abs(current - width) > 1 ? width : current));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [fluid]);

  const scale = fluid ? (boxWidth > 0 ? boxWidth / A4_WIDTH_PX : 0) : SCALE;

  const wrapperStyle: React.CSSProperties = fluid
    ? // Deliberately NOT the full A4 1:1.414 shape. A full page makes a very tall card
      // for content that is unreadable at this size anyway; this crops to the top of the
      // page - name, contact line, summary heading - which is what actually
      // distinguishes one tailored variant from another.
      { aspectRatio: '5 / 4' }
    : { width: THUMB_WIDTH, height: Math.round(THUMB_WIDTH * 1.414) };

  const pageStyle: React.CSSProperties = {
    width: A4_WIDTH_PX,
    transform: `scale(${scale})`,
    transformOrigin: 'top left',
    pointerEvents: 'none',
  };

  return (
    <div
      ref={ref}
      aria-hidden="true"
      className={`relative overflow-hidden bg-white ${
        fluid ? 'w-full' : 'shrink-0 rounded-[var(--radius-at-sm)] border border-[var(--border)]'
      } ${className ?? ''}`}
      style={wrapperStyle}
    >
      {/* Held back until the width is known: rendering at scale 0 then jumping to the
          real size is a visible flash on every card in the grid. */}
      {data && scale > 0 ? (
        <div style={pageStyle}>
          <Resume resumeData={stripHiddenItems(data)} template={template} />
        </div>
      ) : (
        // Placeholder that still reads as a document, so the card height never
        // jumps when the real thumbnail arrives.
        <div className="flex h-full w-full items-center justify-center bg-[var(--at-surface-2)]">
          <FileText className={`text-[var(--muted-foreground)] ${fluid ? 'h-7 w-7' : 'h-5 w-5'}`} />
        </div>
      )}
    </div>
  );
}
