import { describe, expect, it } from 'vitest';
import {
  appearanceStorageKey,
  templateSettingsCacheKey,
  UNSAVED_TEMPLATE_SETTINGS_KEY,
} from '@/lib/resume/appearance-storage';

/**
 * The two editors disagreed about where a resume's appearance lived, and the
 * disagreement lost work: the builder wrote margins/fonts to ONE global browser
 * key and never to the resume, so a change vanished on another device and leaked
 * onto every other resume in this one.
 *
 * The property that matters is isolation: two different resumes must never share
 * a cache key.
 */
describe('appearanceStorageKey', () => {
  it('gives each resume its own key, so appearance cannot leak between them', () => {
    expect(appearanceStorageKey('resume-a')).not.toBe(appearanceStorageKey('resume-b'));
  });

  it('is stable for the same resume, so the cache is actually found again', () => {
    expect(appearanceStorageKey('resume-a')).toBe(appearanceStorageKey('resume-a'));
    expect(appearanceStorageKey('resume-a')).toBe(templateSettingsCacheKey('resume-a'));
  });

  it('matches the key the resume editor has always used, so existing caches still resolve', () => {
    // Changing this string would silently orphan every cached appearance and
    // make resumes appear to reset to defaults.
    expect(templateSettingsCacheKey('abc123')).toBe('fitwright-template-abc123');
  });

  it('falls back to a single shared key only for an unsaved resume', () => {
    expect(appearanceStorageKey(null)).toBe(UNSAVED_TEMPLATE_SETTINGS_KEY);
    expect(appearanceStorageKey(undefined)).toBe(UNSAVED_TEMPLATE_SETTINGS_KEY);
  });

  it('never returns the unsaved key for a real resume', () => {
    // The original bug in one sentence: every resume resolved to this key.
    expect(appearanceStorageKey('resume-a')).not.toBe(UNSAVED_TEMPLATE_SETTINGS_KEY);
  });
});
