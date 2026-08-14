import { describe, expect, it } from 'vitest';
import { getTabFromSearchParams, TAB_IDS } from '@/components/builder/tab-ids';

/**
 * The builder's workspace modes.
 *
 * `resume` is the content mode and keeps that id for URL compatibility - it is
 * linked as `?tab=resume` from elsewhere, and renaming it would break those
 * links silently (an unknown tab falls back rather than erroring).
 */
describe('builder mode routing', () => {
  it('defaults to the content mode', () => {
    expect(getTabFromSearchParams(new URLSearchParams(''))).toBe('resume');
  });

  it('falls back to content for an unknown mode instead of rendering nothing', () => {
    expect(getTabFromSearchParams(new URLSearchParams('tab=nonsense'))).toBe('resume');
  });

  it('routes to the design mode, which the resume editor deep-links into', () => {
    // The resume editor's "Fine-grained formatting" link relies on this.
    expect(getTabFromSearchParams(new URLSearchParams('tab=design'))).toBe('design');
  });

  it('still honours every previously-linkable mode', () => {
    for (const id of ['resume', 'cover-letter', 'outreach', 'interview-prep', 'jd-match']) {
      expect(getTabFromSearchParams(new URLSearchParams(`tab=${id}`))).toBe(id);
    }
  });

  it('keeps content and design first, so the two everyday modes lead', () => {
    expect(TAB_IDS[0]).toBe('resume');
    expect(TAB_IDS[1]).toBe('design');
  });
});
