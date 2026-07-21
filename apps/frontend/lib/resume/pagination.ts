/**
 * Resume page geometry + deterministic pagination (WYSIWYG engine).
 *
 * The in-app preview must lay content out at the EXACT same content-box width
 * the PDF uses, so line wrapping and horizontal layout match pixel-for-pixel.
 * The backend renders the PDF with headless Chromium's `page.pdf`, which lays
 * pages out in CSS pixels at 96 DPI (1in = 96px). Reproducing that geometry
 * here - page size in px, margins as the page padding, content width = page −
 * margins - is what makes the preview a true representation of the export.
 *
 * This module is intentionally PURE (no DOM, no React) so the geometry and the
 * page-break algorithm are unit-testable without a browser.
 */
import type { MarginSettings, PageSize } from '@/lib/types/template-settings';

/** CSS reference pixels per inch (the unit Chromium's PDF layout uses). */
export const CSS_DPI = 96;
export const MM_PER_INCH = 25.4;

/** Convert millimetres to CSS pixels at 96 DPI (Chromium's print layout unit). */
export function mmToPx(mm: number): number {
  return (mm * CSS_DPI) / MM_PER_INCH;
}

/** Physical page dimensions in millimetres (A4 / US Letter). */
export const PAGE_DIMS_MM: Record<PageSize, { width: number; height: number }> = {
  A4: { width: 210, height: 297 },
  LETTER: { width: 215.9, height: 279.4 },
};

export function pageWidthPx(pageSize: PageSize): number {
  return mmToPx(PAGE_DIMS_MM[pageSize].width);
}

export function pageHeightPx(pageSize: PageSize): number {
  return mmToPx(PAGE_DIMS_MM[pageSize].height);
}

/** The printable content-box width = page width − left − right margins. */
export function contentWidthPx(pageSize: PageSize, margins: MarginSettings): number {
  return mmToPx(PAGE_DIMS_MM[pageSize].width - margins.left - margins.right);
}

/** The printable content-box height per page = page height − top − bottom margins. */
export function pageContentHeightPx(pageSize: PageSize, margins: MarginSettings): number {
  return mmToPx(PAGE_DIMS_MM[pageSize].height - margins.top - margins.bottom);
}

/** A measured flow block in content-region coordinates (px from content top). */
export interface FlowBlock {
  top: number;
  bottom: number;
  /**
   * Keep this block on the same page as the one that follows it (e.g. a section
   * title must not be orphaned at the bottom of a page - it stays with its
   * first item). Mirrors CSS `break-after: avoid`.
   */
  keepWithNext?: boolean;
}

// Sub-pixel measurement noise shouldn't force spurious page breaks.
const EPS = 1;

/**
 * Group blocks so a `keepWithNext` block is glued to the following block.
 *
 * The resulting group is atomic for pagination: a section title fused to its
 * first item can never be split, so the title is never orphaned (break-after:
 * avoid) and the item is never split (break-inside: avoid). Chained titles
 * (rare) glue transitively.
 */
export function groupFlowBlocks(blocks: FlowBlock[]): Array<{ top: number; bottom: number }> {
  const groups: Array<{ top: number; bottom: number }> = [];
  let i = 0;
  while (i < blocks.length) {
    const start = blocks[i];
    let end = start;
    // Absorb consecutive keep-with-next blocks into this group.
    while (blocks[i]?.keepWithNext && i + 1 < blocks.length) {
      i += 1;
      end = blocks[i];
    }
    groups.push({ top: start.top, bottom: end.bottom });
    i += 1;
  }
  return groups;
}

/**
 * Compute the content-region Y offset at which each page starts.
 *
 * Walks the (grouped) flow blocks in document order and opens a new page just
 * before any block whose bottom would overflow the current page's content box -
 * never splitting a block (break-inside: avoid). A block taller than a full
 * page is left to overflow (visually clipped) rather than looping forever, and
 * the following block opens its own page, matching a forced break.
 *
 * Returns page start offsets (always begins with `[0]`); the length is the page
 * count. Coordinates are px from the content-region top (i.e. inside margins).
 */
export function computePageOffsets(blocks: FlowBlock[], pageContentHeight: number): number[] {
  if (!(pageContentHeight > 0)) return [0];
  const groups = groupFlowBlocks(blocks);
  const offsets = [0];
  let pageStart = 0;
  for (const g of groups) {
    const overflows = g.bottom - pageStart > pageContentHeight + EPS;
    const hasRoomBefore = g.top - pageStart > EPS;
    if (overflows && hasRoomBefore) {
      pageStart = g.top;
      offsets.push(pageStart);
    }
    // else: it fits, or it's an oversized block already at the page top - leave
    // it (clipped); the next block that overflows will open the following page.
  }
  return offsets;
}

/** Total pages for a measured layout (never fewer than 1). */
export function pageCount(blocks: FlowBlock[], pageContentHeight: number): number {
  return computePageOffsets(blocks, pageContentHeight).length;
}

/** Fit-to-width scale for a page of `pageWidth` inside `availableWidth` (never upscales). */
export function fitScale(pageWidth: number, availableWidth: number): number {
  if (!(availableWidth > 0) || !(pageWidth > 0)) return 1;
  return Math.min(1, availableWidth / pageWidth);
}
