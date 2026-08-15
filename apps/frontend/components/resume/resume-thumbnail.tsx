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

  // Fluid mode fills whatever width the card gives it, using container-query units so
  // the page scales in CSS with no measurement in JavaScript. The alternative - a
  // ResizeObserver feeding a state update - would re-render every card on every layout
  // change, and a grid of twenty of them is exactly where that gets expensive.
  const wrapperStyle: React.CSSProperties = fluid
    ? { containerType: 'inline-size', aspectRatio: '210 / 297' }
    : { width: THUMB_WIDTH, height: Math.round(THUMB_WIDTH * 1.414) };

  const pageStyle: React.CSSProperties = fluid
    ? {
        width: A4_WIDTH_PX,
        // 1cqw is 1% of the container's width, so this is "scale the A4 page down to
        // exactly the container width" and it stays correct at every breakpoint.
        transform: `scale(calc(100cqw / ${A4_WIDTH_PX}))`,
        transformOrigin: 'top left',
        pointerEvents: 'none',
      }
    : {
        width: A4_WIDTH_PX,
        transform: `scale(${SCALE})`,
        transformOrigin: 'top left',
        pointerEvents: 'none',
      };

  return (
    <div
      ref={ref}
      aria-hidden="true"
      className={`relative overflow-hidden rounded-[var(--radius-at-sm)] border border-[var(--border)] bg-white ${
        fluid ? 'w-full' : 'shrink-0'
      } ${className ?? ''}`}
      style={wrapperStyle}
    >
      {data ? (
        <div style={pageStyle}>
          <Resume resumeData={stripHiddenItems(data)} template={template} />
        </div>
      ) : (
        // Placeholder that still reads as a document, so the card height never
        // jumps when the real thumbnail arrives.
        <div className="flex h-full w-full items-center justify-center bg-[var(--at-surface-2)]">
          <FileText className={`text-[var(--muted-foreground)] ${fluid ? 'h-8 w-8' : 'h-5 w-5'}`} />
        </div>
      )}
    </div>
  );
}
