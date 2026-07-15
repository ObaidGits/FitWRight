import { describe, expect, it } from 'vitest';

import { normalizeTemplateSettings } from '@/lib/api/resume';
import { DEFAULT_TEMPLATE_SETTINGS } from '@/lib/types/template-settings';

describe('normalizeTemplateSettings (backward-compatible persisted appearance)', () => {
  it('returns defaults for null/invalid input (legacy resumes)', () => {
    expect(normalizeTemplateSettings(null)).toEqual(DEFAULT_TEMPLATE_SETTINGS);
    expect(normalizeTemplateSettings(undefined)).toEqual(DEFAULT_TEMPLATE_SETTINGS);
    expect(normalizeTemplateSettings('nope')).toEqual(DEFAULT_TEMPLATE_SETTINGS);
  });

  it('merges a partial persisted blob over the defaults (nested groups filled)', () => {
    const merged = normalizeTemplateSettings({
      template: 'latex',
      margins: { top: 20 },
      fontSize: { base: 4 },
    });
    expect(merged.template).toBe('latex');
    // Missing nested keys are backfilled from defaults.
    expect(merged.margins.bottom).toBe(DEFAULT_TEMPLATE_SETTINGS.margins.bottom);
    expect(merged.margins.top).toBe(20);
    expect(merged.fontSize.headerFont).toBe(DEFAULT_TEMPLATE_SETTINGS.fontSize.headerFont);
    expect(merged.fontSize.base).toBe(4);
    // Untouched top-level fields keep defaults.
    expect(merged.pageSize).toBe(DEFAULT_TEMPLATE_SETTINGS.pageSize);
  });
});
