import { describe, expect, it } from 'vitest';
import { shouldShowFirstRun } from '@/features/home/hooks';

describe('deterministic setup detection', () => {
  it('never onboards an established user with a master resume and AI key', () => {
    expect(
      shouldShowFirstRun({
        complete: true,
        has_master_resume: true,
        llm_configured: true,
      })
    ).toBe(false);
  });

  it('onboards a genuinely new user with neither requirement', () => {
    expect(
      shouldShowFirstRun({
        complete: false,
        has_master_resume: false,
        llm_configured: false,
      })
    ).toBe(true);
  });

  it('onboards only for the missing persisted requirement', () => {
    expect(
      shouldShowFirstRun({
        complete: false,
        has_master_resume: true,
        llm_configured: false,
      })
    ).toBe(true);
    expect(
      shouldShowFirstRun({
        complete: false,
        has_master_resume: false,
        llm_configured: true,
      })
    ).toBe(true);
  });

  it('does not guess onboarding state before the authoritative query resolves', () => {
    expect(shouldShowFirstRun(undefined)).toBe(false);
    expect(shouldShowFirstRun(null)).toBe(false);
  });
});
