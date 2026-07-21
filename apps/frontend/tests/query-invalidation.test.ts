import { describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { invalidateResumeLists, invalidateApplicationLists, queryKeys } from '@/lib/query/client';

/**
 * Auto-refresh contract: the shared invalidation helpers must refresh the LIST
 * surfaces (so create/update/delete are seen instantly) without blanket-matching
 * the editor detail (['resumes', id]) - which would clobber in-progress edits.
 */
describe('invalidateResumeLists', () => {
  it('refreshes home, library, and tailor-source lists but NOT the editor detail', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    invalidateResumeLists(qc);

    const calls = spy.mock.calls.map((c) => c[0]);
    // Home list is matched exactly (so it doesn't catch ['resumes', id]).
    expect(calls).toContainEqual({ queryKey: queryKeys.resumes, exact: true });
    expect(calls).toContainEqual({ queryKey: ['resumes', 'library'] });
    expect(calls).toContainEqual({ queryKey: ['resumes', 'tailor-sources'] });
    // No call blanket-invalidates ['resumes'] non-exactly (which would hit detail).
    const blanket = calls.find(
      (c) => JSON.stringify(c?.queryKey) === JSON.stringify(['resumes']) && !c?.exact
    );
    expect(blanket).toBeUndefined();
  });
});

describe('invalidateApplicationLists', () => {
  it('refreshes the board + home count exactly (not the application detail)', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    invalidateApplicationLists(qc);
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.applications, exact: true });
  });
});
