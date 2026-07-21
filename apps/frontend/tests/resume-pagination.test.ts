import { describe, expect, it } from 'vitest';

import {
  CSS_DPI,
  computePageOffsets,
  contentWidthPx,
  fitScale,
  groupFlowBlocks,
  mmToPx,
  pageContentHeightPx,
  pageCount,
  pageHeightPx,
  pageWidthPx,
} from '@/lib/resume/pagination';
import type { MarginSettings } from '@/lib/types/template-settings';

const M = (v: number): MarginSettings => ({ top: v, bottom: v, left: v, right: v });

describe('page geometry (px @ 96 DPI - matches Chromium PDF layout)', () => {
  it('converts mm to CSS px at 96 DPI', () => {
    expect(mmToPx(25.4)).toBeCloseTo(CSS_DPI, 5); // 1 inch = 96px
    expect(mmToPx(0)).toBe(0);
  });

  it('sizes A4 and Letter pages correctly', () => {
    expect(pageWidthPx('A4')).toBeCloseTo(793.7, 1);
    expect(pageHeightPx('A4')).toBeCloseTo(1122.52, 1);
    expect(pageWidthPx('LETTER')).toBeCloseTo(816.0, 1);
    expect(pageHeightPx('LETTER')).toBeCloseTo(1056.0, 1);
  });

  it('derives the printable content box = page minus margins', () => {
    // A4 width 210mm − 10 − 10 = 190mm.
    expect(contentWidthPx('A4', M(10))).toBeCloseTo(mmToPx(190), 5);
    // A4 height 297mm − 10 − 10 = 277mm.
    expect(pageContentHeightPx('A4', M(10))).toBeCloseTo(mmToPx(277), 5);
  });
});

describe('fitScale (scale the canvas, never upscale)', () => {
  it('never upscales when the container is wider than the page', () => {
    expect(fitScale(800, 1200)).toBe(1);
  });
  it('scales down to fit a narrow container', () => {
    expect(fitScale(800, 400)).toBeCloseTo(0.5, 5);
  });
  it('defaults to 1 before the container is measured', () => {
    expect(fitScale(800, 0)).toBe(1);
  });
});

describe('groupFlowBlocks (break-after: avoid - titles glue to first item)', () => {
  it('fuses a keepWithNext block with the following block', () => {
    const groups = groupFlowBlocks([
      { top: 0, bottom: 10, keepWithNext: true },
      { top: 10, bottom: 50 },
      { top: 50, bottom: 90 },
    ]);
    expect(groups).toEqual([
      { top: 0, bottom: 50 },
      { top: 50, bottom: 90 },
    ]);
  });
});

describe('computePageOffsets (break-inside: avoid - never split a block)', () => {
  it('keeps everything on one page when it all fits', () => {
    expect(
      computePageOffsets(
        [
          { top: 0, bottom: 40 },
          { top: 40, bottom: 80 },
        ],
        100
      )
    ).toEqual([0]);
  });

  it('opens a new page before a block that would overflow', () => {
    // b3 (80->120) overflows a 100px page -> page 2 starts at its top (80).
    expect(
      computePageOffsets(
        [
          { top: 0, bottom: 40 },
          { top: 40, bottom: 80 },
          { top: 80, bottom: 120 },
          { top: 120, bottom: 160 },
        ],
        100
      )
    ).toEqual([0, 80]);
  });

  it('keeps a section title with its first item (no orphaned title)', () => {
    const title = { top: 80, bottom: 90, keepWithNext: true };
    const firstItem = { top: 90, bottom: 140 };
    // Glued -> both move to page 2 at the title top (80), not the item top (90).
    expect(computePageOffsets([title, firstItem], 100)).toEqual([0, 80]);
  });

  it('does not loop on an oversized block taller than a full page', () => {
    const offsets = computePageOffsets(
      [
        { top: 0, bottom: 150 }, // taller than the 100px page -> clipped, stays
        { top: 150, bottom: 190 },
      ],
      100
    );
    expect(offsets).toEqual([0, 150]);
  });

  it('returns a single page when the page height is unmeasured', () => {
    expect(computePageOffsets([{ top: 0, bottom: 500 }], 0)).toEqual([0]);
    expect(pageCount([{ top: 0, bottom: 500 }], 0)).toBe(1);
  });

  it('reports the page count from the offsets', () => {
    expect(
      pageCount(
        [
          { top: 0, bottom: 90 },
          { top: 90, bottom: 180 },
          { top: 180, bottom: 270 },
        ],
        100
      )
    ).toBe(3);
  });
});
