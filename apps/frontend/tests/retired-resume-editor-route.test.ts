import { describe, expect, it, vi, beforeEach } from 'vitest';

const redirectMock = vi.hoisted(() => vi.fn());
vi.mock('next/navigation', () => ({ redirect: redirectMock }));

import RetiredResumeEditorPage from '@/app/(app)/resumes/[id]/page';

/**
 * `/resumes/[id]` was a SECOND resume editor. It is retired - the builder is now
 * the only one - but the URL is kept as a redirect because it is bookmarkable and
 * is still referenced by resume notifications created before the merge.
 */
describe('retired resume-editor route', () => {
  beforeEach(() => vi.clearAllMocks());

  it('sends the resume straight to the unified editor', async () => {
    await RetiredResumeEditorPage({ params: Promise.resolve({ id: 'abc123' }) });
    expect(redirectMock).toHaveBeenCalledWith('/builder?id=abc123');
  });

  it('encodes the id, so an id needing escaping cannot break the target URL', async () => {
    await RetiredResumeEditorPage({ params: Promise.resolve({ id: 'a b&c=d' }) });
    expect(redirectMock).toHaveBeenCalledWith('/builder?id=a%20b%26c%3Dd');
  });

  it('carries the id through rather than dropping the user on a generic page', async () => {
    // Redirecting to /builder with no id would silently open an empty editor and
    // look like the resume had been lost.
    await RetiredResumeEditorPage({ params: Promise.resolve({ id: 'r1' }) });
    const target = redirectMock.mock.calls[0][0] as string;
    expect(target).toContain('id=r1');
    expect(target).not.toBe('/builder');
  });
});
