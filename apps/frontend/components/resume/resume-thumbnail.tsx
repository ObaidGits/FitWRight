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
}: {
  resumeId: string;
  /** Only a processed resume has structured content worth rendering. */
  ready: boolean;
  className?: string;
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

  return (
    <div
      ref={ref}
      aria-hidden="true"
      className={`relative shrink-0 overflow-hidden rounded-[var(--radius-at-sm)] border border-[var(--border)] bg-white ${className ?? ''}`}
      style={{ width: THUMB_WIDTH, height: Math.round(THUMB_WIDTH * 1.414) }}
    >
      {data ? (
        <div
          style={{
            width: A4_WIDTH_PX,
            transform: `scale(${SCALE})`,
            transformOrigin: 'top left',
            pointerEvents: 'none',
          }}
        >
          <Resume resumeData={stripHiddenItems(data)} template={template} />
        </div>
      ) : (
        // Placeholder that still reads as a document, so the row height never
        // jumps when the real thumbnail arrives.
        <div className="flex h-full w-full items-center justify-center bg-[var(--at-surface-2)]">
          <FileText className="h-5 w-5 text-[var(--muted-foreground)]" />
        </div>
      )}
    </div>
  );
}
