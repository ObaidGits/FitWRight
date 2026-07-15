'use client';

/**
 * <ResumeDocument> — the WYSIWYG page surface for the in-app preview.
 *
 * This is the single on-screen representation of the exported PDF. It renders
 * the SAME canonical `Resume` renderer the print/PDF path uses, at the EXACT
 * PDF content-box geometry (page size in CSS px at 96 DPI, margins applied as
 * the page padding), so line wrapping, margins, and horizontal layout match the
 * download pixel-for-pixel. Content is then laid across real A4/Letter page
 * surfaces with visible page boundaries — never an infinite scroll — and the
 * whole canvas is scaled to fit its container (we scale the canvas, never
 * reflow the document, so the layout is identical at any zoom).
 *
 * Pagination mirrors the PDF's break rules: content is split between measured
 * flow blocks (items / sections), never through one (`break-inside: avoid`),
 * and section titles stay with their first item (`break-after: avoid`). The
 * geometry + break math live in the pure, unit-tested `lib/resume/pagination`.
 *
 * Fidelity note: item-level breaks are exact for single-column templates; for
 * two-column templates the columns are laid side by side, so break positions
 * are a close approximation (the width/margins/scaling remain exact).
 */
import * as React from 'react';

import Resume, { type ResumeData } from '@/components/dashboard/resume-component';
import { type TemplateSettings, DEFAULT_TEMPLATE_SETTINGS } from '@/lib/types/template-settings';
import {
  type FlowBlock,
  computePageOffsets,
  contentWidthPx,
  fitScale,
  mmToPx,
  pageContentHeightPx,
  pageHeightPx,
  pageWidthPx,
} from '@/lib/resume/pagination';
import baseStyles from './styles/_base.module.css';

/** Unscaled gap between stacked page surfaces (px). */
const PAGE_GAP = 24;

/** Build a class selector from a (possibly undefined/hashed) CSS-module name. */
function classSel(name: string | undefined): string | null {
  if (!name) return null;
  const esc = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(name) : name;
  return `.${esc}`;
}

function measureFlowBlocks(root: HTMLElement): FlowBlock[] {
  const sectionSel = classSel(baseStyles['resume-section']);
  const headerSel = classSel(baseStyles['resume-header']);
  const titleSel = classSel(baseStyles['resume-section-title']);
  const titleSmSel = classSel(baseStyles['resume-section-title-sm']);
  const itemSel = classSel(baseStyles['resume-item']);
  const originTop = root.getBoundingClientRect().top;
  const rect = (el: Element): FlowBlock => {
    const r = el.getBoundingClientRect();
    return { top: r.top - originTop, bottom: r.bottom - originTop };
  };

  const blocks: FlowBlock[] = [];
  // The header (name/contact) is atomic and always first.
  const header = headerSel ? root.querySelector(headerSel) : null;
  if (header) blocks.push(rect(header));

  const sections = sectionSel ? Array.from(root.querySelectorAll(sectionSel)) : [];
  for (const section of sections) {
    const title =
      (titleSel && section.querySelector(titleSel)) ||
      (titleSmSel && section.querySelector(titleSmSel)) ||
      null;
    const items = itemSel ? Array.from(section.querySelectorAll(itemSel)) : [];
    if (items.length > 0) {
      // Item-based section: title stays with its first item; items never split.
      if (title) blocks.push({ ...rect(title), keepWithNext: true });
      for (const item of items) blocks.push(rect(item));
    } else {
      // Title-only / prose section (summary, skills, custom): atomic.
      blocks.push(rect(section));
    }
  }
  // Fallback: nothing recognized (unknown template) → one block spanning all.
  if (blocks.length === 0) {
    const r = root.getBoundingClientRect();
    blocks.push({ top: 0, bottom: r.height });
  }
  return blocks.sort((a, b) => a.top - b.top || a.bottom - b.bottom);
}

export interface ResumeDocumentProps {
  data: ResumeData;
  settings?: TemplateSettings;
  className?: string;
  /** Cap the number of rendered pages (e.g. `1` for a gallery thumbnail). */
  maxPages?: number;
}

