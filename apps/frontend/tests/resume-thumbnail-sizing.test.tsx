import { beforeAll, describe, expect, it, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';

import { ResumeThumbnail } from '@/components/resume/resume-thumbnail';

/**
 * These tests exist because the first version of this file passed while the feature was
 * visibly broken.
 *
 * It asserted the MECHANISM ("container-type is set", "aspect-ratio is set") instead of
 * the OUTCOME ("the page is actually scaled down"). The transform was
 * `scale(calc(100cqw / 794))`, which is invalid CSS - `scale()` takes a plain number and
 * a length divided by a number is a length - so browsers dropped it and every resume
 * rendered at its full 794px inside a ~200px card, clipped mid-word. Every assertion
 * still passed.
 *
 * So each test below checks a number that could only be right if scaling happened.
 */

const A4_WIDTH_PX = 794;

vi.mock('@/features/resumes/hooks', () => ({
  // A body must be present, or the component renders the placeholder and there is no
  // scaled page to inspect at all.
  useResume: () => ({
    data: {
      processed_resume: { personal_info: { name: 'Test Person' } },
      template_settings: undefined,
    },
  }),
}));

vi.mock('@/components/dashboard/resume-component', () => ({
  default: () => <div data-testid="resume-body" />,
  DEFAULT_TEMPLATE_SETTINGS: { template: 'single-column' },
}));

beforeAll(() => {
  class NoopIntersectionObserver {
    constructor(private cb: (e: { isIntersecting: boolean }[]) => void) {
      // Report visible immediately: deferring the fetch is deliberate in production and
      // irrelevant to sizing.
      this.cb([{ isIntersecting: true }]);
    }
    observe() {}
    disconnect() {}
    unobserve() {}
  }
  vi.stubGlobal('IntersectionObserver', NoopIntersectionObserver);

  // jsdom reports every element as 0x0, so a real ResizeObserver would report width 0
  // and the component would correctly refuse to render. This one reports a card-sized
  // width, which is what a browser would do.
  class FakeResizeObserver {
    constructor(private cb: (entries: { contentRect: { width: number } }[]) => void) {}
    observe() {
      this.cb([{ contentRect: { width: 200 } }]);
    }
    disconnect() {}
    unobserve() {}
  }
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
});

function scaleOf(el: HTMLElement): number {
  const match = /scale\(([^)]+)\)/.exec(el.style.transform);
  return match ? Number(match[1]) : NaN;
}

describe('ResumeThumbnail scaling', () => {
  it('scales the page to the measured container width', async () => {
    const { container } = render(<ResumeThumbnail resumeId="r1" ready fluid />);

    await waitFor(() => {
      const page = container.querySelector('[style*="scale"]') as HTMLElement;
      expect(page).not.toBeNull();
      // 200px container / 794px page. THE assertion the old test was missing.
      expect(scaleOf(page)).toBeCloseTo(200 / A4_WIDTH_PX, 4);
    });
  });

  it('produces a finite numeric scale, never a CSS expression', async () => {
    // Pins the actual bug: `scale(calc(...))` is unparseable as a number, and that is
    // precisely how it shipped looking like a cropping decision.
    const { container } = render(<ResumeThumbnail resumeId="r1" ready fluid />);

    await waitFor(() => {
      const page = container.querySelector('[style*="scale"]') as HTMLElement;
      expect(page.style.transform).not.toContain('calc');
      expect(page.style.transform).not.toContain('cq');
      expect(Number.isFinite(scaleOf(page))).toBe(true);
    });
  });

  it('renders the page narrower than its container once scaled', async () => {
    // The visible symptom, stated as a property: scaled width must fit the card.
    const { container } = render(<ResumeThumbnail resumeId="r1" ready fluid />);

    await waitFor(() => {
      const page = container.querySelector('[style*="scale"]') as HTMLElement;
      expect(A4_WIDTH_PX * scaleOf(page)).toBeLessThanOrEqual(200);
    });
  });

  it('keeps the fixed 96px form for the non-grid caller', () => {
    const { container } = render(<ResumeThumbnail resumeId="r1" ready />);
    const wrapper = container.firstElementChild as HTMLElement;

    expect(wrapper.style.width).toBe('96px');
    expect(wrapper.style.height).toBe('136px');
    const page = container.querySelector('[style*="scale"]') as HTMLElement;
    expect(scaleOf(page)).toBeCloseTo(96 / A4_WIDTH_PX, 4);
  });

  it('crops shorter than a full page in the grid, to keep cards compact', () => {
    // A full 1:1.414 page made a very tall card for content unreadable at this size.
    const { container } = render(<ResumeThumbnail resumeId="r1" ready fluid />);
    expect((container.firstElementChild as HTMLElement).style.aspectRatio).toBe('5 / 4');
  });

  it('does not shrink-wrap in the grid', () => {
    const { container } = render(<ResumeThumbnail resumeId="r1" ready fluid />);
    expect((container.firstElementChild as HTMLElement).className).not.toContain('shrink-0');
  });
});
