import { describe, expect, it } from 'vitest';
import { compareByMatchScore } from '@/lib/utils/resume-sort';
import type { ResumeListItem } from '@/lib/api/resume';

/**
 * The library's "Best match first" ordering.
 *
 * The rule worth pinning is what happens to a resume with NO score. An
 * untailored or master resume was never measured against a job, so it must sink
 * to the bottom rather than sort as 0 - otherwise the master resume ranks below
 * the worst-matching tailored variant, which reads as a judgement rather than an
 * absence.
 *
 * These assert the comparator's contract DIRECTLY, in both argument orders, not
 * just the output of `sort`. That matters: for a two-element array V8 calls the
 * comparator once, in one direction only, so a sort-only test can exercise the
 * `b`-is-null branch while never touching the `a`-is-null branch - and pass
 * against a comparator that has one of them backwards.
 */
function item(partial: Partial<ResumeListItem>): ResumeListItem {
  return {
    resume_id: partial.resume_id ?? 'id',
    filename: null,
    is_master: false,
    parent_id: null,
    processing_status: 'ready',
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
    ...partial,
  };
}

const scored = item({ resume_id: 'scored', ats_score: 12 });
const unscored = item({ resume_id: 'unscored', ats_score: null });
const absent = item({ resume_id: 'absent' });
const zero = item({ resume_id: 'zero', ats_score: 0 });

describe('compareByMatchScore contract', () => {
  it('ranks an unscored resume after a scored one, whichever way round it is asked', () => {
    expect(compareByMatchScore(unscored, scored)).toBeGreaterThan(0);
    expect(compareByMatchScore(scored, unscored)).toBeLessThan(0);
  });

  it('treats an absent field the same as an explicit null, in both directions', () => {
    expect(compareByMatchScore(absent, scored)).toBeGreaterThan(0);
    expect(compareByMatchScore(scored, absent)).toBeLessThan(0);
  });

  it('keeps a real zero above an absent score, because 0 is a measurement', () => {
    expect(compareByMatchScore(zero, unscored)).toBeLessThan(0);
    expect(compareByMatchScore(unscored, zero)).toBeGreaterThan(0);
  });

  it('orders two scored resumes by score, descending', () => {
    const high = item({ resume_id: 'high', ats_score: 90 });
    expect(compareByMatchScore(high, scored)).toBeLessThan(0);
    expect(compareByMatchScore(scored, high)).toBeGreaterThan(0);
  });

  it('falls back to most-recently-updated when neither is scored', () => {
    const older = item({ resume_id: 'older', updated_at: '2026-01-01' });
    const newer = item({ resume_id: 'newer', updated_at: '2026-06-01' });
    expect(compareByMatchScore(newer, older)).toBeLessThan(0);
    expect(compareByMatchScore(older, newer)).toBeGreaterThan(0);
  });
});

describe('sorting a library with it', () => {
  it('puts every scored resume above every unscored one, highest first', () => {
    const rows = [
      unscored,
      item({ resume_id: 'low', ats_score: 41 }),
      absent,
      item({ resume_id: 'high', ats_score: 92 }),
      item({ resume_id: 'mid', ats_score: 68 }),
    ].sort(compareByMatchScore);
    expect(rows.slice(0, 3).map((r) => r.resume_id)).toEqual(['high', 'mid', 'low']);
    expect(
      rows
        .slice(3)
        .map((r) => r.resume_id)
        .sort()
    ).toEqual(['absent', 'unscored']);
  });
});