export function ResumeDocument({
  data,
  settings = DEFAULT_TEMPLATE_SETTINGS,
  className,
  maxPages,
}: ResumeDocumentProps) {
  const measureRef = React.useRef<HTMLDivElement>(null);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [offsets, setOffsets] = React.useState<number[]>([0]);
  const [scale, setScale] = React.useState(1);

  const pageSize = settings.pageSize;
  const margins = settings.margins;

  // Geometry (px at 96 DPI — the unit Chromium's PDF layout uses).
  const pageW = pageWidthPx(pageSize);
  const pageH = pageHeightPx(pageSize);
  const contentW = contentWidthPx(pageSize, margins);
  const contentH = pageContentHeightPx(pageSize, margins);
  const marginTopPx = mmToPx(margins.top);
  const marginLeftPx = mmToPx(margins.left);
  const marginRightPx = mmToPx(margins.right);

  // The inner Resume renders with margins zeroed; the page surface supplies the
  // margins as padding (exactly as Playwright applies them to the real PDF).
  const innerSettings: TemplateSettings = React.useMemo(
    () => ({ ...settings, margins: { top: 0, bottom: 0, left: 0, right: 0 } }),
    [settings]
  );

  // Re-measure page breaks whenever the content, template, or geometry changes.
  const measureKey = React.useMemo(
    () => JSON.stringify({ data, s: innerSettings, contentW, contentH }),
    [data, innerSettings, contentW, contentH]
  );

  React.useLayoutEffect(() => {
    const root = measureRef.current;
    if (!root) return;
    const blocks = measureFlowBlocks(root);
    const next = computePageOffsets(blocks, contentH);
    setOffsets((prev) =>
      prev.length === next.length && prev.every((v, i) => v === next[i]) ? prev : next
    );
    // measureKey captures the meaningful inputs; eslint-safe explicit dep.
  }, [measureKey, contentH]);

  // Fit-to-width: scale the whole canvas to the container (never upscale, never
  // reflow the document). A ResizeObserver keeps it correct on layout changes.
  React.useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const apply = () => {
      const avail = el.clientWidth;
      const next = fitScale(pageW, avail);
      setScale((prev) => (Math.abs(prev - next) < 0.001 ? prev : next));
    };
    apply();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, [pageW]);

  const shownOffsets = maxPages && maxPages > 0 ? offsets.slice(0, maxPages) : offsets;

  return (
    <div ref={containerRef} className={className} data-testid="resume-document">
      {/* Hidden measurement copy at the exact content width (drives pagination). */}
      <div
        aria-hidden
        style={{
          position: 'absolute',
          top: 0,
          left: -100000,
          width: contentW,
          visibility: 'hidden',
          pointerEvents: 'none',
        }}
      >
        <div ref={measureRef} className="resume-scope">
          <Resume resumeData={data} template={settings.template} settings={innerSettings} />
        </div>
      </div>

      {/* Visible, scaled page canvas.
          Fit-to-width uses CSS `zoom` — NOT `transform: scale()`. `zoom` performs
          a real layout scale, so glyphs are re-rasterised at the on-screen size
          and stay crisp at any factor. `transform: scale()` instead promotes the
          canvas to a GPU compositing layer that is rastered ONCE at the natural
          (fractional, 793.7px-wide) size and then bilinearly resampled by the
          GPU; at a fractional scale the glyph edges no longer land on the device
          pixel grid (blur), and because the sampled texture is re-projected every
          scroll/re-render frame the sub-pixel offset drifts (the "shaking").
          `zoom` also sizes the layout box for us, so no manual width*scale math.
          A non-zoomed flex parent centres the zoomed child at its scaled size. */}
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        <div className="resume-scope" style={{ zoom: scale, width: pageW }}>
          {shownOffsets.map((offset, i) => (
            <div
              key={i}
              data-testid="resume-page"
              aria-label={i === 0 ? `Page 1 of ${offsets.length}` : undefined}
              // Page 1's DOM already holds the COMPLETE document (visually
              // clipped to its slice), so pages 2+ are purely visual duplicates.
              // Mark them inert so assistive tech + keyboard nav read the resume
              // exactly once (no duplicated/focusable content).
              aria-hidden={i > 0 || undefined}
              inert={i > 0 || undefined}
              style={{
                position: 'relative',
                width: pageW,
                height: pageH,
                marginBottom: i < shownOffsets.length - 1 ? PAGE_GAP : 0,
                background: 'white',
                boxShadow: '0 1px 4px rgba(0,0,0,0.12), 0 0 0 1px rgba(0,0,0,0.06)',
                overflow: 'hidden',
              }}
            >
              {/* Content window = the printable area inside the page margins. */}
              <div
                style={{
                  position: 'absolute',
                  top: marginTopPx,
                  left: marginLeftPx,
                  right: marginRightPx,
                  height: contentH,
                  overflow: 'hidden',
                }}
              >
                <div style={{ transform: `translateY(${-offset}px)` }}>
                  <Resume resumeData={data} template={settings.template} settings={innerSettings} />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default ResumeDocument;
