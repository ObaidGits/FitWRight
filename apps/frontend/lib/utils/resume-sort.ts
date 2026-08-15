import type { ResumeListItem } from '@/lib/api/resume';

/**
 * "Best match first" ordering for the resume library.
 *
 * The rule that matters is what happens to a resume with NO score. An untailored
 * or master resume was never measured against a job, so it sinks below every
 * scored one rather than sorting as 0 - otherwise the master resume ranks beneath
 * the worst-matching tailored variant, which reads as a judgement rather than an
 * absence. A real 0 still outranks an absent score, because 0 is a measurement.
 *
 * Lives here rather than inline in the page so the test exercises THIS function
 * instead of a copy of it that can silently drift.
 */
export function compareByMatchScore(a: ResumeListItem, b: ResumeListItem): number {
  const av = a.ats_score;
  const bv = b.ats_score;
  if (av == null && bv == null) {
    // Neither is scored, so fall back to the default ordering.
    return (b.updated_at ?? '').localeCompare(a.updated_at ?? '');
  }
  if (av == null) return 1;
  if (bv == null) return -1;
  return bv - av;
}
