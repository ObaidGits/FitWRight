import { beforeAll, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';

import { ResumeThumbnail } from '@/components/resume/resume-thumbnail';

// The thumbnail fetches the resume body only once it scrolls into view. Neither the
// query nor the observer is the subject here, so both are stubbed: this file is about
// the SIZING, which is the part that breaks silently.
vi.mock('@/features/resumes/hooks', () => ({
  useResume: () => ({ data: undefined }),
}));

beforeAll(() => {
  // jsdom has no IntersectionObserver. The component uses it to defer the fetch until
  // a card is near the viewport, which is deliberate (a long library must not fire N
  // requests on load) and irrelevant to sizing.
  class NoopObserver {
    observe() {}
    disconnect() {}
    unobserve() {}
  }
  vi.stubGlobal('IntersectionObserver', NoopObserver);
});

describe('ResumeThumbnail sizing', () => {
  it('is a fixed 96px wide by default', () => {
    // The list view relied on this, so the default must not drift when the grid was
    // added.
    const { container } = render(<ResumeThumbnail resumeId="r1" ready={false} />);
    const wrapper = container.firstElementChild as HTMLElement;

    expect(wrapper.style.width).toBe('96px');
    // A4 is 1:1.414, so 96 * 1.414 rounds to 136.
    expect(wrapper.style.height).toBe('136px');
  });

  it('fills its container in fluid mode, keeping the A4 shape', () => {
    /**
     * The grid card has no fixed width, so the thumbnail must scale to whatever it is
     * given. This uses container-query units in CSS rather than measuring width in
     * JavaScript: a ResizeObserver feeding state would re-render every card on every
     * layout change, and a grid of twenty is exactly where that gets expensive.
     */
    const { container } = render(<ResumeThumbnail resumeId="r1" ready={false} fluid />);
    const wrapper = container.firstElementChild as HTMLElement;

    // Without `container-type: inline-size` the cqw units inside resolve against the
    // viewport instead of the card, and every thumbnail would render at page width.
    expect(wrapper.style.containerType).toBe('inline-size');
    expect(wrapper.style.aspectRatio).toBe('210 / 297');
    // No fixed pixel width, or it could not fill the card.
    expect(wrapper.style.width).toBe('');
    expect(wrapper.className).toContain('w-full');
  });

  it('does not shrink-wrap in fluid mode', () => {
    // `shrink-0` is right for a flex row and wrong inside a grid card, where it would
    // stop the thumbnail taking the width it was told to fill.
    const { container } = render(<ResumeThumbnail resumeId="r1" ready={false} fluid />);
    expect((container.firstElementChild as HTMLElement).className).not.toContain('shrink-0');
  });

  it('still reserves the full page shape before content loads', () => {
    // The placeholder holds the card's height so a grid does not reflow row by row as
    // thumbnails arrive.
    const { container } = render(<ResumeThumbnail resumeId="r1" ready={false} fluid />);
    const placeholder = container.querySelector('.h-full.w-full');
    expect(placeholder).not.toBeNull();
  });
});
